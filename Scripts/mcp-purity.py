#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""MCP-Purity: Pure Python file operations MCP server.

Single-tool dispatcher pattern: exposes one MCP tool (purity_call) that routes
to internal handler functions via the 'function' parameter.

Requires only Python 3.9+ stdlib modules.

Usage:
  python3 mcp-purity.py --project-root <path>
                        [--strict]
                        [--debug]
                        [--log-file <path>]   # implies --debug

  --project-root  Required. Sandbox root for all file operations.
  --strict        Reject any path (search/edit/glob/ls) that escapes
                  --project-root, even if the caller passes an absolute path.
                  Without --strict, absolute paths outside the root are allowed.
"""

import argparse
import asyncio
import fnmatch
import glob as glob_mod
import json
import logging
import os
import pathlib
import re
import shutil
import sys
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mcp-purity")


# ---------------------------------------------------------------------------
# Sandbox utility
# ---------------------------------------------------------------------------

def safe_path(project_root: str, relative_path: str, strict: bool = False) -> str:
    """Resolve *relative_path* under *project_root* and verify it stays inside.

    When *strict* is False (default), absolute paths are allowed as-is.
    """
    if os.path.isabs(relative_path) and not strict:
        return os.path.realpath(relative_path)
    resolved = os.path.realpath(os.path.join(project_root, relative_path))
    if not resolved.startswith(project_root + os.sep) and resolved != project_root:
        raise ValueError(f"Path escapes project root: {relative_path}")
    return resolved


# ---------------------------------------------------------------------------
# Parameter aliases — models often use short/alternative names
# ---------------------------------------------------------------------------

# Global aliases — applied regardless of which function is being called.
# Only put aliases here when the canonical target is unambiguous across all
# handlers. Context-dependent aliases (e.g. new_content meaning either
# `content` or `repl` depending on the function) belong in
# PARAM_ALIASES_BY_FUNC below.
PARAM_ALIASES = {
    "path": "relative_path",
    "file_path": "relative_path",
    "root": "relative_path",
    "pattern": "substring_pattern",
    "line_start": "start_line",
    "line_end": "end_line",
    # replace_content aliases
    "search": "needle",
    "find": "needle",
    "old_string": "needle",
    "old": "needle",
    "replacement": "repl",
    "replace": "repl",
    "replace_with": "repl",
    "new_string": "repl",
    "new": "repl",
    # search_for_pattern aliases
    "glob": "paths_include_glob",
    "include": "paths_include_glob",
    "exclude": "paths_exclude_glob",
    # general aliases
    "file": "relative_path",
    "start": "start_line",
    "end": "end_line",
    # semantic / LSP aliases (merged from mcp-clangd; [inspector M1]).
    # NOTE: path/file/file_path already map to relative_path above, which is the
    # canonical path key for BOTH the file layer and the semantic handlers.
    # The CUDA-only init aliases (arch/gpu_arch/cuda_sdk/sdk_path) are dropped on
    # purpose: lazy init has no init params [inspector L-new-1].
    "filepath": "relative_path",
    "symbol": "symbol_name",
    "name": "symbol_name",
    "col": "character",
    "column": "character",
    "char": "character",
    "ctx_lines": "context_lines",
    "context": "context_lines",
    "max": "max_results",
    "count": "max_results",
    "max_refs": "max_references",
    "depth": "call_hierarchy_depth",
}

# Function-specific aliases — applied BEFORE the global PARAM_ALIASES.
# Use this when the same caller-supplied key needs to map to different
# canonical parameter names depending on the handler. Keys here override
# anything in PARAM_ALIASES for the listed function.
PARAM_ALIASES_BY_FUNC: Dict[str, Dict[str, str]] = {
    "replace_content": {
        "old_content": "needle",
        "new_content": "repl",
    },
    "create_text_file": {
        "new_content": "content",
    },
    "replace_lines": {
        "new_content": "content",
    },
    "insert_at_line": {
        "new_content": "content",
    },
}

# Function-name aliases — handler registry exposes several aliases (ls, glob,
# grep, search) for the canonical handler names. Resolve to the canonical
# name before looking up PARAM_ALIASES_BY_FUNC / HANDLER_ACCEPTED_PARAMS.
FUNCTION_ALIASES = {
    "ls": "list_dir",
    "glob": "find_file",
    "grep": "search_for_pattern",
    "search": "search_for_pattern",
}


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


def _canonical_function(function: str) -> str:
    return FUNCTION_ALIASES.get(function, function)


def _resolve_aliases(params: Any, function: Optional[str] = None) -> dict:
    """Return a new dict with aliased parameter names resolved to canonical names.

    Function-specific aliases (PARAM_ALIASES_BY_FUNC) take precedence over the
    global PARAM_ALIASES. ``function`` should be the canonical handler name
    (use ``_canonical_function`` if the caller may pass an alias).

    Accepts a dict natively. If a JSON-encoded string is passed (clients
    sometimes serialize ``params`` as a string), it is decoded first. Anything
    else, or an unparseable string, raises ``ValueError`` so the dispatch
    layer can return a clean error instead of crashing the server.
    """
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

    func_aliases = PARAM_ALIASES_BY_FUNC.get(function or "", {})
    resolved: dict = {}
    for key, value in params.items():
        canonical = func_aliases.get(key) or PARAM_ALIASES.get(key, key)
        # Bug #3: last-wins precedence (unified with the clangd/cuda resolvers).
        # A later explicit key overrides an earlier alias that targets the same
        # canonical name.
        resolved[canonical] = value
    return resolved


# ---------------------------------------------------------------------------
# Gitignore helpers (simplified)
# ---------------------------------------------------------------------------

def _parse_gitignore(gitignore_path: str) -> List[str]:
    """Return a list of fnmatch patterns from a .gitignore file."""
    patterns: List[str] = []
    if not os.path.isfile(gitignore_path):
        return patterns
    with open(gitignore_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line.rstrip("/"))
    return patterns


def _is_ignored(rel: str, patterns: List[str]) -> bool:
    """Check if a relative path matches any gitignore pattern."""
    name = os.path.basename(rel)
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# File handlers
# ---------------------------------------------------------------------------

def handle_read_file(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path")
    if not rel:
        raise ValueError("Missing required parameter: relative_path")
    path = safe_path(project_root, rel, strict)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {rel}")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    start = params.get("start_line", 1)  # 1-based
    end = params.get("end_line")          # 1-based, inclusive
    idx_start = start - 1
    if end is not None:
        selected = lines[idx_start:end]
    else:
        selected = lines[idx_start:]
        end = len(lines)

    content = "".join(selected)

    max_chars = params.get("max_answer_chars", -1)
    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    header = f"[{rel}] lines {start}-{start + len(selected) - 1} of {len(lines)}"
    if truncated:
        header += " (truncated)"
    return {"__raw_text__": f"{header}\n{content}"}


def handle_create_text_file(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path")
    if not rel:
        raise ValueError("Missing required parameter: relative_path")
    content = params.get("content")
    if content is None:
        raise ValueError("Missing required parameter: content")

    path = safe_path(project_root, rel, strict)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

    nbytes = len(content.encode("utf-8"))
    return {"__raw_text__": f"Created {rel} ({nbytes} bytes)"}


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    val = float(size)
    for unit in ("K", "M", "G", "T"):
        val /= 1024
        if val < 1024:
            return f"{val:.1f}{unit}"
    return f"{val:.1f}P"


def _format_mtime(mtime: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def handle_list_dir(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path", ".")
    recursive = _bool_param(params.get("recursive", False))
    skip_ignored = _bool_param(params.get("skip_ignored_files", False))
    long_format = _bool_param(params.get("long", False))
    show_hidden = _bool_param(params.get("show_hidden", False)) or _bool_param(params.get("all", False)) or _bool_param(params.get("hidden", False))
    glob_pattern = params.get("glob", None) or params.get("paths_include_glob", None) or params.get("filter", None)
    grep_pattern = params.get("grep", None) or params.get("grep_pattern", None)
    head_limit = params.get("head_limit", 0)
    offset = params.get("offset", 0)

    path = safe_path(project_root, rel, strict)
    if not os.path.isdir(path):
        return {"text": f"(directory does not exist: {rel})", "count": 0}

    if grep_pattern:
        grep_re = re.compile(grep_pattern, re.IGNORECASE)
    else:
        grep_re = None

    ignore_patterns: List[str] = []
    if skip_ignored:
        ignore_patterns = _parse_gitignore(os.path.join(project_root, ".gitignore"))

    def _accept_name(name: str, is_dir: bool) -> bool:
        if not show_hidden and name.startswith("."):
            return False
        if glob_pattern and not is_dir:
            if not fnmatch.fnmatch(name, glob_pattern):
                return False
        return True

    raw: List[tuple] = []  # (display_rel, is_dir, abs_path)
    if recursive:
        for dirpath, dirnames, filenames in os.walk(path):
            if skip_ignored:
                dirnames[:] = [
                    d for d in dirnames
                    if not _is_ignored(d, ignore_patterns) and d != ".git"
                ]
            else:
                dirnames[:] = [d for d in dirnames if d != ".git"]
            if not show_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(dirnames):
                if not _accept_name(name, True):
                    continue
                entry_rel = os.path.relpath(os.path.join(dirpath, name), project_root)
                if skip_ignored and _is_ignored(entry_rel, ignore_patterns):
                    continue
                raw.append((entry_rel, True, os.path.join(dirpath, name)))
            for name in sorted(filenames):
                if not _accept_name(name, False):
                    continue
                entry_rel = os.path.relpath(os.path.join(dirpath, name), project_root)
                if skip_ignored and _is_ignored(entry_rel, ignore_patterns):
                    continue
                raw.append((entry_rel, False, os.path.join(dirpath, name)))
    else:
        for name in sorted(os.listdir(path)):
            if skip_ignored and (_is_ignored(name, ignore_patterns) or name == ".git"):
                continue
            if not _accept_name(name, os.path.isdir(os.path.join(path, name))):
                continue
            full = os.path.join(path, name)
            entry_rel = os.path.relpath(full, project_root)
            raw.append((entry_rel, os.path.isdir(full), full))

    if long_format:
        lines: List[str] = []
        for entry_rel, is_dir, full in raw:
            try:
                st = os.stat(full)
                size_str = "-" if is_dir else _format_size(st.st_size)
                mtime_str = _format_mtime(st.st_mtime)
            except OSError:
                size_str = "?"
                mtime_str = "?"
            display = entry_rel + ("/" if is_dir else "")
            lines.append(f"{size_str:>7}  {mtime_str}  {display}")
    else:
        lines = [entry_rel + ("/" if is_dir else "") for entry_rel, is_dir, _ in raw]

    if grep_re:
        lines = [l for l in lines if grep_re.search(l)]

    total = len(lines)
    if offset:
        lines = lines[offset:]
    if head_limit > 0:
        lines = lines[:head_limit]

    listing = "\n".join(lines)
    max_chars = params.get("max_answer_chars", -1)
    truncated = False
    if max_chars > 0 and len(listing) > max_chars:
        listing = listing[:max_chars]
        truncated = True

    header = f"[{rel}] {len(raw)} entries"
    if grep_re:
        header += f", {total} matched"
    if offset or head_limit > 0:
        header += f" (showing {offset+1}-{offset+len(lines)} of {total})"
    if truncated:
        header += " (truncated)"
    return {"__raw_text__": f"{header}\n{listing}"}


def handle_find_file(params: dict, project_root: str, strict: bool = False) -> dict:
    file_mask = params.get("file_mask") or params.get("pattern") or params.get("substring_pattern")
    if not file_mask:
        raise ValueError("Missing required parameter: file_mask")
    rel = params.get("relative_path", ".")
    head_limit = params.get("head_limit", 0)  # 0 = unlimited
    offset = params.get("offset", 0)
    path = safe_path(project_root, rel, strict)
    if not os.path.isdir(path):
        return {"text": f"(directory does not exist: {rel})", "count": 0}

    matches: List[str] = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            if fnmatch.fnmatch(name, file_mask):
                match_rel = os.path.relpath(os.path.join(dirpath, name), project_root)
                matches.append(match_rel)

    total = len(matches)
    if offset:
        matches = matches[offset:]
    truncated = False
    if head_limit > 0 and len(matches) > head_limit:
        matches = matches[:head_limit]
        truncated = True

    header = f"Found {total} file(s) matching '{file_mask}'"
    if truncated or offset:
        header += f" (showing {offset+1}-{offset+len(matches)} of {total})"
    return {"__raw_text__": f"{header}\n" + "\n".join(matches) if matches else header}


def handle_replace_content(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path")
    if not rel:
        raise ValueError("Missing required parameter: relative_path")
    needle = params.get("needle")
    if needle is None:
        raise ValueError("Missing required parameter: needle")
    repl = params.get("repl")
    if repl is None:
        raise ValueError("Missing required parameter: repl")
    mode = params.get("mode", "literal")
    if mode not in ("literal", "regex"):
        raise ValueError("Parameter 'mode' must be 'literal' or 'regex'")
    allow_multiple = _bool_param(params.get("allow_multiple_occurrences", False))

    path = safe_path(project_root, rel, strict)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {rel}")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    if mode == "literal":
        count = content.count(needle)
        if count == 0:
            raise ValueError(f"Needle not found in {rel}")
        if count > 1 and not allow_multiple:
            raise ValueError(
                f"Multiple occurrences ({count}) found in {rel}. "
                "Set allow_multiple_occurrences=true to replace all."
            )
        new_content = content.replace(needle, repl)
    else:
        needle = needle.replace("\\|", "|")
        matches = list(re.finditer(needle, content))
        if not matches:
            raise ValueError(f"Pattern not found in {rel}")
        if len(matches) > 1 and not allow_multiple:
            raise ValueError(
                f"Multiple matches ({len(matches)}) found in {rel}. "
                "Set allow_multiple_occurrences=true to replace all."
            )
        new_content = re.sub(needle, repl, content)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_content)

    n = count if mode == "literal" else len(matches)
    return {"__raw_text__": f"Replaced {n} occurrence(s) in {rel}"}


def handle_delete_lines(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path")
    if not rel:
        raise ValueError("Missing required parameter: relative_path")
    start_line = params.get("start_line")  # 1-based
    end_line = params.get("end_line")      # 1-based, inclusive
    single = params.get("line")            # shorthand: line=N means start_line=N, end_line=N
    if single is not None and start_line is None:
        start_line = single
    if single is not None and end_line is None:
        end_line = single
    if start_line is None or end_line is None:
        raise ValueError("Missing required parameters: start_line, end_line (or line)")

    path = safe_path(project_root, rel, strict)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {rel}")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        raise ValueError(
            f"Invalid line range [{start_line}, {end_line}] for file with {len(lines)} lines"
        )

    idx_start = start_line - 1
    idx_end = end_line  # end_line is 1-based inclusive, so slice to end_line
    deleted = lines[idx_start:idx_end]
    new_lines = lines[:idx_start] + lines[idx_end:]

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    return {"__raw_text__": f"Deleted lines {start_line}-{end_line} from {rel}"}


def handle_replace_lines(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path")
    if not rel:
        raise ValueError("Missing required parameter: relative_path")
    start_line = params.get("start_line")  # 1-based
    end_line = params.get("end_line")      # 1-based, inclusive
    single = params.get("line")            # shorthand: line=N means start_line=N, end_line=N
    if single is not None and start_line is None:
        start_line = single
    if single is not None and end_line is None:
        end_line = single
    content = params.get("content")
    if start_line is None or end_line is None:
        raise ValueError("Missing required parameters: start_line, end_line (or line)")
    if content is None:
        raise ValueError("Missing required parameter: content")

    path = safe_path(project_root, rel, strict)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {rel}")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        raise ValueError(
            f"Invalid line range [{start_line}, {end_line}] for file with {len(lines)} lines"
        )

    idx_start = start_line - 1
    idx_end = end_line
    if not content.endswith("\n"):
        content += "\n"
    new_lines = lines[:idx_start] + [content] + lines[idx_end:]

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    return {"__raw_text__": f"Replaced lines {start_line}-{end_line} in {rel}"}


def handle_insert_at_line(params: dict, project_root: str, strict: bool = False) -> dict:
    rel = params.get("relative_path")
    if not rel:
        raise ValueError("Missing required parameter: relative_path")
    line_num = params.get("line")  # 1-based: insert before this line
    content = params.get("content")
    if line_num is None:
        raise ValueError("Missing required parameter: line")
    if content is None:
        raise ValueError("Missing required parameter: content")

    path = safe_path(project_root, rel, strict)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {rel}")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    idx = line_num - 1
    if idx < 0 or idx > len(lines):
        raise ValueError(
            f"Invalid line number {line_num} for file with {len(lines)} lines"
        )

    if not content.endswith("\n"):
        content += "\n"
    lines.insert(idx, content)

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)

    return {"__raw_text__": f"Inserted at line {line_num} in {rel}"}


def handle_search_for_pattern(params: dict, project_root: str, strict: bool = False) -> dict:
    pattern_str = params.get("substring_pattern")
    if not pattern_str:
        raise ValueError("Missing required parameter: substring_pattern")

    ctx_before = params.get("context_lines_before", 0) or 0
    ctx_after = params.get("context_lines_after", 0) or 0
    include_glob = params.get("paths_include_glob", "")
    exclude_glob = params.get("paths_exclude_glob", "")
    search_rel = params.get("relative_path", "")
    max_chars = params.get("max_answer_chars", -1)
    head_limit = params.get("head_limit", 0)  # 0 = unlimited
    offset = params.get("offset", 0)

    search_root = safe_path(project_root, search_rel, strict) if search_rel else project_root
    search_single_file = os.path.isfile(search_root)

    # Smart default: switch to "content" mode when context-line params are set
    # or when the search target is a single file — otherwise the user-passed
    # context params are silently ignored and a single-file search would only
    # answer "yes/no this file matches", which is rarely the actual intent.
    explicit_mode = params.get("output_mode")
    if explicit_mode:
        output_mode = explicit_mode
    else:
        output_mode = "content"
    if output_mode not in ("files_with_matches", "content", "count"):
        raise ValueError("Parameter 'output_mode' must be 'files_with_matches', 'content', or 'count'")

    max_file_size = params.get("max_file_size", 10 * 1024 * 1024)  # default 10 MB
    skip_ignored = _bool_param(params.get("skip_ignored_files", True))

    # LLMs frequently escape | as \| which turns alternation into a literal pipe
    pattern_str = pattern_str.replace(r"\|", "|")

    try:
        pattern = re.compile(pattern_str)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}")

    ignore_patterns: List[str] = []
    if skip_ignored:
        ignore_patterns = _parse_gitignore(os.path.join(project_root, ".gitignore"))

    # Collect all matches first (for count and files_with_matches we need file-level info)
    file_matches: Dict[str, int] = {}  # file_rel -> match count
    content_entries: List[str] = []
    total_match_count = 0
    total_chars = 0
    truncated = False

    if search_single_file:
        file_iter = [(os.path.dirname(search_root), [], [os.path.basename(search_root)])]
    else:
        file_iter = os.walk(search_root)

    for dirpath, dirnames, filenames in file_iter:
        if not search_single_file:
            dirnames[:] = [d for d in dirnames if d != ".git"]
            if skip_ignored:
                dirnames[:] = [d for d in dirnames if not _is_ignored(d, ignore_patterns)]
        for name in filenames:
            if not search_single_file and skip_ignored and _is_ignored(name, ignore_patterns):
                continue

            full = os.path.join(dirpath, name)
            file_rel = os.path.relpath(full, project_root)

            if include_glob and not fnmatch.fnmatch(file_rel, include_glob):
                continue
            if exclude_glob and fnmatch.fnmatch(file_rel, exclude_glob):
                continue

            # Skip files exceeding size limit
            try:
                if os.path.getsize(full) > max_file_size:
                    continue
            except OSError:
                continue

            # Skip binary files (check first 8KB for null bytes)
            try:
                with open(full, "rb") as fb:
                    chunk = fb.read(8192)
                if b"\x00" in chunk:
                    continue
            except (OSError, PermissionError):
                continue

            try:
                fh = open(full, "r", encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            file_hit_count = 0
            need_context = output_mode == "content" and (ctx_before or ctx_after)

            if need_context:
                # Need surrounding lines — read all lines for context access
                try:
                    lines = fh.readlines()
                finally:
                    fh.close()
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        file_hit_count += 1
                        total_match_count += 1
                        line_num = i + 1
                        start = max(0, i - ctx_before)
                        end = min(len(lines), i + ctx_after + 1)
                        ctx = "".join(lines[start:end]).rstrip("\n")
                        entry = f"{file_rel}:{line_num}:\n{ctx}"
                        if max_chars > 0 and total_chars + len(entry) + 1 > max_chars:
                            truncated = True
                            break
                        content_entries.append(entry)
                        total_chars += len(entry) + 1
                        if head_limit > 0 and len(content_entries) >= offset + head_limit:
                            truncated = True
                            break
            else:
                # Stream line-by-line — no need to hold entire file in memory
                try:
                    for i, line in enumerate(fh):
                        if pattern.search(line):
                            file_hit_count += 1
                            total_match_count += 1
                            if output_mode == "content":
                                entry = f"{file_rel}:{i + 1}: {line.rstrip(chr(10))}"
                                if max_chars > 0 and total_chars + len(entry) + 1 > max_chars:
                                    truncated = True
                                    break
                                content_entries.append(entry)
                                total_chars += len(entry) + 1
                                if head_limit > 0 and len(content_entries) >= offset + head_limit:
                                    truncated = True
                                    break
                finally:
                    fh.close()

            if file_hit_count > 0:
                file_matches[file_rel] = file_hit_count

            if truncated:
                break

            # For non-content modes, check head_limit on file count
            if output_mode != "content" and head_limit > 0 and len(file_matches) >= offset + head_limit:
                truncated = True
                break

        if truncated:
            break

    # Format output based on mode
    if output_mode == "count":
        entries = [f"{f}: {c}" for f, c in file_matches.items()]
        if offset:
            entries = entries[offset:]
        if head_limit > 0:
            entries = entries[:head_limit]
        header = f"{total_match_count} match(es) in {len(file_matches)} file(s)"
        if truncated:
            header += " (truncated)"
        return {"__raw_text__": f"{header}\n" + "\n".join(entries) if entries else header}

    elif output_mode == "files_with_matches":
        entries = list(file_matches.keys())
        if offset:
            entries = entries[offset:]
        if head_limit > 0:
            entries = entries[:head_limit]
        header = f"{len(file_matches)} file(s) with matches"
        if truncated:
            header += " (truncated)"
        return {"__raw_text__": f"{header}\n" + "\n".join(entries) if entries else header}

    else:  # content
        entries = content_entries
        if offset:
            entries = entries[offset:]
        if head_limit > 0:
            entries = entries[:head_limit]
        header = f"{total_match_count} match(es)"
        if truncated:
            header += " (truncated)"
        return {"__raw_text__": f"{header}\n" + "\n".join(entries) if entries else header}


# ===========================================================================
# LSP CORE  (Phase 0: folded from mcp-clangd.py / mcp-cuda.py)
# ===========================================================================
#
# This layer adds compiler-accurate semantic code navigation (clangd LSP) to
# the pure-Python file layer above. The file layer answers instantly with no
# LSP; this layer is spun up lazily, only when a semantic function is called
# for a filetype that has sources in the project.
#
# Layering:  dispatcher -> sync file layer (above)  +  async LSP backend layer
#            (below, behind the abstract LspBackend interface) + filetype
#            routing (_route_filetype / _ensure_backend).


# --- LSP framing (Content-Length over stdio) ------------------------------

def encode_lsp_message(body: dict) -> bytes:
    """Encode a dict as an LSP message with Content-Length framing."""
    text = json.dumps(body)
    encoded = text.encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n"
    return header.encode("ascii") + encoded


async def read_lsp_message(reader: "asyncio.StreamReader") -> Optional[dict]:
    """Read one LSP message from reader. Returns None on EOF."""
    content_length = 0
    while True:
        try:
            line_bytes = await reader.readline()
        except Exception:
            return None
        if not line_bytes:
            return None
        line = line_bytes.decode("ascii", errors="replace").strip()
        if not line:
            if content_length > 0:
                break
            continue
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass

    if content_length == 0:
        return None

    try:
        body_bytes = await reader.readexactly(content_length)
    except (asyncio.IncompleteReadError, Exception):
        return None

    try:
        return json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return None


# --- LSP utility helpers ---------------------------------------------------

def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an absolute path."""
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def path_to_uri(path: str) -> str:
    """Convert an absolute path to a file:// URI."""
    return pathlib.Path(path).absolute().as_uri()


