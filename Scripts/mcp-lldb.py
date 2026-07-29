#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
LLDB MCP Server — standalone, no external dependencies.

Design:
  tools/list  → exposes only 'lldb_mcp_status' (minimal token footprint)
  tools/call  → dispatches all ~25 LLDB tools (documented in lldb-mcp skill)

Output ceiling (cap convention v1): every reply is capped by max_answer_chars
(default 24000 chars, ~6k tokens). Line-shaped payloads — backtrace frames,
disassembly, a register bank, a breakpoint or thread list — are truncated by
dropping whole LINES and say where to resume via 'offset'; everything else is cut
on a line boundary and carries one closing accounting line. WHICH END survives is
a per-function decision, not a default — see CAP_POLICY: execution control is
tail-biased because the stop reason arrives after whatever the inferior printed,
everything else is head-biased.

Usage:
  python3 mcp-lldb.py [--debug]
"""

import os
import re
import sys
import json
import uuid
import asyncio
import pty
import fcntl
import termios
import logging
import argparse
from typing import Dict, List, Optional, Any


log = logging.getLogger("mcp-lldb")


def _ensure_dict(value: Any, name: str = "params") -> dict:
    """Coerce *value* to a dict.

    Accepts None (→ {}), dict (passthrough), or JSON-encoded object string.
    Raises ValueError on a non-JSON string, JSON that is not an object,
    or any other type.
    """
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


def _bool_param(value, default=False):
    """Coerce a possibly-stringy value to bool.

    The wire frequently carries booleans as strings ("false"/"0"/"no"), where a
    naive bool("false") would wrongly yield True.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


# ============================================================
# Output ceiling (cap convention v1)
# ============================================================

# 24000 chars is ~6k tokens at the usual ~4 chars/token — a reply one call may
# spend, not a reply that eats the session.
#
# Until now this server had NO ceiling of any kind, so the only backstop was the
# Claude Code harness: it generates the whole oversized payload, spills it to a
# file and costs an extra round trip to read it back. One uncapped call in this
# fleet has already produced 511617 chars that way. LLDB is the worst candidate
# for that treatment, because long output is its NORMAL output: `bt full` on a
# deep stack, `memory read -c 100000`, `disassemble` over a whole function,
# `register read` with the vector banks, `help` with no argument.
#
# Per-call overridable via max_answer_chars, so a caller who genuinely wants the
# whole dump asks for it explicitly. <= 0 means unlimited.
DEFAULT_MAX_ANSWER_CHARS = 24000

# Room kept free for the closing accounting line while filling a line budget.
PAGE_LINE_RESERVE = 80

BIAS_HEAD = "head"
BIAS_TAIL = "tail"


