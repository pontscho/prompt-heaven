#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""mcp-tshark — MCP server for tshark (Wireshark CLI) network capture & analysis.

Usage:
    python3 mcp-tshark.py --project-root /path/to/project
    python3 mcp-tshark.py --list

Single-tool dispatcher: tshark_call(function, params)

Functions:
    start_capture    Start background packet capture
    stop_capture     Stop capture, return metadata (use analyze separately)
    analyze          Analyze a PCAP file
    list_sessions    List capture sessions
    list_interfaces  List network interfaces
    statistics       Protocol statistics from PCAP
    follow_stream    Reconstruct and follow a stream
    config           Save/load capture configurations
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("mcp-tshark")

# ---------------------------------------------------------------------------
# tshark discovery
# ---------------------------------------------------------------------------
_tshark_path: Optional[str] = None


def find_tshark() -> Optional[str]:
    """Locate the tshark binary."""
    path = shutil.which("tshark")
    if path:
        return path
    candidates = ["/usr/bin/tshark", "/usr/local/bin/tshark", "/snap/bin/tshark"]
    if platform.system() == "Darwin":
        candidates.append("/Applications/Wireshark.app/Contents/MacOS/tshark")
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _get_tshark() -> str:
    """Return cached tshark path or raise."""
    global _tshark_path
    if _tshark_path is None:
        _tshark_path = find_tshark()
    if _tshark_path is None:
        raise FileNotFoundError(
            "tshark not found. Install Wireshark/tshark and ensure it is on PATH."
        )
    return _tshark_path


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------
SESSIONS: Dict[str, dict] = {}
CAPTURES_DIR = ".tshark-captures"

# Requests are dispatched CONCURRENTLY (see McpServer.run), which makes SESSIONS
# the one piece of shared mutable state in this module that can be corrupted:
# two handlers writing the dict, or two stop_captures tearing down the same
# capture. Every read and every write of it happens under this lock.
#
# The lock covers BOOKKEEPING ONLY — the id reservation, the field updates, the
# snapshot list_sessions renders from, and the pop that ends a session. It is
# never held across a tshark run: a capture lives for its whole `-a duration:`
# and _packet_count alone may take 30s, so holding it there would just relocate
# the freeze this file was fixed to remove.
#
# Ownership of a capture's CHILD PROCESS is transferred rather than shared:
# whoever pops `session["process"]` under this lock is the only one allowed to
# signal it. That is what stops two stop_captures — or a stop_capture racing
# shutdown — from running the kill ladder against the same pid, and it is why
# the key is absent until there is a child to kill.
_SESSIONS_LOCK = threading.Lock()


def _session_id() -> str:
    return f"{int(time.time())}-{os.urandom(4).hex()}"