SYMBOL_KIND_MAP = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package",
    5: "Class", 6: "Method", 7: "Property", 8: "Field",
    9: "Constructor", 10: "Enum", 11: "Interface", 12: "Function",
    13: "Variable", 14: "Constant", 15: "String", 16: "Number",
    17: "Boolean", 18: "Array", 19: "Object", 20: "Key",
    21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}

DEFINITION_KINDS = {
    "Class", "Struct", "Function", "Method", "Enum",
    "Interface", "Variable", "Field", "Constructor",
}


def symbol_kind_name(kind: int) -> str:
    return SYMBOL_KIND_MAP.get(kind, "Unknown")


def extract_code_range(file_path: str, lsp_range: dict) -> str:
    """Extract the exact text span described by an LSP range from a file."""
    start = lsp_range.get("start", {})
    end = lsp_range.get("end", {})
    start_line = start.get("line", 0)
    start_char = start.get("character", 0)
    end_line = end.get("line", 0)
    end_char = end.get("character", 0)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if start_line == end_line:
            return lines[start_line][start_char:end_char].rstrip("\n") if start_line < len(lines) else ""
        parts = [lines[start_line][start_char:]] if start_line < len(lines) else []
        for i in range(start_line + 1, end_line):
            if i < len(lines):
                parts.append(lines[i])
        if end_line < len(lines):
            parts.append(lines[end_line][:end_char])
        return "".join(parts).rstrip("\n")
    except Exception:
        return ""


