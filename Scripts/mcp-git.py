#!/usr/bin/env python3
"""MCP-Git: Read-only git operations MCP server.

Single-tool dispatcher pattern (like mcp-purity): one tool (git_call) routes
to whitelisted git subcommands. Mutating commands are NOT exposed — those must
go through the user's normal Bash tool with manual approval.

Whitelist strategy:
  - Pure read-only subcommands: any args allowed.
  - Dual-use subcommands: arg-level filter rejects mutating flags.
  - Network-read subcommands (fetch --dry-run, ls-remote, apply --check): allowed.
  - Anything not in the whitelist: rejected.

Output is always Markdown (no JSON/YAML).
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("mcp-git")


# ---------------------------------------------------------------------------
# Sandbox utility (same as mcp-purity)
# ---------------------------------------------------------------------------

def safe_path(project_root: str, relative_path: str, strict: bool = False) -> str:
    if os.path.isabs(relative_path) and not strict:
        return os.path.realpath(relative_path)
    resolved = os.path.realpath(os.path.join(project_root, relative_path))
    if not resolved.startswith(project_root + os.sep) and resolved != project_root:
        raise ValueError(f"Path escapes project root: {relative_path}")
    return resolved


# ---------------------------------------------------------------------------
# Whitelist & validators
# ---------------------------------------------------------------------------

# Pure read-only — any args allowed
SAFE_SUBCOMMANDS = {
    "status", "log", "diff", "show", "blame", "annotate",
    "reflog", "shortlog", "whatchanged", "describe",
    "rev-parse", "rev-list", "merge-base", "name-rev",
    "show-ref", "show-branch", "cat-file", "count-objects",
    "ls-files", "ls-tree", "for-each-ref", "grep",
    "ls-remote", "version", "help",
}


def _reject_flags(args: List[str], forbidden: set, subcmd: str) -> None:
    for a in args:
        base = a.split("=", 1)[0]
        if base in forbidden:
            raise ValueError(f"Forbidden git {subcmd} flag (would mutate repo): {a}")


def validate_branch(args: List[str]) -> None:
    forbidden = {
        "-d", "-D", "--delete",
        "-m", "-M", "--move",
        "-c", "-C", "--copy",
        "-u", "--set-upstream", "--set-upstream-to", "--unset-upstream",
        "--edit-description",
    }
    _reject_flags(args, forbidden, "branch")
    list_flags = {
        "-l", "--list", "-a", "--all", "-r", "--remotes",
        "--show-current", "--contains", "--no-contains",
        "--merged", "--no-merged", "--points-at",
    }
    has_positional = any(not a.startswith("-") for a in args)
    has_list_flag = any(a in list_flags for a in args)
    if has_positional and not has_list_flag:
        raise ValueError(
            "git branch with a positional arg would create a branch. "
            "Add -l/--list/--contains/--merged/--points-at to use it as a filter."
        )


def validate_tag(args: List[str]) -> None:
    forbidden = {
        "-d", "-D", "--delete",
        "-a", "--annotate",
        "-s", "--sign", "--no-sign",
        "-m", "-F", "--file",
        "-f", "--force",
        "--cleanup",
    }
    _reject_flags(args, forbidden, "tag")
    list_flags = {
        "-l", "--list", "-n",
        "--contains", "--no-contains",
        "--merged", "--no-merged", "--points-at",
    }
    has_positional = any(not a.startswith("-") and not a.startswith("-n") for a in args)
    has_list_flag = any(a == "-l" or a == "--list" or a.startswith("-n")
                        or a in list_flags for a in args)
    if has_positional and not has_list_flag:
        raise ValueError(
            "git tag with a positional arg would create a tag. "
            "Add -l/--list/--contains/--merged/--points-at to use it as a filter."
        )


def validate_remote(args: List[str]) -> None:
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return
    allowed = {"show", "get-url"}
    if positional[0] not in allowed:
        raise ValueError(
            f"git remote subcommand '{positional[0]}' not allowed. "
            f"Allowed: {', '.join(sorted(allowed))} (or no subcommand to list)."
        )


def validate_stash(args: List[str]) -> None:
    allowed = {
        "list", "show",
        "push", "save",
        "pop", "apply",
        "drop", "clear",
        "branch",
        "create", "store",
    }
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return
    if positional[0] not in allowed:
        raise ValueError(
            f"git stash subcommand '{positional[0]}' not recognized. "
            f"Allowed: {', '.join(sorted(allowed))}."
        )


def validate_config(args: List[str]) -> None:
    forbidden = {
        "--add", "--unset", "--unset-all", "--replace-all",
        "--rename-section", "--remove-section",
        "--edit", "-e", "--unset-regexp",
    }
    _reject_flags(args, forbidden, "config")
    has_read_flag = any(
        a == "--list" or a == "-l" or a.startswith("--get")
        for a in args
    )
    positional = [a for a in args if not a.startswith("-")]
    if has_read_flag:
        return
    if len(positional) >= 2:
        raise ValueError(
            "git config <name> <value> sets a value (mutates). "
            "Use --get/--get-all/--get-regexp/--list to read."
        )


def validate_fetch(args: List[str]) -> None:
    if "--dry-run" not in args:
        raise ValueError("git fetch is only allowed with --dry-run.")


def validate_apply(args: List[str]) -> None:
    if "--check" not in args:
        raise ValueError("git apply is only allowed with --check.")


FILTERED_SUBCOMMANDS: Dict[str, Callable[[List[str]], None]] = {
    "branch": validate_branch,
    "tag":    validate_tag,
    "remote": validate_remote,
    "stash":  validate_stash,
    "config": validate_config,
    "fetch":  validate_fetch,
    "apply":  validate_apply,
}

SUBCOMMAND_DESCRIPTIONS = {
    "status":         "Working tree status",
    "log":            "Commit history",
    "diff":           "Show changes (working tree, staged, between commits)",
    "show":           "Show a commit, tag, or object",
    "blame":          "Annotate each line with last modifying commit",
    "annotate":       "Alias for blame",
    "reflog":         "Show ref update log",
    "shortlog":       "Summarize git log output",
    "whatchanged":    "Show logs with diff each commit introduces",
    "describe":       "Describe a commit using nearest tag",
    "rev-parse":      "Parse and print revisions",
    "rev-list":       "List commits in reverse order",
    "merge-base":     "Find common ancestor of commits",
    "name-rev":       "Find symbolic name for revision",
    "show-ref":       "List refs",
    "show-branch":    "Show branches and their commits",
    "cat-file":       "Show object content / type / size",
    "count-objects":  "Count and disk usage of objects",
    "ls-files":       "List tracked files",
    "ls-tree":        "List tree object contents",
    "for-each-ref":   "Iterate refs with formatting",
    "grep":           "Search tracked files",
    "ls-remote":      "List references on remote (network)",
    "version":        "Print git version",
    "help":           "Show git help",
    "branch":         "List branches (mutating flags blocked)",
    "tag":            "List tags (mutating flags blocked)",
    "remote":         "List remotes / show / get-url",
    "stash":          "Full stash support: list/show/push/save/pop/apply/drop/clear/branch/create/store",
    "config":         "Read config (--list / --get*)",
    "fetch":          "Network read with --dry-run only",
    "apply":          "Patch validity check with --check only",
}


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _md_fence(content: str, lang: str = "") -> str:
    max_run = 0
    for run in re.findall(r"`+", content):
        if len(run) > max_run:
            max_run = len(run)
    fence = "`" * max(3, max_run + 1)
    return f"{fence}{lang}\n{content}\n{fence}"


def _quote_arg(a: str) -> str:
    if not a or any(c in a for c in " \t\"'\\$`"):
        return "'" + a.replace("'", "'\\''") + "'"
    return a


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

DEFAULT_MAX_CHARS = 100_000


def handle_git_call(arguments: dict, project_root: str, strict: bool = False) -> dict:
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    params = arguments.get("params") or arguments.get("p") or {}

    if not function:
        lines = ["## mcp-git", "", f"Project root: `{project_root}`", "", "Allowed subcommands:", ""]
        for name in sorted(set(list(SAFE_SUBCOMMANDS) + list(FILTERED_SUBCOMMANDS.keys()))):
            desc = SUBCOMMAND_DESCRIPTIONS.get(name, "")
            lines.append(f"- `{name}` — {desc}")
        return {"__raw_text__": "\n".join(lines)}

    if function in SAFE_SUBCOMMANDS:
        validator: Optional[Callable[[List[str]], None]] = None
    elif function in FILTERED_SUBCOMMANDS:
        validator = FILTERED_SUBCOMMANDS[function]
    else:
        return {"error": (
            f"git subcommand '{function}' is not on the read-only whitelist. "
            "Use the Bash tool for mutating operations."
        )}

    args = params.get("args", [])
    if args is None:
        args = []
    if isinstance(args, str):
        args = [args]
    if not isinstance(args, list):
        return {"error": "params.args must be a list of strings"}
    args = [str(a) for a in args]

    if validator is not None:
        try:
            validator(args)
        except ValueError as exc:
            return {"error": str(exc)}

    cwd_param = params.get("cwd", ".")
    try:
        cwd = safe_path(project_root, cwd_param, strict)
    except ValueError as exc:
        return {"error": str(exc)}
    if not os.path.isdir(cwd):
        return {"error": f"cwd is not a directory: {cwd_param}"}

    cmd = ["git", function] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=params.get("timeout", 60),
        )
    except FileNotFoundError:
        return {"error": "git executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"git {function} timed out"}
    except OSError as exc:
        return {"error": f"git {function} failed: {exc}"}

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    max_chars = params.get("max_answer_chars", DEFAULT_MAX_CHARS)
    truncated_stdout = False
    if max_chars > 0 and len(stdout) > max_chars:
        stdout = stdout[:max_chars]
        truncated_stdout = True

    cmd_str = "git " + function + ("" if not args else " " + " ".join(_quote_arg(a) for a in args))

    parts = [f"## git {function}", ""]
    if cwd != project_root:
        parts.append(f"_cwd: `{os.path.relpath(cwd, project_root)}`_")
        parts.append("")
    parts.append(f"`{cmd_str}` (exit {result.returncode})")
    parts.append("")
    if stdout:
        parts.append(_md_fence(stdout))
    elif not stderr and result.returncode == 0:
        parts.append("_(no output)_")
    if truncated_stdout:
        parts.append("")
        parts.append(f"_stdout truncated at {max_chars} chars_")
    if stderr.strip():
        parts.append("")
        parts.append("**stderr:**")
        parts.append(_md_fence(stderr.strip()))

    md = "\n".join(parts)
    is_error = result.returncode != 0 and not stdout
    if is_error:
        return {"error": md}
    return {"__raw_text__": md}


# ---------------------------------------------------------------------------
# MCP Server (same shape as mcp-purity)
# ---------------------------------------------------------------------------

GIT_CALL_TOOL = {
    "name": "git_call",
    "description": (
        "MANDATORY for ALL read-only git operations AND for the full `git stash` "
        "workflow. NEVER invoke these through Bash(\"git ...\") — `git_call` exists "
        "specifically to replace those calls. Using Bash for an operation that "
        "this tool already supports is a VIOLATION: it wastes a permission prompt, "
        "skips the Markdown wrapping, and bypasses the safety filters that block "
        "destructive flags on dual-use subcommands (branch, tag, remote, config).\n\n"
        "ALWAYS-VIA-git_call list — NEVER run these through Bash:\n"
        "  Bash(\"git status ...\")     -> function=\"status\"\n"
        "  Bash(\"git log ...\")        -> function=\"log\"\n"
        "  Bash(\"git diff ...\")       -> function=\"diff\"\n"
        "  Bash(\"git show ...\")       -> function=\"show\"\n"
        "  Bash(\"git blame ...\")      -> function=\"blame\"\n"
        "  Bash(\"git reflog ...\")     -> function=\"reflog\"\n"
        "  Bash(\"git rev-parse ...\")  -> function=\"rev-parse\"\n"
        "  Bash(\"git rev-list ...\")   -> function=\"rev-list\"\n"
        "  Bash(\"git ls-files ...\")   -> function=\"ls-files\"\n"
        "  Bash(\"git ls-tree ...\")    -> function=\"ls-tree\"\n"
        "  Bash(\"git ls-remote ...\")  -> function=\"ls-remote\"\n"
        "  Bash(\"git grep ...\")       -> function=\"grep\"\n"
        "  Bash(\"git describe ...\")   -> function=\"describe\"\n"
        "  Bash(\"git merge-base ...\") -> function=\"merge-base\"\n"
        "  Bash(\"git branch -l/-a/--contains/--merged ...\") -> function=\"branch\"\n"
        "  Bash(\"git tag -l/--contains/--merged ...\")       -> function=\"tag\"\n"
        "  Bash(\"git remote / git remote show / get-url\")  -> function=\"remote\"\n"
        "  Bash(\"git stash ...\") (ANY subcommand)           -> function=\"stash\"\n"
        "  Bash(\"git config --list/--get ...\")              -> function=\"config\"\n"
        "  Bash(\"git fetch --dry-run\")                      -> function=\"fetch\"\n"
        "  Bash(\"git apply --check ...\")                    -> function=\"apply\"\n\n"
        "Use Bash ONLY for the mutating ops this tool does NOT expose (commit, "
        "add, push, reset, checkout, merge, rebase, branch -d/-m, tag -a/-d, "
        "remote add/set-url, config <name> <value>, fetch without --dry-run, "
        "apply without --check). Everything else MUST go through git_call.\n\n"
        "Params: args (CLI args list), cwd (sub-repo, default project root), "
        "max_answer_chars (default 100000), timeout (default 60s). Markdown output.\n\n"
        "Example: function=\"log\", params={\"args\":[\"--oneline\",\"-20\"]}\n"
        "Call without 'function' for full allowlist."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {"type": "string", "description": "Git subcommand."},
            "params":   {"type": "object", "description": "See main description."},
        },
    },
}


class McpServer:
    def __init__(self, project_root: str, strict: bool = False):
        self.project_root = os.path.realpath(project_root)
        self.strict = strict

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
                "serverInfo": {"name": "mcp-git", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [GIT_CALL_TOOL]})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name != "git_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
        result = handle_git_call(arguments, self.project_root, self.strict)
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
    if "--list" in sys.argv:
        print("mcp-git — allowed read-only subcommands:\n")
        for name in sorted(SAFE_SUBCOMMANDS):
            desc = SUBCOMMAND_DESCRIPTIONS.get(name, "")
            print(f"  {name:18s} {desc}")
        print("\nFiltered (arg-level checks):")
        for name in sorted(FILTERED_SUBCOMMANDS.keys()):
            desc = SUBCOMMAND_DESCRIPTIONS.get(name, "")
            print(f"  {name:18s} {desc}")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="MCP-Git: Read-only git operations MCP server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--strict", action="store_true", help="Reject paths outside project root")
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
        print(f"Error: project root is not a directory: {args.project_root}", file=sys.stderr)
        sys.exit(1)

    server = McpServer(args.project_root, strict=args.strict)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