def _captures_path(project_root: str) -> str:
    path = os.path.join(project_root, CAPTURES_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGTERM → wait(5) → SIGKILL a process group."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _cleanup_sessions() -> None:
    """Kill all active capture processes.

    Runs on the event-loop thread as the server shuts down, while handler
    threads may still be mid-flight. The registry is emptied and every child
    CLAIMED (popped) under the lock in one step, so an in-flight stop_capture
    can no longer find a process to signal; the SIGTERM -> wait(5) -> SIGKILL
    ladder then runs OUTSIDE the lock, because up to 10s per child is precisely
    the kind of wait that must not sit inside one.
    """
    with _SESSIONS_LOCK:
        claimed = [(sid, session.pop("process", None))
                   for sid, session in SESSIONS.items()]
        SESSIONS.clear()
    for sid, proc in claimed:
        if proc:
            _kill_process_group(proc)
            log.info("Cleaned up session %s", sid)


# ---------------------------------------------------------------------------
# Field presets
# ---------------------------------------------------------------------------
_DEFAULT_FIELDS = [
    "frame.number",
    "frame.time_relative",
    "ip.src",
    "ip.dst",
    "_ws.col.Protocol",
    "frame.len",
    "_ws.col.Info",
]

FIELD_PRESETS: Dict[str, List[str]] = {
    "default": _DEFAULT_FIELDS,
    "tcp": _DEFAULT_FIELDS + [
        "tcp.srcport", "tcp.dstport", "tcp.flags", "tcp.stream",
    ],
    "http": _DEFAULT_FIELDS + [
        "http.request.method", "http.request.uri", "http.response.code",
    ],
    "dns": _DEFAULT_FIELDS + [
        "dns.qry.name", "dns.qry.type", "dns.a",
    ],
    "tls": _DEFAULT_FIELDS + [
        "tls.handshake.type", "tls.handshake.extensions_server_name",
    ],
}

FIELD_HEADERS: Dict[str, str] = {
    "frame.number":          "#",
    "frame.time_relative":   "Time",
    "ip.src":                "Source",
    "ip.dst":                "Destination",
    "_ws.col.Protocol":      "Protocol",
    "frame.len":             "Length",
    "_ws.col.Info":          "Info",
    "tcp.srcport":           "Src Port",
    "tcp.dstport":           "Dst Port",
    "tcp.flags":             "TCP Flags",
    "tcp.stream":            "Stream",
    "http.request.method":   "Method",
    "http.request.uri":      "URI",
    "http.response.code":    "Status",
    "dns.qry.name":          "Query",
    "dns.qry.type":          "Type",
    "dns.a":                 "Answer",
    "tls.handshake.type":    "HS Type",
    "tls.handshake.extensions_server_name": "SNI",
}

# ---------------------------------------------------------------------------
# Parameter aliases
# ---------------------------------------------------------------------------
PARAM_ALIASES: Dict[str, str] = {
    # file
    "path":           "file",
    "pcap":           "file",
    "pcap_file":      "file",
    "capture_file":   "file",
    # filters
    "filter":         "display_filter",
    "bpf":            "capture_filter",
    "bpf_filter":     "capture_filter",
    # output mode (table/text)
    "format":         "output",
    "output_format":  "output",
    "mode":           "output",
    # fields
    "field":          "fields",
    "columns":        "fields",
    # interface
    "iface":          "interface",
    # session
    "session":        "session_id",
    "id":             "session_id",
    "name":           "session_name",
    "config_name":    "session_name",
    # stat_type
    "stat":           "stat_type",
    # pagination
    "limit":          "head_limit",
    "max_rows":       "head_limit",
    "skip":           "offset",
    # stream / protocol
    "stream":         "stream_id",
    "proto":          "protocol",
    # timeout / max_packets
    "duration":       "timeout",
    "max_time":       "timeout",
    "count":          "max_packets",
    "packets":        "max_packets",
    "max_pkts":       "max_packets",
    # output truncation
    "max_chars":      "max_output_chars",
    "max_len":        "max_output_chars",
    # keep_file
    "keep":           "keep_file",
    # TLS keylog
    "keylog":         "keylog_file",
    "sslkeylog":      "keylog_file",
    "tls_keylog":     "keylog_file",
    # decode_as
    "decode":         "decode_as",
    # follow_stream output_mode (ascii/hex)
    "render":         "output_mode",
    # preset
    "template":       "preset",
    "field_preset":   "preset",
    # config handler
    "op":             "action",
    "operation":      "action",
    "data":           "config",
    "settings":       "config",
    # interval (io stats)
    "step":           "interval",
}


def _resolve_aliases(params: Any) -> dict:
    if params is None:
        return {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'params' was a string but not valid JSON: {exc}. "
                "Pass params as an object, not a JSON-encoded string."
            )
    if not isinstance(params, dict):
        raise ValueError(
            f"'params' must be an object (dict) or a JSON-encoded object string; "
            f"got {type(params).__name__}."
        )
    resolved = {}
    for key, value in params.items():
        canonical = PARAM_ALIASES.get(key, key)
        if canonical not in resolved:
            resolved[canonical] = value
    return resolved


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def _resolve_file(path: str, project_root: str) -> str:
    """Expand ~ and resolve relative paths against project_root."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(project_root, path)
    return path


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def _truncate(text: str, max_chars: int) -> str:
    """Truncate text with a trailing notice if it exceeds max_chars."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n**(truncated — showing first {max_chars:,} chars of {len(text):,})**"
    )