def extract_surrounding_code(file_path: str, line: int, ctx_lines: int = 5) -> str:
    """Extract ctx_lines lines of context around the given (0-based) line."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        start = max(0, line - ctx_lines)
        end = min(len(lines), line + ctx_lines + 1)
        out = []
        for i in range(start, end):
            marker = ">>> " if i == line else "    "
            out.append(f"{marker}{lines[i].rstrip()}")
        return "\n".join(out)
    except Exception:
        return ""


def _relative_path(uri: str, project_root: str) -> str:
    """Return the path relative to project_root, or the absolute path if outside."""
    abs_path = pathlib.Path(uri_to_path(uri))
    try:
        return str(abs_path.relative_to(project_root))
    except ValueError:
        return str(abs_path)


def _detect_language(file_path: str) -> str:
    """Map a source path to its LSP languageId (CUDA-superset of clangd's)."""
    suffix = pathlib.Path(file_path).suffix.lower()
    if suffix in (".cu", ".cuh"):
        return "cuda"
    if suffix == ".c":
        return "c"
    return "cpp"


# ===========================================================================
# CUDA CONFIG  (language-config of the clangd-family backend)
# ===========================================================================

def _find_cuda_sdk(explicit_path: Optional[str] = None,
                   project_root: Optional[str] = None) -> Optional[str]:
    """Discover the CUDA SDK root directory (or None)."""
    if explicit_path:
        p = pathlib.Path(explicit_path)
        if (p / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (explicit): {p}")
            return str(p)
        log.debug(f"CUDA SDK explicit path invalid (no nvcc): {p}")

    env_path = os.environ.get("CUDA_PATH")
    if env_path:
        p = pathlib.Path(env_path)
        if (p / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (CUDA_PATH): {p}")
            return str(p)

    env_home = os.environ.get("CUDA_HOME")
    if env_home:
        p = pathlib.Path(env_home)
        if (p / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (CUDA_HOME): {p}")
            return str(p)

    nvcc = shutil.which("nvcc")
    if nvcc:
        sdk_root = pathlib.Path(nvcc).resolve().parent.parent
        if (sdk_root / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (PATH nvcc): {sdk_root}")
            return str(sdk_root)

    cuda_roots = sorted(glob_mod.glob("/usr/local/cuda-*"),
                        key=lambda p: p, reverse=True)
    best = None
    for root in cuda_roots:
        if (pathlib.Path(root) / "bin" / "nvcc").exists():
            if re.search(r"cuda-12", root):
                log.debug(f"CUDA SDK (glob, prefer 12.x): {root}")
                return root
            if best is None:
                best = root
    if best:
        log.debug(f"CUDA SDK (glob): {best}")
        return best

    default = pathlib.Path("/usr/local/cuda")
    if default.exists() and (default / "bin" / "nvcc").exists():
        log.debug(f"CUDA SDK (default symlink): {default}")
        return str(default.resolve())

    if project_root:
        cmake_cache = pathlib.Path(project_root) / "build" / "CMakeCache.txt"
        if cmake_cache.exists():
            try:
                text = cmake_cache.read_text(encoding="utf-8")
                m = re.search(r"CMAKE_CUDA_COMPILER[^=]*=(.+)", text)
                if m:
                    nvcc_path = pathlib.Path(m.group(1).strip())
                    sdk_root = nvcc_path.parent.parent
                    if (sdk_root / "bin" / "nvcc").exists():
                        log.debug(f"CUDA SDK (CMakeCache): {sdk_root}")
                        return str(sdk_root)
            except Exception:
                pass

    log.debug("CUDA SDK: not found")
    return None


def _detect_cuda_arch(project_root: Optional[str] = None,
                      compile_commands: Optional[List[dict]] = None) -> Optional[str]:
    """Auto-detect GPU architecture. Returns e.g. 'sm_86' or None."""
    if project_root:
        cmake_cache = pathlib.Path(project_root) / "build" / "CMakeCache.txt"
        if cmake_cache.exists():
            try:
                text = cmake_cache.read_text(encoding="utf-8")
                m = re.search(r"CMAKE_CUDA_ARCHITECTURES[^=]*=\s*(.+)", text)
                if m:
                    arch = m.group(1).strip().split(";")[0].split(",")[0]
                    if arch.isdigit():
                        log.debug(f"CUDA arch (CMakeCache): sm_{arch}")
                        return f"sm_{arch}"
            except Exception:
                pass

    if compile_commands:
        for entry in compile_commands:
            cmd = entry.get("command", "") or " ".join(entry.get("arguments", []))
            m = re.search(r"compute_(\d+)", cmd)
            if m:
                arch = m.group(1)
                log.debug(f"CUDA arch (compile_commands): sm_{arch}")
                return f"sm_{arch}"

    return None


NVCC_STRIP_FLAGS = {
    "-forward-unknown-to-host-compiler",
    "-Wno-deprecated-gpu-targets",
    "-lineinfo",
    "--expt-relaxed-constexpr",
    "--expt-extended-lambda",
}

NVCC_STRIP_PREFIXES = (
    "--generate-code=",
    "-diag-suppress=",
    "--options-file",
    "-gencode",
    "--gpu-code=",
    "--gpu-architecture=",
    "-rdc=",
    "-dlink",
    "--device-link",
)


def _expand_rsp_file(rsp_path: str, base_dir: str) -> List[str]:
    """Expand an nvcc response file (--options-file or @file) into flags."""
    p = pathlib.Path(rsp_path)
    if not p.is_absolute():
        p = pathlib.Path(base_dir) / p
    try:
        text = p.read_text(encoding="utf-8")
        return text.split()
    except Exception:
        log.debug(f"Cannot read RSP file: {p}")
        return []


def _translate_compile_commands(entries: List[dict], cuda_path: str,
                                cuda_arch: str, base_dir: str) -> List[dict]:
    """Translate nvcc compile_commands.json entries to clangd-compatible ones."""
    cuda_extensions = {".cu", ".cuh"}
    cuda_include = os.path.join(cuda_path, "targets", "x86_64-linux", "include")
    if not os.path.isdir(cuda_include):
        cuda_include = os.path.join(cuda_path, "include")

    translated = []
    for entry in entries:
        file_path = entry.get("file", "")
        suffix = pathlib.Path(file_path).suffix.lower()
        if suffix not in cuda_extensions:
            continue

        if "arguments" in entry:
            args = list(entry["arguments"])
        elif "command" in entry:
            import shlex
            args = shlex.split(entry["command"])
        else:
            continue

        new_args = ["clang++"]
        skip_next = False
        for i, arg in enumerate(args[1:], 1):
            if skip_next:
                skip_next = False
                continue

            if arg in NVCC_STRIP_FLAGS:
                continue
            if any(arg.startswith(p) for p in NVCC_STRIP_PREFIXES):
                continue

            if arg == "--options-file" and i + 1 < len(args):
                expanded = _expand_rsp_file(args[i + 1], entry.get("directory", base_dir))
                new_args.extend(expanded)
                skip_next = True
                continue

            if arg.startswith("@"):
                expanded = _expand_rsp_file(arg[1:], entry.get("directory", base_dir))
                new_args.extend(expanded)
                continue

            if arg == "-x" and i + 1 < len(args) and args[i + 1] == "cu":
                skip_next = True
                continue

            if arg in ("-arch", "-code", "--gpu-architecture", "--gpu-code") and i + 1 < len(args):
                skip_next = True
                continue

            new_args.append(arg)

        new_args.extend([
            "-x", "cuda",
            f"--cuda-path={cuda_path}",
            f"--cuda-gpu-arch={cuda_arch}",
            "-isystem", cuda_include,
            "-D__CUDA_ARCH__=860",
            "--no-cuda-version-check",
        ])

        translated.append({
            "directory": entry.get("directory", base_dir),
            "file": file_path,
            "arguments": new_args,
        })

    return translated


def _prepare_compile_commands(project_root: str, cuda_path: str,
                              cuda_arch: str,
                              compile_commands_dir: Optional[str] = None) -> str:
    """Translate CUDA compile_commands into a cache dir; return the cache dir."""
    cache_dir = os.path.join(project_root, ".cache", "mcp-cuda")
    os.makedirs(cache_dir, exist_ok=True)

    search_dirs = []
    if compile_commands_dir:
        search_dirs.append(compile_commands_dir)
    search_dirs.extend([
        os.path.join(project_root, "build"),
        project_root,
    ])

    original = None
    original_entries = []
    for d in search_dirs:
        cc_path = os.path.join(d, "compile_commands.json")
        if os.path.isfile(cc_path):
            try:
                with open(cc_path, "r", encoding="utf-8") as f:
                    original_entries = json.load(f)
                original = cc_path
                log.debug(f"Found compile_commands.json: {cc_path}")
                break
            except Exception as e:
                log.debug(f"Cannot read {cc_path}: {e}")

    if original:
        translated = _translate_compile_commands(
            original_entries, cuda_path, cuda_arch,
            os.path.dirname(original)
        )
    else:
        log.debug("No compile_commands.json found - generating minimal entries from .cu files")
        translated = _generate_minimal_compile_commands(project_root, cuda_path, cuda_arch)

    out_path = os.path.join(cache_dir, "compile_commands.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(translated, f, indent=2)
    log.debug(f"Wrote {len(translated)} CUDA entries to {out_path}")
    return cache_dir


def _generate_minimal_compile_commands(project_root: str, cuda_path: str,
                                       cuda_arch: str) -> List[dict]:
    """Generate compile_commands.json entries for .cu files when none exists."""
    cuda_include = os.path.join(cuda_path, "targets", "x86_64-linux", "include")
    if not os.path.isdir(cuda_include):
        cuda_include = os.path.join(cuda_path, "include")

    entries = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "out", "dist")]
        for fname in files:
            if pathlib.Path(fname).suffix.lower() == ".cu":
                full_path = os.path.join(root, fname)
                entries.append({
                    "directory": project_root,
                    "file": full_path,
                    "arguments": [
                        "clang++",
                        "-x", "cuda",
                        f"--cuda-path={cuda_path}",
                        f"--cuda-gpu-arch={cuda_arch}",
                        "-isystem", cuda_include,
                        "-D__CUDA_ARCH__=860",
                        "--no-cuda-version-check",
                        "-std=c++17",
                        f"-I{project_root}",
                        "-c", full_path,
                    ],
                })
    return entries


def _has_cuda_sources(project_root: str,
                      cc_entries: Optional[List[dict]] = None) -> bool:
    """True if the project actually contains CUDA translation units.

    A bare CUDA SDK on the host is NOT sufficient - there must be .cu/.cuh
    sources (or compile_commands entries referencing them) for a CUDA backend
    to have anything to index.
    """
    if cc_entries:
        for entry in cc_entries:
            f = str(entry.get("file", "")).lower()
            if f.endswith(".cu") or f.endswith(".cuh"):
                return True

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "out", "dist")]
        for fname in files:
            if pathlib.Path(fname).suffix.lower() in (".cu", ".cuh"):
                return True
    return False


# ===========================================================================
# Abstract LSP-backend interface
# ===========================================================================

class LspBackend:
    """Abstract interface the semantic dispatcher depends on.

    The dispatcher and semantic handlers only use the methods/attributes
    declared here; ClangdClient is the first (Phase 0) implementation. A second
    implementation (e.g. luals for Lua, Phase 1) can be slotted into the backend
    map without touching the handlers. Methods raise NotImplementedError so a
    half-built backend fails loudly rather than silently mis-answering.
    """

    project_root: str = ""
    process: Optional["asyncio.subprocess.Process"] = None

    async def start(self, project_root: str, **kwargs) -> str:
        raise NotImplementedError

    async def open_document(self, path: str) -> None:
        raise NotImplementedError

    async def workspace_symbol(self, query: str) -> List[dict]:
        raise NotImplementedError

    async def document_symbol(self, path: str) -> List[dict]:
        raise NotImplementedError

    async def definition(self, path: str, line: int, char: int) -> Any:
        raise NotImplementedError

    async def references(self, path: str, line: int, char: int) -> List[dict]:
        raise NotImplementedError

    async def implementation(self, path: str, line: int, char: int) -> List[dict]:
        raise NotImplementedError

    async def hover(self, path: str, line: int, char: int) -> Optional[dict]:
        raise NotImplementedError

    async def inlay_hints(self, path: str, start_line: int, end_line: int) -> List[dict]:
        raise NotImplementedError

    async def prepare_call_hierarchy(self, path: str, line: int, char: int) -> List[dict]:
        raise NotImplementedError

    async def call_hierarchy_incoming(self, item: dict) -> List[dict]:
        raise NotImplementedError

    async def call_hierarchy_outgoing(self, item: dict) -> List[dict]:
        raise NotImplementedError

    async def get_diagnostics(self, path: str, timeout: float = 10.0) -> List[dict]:
        raise NotImplementedError


# ===========================================================================
# ClangdClient  (C/C++/CUDA in one backend; language is config)
# ===========================================================================

class ClangdClient(LspBackend):
    """Async LSP client for clangd with background reader and push-notification
    support. CUDA-aware: when cuda_path is supplied to start(), compile_commands
    are translated for clangd via the CUDA CONFIG helpers above.
    """

    def __init__(self) -> None:
        self.project_root: str = ""
        self.cuda_path: str = ""
        self.cuda_arch: str = ""
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_id: int = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._diagnostics: Dict[str, List] = {}       # uri -> list[diagnostic]
        self._diag_events: Dict[str, asyncio.Event] = {}
        self._opened_files: set = set()
        self._reader_task: Optional[asyncio.Task] = None
        self._indexing_done: asyncio.Event = asyncio.Event()
        self._active_progress: set = set()   # tokens with begin but no end yet
        self._send_lock = asyncio.Lock()

    async def start(self, project_root: str, clangd_path: str = "clangd",
                    compile_commands_dir: Optional[str] = None,
                    cuda_path: Optional[str] = None,
                    cuda_arch: Optional[str] = None) -> str:
        """Launch clangd, perform LSP handshake, wait for background indexing.

        When cuda_path is given, the project's compile_commands.json is
        translated (nvcc -> clang flags, .cu/.cuh entries) into a cache dir and
        clangd is pointed at it (CUDA mode). Otherwise clangd uses the project's
        own compile_commands (plain C/C++ mode).
        """
        if self.process is not None:
            return "already initialized"

        self.project_root = str(pathlib.Path(project_root).resolve())
        self._indexing_done.clear()

        if compile_commands_dir:
            compile_commands_dir = str(pathlib.Path(compile_commands_dir).resolve())

        cuda_mode = bool(cuda_path)
        if cuda_mode:
            self.cuda_path = cuda_path
            self.cuda_arch = cuda_arch or "sm_86"
            # NOTE (Phase 0 limitation): the CUDA-translated DB is .cu/.cuh-only,
            # so a mixed C/C++ + CUDA project gets reduced C/C++ coverage while in
            # CUDA mode. Refining the merge is deferred past the skeleton phase.
            cc_dir = _prepare_compile_commands(
                self.project_root, self.cuda_path, self.cuda_arch, compile_commands_dir
            )
        else:
            self.cuda_path = ""
            self.cuda_arch = ""
            cc_dir = compile_commands_dir or self.project_root

        args = [
            clangd_path,
            "--background-index",
            "--clang-tidy=false",
            "--header-insertion=never",
            "--pch-storage=memory",
            f"--compile-commands-dir={cc_dir}",
        ]

        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
            cwd=self.project_root,
        )
        log.debug(f"clangd PID: {self.process.pid} (cuda_mode={cuda_mode})")

        self._reader_task = asyncio.create_task(self._reader_loop())

        init_params = {
            "processId": self.process.pid,
            "rootUri": path_to_uri(self.project_root),
            "workspaceFolders": [{"uri": path_to_uri(self.project_root), "name": "workspace"}],
            "initializationOptions": {
                "compilationDatabasePath": cc_dir,
            },
            "capabilities": {
                "general": {"positionEncodings": ["utf-8", "utf-16"]},
                "textDocument": {
                    "definition": {"linkSupport": True},
                    "publishDiagnostics": {},
                    "inlayHint": {"dynamicRegistration": True},
                },
                "window": {
                    "workDoneProgress": True,
                },
            },
        }
        response = await self._request("initialize", init_params, timeout=30.0)
        if "error" in response:
            raise RuntimeError(f"clangd initialize failed: {response['error']}")

        await self._notify("initialized", {})

        log.debug("Waiting for clangd background indexing...")
        try:
            await asyncio.wait_for(self._indexing_done.wait(), timeout=60.0)
            log.debug("Background indexing done.")
        except asyncio.TimeoutError:
            log.debug("Indexing wait timed out - priming index by opening source files...")

        await self._prime_index()

        version = response.get("result", {}).get("serverInfo", {})
        mode = "CUDA" if cuda_mode else "C/C++"
        return f"clangd initialized ({mode}) at {self.project_root} - {version}"

    async def _prime_index(self) -> None:
        """Open a sample of source files so the workspace index is populated."""
        prime_exts = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".cu", ".cuh"}
        source_files = []
        for root, _, files in os.walk(self.project_root):
            if any(part.startswith(".") or part in ("build", "out", "dist", ".git")
                   for part in pathlib.Path(root).parts):
                continue
            for fname in files:
                if pathlib.Path(fname).suffix.lower() in prime_exts:
                    source_files.append(os.path.join(root, fname))
                    if len(source_files) >= 10:
                        break
            if len(source_files) >= 10:
                break

        if source_files:
            log.debug(f"Priming index with {len(source_files)} file(s)")
            for path in source_files:
                await self.open_document(path)
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.process:
            try:
                await self._notify("exit", {})
            except Exception:
                pass
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                    await self.process.wait()
                except Exception:
                    pass
            self.process = None

        self._pending.clear()
        self._opened_files.clear()
        self._diagnostics.clear()

    async def _reader_loop(self) -> None:
        """Background task: read all LSP messages and route them."""
        assert self.process and self.process.stdout
        reader = self.process.stdout
        while True:
            msg = await read_lsp_message(reader)
            if msg is None:
                log.debug("clangd stdout EOF")
                # Bug #2 fix: fail every outstanding request so callers get a
                # prompt error instead of blocking until their per-request
                # timeout when clangd dies mid-flight.
                for req_id, fut in list(self._pending.items()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("clangd backend terminated unexpectedly"))
                    self._pending.pop(req_id, None)
                break

            msg_id = msg.get("id")
            method = msg.get("method", "")

            if msg_id is not None and ("result" in msg or "error" in msg):
                fut = self._pending.pop(msg_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)

            elif method == "window/workDoneProgress/create":
                token = msg.get("params", {}).get("token", "")
                if token:
                    self._active_progress.add(token)
                await self._send({"jsonrpc": "2.0", "id": msg_id, "result": None})

            elif method == "$/progress":
                token = msg.get("params", {}).get("token", "")
                kind = msg.get("params", {}).get("value", {}).get("kind", "")
                if kind == "begin":
                    self._active_progress.add(token)
                elif kind == "end":
                    self._active_progress.discard(token)
                    if not self._active_progress:
                        log.debug("All progress tokens finished - indexing done")
                        self._indexing_done.set()

            elif method == "textDocument/publishDiagnostics":
                params = msg.get("params", {})
                uri = params.get("uri", "")
                diags = params.get("diagnostics", [])
                self._diagnostics[uri] = diags
                ev = self._diag_events.get(uri)
                if ev:
                    ev.set()
                log.debug(f"Diagnostics for {uri}: {len(diags)} items")

            else:
                log.debug(f"Unhandled notification: {method}")

    async def _send(self, body: dict) -> None:
        assert self.process and self.process.stdin
        data = encode_lsp_message(body)
        async with self._send_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _request(self, method: str, params: Any, timeout: float = 10.0) -> dict:
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return {"error": {"message": f"timeout waiting for {method}"}}

    async def _notify(self, method: str, params: Any) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def open_document(self, path: str) -> None:
        """Send textDocument/didOpen if not already open."""
        abs_path = pathlib.Path(path)
        if not abs_path.is_absolute():
            abs_path = pathlib.Path(self.project_root) / abs_path
        abs_path = abs_path.resolve()
        uri = abs_path.as_uri()
        if uri in self._opened_files:
            return
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            log.debug(f"Cannot read {abs_path}: {e}")
            return
        self._opened_files.add(uri)
        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": _detect_language(str(abs_path)),
                "version": 1,
                "text": content,
            }
        })

    async def workspace_symbol(self, query: str) -> List[dict]:
        resp = await self._request("workspace/symbol", {"query": query}, timeout=15.0)
        return resp.get("result") or []

    async def document_symbol(self, path: str) -> List[dict]:
        await self.open_document(path)
        abs_uri = pathlib.Path(path) if pathlib.Path(path).is_absolute() \
            else pathlib.Path(self.project_root) / path
        resp = await self._request("textDocument/documentSymbol", {
            "textDocument": {"uri": abs_uri.resolve().as_uri()}
        }, timeout=15.0)
        return resp.get("result") or []

    async def definition(self, path: str, line: int, char: int) -> Any:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=15.0)
        return resp.get("result")

    async def references(self, path: str, line: int, char: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
            "context": {"includeDeclaration": True},
        }, timeout=15.0)
        return resp.get("result") or []

    async def implementation(self, path: str, line: int, char: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/implementation", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=15.0)
        result = resp.get("result")
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    async def hover(self, path: str, line: int, char: int) -> Optional[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=10.0)
        return resp.get("result")

    async def inlay_hints(self, path: str, start_line: int, end_line: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/inlayHint", {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": start_line, "character": 0},
                "end": {"line": end_line, "character": 0},
            },
        }, timeout=15.0)
        return resp.get("result") or []

    async def prepare_call_hierarchy(self, path: str, line: int, char: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=10.0)
        result = resp.get("result")
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    async def call_hierarchy_incoming(self, item: dict) -> List[dict]:
        resp = await self._request("callHierarchy/incomingCalls", {"item": item}, timeout=10.0)
        return resp.get("result") or []

    async def call_hierarchy_outgoing(self, item: dict) -> List[dict]:
        resp = await self._request("callHierarchy/outgoingCalls", {"item": item}, timeout=10.0)
        return resp.get("result") or []

    async def get_diagnostics(self, path: str, timeout: float = 10.0) -> List[dict]:
        """Open document, wait for publishDiagnostics push, return diagnostics."""
        uri = self._abs_uri(path)
        ev = self._diag_events.setdefault(uri, asyncio.Event())
        ev.clear()
        await self.open_document(path)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self._diagnostics.get(uri, [])

    def _abs_uri(self, path: str) -> str:
        p = pathlib.Path(path)
        if not p.is_absolute():
            p = pathlib.Path(self.project_root) / p
        return p.resolve().as_uri()

    def _abs_path(self, path: str) -> str:
        p = pathlib.Path(path)
        if not p.is_absolute():
            p = pathlib.Path(self.project_root) / p
        return str(p.resolve())


