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
well-formedness of a file (json, python, yaml, toml, xml, ini, csv, tsv, plist),
and NEVER mutates anything. There is no way
to pass a raw shell string: each function builds a fixed argv (shell=False),
filters are applied in Python, and numeric params (pid/port) are int-validated,
so there is no shell-injection surface.

Purpose: let the model run the common non-invasive `ps` / `lsof` / `netstat` /
`ss` / `df` / `du` / `free` / `env` / `stat` / `ifconfig` / `pstree` / `ulimit` /
`launchctl` / `<tool> --version` / `shasum` / `md5sum` inspections — and the
`python3 -c "import ast; ast.parse(...)"` / `py_compile` / `json.tool` / `jq .` /
`xmllint --noout` validation one-liners — through a single pre-approved MCP tool
instead of per-call Bash prompts. Validators run in-process (stdlib parsers), so
nothing is written: `py_compile` in particular would leave a __pycache__/*.pyc.

The one execution-shaped function, `versions`, probes only an ALLOW-LISTED set of
binary NAMES (_VERSION_TOOLS) with fixed flags; the caller can never supply argv.

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


def _run(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    """Run an argv (shell=False) read-only. Never raises; returns (rc, out, err).

    stdin=DEVNULL, always: this server's stdin is the JSON-RPC stream, and
    capture_output only redirects stdout/stderr -- stdin would be INHERITED.
    A child that reads it (any of these tools when a flag makes it wait on
    input) would silently swallow protocol messages. Every probe below is
    read-only and takes no input, so DEVNULL costs nothing here.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
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


def _fmt_table(header: List[str], rows: List[List[str]]) -> str:
    widths = [len(h) for h in header]
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
        return "  ".join(out)
    return "\n".join([fmt_row(header)] + [fmt_row(r) for r in rows])


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
    timeout = _int_param(p.get("timeout", 15), "timeout") if "timeout" in p else 15

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
    timeout = _int_param(p.get("timeout", 15), "timeout") if "timeout" in p else 15

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
    return f"_{label}_\n\n" + (_md_fence(out.strip()) if out.strip() else "_(none)_")


def h_connections(p: dict) -> str:
    state = (p.get("state") or "established").strip().lower()
    timeout = _int_param(p.get("timeout", 15), "timeout") if "timeout" in p else 15
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
    return f"_{label}_\n\n" + (_md_fence(out.strip()) if out.strip() else "_(none)_")


def h_open_files(p: dict) -> str:
    if not _have("lsof"):
        raise ValueError("lsof not available on this host.")
    timeout = _int_param(p.get("timeout", 20), "timeout") if "timeout" in p else 20
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
    return f"{_md_fence(chr(10).join(lines))}{note}"


def h_host(p: dict) -> str:
    parts = []
    parts.append(f"- hostname: `{socket.gethostname()}`")
    parts.append(f"- platform: `{platform.platform()}`")
    rc, out, _ = _run(["uname", "-a"], 5)
    if rc == 0 and out.strip():
        parts.append(f"- uname: `{out.strip()}`")
    if IS_MAC:
        rc, out, _ = _run(["sw_vers"], 5)
        if rc == 0 and out.strip():
            parts.append("\n" + _md_fence(out.strip()))
        rc, out, _ = _run(["sysctl", "-n", "hw.ncpu"], 5)
        if rc == 0 and out.strip():
            parts.append(f"- cpus: {out.strip()}")
    else:
        try:
            with open("/etc/os-release") as f:
                osr = {k: v.strip('"') for k, v in
                       (ln.split("=", 1) for ln in f.read().splitlines() if "=" in ln)}
            if osr.get("PRETTY_NAME"):
                parts.append(f"- os: {osr['PRETTY_NAME']}")
        except OSError:
            pass
        parts.append(f"- cpus: {os.cpu_count()}")
    try:
        la = os.getloadavg()
        parts.append(f"- loadavg: {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}")
    except (OSError, AttributeError):
        pass
    rc, out, _ = _run(["uptime"], 5)
    if rc == 0 and out.strip():
        parts.append(f"- uptime: `{out.strip()}`")
    return "\n".join(parts)


def h_memory(p: dict) -> str:
    if IS_MAC:
        # No source label on any of the three branches: `vm_stat`, `free -h` and
        # /proc/meminfo are unmistakable from their own first line, so naming
        # them would restate what the payload already says.
        parts = []
        rc, out, _ = _run(["sysctl", "-n", "hw.memsize"], 5)
        if rc == 0 and out.strip().isdigit():
            parts.append(f"- total: {_kb_human(int(out.strip()) / 1024)}")
        rc, out, _ = _run(["vm_stat"], 5)
        if rc == 0 and out.strip():
            parts.append("\n" + _md_fence(out.strip()))
        else:
            raise ValueError("vm_stat unavailable")
        return "\n".join(parts)
    # linux
    if _have("free"):
        rc, out, err = _run(["free", "-h"], 5)
        if rc == 0 and out.strip():
            return _md_fence(out.strip())
    try:
        with open("/proc/meminfo") as f:
            data = f.read()
        return _md_fence(data.strip())
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
                rebuilt.append("  ".join(cols))
            else:
                rebuilt.append(ln)
        out = "\n".join(rebuilt)
    return _md_fence(out.strip())


def h_disk_usage(p: dict) -> str:
    if not p.get("path") or not str(p["path"]).strip():
        raise ValueError("'disk_usage' requires params.path.")
    raw = str(p["path"]).strip()
    path = os.path.realpath(raw)
    if not os.path.exists(path):
        raise ValueError(f"path does not exist: {path}")
    depth = _int_param(p.get("depth", 1), "depth") if "depth" in p else 1
    top = _int_param(p.get("top", 20), "top") if "top" in p else 20
    timeout = _int_param(p.get("timeout", 30), "timeout") if "timeout" in p else 30
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
    body = "\n".join(f"{_kb_human(kb):>9}  {os.path.relpath(name, path)}"
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
            return _md_fence(out.strip())
    try:
        with open("/proc/mounts") as f:
            # The fallback IS news: `mount` was unavailable, and /proc/mounts
            # has a different column layout.
            return "_/proc/mounts_\n\n" + _md_fence(f.read().strip())
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
            extra = f" → `{real}`" if real != resolved else ""
            lines.append(f"- `{n}`: `{resolved}`{extra}")
        else:
            lines.append(f"- `{n}`: _not found in PATH_")
    return "\n".join(lines)


def h_env(p: dict) -> str:
    key = p.get("key")
    filt = (p.get("filter") or "").strip().lower()
    show_secrets = _bool_param(p.get("show_secrets", False))

    def render(k: str, v: str) -> str:
        if _SECRET_KEY_RE.search(k) and not show_secrets:
            return f"- `{k}` = ***REDACTED*** _(len {len(v)})_"
        return f"- `{k}` = `{v}`"

    env = dict(os.environ)
    if key:
        k = str(key)
        if k not in env:
            return f"_env var `{k}` is not set_"
        return render(k, env[k])
    keys = sorted(env)
    if filt:
        keys = [k for k in keys if filt in k.lower()]
    if not keys:
        return "_no matching env vars_"
    # The count survives, the filter does not: how many matched is news, what
    # was matched against is the caller's own word.
    lines = [f"_{len(keys)} vars_", ""]
    lines += [render(k, env[k]) for k in keys]
    if not show_secrets:
        lines.append("\n_secret-looking values redacted; pass show_secrets=true to reveal_")
    return "\n".join(lines)


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
        return f"{uname or uid}:{gname or gid}  (uid={uid}, gid={gid})"

    lines = [f"- type   : {kind}",
             f"- mode   : `{st_mod.filemode(st.st_mode)}`  ({oct(st_mod.S_IMODE(st.st_mode))})",
             f"- owner  : {owner(st.st_uid, st.st_gid)}",
             f"- size   : {st.st_size} B ({_kb_human(st.st_size / 1024)})",
             f"- links  : {st.st_nlink}",
             f"- inode  : {st.st_ino}  (device {st.st_dev})",
             f"- mtime  : {_fmt_time(st.st_mtime)}",
             f"- atime  : {_fmt_time(st.st_atime)}",
             f"- ctime  : {_fmt_time(st.st_ctime)}"]
    if path != str(raw).strip():
        # expanduser() rewrote the caller's own argument -- THAT is news.  An
        # unchanged path is not: they are holding the string they sent.
        lines[:0] = [f"_`{path}`_", ""]

    if is_link:
        try:
            lines.append(f"- target : `{os.readlink(path)}`")
        except OSError:
            pass
        real = os.path.realpath(path)
        broken = "" if os.path.exists(real) else "  _(BROKEN — target missing)_"
        lines.append(f"- resolves to: `{real}`{broken}")
    else:
        real = os.path.realpath(path)
        if real != os.path.abspath(path):
            lines.append(f"- realpath: `{real}`  _(path traverses a symlink)_")

    if st_mod.S_ISDIR(st.st_mode):
        try:
            entries = os.listdir(path)
            ndirs = sum(1 for e in entries
                        if os.path.isdir(os.path.join(path, e)))
            lines.append(f"- entries: {len(entries)} "
                         f"({len(entries) - ndirs} files, {ndirs} dirs)")
        except OSError as exc:
            lines.append(f"- entries: _unreadable ({exc.strerror})_")

    acc = "".join(n for n, m in (("r", os.R_OK), ("w", os.W_OK), ("x", os.X_OK))
                  if os.access(path, m))
    lines.append(f"- access for this server process: {acc or 'none'}")
    return "\n".join(lines)


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
    return head + "\n\n" + _md_fence(text)


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
    return f"_{label}_\n\n" + (_md_fence(out.strip()) if out.strip() else "_(empty)_")


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
            lines.append(f"{pid:>7}  {user:<10} {comm}")
            child_prefix = ""
        else:
            branch = "`- " if is_last else "|- "
            lines.append(f"{pid:>7}  {user:<10} {prefix}{branch}{comm}")
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
    return head + "\n\n" + _md_fence("\n".join(lines)) + note


def h_limits(p: dict) -> str:
    import resource

    parts: List[str] = []
    if "pid" in p:
        pid = _int_param(p["pid"], "pid")
        if IS_LINUX:
            try:
                with open(f"/proc/{pid}/limits") as f:
                    return _md_fence(f.read().strip())
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
            parts += ["", "### system (launchctl limit)", "", _md_fence(out.strip())]
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
    text = "\n".join(([header] if header else []) + body_lines)
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
                             # server loop is single-threaded while hashing
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
            rows.append(["_is a directory_", "", path])
            continue
        try:
            size = os.path.getsize(path)
            if max_mb > 0 and size > max_mb * 1024 * 1024:
                rows.append([f"_skipped: {_kb_human(size / 1024)} exceeds "
                             f"max_mb={max_mb}_", "", path])
                continue
            digest_obj = hashlib.new(algo)
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
                    digest_obj.update(chunk)
        except OSError as exc:
            rows.append([f"_error: {exc.strerror or exc}_", "", path])
            continue
        digest = digest_obj.hexdigest()
        rows.append([digest,
                     f"{size} B" if size < 1024 else _kb_human(size / 1024),
                     path])
        if expect:
            verdict = digest == expect

    # No "## sha256" title: the table's FIRST COLUMN is named after the algo,
    # so the title restated a word the payload already carries.
    out = _md_fence(_fmt_table([algo, "size", "path"], rows))
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
# platform, and its output has to be re-parsed. Two deliberate choices:
#   * Python is compiled with compile() IN MEMORY — NEVER py_compile, which
#     writes __pycache__/*.pyc and would make this read-only server mutate the
#     tree. compile() is also strictly stronger than ast.parse: it runs the
#     symtable/codegen pass, so `break` outside a loop, `return` outside a
#     function and module-level `nonlocal` are caught too.
#   * XML entity declarations are refused (XXE + billion-laughs guard), so the
#     validator is safe on untrusted input.
# This is FORMAL well-formedness (does it parse), NOT schema validation.
# Verdict vocabulary matches the p:verify skill: OK / FAIL / LIMITED / SKIP.

_V_OK = "OK"
_V_FAIL = "FAIL"
_V_LIMITED = "LIMITED"
_V_SKIP = "SKIP"

# extension -> format. Parity with ClaudeCode/skills/verify/scripts/validate.py,
# plus .py/.pyi which that script does not cover.
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
}

_VALIDATE_MAX_MB = 32        # parsers build a full in-memory tree; the server
                             # loop is single-threaded while parsing

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


def _v_python(data: bytes, name: str) -> _VResult:
    import warnings

    caught: List[Any] = []
    try:
        with warnings.catch_warnings(record=True) as caught:
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
        f"tomllib/tomli{'' if has_toml else '✗'}. Everything else "
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
        "of json, python, yaml, toml, xml, ini, csv, tsv and plist. "
        "PREFER THIS over Bash for `ps`, `lsof`, "
        "`netstat`, `ss`, `df`, `du`, `free`, `env`, `stat`, `ifconfig`/`ip addr`, "
        "`pstree`, `ulimit`, `launchctl`/`systemctl`, `<tool> --version` as the "
        "PRIMARY command — it is pre-approved (no permission prompt) and returns "
        "structured Markdown. (Piping a stream into grep/etc. in Bash is still "
        "fine — that is not what this replaces.)\n\n"
        "Also replaces the validate-by-shell one-liners (`ast.parse`, "
        "`py_compile`, `json.tool`, `jq .`, `xmllint --noout`): reports "
        "line:col and writes nothing.\n\n"
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
        "  json python yaml toml xml ini csv tsv plist — each is also its own "
        "function, same params, format pinned; aliases py/ast/yml/xmllint/plutil. "
        "Per-format detail: the p:mcp-inspect skill.\n\n"
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


class McpServer:
    def __init__(self, project_root: Optional[str]):
        self.project_root = os.path.realpath(project_root) if project_root else None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting (%s)", _SYS)
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("Invalid JSON: %s", exc)
                    continue
                log.debug("← %s", json.dumps(msg)[:200])
                try:
                    response = self._handle_message(msg)
                except Exception as exc:
                    log.exception("Unhandled exception while handling message")
                    response = self._error(
                        msg.get("id"), -32603,
                        f"Internal error: {type(exc).__name__}: {exc}",
                    )
                if response is not None:
                    out = json.dumps(response)
                    log.debug("→ %s", out[:200])
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()
        finally:
            log.info("MCP server shutting down")

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


if __name__ == "__main__":
    main()