def _max_answer_chars(args: dict) -> int:
    """The per-call ceiling. <= 0 disables it — an explicit "give me all of it"."""
    try:
        return int(args.get("max_answer_chars", DEFAULT_MAX_ANSWER_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ANSWER_CHARS


def _offset(args: dict) -> int:
    """First payload LINE to display, 0-based — the value the page line hands back.

    Display-level paging over output this call already produced. It is NOT an
    address offset and NOT an index inside an LLDB command: the command is
    re-executed on every call, so a caller walking a long dump pays for the
    command each time.

    Read only for the functions CAP_POLICY marks pageable — the ones that can
    really resume. The others never emit an ``offset=`` hint, because a resume
    hint for a knob that does not exist lies to the caller.
    """
    try:
        return max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def _rows_note(start: int, shown: int, total: int) -> str:
    """Line accounting for a line-shaped payload; goes on its LAST line.

    <total> is EXACT and always known here: read_until_prompt() buffers the whole
    PTY reply before any handler sees it, so this server never streams and never
    has to guess a count. A server that CANNOT know its total must SAY so on this
    line instead of inventing a number.

    Display indices are 1-based inclusive, which makes the 1-based last line
    equal to the 0-based ``offset`` of the next one — so the hint is literally the
    value to pass back. ``offset=`` is emitted only when lines really remain: a
    resume hint that would return nothing is worse than no hint.
    """
    last = start + shown
    if shown == 0:
        # Spelled out rather than as a 1-based range, which would invert
        # ("rows 100-99") when the caller offsets past the end.
        return (f"[no rows at offset {start} of {total}]" if start
                else f"[{total} rows]")
    if last < total:
        return (f"[showing rows {start + 1}-{last} of {total}; "
                f"offset={last} for more]")
    if start > 0:
        return f"[showing rows {start + 1}-{last} of {total}; no rows left]"
    return f"[{total} row{'s' if total != 1 else ''}]"


def _page_lines(text: str, offset: int, max_chars: int) -> str:
    """A line-shaped payload, paged by LINE — never cut mid-line.

    A backtrace frame, a disassembled instruction, a register, one breakpoint:
    the unit of meaning is a whole line, and half an address or half a mangled
    symbol name is worse than a missing line. So the ceiling is spent by dropping
    whole lines, and the closing line says where to resume.

    The note is emitted ONLY when the view is actually partial. Unlike a SQL row
    count, the line count of a debugger reply is not something the caller asked
    for, and a bare "[42 rows]" appended to every single answer is pure per-call
    boilerplate — the exact cost this convention exists to cut.
    """
    lines = text.split("\n")
    total = len(lines)
    # NOT clamped to `total`: an over-offset must be reported back as the value
    # the CALLER passed. Clamping first turns `offset=99 of 6` into the
    # meaningless `offset=6 of 6` and hides the caller's mistake. A Python slice
    # past the end is already empty, so the clamp bought nothing.
    start = max(0, offset)
    budget = max_chars if max_chars > 0 else 0

    kept: List[str] = []
    for line in lines[start:]:
        # At least one line always survives: a bare accounting line tells the
        # caller nothing, and _cap_text() below is the hard backstop anyway.
        if budget > 0 and kept and budget - len(line) - 1 < PAGE_LINE_RESERVE:
            break
        budget -= len(line) + 1
        kept.append(line)

    if start == 0 and len(kept) == total:
        return text
    note = _rows_note(start, len(kept), total)
    return "\n".join(kept + [note]) if kept else note


def _cap_text(text: str, max_chars: int, bias: str = BIAS_HEAD) -> str:
    """Cut to ``max_chars`` on a LINE BOUNDARY, with ONE closing line.

    ``bias`` names which END is kept; it is a per-function decision (CAP_POLICY),
    not a default anyone should rely on. The closing line always names the end it
    kept and is always the payload's LAST line — even for a tail-biased cut, where
    what was dropped is at the top. Both because the end of a payload is what
    actually gets read, and because a marker at the cut site is the one place a
    reader skips.

    A frame address or a mangled symbol name is never halved, because the cut
    lands on a newline; the single exception is a payload with no newline to cut
    at, where the boundary does not exist and the hard cut is the honest answer.

    For a pageable function this is the LAST resort: _page_lines() has already
    spent the ceiling by whole lines. What lands here is the non-line payloads (a
    value dump, a composite of several command outputs) plus the one overflow the
    line pager cannot prevent — a single line wider than the whole ceiling.

    No fence repair, unlike mcp-context7.py and mcp-jenkins.py:619: those two
    emit or forward markdown, while every payload here is raw PTY text under a
    plain label — this file contains no fence literal at all. Balancing fences in
    debugger output could only ever fire on an odd count the INFERIOR printed,
    where "repairing" it would fabricate a line LLDB never produced.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)
    keep_tail = bias == BIAS_TAIL

    def marker(kept: int) -> str:
        # The two ends are spelled out instead of interpolating ``bias``, so a
        # search for the convention's closing line finds it verbatim in the
        # source rather than as a template with a hole in it.
        if keep_tail:
            return (f"\n[truncated: kept {kept} of {total} chars from the tail; "
                    f"raise max_answer_chars or narrow the query]")
        return (f"\n[truncated: kept {kept} of {total} chars from the head; "
                f"raise max_answer_chars or narrow the query]")

    # marker(total) is the longest the line can ever get (kept <= total), so
    # reserving that much cannot overshoot once the real count is known.
    keep = max_chars - len(marker(total))
    if keep <= 0:
        # The ceiling is smaller than the accounting line itself. The line still
        # wins: a payload with no accounting is worse than no payload.
        return marker(0).lstrip("\n")
    if keep_tail:
        cut = text.find("\n", total - keep)
        body = text[cut + 1:] if 0 <= cut < total - 1 else text[total - keep:]
    else:
        cut = text.rfind("\n", 0, keep + 1)
        body = text[:cut] if cut > 0 else text[:keep]
    return body + marker(len(body))


# ============================================================
# LLDB Session
# ============================================================

class LldbSession:
    def __init__(self, session_id: str, lldb_path: str, working_dir: Optional[str] = None):
        self.id = session_id
        self.lldb_path = lldb_path
        self.working_dir = working_dir or os.getcwd()
        self.process = None
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.target: Optional[str] = None
        self.ready = False

    async def start(self) -> str:
        """Start the LLDB process with a PTY."""
        log.debug(f"Starting LLDB: {self.lldb_path}")

        self.master_fd, self.slave_fd = pty.openpty()

        # Disable echo on slave end
        settings = termios.tcgetattr(self.slave_fd)
        settings[3] = settings[3] & ~termios.ECHO
        termios.tcsetattr(self.slave_fd, termios.TCSADRAIN, settings)

        self.process = await asyncio.create_subprocess_exec(
            self.lldb_path,
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            cwd=self.working_dir,
        )
        log.debug(f"LLDB PID: {self.process.pid}")

        # Parent doesn't need the slave end
        os.close(self.slave_fd)
        self.slave_fd = None

        # Non-blocking reads on master
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        output = await self.read_until_prompt()
        self.ready = True

        version_output = await self.execute_command("version")
        return output + version_output

    async def execute_command(self, command: str, timeout: float = 30.0) -> str:
        """Execute an LLDB command and return the output."""
        if not self.ready:
            raise RuntimeError("LLDB session not ready")
        if not self.process:
            raise RuntimeError("LLDB session not ready: no process")
        if self.process.returncode is not None:
            raise RuntimeError(f"LLDB process terminated (code {self.process.returncode})")

        log.debug(f"Executing: {command!r}")
        os.write(self.master_fd, f"{command}\n".encode())
        return await self.read_until_prompt(timeout=timeout)

    async def read_until_prompt(self, timeout: float = 30.0) -> str:
        """Read PTY output until the LLDB prompt is seen or timeout."""
        if self.master_fd is None:
            raise RuntimeError("PTY not initialized")

        buffer = b""
        # Include trailing space to avoid false matches in help/backtrace output
        prompt_pattern = b"(lldb) "
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        while True:
            elapsed = loop.time() - start_time
            if elapsed > timeout:
                log.debug(f"Timeout after {elapsed:.1f}s")
                return buffer.decode("utf-8", errors="replace") + "\n[Timeout waiting for LLDB response]"

            if self.process and self.process.returncode is not None:
                log.debug(f"Process exited: {self.process.returncode}")
                if buffer:
                    return buffer.decode("utf-8", errors="replace")
                raise RuntimeError(f"LLDB process terminated (code {self.process.returncode})")

            try:
                chunk = os.read(self.master_fd, 4096)
                if chunk:
                    buffer += chunk
                    log.debug(f"Read {len(chunk)} bytes")
                    if prompt_pattern in buffer:
                        log.debug("Prompt found")
                        return buffer.decode("utf-8", errors="replace")
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError as e:
                log.debug(f"PTY read error: {e}")
                if buffer:
                    return buffer.decode("utf-8", errors="replace") + f"\n[PTY error: {e}]"
                raise RuntimeError(f"PTY read error: {e}")

    async def cleanup(self) -> None:
        """Terminate LLDB and release all resources."""
        log.debug(f"Cleaning up session {self.id}")
        try:
            if self.master_fd is not None:
                try:
                    os.write(self.master_fd, b"quit\n")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log.debug(f"Error sending quit: {e}")

            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()

            if self.master_fd is not None:
                os.close(self.master_fd)
                self.master_fd = None

            if self.slave_fd is not None:
                os.close(self.slave_fd)
                self.slave_fd = None

        except Exception as e:
            log.debug(f"Cleanup error: {e}")
        finally:
            self.process = None
            self.ready = False


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, LldbSession] = {}

    def get(self, session_id: str) -> LldbSession:
        if session_id not in self.sessions:
            raise ValueError(f"No active LLDB session: {session_id}")
        return self.sessions[session_id]

    async def cleanup_all(self) -> None:
        for session in list(self.sessions.values()):
            await session.cleanup()
        self.sessions.clear()


# ============================================================
# Tool Handlers
# ============================================================

async def handle_lldb_mcp_status(mgr: SessionManager, args: dict) -> str:
    count = len(mgr.sessions)
    return f"LLDB MCP server is running. Active sessions: {count}"


async def handle_lldb_start(mgr: SessionManager, args: dict) -> str:
    lldb_path = args.get("lldb_path", "lldb")
    working_dir = args.get("working_dir")

    # Verify LLDB binary is executable
    try:
        # stdin=DEVNULL: this is only a version probe, but lldb_path comes from
        # the caller, so an arbitrary binary gets spawned here. Without this it
        # would inherit our stdin -- which is the JSON-RPC stream -- and any
        # child that reads input would swallow protocol messages. The real
        # session below deliberately keeps its own PTY stdin; this probe must not.
        proc = await asyncio.create_subprocess_exec(
            lldb_path, "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                return f"Failed to start LLDB: invalid path '{lldb_path}'. {stderr.decode().strip()}"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Failed to start LLDB: timeout verifying '{lldb_path}'"
    except Exception as e:
        return f"Failed to start LLDB: {e}"

    session_id = str(uuid.uuid4())
    working_dir = working_dir or os.getcwd()
    session = LldbSession(session_id=session_id, lldb_path=lldb_path, working_dir=working_dir)

    try:
        output = await asyncio.wait_for(session.start(), timeout=15.0)
    except asyncio.TimeoutError:
        await session.cleanup()
        return "Failed to start LLDB: timeout during initialization"
    except Exception as e:
        await session.cleanup()
        return f"Failed to start LLDB: {e}"

    mgr.sessions[session_id] = session
    return f"LLDB session started. ID: {session_id}\n\nOutput:\n{output}"


async def handle_lldb_load(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    program = args.get("program", "")
    arguments: List[str] = args.get("arguments", [])

    try:
        session = mgr.get(session_id)

        if session.working_dir and not os.path.isabs(program):
            program = os.path.join(session.working_dir, program)

        output = await session.execute_command(f'file "{program}"')

        if arguments:
            args_str = " ".join(f'"{a}"' for a in arguments)
            args_out = await session.execute_command(f"settings set -- target.run-args {args_str}")
            output += f"\n{args_out}"

        session.target = program
        return f"Program loaded: {program}\n\nOutput:\n{output}"

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to load program: {e}"


async def handle_lldb_command(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    command = args.get("command", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command(command)
        return f"Command: {command}\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to execute command: {e}"


async def handle_lldb_terminate(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        await session.cleanup()
        mgr.sessions.pop(session_id, None)
        return f"LLDB session terminated: {session_id}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to terminate session: {e}"


async def handle_lldb_list_sessions(mgr: SessionManager, args: dict) -> str:
    if not mgr.sessions:
        return "No active LLDB sessions."

    lines = [f"Active LLDB sessions ({len(mgr.sessions)}):"]
    for sid, session in mgr.sessions.items():
        lines.append(f"  {sid}: target={session.target or 'none'}, dir={session.working_dir}")
    return "\n".join(lines)


async def handle_lldb_attach(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    pid = args.get("pid")

    try:
        session = mgr.get(session_id)
        # Attach may take time, use longer timeout
        output = await session.execute_command(f"process attach -p {pid}", timeout=60.0)
        return f"Attached to PID {pid}\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to attach: {e}"


async def handle_lldb_load_core(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    program = args.get("program", "")
    core_path = args.get("core_path", "")

    try:
        session = mgr.get(session_id)
        file_out = await session.execute_command(f'file "{program}"')
        # Fix: correct LLDB command for loading a core file
        core_out = await session.execute_command(f'target create --core "{core_path}"')
        bt_out = await session.execute_command("bt")
        return f"Core loaded: {core_path}\n\n{file_out}\n{core_out}\n\nBacktrace:\n{bt_out}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to load core: {e}"


async def handle_lldb_set_breakpoint(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    location = args.get("location", "")
    condition = args.get("condition")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command(f'breakpoint set --name "{location}"')

        if condition:
            match = re.search(r"Breakpoint (\d+):", output)
            if match:
                bp_num = match.group(1)
                cond_out = await session.execute_command(
                    f'breakpoint modify -c "{condition}" {bp_num}'
                )
                output += f"\n{cond_out}"

        label = f"Breakpoint at: {location}"
        if condition:
            label += f" (condition: {condition})"
        return f"{label}\n\nOutput:\n{output}"

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to set breakpoint: {e}"


async def handle_lldb_continue(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command("continue", timeout=60.0)
        return f"Continued execution\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to continue: {e}"


async def handle_lldb_step(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    instructions = _bool_param(args.get("instructions"), default=False)

    try:
        session = mgr.get(session_id)
        command = "si" if instructions else "s"
        output = await session.execute_command(command)
        label = "instruction" if instructions else "line"
        return f"Stepped {label}\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to step: {e}"


async def handle_lldb_next(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    instructions = _bool_param(args.get("instructions"), default=False)

    try:
        session = mgr.get(session_id)
        command = "ni" if instructions else "n"
        output = await session.execute_command(command)
        label = "instruction" if instructions else "function call"
        return f"Stepped over {label}\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to step over: {e}"


async def handle_lldb_finish(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command("finish")
        return f"Finished current function\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to finish: {e}"


async def handle_lldb_backtrace(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    full = _bool_param(args.get("full"), default=False)
    limit = args.get("limit")

    try:
        session = mgr.get(session_id)
        command = "bt"
        if full:
            # 'bt full' shows local variables per frame
            command += " full"
        if limit is not None:
            command += f" {limit}"
        output = await session.execute_command(command)
        return f"Backtrace:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to get backtrace: {e}"


async def handle_lldb_print(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    expression = args.get("expression", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command(f"p {expression}")
        return f"Print {expression}:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to print: {e}"


async def handle_lldb_examine(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    expression = args.get("expression", "")
    fmt = args.get("format", "x")
    count = args.get("count", 1)

    format_map = {
        "x": "x",   # hex
        "d": "d",   # decimal
        "u": "u",   # unsigned decimal
        "o": "o",   # octal
        "t": "t",   # binary
        "i": "i",   # instruction
        "c": "c",   # character
        "f": "f",   # float
        "s": "s",   # string
    }
    lldb_fmt = format_map.get(fmt, "x")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command(
            f"memory read -f {lldb_fmt} -c {count} {expression}"
        )
        return f"Examine {expression} (format={fmt}, count={count}):\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to examine memory: {e}"


async def handle_lldb_info_registers(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    register = args.get("register")

    try:
        session = mgr.get(session_id)
        command = "register read"
        if register:
            command += f" {register}"
        output = await session.execute_command(command)
        return f"Registers:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to read registers: {e}"


async def handle_lldb_watchpoint(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    expression = args.get("expression", "")
    watch_type = args.get("watch_type", "write")

    watch_map = {"read": "r", "write": "w", "read_write": "rw"}
    opt = watch_map.get(watch_type, "w")

    try:
        session = mgr.get(session_id)
        # Fix: -w flag must precede the -- separator
        output = await session.execute_command(
            f"watchpoint set expression -w {opt} -- {expression}"
        )
        return f"Watchpoint set on {expression} (type={watch_type})\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to set watchpoint: {e}"


async def handle_lldb_frame_info(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    frame_index = args.get("frame_index", 0)

    try:
        session = mgr.get(session_id)
        frame_out = await session.execute_command(f"frame select {frame_index}")
        vars_out = await session.execute_command("frame variable")
        source_out = await session.execute_command("source list")
        return (
            f"Frame {frame_index}:\n\n{frame_out}"
            f"\n\nVariables:\n{vars_out}"
            f"\n\nSource:\n{source_out}"
        )
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to get frame info: {e}"


async def handle_lldb_run(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command("run", timeout=60.0)
        return f"Running program\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to run: {e}"


async def handle_lldb_kill(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command("process kill")
        return f"Killed process\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to kill process: {e}"


async def handle_lldb_thread_list(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command("thread list")
        return f"Threads:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to list threads: {e}"


async def handle_lldb_thread_select(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    # Note: thread_index is 1-based LLDB thread index, not OS PID
    thread_index = args.get("thread_index")

    try:
        session = mgr.get(session_id)
        out = await session.execute_command(f"thread select {thread_index}")
        bt_out = await session.execute_command("bt")
        return f"Selected thread {thread_index}\n\nOutput:\n{out}\n\nBacktrace:\n{bt_out}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to select thread: {e}"


async def handle_lldb_breakpoint_list(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command("breakpoint list")
        return f"Breakpoints:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to list breakpoints: {e}"


async def handle_lldb_breakpoint_delete(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    breakpoint_id = args.get("breakpoint_id")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command(f"breakpoint delete {breakpoint_id}")
        return f"Deleted breakpoint {breakpoint_id}\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to delete breakpoint: {e}"


async def handle_lldb_expression(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    expression = args.get("expression", "")

    try:
        session = mgr.get(session_id)
        output = await session.execute_command(f"expression -- {expression}")
        return f"Expression: {expression}\n\nOutput:\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to evaluate expression: {e}"


async def handle_lldb_process_info(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")

    try:
        session = mgr.get(session_id)
        status_out = await session.execute_command("process status")
        info_out = await session.execute_command("process info")
        return f"Process status:\n\n{status_out}\n\nProcess info:\n{info_out}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to get process info: {e}"


async def handle_lldb_disassemble(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    location = args.get("location")
    count = args.get("count", 10)

    try:
        session = mgr.get(session_id)
        command = "disassemble"
        if location:
            command += f" --name {location}"
        command += f" -c {count}"
        output = await session.execute_command(command)
        return f"Disassembly:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to disassemble: {e}"


async def handle_lldb_help(mgr: SessionManager, args: dict) -> str:
    session_id = args.get("session_id", "")
    command = args.get("command")

    try:
        session = mgr.get(session_id)
        if command:
            output = await session.execute_command(f"help {command}")
            return f"Help for '{command}':\n\n{output}"
        else:
            output = await session.execute_command("help")
            return f"LLDB help:\n\n{output}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to get help: {e}"


async def handle_lldb_call(mgr: SessionManager, args: dict) -> str:
    """Dispatcher: call any LLDB tool by name. Used by the AI via the lldb-mcp skill."""
    function = args.get("function", "")
    raw_params = args.get("params") or {}
    try:
        params = _ensure_dict(raw_params)
    except ValueError as exc:
        return f"Error: {exc}"

    if not function:
        count = len(mgr.sessions)
        return f"LLDB MCP server is running. Active sessions: {count}"

    # Prevent recursive dispatch
    if function == "lldb_call":
        return "Cannot dispatch lldb_call recursively"

    handler = ALL_HANDLERS.get(function)
    if handler is None:
        available = ", ".join(sorted(ALL_HANDLERS.keys()))
        return f"Unknown function: '{function}'. Available: {available}"

    # The ceiling is imposed HERE, at the one point every function passes
    # through, so the per-function head/tail decision lives in one auditable
    # table instead of scattered across 29 return statements.
    return _apply_cap(function, params, await handler(mgr, params))


# ============================================================
# MCP Tool Registry
# ============================================================

# Only one tool appears in tools/list (minimal token footprint).
# Called without 'function' → returns server status. All 29 LLDB tools reachable via lldb_call (see lldb-mcp skill).
LISTED_TOOLS = [
    {
        "name": "lldb_call",
        "description": (
            "Call any LLDB function by name. "
            "Returns server status and active session count if called without 'function'. "
            "Invoke the lldb-mcp skill for the full list of available functions and their parameters."
            "\n\n"
            "When NOT to use:\n"
            "  - Ad-hoc shell → Bash. Build → mcp-forge. Code navigation → mcp-purity.\n\n"
            "Prefer this OVER Bash(\"lldb ...\") — interactive LLDB in Bash doesn't work; "
            "this gives structured, scriptable access to all LLDB operations.\n\n"
            "IMPORTANT: Before first use, load the p:mcp-lldb skill for full API reference "
            "and parameter schemas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "LLDB function name (e.g. lldb_start, lldb_load, lldb_backtrace)",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the function (see lldb-mcp skill for schema)",
                },
            },
            "required": [],
        },
    },
]

ALL_HANDLERS = {
    "lldb_mcp_status":        handle_lldb_mcp_status,
    "lldb_call":              handle_lldb_call,
    "lldb_start":             handle_lldb_start,
    "lldb_load":              handle_lldb_load,
    "lldb_command":           handle_lldb_command,
    "lldb_terminate":         handle_lldb_terminate,
    "lldb_list_sessions":     handle_lldb_list_sessions,
    "lldb_attach":            handle_lldb_attach,
    "lldb_load_core":         handle_lldb_load_core,
    "lldb_set_breakpoint":    handle_lldb_set_breakpoint,
    "lldb_continue":          handle_lldb_continue,
    "lldb_step":              handle_lldb_step,
    "lldb_next":              handle_lldb_next,
    "lldb_finish":            handle_lldb_finish,
    "lldb_backtrace":         handle_lldb_backtrace,
    "lldb_print":             handle_lldb_print,
    "lldb_examine":           handle_lldb_examine,
    "lldb_info_registers":    handle_lldb_info_registers,
    "lldb_watchpoint":        handle_lldb_watchpoint,
    "lldb_frame_info":        handle_lldb_frame_info,
    "lldb_run":               handle_lldb_run,
    "lldb_kill":              handle_lldb_kill,
    "lldb_thread_list":       handle_lldb_thread_list,
    "lldb_thread_select":     handle_lldb_thread_select,
    "lldb_breakpoint_list":   handle_lldb_breakpoint_list,
    "lldb_breakpoint_delete": handle_lldb_breakpoint_delete,
    "lldb_expression":        handle_lldb_expression,
    "lldb_process_info":      handle_lldb_process_info,
    "lldb_disassemble":       handle_lldb_disassemble,
    "lldb_help":              handle_lldb_help,
}


# Per-function output policy: (which end survives, line-pageable).
#
# BIAS is a real decision for a debugger, not a default. Two shapes occur here:
#
#   HEAD — the answer is at the TOP. A backtrace starts at frame 0, which is the
#     crash; a disassembly and a memory dump start at the address the caller
#     asked for; `register read` prints the general-purpose bank before the far
#     bulkier vector banks; a value dump prints the object before its nested
#     members; `help` prints the synopsis first. Cutting the bottom loses
#     context, cutting the top loses the answer.
#
#   TAIL — the answer is at the BOTTOM. Every execution-control command shares
#     the inferior's PTY, so its reply is [everything the program printed] and
#     THEN `Process N stopped`, the stop reason and the current frame. A chatty
#     program can push megabytes of its own stdout ahead of those few lines — and
#     those few lines are the entire reason the call was made. Head-biasing
#     `lldb_continue` would faithfully return 24000 chars of printf spam and drop
#     the stop reason.
#
# LINE-PAGEABLE means the payload is a uniform sequence of lines, so the ceiling
# is spent by dropping whole lines with a truthful `offset=` resume hint instead
# of by cutting characters. It is False for composite replies (several
# concatenated command outputs, where "line N" is not a unit of anything) and for
# structured value dumps (nesting makes an arbitrary line offset unreadable) —
# those get a plain line-boundary cut and NO resume hint, since they cannot
# resume.
#
# INVARIANT: pageable implies HEAD. The page note sits on the last line, so a
# tail-biased cut would keep that note AND add the truncation notice — two
# closing lines where the convention allows exactly one. A head-biased cut drops
# the page note it supersedes, leaving exactly one.
CAP_POLICY = {
    # Line-shaped enumerations and dumps: paged by line, resumable.
    "lldb_backtrace":         (BIAS_HEAD, True),
    "lldb_disassemble":       (BIAS_HEAD, True),
    "lldb_examine":           (BIAS_HEAD, True),
    "lldb_info_registers":    (BIAS_HEAD, True),
    "lldb_breakpoint_list":   (BIAS_HEAD, True),
    "lldb_thread_list":       (BIAS_HEAD, True),
    "lldb_list_sessions":     (BIAS_HEAD, True),
    "lldb_help":              (BIAS_HEAD, True),
    # An arbitrary LLDB command: the shape is unknown, so a line is the only unit
    # that is always safe, and paging is the only volume knob this one has.
    "lldb_command":           (BIAS_HEAD, True),

    # Structured value dumps: the top-level value first, nested members after.
    "lldb_print":             (BIAS_HEAD, False),
    "lldb_expression":        (BIAS_HEAD, False),

    # Composites of several command outputs. Head keeps the setup confirmation
    # AND frame 0 of the backtrace that follows it — which is the whole point of
    # opening a core file or selecting a thread.
    "lldb_load_core":         (BIAS_HEAD, False),
    "lldb_thread_select":     (BIAS_HEAD, False),
    "lldb_frame_info":        (BIAS_HEAD, False),
    "lldb_process_info":      (BIAS_HEAD, False),
    "lldb_start":             (BIAS_HEAD, False),
    "lldb_load":              (BIAS_HEAD, False),

    # Short confirmations whose PTY output can still carry a long resolved-
    # location or error list; the verdict line comes first either way.
    "lldb_set_breakpoint":    (BIAS_HEAD, False),
    "lldb_watchpoint":        (BIAS_HEAD, False),
    "lldb_breakpoint_delete": (BIAS_HEAD, False),
    "lldb_terminate":         (BIAS_HEAD, False),
    "lldb_mcp_status":        (BIAS_HEAD, False),

    # Execution control: the stop reason and the current frame arrive LAST, after
    # however much the inferior printed to the shared PTY.
    "lldb_run":               (BIAS_TAIL, False),
    "lldb_continue":          (BIAS_TAIL, False),
    "lldb_step":              (BIAS_TAIL, False),
    "lldb_next":              (BIAS_TAIL, False),
    "lldb_finish":            (BIAS_TAIL, False),
    "lldb_attach":            (BIAS_TAIL, False),
    "lldb_kill":              (BIAS_TAIL, False),
}


def _apply_cap(function: str, args: dict, text: str) -> str:
    """Impose the per-call ceiling on one function's reply.

    An unlisted function falls back to (head, not pageable): a handler added
    later is capped from its first call rather than silently unbounded.
    """
    bias, pageable = CAP_POLICY.get(function, (BIAS_HEAD, False))
    cap = _max_answer_chars(args)
    if pageable:
        text = _page_lines(text, _offset(args), cap)
    return _cap_text(text, cap, bias)


# ============================================================
# MCP Server — JSON-RPC 2.0 over stdio
# ============================================================

class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        self.manager = SessionManager()

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(msg_id: Any, text: str) -> dict:
        """Return a tool-level error (visible in LLM context, per SEP-2140)."""
        return McpServer._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    async def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        log.debug(f"← {method} (id={msg_id})")

        # Notifications carry no id and require no response
        if msg_id is None:
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-lldb", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": LISTED_TOOLS})

        if method == "tools/call":
            return await self._dispatch_tool(msg_id, msg.get("params", {}))

        return self._error(msg_id, -32601, f"Method not found: {method}")

    async def _dispatch_tool(self, msg_id: Any, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as exc:
                return self._tool_error(msg_id, f"'arguments' was a string but not valid JSON: {exc}")
        if not isinstance(args, dict):
            return self._tool_error(msg_id, f"'arguments' must be an object; got {type(args).__name__}.")

        handler = ALL_HANDLERS.get(name)
        if handler is None:
            return self._tool_error(
                msg_id,
                f"Unknown tool: '{name}'. Invoke the lldb-mcp skill for the full tool list."
            )

        try:
            result = await handler(self.manager, args)
            if name != "lldb_call":
                # lldb_call has already capped its inner function's reply under
                # THAT function's policy. Capping again here would append a
                # second closing line whenever the inner call raised the ceiling,
                # and the convention allows exactly one.
                result = _apply_cap(name, args, result)
            return self._result(msg_id, {"content": [{"type": "text", "text": result}]})
        except Exception as e:
            log.debug(f"Handler '{name}' error: {e}")
            return self._tool_error(msg_id, f"Error in {name}: {e}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.debug("mcp-lldb server ready (stdio)")

        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    log.debug("stdin EOF — shutting down")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    log.debug(f"JSON parse error: {e}")
                    continue

                try:
                    response = await self.handle_message(msg)
                except Exception as exc:
                    log.exception("Unhandled exception while handling message")
                    response = self._error(
                        msg.get("id"), -32603,
                        f"Internal error: {type(exc).__name__}: {exc}",
                    )
                if response is not None:
                    log.debug("→ RAW: %s", json.dumps(response))
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()

        finally:
            log.debug("Cleaning up all sessions")
            await self.manager.cleanup_all()


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLDB MCP Server — standalone, no external dependencies"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    parsed = parser.parse_args()

    level = logging.DEBUG if (parsed.debug or parsed.log_file) else logging.WARNING
    log_handlers = []
    if parsed.log_file:
        log_handlers.append(logging.FileHandler(parsed.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    server = McpServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        log.debug("Server stopped")


if __name__ == "__main__":
    main()