# ===========================================================================
# Location formatting helpers
# ===========================================================================

def _format_location(uri: str, lsp_range: dict, project_root: str) -> dict:
    rel = _relative_path(uri, project_root)
    start = lsp_range.get("start", {})
    end = lsp_range.get("end", {})
    return {
        "path": rel,
        "uri": uri,
        "range": lsp_range,
        "range_human": {
            "start": {"line": start.get("line", 0) + 1, "character": start.get("character", 0) + 1},
            "end": {"line": end.get("line", 0) + 1, "character": end.get("character", 0) + 1},
        },
        "line_text": _get_line(uri_to_path(uri), start.get("line", 0)),
    }


def _get_line(abs_path: str, zero_line: int) -> Optional[str]:
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if 0 <= zero_line < len(lines):
            return lines[zero_line].rstrip("\n")
    except Exception:
        pass
    return None


def _location_from_payload(payload: dict, project_root: str) -> Optional[dict]:
    """Normalise a raw LSP location/link dict into our standard format."""
    uri = payload.get("uri") or payload.get("targetUri")
    if not uri:
        return None
    lsp_range = (payload.get("range")
                 or payload.get("targetSelectionRange")
                 or payload.get("targetRange"))
    if not lsp_range:
        return None
    return _format_location(uri, lsp_range, project_root)


