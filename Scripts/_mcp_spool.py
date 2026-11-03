#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the LOCAL contract of one session's peer-message spool.

Two files of this repo talk through a directory on disk and nothing else: the
MCP server `Scripts/mcp-messagebox.py`, which fetches peer messages from the hub
and writes them into `~/.claude/messagebox/<cc_pid>-<cc_start>/`, and the hook
`Scripts/messagebox-hook.py`, which reads ONLY that directory and hands the
messages to the model. The hook is the last step before the model, so it must
judge a message by exactly the rules the server judged it by, and both must find
the owning Claude Code session the same way. This file holds that contract once:

* **who owns the spool** -- the owning session is the first ancestor with a
  `~/.claude/sessions/<pid>.json` that parses as strict JSON and names that pid;
  `startedAt // 1000` is the `<cc_start>` of the directory name. A file that is
  there but invalid ends the walk (fail closed);
* **what may be read from disk** -- `O_NOFOLLOW`, a regular file, ours, bounded;
  what a private directory is (a real directory of ours with no group or other
  bit);
* **the grammar of a name, a host and an id** -- `[A-Za-z0-9._-]{1,64}`,
  `ms_`/`pe_` + 22 base64url characters, an instance key of 22-43;
* **the text rule** -- a hand copy of ai-soul's `isInvisibleUnsafe` with the
  schedule predicate's `\\n`/`\\t` allow-list, plus a refusal of a
  `<system-reminder` / `</system-reminder` tag, because the harness wraps a
  rewake in one;
* **what a spool record is** -- `_valid_message`, one function for the server
  (a hub message, extra keys tolerated) and the hook (a spool file, exactly the
  contract's keys);
* **strict JSON for those files** -- duplicate keys, NaN, infinities and integer
  literals over 4300 characters refused.

**Why a fifteenth source and not blocks in an existing one.** ADR 0014's test is
whether an existing source could hold them without becoming a shelf. The nearest
is `_mcp_json.py`, and it is the wrong question: it owns how a JSON-RPC frame is
enveloped and parsed. This file answers a different one -- what a peer message
and the directory it lives in must look like on disk for two processes of one
session to agree -- and its strict parser is therefore its OWN, not a call to
`_strict_loads`: a block may use only builtins, its host's imports and the names
co-listed on its own marker, so a block of this source cannot reach a block of
that one, and the hook must not carry the JSON-RPC envelope to parse a file. The
two parsers agree by a row of `tests/test_messagebox.py`, the way the two search
sanitizers agree. The text rule has no other home either: the search sources'
sanitizers shape output for a model, this one REFUSES input that would reach it.

**It is a WHOLE source.** The blocks call one another (the record check reads the
grammar, the text rule and the parser; the owner walk reads the file reader and
the parser), so a host spells ONE marker naming every block in source order. Both
hosts do. Hosts: `Scripts/mcp-messagebox.py` (matches the server glob) and
`Scripts/messagebox-hook.py`, a script that is not an MCP server and is named in
`Scripts/amalgamate.py:DECLARED_HOSTS` by hand. The hook imports no socket,
`urllib`, `http` or `ssl` module, and no block here needs one; a suite row reads
its imports to keep it so.

**No host imports this module.** Its blocks are inlined between
`# BEGIN GENERATED` / `# END GENERATED` markers by `Scripts/amalgamate.py`, so the
server and the hook stay self-contained single files: an imported sibling would
write `Scripts/__pycache__/*.pyc` into a tree every suite that snapshots bytecode
asserts stays empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and a hook run as `python3 -I <path>` could
not import a sibling at all.

The blocks read `json`, `os`, `re`, `stat`, `subprocess` and `unicodedata` and the
typing names `Any`, `NamedTuple` and `Optional`, which both hosts import. Every
block is written with no line break inside a bracket and every indent a whole
multiple of four, so the tab conversion of `Scripts/amalgamate.py` would accept
them. Their behaviour is pinned by `tests/test_messagebox.py` and
`tests/test_messagebox_hook.py`, which drive the generated copies.
"""

import json
import os
import re
import stat
import subprocess
import unicodedata
from typing import Any, NamedTuple, Optional

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
