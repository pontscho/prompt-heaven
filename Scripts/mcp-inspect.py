#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""mcp-inspect — read-only, non-invasive system/process/network inspection.

Single-tool dispatcher pattern (like mcp-git): one MCP tool (inspect_call)
routes to internal handlers via the 'function' parameter. Every handler is
READ-ONLY — it inspects live system state (processes, open files, sockets,
memory, disk, host, environment) and NEVER mutates anything. There is no way
to pass a raw shell string: each function builds a fixed argv (shell=False),
filters are applied in Python, and numeric params (pid/port) are int-validated,
so there is no shell-injection surface.

Purpose: let the model run the common non-invasive `ps` / `lsof` / `netstat` /
`ss` / `df` / `du` / `free` / `env` inspections through a single pre-approved
MCP tool instead of per-call Bash prompts.

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
    """Run an argv (shell=False) read-only. Never raises; returns (rc, out, err)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"
    except OSError as exc:
        return 1, "", f"{cmd[0]}: {exc}"


def _have(name: str) -> bool:
    return shutil.which(name) is not None


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
    head = f"## processes (sort={sort}"
    if filt:
        head += f", filter={filt!r}"
    if user:
        head += f", user={user}"
    head += f") — showing {len(rows)} of {total}"
    return head + "\n\n" + _md_fence(body)


def h_process(p: dict) -> str:
    if "pid" not in p:
        raise ValueError("'process' requires params.pid (integer).")
    pid = _int_param(p["pid"], "pid")
    fmt = "pid,ppid,pgid,user,pcpu,pmem,rss,vsz,stat,nice,etime,comm,args"
    rc, out, err = _run(["ps", "-o", fmt, "-p", str(pid)], 15)
    if rc != 0 or not out.strip() or len(out.splitlines()) < 2:
        raise ValueError(err.strip() or f"no such process: {pid}")
    header, rows = _parse_ps(out)
    parts = [f"## process {pid}", "", _md_fence(_fmt_table(header, rows))]
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
    return f"## listening ports — {label}\n\n" + (_md_fence(out.strip()) if out.strip() else "_(none)_")


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
    return f"## connections — {label}\n\n" + (_md_fence(out.strip()) if out.strip() else "_(none)_")


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
    return f"## open files\n\n{_md_fence(chr(10).join(lines))}{note}"


def h_host(p: dict) -> str:
    parts = ["## host", ""]
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
        parts = ["## memory (macOS)", ""]
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
            return "## memory (Linux)\n\n" + _md_fence(out.strip())
    try:
        with open("/proc/meminfo") as f:
            data = f.read()
        return "## memory (/proc/meminfo)\n\n" + _md_fence(data.strip())
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
    return "## disk (df)\n\n" + _md_fence(out.strip())


def h_disk_usage(p: dict) -> str:
    if not p.get("path") or not str(p["path"]).strip():
        raise ValueError("'disk_usage' requires params.path.")
    path = os.path.realpath(str(p["path"]).strip())
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
    if top > 0:
        entries = entries[:top]
    body = "\n".join(f"{_kb_human(kb):>9}  {name}" for kb, name in entries)
    note = ""
    if err.strip():
        note = "\n\n_(some paths were not readable and were skipped)_"
    return f"## disk usage — `{path}` (depth {depth}, top {top})\n\n{_md_fence(body)}{note}"


def h_mounts(p: dict) -> str:
    if _have("mount"):
        rc, out, err = _run(["mount"], 10)
        if rc == 0 and out.strip():
            return "## mounts\n\n" + _md_fence(out.strip())
    try:
        with open("/proc/mounts") as f:
            return "## mounts (/proc/mounts)\n\n" + _md_fence(f.read().strip())
    except OSError as exc:
        raise ValueError(f"cannot read mounts: {exc}")


def h_which(p: dict) -> str:
    name = p.get("name") or p.get("cmd") or p.get("command")
    if not name:
        raise ValueError("'which' requires params.name (binary to resolve).")
    names = name if isinstance(name, list) else [name]
    lines = ["## which", ""]
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
        return "## env\n\n" + render(k, env[k])
    keys = sorted(env)
    if filt:
        keys = [k for k in keys if filt in k.lower()]
    if not keys:
        return "_no matching env vars_"
    lines = [f"## env ({len(keys)} vars"
             + (f", filter={filt!r}" if filt else "") + ")", ""]
    lines += [render(k, env[k]) for k in keys]
    if not show_secrets:
        lines.append("\n_secret-looking values redacted; pass show_secrets=true to reveal_")
    return "\n".join(lines)


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
}


def _status_text(project_root: Optional[str]) -> str:
    lines = ["## mcp-inspect", "",
             f"Platform: `{platform.platform()}`  ({_SYS})",
             f"Hostname: `{socket.gethostname()}`", ""]
    if project_root:
        lines.append(f"Project root (du/df default base): `{project_root}`\n")
    bins = ["ps", "lsof", "ss", "netstat", "df", "du", "mount", "free",
            "vm_stat", "sysctl", "uptime", "uname"]
    avail = ", ".join(f"{b}{'' if _have(b) else '✗'}" for b in bins)
    lines.append(f"Underlying binaries (✗ = missing): {avail}\n")
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
        "sockets, memory, disk, host, environment. PREFER THIS over Bash for "
        "`ps`, `lsof`, `netstat`, `ss`, `df`, `du`, `free`, `env` as the PRIMARY "
        "command — it is pre-approved (no permission prompt) and returns "
        "structured Markdown. (Piping a stream into grep/etc. in Bash is still "
        "fine — that is not what this replaces.)\n\n"
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
        "redacted unless show_secrets=true\n\n"
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