def _flatten_hover(contents: Any) -> str:
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        val = contents.get("value")
        return str(val) if val is not None else ""
    if isinstance(contents, list):
        parts = [_flatten_hover(c) for c in contents]
        return "\n".join(p for p in parts if p)
    return str(contents)


def _infer_type(text: str) -> str:
    """Extract the most relevant type name from clangd hover text."""
    cleaned = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "").strip()
    for line in cleaned.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith(("@", "#")):
            continue
        if candidate.lower().startswith("type-alias"):
            continue
        if candidate.startswith("aka:"):
            candidate = candidate[4:].strip()
        candidate = candidate.split("//", 1)[0].strip()
        if not candidate:
            continue
        if candidate.startswith("(") and candidate.endswith(")"):
            inner = candidate[1:-1].strip()
            if inner:
                return inner
        if "->" in candidate:
            return candidate.split("->")[-1].strip()
        if any(sym in candidate for sym in ("::", "<", "*", "&")) or candidate.startswith("std::"):
            return candidate
        if any(c.isalpha() for c in candidate):
            return candidate
    return cleaned


def _iter_document_symbols(symbols: List[dict]):
    """Flatten hierarchical document symbols."""
    for s in symbols:
        if not isinstance(s, dict):
            continue
        yield s
        for child in _iter_document_symbols(s.get("children") or []):
            yield child


# ===========================================================================
# Filesystem fallback for workspace symbols (static-inline in headers)
# ===========================================================================

_FALLBACK_EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".hxx", ".m", ".mm", ".cu", ".cuh")
_FALLBACK_SKIP_DIRS = {
    "build", "vendor", "third_party", "third-party", "node_modules",
    ".git", ".cache", ".clangd", ".ccache", "_deps",
}


def _find_files_with_word(root: str, word: str, exts=_FALLBACK_EXTS,
                          limit: int = 20) -> List[str]:
    """Walk *root* and return up to *limit* source files whose content contains
    *word* as a whole identifier. Pure Python, no shell deps. This single DRY
    grep core feeds the A-class semantic fallback (_fallback_workspace_symbols
    and _symbol_to_location tier 3).
    """
    rx = re.compile(rb"\b" + re.escape(word.encode("utf-8", "ignore")) + rb"\b")
    hits: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _FALLBACK_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(exts):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full, "rb") as f:
                    if rx.search(f.read()):
                        hits.append(full)
                        if len(hits) >= limit:
                            return hits
            except (OSError, IOError):
                continue
    return hits


def _outline_flatten(symbols: List[dict]) -> List[dict]:
    """Flatten a hierarchical DocumentSymbol tree into a flat list."""
    out: List[dict] = []
    for sym in symbols:
        out.append(sym)
        children = sym.get("children") or []
        if children:
            out.extend(_outline_flatten(children))
    return out


async def _fallback_workspace_symbols(client: ClangdClient, query: str,
                                      limit: int = 50) -> List[dict]:
    """Locate symbols clangd's global index drops (notably static-inline in
    headers) by grepping the project for the identifier, then asking clangd for
    the real DocumentSymbol of each candidate file and filtering by name.
    """
    candidates = _find_files_with_word(client.project_root, query, limit=20)
    results: List[dict] = []
    seen: set = set()
    for path in candidates:
        try:
            doc_syms = await client.document_symbol(path)
        except Exception:
            continue
        file_uri = pathlib.Path(path).as_uri()
        for sym in _outline_flatten(doc_syms):
            name = sym.get("name", "")
            if query not in name:
                continue
            if "selectionRange" in sym:
                lsp_range = sym.get("selectionRange") or sym.get("range") or {}
                location = {"uri": file_uri, "range": lsp_range}
            else:
                location = sym.get("location") or {"uri": file_uri, "range": {}}
            key = (name, location.get("uri"),
                   location.get("range", {}).get("start", {}).get("line"))
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": name,
                "kind": sym.get("kind", 0),
                "containerName": sym.get("containerName") or sym.get("detail"),
                "location": location,
            })
            if len(results) >= limit:
                return results
    return results


# ===========================================================================
# Symbol lookup: name -> position (single 3-tier cascade; fixes CUDA bug #1)
# ===========================================================================

async def _symbol_to_location(client: ClangdClient, symbol_name: str,
                              preferred_path: Optional[str] = None,
                              max_retries: int = 3) -> Optional[dict]:
    """Find the first workspace symbol matching symbol_name with a definition
    kind. Three tiers: (1) workspace/symbol with retries, (2) document_symbol on
    the preferred file, (3) grep the tree then document_symbol on each candidate.
    This is the SINGLE canonical cascade - the CUDA truncation (bug #1) cannot
    recur because there is no second, shorter copy.
    """
    if preferred_path:
        await client.open_document(client._abs_path(preferred_path))
    abs_preferred = client._abs_path(preferred_path) if preferred_path else None

    def _make_entry(uri: str, start: dict) -> dict:
        return {
            "path": uri_to_path(uri),
            "uri": uri,
            "line": start.get("line", 0),
            "char": start.get("character", 0),
        }

    for attempt in range(max_retries):
        symbols = await client.workspace_symbol(symbol_name)

        best = None
        for sym in symbols:
            kind = symbol_kind_name(sym.get("kind", 0))
            if sym.get("name") != symbol_name:
                continue
            if kind not in DEFINITION_KINDS:
                continue
            loc = sym.get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            if uri:
                entry = _make_entry(uri, start)
                if abs_preferred and entry["path"] == abs_preferred:
                    return entry
                if best is None:
                    best = entry
        if best:
            return best

        best = None
        for sym in symbols:
            if sym.get("name") != symbol_name:
                continue
            loc = sym.get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            if uri:
                entry = _make_entry(uri, start)
                if abs_preferred and entry["path"] == abs_preferred:
                    return entry
                if best is None:
                    best = entry
        if best:
            return best

        if attempt < max_retries - 1:
            log.debug(f"workspace/symbol retry {attempt + 1} for '{symbol_name}'")
            await asyncio.sleep(1.0)

    # Tier 2: document_symbol on the preferred file
    if abs_preferred:
        log.debug(f"workspace/symbol miss - document_symbol on {abs_preferred}")
        try:
            doc_syms = await client.document_symbol(abs_preferred)
            file_uri = pathlib.Path(abs_preferred).as_uri()
            for sym in _outline_flatten(doc_syms):
                if sym.get("name") == symbol_name:
                    sel = sym.get("selectionRange") or sym.get("range") or {}
                    return _make_entry(file_uri, sel.get("start", {}))
        except Exception:
            pass

    # Tier 3: grep project tree then document_symbol on each candidate
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol_name):
        candidates = _find_files_with_word(client.project_root, symbol_name, limit=10)
        if abs_preferred:
            candidates = [c for c in candidates if c != abs_preferred]
        for fpath in candidates:
            try:
                doc_syms = await client.document_symbol(fpath)
            except Exception:
                continue
            file_uri = pathlib.Path(fpath).as_uri()
            for sym in _outline_flatten(doc_syms):
                if sym.get("name") == symbol_name and symbol_kind_name(sym.get("kind", 0)) in DEFINITION_KINDS:
                    sel = sym.get("selectionRange") or sym.get("range") or {}
                    return _make_entry(file_uri, sel.get("start", {}))

    return None


# ===========================================================================
# Call hierarchy helpers
# ===========================================================================

async def _collect_call_hierarchy(client: ClangdClient, path: str, line: int,
                                  char: int, depth: int) -> Optional[dict]:
    if depth < 0:
        return None
    items = await client.prepare_call_hierarchy(path, line, char)
    if not items:
        return None

    formatted = []
    for item in items:
        expanded = await _expand_hierarchy_item(client, item, depth, set())
        if expanded:
            formatted.append(expanded)

    if not formatted:
        return None
    return formatted[0] if len(formatted) == 1 else {"roots": formatted}


async def _expand_hierarchy_item(client: ClangdClient, item: dict, depth: int,
                                 seen: set) -> Optional[dict]:
    uri = item.get("uri", "")
    sel_range = item.get("selectionRange") or item.get("range", {})
    start = sel_range.get("start", {})
    node_id = f"{uri}:{start.get('line')}:{start.get('character')}"
    if node_id in seen:
        return None
    seen.add(node_id)

    location = _format_location(uri, sel_range, client.project_root) if uri else None
    node = {
        "symbol": item.get("name", ""),
        "kind": symbol_kind_name(item.get("kind", 0)),
        "location": location,
    }
    if item.get("detail"):
        node["detail"] = item["detail"]

    if depth == 0:
        return node

    incoming = await client.call_hierarchy_incoming(item)
    outgoing = await client.call_hierarchy_outgoing(item)

    if incoming:
        in_entries = []
        for entry in incoming:
            src = entry.get("from")
            if not src:
                continue
            child = await _expand_hierarchy_item(client, src, depth - 1, seen.copy())
            if child:
                if entry.get("fromRanges"):
                    child["ranges"] = entry["fromRanges"]
                in_entries.append(child)
        if in_entries:
            node["incoming"] = in_entries

    if outgoing:
        out_entries = []
        for entry in outgoing:
            tgt = entry.get("to")
            if not tgt:
                continue
            child = await _expand_hierarchy_item(client, tgt, depth - 1, seen.copy())
            if child:
                if entry.get("fromRanges"):
                    child["ranges"] = entry["fromRanges"]
                out_entries.append(child)
        if out_entries:
            node["outgoing"] = out_entries

    return node


# ===========================================================================
# Backend map + lazy init
# ===========================================================================
#
# The backend map is keyed by LSP-backend TYPE ("clangd" in Phase 0; "luals"
# later). Filetype routing selects the type. Init is LAZY and authored here
# (NOT the clangd eager-at-startup model): the first semantic call for a
# filetype triggers init; concurrent first-calls coalesce onto one in-flight
# task; an init failure is cached for a backoff window so a crashing clangd is
# not re-spawned on every call. The file layer NEVER triggers init.