def _markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    """Build a markdown table from headers and rows."""
    if not headers:
        return ""
    ncols = len(headers)
    col_w = [len(h) for h in headers]
    for row in rows:
        for i in range(min(len(row), ncols)):
            col_w[i] = max(col_w[i], len(row[i]))

    hdr = "| " + " | ".join(h.ljust(col_w[i]) for i, h in enumerate(headers)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in col_w) + "|"
    lines = [hdr, sep]
    for row in rows:
        cells = []
        for i in range(ncols):
            cell = row[i] if i < len(row) else ""
            cells.append(cell.ljust(col_w[i]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _tshark_fields_to_markdown(
    output: str,
    fields: List[str],
    head_limit: int = 0,
    offset: int = 0,
) -> Tuple[str, int]:
    """Parse tshark -T fields (tab-separated, header=y) into markdown table.

    Returns (table_string, total_data_row_count).
    """
    raw_lines = output.strip().split("\n")
    if not raw_lines:
        return "", 0

    data_lines = raw_lines[1:] if len(raw_lines) > 1 else []
    total = len(data_lines)

    if offset > 0:
        data_lines = data_lines[offset:]
    if head_limit > 0:
        data_lines = data_lines[:head_limit]

    headers = [FIELD_HEADERS.get(f, f) for f in fields]
    rows = [line.split("\t") for line in data_lines]
    return _markdown_table(headers, rows), total


def _format_size(size: int) -> str:
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


# ---------------------------------------------------------------------------
# Handler functions
# ---------------------------------------------------------------------------

# ---- 1. start_capture ----------------------------------------------------

def handle_start_capture(params: dict, project_root: str) -> dict:
    tshark = _get_tshark()

    default_iface = "lo0" if platform.system() == "Darwin" else "any"
    interface      = params.get("interface", default_iface)
    capture_filter = params.get("capture_filter", "")
    timeout_sec    = int(params.get("timeout", 60))
    max_packets    = int(params.get("max_packets", 100000))
    session_name   = params.get("session_name", "")

    captures_dir = _captures_path(project_root)

    # The id is RESERVED before the spawn instead of registered after it. The
    # pcap path is derived from the id, so two concurrent start_captures that
    # picked the same id would hand tshark the same `-w` file AND the second
    # SESSIONS write would drop the first entry — orphaning a live child nobody
    # can stop any more. `int(time.time())` plus 4 random bytes only collides
    # inside a single second, but the cost of that collision is a lost capture
    # and the cost of ruling it out is one dict lookup.
    with _SESSIONS_LOCK:
        sid = _session_id()
        while sid in SESSIONS:
            sid = _session_id()
        pcap_path = os.path.join(captures_dir, f"{sid}.pcap")
        SESSIONS[sid] = {
            "interface":      interface,
            "capture_filter": capture_filter,
            "timeout":        timeout_sec,
            "max_packets":    max_packets,
            "pcap_path":      pcap_path,
            "session_name":   session_name,
            "start_time":     time.time(),
            # No "process" key yet: that key IS the kill claim (see
            # _SESSIONS_LOCK), so it may not exist before there is a child.
            "status":         "starting",
        }

    args = [tshark, "-i", interface]
    if capture_filter:
        args += ["-f", capture_filter]
    args += [
        "-a", f"duration:{timeout_sec}",
        "-c", str(max_packets),
        "-w", pcap_path,
        "-q",
    ]

    log.info("Starting capture: %s", " ".join(args))
    try:
        # stdin=DEVNULL on every tshark spawn in this file: stdin is NOT covered
        # by capture_output/stdout redirection, so it is inherited -- and this
        # server's stdin is the JSON-RPC stream. tshark reads stdin whenever the
        # capture source resolves to it (`-r -`, or `-i -`), which would make it
        # devour protocol messages. No spawn here ever wants stdin.
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
    except OSError as exc:
        with _SESSIONS_LOCK:
            SESSIONS.pop(sid, None)   # nothing to stop — drop the reservation
        return {"error": f"Failed to start tshark: {exc}"}

    with _SESSIONS_LOCK:
        session = SESSIONS.get(sid)
        # The reservation can disappear from under us while we are spawning:
        # shutdown empties the registry, and a stop_capture that guessed the id
        # claims it. In both cases nobody else holds this child, so publishing it
        # would resurrect a session already declared dead — kill it instead.
        stolen = session is None or session.get("status") == "stopping"
        if stolen:
            SESSIONS.pop(sid, None)
        else:
            session["process"] = proc
            session["status"] = "running"
    if stolen:
        _kill_process_group(proc)   # outside the lock: up to 10s of waiting
        return {"error": f"Capture {sid} was stopped while it was still starting."}

    parts = [
        "## Capture Started",
        f"**Session:** `{sid}`"
        + (f" | **Name:** {session_name}" if session_name else ""),
        f"**Interface:** {interface}"
        + (f" | **Filter:** `{capture_filter}`" if capture_filter else ""),
        f"**Timeout:** {timeout_sec}s | **Max packets:** {max_packets}",
        f"**PCAP:** `{CAPTURES_DIR}/{sid}.pcap`",
    ]
    return {"__raw_text__": "\n".join(parts)}


# ---- 2. stop_capture -----------------------------------------------------

def _packet_count(pcap_path: str) -> int:
    """Quick packet count via tshark — no full dissection."""
    try:
        tshark = _get_tshark()
        r = subprocess.run(
            [tshark, "-r", pcap_path, "-T", "fields", "-e", "frame.number"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,   # never let tshark reach the MCP stream
        )
        if r.returncode == 0 and r.stdout.strip():
            return len(r.stdout.strip().split("\n"))
    except Exception:
        pass
    return -1


def handle_stop_capture(params: dict, project_root: str) -> dict:
    sid = params.get("session_id", "")
    if not sid:
        return {"error": "session_id is required"}

    # ONE claim, taken under the lock: the caller that flips the status to
    # "stopping" and pops the child owns this teardown from here on. A second
    # concurrent stop_capture is turned away instead of SIGTERMing the same pid,
    # re-running the 30s packet count, and racing this one's unlink() against its
    # own getsize(). Everything below works off LOCALS — the long part (kill
    # ladder, packet count) must not hold the lock, and the transient "stopping"
    # status is what keeps a concurrent list_sessions coherent while it runs.
    with _SESSIONS_LOCK:
        session = SESSIONS.get(sid)
        if not session:
            return {"error": f"Unknown session: {sid}. Use list_sessions to see active sessions."}
        if session.get("status") == "stopping":
            return {"error": f"Session {sid} is already being stopped."}
        stop_time = time.time()
        session["status"] = "stopping"
        session["stop_time"] = stop_time
        proc = session.pop("process", None)
        pcap_path = session.get("pcap_path", "")
        start_time = session.get("start_time", stop_time)

    if proc:
        _kill_process_group(proc)

    duration = stop_time - start_time

    if not os.path.isfile(pcap_path) or os.path.getsize(pcap_path) == 0:
        keep = _bool_param(params, "keep_file", True)
        if not keep and os.path.isfile(pcap_path):
            os.unlink(pcap_path)
        with _SESSIONS_LOCK:
            SESSIONS.pop(sid, None)
        return {
            "__raw_text__": (
                f"## Capture Stopped\n"
                f"**Session:** `{sid}` | **Duration:** {duration:.1f}s\n"
                f"No packets captured."
            )
        }

    file_size = os.path.getsize(pcap_path)
    packets = _packet_count(pcap_path)

    keep = _bool_param(params, "keep_file", True)
    pcap_deleted = False
    if not keep and os.path.isfile(pcap_path):
        os.unlink(pcap_path)
        pcap_deleted = True

    rel_path = f"{CAPTURES_DIR}/{os.path.basename(pcap_path)}"
    parts = [
        f"## Capture Stopped",
        f"**Session:** `{sid}` | **Duration:** {duration:.1f}s",
        f"**Packets:** {packets}" if packets >= 0 else "**Packets:** unknown",
        f"**PCAP:** `{rel_path}` ({_format_size(file_size)})",
    ]

    if pcap_deleted:
        parts.append("*PCAP file deleted.*")
    else:
        parts.append(f"\nUse `analyze`, `statistics`, or `follow_stream` with "
                      f"`file: \"{rel_path}\"` to inspect the capture.")

    with _SESSIONS_LOCK:
        SESSIONS.pop(sid, None)
    return {"__raw_text__": "\n".join(parts)}


def _bool_param(params: dict, key: str, default: bool) -> bool:
    val = params.get(key, default)
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(val)


# ---- 3. analyze (+ _run_analyze helper) ----------------------------------

# Upper bound on the caller-supplied `analyze` timeout. An unclamped timeout the
# CALLER picks is the same defect class as the inline blocking read loop this
# server used to have: it hands one request the right to occupy a worker for as
# long as it likes. PARAM_ALIASES maps `duration` and `max_time` onto it, so
# {"duration": 86400} — an entirely plausible thing to write when you are
# thinking about a CAPTURE — used to buy a day-long subprocess wait. Concurrent
# dispatch bounds the blast radius (the loop keeps reading), it does not remove
# it: MAX_INFLIGHT_REQUESTS such calls and every worker is busy again, which
# reads as a dead server for the second time. 600s is far past any local-file
# dissection that was ever going to finish.
MAX_ANALYZE_SEC = 600


def _run_analyze(params: dict, project_root: str) -> str:
    """Core analysis — shared by stop_capture and analyze."""
    tshark = _get_tshark()

    file_path      = params.get("file", "")
    if not file_path:
        raise ValueError("file is required")
    file_path = _resolve_file(file_path, project_root)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"PCAP file not found: {file_path}")

    display_filter = params.get("display_filter", "")
    output_mode    = params.get("output", "table")
    custom_fields  = params.get("fields", "")
    preset         = params.get("preset", "default")
    decode_as      = params.get("decode_as", "")
    keylog_file    = params.get("keylog_file", "")
    head_limit     = int(params.get("head_limit", 0))
    offset         = int(params.get("offset", 0))
    max_chars      = int(params.get("max_output_chars", 500000))
    timeout_sec    = min(MAX_ANALYZE_SEC, max(1, int(params.get("timeout", 120))))

    if custom_fields:
        fields = [f.strip() for f in custom_fields.split(",") if f.strip()]
    else:
        fields = FIELD_PRESETS.get(preset, FIELD_PRESETS["default"])

    common_tail: List[str] = []
    if display_filter:
        common_tail += ["-Y", display_filter]
    if decode_as:
        common_tail += ["-d", decode_as]
    if keylog_file:
        keylog_file = _resolve_file(keylog_file, project_root)
        common_tail += ["-o", f"tls.keylog_file:{keylog_file}"]

    # Build tshark args based on output mode
    #   table   — -T fields (default, markdown table)
    #   text    — one-line summaries
    #   verbose — -V full protocol tree dissection
    #   json    — -T json structured output
    #   hex     — -x hex+ASCII dump
    base = [tshark, "-r", file_path]

    if output_mode == "verbose":
        args = base + ["-V"] + common_tail
    elif output_mode == "json":
        args = base + ["-T", "json"] + common_tail
    elif output_mode == "hex":
        args = base + ["-x"] + common_tail
    elif output_mode == "text":
        args = base + common_tail
    else:
        args = base + [
            "-T", "fields",
            "-E", "header=y",
            "-E", "separator=/t",
            "-E", "quote=n",
        ]
        for field in fields:
            args += ["-e", field]
        args += common_tail

    log.info("Analyzing: %s", " ".join(args))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec,
                                stdin=subprocess.DEVNULL)   # never reach the MCP stream
    except subprocess.TimeoutExpired:
        return f"**Error:** Analysis timed out after {timeout_sec}s"

    if result.returncode != 0 and not result.stdout:
        return f"**Error:** tshark failed (exit {result.returncode}): {result.stderr.strip()}"

    basename = os.path.basename(file_path)

    # json mode — pass through raw JSON (already structured)
    if output_mode == "json":
        parts = [f"## Packet Analysis: {basename} (JSON)"]
        if display_filter:
            parts.append(f"**Filter:** `{display_filter}`")
        parts += ["", "```json", result.stdout.strip(), "```"]
        return _truncate("\n".join(parts), max_chars)

    # verbose / hex / text — code-fenced block output
    if output_mode in ("text", "verbose", "hex"):
        body = result.stdout.strip()
        mode_label = {"text": "", "verbose": " (verbose)", "hex": " (hex)"}
        lines = body.split("\n") if body else []
        total = len(lines)
        if offset > 0:
            lines = lines[offset:]
        if head_limit > 0:
            lines = lines[:head_limit]
        shown = len(lines)

        parts = [f"## Packet Analysis: {basename}{mode_label.get(output_mode, '')}"]
        if display_filter:
            parts.append(f"**Filter:** `{display_filter}`")
        pagination = ""
        if offset > 0 or head_limit > 0:
            pagination = f" (showing lines {offset + 1}–{offset + shown} of {total})"
        parts.append(f"**Lines:** {total}{pagination}")
        lang = "json" if output_mode == "json" else ""
        parts += ["", f"```{lang}", "\n".join(lines), "```"]
        return _truncate("\n".join(parts), max_chars)

    # table mode (default)
    table, total = _tshark_fields_to_markdown(
        result.stdout, fields, head_limit, offset,
    )

    parts = [f"## Packet Analysis: {basename}"]
    meta: List[str] = []
    if display_filter:
        meta.append(f"**Filter:** `{display_filter}`")
    meta.append(f"**Packets:** {total}")
    if offset > 0 or head_limit > 0:
        end = min(offset + head_limit, total) if head_limit > 0 else total
        meta.append(f"(showing {offset + 1}–{end})")
    parts.append(" | ".join(meta))
    parts += ["", table]
    return _truncate("\n".join(parts), max_chars)


def handle_analyze(params: dict, project_root: str) -> dict:
    try:
        return {"__raw_text__": _run_analyze(params, project_root)}
    except (ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}


# ---- 4. list_sessions ----------------------------------------------------

def handle_list_sessions(params: dict, project_root: str) -> dict:
    status_filter = params.get("status", "")

    # A snapshot under the lock, rendered outside it: os.path.getsize is file I/O
    # per row, and a concurrent start/stop must not queue behind this render.
    # Each session is shallow-copied because the live dict keeps being mutated by
    # its owning handler while we format — iterating SESSIONS directly would also
    # raise "dictionary changed size during iteration" the moment one of them
    # finished mid-loop.
    with _SESSIONS_LOCK:
        snapshot = [(sid, dict(session)) for sid, session in SESSIONS.items()]

    if not snapshot:
        return {"__raw_text__": "## Capture Sessions\n\nNo active sessions."}

    now = time.time()
    headers = ["Session ID", "Name", "Interface", "Filter", "Status", "Duration", "PCAP Size"]
    rows: List[List[str]] = []

    for sid, s in snapshot:
        status = s.get("status", "unknown")
        if status_filter and status != status_filter:
            continue

        duration = (s.get("stop_time", now) - s.get("start_time", now))
        pcap = s.get("pcap_path", "")
        try:
            size = os.path.getsize(pcap) if os.path.isfile(pcap) else 0
        except OSError:
            size = 0

        rows.append([
            sid,
            s.get("session_name", ""),
            s.get("interface", ""),
            s.get("capture_filter", ""),
            status,
            f"{duration:.1f}s",
            _format_size(size),
        ])

    if not rows:
        return {"__raw_text__": f"## Capture Sessions\n\nNo sessions with status '{status_filter}'."}

    return {"__raw_text__": f"## Capture Sessions\n\n{_markdown_table(headers, rows)}"}


# ---- 5. list_interfaces --------------------------------------------------

def handle_list_interfaces(params: dict, project_root: str) -> dict:
    tshark = _get_tshark()
    try:
        result = subprocess.run(
            [tshark, "-D"], capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL,   # never let tshark reach the MCP stream
        )
    except subprocess.TimeoutExpired:
        return {"error": "tshark -D timed out"}
    except OSError as exc:
        return {"error": f"Failed to run tshark: {exc}"}

    if result.returncode != 0:
        return {"error": f"tshark -D failed: {result.stderr.strip()}"}

    headers = ["#", "Interface", "Description"]
    rows: List[List[str]] = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        dot = line.find(".")
        if dot < 0:
            continue
        num = line[:dot].strip()
        rest = line[dot + 1:].strip()

        desc = ""
        name = rest
        paren = rest.find("(")
        if paren >= 0:
            name = rest[:paren].strip()
            close = rest.find(")", paren)
            if close >= 0:
                desc = rest[paren + 1:close]

        rows.append([num, name, desc])

    return {"__raw_text__": f"## Network Interfaces\n\n{_markdown_table(headers, rows)}"}


# ---- 6. statistics -------------------------------------------------------

STAT_MAP: Dict[str, str] = {
    "io":        "io,stat,{interval}",
    "conv":      "conv,{protocol}",
    "endpoints": "endpoints,{protocol}",
    "expert":    "expert",
    "http":      "http,tree",
    "dns":       "dns,tree",
}


def handle_statistics(params: dict, project_root: str) -> dict:
    tshark = _get_tshark()

    file_path = params.get("file", "")
    if not file_path:
        return {"error": "file is required"}
    stat_type = params.get("stat_type", "")
    if not stat_type:
        return {"error": f"stat_type is required ({', '.join(sorted(STAT_MAP))})"}

    file_path = _resolve_file(file_path, project_root)
    if not os.path.isfile(file_path):
        return {"error": f"PCAP file not found: {file_path}"}

    template = STAT_MAP.get(stat_type)
    if not template:
        return {"error": f"Unknown stat_type: {stat_type}. Valid: {', '.join(sorted(STAT_MAP))}"}

    protocol       = params.get("protocol", "tcp")
    interval       = params.get("interval", "0")
    display_filter = params.get("display_filter", "")
    max_chars      = int(params.get("max_output_chars", 500000))

    stat_arg = template.format(interval=interval, protocol=protocol)
    args = [tshark, "-r", file_path, "-z", stat_arg, "-q"]
    if display_filter:
        args += ["-Y", display_filter]

    log.info("Statistics: %s", " ".join(args))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL)   # never reach the MCP stream
    except subprocess.TimeoutExpired:
        return {"error": "Statistics timed out after 120s"}

    if result.returncode != 0 and not result.stdout:
        return {"error": f"tshark failed: {result.stderr.strip()}"}

    basename = os.path.basename(file_path)
    parts = [f"## Statistics: {stat_type} — {basename}"]
    if display_filter:
        parts.append(f"**Filter:** `{display_filter}`")
    parts += ["", "```", result.stdout.strip(), "```"]

    return {"__raw_text__": _truncate("\n".join(parts), max_chars)}


