#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""mcp-inspect — read-only, non-invasive system/process/network inspection.

Single-tool dispatcher pattern (like mcp-git): one MCP tool (inspect_call)
routes to internal handlers via the 'function' parameter. Every handler is
READ-ONLY — it inspects live system state (processes, process tree, open files,
sockets, network interfaces/routes, memory, disk, mounts, file metadata,
services, resource limits, toolchain versions, host, environment) or the FORMAL
well-formedness of a file (json, python, yaml, toml, xml, ini, csv, tsv, plist,
javascript), and NEVER mutates anything. There is no way
to pass a raw shell string: each function builds a fixed argv (shell=False),
filters are applied in Python, and numeric params (pid/port) are int-validated,
so there is no shell-injection surface.

Purpose: let the model run the common non-invasive `ps` / `lsof` / `netstat` /
`ss` / `df` / `du` / `free` / `env` / `stat` / `ifconfig` / `pstree` / `ulimit` /
`launchctl` / `<tool> --version` / `shasum` / `md5sum` inspections — and the
`python3 -c "import ast; ast.parse(...)"` / `py_compile` / `json.tool` / `jq .` /
`xmllint --noout` / `node --check` validation one-liners — through a single
pre-approved MCP tool instead of per-call Bash prompts. Validators run in-process
(stdlib parsers), so nothing is written: `py_compile` in particular would leave a
__pycache__/*.pyc. JavaScript is the one format with no stdlib parser: it spawns
`node --check`, which PARSES ONLY — never `-e`/`-p`/require/import, so the code
under validation is never executed, and nothing is written there either.

The execution-shaped functions are `versions`, which probes only an ALLOW-LISTED
set of binary NAMES (_VERSION_TOOLS) with fixed flags, and the javascript
validator's fixed `node --check` argv; the caller can never supply argv.

Cross-platform: macOS (Darwin) and Linux. Commands are selected per platform;
missing underlying binaries degrade to a clear error, never a crash.

Output is always Markdown.

Usage:
  python3 mcp-inspect.py [--project-root <path>] [--debug] [--log-file <path>]

Call `inspect_call` with no `function` to print the full function list (also
available via `python3 mcp-inspect.py --list`).
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("mcp-inspect")

_SYS = platform.system()
IS_MAC = _SYS == "Darwin"
IS_LINUX = _SYS == "Linux"

DEFAULT_MAX_CHARS = 100_000

# Keys matching this (on the NAME) get their env value redacted.
_SECRET_KEY_RE = re.compile(
    r"(secret|token|passwd|password|api[-_]?key|access[-_]?key|auth|"
    r"credential|private[-_]?key|session|cookie|bearer|client[-_]?secret)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Param / output helpers
# ---------------------------------------------------------------------------

def _ensure_dict(value: Any, name: str = "params") -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'{name}' was a string but not valid JSON: {exc}. "
                f"Pass '{name}' as an object, not a JSON-encoded string."
            )
    if not isinstance(value, dict):
        raise ValueError(
            f"'{name}' must be an object (dict) or a JSON-encoded object string; "
            f"got {type(value).__name__}."
        )
    return value


def _bool_param(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


def _int_param(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"'{name}' must be an integer, not a boolean.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ValueError(f"'{name}' must be an integer; got {value!r}.")


def _md_fence(content: str, lang: str = "") -> str:
    max_run = 0
    for run in re.findall(r"`+", content):
        max_run = max(max_run, len(run))
    fence = "`" * max(3, max_run + 1)
    return f"{fence}{lang}\n{content}\n{fence}"


def _md_lines(lines: List[str]) -> str:
    """Join bare `key: value` lines, fencing ONLY when there is more than one.

    Markdown folds a lone newline into a space, so a multi-line block needs the
    fence or it silently runs together.  A single line does not, and there the
    fence would be 8 characters of overhead on a 17-character answer -- worse
    than the `- ` bullets it replaced.  Assumes a `key:` lead, which is why no
    block-level first character has to be considered here.
    """
    if len(lines) <= 1:
        return "\n".join(lines)
    return _md_fence("\n".join(lines))


def _squeeze(text: str, dedent: bool = False) -> str:
    """Runs of spaces -> one, and no trailing whitespace, per line.

    These payloads are aligned tables from ps/lsof/netstat/df/launchctl, not
    source code: the padding is presentation, and one space marks the same
    column boundary for a fraction of the width.

    TABS are deliberately untouched.  ifconfig indents each stanza body with
    one, and that indent is the ONLY thing saying which interface a line
    belongs to -- collapsing it would merge two interfaces into one reading.
    `dedent` is for launchctl, which prefixes every line but the first with a
    tab of its own accord, where the indent means nothing at all.
    """
    out = []
    for ln in text.splitlines():
        ln = re.sub(r" {2,}", " ", ln).rstrip()
        out.append(ln.lstrip() if dedent else ln)
    return "\n".join(out)


def _fold_kv(text: str) -> List[str]:
    """`Key:<padding>value` -> bare `key: value`, alignment and trailing dot gone.

    vm_stat and /proc/meminfo are both key/value already, and both pad the value
    out to a fixed column -- about 30 characters a line on vm_stat's 21 lines.
    Shared by both so the Linux path runs the code the macOS tests exercise.
    A line with no colon is passed through rather than dropped: losing data to
    tidy a format would be a bad trade.
    """
    out: List[str] = []
    for ln in text.splitlines():
        key, sep, val = ln.partition(":")
        if not sep:
            out.append(ln.strip())
            continue
        key, val = key.strip().strip('"').lower(), val.strip().rstrip(".")
        out.append(f"{key}: {val}" if key and val else ln.strip())
    return out


# Ceiling on a CALLER-SUPPLIED `timeout`. The five functions that accept one
# (processes / ports / connections / open_files / disk_usage) handed it straight
# to `_run` with no upper bound, so `{"timeout": 86400}` bought a day-long `du`.
# That is the same defect class as the read loop at the bottom of this file, one
# layer down: it no longer DEAFENS the server -- the stdin reader owns a thread
# outside the handler pool -- but MAX_INFLIGHT_REQUESTS of them and the pool is
# gone, which the caller experiences as the same dead server. 120s is four times
# the largest default here (`du`'s 30), so a genuinely slow tree can still be
# waited out, while an unbounded park is no longer expressible.
MAX_TIMEOUT_SEC = 120


def _timeout_param(p: dict, default: int) -> int:
    """The caller's `timeout`, clamped to [1, MAX_TIMEOUT_SEC].

    The lower bound is not cosmetic: subprocess.run treats a zero or negative
    timeout as ALREADY expired, so `{"timeout": -1}` turned every probe into an
    instant rc=124 that reads exactly like the underlying tool being broken.
    Absent `timeout` returns the caller's default un-clamped -- those are this
    module's own literals, all well under the ceiling.
    """
    if "timeout" not in p:
        return default
    return max(1, min(MAX_TIMEOUT_SEC, _int_param(p["timeout"], "timeout")))


def _run(cmd: List[str], timeout: int = 15,
         stdin_text: Optional[str] = None) -> Tuple[int, str, str]:
    """Run an argv (shell=False) read-only. Never raises; returns (rc, out, err).

    stdin=DEVNULL by default: this server's stdin is the JSON-RPC stream, and
    capture_output only redirects stdout/stderr -- stdin would be INHERITED.
    A child that reads it (any of these tools when a flag makes it wait on
    input) would silently swallow protocol messages. Almost every probe below is
    read-only and takes no input, so DEVNULL costs nothing here.

    `stdin_text` is the one exception: `node --check` reads a script from stdin,
    which is how inline `content` is validated without writing a temp file. It
    goes through `input=` because subprocess.run refuses `input=` and `stdin=`
    together, so the two forms are spelled out rather than built as kwargs --
    the stdin choice stays visible AT the call.
    """
    try:
        if stdin_text is None:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, input=stdin_text)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"
    except OSError as exc:
        return 1, "", f"{cmd[0]}: {exc}"


def _have(name: str) -> bool:
    return shutil.which(name) is not None


def _mod_present(name: str) -> bool:
    """Is an importable module available? Checked WITHOUT importing it."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _truncate(text: str, max_chars: int) -> Tuple[str, bool]:
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _ps_selector() -> List[str]:
    # macOS: -axo (all incl. no-tty). Linux: -eo (every process).
    fmt = "pid,ppid,user,pcpu,pmem,rss,stat,etime,comm,args"
    return ["ps", "-axo", fmt] if IS_MAC else ["ps", "-eo", fmt]


def _parse_ps(out: str) -> Tuple[List[str], List[List[str]]]:
    """Parse `ps -o ...` output into (header, rows). Last column (args) may
    contain spaces, so split into exactly len(header) fields."""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return [], []
    header = lines[0].split()
    ncol = len(header)
    rows: List[List[str]] = []
    for ln in lines[1:]:
        parts = ln.split(None, ncol - 1)
        if len(parts) < ncol:
            parts += [""] * (ncol - len(parts))
        rows.append(parts)
    return header, rows


def _fmt_table(header: List[str], rows: List[List[str]],
               show_header: bool = True) -> str:
    """Align columns; `show_header=False` drops the header ROW but not its job.

    A caller drops the header only when the values name themselves (a 64-char
    hex digest, a size, a path).  The alignment then stays load-bearing: with no
    header to count columns against, a path containing a space is only
    unambiguous because the columns line up.
    """
    widths = [len(h) for h in header] if show_header else [0] * len(header)
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    # last column left un-padded so long command lines don't blow up width
    def fmt_row(cells: List[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            if i == len(cells) - 1:
                out.append(cell)
            else:
                out.append(cell.ljust(widths[i]))
        # One space, and no trailing whitespace: the ljust above already fixes
        # the columns, so the second space was pure width.  The rstrip is what
        # stops a short row from being padded out to a long column and then
        # ending in nothing -- which is how `md5` grew trailing spaces.
        return " ".join(out).rstrip()
    return "\n".join(([fmt_row(header)] if show_header else [])
                     + [fmt_row(r) for r in rows])


def _kb_human(kb: float) -> str:
    units = ["K", "M", "G", "T", "P"]
    v = float(kb)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f}{u}" if u != "K" else f"{int(v)}{u}"
        v /= 1024
    return f"{v:.1f}P"


# ---------------------------------------------------------------------------
# Handlers — each returns markdown string (raises ValueError for bad params)
# ---------------------------------------------------------------------------

def h_processes(p: dict) -> str:
    filt = (p.get("filter") or p.get("name") or "").strip().lower()
    user = (p.get("user") or "").strip()
    sort_given = bool((p.get("sort") or "").strip())
    sort = (p.get("sort") or "cpu").strip().lower()
    limit = _int_param(p.get("limit", 30), "limit") if "limit" in p else 30
    timeout = _timeout_param(p, 15)

    rc, out, err = _run(_ps_selector(), timeout)
    if rc not in (0,) and not out:
        raise ValueError(err or "ps failed")
    header, rows = _parse_ps(out)
    if not header:
        return "_(no process data)_"

    idx = {h.lower(): i for i, h in enumerate(header)}
    args_i = idx.get("args", len(header) - 1)
    user_i = idx.get("user")
    cpu_i = idx.get("pcpu")
    mem_i = idx.get("pmem")

    if filt:
        rows = [r for r in rows if filt in r[args_i].lower()]
    if user and user_i is not None:
        rows = [r for r in rows if r[user_i] == user]

    sort_i = {"cpu": cpu_i, "mem": mem_i, "pid": idx.get("pid")}.get(sort, cpu_i)
    if sort_i is not None:
        def key(r):
            try:
                return float(r[sort_i])
            except (ValueError, IndexError):
                return -1.0
        rows.sort(key=key, reverse=(sort != "pid"))

    total = len(rows)
    if limit > 0:
        rows = rows[:limit]

    body = _fmt_table(header, rows)
    # `filter`/`user`/`limit` were the caller's own words.  `sort` is worth a
    # word only when the SERVER picked it, because it is what put these rows in
    # this order.  The count stays either way: it is what says whether `limit`
    # bit, which no parameter echo can tell.
    said = [] if sort_given else [f"sort {sort}"]
    said.append(f"{len(rows)} of {total}")
    return "_" + ", ".join(said) + "_\n\n" + _md_fence(body)


def h_process(p: dict) -> str:
    if "pid" not in p:
        raise ValueError("'process' requires params.pid (integer).")
    pid = _int_param(p["pid"], "pid")
    fmt = "pid,ppid,pgid,user,pcpu,pmem,rss,vsz,stat,nice,etime,comm,args"
    rc, out, err = _run(["ps", "-o", fmt, "-p", str(pid)], 15)
    if rc != 0 or not out.strip() or len(out.splitlines()) < 2:
        raise ValueError(err.strip() or f"no such process: {pid}")
    header, rows = _parse_ps(out)
    parts = [_md_fence(_fmt_table(header, rows))]
    if _have("lsof"):
        rc2, out2, _ = _run(["lsof", "-nP", "-p", str(pid)], 15)
        if rc2 == 0 and out2.strip():
            nfiles = max(0, len(out2.splitlines()) - 1)
            parts.append(f"\n_open file descriptors: {nfiles}_")
    return "\n".join(parts)


def _lsof_listen(proto: str, timeout: int) -> Tuple[int, str, str]:
    if proto == "tcp":
        return _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout)
    if proto == "udp":
        return _run(["lsof", "-nP", "-iUDP"], timeout)
    # all: run both, concat
    rc1, o1, e1 = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout)
    rc2, o2, e2 = _run(["lsof", "-nP", "-iUDP"], timeout)
    out = o1
    if o2.strip():
        # drop the duplicate header line from the second call
        out = o1 + "\n".join(o2.splitlines()[1:]) if o1 else o2
    return (0 if (rc1 == 0 or rc2 == 0) else rc1, out, (e1 or e2))


def h_ports(p: dict) -> str:
    proto = (p.get("proto") or "all").strip().lower()
    if proto not in ("tcp", "udp", "all"):
        raise ValueError("params.proto must be one of: tcp, udp, all.")
    timeout = _timeout_param(p, 15)

    if _have("lsof"):
        rc, out, err = _lsof_listen(proto, timeout)
        label = f"lsof listening ({proto})"
    elif IS_LINUX and _have("ss"):
        flag = {"tcp": "-tlnp", "udp": "-ulnp", "all": "-tulnp"}[proto]
        rc, out, err = _run(["ss", "-H", flag], timeout)
        label = f"ss {flag}"
    elif _have("netstat"):
        rc, out, err = _run(["netstat", "-an"], timeout)
        label = "netstat -an"
    else:
        raise ValueError("no lsof/ss/netstat available to list ports.")
    if rc not in (0,) and not out.strip():
        raise ValueError(err.strip() or "port listing failed")
    # The backend stays: which of lsof/ss/netstat answered decides how to read
    # these columns, and the caller never chose it.  The `netstat -an` fallback
    # in particular is NOT filtered to listening sockets -- the other two are.
    return f"_{label}_\n\n" + (_md_fence(_squeeze(out.strip()))
                               if out.strip() else "_(none)_")


def h_connections(p: dict) -> str:
    state = (p.get("state") or "established").strip().lower()
    timeout = _timeout_param(p, 15)
    if _have("lsof"):
        if state == "established":
            rc, out, err = _run(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"], timeout)
        else:
            rc, out, err = _run(["lsof", "-nP", "-i"], timeout)
        label = f"lsof connections ({state})"
    elif IS_LINUX and _have("ss"):
        rc, out, err = _run(["ss", "-H", "-tunp"], timeout)
        label = "ss -tunp"
    elif _have("netstat"):
        rc, out, err = _run(["netstat", "-an"], timeout)
        label = "netstat -an"
    else:
        raise ValueError("no lsof/ss/netstat available to list connections.")
    if rc not in (0,) and not out.strip():
        raise ValueError(err.strip() or "connection listing failed")
    return f"_{label}_\n\n" + (_md_fence(_squeeze(out.strip()))
                               if out.strip() else "_(none)_")


def h_open_files(p: dict) -> str:
    if not _have("lsof"):
        raise ValueError("lsof not available on this host.")
    timeout = _timeout_param(p, 20)
    limit = _int_param(p.get("limit", 200), "limit") if "limit" in p else 200
    cmd = ["lsof", "-nP"]
    filtered = False
    if "pid" in p:
        cmd += ["-p", str(_int_param(p["pid"], "pid"))]
        filtered = True
    if "user" in p and str(p["user"]).strip():
        cmd += ["-u", str(p["user"]).strip()]
        filtered = True
    if "port" in p:
        cmd += ["-i", f":{_int_param(p['port'], 'port')}"]
        filtered = True
    path = p.get("path")
    if path and str(path).strip():
        cmd.append(os.path.realpath(str(path)))
        filtered = True

    rc, out, err = _run(cmd, timeout)
    # lsof exits 1 when some handles are inaccessible even on success; trust stdout
    if not out.strip():
        if err.strip():
            raise ValueError(err.strip())
        return "_(no matching open files)_"
    lines = out.splitlines()
    total = max(0, len(lines) - 1)
    note = ""
    if not filtered:
        note = "\n\n_no filter given (pid/port/user/path) — output may be large._"
    if limit > 0 and len(lines) > limit + 1:
        lines = lines[: limit + 1]
        note += f"\n\n_truncated to first {limit} of {total} entries._"
    return f"{_md_fence(_squeeze(chr(10).join(lines)))}{note}"


def h_host(p: dict) -> str:
    lines = [f"hostname: {socket.gethostname()}",
             f"platform: {platform.platform()}"]
    rc, out, _ = _run(["uname", "-a"], 5)
    if rc == 0 and out.strip():
        lines.append(f"uname: {out.strip()}")
    if IS_MAC:
        rc, out, _ = _run(["sw_vers"], 5)
        if rc == 0 and out.strip():
            # sw_vers is key/value already -- folded into the same block rather
            # than nested in its own fence, which a fence cannot hold anyway.
            lines += _fold_kv(out.strip())
        rc, out, _ = _run(["sysctl", "-n", "hw.ncpu"], 5)
        if rc == 0 and out.strip():
            lines.append(f"cpus: {out.strip()}")
    else:
        try:
            with open("/etc/os-release") as f:
                osr = {k: v.strip('"') for k, v in
                       (ln.split("=", 1) for ln in f.read().splitlines() if "=" in ln)}
            if osr.get("PRETTY_NAME"):
                lines.append(f"os: {osr['PRETTY_NAME']}")
        except OSError:
            pass
        lines.append(f"cpus: {os.cpu_count()}")
    try:
        la = os.getloadavg()
        lines.append(f"loadavg: {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}")
    except (OSError, AttributeError):
        pass
    rc, out, _ = _run(["uptime"], 5)
    if rc == 0 and out.strip():
        lines.append(f"uptime: {out.strip()}")
    return _md_lines(lines)


def h_memory(p: dict) -> str:
    if IS_MAC:
        # No source label on any of the three branches: `vm_stat`, `free -h` and
        # /proc/meminfo are unmistakable from their own first line, so naming
        # them would restate what the payload already says.
        lines = []
        rc, out, _ = _run(["sysctl", "-n", "hw.memsize"], 5)
        if rc == 0 and out.strip().isdigit():
            lines.append(f"total: {_kb_human(int(out.strip()) / 1024)}")
        rc, out, _ = _run(["vm_stat"], 5)
        if rc != 0 or not out.strip():
            raise ValueError("vm_stat unavailable")
        for ln in _fold_kv(out.strip()):
            # vm_stat's banner is the one line whose value is prose; the only
            # thing in it a caller needs is the page size the counts are in.
            if ln.startswith("mach virtual memory statistics"):
                m = re.search(r"(\d+)", ln)
                if m:
                    lines.append(f"page size: {m.group(1)}")
                continue
            lines.append(ln)
        return _md_lines(lines)
    # linux
    if _have("free"):
        rc, out, err = _run(["free", "-h"], 5)
        if rc == 0 and out.strip():
            # `free -h` is a TABLE, not key/value -- folding it would be wrong.
            return _md_fence(_squeeze(out.strip()))
    try:
        with open("/proc/meminfo") as f:
            data = f.read()
        return _md_lines(_fold_kv(data.strip()))
    except OSError as exc:
        raise ValueError(f"cannot read memory info: {exc}")


def h_disk(p: dict) -> str:
    path = p.get("path")
    cmd = ["df", "-kP"] if not IS_MAC else ["df", "-kP"]
    if path and str(path).strip():
        cmd.append(os.path.realpath(str(path)))
    rc, out, err = _run(cmd, 15)
    if rc != 0 and not out.strip():
        raise ValueError(err.strip() or "df failed")
    # reformat sizes to human where the 2nd..4th columns are 1K-blocks
    lines = out.splitlines()
    if lines:
        rebuilt = [lines[0].replace("1024-blocks", "size").replace("1K-blocks", "size")]
        for ln in lines[1:]:
            cols = ln.split()
            if len(cols) >= 4 and cols[1].isdigit():
                cols[1] = _kb_human(float(cols[1]))
                cols[2] = _kb_human(float(cols[2])) if cols[2].isdigit() else cols[2]
                cols[3] = _kb_human(float(cols[3])) if cols[3].isdigit() else cols[3]
                rebuilt.append(" ".join(cols))
            else:
                rebuilt.append(ln)
        out = "\n".join(rebuilt)
    # `df`'s own header keeps its original padding while the rows above are
    # rebuilt -- squeezing puts both on the same footing.
    return _md_fence(_squeeze(out.strip()))


def h_disk_usage(p: dict) -> str:
    if not p.get("path") or not str(p["path"]).strip():
        raise ValueError("'disk_usage' requires params.path.")
    raw = str(p["path"]).strip()
    path = os.path.realpath(raw)
    if not os.path.exists(path):
        raise ValueError(f"path does not exist: {path}")
    depth = _int_param(p.get("depth", 1), "depth") if "depth" in p else 1
    top = _int_param(p.get("top", 20), "top") if "top" in p else 20
    timeout = _timeout_param(p, 30)
    depth_flag = ["-d", str(depth)] if IS_MAC else [f"--max-depth={depth}"]
    rc, out, err = _run(["du", "-k"] + depth_flag + [path], timeout)
    if rc not in (0,) and not out.strip():
        raise ValueError(err.strip() or "du failed (path too large? raise timeout)")
    entries = []
    for ln in out.splitlines():
        parts = ln.split("\t") if "\t" in ln else ln.split(None, 1)
        if len(parts) == 2 and parts[0].strip().isdigit():
            entries.append((int(parts[0]), parts[1].strip()))
    entries.sort(reverse=True)
    total = len(entries)
    if top > 0:
        entries = entries[:top]
    # Names relative to the queried root: `du` prefixes every line with the
    # root, which is the caller's own argument -- repeating a long absolute
    # path once per row is pure echo.  `.` is the root itself.  Lexical only,
    # and safe: `du` was rooted at `path`, so every name starts with it.
    # One space, no right-alignment: a size never contains one, so the first
    # space IS the column boundary and the padding bought nothing but width.
    body = "\n".join(f"{_kb_human(kb)} {os.path.relpath(name, path)}"
                     for kb, name in entries)
    # Say only what the caller could not know: the resolved path when it is
    # NOT the one they passed, a depth THEY did not choose, and a clip that
    # actually dropped rows (`top` alone would not say whether it bit).
    said = []
    if path != raw:
        said.append(f"`{path}`")
    if "depth" not in p:
        said.append(f"depth {depth}")
    if len(entries) < total:
        said.append(f"top {top} of {total}")
    head = "_" + ", ".join(said) + "_\n\n" if said else ""
    note = ""
    if err.strip():
        note = "\n\n_(some paths were not readable and were skipped)_"
    return f"{head}{_md_fence(body)}{note}"


def h_mounts(p: dict) -> str:
    if _have("mount"):
        rc, out, err = _run(["mount"], 10)
        if rc == 0 and out.strip():
            return _md_fence(_squeeze(out.strip()))
    try:
        with open("/proc/mounts") as f:
            # The fallback IS news: `mount` was unavailable, and /proc/mounts
            # has a different column layout.
            return "_/proc/mounts_\n\n" + _md_fence(_squeeze(f.read().strip()))
    except OSError as exc:
        raise ValueError(f"cannot read mounts: {exc}")


def h_which(p: dict) -> str:
    name = p.get("name") or p.get("cmd") or p.get("command")
    if not name:
        raise ValueError("'which' requires params.name (binary to resolve).")
    names = name if isinstance(name, list) else [name]
    lines: List[str] = []
    for n in names:
        n = str(n).strip()
        resolved = shutil.which(n)
        if resolved:
            real = os.path.realpath(resolved)
            extra = f" → {real}" if real != resolved else ""
            lines.append(f"{n}: {resolved}{extra}")
        else:
            lines.append(f"{n}: not found in PATH")
    return _md_lines(lines)


def h_env(p: dict) -> str:
    key = p.get("key")
    filt = (p.get("filter") or "").strip().lower()
    show_secrets = _bool_param(p.get("show_secrets", False))

    redacted: List[str] = []

    def render(k: str, v: str) -> str:
        if _SECRET_KEY_RE.search(k) and not show_secrets:
            redacted.append(k)
            return f"{k} = ***REDACTED*** (len {len(v)})"
        return f"{k} = {v}"

    env = dict(os.environ)
    if key:
        k = str(key)
        if k not in env:
            return f"_env var `{k}` is not set_"
        head, body = "", render(k, env[k])
    else:
        keys = sorted(env)
        if filt:
            keys = [k for k in keys if filt in k.lower()]
        if not keys:
            return "_no matching env vars_"
        # The count survives, the filter does not: how many matched is news,
        # what was matched against is the caller's own word.
        head = f"_{len(keys)} vars_\n\n"
        body = _md_lines([render(k, env[k]) for k in keys])
    # Only when something WAS redacted.  The old form printed the policy
    # unconditionally, so a reply holding nothing secret still spent 77
    # characters announcing a redaction that never happened.
    note = ("\n\n_secret-looking values redacted; pass show_secrets=true to "
            "reveal_") if redacted else ""
    return head + body + note


def _fmt_time(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def h_stat(p: dict) -> str:
    import stat as st_mod

    raw = p.get("path") or p.get("file")
    if not raw or not str(raw).strip():
        raise ValueError("'stat' requires params.path.")
    path = os.path.expanduser(str(raw).strip())
    try:
        st = os.lstat(path)          # lstat: describe the link itself, not its target
    except OSError as exc:
        raise ValueError(f"cannot stat {path}: {exc.strerror or exc}")

    is_link = st_mod.S_ISLNK(st.st_mode)
    kind = ("symlink" if is_link else
            "directory" if st_mod.S_ISDIR(st.st_mode) else
            "fifo" if st_mod.S_ISFIFO(st.st_mode) else
            "socket" if st_mod.S_ISSOCK(st.st_mode) else
            "block device" if st_mod.S_ISBLK(st.st_mode) else
            "char device" if st_mod.S_ISCHR(st.st_mode) else "file")

    def owner(uid: int, gid: int) -> str:
        uname = gname = None
        try:
            import pwd
            uname = pwd.getpwuid(uid).pw_name
        except (ImportError, KeyError):
            pass
        try:
            import grp
            gname = grp.getgrgid(gid).gr_name
        except (ImportError, KeyError):
            pass
        return f"{uname or uid}:{gname or gid} (uid={uid}, gid={gid})"

    # Bare `key: value`, fenced.  The old `- ` bullets were not decoration --
    # they are what kept these on separate lines, because Markdown folds a lone
    # newline into a space.  A fence does the same job for 8 characters where
    # ten bullets cost 20, and it retires the colon alignment as well.
    lines = [f"type: {kind}",
             f"mode: {st_mod.filemode(st.st_mode)} ({oct(st_mod.S_IMODE(st.st_mode))})",
             f"owner: {owner(st.st_uid, st.st_gid)}",
             f"size: {st.st_size} B ({_kb_human(st.st_size / 1024)})",
             f"links: {st.st_nlink}",
             f"inode: {st.st_ino} (device {st.st_dev})",
             f"mtime: {_fmt_time(st.st_mtime)}",
             f"atime: {_fmt_time(st.st_atime)}",
             f"ctime: {_fmt_time(st.st_ctime)}"]

    if is_link:
        try:
            lines.append(f"target: {os.readlink(path)}")
        except OSError:
            pass
        real = os.path.realpath(path)
        broken = "" if os.path.exists(real) else "  (BROKEN — target missing)"
        lines.append(f"resolves to: {real}{broken}")
    else:
        real = os.path.realpath(path)
        if real != os.path.abspath(path):
            lines.append(f"realpath: {real}  (path traverses a symlink)")

    if st_mod.S_ISDIR(st.st_mode):
        try:
            entries = os.listdir(path)
            ndirs = sum(1 for e in entries
                        if os.path.isdir(os.path.join(path, e)))
            lines.append(f"entries: {len(entries)} "
                         f"({len(entries) - ndirs} files, {ndirs} dirs)")
        except OSError as exc:
            lines.append(f"entries: unreadable ({exc.strerror})")

    acc = "".join(n for n, m in (("r", os.R_OK), ("w", os.W_OK), ("x", os.X_OK))
                  if os.access(path, m))
    lines.append(f"access for this server process: {acc or 'none'}")
    # The resolved path stays OUTSIDE the fence: it is the server talking about
    # the argument, not part of the stat record.  And only when expanduser()
    # rewrote what the caller sent -- an unchanged path is theirs already.
    head = f"_`{path}`_\n\n" if path != str(raw).strip() else ""
    return head + _md_fence("\n".join(lines))


def h_interfaces(p: dict) -> str:
    filt = (p.get("filter") or p.get("name") or "").strip()
    if IS_LINUX and _have("ip"):
        rc, out, err = _run(["ip", "-o", "addr"], 10)
        label, per_line = "ip -o addr", True
    elif _have("ifconfig"):
        rc, out, err = _run(["ifconfig", "-a"], 10)
        label, per_line = "ifconfig -a", False
    elif _have("ip"):
        rc, out, err = _run(["ip", "-o", "addr"], 10)
        label, per_line = "ip -o addr", True
    else:
        raise ValueError("no ip/ifconfig available to list interfaces.")
    if rc != 0 and not out.strip():
        raise ValueError(err.strip() or "interface listing failed")

    text = out.strip()
    if filt:
        if per_line:
            text = "\n".join(ln for ln in text.splitlines() if filt in ln)
        else:
            # ifconfig stanzas: a new interface starts at column 0
            blocks: List[List[str]] = []
            cur: List[str] = []
            for ln in text.splitlines():
                if ln and not ln[0].isspace():
                    if cur:
                        blocks.append(cur)
                    cur = [ln]
                else:
                    cur.append(ln)
            if cur:
                blocks.append(cur)
            text = "\n\n".join("\n".join(b) for b in blocks if filt in b[0])
        if not text.strip():
            return f"_no interface matching {filt!r}_"

    head = f"_{label}_"
    try:
        names = ", ".join(n for _, n in socket.if_nameindex())
        if names:
            head += f"\n\n_present: {names}_"
    except (OSError, AttributeError):
        pass
    # Squeezed but NOT dedented: ifconfig's leading tab is what marks a line as
    # belonging to the interface above it rather than starting a new one.
    return head + "\n\n" + _md_fence(_squeeze(text))


def h_route(p: dict) -> str:
    if IS_LINUX and _have("ip"):
        rc, out, err = _run(["ip", "route"], 10)
        label = "ip route"
    elif _have("netstat"):
        rc, out, err = _run(["netstat", "-rn"], 15)
        label = "netstat -rn"
    else:
        raise ValueError("no ip/netstat available to show the routing table.")
    if rc != 0 and not out.strip():
        raise ValueError(err.strip() or "route listing failed")
    return f"_{label}_\n\n" + (_md_fence(_squeeze(out.strip()))
                               if out.strip() else "_(empty)_")


def h_pstree(p: dict) -> str:
    root = _int_param(p["pid"], "pid") if "pid" in p else None
    depth = _int_param(p.get("depth", 0), "depth") if "depth" in p else 0
    limit = _int_param(p.get("limit", 200), "limit") if "limit" in p else 200

    fmt = "pid,ppid,user,comm"
    rc, out, err = _run(["ps", "-axo", fmt] if IS_MAC else ["ps", "-eo", fmt], 15)
    if rc != 0 and not out:
        raise ValueError(err.strip() or "ps failed")
    header, rows = _parse_ps(out)
    if not header:
        return "_(no process data)_"

    idx = {h.lower(): i for i, h in enumerate(header)}
    i_pid, i_ppid = idx.get("pid", 0), idx.get("ppid", 1)
    i_user, i_comm = idx.get("user", 2), idx.get("comm", 3)

    info: Dict[int, Tuple[str, str]] = {}
    parent: Dict[int, int] = {}
    kids: Dict[int, List[int]] = {}
    for r in rows:
        try:
            pid, ppid = int(r[i_pid]), int(r[i_ppid])
        except (ValueError, IndexError):
            continue
        info[pid] = (r[i_user], r[i_comm])
        parent[pid] = ppid
        kids.setdefault(ppid, []).append(pid)
    for v in kids.values():
        v.sort()

    if root is not None and root not in info:
        raise ValueError(f"no such process: {root}")
    roots = [root] if root is not None else sorted(
        pid for pid, ppid in parent.items() if ppid not in info)

    lines: List[str] = []
    state = {"truncated": False}
    visited: set = set()

    def walk(pid: int, prefix: str, is_last: bool, level: int) -> None:
        if limit > 0 and len(lines) >= limit:
            state["truncated"] = True
            return
        if depth > 0 and level > depth:
            return
        if pid in visited:
            return  # a self/loop-parented ps row must not recurse forever
        visited.add(pid)
        user, comm = info.get(pid, ("?", "?"))
        if level == 0:
            lines.append(f"{pid:>7} {user:<10} {comm}".rstrip())
            child_prefix = ""
        else:
            branch = "`- " if is_last else "|- "
            lines.append(f"{pid:>7} {user:<10} {prefix}{branch}{comm}".rstrip())
            child_prefix = prefix + ("   " if is_last else "|  ")
        children = kids.get(pid, [])
        for n, child in enumerate(children):
            walk(child, child_prefix, n == len(children) - 1, level + 1)

    for n, r_pid in enumerate(roots):
        walk(r_pid, "", n == len(roots) - 1, 0)

    # `pid` and `depth` were the caller's own words.  The two counts are not:
    # they say how much of the host this tree actually covers, which is the one
    # thing a subtree request cannot answer for itself.
    head = (f"_{len(lines)} rows of {len(info)} host processes_"
            if root is not None or depth > 0
            else f"_{len(lines)} of {len(info)} processes_")
    note = f"\n\n_truncated at limit={limit}; pass a larger limit or a pid to narrow._" \
        if state["truncated"] else ""
    # NOT squeezed.  The `|  ` and `   ` runs inside each row ARE the depth of
    # the tree, and the pid/user columns are padded so every branch starts at
    # the same column -- collapse either and the tree stops being one.
    return head + "\n\n" + _md_fence("\n".join(lines)) + note


def h_limits(p: dict) -> str:
    import resource

    parts: List[str] = []
    if "pid" in p:
        pid = _int_param(p["pid"], "pid")
        if IS_LINUX:
            try:
                with open(f"/proc/{pid}/limits") as f:
                    return _md_fence(_squeeze(f.read().strip()))
            except OSError as exc:
                raise ValueError(f"cannot read limits for pid {pid}: "
                                 f"{exc.strerror or exc}")
        parts.append(f"_macOS exposes no per-process rlimits — `pid={pid}` ignored; "
                     "showing this server's limits and the system limits instead._\n")

    rows = []
    for name in sorted(n for n in dir(resource) if n.startswith("RLIMIT_")):
        try:
            soft, hard = resource.getrlimit(getattr(resource, name))
        except (ValueError, OSError):
            continue
        def show(v: int) -> str:
            return "unlimited" if v == resource.RLIM_INFINITY else str(v)
        rows.append([name[len("RLIMIT_"):].lower(), show(soft), show(hard)])

    # No "## limits — this MCP server process" title: the italic line below
    # already says WHOSE limits these are, which is the only part the caller
    # could not have worked out, and it says it better.
    parts += ["_inherited from whatever launched the server; NOT the Bash tool's shell._", "",
              _md_fence(_fmt_table(["limit", "soft", "hard"], rows))]

    if IS_MAC and _have("launchctl"):
        rc, out, _ = _run(["launchctl", "limit"], 5)
        if rc == 0 and out.strip():
            # An italic label, not the `###` heading it used to be: this was the
            # last Markdown heading left in the server.  `dedent` because
            # launchctl tabs every line but the first for no reason at all.
            parts += ["", "_system (launchctl limit)_", "",
                      _md_fence(_squeeze(out.strip(), dedent=True))]
    elif IS_LINUX:
        try:
            with open("/proc/sys/fs/file-max") as f:
                parts += ["", f"_system file-max: {f.read().strip()}_"]
        except OSError:
            pass
    return "\n".join(parts)


def h_services(p: dict) -> str:
    filt = (p.get("filter") or p.get("name") or "").strip().lower()
    user_scope = _bool_param(p.get("user", False))
    limit = _int_param(p.get("limit", 100), "limit") if "limit" in p else 100

    if IS_MAC:
        if not _have("launchctl"):
            raise ValueError("launchctl not available on this host.")
        rc, out, err = _run(["launchctl", "list"], 15)
        label = "launchctl list"
    else:
        if not _have("systemctl"):
            raise ValueError("systemctl not available on this host.")
        cmd = (["systemctl"] + (["--user"] if user_scope else [])
               + ["list-units", "--type=service", "--all",
                  "--no-pager", "--plain", "--no-legend"])
        rc, out, err = _run(cmd, 20)
        label = "systemctl" + (" --user" if user_scope else "") + " list-units --type=service"
    if rc != 0 and not out.strip():
        raise ValueError(err.strip() or "service listing failed")

    lines = out.strip().splitlines()
    header = lines[0] if (IS_MAC and lines and lines[0].lower().startswith("pid")) else None
    body_lines = lines[1:] if header else lines
    total = len(body_lines)
    if filt:
        body_lines = [ln for ln in body_lines if filt in ln.lower()]
    if not body_lines:
        return f"_no service matching {filt!r} (of {total})_"
    shown = len(body_lines)
    note = ""
    if limit > 0 and shown > limit:
        body_lines = body_lines[:limit]
        note = f"\n\n_showing first {limit} of {shown} matches._"
    text = _squeeze("\n".join(([header] if header else []) + body_lines))
    head = f"_{label} — {len(body_lines)} of {total}_"
    return head + "\n\n" + _md_fence(text) + note


# Allow-listed version probes: tool -> argv flags (None = ["--version"]).
# The caller may only NAME a tool from this map; it can never supply argv, so
# there is no way to turn this into arbitrary command execution.
_VERSION_TOOLS: Dict[str, Optional[List[str]]] = {
    "python3": None, "python": None, "pip3": None, "uv": None,
    "node": None, "npm": None, "pnpm": None, "yarn": None,
    "bun": None, "deno": None, "tsc": None,
    "git": None, "gh": None,
    "cmake": None, "ninja": None, "make": None, "pkg-config": None,
    "gcc": None, "g++": None, "clang": None, "clang++": None,
    "clangd": None, "clang-tidy": None, "nvcc": None,
    "rustc": None, "cargo": None, "go": None,
    "lua": ["-v"], "luajit": ["-v"], "lua-language-server": None,
    "docker": None, "psql": None, "sqlite3": None, "jq": None,
    "ffmpeg": ["-version"], "tshark": None, "lldb": None,
    "java": ["-version"], "ruby": None, "perl": None,
    "bash": None, "zsh": None, "swift": None,
}


def h_versions(p: dict) -> str:
    req = p.get("tools") or p.get("tool") or p.get("name")
    explicit = bool(req)
    if explicit:
        names = (list(req) if isinstance(req, list)
                 else [t for t in str(req).replace(",", " ").split() if t])
        names = [str(n).strip() for n in names]
        unknown = sorted({n for n in names if n not in _VERSION_TOOLS})
        if unknown:
            raise ValueError(
                "not in the version-probe allow-list: " + ", ".join(unknown)
                + ". Allowed: " + ", ".join(sorted(_VERSION_TOOLS))
                + ". (Allow-listed by design — this function never runs an "
                  "arbitrary binary.)"
            )
    else:
        names = sorted(_VERSION_TOOLS)

    rows = []
    for name in names:
        resolved = shutil.which(name)
        if not resolved:
            if explicit:
                rows.append([name, "_not installed_", ""])
            continue
        rc, out, err = _run([resolved] + (_VERSION_TOOLS[name] or ["--version"]), 10)
        text = [ln for ln in (out or err).splitlines() if ln.strip()]
        rows.append([name,
                     text[0].strip() if text else f"(no output, rc={rc})",
                     resolved])
    if not rows:
        return "_none of the allow-listed tools are installed_"
    # Only the implicit call gets a line: there the caller named nothing, so how
    # many of the allow-list are actually installed is the whole answer.  When
    # they named the tools, the rows already are the answer.
    head = ("" if explicit else
            f"_{len(rows)} of {len(_VERSION_TOOLS)} allow-listed tools installed_\n\n")
    return head + _md_fence(_fmt_table(["tool", "version", "path"], rows))


# Digests are computed with hashlib, NOT by shelling out to shasum/sha256sum/
# md5/md5sum: those differ per platform (`md5 -q` on macOS vs `md5sum` on Linux),
# may be missing, and their output has to be re-parsed. hashlib is stdlib, needs
# no argv, streams the file in chunks, and gives identical digests everywhere.
_HASH_ALGOS = ("sha256", "sha512", "sha384", "sha224", "sha1", "md5",
               "blake2b", "blake2s")
_HASH_MAX_MB = 2048          # refuse bigger files unless max_mb is raised: the
                             # digest holds one handler worker start to finish
_HASH_CHUNK = 1024 * 1024


def h_hash(p: dict, algo: str = "") -> str:
    import hashlib

    single = p.get("path") or p.get("file")
    multi = p.get("paths")
    if single and multi:
        raise ValueError("pass either 'path' (one file) or 'paths' (a list) — "
                         "not both, so nothing is silently dropped.")
    raw = single or multi
    if not raw:
        raise ValueError("hashing requires params.path (one file, or a list of files).")
    algo = (algo or str(p.get("algo") or "sha256")).strip().lower()
    if algo not in _HASH_ALGOS:
        raise ValueError(f"unsupported algo {algo!r}; use one of: "
                         + ", ".join(_HASH_ALGOS))
    max_mb = _int_param(p.get("max_mb", _HASH_MAX_MB), "max_mb") \
        if "max_mb" in p else _HASH_MAX_MB
    paths = list(raw) if isinstance(raw, list) else [raw]
    expect = str(p.get("expect") or "").strip().lower()
    if expect and len(paths) != 1:
        raise ValueError("'expect' compares a single file — pass exactly one path.")

    rows: List[List[str]] = []
    verdict: Optional[bool] = None
    for item in paths:
        path = os.path.expanduser(str(item).strip())
        if os.path.isdir(path):
            rows.append([path, "(is a directory)", ""])
            continue
        try:
            size = os.path.getsize(path)
            if max_mb > 0 and size > max_mb * 1024 * 1024:
                rows.append([path, f"(skipped: {_kb_human(size / 1024)} "
                             f"exceeds max_mb={max_mb})", ""])
                continue
            digest_obj = hashlib.new(algo)
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
                    digest_obj.update(chunk)
        except OSError as exc:
            rows.append([path, f"(error: {exc.strerror or exc})", ""])
            continue
        digest = digest_obj.hexdigest()
        rows.append([path, digest,
                     f"{size} B" if size < 1024 else _kb_human(size / 1024)])
        if expect:
            verdict = digest == expect

    # `path` first, because that is what identifies the row, and no header row
    # at all: a 64-char hex digest and a size name themselves.  Which is also
    # why the alignment has to stay -- with no header to count columns against,
    # it is the only thing keeping a path that contains a space unambiguous.
    out = _md_fence(_fmt_table(["path", algo, "size"], rows, show_header=False))
    if expect:
        out += ("\n\n**MATCH** — the digest equals the expected value."
                if verdict else
                f"\n\n**MISMATCH** — expected `{expect}`.")
    return out


def h_sha256(p: dict) -> str:
    return h_hash(p, "sha256")


def h_md5(p: dict) -> str:
    return h_hash(p, "md5")


# ---------------------------------------------------------------------------
# Format / syntax validation
# ---------------------------------------------------------------------------

# Validation runs IN-PROCESS with stdlib parsers instead of shelling out to
# `python3 -c "import ast; ast.parse(...)"`, `python3 -m json.tool`, `jq .` or
# `xmllint --noout`: each of those costs a Bash permission prompt, differs per
# platform, and its output has to be re-parsed. Three deliberate choices:
#   * Python is compiled with compile() IN MEMORY — NEVER py_compile, which
#     writes __pycache__/*.pyc and would make this read-only server mutate the
#     tree. compile() is also strictly stronger than ast.parse: it runs the
#     symtable/codegen pass, so `break` outside a loop, `return` outside a
#     function and module-level `nonlocal` are caught too.
#   * XML entity declarations are refused (XXE + billion-laughs guard), so the
#     validator is safe on untrusted input.
#   * JavaScript is the ONE format with no stdlib parser, so it is the one that
#     does shell out — to `node --check`, which parses and stops. `node -e` /
#     `--eval` / `-p` / requiring the file would EXECUTE it, which is why none
#     of them appear here, and why no temp file is written for `content` either
#     (stdin carries it instead).
# This is FORMAL well-formedness (does it parse), NOT schema validation.
# Verdict vocabulary matches the p:verify skill: OK / FAIL / LIMITED / SKIP.

_V_OK = "OK"
_V_FAIL = "FAIL"
_V_LIMITED = "LIMITED"
_V_SKIP = "SKIP"

# extension -> format. Parity with ClaudeCode/skills/verify/scripts/validate.py,
# plus .py/.pyi and the .js/.mjs/.cjs trio which that script does not cover.
_VALIDATE_EXT = {
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml", ".svg": "xml", ".xsd": "xml", ".rss": "xml",
    ".plist": "plist",
    ".ini": "ini", ".cfg": "ini",
    ".csv": "csv",
    ".tsv": "tsv",
    ".py": "python", ".pyi": "python",
    # .js/.mjs/.cjs ONLY. `node --check` parses neither JSX nor TypeScript, so
    # .jsx/.ts/.tsx/.mts/.cts stay unmapped on purpose: an honest "unknown
    # format for this extension" SKIP beats a bogus FAIL on a file that is fine.
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
}

_VALIDATE_MAX_MB = 32        # parsers build a full in-memory tree, and hold one
                             # handler worker for as long as they are building it

# (status, message, line, col)
_VResult = Tuple[str, str, Optional[int], Optional[int]]


def _v_ok(msg: str) -> _VResult:
    return (_V_OK, msg, None, None)


def _v_fail(msg: str, line: Optional[int] = None,
            col: Optional[int] = None) -> _VResult:
    return (_V_FAIL, msg, line, col)


def _v_limited(msg: str, line: Optional[int] = None) -> _VResult:
    return (_V_LIMITED, msg, line, None)


def _v_skip(msg: str) -> _VResult:
    return (_V_SKIP, msg, None, None)


def _v_decode(data: bytes) -> str:
    # utf-8-sig tolerates (and strips) a leading BOM: legal for these formats,
    # and it otherwise trips up the parsers.
    return data.decode("utf-8-sig")


def _v_json(data: bytes, name: str) -> _VResult:
    try:
        text = _v_decode(data)
    except UnicodeDecodeError as exc:
        return _v_fail(f"not valid UTF-8: {exc}")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return _v_fail(exc.msg, exc.lineno, exc.colno)
    return _v_ok("valid JSON")


# The ONE piece of process-global state any handler in this module touches.
# `warnings.catch_warnings` works by swapping `warnings.filters` and
# `warnings.showwarning` out and restoring them on exit -- the stdlib documents
# it as "modifying global state and therefore not thread-safe" -- so once
# handlers run concurrently, two `python` validations at once would (a) record
# file A's SyntaxWarning into file B's report, because whichever recorder is
# installed catches it, and (b) on the losing unwind order leave the recorder and
# the "always" filter installed for the rest of the process, since each thread
# restores the snapshot IT saw. Python 3.14 can make the filters context-local,
# but only in a free-threaded build or under -X context_aware_warnings (checked
# on this host: warnings._use_context == 0), and this script's floor is 3.9 where
# no such mode exists -- so the resource is guarded rather than assumed fixed.
# The lock wraps ONLY the compile: a Python source parses in milliseconds, so
# serialising this one validator is unmeasurable, and every other function --
# including the other nine validators, which are all per-call objects -- still
# runs fully concurrently.
_V_PYTHON_LOCK = threading.Lock()


def _v_python(data: bytes, name: str) -> _VResult:
    import warnings

    caught: List[Any] = []
    try:
        with _V_PYTHON_LOCK, warnings.catch_warnings(record=True) as caught:
            # SyntaxWarnings (invalid escape sequence, `assert (x, y)`, `is` with
            # a literal) are real defects that ast.parse never surfaces.
            warnings.simplefilter("always", SyntaxWarning)
            # compile() is fed the RAW BYTES, never a pre-decoded str: only then
            # does the tokenizer honour a PEP-263 coding cookie / UTF-8 BOM the
            # way the interpreter itself does, so a valid
            # `# -*- coding: latin-1 -*-` source is not mis-reported as "not
            # valid UTF-8". A genuinely broken encoding surfaces as a
            # SyntaxError from compile(), which is exactly right.
            compile(data, name or "<content>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        return _v_fail(exc.msg or "syntax error", exc.lineno, exc.offset)
    except Exception as exc:      # null bytes, recursion depth, ...
        return _v_fail(f"{type(exc).__name__}: {exc}")
    ver = "%d.%d" % sys.version_info[:2]
    warned = [w for w in caught if issubclass(w.category, SyntaxWarning)]
    if warned:
        first = warned[0]
        return _v_ok(f"compiles on Python {ver} but emits {len(warned)} "
                     f"SyntaxWarning(s); first: {first.message} "
                     f"(line {first.lineno})")
    return _v_ok(f"valid Python syntax (compiled on {ver})")


def _v_xml(data: bytes, name: str) -> _VResult:
    import xml.parsers.expat as expat

    parser = expat.ParserCreate()

    def _block_entity_decl(ent, is_param, value, base, system_id, public_id,
                           notation):
        # No inline value but a SYSTEM/PUBLIC id => external entity (XXE).
        # An inline value => internal entity (the billion-laughs vector).
        if value is None and (system_id is not None or public_id is not None):
            raise ValueError("external entity declaration not allowed (XXE guard)")
        raise ValueError("entity declaration not allowed (entity-expansion guard)")

    def _block_external(*_a, **_k):
        raise ValueError("external entity reference not allowed (XXE guard)")

    # A plain DOCTYPE is still accepted. expat fires the entity-declaration
    # handler before any reference, so both vectors die at declaration time.
    parser.EntityDeclHandler = _block_entity_decl
    parser.ExternalEntityRefHandler = _block_external
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        # expat reports a 0-based column
        return _v_fail(expat.ErrorString(exc.code), exc.lineno, exc.offset + 1)
    except ValueError as exc:
        return _v_fail(str(exc))
    return _v_ok("well-formed XML")


def _v_ini(data: bytes, name: str) -> _VResult:
    import configparser

    try:
        text = _v_decode(data)
    except UnicodeDecodeError as exc:
        return _v_fail(f"not valid UTF-8: {exc}")
    cp = configparser.ConfigParser(strict=True)   # strict => duplicate detection
    try:
        cp.read_string(text)
    except configparser.Error as exc:
        # most configparser errors carry .lineno; ParsingError instead keeps a
        # list of (lineno, line) in .errors
        line = getattr(exc, "lineno", None)
        if line is None:
            errs = getattr(exc, "errors", None)
            if errs:
                line = errs[0][0]
        return _v_fail(" ".join(str(exc).split()), line)
    return _v_ok(f"valid INI ({len(cp.sections())} section(s))")


def _v_delim(data: bytes, delimiter: str, label: str) -> _VResult:
    import csv
    import io

    try:
        text = _v_decode(data)
    except UnicodeDecodeError as exc:
        return _v_fail(f"not valid UTF-8: {exc}")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    expected: Optional[int] = None
    seen = 0
    try:
        for i, row in enumerate(reader, 1):
            seen += 1
            if expected is None:
                expected = len(row)
            elif len(row) != expected:
                return _v_fail(f"inconsistent column count: row {i} has "
                               f"{len(row)} field(s), expected {expected}", i)
    except csv.Error as exc:
        # csv.Error has no position; the reader tracks the physical line
        return _v_fail(f"{label} parse error: {exc}", reader.line_num)
    if expected is None:
        return _v_ok(f"empty {label} (no rows)")
    return _v_ok(f"consistent {label} ({seen} row(s) x {expected} column(s))")


def _v_csv(data: bytes, name: str) -> _VResult:
    return _v_delim(data, ",", "CSV")


def _v_tsv(data: bytes, name: str) -> _VResult:
    return _v_delim(data, "\t", "TSV")


def _v_plist(data: bytes, name: str) -> _VResult:
    import plistlib

    try:
        plistlib.loads(data)
    except Exception as exc:   # plistlib raises a grab-bag of exception types
        # an XML plist fails through expat, whose ExpatError carries
        # lineno + a 0-based offset; binary/InvalidFileException carries neither
        line = getattr(exc, "lineno", None)
        off = getattr(exc, "offset", None)
        col = (off + 1) if (line is not None and off is not None) else None
        return _v_fail(f"{type(exc).__name__}: {exc}", line, col)
    return _v_ok("valid plist")


def _v_toml(data: bytes, name: str) -> _VResult:
    loads = None
    for modname in ("tomllib", "tomli"):   # tomllib is 3.11+, tomli the backport
        try:
            loads = __import__(modname).loads
            break
        except ImportError:
            continue
    if loads is None:
        return _v_skip("no TOML parser available (tomllib is Python 3.11+; "
                       "`pip install tomli` to validate TOML on 3.9/3.10)")
    try:
        text = _v_decode(data)
    except UnicodeDecodeError as exc:
        return _v_fail(f"not valid UTF-8: {exc}")
    try:
        loads(text)
    except Exception as exc:
        # tomllib (3.14+) / tomli (2.2+) expose lineno+colno; older builds only
        # put the position in the message text
        line = getattr(exc, "lineno", None)
        col = getattr(exc, "colno", None)
        if line is None:
            m = re.search(r"at line (\d+), column (\d+)", str(exc))
            if m:
                line, col = int(m.group(1)), int(m.group(2))
        return _v_fail(f"{type(exc).__name__}: " + " ".join(str(exc).split()),
                       line, col)
    return _v_ok("valid TOML")


def _v_yaml(data: bytes, name: str) -> _VResult:
    try:
        text = _v_decode(data)
    except UnicodeDecodeError as exc:
        return _v_fail(f"not valid UTF-8: {exc}")
    try:
        import yaml          # PyYAML — NOT stdlib
    except ImportError:
        # Conservative stdlib fallback: prove what can be proven and say
        # plainly that this is NOT a parse. (The p:verify skill additionally
        # balances flow collections; that heuristic is deliberately not
        # duplicated here — a LIMITED verdict already tells the caller the file
        # was never parsed, so the extra 90 lines buy very little.)
        for i, ln in enumerate(text.splitlines(), 1):
            stripped = ln.lstrip(" \t")
            if "\t" in ln[:len(ln) - len(stripped)]:
                return _v_limited("tab character in indentation (YAML forbids "
                                  "tabs for indentation) [stdlib pre-check; "
                                  "no PyYAML]", i)
        return _v_limited("structural pre-check passed (UTF-8 decodes, no tab "
                          "indentation) — NOT a full parse; install PyYAML for "
                          "real YAML validation")
    try:
        # Every document in the stream; safe_load_all refuses arbitrary Python
        # object construction.
        for _ in yaml.safe_load_all(text):
            pass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = (mark.line + 1) if mark is not None else None
        col = (mark.column + 1) if mark is not None else None
        first = str(exc).splitlines()[0] if str(exc) else "YAML error"
        return _v_fail(first, line, col)
    return _v_ok("valid YAML (PyYAML safe_load_all)")


def _v_node_error(err: str, target: str, rc: int) -> _VResult:
    """node's stderr -> a FAIL row. Its stack trace is dropped, not reported.

    node prints `<file>:<line>`, the offending source line, a caret line, then
    `SyntaxError: ...` and eight frames of its own loader. Only the position and
    the one-line message say anything about the validated file.
    """
    if rc == 127:               # _run's own code: node vanished after which()
        return _v_fail(" ".join(err.split()) or "node: not found in PATH")
    if rc == 124:               # ditto: timed out, so nothing was checked
        return _v_limited(" ".join(err.split()) or "node --check timed out")
    lines = err.splitlines()
    line: Optional[int] = None
    col: Optional[int] = None
    # matched on the basename so a realpath'd/symlinked target still lands, and
    # anchored so a `(node:123) Warning:` preamble cannot be mistaken for it
    head = re.compile(r"^.*" + re.escape(os.path.basename(target)) + r":(\d+)$")
    for i, ln in enumerate(lines):
        m = head.match(ln)
        if not m:
            continue
        line = int(m.group(1))
        for cand in lines[i + 1:i + 4]:
            if "^" in cand and not cand.strip("^ "):   # spaces + carets only
                col = cand.index("^") + 1
                break
        break
    msg = ""
    for ln in lines:
        if re.match(r"^\w*Error\b", ln):     # SyntaxError, and nothing indented
            msg = " ".join(ln.split())
            break
    if not msg:
        msg = next((" ".join(ln.split()) for ln in lines if ln.strip()),
                   f"node --check failed (rc={rc})")
    return _v_fail(msg, line, col)


def _v_javascript(data: bytes, name: str) -> _VResult:
    node = shutil.which("node")
    if node is None:
        # FAIL, not the SKIP a missing PyYAML/tomllib gets. The asymmetry is
        # deliberate -- do NOT harmonise it: those two are optional PARSERS, and
        # SKIP/LIMITED still leaves the batch verdict PASSED, which is honest for
        # "this host's Python cannot read TOML". Here the caller asked whether a
        # JS file parses and got NO answer at all; a SKIP row in a batch reads as
        # "nothing to see here" and would let **PASSED** stand over an unchecked
        # file. FAIL is the only rung that cannot be mistaken for success.
        return _v_fail("no `node` in PATH (install Node.js to validate "
                       "JavaScript)")
    if name != "<content>":
        # A PATH, so node decides script-vs-module itself -- extension, nearest
        # package.json "type", and on newer node its own syntax detection --
        # exactly what it does when running the file. Not re-implemented here.
        # --check NEVER executes the code (no -e/-p/require/import).
        path = os.path.abspath(name)
        rc, _out, err = _run([node, "--check", path], 15)
        if rc == 0:
            return _v_ok("valid JavaScript syntax (node --check)")
        return _v_node_error(err, path, rc)
    # Inline content has no path and no extension. `node --check` also reads a
    # script from STDIN, which is the only way to check it here: writing a temp
    # file would break this server's read-only contract. Both goals are tried,
    # module first (`import`/`export`/top-level `await` are syntax errors in a
    # script, strict-mode-only identifiers the other way round) -- with no
    # extension to decide from, text that parses under EITHER goal is valid JS.
    try:
        text = _v_decode(data)
    except UnicodeDecodeError as exc:
        return _v_fail(f"not valid UTF-8: {exc}")
    failures: List[_VResult] = []
    for goal, argv in (("ES module", [node, "--input-type=module", "--check"]),
                       ("CommonJS", [node, "--check"])):
        rc, _out, err = _run(argv, 15, stdin_text=text)
        if rc == 0:
            return _v_ok(f"valid JavaScript syntax (parsed as {goal})")
        failures.append(_v_node_error(err, "[stdin]", rc))
    return failures[0]      # the module goal's: the precise one of the two


_VALIDATORS = {
    "json":   _v_json,
    "python": _v_python,
    "yaml":   _v_yaml,
    "toml":   _v_toml,
    "xml":    _v_xml,
    "ini":    _v_ini,
    "csv":    _v_csv,
    "tsv":    _v_tsv,
    "plist":  _v_plist,
    "javascript": _v_javascript,
}


def _validate_bytes(data: bytes, fmt: str, name: str) -> _VResult:
    try:
        return _VALIDATORS[fmt](data, name)
    except Exception as exc:   # a validator bug must not sink the whole batch
        log.exception("validator %s crashed on %s", fmt, name)
        return _v_fail(f"validator error: {type(exc).__name__}: {exc}")


def _v_pos(line: Optional[int], col: Optional[int]) -> str:
    if line is None:
        return ""
    return f"{line}:{col}" if col else str(line)


def h_validate(p: dict, fmt: str = "") -> str:
    single = p.get("path") or p.get("file")
    multi = p.get("paths")
    content = p.get("content")
    if content is None:
        content = p.get("text")
    if single and multi:
        raise ValueError("pass either 'path' (one file) or 'paths' (a list) — "
                         "not both, so nothing is silently dropped.")
    if content is not None and (single or multi):
        raise ValueError("pass either 'content' (inline text) or 'path'/'paths' "
                         "(files) — not both.")
    fmt = (fmt or str(p.get("format") or p.get("fmt") or "")).strip().lower()
    if fmt and fmt not in _VALIDATORS:
        raise ValueError(f"unsupported format {fmt!r}; use one of: "
                         + ", ".join(sorted(_VALIDATORS)))
    max_mb = _int_param(p.get("max_mb", _VALIDATE_MAX_MB), "max_mb") \
        if "max_mb" in p else _VALIDATE_MAX_MB
    strict = _bool_param(p.get("strict"), False)

    rows: List[List[str]] = []
    counts = {_V_OK: 0, _V_FAIL: 0, _V_LIMITED: 0, _V_SKIP: 0}

    def record(status: str, msg: str, line: Optional[int], col: Optional[int],
               used_fmt: str, target: str) -> None:
        counts[status] = counts.get(status, 0) + 1
        rows.append([status, used_fmt or "?", _v_pos(line, col), target, msg])

    if content is not None:
        if not fmt:
            raise ValueError("validating inline 'content' requires params.format "
                             "(one of: " + ", ".join(sorted(_VALIDATORS))
                             + ") — there is no filename to detect it from.")
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            data = bytes(content)
        else:
            raise ValueError("'content' must be a string; got "
                             f"{type(content).__name__}.")
        record(*_validate_bytes(data, fmt, "<content>"), fmt, "<content>")
    else:
        # an empty 'paths' is falsy, so it has to be caught BEFORE the
        # `single or multi` collapse or it degrades to the generic message
        if isinstance(multi, (list, tuple)) and not multi and not single:
            raise ValueError("'paths' was an empty list — nothing to validate.")
        raw = single or multi
        if not raw:
            raise ValueError("validation requires params.path (one file), "
                             "params.paths (a list), or params.content "
                             "(inline text + format).")
        paths = list(raw) if isinstance(raw, list) else [raw]
        for item in paths:
            path = os.path.expanduser(str(item).strip())
            used = fmt or _VALIDATE_EXT.get(os.path.splitext(path)[1].lower(), "")
            if os.path.isdir(path):
                record(_V_SKIP, "is a directory", None, None, used, path)
                continue
            if not used:
                record(_V_SKIP, "unknown format for this extension — pass "
                       "params.format", None, None, "", path)
                continue
            try:
                size = os.path.getsize(path)
                if max_mb > 0 and size > max_mb * 1024 * 1024:
                    record(_V_SKIP, f"{_kb_human(size / 1024)} exceeds "
                           f"max_mb={max_mb}", None, None, used, path)
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                record(_V_FAIL, f"cannot read: {exc.strerror or exc}",
                       None, None, used, path)
                continue
            record(*_validate_bytes(data, used, path), used, path)

    summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    if counts[_V_FAIL]:
        verdict = "**FAILED**"
    elif strict and (counts[_V_LIMITED] or counts[_V_SKIP]):
        verdict = "**NOT VERIFIED (strict)**"
    else:
        verdict = "**PASSED**"
    # No title: the `format` column already carries the format PER ROW, which is
    # strictly better than one in a header -- a mixed-extension batch has no
    # single format to name, and the old header printed nothing at all for it.
    return (_md_fence(_fmt_table(["status", "format", "at", "target",
                                  "detail"], rows))
            + f"\n\n{verdict} — {summary}.")


def h_json(p: dict) -> str:
    return h_validate(p, "json")


def h_python(p: dict) -> str:
    return h_validate(p, "python")


def h_yaml(p: dict) -> str:
    return h_validate(p, "yaml")


def h_toml(p: dict) -> str:
    return h_validate(p, "toml")


def h_xml(p: dict) -> str:
    return h_validate(p, "xml")


def h_ini(p: dict) -> str:
    return h_validate(p, "ini")


def h_csv(p: dict) -> str:
    return h_validate(p, "csv")


def h_tsv(p: dict) -> str:
    return h_validate(p, "tsv")


def h_plist(p: dict) -> str:
    return h_validate(p, "plist")


def h_javascript(p: dict) -> str:
    return h_validate(p, "javascript")


# canonical -> (handler, one-line description)
HANDLERS: Dict[str, Tuple[Any, str]] = {
    "processes":   (h_processes, "List processes (params: filter, user, sort=cpu|mem|pid, limit)"),
    "process":     (h_process, "Detail for one PID (params: pid)"),
    "ports":       (h_ports, "Listening sockets (params: proto=tcp|udp|all)"),
    "connections": (h_connections, "Network connections (params: state=established|all)"),
    "open_files":  (h_open_files, "Open files/FDs via lsof (params: pid, port, user, path, limit)"),
    "host":        (h_host, "Host/OS/uname/uptime/cpu/loadavg"),
    "memory":      (h_memory, "Memory usage (vm_stat / free / meminfo)"),
    "disk":        (h_disk, "Filesystem usage via df (params: path)"),
    "disk_usage":  (h_disk_usage, "Directory sizes via du (params: path [req], depth, top)"),
    "mounts":      (h_mounts, "Mounted filesystems"),
    "which":       (h_which, "Resolve a binary in PATH (params: name)"),
    "env":         (h_env, "Environment vars, secrets redacted (params: key, filter, show_secrets)"),
    "stat":        (h_stat, "File/dir metadata: mode, owner, inode, times, symlink target (params: path [req])"),
    "interfaces":  (h_interfaces, "Network interfaces + addresses (params: filter)"),
    "route":       (h_route, "Routing table"),
    "pstree":      (h_pstree, "Process hierarchy as a tree (params: pid, depth, limit)"),
    "limits":      (h_limits, "Resource limits / rlimits (params: pid — per-PID on Linux only)"),
    "services":    (h_services, "launchctl/systemctl services (params: filter, user, limit)"),
    "versions":    (h_versions, "Versions of allow-listed tools (params: tools)"),
    "hash":        (h_hash, "File digest (params: path [req], algo=sha256|sha512|sha1|md5|blake2b, expect, max_mb)"),
    "sha256":      (h_sha256, "SHA-256 of a file or files (params: path [req], expect)"),
    "md5":         (h_md5, "MD5 of a file or files (params: path [req], expect)"),
    "validate":    (h_validate, "Syntax/format validation, format auto-detected from the extension (params: path | paths | content+format; format, strict, max_mb)"),
    "json":        (h_json, "Validate JSON (params: path | paths | content)"),
    "python":      (h_python, "Validate Python syntax via in-memory compile() — stronger than ast.parse, writes no .pyc (params: path | paths | content)"),
    "yaml":        (h_yaml, "Validate YAML, all documents — PyYAML if installed, else a LIMITED stdlib pre-check (params: path | paths | content)"),
    "toml":        (h_toml, "Validate TOML — needs tomllib (3.11+) or tomli, else SKIP (params: path | paths | content)"),
    "xml":         (h_xml, "Validate XML well-formedness, entity declarations refused (XXE guard) (params: path | paths | content)"),
    "ini":         (h_ini, "Validate INI/.cfg, duplicate sections/keys rejected (params: path | paths | content)"),
    "csv":         (h_csv, "Validate CSV + column-count consistency (params: path | paths | content)"),
    "tsv":         (h_tsv, "Validate TSV + column-count consistency (params: path | paths | content)"),
    "plist":       (h_plist, "Validate binary or XML plist (params: path | paths | content)"),
    "javascript":  (h_javascript, "Validate JavaScript syntax via `node --check` — parses only, never runs the code; FAIL without node; .jsx/.ts NOT covered (params: path | paths | content)"),
}

# alias -> canonical
ALIASES = {
    "ps": "processes", "proc": "processes", "procs": "processes",
    "listening": "ports", "netstat": "ports", "ss": "ports", "port": "ports",
    "lsof": "open_files", "openfiles": "open_files", "fds": "open_files",
    "conn": "connections", "connection": "connections",
    "uname": "host", "sysinfo": "host", "system": "host",
    "mem": "memory", "free": "memory", "vm_stat": "memory", "vmstat": "memory",
    "df": "disk", "filesystem": "disk", "fs": "disk",
    "du": "disk_usage", "usage": "disk_usage",
    "mount": "mounts",
    "environment": "env", "printenv": "env",
    "file_info": "stat", "fileinfo": "stat", "metadata": "stat",
    "ifconfig": "interfaces", "ip": "interfaces", "interface": "interfaces",
    "nics": "interfaces", "addr": "interfaces",
    "routes": "route", "routing": "route", "routetable": "route",
    "tree": "pstree", "ptree": "pstree", "processtree": "pstree",
    "ulimit": "limits", "rlimit": "limits", "rlimits": "limits",
    "service": "services", "launchctl": "services", "systemctl": "services",
    "units": "services", "daemons": "services",
    "version": "versions", "toolchain": "versions", "tools": "versions",
    "checksum": "hash", "digest": "hash",
    "sha256sum": "sha256", "shasum": "sha256", "sha": "sha256",
    "md5sum": "md5",
    "lint": "validate", "check": "validate", "verify": "validate",
    "syntax": "validate", "parse": "validate", "wellformed": "validate",
    "py": "python", "ast": "python", "py_compile": "python",
    "pycompile": "python", "python3": "python",
    "yml": "yaml", "jsonlint": "json", "xmllint": "xml", "plutil": "plist",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript",
    "node": "javascript", "nodejs": "javascript",
}


def _status_text(project_root: Optional[str]) -> str:
    lines = ["## mcp-inspect", "",
             f"Platform: `{platform.platform()}`  ({_SYS})",
             f"Hostname: `{socket.gethostname()}`", ""]
    if project_root:
        lines.append(f"Project root (du/df default base): `{project_root}`\n")
    bins = ["ps", "lsof", "ss", "netstat", "df", "du", "mount", "free",
            "vm_stat", "sysctl", "uptime", "uname", "ip", "ifconfig",
            "launchctl", "systemctl"]
    avail = ", ".join(f"{b}{'' if _have(b) else '✗'}" for b in bins)
    lines.append(f"Underlying binaries (✗ = missing): {avail}\n")
    has_toml = _mod_present("tomllib") or _mod_present("tomli")
    lines.append(
        "Optional validation parsers (✗ = missing, that format degrades to "
        f"LIMITED/SKIP): PyYAML{'' if _mod_present('yaml') else '✗'}, "
        f"tomllib/tomli{'' if has_toml else '✗'}. External binary: "
        f"node{'' if _have('node') else '✗'} — javascript is validated by "
        "`node --check`, and its absence FAILS the row instead of degrading it "
        "(an unchecked file must not read as PASSED). Everything else "
        "(json/python/xml/ini/csv/tsv/plist) is stdlib.\n")
    lines.append("Functions (all READ-ONLY):\n")
    for name, (_, desc) in HANDLERS.items():
        al = [a for a, c in ALIASES.items() if c == name]
        alias_str = f"  _(aliases: {', '.join(al)})_" if al else ""
        lines.append(f"- `{name}` — {desc}{alias_str}")
    return "\n".join(lines)


def handle_inspect_call(arguments: dict, project_root: Optional[str]) -> dict:
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    raw_params = arguments.get("params") or arguments.get("p") or {}
    try:
        params = _ensure_dict(raw_params)
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        return {"__raw_text__": _status_text(project_root)}

    canonical = ALIASES.get(function, function)
    entry = HANDLERS.get(canonical)
    if entry is None:
        return {"error": (
            f"unknown function '{function}'. Available: "
            + ", ".join(sorted(HANDLERS)) + ". Call with no 'function' for details."
        )}
    handler = entry[0]
    try:
        md = handler(params)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # defensive; a handler bug must not hang the client
        log.exception("handler %s crashed", canonical)
        return {"error": f"internal error in '{canonical}': {type(exc).__name__}: {exc}"}

    max_chars = params.get("max_answer_chars", DEFAULT_MAX_CHARS)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    md, truncated = _truncate(md, max_chars)
    if truncated:
        md += f"\n\n_output truncated at {max_chars} chars_"
    return {"__raw_text__": md}


# ---------------------------------------------------------------------------
# MCP server (mcp-git shape)
# ---------------------------------------------------------------------------

INSPECT_CALL_TOOL = {
    "name": "inspect_call",
    "description": (
        "Read-only, non-invasive system inspection: processes, open files, "
        "sockets, memory, disk, host, file metadata, file digests, network topology, "
        "services, toolchain versions, environment — plus SYNTAX/FORMAT VALIDATION "
        "of json, python, yaml, toml, xml, ini, csv, tsv, plist and javascript. "
        "PREFER THIS over Bash for `ps`, `lsof`, "
        "`netstat`, `ss`, `df`, `du`, `free`, `env`, `stat`, `ifconfig`/`ip addr`, "
        "`pstree`, `ulimit`, `launchctl`/`systemctl`, `<tool> --version` as the "
        "PRIMARY command — it is pre-approved (no permission prompt) and returns "
        "structured Markdown. (Piping a stream into grep/etc. in Bash is still "
        "fine — that is not what this replaces.)\n\n"
        "Also replaces the validate-by-shell one-liners (`ast.parse`, "
        "`py_compile`, `json.tool`, `jq .`, `xmllint --noout`, `node --check`): "
        "reports line:col and writes nothing.\n\n"
        "Single-tool dispatcher: pass `function` + `params` (or `f` + `p`). "
        "Called without `function` → server status + full function list.\n\n"
        "Functions (aliases in parens):\n"
        "  processes (ps)        params: filter, user, sort=cpu|mem|pid, limit\n"
        "  process               params: pid\n"
        "  ports (netstat/ss)    params: proto=tcp|udp|all\n"
        "  connections           params: state=established|all\n"
        "  open_files (lsof)     params: pid, port, user, path, limit\n"
        "  host (uname)          host/OS/uptime/cpu/loadavg\n"
        "  memory (free/vm_stat) memory usage\n"
        "  disk (df)             params: path\n"
        "  disk_usage (du)       params: path [required], depth, top\n"
        "  mounts                mounted filesystems\n"
        "  which                 params: name\n"
        "  env                   params: key, filter; secret-looking values "
        "redacted unless show_secrets=true\n"
        "  stat (file_info)      params: path [required] — mode/owner/inode/times/"
        "symlink target/dir entry count\n"
        "  interfaces (ifconfig) params: filter\n"
        "  route (routes)        routing table\n"
        "  pstree (tree)         params: pid, depth, limit — parent/child process tree\n"
        "  limits (ulimit)       params: pid (per-PID on Linux only)\n"
        "  services (launchctl)  params: filter, user, limit\n"
        "  versions (toolchain)  params: tools — allow-listed binaries only\n"
        "  sha256 (shasum)       params: path [required] (or a list), expect\n"
        "  md5 (md5sum)          params: path [required] (or a list), expect\n"
        "  hash (checksum)       params: path [required], algo=sha256|sha512|sha1|"
        "md5|blake2b, expect, max_mb\n"
        "  validate (lint/check) params: path | paths (a LIST — check many files "
        "in ONE call) | content+format; format (else from the extension), "
        "strict, max_mb (0 = no cap)\n"
        "  json python yaml toml xml ini csv tsv plist javascript — each is also "
        "its own function, same params, format pinned; aliases "
        "py/ast/yml/xmllint/plutil/js. javascript is `node --check` (syntax "
        "only, never executed; .js/.mjs/.cjs — NOT .jsx/.ts; FAIL if node is "
        "not installed). Per-format detail: the p:mcp-inspect skill.\n\n"
        "Everything is READ-ONLY (no mutation, shell=False, no injection surface). "
        "Example: function=\"processes\", params={\"filter\":\"node\",\"sort\":\"mem\"}"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {"type": "string", "description": "Inspection function (see description)."},
            "params":   {"type": "object", "description": "Function parameters."},
        },
    },
}


# How many tool calls may be in flight at once. The stdin reader owns a thread of
# its own, OUTSIDE this pool, so saturating it delays queued CALLS and can never
# stop the server from READING -- which is the whole point of the split in run().
MAX_INFLIGHT_REQUESTS = 8


class McpServer:
    def __init__(self, project_root: Optional[str]):
        self.project_root = os.path.realpath(project_root) if project_root else None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting (%s)", _SYS)
        # TWO executors, and one task per request, on purpose. This loop used to
        # call the handler INLINE on the event-loop thread, between the two
        # awaits of the readline -- so for as long as a handler ran, the server
        # did not read stdin. `du` is a 30s budget and `open_files` a 20s one;
        # every request behind one of those sat unread in the pipe, timed out
        # client-side at ~60s, and was then answered against an id the client had
        # already abandoned. From the caller's chair that is a dead server with a
        # restart as the only lever -- and because this server is pre-approved
        # and called constantly, it is the one where that is felt most often.
        #
        # The reader gets a pool OF ITS OWN. One shared pool would let
        # MAX_INFLIGHT_REQUESTS slow handlers occupy every worker and leave the
        # readline with nowhere to run: the same deafness, reintroduced by the
        # fix and much harder to see.
        #
        # Handlers are safe to run concurrently, and that was AUDITED rather than
        # assumed. The module declares no `global` anywhere; it caches nothing --
        # no memoised which()/versions table, no host or process snapshot; and
        # _VERSION_TOOLS / _VALIDATE_EXT / _VALIDATORS / HANDLERS / ALIASES are
        # built at import time and thereafter only read (`.get`, indexing,
        # sorted). Every list and dict a handler appends to is created inside that
        # handler, the probes are read-only with shell=False, and project_root is
        # written once in __init__. The ONE exception the audit turned up is the
        # process-global warnings filter that `_v_python` must swap to see
        # SyntaxWarnings, and that is serialised at the source by _V_PYTHON_LOCK
        # instead of costing everything else its concurrency.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inspect-stdin")
        workers = ThreadPoolExecutor(max_workers=MAX_INFLIGHT_REQUESTS,
                                     thread_name_prefix="inspect-call")
        inflight: set = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    # A closed or detached stdin raises here; unwrapped, it
                    # propagated out of run() as a traceback instead of a
                    # shutdown, and the `finally` never got to cancel anything.
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
                    # Answering is not optional: the bare `continue` that used to
                    # be here left the caller's request id unanswered until it
                    # timed out, for a line the server had already understood was
                    # broken.
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    # `5` is valid JSON. It used to reach msg.get() and take the
                    # process down with an AttributeError escaping run() -- and an
                    # MCP client does not respawn a dead stdio server.
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue

                log.debug("← %s", json.dumps(msg)[:200])
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
            # Exception, NOT BaseException: the `finally` in run() cancels every
            # inflight task on shutdown, and a swallowed CancelledError would
            # turn each of those into a bogus -32603 written to a stdout that is
            # already gone.
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            self._write(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message.

        Called only from the event-loop thread: handlers run in the worker pool,
        but `_serve` resumes on the loop after its await, so two replies cannot
        interleave mid-line and this needs no lock.
        """
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {exc}"))
        log.debug("→ %s", out[:200])
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            # Unguarded, a client that hung up mid-reply killed the process with
            # the write instead of letting the read loop notice the closed stdin.
            log.warning("stdout write failed: %s", exc)

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mcp-inspect", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [INSPECT_CALL_TOOL]})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name != "inspect_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
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
        try:
            result = handle_inspect_call(arguments, self.project_root)
        except Exception as exc:
            log.exception("Unhandled exception in handle_inspect_call")
            result = {"error": f"Internal server error: {type(exc).__name__}: {exc}"}
        is_error = "error" in result
        text = result.get("__raw_text__") or result.get("error", "")
        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main() -> None:
    if "--list" in sys.argv:
        print(_status_text(None))
        sys.exit(0)

    parser = argparse.ArgumentParser(description="mcp-inspect — read-only system inspection MCP server")
    parser.add_argument("--project-root", help="Optional base dir for relative du/df paths")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()

    level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
    log_handlers: list = []
    if args.log_file:
        log_handlers.append(logging.FileHandler(args.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    server = McpServer(args.project_root)
    asyncio.run(server.run())
    # stdin is closed, so the client is gone. Handler threads live in the
    # server's OWN executors rather than the loop's default one, so asyncio does
    # not join them on the way out -- but concurrent.futures registers an atexit
    # hook that would, and one handler mid-`du` would hold this process open for
    # the rest of its 30s budget after the client had already left. Every reply
    # is flushed as it is written and logging flushes per record, so there is
    # nothing left to drain.
    os._exit(0)


if __name__ == "__main__":
    main()
