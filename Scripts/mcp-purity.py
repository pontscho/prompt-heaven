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
import json
import logging
import os
import re
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
        if canonical not in resolved:
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
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def handle_purity_call(arguments: dict, project_root: str, strict: bool = False) -> dict:
    """Route a purity_call invocation to the appropriate handler.

    Accepts both long and short keys: function/f, params/p.
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
        return handler(params, project_root, strict)
    except (ValueError, FileNotFoundError, OSError) as exc:
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
        "LANGUAGE-SPECIFIC ROUTING — purity is NOT for symbol navigation\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "For source-code symbol work (definitions, references, types, callers,\n"
        "diagnostics, hover, document outline) purity is the WRONG tool. You\n"
        "MUST route to the language-specific LSP-backed MCP server:\n\n"
        "  • C / C++ / Objective-C (.c .cpp .cc .cxx .h .hpp .hh .hxx .m .mm)\n"
        "        → mcp-clangd  (clangd_call)   — MANDATORY, no substitutes\n"
        "  • CUDA (.cu .cuh)\n"
        "        → mcp-cuda    (cuda_call)     — MANDATORY, no substitutes\n"
        "  • Lua (.lua)\n"
        "        → mcp-luals   (luals_call)    — MANDATORY, no substitutes\n\n"
        "Using `purity_call` (search_for_pattern / find_file) as a substitute\n"
        "for these LSPs is a VIOLATION. grep over `.c`/`.cpp`/`.cu`/`.cuh`/`.lua`\n"
        "files to locate a function, struct, class, kernel, macro, typedef,\n"
        "table field, or callers is FORBIDDEN — those queries are answered\n"
        "compiler-accurately by clangd/cuda/luals, and text matching will miss\n"
        "overloads, macros, and indirect references. purity grep on those\n"
        "extensions is only acceptable for non-symbol content (comments, log\n"
        "strings, license headers, build-system text), and even then prefer\n"
        "the LSP if a symbol is involved.\n\n"
        "When NOT to use purity at all:\n"
        "  - Plain reading       → built-in Read (less MCP overhead).\n"
        "  - C/C++ symbols       → mcp-clangd (clangd_call).\n"
        "  - CUDA symbols        → mcp-cuda   (cuda_call).\n"
        "  - Lua symbols         → mcp-luals  (luals_call).\n"
        "  - Git                 → mcp-git.\n"
        "  - Build / test / clean→ mcp-forge / mcp-compile.\n\n"
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
        "Functions: search_for_pattern (grep), find_file (glob), list_dir (ls),\n"
        "create_text_file, replace_content, replace_lines, delete_lines,\n"
        "insert_at_line, read_file. Project-root-scoped, .gitignore-aware, binary-safe.\n"
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
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
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
            result = handle_purity_call(arguments, self.project_root, self.strict)
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