# ---- 7. follow_stream ----------------------------------------------------

def handle_follow_stream(params: dict, project_root: str) -> dict:
    tshark = _get_tshark()

    file_path = params.get("file", "")
    if not file_path:
        return {"error": "file is required"}
    if params.get("stream_id") is None:
        return {"error": "stream_id is required"}
    stream_id = int(params["stream_id"])

    file_path = _resolve_file(file_path, project_root)
    if not os.path.isfile(file_path):
        return {"error": f"PCAP file not found: {file_path}"}

    protocol    = params.get("protocol", "tcp")
    output_mode = params.get("output_mode", "ascii")
    max_chars   = int(params.get("max_output_chars", 500000))

    args = [
        tshark, "-r", file_path,
        "-z", f"follow,{protocol},{output_mode},{stream_id}",
        "-q",
    ]

    log.info("Follow stream: %s", " ".join(args))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL)   # never reach the MCP stream
    except subprocess.TimeoutExpired:
        return {"error": "Follow stream timed out after 120s"}

    if result.returncode != 0 and not result.stdout:
        return {"error": f"tshark failed: {result.stderr.strip()}"}

    basename = os.path.basename(file_path)
    parts = [
        f"## Stream Follow: {protocol} stream {stream_id} — {basename}",
        "",
        "```",
        result.stdout.strip(),
        "```",
    ]
    return {"__raw_text__": _truncate("\n".join(parts), max_chars)}


