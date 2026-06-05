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
import time
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
    """Kill all active capture processes."""
    for sid, session in list(SESSIONS.items()):
        proc = session.get("process")
        if proc:
            _kill_process_group(proc)
            log.info("Cleaned up session %s", sid)
    SESSIONS.clear()


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

    sid = _session_id()
    pcap_path = os.path.join(_captures_path(project_root), f"{sid}.pcap")

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
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
    except OSError as exc:
        return {"error": f"Failed to start tshark: {exc}"}

    SESSIONS[sid] = {
        "process":        proc,
        "interface":      interface,
        "capture_filter": capture_filter,
        "timeout":        timeout_sec,
        "max_packets":    max_packets,
        "pcap_path":      pcap_path,
        "session_name":   session_name,
        "start_time":     time.time(),
        "status":         "running",
    }

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

    session = SESSIONS.get(sid)
    if not session:
        return {"error": f"Unknown session: {sid}. Use list_sessions to see active sessions."}

    proc = session.get("process")
    if proc:
        _kill_process_group(proc)

    session["status"] = "stopped"
    session["stop_time"] = time.time()
    duration = session["stop_time"] - session.get("start_time", session["stop_time"])

    pcap_path = session.get("pcap_path", "")
    if not os.path.isfile(pcap_path) or os.path.getsize(pcap_path) == 0:
        keep = _bool_param(params, "keep_file", True)
        if not keep and os.path.isfile(pcap_path):
            os.unlink(pcap_path)
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

    SESSIONS.pop(sid, None)
    return {"__raw_text__": "\n".join(parts)}


def _bool_param(params: dict, key: str, default: bool) -> bool:
    val = params.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() not in ("false", "0", "no")
    return bool(val)


# ---- 3. analyze (+ _run_analyze helper) ----------------------------------

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
    timeout_sec    = int(params.get("timeout", 120))

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
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
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

    if not SESSIONS:
        return {"__raw_text__": "## Capture Sessions\n\nNo active sessions."}

    now = time.time()
    headers = ["Session ID", "Name", "Interface", "Filter", "Status", "Duration", "PCAP Size"]
    rows: List[List[str]] = []

    for sid, s in SESSIONS.items():
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
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
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
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
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

def handle_config(params: dict, project_root: str) -> dict:
    action = params.get("action", "")
    if not action:
        return {"error": "action is required (save, load, list, delete)"}

    config_path = os.path.join(_captures_path(project_root), "configs.json")

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
class McpServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

    def __init__(self, project_root: str):
        self.project_root = os.path.realpath(project_root)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, project_root=%s", self.project_root)
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
            _cleanup_sessions()
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


if __name__ == "__main__":
    main()