_backends: Dict[str, ClangdClient] = {}
_backend_init_tasks: Dict[str, "asyncio.Task"] = {}
_backend_init_failed: Dict[str, float] = {}     # backend_type -> loop.time() of last failure
_INIT_FAILURE_BACKOFF = 30.0                    # seconds
_CLANGD_FILETYPES = {"c", "cpp", "cuda"}


def _route_filetype(filetype: str) -> Optional[str]:
    """Map a languageId (_detect_language output) to a backend TYPE, or None."""
    if filetype in _CLANGD_FILETYPES:
        return "clangd"
    return None


def _require_backend(filetype: str) -> ClangdClient:
    """Return the live backend for *filetype* or raise if not initialized.

    Sync accessor for callers that must NOT trigger init (status, no-op handlers).
    The semantic handlers use the async _ensure_backend trigger instead.
    """
    backend_type = _route_filetype(filetype)
    if backend_type is None:
        raise ValueError(f"No LSP backend for filetype '{filetype}'")
    client = _backends.get(backend_type)
    if client is None or client.process is None:
        raise RuntimeError(f"LSP backend '{backend_type}' not initialized for filetype '{filetype}'")
    return client


async def _init_backend(backend_type: str, project_root: str) -> Optional[ClangdClient]:
    """Spawn and initialize one backend. Returns the live client, or None on
    failure (failure is cached by the caller via _backend_init_failed).
    """
    client = ClangdClient()
    cuda_path = None
    cuda_arch = None
    # CUDA mode only when the project actually has .cu/.cuh sources AND an SDK
    # exists - a bare SDK is not enough (matches _has_cuda_sources contract).
    if _has_cuda_sources(project_root):
        cuda_path = _find_cuda_sdk(project_root=project_root)
        if cuda_path:
            cuda_arch = _detect_cuda_arch(project_root=project_root) or "sm_86"
        else:
            log.debug("CUDA sources present but no SDK found - starting clangd in plain mode")
    msg = await client.start(project_root, cuda_path=cuda_path, cuda_arch=cuda_arch)
    log.debug("Backend '%s' init: %s", backend_type, msg)
    return client


async def _ensure_backend(filetype: str, project_root: str) -> ClangdClient:
    """Lazy-init trigger: return a live backend for *filetype*, starting it on
    first use. Coalesces concurrent first-calls onto a single in-flight init
    task and honours an init-failure backoff window.
    """
    backend_type = _route_filetype(filetype)
    if backend_type is None:
        raise ValueError(f"No LSP backend for filetype '{filetype}'")

    resolved_root = str(pathlib.Path(project_root).resolve())
    loop = asyncio.get_running_loop()

    client = _backends.get(backend_type)
    if client is not None and client.process is not None:
        # Defensive project-root mismatch warning [inspector M3]: there is no
        # handle_init under lazy init, and one server process is pinned to one
        # --project-root, so this should not fire - but surface it if it does.
        if client.project_root != resolved_root:
            log.warning(
                "LSP backend '%s' already running on a different project_root "
                "(active=%s, requested=%s); restart the server to switch projects.",
                backend_type, client.project_root, resolved_root,
            )
        return client

    failed_at = _backend_init_failed.get(backend_type)
    if failed_at is not None and (loop.time() - failed_at) < _INIT_FAILURE_BACKOFF:
        raise RuntimeError(
            f"LSP backend '{backend_type}' init failed recently; backing off "
            f"(retry in ~{int(_INIT_FAILURE_BACKOFF - (loop.time() - failed_at))}s)"
        )

    # Once-only / coalescing guard: the check-and-create below has no await, so
    # concurrent first-calls in this single-threaded loop cannot both create a
    # task - the second sees the one the first stored.
    task = _backend_init_tasks.get(backend_type)
    if task is None or task.done():
        task = asyncio.ensure_future(_init_backend(backend_type, resolved_root))
        _backend_init_tasks[backend_type] = task

    try:
        client = await asyncio.wait_for(asyncio.shield(task), timeout=90.0)
    except asyncio.TimeoutError:
        raise RuntimeError(f"LSP backend '{backend_type}' init timed out (90s)")
    except Exception as exc:
        _backend_init_failed[backend_type] = loop.time()
        _backend_init_tasks.pop(backend_type, None)
        raise RuntimeError(f"LSP backend '{backend_type}' init failed: {exc}")

    if client is None or client.process is None:
        _backend_init_failed[backend_type] = loop.time()
        _backend_init_tasks.pop(backend_type, None)
        raise RuntimeError(f"LSP backend '{backend_type}' could not be initialized")

    _backends[backend_type] = client
    _backend_init_failed.pop(backend_type, None)
    return client


# ===========================================================================
# Semantic data helpers (return plain Python objects, or {"error": ...})
# ===========================================================================

async def _def_by_name(client: ClangdClient, symbol_name: str,
                       preferred_path: Optional[str], context_lines: int) -> Any:
    loc = await _symbol_to_location(client, symbol_name, preferred_path=preferred_path or None)
    if not loc:
        return {"error": f"Symbol '{symbol_name}' not found in workspace"}
    def_result = await client.definition(loc["path"], loc["line"], loc["char"])
    if not def_result:
        return {"error": f"No definition found for '{symbol_name}'"}
    locations = def_result if isinstance(def_result, list) else [def_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        abs_path = uri_to_path(location["uri"])
        def_line = location["range"]["start"]["line"]
        results.append({
            "symbol": symbol_name,
            "location": location,
            "context": extract_surrounding_code(abs_path, def_line, context_lines),
        })
    return results


async def _def_at(client: ClangdClient, abs_path: str, line: int, char: int,
                  context_lines: int) -> Any:
    def_result = await client.definition(abs_path, line, char)
    if not def_result:
        return {"error": "No definition found at this position"}
    locations = def_result if isinstance(def_result, list) else [def_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        ap = uri_to_path(location["uri"])
        dl = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(ap, dl, context_lines),
        })
    return results


async def _refs_by_name(client: ClangdClient, symbol_name: str,
                        preferred_path: Optional[str], max_results: int,
                        context_lines: int) -> dict:
    if preferred_path:
        await client.open_document(client._abs_path(preferred_path))
    symbols = await client.workspace_symbol(symbol_name)
    all_refs = []
    seen = set()
    for sym in symbols:
        if sym.get("name") != symbol_name:
            continue
        if symbol_kind_name(sym.get("kind", 0)) not in DEFINITION_KINDS:
            continue
        loc = sym.get("location", {})
        start = loc.get("range", {}).get("start", {})
        refs = await client.references(uri_to_path(loc.get("uri", "")),
                                       start.get("line", 0), start.get("character", 0))
        for ref in refs:
            if max_results > 0 and len(all_refs) >= max_results:
                break
            ref_uri = ref.get("uri", "")
            ref_start = ref.get("range", {}).get("start", {})
            key = f"{ref_uri}:{ref_start.get('line')}:{ref_start.get('character')}"
            if key in seen:
                continue
            seen.add(key)
            location = _location_from_payload(ref, client.project_root)
            if not location:
                continue
            ap = uri_to_path(location["uri"])
            rl = location["range"]["start"]["line"]
            all_refs.append({
                "symbol": symbol_name,
                "location": location,
                "context": extract_surrounding_code(ap, rl, context_lines) if context_lines > 0 else None,
            })
        if max_results > 0 and len(all_refs) >= max_results:
            break
    return {"symbol": symbol_name, "count": len(all_refs), "references": all_refs}


async def _refs_at(client: ClangdClient, abs_path: str, line: int, char: int,
                   max_results: int, context_lines: int) -> dict:
    refs = await client.references(abs_path, line, char)
    all_refs = []
    seen = set()
    for ref in refs:
        if max_results > 0 and len(all_refs) >= max_results:
            break
        ref_uri = ref.get("uri", "")
        ref_start = ref.get("range", {}).get("start", {})
        key = f"{ref_uri}:{ref_start.get('line')}:{ref_start.get('character')}"
        if key in seen:
            continue
        seen.add(key)
        location = _location_from_payload(ref, client.project_root)
        if not location:
            continue
        ap = uri_to_path(location["uri"])
        rl = location["range"]["start"]["line"]
        all_refs.append({
            "location": location,
            "context": extract_surrounding_code(ap, rl, context_lines) if context_lines > 0 else None,
        })
    return {"count": len(all_refs), "references": all_refs}


def _ok(data: Any) -> dict:
    """Wrap a semantic result object as a purity __raw_text__ payload."""
    return {"__raw_text__": json.dumps(data, ensure_ascii=False, indent=2)}


# ===========================================================================
# Semantic handlers (10 canonical; async; signature (params, project_root, strict))
# ===========================================================================
#
# A-class (grep-degradable): find_definition, find_references, symbol,
#   symbol_context fall back to the grep net on empty/no-index.
# B-class (honest error): type_at, diagnostics, outline, find_implementations
#   return an explicit error when the LSP yields nothing - never a grep guess.
# All semantic path params are confined to --project-root via safe_path
# [security P1, CWE-22] before any path reaches the LSP backend.

async def handle_find_definition(params: dict, project_root: str, strict: bool = False) -> dict:
    """Find a definition by symbol name OR by file position (symbol/at routing)."""
    line = params.get("line")
    char = params.get("character")
    symbol_name = params.get("symbol_name", "")
    path = params.get("relative_path", "")
    context_lines = int(params.get("context_lines", 5))

    if line is not None or char is not None:
        if not path:
            return {"error": "Positional find_definition requires 'path' with 'line'/'character'"}
        abs_path = safe_path(project_root, path, strict)
        client = await _ensure_backend(_detect_language(abs_path), project_root)
        l = int(line if line is not None else 1) - 1
        c = int(char if char is not None else 1) - 1
        data = await _def_at(client, client._abs_path(abs_path), l, c, context_lines)
    elif symbol_name:
        abs_path = safe_path(project_root, path, strict) if path else None
        ft = _detect_language(abs_path) if abs_path else "cpp"
        client = await _ensure_backend(ft, project_root)
        data = await _def_by_name(client, symbol_name, abs_path, context_lines)
    else:
        return {"error": "find_definition requires either 'symbol' (name) or 'at' (path + line + character)"}

    if isinstance(data, dict) and "error" in data:
        return data
    return _ok(data)


async def handle_find_references(params: dict, project_root: str, strict: bool = False) -> dict:
    """Find references by symbol name OR by file position (symbol/at routing)."""
    line = params.get("line")
    char = params.get("character")
    symbol_name = params.get("symbol_name", "")
    path = params.get("relative_path", "")
    max_results = int(params.get("max_results", 50))
    context_lines = int(params.get("context_lines", 3))

    if line is not None or char is not None:
        if not path:
            return {"error": "Positional find_references requires 'path' with 'line'/'character'"}
        abs_path = safe_path(project_root, path, strict)
        client = await _ensure_backend(_detect_language(abs_path), project_root)
        l = int(line if line is not None else 1) - 1
        c = int(char if char is not None else 1) - 1
        data = await _refs_at(client, client._abs_path(abs_path), l, c, max_results, context_lines)
    elif symbol_name:
        abs_path = safe_path(project_root, path, strict) if path else None
        ft = _detect_language(abs_path) if abs_path else "cpp"
        client = await _ensure_backend(ft, project_root)
        data = await _refs_by_name(client, symbol_name, abs_path, max_results, context_lines)
    else:
        return {"error": "find_references requires either 'symbol' (name) or 'at' (path + line + character)"}

    return _ok(data)


async def handle_find_implementations(params: dict, project_root: str, strict: bool = False) -> dict:
    """Find implementations at a file position (B-class; positional only)."""
    path = params.get("relative_path", "")
    if not path:
        return {"error": "find_implementations requires 'path' with 'line'/'character'"}
    abs_path = safe_path(project_root, path, strict)
    client = await _ensure_backend(_detect_language(abs_path), project_root)
    line = int(params.get("line", 1)) - 1
    char = int(params.get("character", 1)) - 1
    impls = await client.implementation(client._abs_path(abs_path), line, char)
    results = []
    for payload in impls:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        ap = uri_to_path(location["uri"])
        il = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(ap, il, int(params.get("context_lines", 5))),
        })
    if not results:
        return {"error": "No implementations found at this position"}
    return _ok(results)