# ---- 8. config ------------------------------------------------------------

# The saved-config store is a read-modify-write over one file, so concurrent
# dispatch can destroy it outright: `open(..., "w")` TRUNCATES before json.dump
# refills it, and a reader landing in that window parses invalid JSON, falls back
# to `{}` (the except below), and the next save writes that empty dict back —
# every other saved configuration gone, with a success reply. A separate lock
# from _SESSIONS_LOCK because it guards a different object and only ever spans a
# few KB of local file I/O; it is held across the whole read-modify-write, since
# locking each half separately is what leaves the lost-update window open.
_CONFIG_LOCK = threading.Lock()


def handle_config(params: dict, project_root: str) -> dict:
    action = params.get("action", "")
    if not action:
        return {"error": "action is required (save, load, list, delete)"}

    config_path = os.path.join(_captures_path(project_root), "configs.json")

    with _CONFIG_LOCK:
        configs: Dict[str, dict] = {}
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r") as fh:
                    configs = json.load(fh)
            except (json.JSONDecodeError, OSError):
                configs = {}

        if action == "list":
            if not configs:
                return {"__raw_text__": "## Saved Configurations\n\nNo saved configurations."}
            headers = ["Name", "Keys"]
            rows = [[n, ", ".join(sorted(c.keys()))] for n, c in sorted(configs.items())]
            return {"__raw_text__": f"## Saved Configurations\n\n{_markdown_table(headers, rows)}"}

        name = params.get("session_name", "")
        if not name:
            return {"error": "name is required for save/load/delete"}

        if action == "save":
            config_data = params.get("config")
            if not config_data or not isinstance(config_data, dict):
                return {"error": "config (dict) is required for save"}
            configs[name] = config_data
            with open(config_path, "w") as fh:
                json.dump(configs, fh, indent=2)
            return {
                "__raw_text__": (
                    f"## Configuration Saved\n"
                    f"**Name:** `{name}`\n"
                    f"**Keys:** {', '.join(sorted(config_data.keys()))}"
                )
            }

        if action == "load":
            if name not in configs:
                return {"error": f"Configuration not found: {name}"}
            cfg = configs[name]
            lines = [f"## Configuration: {name}", ""]
            for k, v in sorted(cfg.items()):
                lines.append(f"- **{k}:** `{v}`")
            return {"__raw_text__": "\n".join(lines)}

        if action == "delete":
            if name not in configs:
                return {"error": f"Configuration not found: {name}"}
            del configs[name]
            with open(config_path, "w") as fh:
                json.dump(configs, fh, indent=2)
            return {"__raw_text__": f"## Configuration Deleted\n**Name:** `{name}`"}

    return {"error": f"Unknown action: {action}. Valid: save, load, list, delete"}


