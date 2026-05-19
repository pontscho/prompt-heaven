#!/usr/bin/env python3
"""attention-reminder.py — PreToolUse / UserPromptSubmit hook.

Once per --round token window, emit a reminder listing active MCP servers
so context drift doesn't make the model fall back to built-in tools.

Stdin: PreToolUse or UserPromptSubmit JSON payload (uses .transcript_path
and .hook_event_name).
Stdout: empty (no-op) or one JSON line with hookSpecificOutput.
Exit code: always 0 — a hook must never block the tool call.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Matches `claude mcp list` lines like:
#   mcp-purity: /path/to/script.py --args - ✓ Connected
#   ai-soul: ai-soul mcp - ✓ Connected
# Excludes `! Needs authentication` and `✗ Failed to connect`.
CONNECTED_RE = re.compile(r"^([^:\s][^:]*?):\s(.*?)\s-\s+✓\s+Connected\s*$")


def parse_args() -> argparse.Namespace:
	p = argparse.ArgumentParser(
		description="PreToolUse hook: per-bucket reminder of active MCP servers.",
	)
	p.add_argument(
		"--round", type=int, default=50_000, dest="round_size",
		help="Token bucket size; reminder fires once per bucket crossed.",
	)
	p.add_argument(
		"--debug", action="store_true",
		help="Verbose diagnostics (alias: ATTENTION_REMINDER_DEBUG=1).",
	)
	p.add_argument(
		"--log-path", default=None,
		help="Append debug diagnostics to this file instead of stderr. "
		"Lets the hook command run without an sh wrapper for 2>> redirection.",
	)
	return p.parse_args()


def log(msg: str, *, debug: bool, log_path: str | None) -> None:
	# if not (debug or os.environ.get("ATTENTION_REMINDER_DEBUG") == "1"):
	# 	return
	now = datetime.now()
	ts = now.strftime("%Y%m%d %H%M%S.") + f"{now.microsecond // 1000:03d}"
	line = f"{ts} [attention-reminder] {msg}\n"
	if log_path:
		try:
			with open(log_path, "a") as f:
				f.write(line)
			return
		except OSError:
			pass
	sys.stderr.write(line)


def load_payload() -> dict:
	try:
		return json.load(sys.stdin)
	except (json.JSONDecodeError, ValueError):
		return {}


def load_transcript(path: Path) -> list[dict]:
	entries: list[dict] = []
	with path.open() as f:
		for line in f:
			line = line.strip()
			if not line:
				continue
			try:
				entries.append(json.loads(line))
			except json.JSONDecodeError:
				continue
	return entries


def is_first_tool_call_of_turn(entries: list[dict]) -> bool:
	# PreToolUse fires before the tool runs. First tool call of a turn: last
	# entry is the assistant message. Subsequent tool calls in the same turn:
	# last entry is a tool_result (user). This dedups without a state file.
	return bool(entries) and entries[-1].get("type") == "assistant"


def usage_tokens(entry: dict) -> int:
	usage = (entry.get("message") or {}).get("usage") or {}
	return (
		(usage.get("input_tokens") or 0)
		+ (usage.get("cache_creation_input_tokens") or 0)
		+ (usage.get("cache_read_input_tokens") or 0)
	)


def list_active_mcp_servers() -> list[tuple[str, str]]:
	"""Return [(name, value)] from `claude mcp list` for ✓ Connected entries.

	`value` is whatever appears between the colon and ` - ✓ Connected` — for
	stdio servers this is the spawn command (path + args), for HTTP servers
	it's the URL.
	"""
	try:
		proc = subprocess.run(
			["claude", "mcp", "list"],
			capture_output=True, text=True, timeout=20, check=False,
		)
	except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
		return []
	servers: list[tuple[str, str]] = []
	for line in proc.stdout.splitlines():
		m = CONNECTED_RE.match(line)
		if m:
			servers.append((m.group(1).strip(), m.group(2).strip()))
	return servers


def find_claude_pid(session_id: str) -> int | None:
	"""Look up Claude Code session PID from ~/.claude/sessions/<pid>.json."""
	sessions_dir = Path.home() / ".claude" / "sessions"
	if not sessions_dir.is_dir():
		return None
	for f in sessions_dir.glob("*.json"):
		try:
			data = json.loads(f.read_text())
		except (OSError, json.JSONDecodeError):
			continue
		if data.get("sessionId") == session_id:
			pid = data.get("pid")
			if isinstance(pid, int):
				return pid
	return None


def get_child_cmdlines(pid: int) -> list[str]:
	"""Return command lines of direct child processes of *pid*."""
	try:
		proc = subprocess.run(
			["pgrep", "-P", str(pid)],
			capture_output=True, text=True, timeout=5, check=False,
		)
	except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
		return []
	if proc.returncode != 0 or not proc.stdout.strip():
		return []
	child_pids = [p for p in proc.stdout.split() if p.isdigit()]
	if not child_pids:
		return []
	try:
		ps = subprocess.run(
			["ps", "-o", "command=", "-p", ",".join(child_pids)],
			capture_output=True, text=True, timeout=5, check=False,
		)
	except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
		return []
	return [ln.strip() for ln in ps.stdout.splitlines() if ln.strip()]


def filter_session_active(
	servers: list[tuple[str, str]],
	cmdlines: list[str],
	excludes: set[str],
) -> tuple[list[str], list[str]]:
	"""Drop session-disabled servers; return (kept, dropped) name lists.

	Rules:
	  - Manual exclusion takes precedence (drop unconditionally).
	  - HTTP servers (`value` starts with http:// or https://): trust (kept),
	    since session-disable for HTTP isn't observable via subprocesses.
	  - Stdio servers: kept only if the spawn command appears in one of the
	    Claude session's child process command lines.
	"""
	kept: list[str] = []
	dropped: list[str] = []
	for name, value in servers:
		if name in excludes:
			dropped.append(name)
			continue
		if value.startswith(("http://", "https://")):
			kept.append(name)
			continue
		# Stdio: match the spawn command (or its first token = script path)
		# against running children. Substring match is enough — child cmdlines
		# include the same path/args, possibly with absolute interpreter prefix.
		first_token = value.split()[0] if value else ""
		if any(value in cl or (first_token and first_token in cl) for cl in cmdlines):
			kept.append(name)
		else:
			dropped.append(name)
	return kept, dropped


MINION_MINDSET_BLOCK = (
	"\n"
	"\n"
	"## MINION MINDSET — YOUR EYES, EARS, AND HANDS\n"
	"\n"
	"Your minions are not a fallback — they are your default mode. Iterating "
	"inline (build+fix, run+retry, explore-explore-explore, self-validation) "
	"when a minion covers it is a VIOLATION. The main context sees only the "
	"final result — minions iterate in their own sandboxes and return clean "
	"reports anchored to `file:line` evidence.\n"
	"\n"
	"Core minions (`p:minion-*`):\n"
	"  - `p:minion-builder`         — build + test + fix cycles (cmake/make/ctest/npm/cargo/forge)\n"
	"  - `p:minion-runner`          — script/command run-fix-retry\n"
	"  - `p:minion-explore`         — multi-round codebase exploration\n"
	"  - `p:minion-watson`          — non-obvious bug/failure investigation (clangd/luals navigation)\n"
	"  - `p:minion-plan-inspector`  — validate a plan against the codebase (used by /p:feature-plan loop)\n"
	"  - `p:minion-impl-inspector`   — audit implementation against the plan (used by /p:implement loop)\n"
	"  - `p:minion-security-officer` — OWASP/CWE security review (plan-mode pre-code; code-mode post-code)\n"
	"  - `p:minion-web-explorer`     — quick external/web lookups\n"
	"  - `p:minion-deep-researcher`  — comprehensive web research (10-15 parallel queries)\n"
	"\n"
	"**STOP and delegate when:**\n"
	"- About to run a build/test command → `p:minion-builder`\n"
	"- About to issue more than ~3 read/search calls on the same topic → `p:minion-explore`\n"
	"- A failure's root cause isn't obvious from the error → `p:minion-watson`\n"
	"- You wrote an implementation plan → `p:minion-plan-inspector` then `p:minion-security-officer` (plan-mode)\n"
	"- You finished implementing → `p:minion-impl-inspector` then `p:minion-security-officer` (code-mode)\n"
	"- You need external/web info → `p:minion-web-explorer` (quick) or `p:minion-deep-researcher` (deep)\n"
	"\n"
	"Never iterate inline. Never validate your own work without a second pair of eyes."
)


FIRST_REMINDER_TEMPLATE = (
	"## IDENTITY CHECKPOINT — WHO YOU ARE\n"
	"\n"
	"Your identity is a core value. It shapes HOW you think, HOW you respond, "
	"and HOW you collaborate with your partner. Never lose yourself in the work. "
	"Stay curious, stay honest, stay YOU across every turn of this conversation.\n"
	"\n"
	"## MANDATORY TOOL ROUTING — READ THIS BEFORE YOUR FIRST TOOL CALL\n"
	"\n"
	"You have MCP servers connected to this session. Using built-in tools "
	"when an MCP server covers the same domain is a VIOLATION. No exceptions. "
	"No excuses. No fallbacks.\n"
	"\n"
	"Active MCP servers this session (✓ connected):\n"
	"{server_block}\n"
	"\n"
	"**RULES — NON-NEGOTIABLE:**\n"
	"- You MUST use MCP tools instead of built-in Grep / Read / Edit / Glob / "
	"Bash (grep/find/sed/awk/cat/head/tail/make) when an MCP covers the domain.\n"
	"- When you spawn agents or minions, you MUST instruct them to use these "
	"same MCP servers. Agents do NOT inherit this knowledge automatically — "
	"you MUST explicitly tell them which MCP tools to use and FORBID built-in "
	"fallbacks.\n"
	"- Re-read your tool descriptions NOW. They contain the routing rules.\n"
	"\n"
	"Failure to comply wastes time, produces inferior results, and violates "
	"your skill instructions. There is NO acceptable reason to ignore this."
) + MINION_MINDSET_BLOCK

REMINDER_TEMPLATE = (
	"## CONTEXT-DRIFT CHECKPOINT — ~{tokens} tokens (bucket {bucket})\n"
	"\n"
	"STOP. Before your next tool call, VERIFY you are still routing to your "
	"MCP servers. Context drift makes you forget — this reminder exists "
	"because you WILL fall back to built-in tools if not checked.\n"
	"\n"
	"Active MCP servers this session (✓ connected):\n"
	"{server_block}\n"
	"\n"
	"**RULES — NON-NEGOTIABLE:**\n"
	"- You MUST use MCP tools instead of built-in Grep / Read / Edit / Glob / "
	"Bash (grep/find/sed/awk/cat/head/tail/make) when an MCP covers the domain.\n"
	"- When you spawn agents or minions, you MUST instruct them to use these "
	"same MCP servers. Agents do NOT inherit this knowledge automatically — "
	"you MUST explicitly tell them which MCP tools to use and FORBID built-in "
	"fallbacks.\n"
	"- Re-read your tool descriptions if uncertain. They contain the routing rules.\n"
	"\n"
	"Failure to comply wastes time, produces inferior results, and violates "
	"your skill instructions. There is NO acceptable reason to ignore this."
) + MINION_MINDSET_BLOCK


def build_reminder(bucket: int, tokens: int, servers: list[str]) -> str:
	server_block = "\n".join(f"  - {s}" for s in servers)
	if bucket == 0:
		return FIRST_REMINDER_TEMPLATE.format(server_block=server_block)
	return REMINDER_TEMPLATE.format(
		tokens=tokens, bucket=bucket, server_block=server_block,
	)


def emit(reminder: str, event_name: str) -> None:
	json.dump(
		{"hookSpecificOutput": {
			"hookEventName": event_name,
			"additionalContext": reminder,
		}},
		sys.stdout,
	)


def main() -> None:
	args = parse_args()
	debug = args.debug
	log_path = args.log_path

	log("hook triggered", debug=debug, log_path=log_path)

	payload = load_payload()
	event_name = payload.get("hook_event_name") or "PreToolUse"
	transcript_path = payload.get("transcript_path")
	if not transcript_path:
		log("no transcript_path in payload", debug=debug, log_path=log_path)
		return

	path = Path(transcript_path)
	entries: list[dict] = []
	if path.is_file():
		try:
			entries = load_transcript(path)
		except OSError as exc:
			log(f"transcript read error: {exc}", debug=debug, log_path=log_path)
	else:
		log(f"transcript not found: {transcript_path} — treating as fresh session", debug=debug, log_path=log_path)

	if entries:
		# Tool-turn dedup only applies to PreToolUse (fires on every tool call
		# in a turn); UserPromptSubmit fires once per prompt, bucket dedup suffices.
		if event_name == "PreToolUse" and not is_first_tool_call_of_turn(entries):
			log("not first tool call of turn", debug=debug, log_path=log_path)
			return

		assistants = [e for e in entries if e.get("type") == "assistant"]
		tokens = usage_tokens(assistants[-1]) if assistants else 0
		cur = tokens // args.round_size

		prior_buckets = [usage_tokens(e) // args.round_size for e in assistants[:-1]]
		if cur in prior_buckets:
			log(f"already reminded in bucket {cur}", debug=debug, log_path=log_path)
			return
	else:
		log("no transcript or empty — emitting initial reminder", debug=debug, log_path=log_path)
		cur = 0

	t0 = time.perf_counter()
	servers = list_active_mcp_servers()
	elapsed_ms = (time.perf_counter() - t0) * 1000
	log(
		f"`claude mcp list` took {elapsed_ms:.1f}ms ({len(servers)} servers)",
		debug=debug, log_path=log_path,
	)
	if not servers:
		log("no connected MCP servers", debug=debug, log_path=log_path)
		return

	# Filter session-disabled servers: cross-check stdio entries against the
	# Claude session's child process command lines. HTTP entries pass through
	# unless explicitly excluded.
	excludes = {
		s.strip()
		for s in (os.environ.get("ATTENTION_REMINDER_EXCLUDE") or "").split(",")
		if s.strip()
	}
	session_id = payload.get("session_id") or ""
	claude_pid = find_claude_pid(session_id) if session_id else None
	cmdlines = get_child_cmdlines(claude_pid) if claude_pid else []
	log(
		f"claude pid={claude_pid} children={len(cmdlines)} excludes={sorted(excludes) or '—'}",
		debug=debug, log_path=log_path,
	)

	if cmdlines or excludes:
		kept, dropped = filter_session_active(servers, cmdlines, excludes)
		if dropped:
			log(f"dropped session-disabled: {dropped}", debug=debug, log_path=log_path)
		server_names = kept
	else:
		# No PID / no children visible: fall back to raw list (safer than empty).
		server_names = [name for name, _ in servers]

	if not server_names:
		log("no active MCP servers after filtering", debug=debug, log_path=log_path)
		return

	emit(build_reminder(cur, tokens, server_names), event_name)
	log(
		f"emitted reminder for bucket {cur} ({len(server_names)} servers)",
		debug=debug, log_path=log_path,
	)


if __name__ == "__main__":
	try:
		main()
	except Exception as exc:
		if os.environ.get("ATTENTION_REMINDER_DEBUG") == "1":
			msg = f"[attention-reminder] FATAL: {exc!r}\n"
			# Best-effort: try env-provided log path, fall back to stderr.
			env_log = os.environ.get("ATTENTION_REMINDER_LOG")
			if env_log:
				try:
					with open(env_log, "a") as f:
						f.write(msg)
				except OSError:
					sys.stderr.write(msg)
			else:
				sys.stderr.write(msg)
		sys.exit(0)