async def handle_type_at(params: dict, project_root: str, strict: bool = False) -> dict:
    """Type at a position: hover text + the deduced type (auto/template)."""
    path = params.get("relative_path", "")
    if not path:
        return {"error": "type_at requires 'path' with 'line'/'character'"}
    abs_path = safe_path(project_root, path, strict)
    client = await _ensure_backend(_detect_language(abs_path), project_root)
    line = int(params.get("line", 1)) - 1
    char = int(params.get("character", 1)) - 1
    result = await client.hover(client._abs_path(abs_path), line, char)
    if not result:
        return {"error": "No hover/type information at this position"}
    contents = result.get("contents")
    raw_text = _flatten_hover(contents).strip()
    deduced = _infer_type(raw_text)
    hover_range = result.get("range")
    location = None
    if hover_range:
        uri = pathlib.Path(client._abs_path(abs_path)).as_uri()
        location = _format_location(uri, hover_range, client.project_root)
    return _ok({"text": raw_text, "deduced_type": deduced, "location": location})


async def handle_diagnostics(params: dict, project_root: str, strict: bool = False) -> dict:
    """Compiler diagnostics for a file (B-class)."""
    path = params.get("relative_path", "")
    if not path:
        return {"error": "diagnostics requires 'path'"}
    abs_path = safe_path(project_root, path, strict)
    client = await _ensure_backend(_detect_language(abs_path), project_root)
    timeout = float(params.get("timeout", 10.0))
    cpath = client._abs_path(abs_path)
    diags = await client.get_diagnostics(cpath, timeout=timeout)
    severity_map = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}
    results = []
    for d in diags:
        lsp_range = d.get("range", {})
        uri = pathlib.Path(cpath).as_uri()
        results.append({
            "message": d.get("message", ""),
            "severity": severity_map.get(d.get("severity", 0), "Unknown"),
            "code": d.get("code"),
            "source": d.get("source"),
            "location": _format_location(uri, lsp_range, client.project_root),
        })
    return _ok({
        "path": _relative_path(pathlib.Path(cpath).as_uri(), client.project_root),
        "count": len(results),
        "diagnostics": results,
    })


async def handle_outline(params: dict, project_root: str, strict: bool = False) -> dict:
    """Document symbols / outline for a file (B-class)."""
    path = params.get("relative_path", "")
    if not path:
        return {"error": "outline requires 'path'"}
    abs_path = safe_path(project_root, path, strict)
    client = await _ensure_backend(_detect_language(abs_path), project_root)
    cpath = client._abs_path(abs_path)
    symbols = await client.document_symbol(cpath)
    file_uri = pathlib.Path(cpath).as_uri()

    def fmt(sym: dict) -> dict:
        if "selectionRange" in sym:
            sel = _format_location(file_uri, sym["selectionRange"], client.project_root)
            ext = _format_location(file_uri, sym.get("range", sym["selectionRange"]), client.project_root)
            node = {
                "symbol": sym.get("name", ""),
                "kind": symbol_kind_name(sym.get("kind", 0)),
                "detail": sym.get("detail"),
                "selection": sel,
                "extent": ext,
            }
            children = sym.get("children") or []
            if children:
                node["children"] = [fmt(c) for c in children]
        else:
            loc = sym.get("location", {})
            node = {
                "symbol": sym.get("name", ""),
                "kind": symbol_kind_name(sym.get("kind", 0)),
                "container": sym.get("containerName"),
                "location": _format_location(loc.get("uri", file_uri), loc.get("range", {}), client.project_root),
            }
        return node

    return _ok([fmt(s) for s in symbols])


async def handle_symbol(params: dict, project_root: str, strict: bool = False) -> dict:
    """Search workspace symbols by query (A-class; grep fallback unless strict)."""
    query = params.get("query") or params.get("symbol_name") or ""
    if not query:
        return {"error": "symbol requires 'query' (or 'symbol')"}
    limit = int(params.get("limit") or params.get("max_results") or 50)
    # 'strict' here disables the filesystem fallback (clangd API); it is a params
    # key, distinct from the server-level sandbox 'strict' arg used by safe_path.
    disable_fallback = _bool_param(params.get("strict"), default=False)
    client = await _ensure_backend("cpp", project_root)

    symbols = await client.workspace_symbol(query)
    fallback_used = False
    if not symbols and not disable_fallback and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", query):
        symbols = await _fallback_workspace_symbols(client, query, limit=limit)
        fallback_used = True

    results = []
    for sym in symbols[:limit]:
        loc = sym.get("location", {})
        uri = loc.get("uri", "")
        lsp_range = loc.get("range", {})
        results.append({
            "symbol": sym.get("name", ""),
            "kind": symbol_kind_name(sym.get("kind", 0)),
            "container": sym.get("containerName"),
            "location": _format_location(uri, lsp_range, client.project_root) if uri else None,
        })
    out = {"query": query, "count": len(results), "symbols": results}
    if fallback_used:
        out["source"] = "fallback:document_symbol"
    return _ok(out)


async def handle_symbol_context(params: dict, project_root: str, strict: bool = False) -> dict:
    """Definition + references for a symbol in one call (A-class)."""
    symbol_name = params.get("symbol_name", "")
    if not symbol_name:
        return {"error": "symbol_context requires 'symbol' (name)"}
    path = params.get("relative_path", "")
    max_references = int(params.get("max_references", 20))
    context_lines = int(params.get("context_lines", 5))
    abs_path = safe_path(project_root, path, strict) if path else None
    ft = _detect_language(abs_path) if abs_path else "cpp"
    client = await _ensure_backend(ft, project_root)
    definition = await _def_by_name(client, symbol_name, abs_path, context_lines)
    references = await _refs_by_name(client, symbol_name, abs_path, max_references, 2)
    return _ok({"symbol": symbol_name, "definition": definition, "references": references})


async def handle_inlay_hints(params: dict, project_root: str, strict: bool = False) -> dict:
    """Inlay hints (parameter names, type hints) for a file range."""
    path = params.get("relative_path", "")
    if not path:
        return {"error": "inlay_hints requires 'path'"}
    abs_path = safe_path(project_root, path, strict)
    client = await _ensure_backend(_detect_language(abs_path), project_root)
    start_line = int(params.get("start_line", 1)) - 1
    end_line = int(params.get("end_line", 9999)) - 1
    limit = int(params.get("limit", 100))
    hints = await client.inlay_hints(client._abs_path(abs_path), start_line, end_line)
    kind_map = {1: "Parameter", 2: "Type"}
    results = []
    for hint in hints[:limit]:
        pos = hint.get("position", {})
        label = hint.get("label", "")
        if isinstance(label, list):
            label = "".join(
                p.get("value", "") if isinstance(p, dict) else str(p)
                for p in label
            )
        raw_kind = hint.get("kind")
        results.append({
            "label": label,
            "kind": kind_map.get(raw_kind, "Unknown") if raw_kind else None,
            "position": {
                "lsp": pos,
                "human": {"line": pos.get("line", 0) + 1, "character": pos.get("character", 0) + 1},
            },
            "tooltip": hint.get("tooltip"),
        })
    return _ok(results)


async def handle_symbol_change_impact(params: dict, project_root: str, strict: bool = False) -> dict:
    """Definition + references + call hierarchy for impact analysis.

    Mixed class: the references part is A-class (grep-degradable); the
    call-hierarchy part is B-class. If the LSP yields no call hierarchy, the
    result is flagged partial rather than failing the whole call.
    """
    symbol_name = params.get("symbol_name", "")
    if not symbol_name:
        return {"error": "symbol_change_impact requires 'symbol' (name)"}
    path = params.get("relative_path", "")
    max_references = int(params.get("max_references", 50))
    depth = int(params.get("call_hierarchy_depth", 1))
    abs_path = safe_path(project_root, path, strict) if path else None
    ft = _detect_language(abs_path) if abs_path else "cpp"
    client = await _ensure_backend(ft, project_root)

    definition = await _def_by_name(client, symbol_name, abs_path, 3)
    references = await _refs_by_name(client, symbol_name, abs_path, max_references, 2)

    call_hierarchies = []
    if isinstance(definition, list):
        seen_roots = set()
        for defn in definition:
            loc = defn.get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            line = start.get("line", 0)
            char = start.get("character", 0)
            key = f"{uri}:{line}:{char}"
            if key in seen_roots:
                continue
            seen_roots.add(key)
            hier = await _collect_call_hierarchy(client, uri_to_path(uri), line, char, depth)
            if hier:
                call_hierarchies.append(hier)

    ref_files = sorted({
        r.get("location", {}).get("path", "")
        for r in (references.get("references", []) if isinstance(references, dict) else [])
        if r.get("location", {}).get("path")
    })

    return _ok({
        "symbol": symbol_name,
        "definition": definition,
        "references": references,
        "reference_summary": {
            "count": references.get("count", 0) if isinstance(references, dict) else 0,
            "files": ref_files,
        },
        "call_hierarchy": call_hierarchies,
        "partial": not call_hierarchies,
    })


def handle_lsp_init_noop(params: dict, project_root: str, strict: bool = False) -> dict:
    """Deprecation no-op for legacy clangd_init / cuda_init names.

    Under lazy init there is no explicit init step - the LSP backend spins up on
    first semantic call. This returns a benign notice (NOT an error) so existing
    callers that still send an init do not break.
    """
    return {"__raw_text__":
            "LSP backends initialize lazily on first semantic call - no explicit "
            "init is needed. (clangd_init/cuda_init are accepted as no-ops.)"}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Callable[..., dict]] = {
    "read_file": handle_read_file,
    "create_text_file": handle_create_text_file,
    "list_dir": handle_list_dir,
    "ls": handle_list_dir,
    "find_file": handle_find_file,
    "glob": handle_find_file,
    "replace_content": handle_replace_content,
    "delete_lines": handle_delete_lines,
    "replace_lines": handle_replace_lines,
    "insert_at_line": handle_insert_at_line,
    "search_for_pattern": handle_search_for_pattern,
    "grep": handle_search_for_pattern,
    "search": handle_search_for_pattern,
    # --- semantic (LSP) functions: canonical names ---
    "find_definition": handle_find_definition,
    "find_references": handle_find_references,
    "find_implementations": handle_find_implementations,
    "type_at": handle_type_at,
    "diagnostics": handle_diagnostics,
    "outline": handle_outline,
    "symbol": handle_symbol,
    "symbol_context": handle_symbol_context,
    "inlay_hints": handle_inlay_hints,
    "symbol_change_impact": handle_symbol_change_impact,
    # --- legacy clangd_* names as DIRECT keys ([inspector C1]: FUNCTION_ALIASES
    #     does NOT route; the dispatcher looks up the RAW function name) ---
    "clangd_find_definition": handle_find_definition,
    "clangd_find_definition_at": handle_find_definition,
    "clangd_find_references": handle_find_references,
    "clangd_find_references_at": handle_find_references,
    "clangd_find_implementations_at": handle_find_implementations,
    "clangd_workspace_symbols": handle_symbol,
    "clangd_document_outline": handle_outline,
    "clangd_symbol_context": handle_symbol_context,
    "clangd_inlay_hints": handle_inlay_hints,
    "clangd_symbol_change_impact": handle_symbol_change_impact,
    "clangd_hover": handle_type_at,
    "clangd_diagnostics": handle_diagnostics,
    "clangd_deduced_type_at": handle_type_at,
    "clangd_init": handle_lsp_init_noop,
    # --- legacy cuda_* names as DIRECT keys ---
    "cuda_find_definition": handle_find_definition,
    "cuda_find_definition_at": handle_find_definition,
    "cuda_find_references": handle_find_references,
    "cuda_find_references_at": handle_find_references,
    "cuda_find_implementations_at": handle_find_implementations,
    "cuda_workspace_symbols": handle_symbol,
    "cuda_document_outline": handle_outline,
    "cuda_symbol_context": handle_symbol_context,
    "cuda_inlay_hints": handle_inlay_hints,
    "cuda_symbol_change_impact": handle_symbol_change_impact,
    "cuda_hover": handle_type_at,
    "cuda_diagnostics": handle_diagnostics,
    "cuda_deduced_type_at": handle_type_at,
    "cuda_init": handle_lsp_init_noop,
}