# ---------------------------------------------------------------------------
# Handler registry + aliases
# ---------------------------------------------------------------------------
HANDLERS: Dict[str, Callable[..., dict]] = {
    "start_capture":   handle_start_capture,
    "stop_capture":    handle_stop_capture,
    "analyze":         handle_analyze,
    "list_sessions":   handle_list_sessions,
    "list_interfaces": handle_list_interfaces,
    "statistics":      handle_statistics,
    "follow_stream":   handle_follow_stream,
    "config":          handle_config,
    # aliases
    "start":           handle_start_capture,
    "capture":         handle_start_capture,
    "stop":            handle_stop_capture,
    "read":            handle_analyze,
    "analyse":         handle_analyze,
    "sessions":        handle_list_sessions,
    "interfaces":      handle_list_interfaces,
    "stats":           handle_statistics,
    "follow":          handle_follow_stream,
}

_PRIMARY_FUNCTIONS = {
    "start_capture", "stop_capture", "analyze", "list_sessions",
    "list_interfaces", "statistics", "follow_stream", "config",
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def handle_tshark_call(arguments: dict, project_root: str) -> dict:
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    raw_params = arguments.get("params") or arguments.get("p") or {}
    try:
        params = _resolve_aliases(raw_params)
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        func_list = "\n".join(f"  {n}" for n in sorted(HANDLERS))
        return {
            "__raw_text__": (
                f"mcp-tshark OK — project: {project_root}\n"
                f"Available functions:\n{func_list}"
            )
        }

    handler = HANDLERS.get(function)
    if not handler:
        primary = ", ".join(sorted(_PRIMARY_FUNCTIONS))
        return {"error": f"Unknown function: {function}. Available: {primary}"}

    try:
        return handler(params, project_root)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("Unhandled exception in handler '%s'", function)
        return {"error": f"Internal error in '{function}': {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Tool descriptor
# ---------------------------------------------------------------------------
TSHARK_CALL_TOOL = {
    "name": "tshark_call",
    "description": (
        "Network packet capture and analysis via tshark (Wireshark CLI).\n\n"
        "Single dispatcher — set `function` to route:\n\n"
        "  start_capture    Start background packet capture\n"
        "  stop_capture     Stop capture, return metadata (use analyze separately)\n"
        "  analyze          Analyze a PCAP file\n"
        "  list_sessions    List capture sessions\n"
        "  list_interfaces  List network interfaces\n"
        "  statistics       Protocol statistics (io, conv, endpoints, expert, http, dns)\n"
        "  follow_stream    Reconstruct and follow a stream\n"
        "  config           Save/load capture configurations\n\n"
        "Returns server status if called without 'function'.\n\n"
        "Field presets: default, tcp, http, dns, tls.\n"
        "Output modes: table, text, verbose (-V protocol tree), "
        "json (-T json structured), hex (-x hex+ASCII dump).\n\n"
        "Parameter aliases: path/pcap→file, filter→display_filter, "
        "bpf→capture_filter, iface→interface, limit/max_rows→head_limit, "
        "skip→offset, proto→protocol, stream→stream_id, stat→stat_type, "
        "duration→timeout, count/packets→max_packets, keep→keep_file, "
        "keylog/sslkeylog→keylog_file, decode→decode_as, "
        "max_chars→max_output_chars, template→preset, op→action, "
        "data/settings→config, step→interval, render→output_mode."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "description": (
                    "Function name (e.g. start_capture, analyze, statistics). "
                    "Alias: 'f'."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Function parameters — all args go here. Alias: 'p'."
                ),
            },
        },
    },
}


