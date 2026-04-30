#!/usr/bin/env python3
"""MCP-Compile: Build command runner MCP server with output filtering.

Single-tool dispatcher pattern: exposes one MCP tool (compile_call) that routes
to internal handler functions via the 'function' parameter.

Requires only Python 3.9+ stdlib modules.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mcp-compile")


# ---------------------------------------------------------------------------
# Parameter aliases
# ---------------------------------------------------------------------------

PARAM_ALIASES = {
	"cmd": "command",
	"dir": "cwd",
	"timeout_sec": "timeout",
}

FILTER_ALIASES = {
	"pattern": "grep",
	"regex": "grep",
	"context": "grep_context",
	"invert": "invert_grep",
}


def _resolve_aliases(params: dict, aliases: dict) -> dict:
	"""Return a new dict with aliased parameter names resolved to canonical names."""
	resolved = {}
	for key, value in params.items():
		canonical = aliases.get(key, key)
		if canonical not in resolved:
			resolved[canonical] = value
	return resolved


# ---------------------------------------------------------------------------
# Output filtering
# ---------------------------------------------------------------------------

def _apply_grep(lines: List[str], pattern: str, context: int = 0,
                invert: bool = False) -> List[str]:
	"""Filter lines by regex pattern with optional context lines."""
	try:
		regex = re.compile(pattern, re.IGNORECASE)
	except re.error as exc:
		return [f"[invalid grep pattern: {exc}]"]

	if not context:
		if invert:
			return [l for l in lines if not regex.search(l)]
		return [l for l in lines if regex.search(l)]

	# With context lines
	total = len(lines)
	matched_indices = set()
	for i, line in enumerate(lines):
		hit = regex.search(line)
		if (hit and not invert) or (not hit and invert):
			for j in range(max(0, i - context), min(total, i + context + 1)):
				matched_indices.add(j)

	result = []
	prev_idx = -2
	for idx in sorted(matched_indices):
		if idx > prev_idx + 1 and prev_idx >= 0:
			result.append("--")
		result.append(lines[idx])
		prev_idx = idx

	return result


def _apply_head_tail(lines: List[str], head: Optional[int],
                     tail: Optional[int]) -> List[str]:
	"""Apply head and/or tail filtering."""
	total = len(lines)

	if head and tail:
		if head + tail >= total:
			return lines
		top = lines[:head]
		bottom = lines[-tail:]
		skipped = total - head - tail
		return top + [f"... ({skipped} lines omitted) ..."] + bottom

	if head:
		if head >= total:
			return lines
		return lines[:head] + [f"... ({total - head} more lines) ..."]

	if tail:
		if tail >= total:
			return lines
		return [f"... ({total - tail} lines omitted) ..."] + lines[-tail:]

	return lines


def filter_output(raw_lines: List[str], filter_cfg: Optional[dict]) -> List[str]:
	"""Apply filter chain: grep → head/tail."""
	if not filter_cfg:
		return raw_lines

	cfg = _resolve_aliases(filter_cfg, FILTER_ALIASES)
	lines = raw_lines

	# Step 1: grep
	grep_pattern = cfg.get("grep")
	if grep_pattern:
		grep_ctx = cfg.get("grep_context", 0)
		invert = cfg.get("invert_grep", False)
		lines = _apply_grep(lines, grep_pattern, grep_ctx, invert)

	# Step 2: head/tail
	head = cfg.get("head")
	tail = cfg.get("tail")
	if head or tail:
		lines = _apply_head_tail(lines, head, tail)

	return lines


# ---------------------------------------------------------------------------
# Command safety check
# ---------------------------------------------------------------------------

DANGEROUS_PATTERNS = [
	r'\brm\b',
	r'\brmdir\b',
	r'\bsudo\b',
	r'\bmkfs\b',
	r'\bdd\b\s+',
	r'\b:\(\)\s*\{',        # fork bomb
	r'>\s*/dev/sd',
	r'\bshred\b',
	r'\bwipe\b',
	r'\bfdisk\b',
	r'\bparted\b',
	r'\bcurl\b.*\|\s*sh',
	r'\bwget\b.*\|\s*sh',
	r'\bchmod\b',
	r'\bchown\b',
	r'\bkill\b',
	r'\bpkill\b',
	r'\bkillall\b',
	r'\breboot\b',
	r'\bshutdown\b',
	r'\binit\b\s+[0-6]',
]

_DANGEROUS_RE = re.compile('|'.join(DANGEROUS_PATTERNS), re.IGNORECASE)


def _check_command_safety(command: str) -> None:
	"""Reject commands containing dangerous patterns."""
	match = _DANGEROUS_RE.search(command)
	if match:
		raise ValueError(
			f"BLOCKED: command contains dangerous pattern '{match.group()}'. "
			f"This tool is for build commands only, not destructive operations."
		)


# ---------------------------------------------------------------------------
# Build handler
# ---------------------------------------------------------------------------

MAX_OUTPUT_BYTES = 50 * 1024 * 1024  # 50 MB


def handle_build(params: dict, project_root: str, default_command: Optional[str],
                 default_timeout: int) -> dict:
	"""Run a build command and return filtered output as Markdown."""
	params = _resolve_aliases(params, PARAM_ALIASES)

	command = params.get("command") or default_command
	if not command:
		raise ValueError(
			"Missing required parameter: command (and no --default-command configured)"
		)

	_check_command_safety(command)

	cwd = params.get("cwd", project_root)
	if not os.path.isabs(cwd):
		cwd = os.path.join(project_root, cwd)
	cwd = os.path.realpath(cwd)

	timeout = params.get("timeout", default_timeout)
	merge_stderr = params.get("merge_stderr", True)
	filter_cfg = params.get("filter")

	log.info("Running: %s (cwd=%s, timeout=%ds)", command, cwd, timeout)

	stderr_target = subprocess.STDOUT if merge_stderr else subprocess.PIPE
	start = time.monotonic()
	timed_out = False
	exit_code = -1

	try:
		proc = subprocess.Popen(
			command,
			shell=True,
			cwd=cwd,
			stdout=subprocess.PIPE,
			stderr=stderr_target,
			preexec_fn=os.setsid,
		)

		try:
			stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
		except subprocess.TimeoutExpired:
			timed_out = True
			# Kill the entire process group
			try:
				os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
			except OSError:
				pass
			try:
				proc.wait(timeout=5)
			except subprocess.TimeoutExpired:
				try:
					os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
				except OSError:
					pass
				proc.wait(timeout=5)
			stdout_bytes = proc.stdout.read() if proc.stdout else b""
			stderr_bytes = proc.stderr.read() if proc.stderr else b""

		exit_code = proc.returncode

	except FileNotFoundError:
		raise ValueError(f"Command not found or cwd doesn't exist: {command}")
	except OSError as exc:
		raise ValueError(f"Failed to execute command: {exc}")

	duration = time.monotonic() - start

	# Decode output
	raw_output = stdout_bytes or b""
	if stderr_bytes and not merge_stderr:
		raw_output = raw_output + b"\n--- stderr ---\n" + stderr_bytes

	if len(raw_output) > MAX_OUTPUT_BYTES:
		raw_output = raw_output[:MAX_OUTPUT_BYTES]
		truncated_bytes = True
	else:
		truncated_bytes = False

	output_text = raw_output.decode("utf-8", errors="replace")
	all_lines = output_text.splitlines()
	total_lines = len(all_lines)

	# Apply filters
	filtered_lines = filter_output(all_lines, filter_cfg)
	shown_lines = len(filtered_lines)

	# Build Markdown
	if timed_out:
		status = f"TIMEOUT (killed after {timeout}s)"
		icon = "⏱️"
	elif exit_code == 0:
		status = "SUCCESS"
		icon = "✅"
	else:
		status = f"FAILED (exit code: {exit_code})"
		icon = "❌"

	md_parts = [f"## Build Result: {icon} {status}"]
	md_parts.append(f"**Project:** `{project_root}`")
	md_parts.append(f"**Command:** `{command}`")
	md_parts.append(f"**Duration:** {duration:.1f}s")

	md_parts.append("")
	md_parts.append("```")
	md_parts.append("\n".join(filtered_lines))
	md_parts.append("```")

	return {"__raw_text__": "\n".join(md_parts)}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS = {
	"build": "build",
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def handle_compile_call(arguments: dict, project_root: str,
                        default_command: Optional[str],
                        default_timeout: int) -> dict:
	"""Route a compile_call invocation to the appropriate handler."""
	function = (arguments.get("function") or arguments.get("f") or "").strip()
	params = _resolve_aliases(arguments.get("params") or arguments.get("p") or {},
	                          PARAM_ALIASES)

	if not function:
		info = f"mcp-compile OK — project: {project_root}"
		if default_command:
			info += f"\nDefault command: {default_command}"
		info += f"\nDefault timeout: {default_timeout}s"
		info += "\nAvailable functions:\n  build    Run a build command with output filtering"
		return {"__raw_text__": info}

	if function == "build":
		try:
			return handle_build(params, project_root, default_command, default_timeout)
		except (ValueError, OSError) as exc:
			return {"error": str(exc)}

	return {"error": f"Unknown function: {function}. Available: build"}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

COMPILE_CALL_TOOL = {
	"name": "compile_call",
	"description": (
		"Build/compile runner (make, cmake, ninja, npm/cargo/gradle/mvn) with output "
		"filtering (grep/head/tail).\n\n"
		"When NOT to use:\n"
		"  - Ad-hoc shell → Bash. Tests → Bash.\n"
		"  - Read-only git → mcp-git. File search/edit → mcp-purity.\n\n"
		"Prefer this OVER Bash(\"make ...\"), Bash(\"cmake ...\"), Bash(\"ninja ...\"), "
		"Bash(\"npm run build\"), etc. — those flood context with build noise.\n\n"
		"Functions: build. build params: command (optional if --default-command set), "
		"cwd (default project root), timeout (default 600s), "
		"filter ({grep, head, tail} — applied as grep → head/tail). "
		"Aliases: cmd→command, dir→cwd, pattern→grep.\n\n"
		"Example: function=\"build\", "
		"params={\"command\":\"make -C build -j4\",\"filter\":{\"grep\":\"error|warning\"}}\n"
		"Call without 'function' for status."
	),
	"inputSchema": {
		"type": "object",
		"properties": {
			"function": {
				"type": "string",
				"description": "Function name to call: 'build'",
			},
			"params": {
				"type": "object",
				"description": (
					"Function parameters. For build: "
					"{command?, cwd?, timeout?, merge_stderr?, "
					"filter?: {grep?, grep_context?, invert_grep?, head?, tail?}}"
				),
			},
		},
	},
}


class McpServer:
	"""Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

	def __init__(self, project_root: str, default_command: Optional[str],
	             default_timeout: int):
		self.project_root = os.path.realpath(project_root)
		self.default_command = default_command
		self.default_timeout = default_timeout

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
				response = self._handle_message(msg)
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
				"serverInfo": {"name": "mcp-compile", "version": "0.1.0"},
				"capabilities": {"tools": {}},
			})

		if method == "ping":
			return self._result(msg_id, {})

		if method == "tools/list":
			return self._result(msg_id, {"tools": [COMPILE_CALL_TOOL]})

		if method == "tools/call":
			return self._handle_tool_call(msg_id, params)

		return self._error(msg_id, -32601, f"Method not found: {method}")

	def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
		tool_name = params.get("name", "")
		arguments = params.get("arguments") or {}

		if tool_name != "compile_call":
			return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")

		result = handle_compile_call(
			arguments, self.project_root,
			self.default_command, self.default_timeout,
		)
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
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
	parser = argparse.ArgumentParser(
		description="MCP-Compile: Build command runner MCP server with output filtering"
	)
	parser.add_argument("--project-root", required=True, help="Project root directory")
	parser.add_argument("--default-command", default=None,
	                    help="Default build command if not specified in call")
	parser.add_argument("--default-timeout", type=int, default=600,
	                    help="Default timeout in seconds (default: 600)")
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

	if not os.path.isdir(args.project_root):
		print(f"Error: project root is not a directory: {args.project_root}",
		      file=sys.stderr)
		sys.exit(1)

	server = McpServer(args.project_root, args.default_command, args.default_timeout)
	asyncio.run(server.run())


if __name__ == "__main__":
	main()