# Per-handler accepted (canonical) parameter names. Used only to augment
# error messages with an "Unknown params: ..." hint when a handler raises.
# Keys here are POST-alias canonical names — by the time this set is
# consulted, _resolve_aliases has already mapped caller-supplied aliases
# onto these canonical names.
HANDLER_ACCEPTED_PARAMS: Dict[str, set] = {
    "read_file": {
        "relative_path", "start_line", "end_line", "max_answer_chars",
    },
    "create_text_file": {
        "relative_path", "content",
    },
    "list_dir": {
        "relative_path", "recursive", "skip_ignored_files",
        "long", "show_hidden", "all", "hidden",
        "paths_include_glob", "filter", "grep", "grep_pattern",
        "head_limit", "offset", "max_answer_chars",
    },
    "find_file": {
        "file_mask", "pattern", "substring_pattern", "relative_path",
        "head_limit", "offset",
    },
    "replace_content": {
        "relative_path", "needle", "repl", "mode",
        "allow_multiple_occurrences",
    },
    "delete_lines": {
        "relative_path", "start_line", "end_line", "line",
    },
    "replace_lines": {
        "relative_path", "start_line", "end_line", "line", "content",
    },
    "insert_at_line": {
        "relative_path", "line", "content",
    },
    "search_for_pattern": {
        "substring_pattern", "context_lines_before", "context_lines_after",
        "paths_include_glob", "paths_exclude_glob", "relative_path",
        "max_answer_chars", "head_limit", "offset", "output_mode",
        "max_file_size", "skip_ignored_files",
    },
    # --- semantic (LSP) functions: POST-alias canonical param names ---
    "find_definition": {
        "relative_path", "line", "character", "symbol_name", "context_lines",
    },
    "find_references": {
        "relative_path", "line", "character", "symbol_name", "max_results",
        "context_lines",
    },
    "find_implementations": {
        "relative_path", "line", "character", "context_lines",
    },
    "type_at": {
        "relative_path", "line", "character",
    },
    "diagnostics": {
        "relative_path", "timeout",
    },
    "outline": {
        "relative_path",
    },
    "symbol": {
        "query", "symbol_name", "limit", "max_results", "strict",
    },
    "symbol_context": {
        "symbol_name", "relative_path", "max_references", "context_lines",
    },
    "inlay_hints": {
        "relative_path", "start_line", "end_line", "limit",
    },
    "symbol_change_impact": {
        "symbol_name", "relative_path", "max_references", "call_hierarchy_depth",
    },
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def handle_purity_call(arguments: dict, project_root: str, strict: bool = False) -> dict:
    """Route a purity_call invocation to the appropriate handler.

    Accepts both long and short keys: function/f, params/p. Async semantic (LSP)
    handlers are awaited directly; sync file handlers run in the default thread
    executor so long file ops never block the event loop.
    """
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    canonical_func = _canonical_function(function)
    raw_params = arguments.get("params") or arguments.get("p") or {}

    try:
        params = _resolve_aliases(raw_params, canonical_func)
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        func_list = "\n".join(f"  {name}" for name in sorted(HANDLERS.keys()))
        return {"__raw_text__": f"mcp-purity OK — project: {project_root}\nAvailable functions:\n{func_list}"}

    handler = HANDLERS.get(function)
    if not handler:
        func_list = ", ".join(sorted(HANDLERS.keys()))
        return {"error": f"Unknown function: {function}. Available: {func_list}"}

    try:
        if asyncio.iscoroutinefunction(handler):
            return await handler(params, project_root, strict)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: handler(params, project_root, strict))
    except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
        err = str(exc)
        accepted = HANDLER_ACCEPTED_PARAMS.get(canonical_func)
        if accepted:
            unknown = sorted(set(params.keys()) - accepted)
            if unknown:
                err += (
                    f" | Unknown params for '{canonical_func}': {', '.join(unknown)}."
                    f" Accepted: {', '.join(sorted(accepted))}."
                )
        return {"error": err}
    except Exception as exc:
        log.exception("Unhandled exception in handler '%s'", canonical_func)
        return {"error": f"Internal error in '{canonical_func}': {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

PURITY_CALL_TOOL = {
    "name": "purity_call",
    "description": (
        "Project file operations: search, glob, list (ls), write, surgical edits.\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "ABSOLUTE PROHIBITION — Bash(\"find ...\") IS FORBIDDEN, NO EXCEPTIONS\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "You MUST NEVER invoke `find` through Bash. Not with `-name`, not with\n"
        "`-type f`, not with `-iname`, not piped through `xargs`, not wrapped in\n"
        "a `for` loop, not via `$(find ...)`, not via `sh -c 'find ...'`. ANY\n"
        "appearance of the literal command name `find` in a Bash invocation is a\n"
        "VIOLATION. The same prohibition applies to `fd`, `fdfind`, `locate`,\n"
        "`mlocate`, `plocate`, `tree`, ad-hoc Python `os.walk` scripts, and any\n"
        "other text-matching hack used to enumerate files. There is NO\n"
        "legitimate reason to fall back to these — `find_file` covers every\n"
        "Unix `find -name <glob>` / `find <dir> -name <glob>` / `find -type f`\n"
        "wildcard search, returns sandbox-scoped relative paths, skips `.git`,\n"
        "and supports pagination via `head_limit` / `offset`. If you catch\n"
        "yourself typing `find` into Bash, STOP — call `find_file` instead.\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "SEMANTIC CODE NAVIGATION — purity_call now ALSO does symbol work\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "purity_call now provides compiler-accurate (clangd-backed) symbol\n"
        "navigation for C / C++ / CUDA, alongside the file ops above:\n\n"
        "  find_definition       - by symbol name OR file position (symbol/at)\n"
        "  find_references        - by symbol name OR file position\n"
        "  find_implementations   - at a file position\n"
        "  type_at                - hover + deduced (auto/template) type\n"
        "  diagnostics            - compiler warnings/errors for a file\n"
        "  outline                - document symbol outline\n"
        "  symbol                 - workspace symbol search (index + grep fallback)\n"
        "  symbol_context         - definition + references in one call\n"
        "  inlay_hints            - parameter/type hints for a range\n"
        "  symbol_change_impact   - definition + references + call hierarchy\n\n"
        "The clangd LSP spins up lazily on first use. For symbol work PREFER these\n"
        "over grepping source - text matching misses overloads, macros, and\n"
        "indirect references. search_for_pattern remains free-text (literal/regex)\n"
        "search over ANY filetype (comments, log strings, build text) - use it when\n"
        "you want text, not a symbol. The standalone clangd_call / cuda_call tools\n"
        "still exist and run in parallel; the legacy clangd_*/cuda_* function names\n"
        "are accepted here as aliases.\n\n"
        "When NOT to use purity:\n"
        "  - Plain reading        -> built-in Read (less MCP overhead).\n"
        "  - Lua symbols          -> mcp-luals (luals_call).\n"
        "  - Git                  -> mcp-git.\n"
        "  - Build / test / clean -> mcp-forge / mcp-compile.\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "MANDATORY Bash → purity mappings (NEVER call these via Bash):\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "  Bash(\"ls ...\")                   → function=\"ls\"\n"
        "  Bash(\"find ...\")                 → function=\"find_file\" (alias \"glob\")  [FORBIDDEN via Bash]\n"
        "  Bash(\"find . -name ...\")         → function=\"find_file\", params={\"pattern\":\"...\"}\n"
        "  Bash(\"find <dir> -type f -name\") → function=\"find_file\", params={\"pattern\":\"...\",\"path\":\"<dir>\"}\n"
        "  Bash(\"fd ...\") / Bash(\"locate ...\") → function=\"find_file\"  [FORBIDDEN via Bash]\n"
        "  Bash(\"grep ...\")                 → function=\"search_for_pattern\" (alias \"grep\")\n"
        "  Bash(\"rg ...\") / Bash(\"ag ...\") → function=\"search_for_pattern\"  [for non-symbol text only]\n"
        "  Bash(\"cat ...\")                  → built-in Read\n"
        "  Bash(\"sed/awk\")                  → function=\"replace_content\"\n"
        "Also prefer this OVER built-in Write/Edit/Glob/Grep.\n\n"
        "File ops: search_for_pattern (grep), find_file (glob), list_dir (ls),\n"
        "create_text_file, replace_content, replace_lines, delete_lines, insert_at_line,\n"
        "read_file. Semantic (clangd-backed): find_definition, find_references,\n"
        "find_implementations, type_at, diagnostics, outline, symbol, symbol_context,\n"
        "inlay_hints, symbol_change_impact. Project-root-scoped, .gitignore-aware, binary-safe.\n"
        "`find_file` pattern is fnmatch-style (`*.cu`, `test_*.py`, etc.); search\n"
        "root is the optional `path` (alias of `relative_path`, default = project root).\n\n"
        "Examples:\n"
        "  ls -l:      function=\"ls\", params={\"path\":\"src/\",\"long\":true}\n"
        "  find files: function=\"find_file\", params={\"pattern\":\"*.py\",\"path\":\"src/\"}\n"
        "  grep text:  function=\"search_for_pattern\", "
        "params={\"pattern\":\"TODO\",\"path\":\"docs/\",\"glob\":\"*.md\"}\n"
        "Call without 'function' for full function list."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "description": "Function name to call",
            },
            "params": {
                "type": "object",
                "description": "Function parameters",
            },
        },
    },
}


class McpServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

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
                try:
                    response = await self._handle_message(msg)
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

    async def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        # Notifications (no id) — no response
        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mcp-purity", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": [PURITY_CALL_TOOL]})

        if method == "tools/call":
            return await self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    async def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if tool_name != "purity_call":
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
            result = await handle_purity_call(arguments, self.project_root, self.strict)
        except Exception as exc:
            log.exception("Unhandled exception in handle_purity_call")
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
# CLI
# ---------------------------------------------------------------------------

HANDLER_DESCRIPTIONS = {
    "read_file":           "Read file contents (with optional line range)",
    "create_text_file":    "Create or overwrite a file",
    "list_dir":            "List directory contents (recursive, long for size+mtime, glob for fnmatch filter, grep for regex on output, show_hidden for dotfiles, head_limit+offset for pagination; alias: ls)",
    "find_file":           "Find files by wildcard pattern",
    "replace_content":     "Replace text in a file (literal or regex)",
    "delete_lines":        "Delete a range of lines",
    "replace_lines":       "Replace a range of lines with new content",
    "insert_at_line":      "Insert content before a given line",
    "search_for_pattern":  "Regex search across project files (output_mode: files_with_matches|content|count, head_limit, offset)",
    "find_definition":     "Find a symbol's definition by name OR file position (symbol/at)",
    "find_references":      "Find references to a symbol by name OR file position",
    "find_implementations": "Find implementations at a file position",
    "type_at":              "Type/hover at a position (incl. deduced auto/template type)",
    "diagnostics":          "Compiler diagnostics (warnings/errors) for a file",
    "outline":              "Document symbol outline for a file",
    "symbol":               "Search workspace symbols by query (clangd index + grep fallback)",
    "symbol_context":       "Definition + references for a symbol in one call",
    "inlay_hints":          "Inlay hints (parameter/type) for a file range",
    "symbol_change_impact": "Definition + references + call hierarchy for impact analysis",
}


def main() -> None:
    if "--list" in sys.argv:
        print("mcp-purity — available functions:\n")
        for name in sorted(HANDLERS.keys()):
            desc = HANDLER_DESCRIPTIONS.get(name, "")
            print(f"  {name:25s} {desc}")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="MCP-Purity: Pure Python file operations MCP server")
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