# ---------------------------------------------------------------------------
# McpServer
# ---------------------------------------------------------------------------

# How many tool calls may be in flight at once. The stdin reader owns a thread of
# its own, OUTSIDE this pool, so saturating it delays queued CALLS and can never
# stop the server from READING — which is the whole point of the split in run().
# 8 also bounds how many tshark children this server can have dissecting at once.
MAX_INFLIGHT_REQUESTS = 8


class McpServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

    def __init__(self, project_root: str):
        self.project_root = os.path.realpath(project_root)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, project_root=%s", self.project_root)
        # TWO executors, and one task per request, on purpose. This loop used to
        # call _handle_message INLINE on the very thread that awaits the
        # readline, so a single blocking handler froze the whole event loop for
        # its duration — and here that duration is BUDGETED: `analyze` defaults
        # to 120s and statistics / follow_stream carry a hard 120s, so two
        # minutes of deafness was the DESIGNED case, not the failure case. Every
        # other request then sat unread in the pipe, timed out client-side at
        # ~60s (twice as fast as the call it was waiting behind), and was finally
        # answered against an id the client had already abandoned. From the
        # caller's chair that is a dead server, and a restart was the only lever.
        #
        # The reader gets a pool to ITSELF because a single shared pool is the
        # easy way to reintroduce the same deafness one layer down:
        # MAX_INFLIGHT_REQUESTS busy handlers would leave `sys.stdin.readline`
        # with no thread to run on, and the server stops reading again.
        #
        # Handlers are safe to run concurrently because the module's shared
        # mutable state is guarded: SESSIONS by _SESSIONS_LOCK (bookkeeping only,
        # never across a capture or a packet count) and the saved-config file by
        # _CONFIG_LOCK. `_tshark_path` is an idempotent single-value cache — two
        # threads racing it compute the same path — and project_root is written
        # once in __init__ before this loop starts.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tshark-stdin")
        workers = ThreadPoolExecutor(max_workers=MAX_INFLIGHT_REQUESTS,
                                     thread_name_prefix="tshark-call")
        inflight: set = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    # A closed/detached stdin must end the loop through the
                    # `finally` below, not unwind out of run() past the cleanup.
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
                    # timed out — indistinguishable from a hung server.
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    # `5` on a line is valid JSON. It used to reach msg.get() and
                    # take the process down with an AttributeError that escaped
                    # run() — and an MCP client does not respawn a dead stdio
                    # server, so one stray line was a restart.
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
            # Last, and deliberately after the executors are released: this kills
            # the capture children, and it claims each one under _SESSIONS_LOCK
            # so a stop_capture still mid-flight cannot signal the same pid.
            _cleanup_sessions()
            log.info("MCP server shutting down")

    async def _serve(self, loop, workers: ThreadPoolExecutor, msg: dict) -> None:
        """One request, from dispatch to written reply. Runs as its own task."""
        try:
            response = await loop.run_in_executor(workers, self._handle_message, msg)
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            # Catching Exception and not BaseException is load-bearing: the
            # `finally` above cancels these tasks, and swallowing CancelledError
            # would turn shutdown into a hang.
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
        but `_serve` resumes on the loop after its await, so concurrent replies
        cannot interleave and this needs no lock.
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
            # Unguarded, a client that closed the pipe mid-reply killed the
            # process with the traceback escaping run().
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
                "serverInfo": {"name": "mcp-tshark", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [TSHARK_CALL_TOOL]})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)
        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name != "tshark_call":
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
            result = handle_tshark_call(arguments, self.project_root)
        except Exception as exc:
            log.exception("Unhandled exception in handle_tshark_call")
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


# ---------------------------------------------------------------------------
# Handler descriptions (--list)
# ---------------------------------------------------------------------------
HANDLER_DESCRIPTIONS: Dict[str, str] = {
    "start_capture":   "Start background packet capture on an interface",
    "stop_capture":    "Stop a running capture, return metadata (use analyze separately)",
    "analyze":         "Analyze a PCAP file (table or text output)",
    "list_sessions":   "List active and stopped capture sessions",
    "list_interfaces": "List available network interfaces",
    "statistics":      "Protocol statistics (io, conv, endpoints, expert, http, dns)",
    "follow_stream":   "Reconstruct and follow a TCP/UDP/TLS/HTTP stream",
    "config":          "Save/load/list/delete capture configurations",
    "start":           "→ start_capture",
    "capture":         "→ start_capture",
    "stop":            "→ stop_capture",
    "read":            "→ analyze",
    "analyse":         "→ analyze",
    "sessions":        "→ list_sessions",
    "interfaces":      "→ list_interfaces",
    "stats":           "→ statistics",
    "follow":          "→ follow_stream",
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    if "--list" in sys.argv:
        print("mcp-tshark — available functions:\n")
        for name in sorted(HANDLERS):
            desc = HANDLER_DESCRIPTIONS.get(name, "")
            print(f"  {name:25s} {desc}")
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="MCP-tshark: network capture & analysis MCP server",
    )
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()

    level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
    handlers: list = []
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    if not os.path.isdir(args.project_root):
        print(
            f"Error: project root is not a directory: {args.project_root}",
            file=sys.stderr,
        )
        sys.exit(1)

    tshark = find_tshark()
    if tshark:
        log.info("tshark found: %s", tshark)
    else:
        log.warning("tshark not found — capture/analysis functions will fail")

    server = McpServer(args.project_root)
    asyncio.run(server.run())
    # stdin is closed, so the client is gone. Handler threads live in the
    # server's own executors rather than the loop's default one, so asyncio does
    # not join them — but concurrent.futures registers an atexit hook that WOULD,
    # and one handler mid-`analyze` would hold this process open for the rest of
    # its 120s budget (up to MAX_ANALYZE_SEC if the caller asked for more). Every
    # reply is flushed as it is written and logging flushes per record, so there
    # is nothing left to drain; the captures were killed in run()'s finally.
    os._exit(0)


if __name__ == "__main__":
    main()
