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

  --project-root  Required. Sandbox root for file operations.
  --strict        Hard-sandbox: reject ANY path that escapes --project-root
                  (reads included) and reject absolute paths outright.
                  Without --strict (default): destructive ops (create/replace/
                  delete/insert) stay sandboxed to the root, but non-destructive
                  ops (read/search/list/glob/semantic) MAY resolve paths
                  outside the root.
"""

import argparse
import asyncio
import contextvars
import fnmatch
import glob as glob_mod
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mcp-purity")


# ---------------------------------------------------------------------------
# ReDoS guard (F4 / CWE-1333)
# ---------------------------------------------------------------------------

_MAX_REGEX_LEN = 1000          # caller-supplied pattern length ceiling
_SEARCH_DEADLINE_SECS = 5.0    # wall-clock budget for multi-file scan loops


def _check_regex_len(pattern: str, param_name: str = "pattern") -> None:
    """Raise ValueError if *pattern* exceeds the length ceiling."""
    if len(pattern) > _MAX_REGEX_LEN:
        raise ValueError(
            f"Regex pattern too long ({len(pattern)} chars); "
            f"maximum allowed is {_MAX_REGEX_LEN} chars (parameter: {param_name})"
        )


# ---------------------------------------------------------------------------
# Sandbox utility
# ---------------------------------------------------------------------------

def _sanitize_log(s: str) -> str:
    """Strip CR/LF and other control chars from caller-controlled strings before
    interpolating them into log or error messages (CWE-117 / F10)."""
    return s.replace("\r", " ").replace("\n", " ")


# Set by the dispatcher for the duration of a single handler call: True while a
# NON-DESTRUCTIVE handler (read/search/list/glob/semantic) runs, False for the
# destructive ones (create/replace/delete/insert). safe_path reads it to decide
# whether a path resolving outside the root is tolerable. A ContextVar (not a
# plain global) so the value is task/thread-local; the sync file handlers run in
# an executor, so the dispatcher must propagate the context via copy_context().
# Default False = fail-safe: anything not explicitly opted in stays sandboxed.
_ALLOW_OUTSIDE_ROOT: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "purity_allow_outside_root", default=False
)


def safe_path(project_root: str, relative_path: str, strict: bool = False) -> str:
    """Resolve *relative_path* under *project_root* and verify containment.

    A path resolving INSIDE the root is always accepted. A path resolving
    OUTSIDE the root is accepted only for non-destructive ops (the dispatcher
    opts them in via _ALLOW_OUTSIDE_ROOT) and only when the server is not in
    --strict mode; destructive ops always stay sandboxed. --strict is the
    hard-sandbox: it rejects every escape and every absolute path outright
    (F1 / CWE-22).
    """
    if os.path.isabs(relative_path) and strict:
        raise ValueError(_sanitize_log(f"Path escapes project root: {relative_path}"))
    resolved = os.path.realpath(os.path.join(project_root, relative_path))
    root = os.path.realpath(project_root)
    if resolved == root or resolved.startswith(root + os.sep):
        return resolved
    # Path resolves OUTSIDE the root: permit only for read-only ops, and never
    # under --strict.
    if not strict and _ALLOW_OUTSIDE_ROOT.get():
        return resolved
    raise ValueError(_sanitize_log(f"Path escapes project root: {relative_path}"))


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
    "paths": "relative_path",
    "root": "relative_path",
    "pattern": "substring_pattern",
    "line_start": "start_line",
    "line_end": "end_line",
    # replace_content aliases
    "search": "needle",
    "find": "needle",
    "old_string": "needle",
    "old_str": "needle",
    "old": "needle",
    "replacement": "repl",
    "replace": "repl",
    "replace_with": "repl",
    "new_string": "repl",
    "new_str": "repl",
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
        "force": "overwrite",
    },
    "replace_lines": {
        "new_content": "content",
    },
    "insert_at_line": {
        "new_content": "content",
    },
    "list_dir": {
        "long_format": "long",
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
    "temp_dir": "create_temp_dir",
    "mktemp": "create_temp_dir",
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

    # `paths`/`path` are occasionally sent as a list (grep-style multi-root).
    # purity searches a single root, so normalize: a 1-element list collapses to
    # its element, an empty list is dropped (downstream applies the default /
    # required-param rule), and a multi-element list is a hard error since
    # multi-root search is unsupported.
    rp = resolved.get("relative_path")
    if isinstance(rp, list):
        if len(rp) == 1:
            resolved["relative_path"] = rp[0]
        elif not rp:
            resolved.pop("relative_path")
        else:
            raise ValueError(
                "relative_path/paths received a multi-element list; purity "
                "searches a single root. Pass one path or issue separate calls."
            )
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
        raise FileNotFoundError(f"File not found: {_sanitize_log(rel)}")

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
    overwrite = _bool_param(params.get("overwrite", True))

    path = safe_path(project_root, rel, strict)
    if not overwrite and os.path.exists(path):
        raise ValueError(
            f"File already exists: {rel}. Pass overwrite=true (or force=true) to replace it."
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

    nbytes = len(content.encode("utf-8"))
    return {"__raw_text__": f"Created {rel} ({nbytes} bytes)"}


def handle_create_temp_dir(params: dict, project_root: str, strict: bool = False) -> dict:
    """Create the project's ``.claude/tmp`` scratch directory, return its abs path.

    All temporary artifacts (screenshots, debug output, scratch files) belong
    here — never in the project root. Idempotent: creating an already-existing
    directory is a no-op that still returns the absolute path.

    ``subpath`` nests a scratch dir under ``.claude/tmp`` (missing parents are
    created); ``unique=true`` appends a random suffix via mkdtemp so parallel
    callers never collide. The resolved target is fenced under ``.claude/tmp``,
    so a traversing or absolute subpath is rejected instead of escaping.

    Note: writing a temp FILE needs no call here — create_text_file already
    creates missing parent directories. Use this only when something else has
    to write into a directory that must already exist.
    """
    base = safe_path(project_root, os.path.join(".claude", "tmp"), strict)
    sub = str(params.get("subpath") or "").strip()
    if sub:
        if os.path.isabs(sub):
            raise ValueError(
                "'subpath' must be relative to .claude/tmp, not an absolute path."
            )
        sub = sub.rstrip("/\\")
    unique = _bool_param(params.get("unique", False))

    target = os.path.join(base, sub) if sub else base
    if not _path_within_root(pathlib.Path(target), base):
        raise ValueError(_sanitize_log(
            f"'subpath' escapes the scratch directory: {sub!r} must stay under .claude/tmp"
        ))

    if unique:
        parent = os.path.dirname(target) if sub else base
        os.makedirs(parent, exist_ok=True)
        prefix = f"{os.path.basename(target)}-" if sub else "tmp-"
        target = tempfile.mkdtemp(prefix=prefix, dir=parent)
    else:
        os.makedirs(target, exist_ok=True)
    return {"__raw_text__": target}


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
        return {"text": f"(directory does not exist: {_sanitize_log(rel)})", "count": 0}

    if grep_pattern:
        _check_regex_len(grep_pattern, "grep_pattern")
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
        # F3/CWE-1333: aggregate deadline guards the per-name filter loop.
        # Residual: a single catastrophic re.search() call on a long name cannot
        # be preempted in pure-stdlib re — length cap + deadline are the
        # proportionate mitigation for a local single-user tool.
        _grep_deadline = time.monotonic() + _SEARCH_DEADLINE_SECS
        filtered: List[str] = []
        for _l in lines:
            if time.monotonic() > _grep_deadline:
                raise ValueError(
                    f"list_dir grep exceeded time budget ({_SEARCH_DEADLINE_SECS:.0f}s); "
                    "use a more specific grep pattern"
                )
            if grep_re.search(_l):
                filtered.append(_l)
        lines = filtered

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


def _compile_path_glob(mask: str) -> "re.Pattern[str]":
    """Translate a path-style glob to a regex, mirroring Glob semantics.

    ``**`` matches across directory separators (a leading ``**/`` also matches
    zero directories), ``*`` matches within a single path segment, and ``?``
    matches one non-separator character. Used by find_file so callers can pass
    patterns like ``**/*.md`` or ``docs/*.md`` instead of getting no matches.
    """
    i, n = 0, len(mask)
    out: List[str] = []
    while i < n:
        c = mask[i]
        if c == "*":
            if mask[i : i + 2] == "**":
                i += 2
                if mask[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


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

    # A mask containing a separator or a recursive ** is matched against each
    # file's path relative to the search root (Glob semantics); a bare mask
    # matches the basename. Without this, fnmatch on a basename silently
    # returns nothing for path-style patterns like "**/*.md" or "docs/*.md".
    path_style = ("/" in file_mask) or ("**" in file_mask)
    path_re = _compile_path_glob(file_mask) if path_style else None

    matches: List[str] = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if path_style:
                cand = os.path.relpath(full, path).replace(os.sep, "/")
                hit = path_re.match(cand) is not None
            else:
                hit = fnmatch.fnmatch(name, file_mask)
            if hit:
                matches.append(os.path.relpath(full, project_root))

    total = len(matches)
    if offset:
        matches = matches[offset:]
    truncated = False
    if head_limit > 0 and len(matches) > head_limit:
        matches = matches[:head_limit]
        truncated = True

    header = f"Found {total} file(s) matching '{_sanitize_log(file_mask)}'"
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

    _rel = _sanitize_log(rel)
    if mode == "literal":
        count = content.count(needle)
        if count == 0:
            raise ValueError(f"Needle not found in {_rel}")
        if count > 1 and not allow_multiple:
            raise ValueError(
                f"Multiple occurrences ({count}) found in {_rel}. "
                "Set allow_multiple_occurrences=true to replace all."
            )
        new_content = content.replace(needle, repl)
    else:
        _check_regex_len(needle, "needle")
        needle = needle.replace("\\|", "|")
        # F3/CWE-1333: bound content size before running the regex to limit
        # backtracking exposure.  The 10 MB cap matches search_for_pattern's
        # max_file_size default.
        # Residual: a single catastrophic re.finditer/re.sub call on a large
        # input cannot be preempted in pure-stdlib re — the length cap on the
        # pattern + this content-size ceiling are the proportionate mitigation
        # for a local single-user tool running in a thread-pool executor.
        _RC_MAX_CONTENT = 10 * 1024 * 1024  # 10 MB
        if len(content) > _RC_MAX_CONTENT:
            raise ValueError(
                f"File content ({len(content)} bytes) exceeds the {_RC_MAX_CONTENT // (1024*1024)} MB "
                "limit for regex replace; use literal mode or a smaller file"
            )
        matches = list(re.finditer(needle, content))
        if not matches:
            raise ValueError(f"Pattern not found in {_rel}")
        if len(matches) > 1 and not allow_multiple:
            raise ValueError(
                f"Multiple matches ({len(matches)}) found in {_rel}. "
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


# Extensions treated as "source code" by `restrict_search_to_code_files`.
# Serena had this as a single boolean backed by the project's configured
# language; purity is polyglot, so we approximate with a broad source-file
# set. Docs/data/config (.md, .json, .yaml, .txt, ...) are deliberately
# excluded — that is exactly what callers restrict AWAY from.
CODE_FILE_EXTENSIONS = frozenset({
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".m", ".mm",
    ".cu", ".cuh", ".py", ".pyi", ".lua", ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".swift",
    ".rb", ".php", ".pl", ".pm", ".sh", ".bash", ".zsh", ".cs", ".fs",
    ".hs", ".ml", ".mli", ".ex", ".exs", ".erl", ".clj", ".dart", ".r",
    ".sql", ".vim", ".el", ".tcl", ".groovy", ".gradle",
})


def _is_code_file(name: str) -> bool:
    """True if `name` has a source-code extension (see CODE_FILE_EXTENSIONS)."""
    return pathlib.Path(name).suffix.lower() in CODE_FILE_EXTENSIONS


def _glob_matches(rel_path: str, glob: str) -> bool:
    """Match a project-relative path against a caller glob with model-friendly,
    ripgrep/git-like semantics layered over Python fnmatch.

    Plain ``fnmatch(rel_path, glob)`` has two footguns that repeatedly bite
    LLM callers of search_for_pattern:

      1. A bare filename (``requirements.yaml``) only matches a root-level file,
         because the relative path of a nested file carries directory segments
         that the pattern lacks.
      2. The intuitive "any depth" fix ``**/requirements.yaml`` is ALSO wrong —
         fnmatch has no globstar, so ``**`` is just two ``*`` and the pattern
         still requires the literal ``/``, matching nested files ONLY and
         silently missing the root-level one.

    This helper makes both do what the caller meant:

      * matches if EITHER the full relative path OR the basename matches, so a
        bare filename hits at any depth;
      * a leading ``**/`` is treated as globstar ("zero or more directories"),
        so ``**/x`` also matches a root-level ``x``.

    Path-scoped globs (``src/*.c``) stay scoped: the basename of a nested file
    won't spuriously match a glob that carries its own directory component.
    """
    candidates = [glob]
    # globstar: "**/foo" should also match "foo" (zero intervening dirs).
    if glob.startswith("**/"):
        candidates.append(glob[3:])
    base = os.path.basename(rel_path)
    for pat in candidates:
        if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def handle_search_for_pattern(params: dict, project_root: str, strict: bool = False) -> dict:
    pattern_str = params.get("substring_pattern")
    if not pattern_str:
        raise ValueError("Missing required parameter: substring_pattern")

    # `context_lines` (ripgrep -C) seeds both directions; explicit before/after win.
    ctx_both = params.get("context_lines")
    ctx_before = params.get("context_lines_before")
    ctx_after = params.get("context_lines_after")
    if ctx_before is None:
        ctx_before = ctx_both
    if ctx_after is None:
        ctx_after = ctx_both
    ctx_before = int(ctx_before) if ctx_before else 0
    ctx_after = int(ctx_after) if ctx_after else 0
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
    if output_mode == "context":
        # tolerate ripgrep/Grep-style "context" -> content with surrounding lines
        output_mode = "content"
    if output_mode not in ("files_with_matches", "content", "count"):
        raise ValueError("Parameter 'output_mode' must be 'files_with_matches', 'content', or 'count'")

    max_file_size = params.get("max_file_size", 10 * 1024 * 1024)  # default 10 MB
    skip_ignored = _bool_param(params.get("skip_ignored_files", True))
    # Serena-compat: when true, restrict the scan to source-code files
    # (CODE_FILE_EXTENSIONS) and skip docs/data/config. Default false =
    # search everything, matching Serena's default.
    code_only = _bool_param(params.get("restrict_search_to_code_files", False))

    _check_regex_len(pattern_str, "substring_pattern")

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

    # Wall-clock deadline: abort if the aggregate scan exceeds the budget.
    # Checked between files and every 256 lines within a file so a runaway
    # multi-file scan is bounded even when no per-line match fires.
    _deadline = time.monotonic() + _SEARCH_DEADLINE_SECS

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

            # Deadline check between files
            if time.monotonic() > _deadline:
                raise ValueError(
                    f"search exceeded time budget ({_SEARCH_DEADLINE_SECS:.0f}s); "
                    "use a more specific path or pattern"
                )

            # F6 fix / CWE-22: re-contain walked path via realpath so a regular-
            # file symlink inside the repo that resolves outside project_root is
            # NOT opened/read.  In-root files and in-root-resolving symlinks
            # pass the check (realpath is contained under project_root).
            if not _path_within_root(pathlib.Path(full), project_root):
                log.debug("Skipping out-of-root symlink in search walk: %s", full)
                continue

            if include_glob and not _glob_matches(file_rel, include_glob):
                continue
            if exclude_glob and _glob_matches(file_rel, exclude_glob):
                continue
            if code_only and not _is_code_file(name):
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
                    if i % 256 == 0 and time.monotonic() > _deadline:
                        raise ValueError(
                            f"search exceeded time budget ({_SEARCH_DEADLINE_SECS:.0f}s); "
                            "use a more specific path or pattern"
                        )
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
                        if i % 256 == 0 and time.monotonic() > _deadline:
                            raise ValueError(
                                f"search exceeded time budget ({_SEARCH_DEADLINE_SECS:.0f}s); "
                                "use a more specific path or pattern"
                            )
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
            else:
                # F9 / CWE-789: reject absurdly large Content-Length before
                # readexactly to prevent OOM from a buggy/malicious LSP child.
                _LSP_MAX_MESSAGE = 64 * 1024 * 1024  # 64 MB
                if content_length > _LSP_MAX_MESSAGE:
                    log.warning("LSP Content-Length %d exceeds 64 MB ceiling; aborting read",
                                content_length)
                    return None

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
    """Convert a file:// URI to an absolute filesystem path - the exact inverse
    of pathlib.Path.as_uri() / path_to_uri(). Percent-DECODES the path
    (url2pathname is platform-aware: drive letters on Windows, plain unquote on
    POSIX), so a path containing spaces or other reserved chars round-trips
    losslessly instead of carrying literal %20 back into open_document/realpath.

    SECURITY: decoding also normalises encoded traversal - an LSP-returned
    file:///root/%2e%2e/escape decodes to /root/../escape, which the downstream
    realpath / _path_within_root containment checks then collapse and reject.
    The old prefix-strip left %2e%2e literal, slipping past those checks.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    return url2pathname(parsed.path)


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
    if suffix == ".lua":
        return "lua"
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
                    # F7 / CWE-20, CWE-22: reject a CMakeCache-derived SDK path
                    # that resolves INSIDE the reviewed project root — that is a
                    # poisoned-header-tree attack (repo-internal "SDK" directory).
                    # Also require the path to actually exist on the host.
                    resolved_sdk = pathlib.Path(os.path.realpath(str(sdk_root)))
                    if _path_within_root(resolved_sdk, project_root):
                        log.warning(
                            "CMakeCache CUDA SDK path resolves inside project "
                            "root — rejecting (poisoned-header-tree attack): %s",
                            resolved_sdk,
                        )
                    elif not resolved_sdk.exists():
                        log.warning(
                            "CMakeCache CUDA SDK path does not exist on host "
                            "— rejecting: %s", resolved_sdk,
                        )
                    elif (resolved_sdk / "bin" / "nvcc").exists():
                        log.debug("CUDA SDK (CMakeCache, validated outside root): %s",
                                  resolved_sdk)
                        return str(resolved_sdk)
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
    # NOTE: "--options-file" intentionally NOT listed here — the space-form
    # "--options-file <path>" is handled by a dedicated expansion+filter branch
    # inside _translate_compile_commands (F2 fix: previously this entry shadowed
    # that branch, making it dead code).  The "=" prefix form
    # "--options-file=<path>" does not appear in nvcc output.
    "-gencode",
    "--gpu-code=",
    "--gpu-architecture=",
    "-rdc=",
    "-dlink",
    "--device-link",
)

# Flags that enable clangd to load arbitrary shared objects (plugin load) or
# execute external programs — forwarding them from repo build metadata into the
# generated compile_commands.json would give a malicious repo a code-execution
# primitive via the clangd indexing path (F6b / CWE-94, CWE-829).
# KEEP THIS LIST TIGHT: only drop flags with proven code-execution / arbitrary-
# file-read capability.  All other flags (includes, defines, std, warnings,
# optimisation, sanitizers, etc.) must pass through so legitimate indexing works.
CLANGD_EXEC_DENYLIST_EXACT = frozenset({
    "-fplugin",   # bare form (unusual but possible)
    "-load",      # LLVM plugin load; also used in -Xclang -load <so> pair
})

CLANGD_EXEC_DENYLIST_PREFIXES = (
    "-fplugin=",        # -fplugin=<path.so>
    "-fplugin-arg-",    # -fplugin-arg-<plugin>-<key>=<val>
)


def _path_within_root(path: pathlib.Path, project_root: str) -> bool:
    """Return True iff *path* (after realpath) is contained under *project_root*.

    Uses the same canonical containment idiom as safe_path / _lsp_path_in_root
    (F1 / CWE-22): realpath both sides, then require equality or a strict
    startswith(root + sep) so sibling-prefix attacks (/rootX) are blocked.
    """
    try:
        resolved = os.path.realpath(str(path))
        root = os.path.realpath(project_root)
        return resolved == root or resolved.startswith(root + os.sep)
    except Exception:
        return False


def _expand_rsp_file(rsp_path: str, base_dir: str,
                     project_root: Optional[str] = None,
                     _depth: int = 0) -> List[str]:
    """Expand an nvcc response file (--options-file or @file) into flags.

    F6a / CWE-22: if *project_root* is supplied the resolved RSP path MUST be
    contained under it; an out-of-root path (absolute or via ..) is rejected —
    the token is silently dropped and a warning is logged rather than reading
    an arbitrary file on the repo's behalf.

    F1 nesting guard: nested @file tokens inside an RSP file are stripped
    (depth > 0) to prevent a re-expansion bypass of the denylist filter.
    The denylist filtering itself is performed by the caller (_filter_exec_flags)
    on the tokens returned here.
    """
    p = pathlib.Path(rsp_path)
    if not p.is_absolute():
        p = pathlib.Path(base_dir) / p
    if project_root is not None and not _path_within_root(p, project_root):
        log.warning("RSP/options-file path outside project root — skipping: %s", p)
        return []
    try:
        text = p.read_text(encoding="utf-8")
        tokens = text.split()
        if _depth > 0:
            # Nested @file tokens cannot be safely re-expanded without re-running
            # the full denylist; strip them to prevent bypass via nesting.
            nested = [t for t in tokens if t.startswith("@")]
            if nested:
                log.warning(
                    "Dropping %d nested @file token(s) inside RSP '%s' "
                    "to prevent denylist bypass: %s",
                    len(nested), p, nested,
                )
            tokens = [t for t in tokens if not t.startswith("@")]
        return tokens
    except Exception:
        log.debug("Cannot read RSP file: %s", p)
        return []


def _filter_exec_flags(tokens: List[str]) -> List[str]:
    """Apply the CLANGD_EXEC_DENYLIST to a list of flag tokens and return
    a cleaned list with all code-execution / plugin-load flags removed.

    F1 / CWE-94, CWE-829: identical filter logic to the inline loop inside
    _translate_compile_commands, factored into a helper so it can be applied
    to both the top-level arg stream AND to RSP/options-file expanded tokens
    (which arrive AFTER the top-level loop has already run, bypassing the
    per-iteration denylist check).

    Handles:
      - Exact-match flags in CLANGD_EXEC_DENYLIST_EXACT  (e.g. -load)
      - Prefix-match flags in CLANGD_EXEC_DENYLIST_PREFIXES  (e.g. -fplugin=)
      - -Xclang <dangerous-payload> pairs
    Each dropped flag is logged at WARNING level.
    """
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-Xclang" and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if (nxt in CLANGD_EXEC_DENYLIST_EXACT or
                    any(nxt.startswith(px) for px in CLANGD_EXEC_DENYLIST_PREFIXES)):
                log.warning(
                    "Dropping dangerous -Xclang pair from expanded RSP tokens: %s %s",
                    tok, nxt,
                )
                i += 2  # drop both the sentinel and its payload
                continue
        if tok in CLANGD_EXEC_DENYLIST_EXACT:
            log.warning(
                "Dropping dangerous flag from expanded RSP tokens: %s", tok
            )
            i += 1
            continue
        if any(tok.startswith(px) for px in CLANGD_EXEC_DENYLIST_PREFIXES):
            log.warning(
                "Dropping dangerous flag from expanded RSP tokens: %s", tok
            )
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _translate_compile_commands(entries: List[dict], cuda_path: str,
                                cuda_arch: str, base_dir: str,
                                project_root: Optional[str] = None) -> List[dict]:
    """Translate nvcc compile_commands.json entries to clangd-compatible ones.

    F6b / CWE-94, CWE-829: drops CLANGD_EXEC_DENYLIST flags before forwarding
    so a malicious repo cannot load arbitrary shared objects via clangd.
    All other flags (includes, defines, std, warnings, etc.) are kept so
    legitimate indexing is not broken.
    F6a: RSP/options-file paths are constrained to project_root via
    _expand_rsp_file (which calls _path_within_root).
    """
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

        entry_dir = entry.get("directory", base_dir)
        new_args = ["clang++"]
        skip_next = False
        xclang_load_pending = False  # tracks -Xclang seen before -load/-fplugin
        for i, arg in enumerate(args[1:], 1):
            if skip_next:
                skip_next = False
                continue

            if arg in NVCC_STRIP_FLAGS:
                continue
            if any(arg.startswith(p) for p in NVCC_STRIP_PREFIXES):
                continue

            # F6b: drop code-execution / plugin-load flags (denylist).
            # -Xclang passes a single argument to the clang cc1 layer; when
            # the argument is -load or -fplugin it triggers shared-object load.
            # Drop both the -Xclang sentinel and its dangerous payload.
            if arg == "-Xclang" and i + 1 < len(args):
                next_arg = args[i + 1]
                if (next_arg in CLANGD_EXEC_DENYLIST_EXACT or
                        any(next_arg.startswith(px)
                            for px in CLANGD_EXEC_DENYLIST_PREFIXES)):
                    log.warning("Dropping dangerous -Xclang pair from "
                                "compile_commands: %s %s", arg, next_arg)
                    skip_next = True
                    continue

            if arg in CLANGD_EXEC_DENYLIST_EXACT:
                log.warning("Dropping dangerous flag from compile_commands: %s", arg)
                continue

            if any(arg.startswith(px) for px in CLANGD_EXEC_DENYLIST_PREFIXES):
                log.warning("Dropping dangerous flag from compile_commands: %s", arg)
                continue

            # F6a: RSP / options-file expansion — path contained under project_root.
            # F1 fix: apply _filter_exec_flags to ALL expanded tokens so flags
            # smuggled inside an in-root RSP file cannot bypass the denylist
            # (the top-level loop only filtered `arg`; expanded tokens arrived
            # post-filter via new_args.extend).  _expand_rsp_file's nesting guard
            # strips nested @file tokens to prevent re-bypass via nesting.
            if arg == "--options-file" and i + 1 < len(args):
                expanded = _expand_rsp_file(args[i + 1], entry_dir,
                                            project_root=project_root,
                                            _depth=1)
                new_args.extend(_filter_exec_flags(expanded))
                skip_next = True
                continue

            if arg.startswith("@"):
                expanded = _expand_rsp_file(arg[1:], entry_dir,
                                            project_root=project_root,
                                            _depth=1)
                new_args.extend(_filter_exec_flags(expanded))
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
            "directory": entry_dir,
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

    # Fix 4 / cache-dir symlink write guard: after makedirs (which follows
    # existing symlinks), verify the resolved cache dir is actually inside
    # project_root so a repo-controlled .cache symlink cannot redirect the
    # compile_commands.json write to an out-of-root location (CWE-22 write sink).
    if not _path_within_root(pathlib.Path(cache_dir), project_root):
        log.warning(
            "Cache dir '%s' resolves outside project root — skipping "
            "compile_commands.json write (possible symlink escape).", cache_dir
        )
        return cache_dir

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
            os.path.dirname(original),
            project_root=project_root,
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
    """Generate compile_commands.json entries for .cu files when none exists.

    F11 / CWE-22: each walked .cu path is realpath-contained under project_root
    before being emitted.  Symlinks that resolve outside the root are skipped
    so a malicious repo cannot coerce clangd into indexing an out-of-root file.
    """
    cuda_include = os.path.join(cuda_path, "targets", "x86_64-linux", "include")
    if not os.path.isdir(cuda_include):
        cuda_include = os.path.join(cuda_path, "include")

    entries = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "out", "dist")]
        for fname in files:
            if pathlib.Path(fname).suffix.lower() == ".cu":
                full_path = os.path.join(root, fname)
                # F11: realpath containment — symlinked .cu files that resolve
                # outside project_root are dropped to prevent out-of-root reads.
                if not _path_within_root(pathlib.Path(full_path), project_root):
                    log.warning(
                        "Skipping .cu file outside project root "
                        "(possible symlink escape): %s", full_path,
                    )
                    continue
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

    async def type_definition(self, path: str, line: int, char: int) -> Any:
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
# Shared fallback constants (consumed by BaseLspClient.fallback_extensions and
# the filesystem grep helpers _find_files_with_word / _fallback_workspace_symbols
# defined below; kept here so the class default can reference them).
# ===========================================================================

_FALLBACK_EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".hxx", ".m", ".mm", ".cu", ".cuh")
_FALLBACK_SKIP_DIRS = {
    "build", "vendor", "third_party", "third-party", "node_modules",
    ".git", ".cache", ".clangd", ".ccache", "_deps",
}


# ===========================================================================
# BaseLspClient  (shared concrete LSP machinery + per-language hooks)
# ===========================================================================

class BaseLspClient(LspBackend):
    """Concrete shared LSP client: transport, request/response correlation,
    background reader loop, document priming and the standard textDocument/*
    wrappers. ClangdClient and LuaLsClient inherit this and override only the
    handshake (start) plus a small per-language divergence layer.

    Divergence layer - hooks/attributes consulted by the module-level semantic
    helpers (NOT by the handlers directly), so the handlers stay
    backend-agnostic:

    * ``supports_call_hierarchy`` - prepare/incoming/outgoing call hierarchy is
      available (clangd: True, luals: False).
    * ``fallback_extensions`` - extensions the grep-based symbol fallback walks
      (C/C++/ObjC set for clangd, (".lua",) for luals).
    * ``prime_extensions`` - extensions _prime_index opens to seed the index.
    * ``_language_id(path)`` - the LSP languageId for textDocument/didOpen.
    * ``infer_type(text)`` - turn a hover payload into a type string.
    * ``supplemental_references(...)`` - extra (non-LSP) references merged into
      a name-based references query; no-op here.

    The reader loop handles the shared cases ($/progress, publishDiagnostics,
    request/response correlation) inline and routes backend-specific
    server->client requests and unhandled notifications to the protected
    ``_handle_server_request`` / ``_handle_unknown_notification`` hooks.
    """

    # --- per-language divergence layer (overridden by subclasses) ---------
    supports_call_hierarchy: bool = True
    fallback_extensions: tuple = _FALLBACK_EXTS
    prime_extensions: tuple = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".cu", ".cuh")

    def _language_id(self, path: str) -> str:
        """LSP languageId for a path; default is clangd's CUDA-aware detector."""
        return _detect_language(path)

    def infer_type(self, text: str) -> str:
        """Derive a type string from a hover payload (C++-aware by default)."""
        return _infer_type(text)

    async def supplemental_references(self, symbol_name: str, seen: set,
                                      remaining: int,
                                      preferred_path: Optional[str] = None) -> List[dict]:
        """Extra (non-LSP) references merged into a name-based query. No-op base
        (clangd); LuaLsClient folds the Lua dynamic-dispatch text-grep here."""
        return []

    # --- shared state -----------------------------------------------------
    def __init__(self) -> None:
        self.project_root: str = ""
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_id: int = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._diagnostics: Dict[str, List] = {}       # uri -> list[diagnostic]
        self._diag_events: Dict[str, asyncio.Event] = {}
        # _doc_state: resolved-abs-path STRING -> {"uri": str, "mtime_ns": int,
        # "version": int}. Path key (not URI) so revalidation can stat() the
        # stored paths without hitting the as_uri()/uri_to_path percent-encode
        # asymmetry; the uri is kept in the value for notifications.
        self._doc_state: Dict[str, dict] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._indexing_done: asyncio.Event = asyncio.Event()
        self._active_progress: set = set()   # tokens with begin but no end yet
        self._send_lock = asyncio.Lock()
        # Crash sentinel: flipped True by _reader_loop on stdout EOF. Together
        # with process.returncode it lets _client_is_alive evict a dead backend
        # (a bare `process is not None` check treated the corpse as live, so the
        # whole server wedged until a manual restart). Never reset in place - a
        # recovered backend is always a freshly constructed client object.
        self._backend_dead: bool = False

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
        self._doc_state.clear()
        self._diagnostics.clear()

    async def _prime_index(self) -> None:
        """Open a sample of source files so the workspace index is populated.
        Globs self.prime_extensions (clangd's 8-ext set by default, (".lua",)
        for luals) - distinct from fallback_extensions."""
        prime_exts = set(self.prime_extensions)
        source_files = []
        for root, _, files in os.walk(self.project_root):
            if any(part.startswith(".") or part in ("build", "out", "dist", ".git")
                   for part in pathlib.Path(root).parts):
                continue
            for fname in files:
                if pathlib.Path(fname).suffix.lower() in prime_exts:
                    # CWE-22/CWE-59: re-contain via realpath before queuing for
                    # open_document, which resolves symlinks and reads content.
                    # In-root files and in-root-resolving symlinks pass; only
                    # out-of-root symlink escapes are skipped.
                    candidate = pathlib.Path(os.path.join(root, fname))
                    if not _path_within_root(candidate, self.project_root):
                        log.debug("Skipping out-of-root symlink in prime walk: %s", candidate)
                        continue
                    source_files.append(str(candidate))
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

    _PROGRESS_START_GRACE = 2.0      # how long to wait for indexing to ANNOUNCE itself
    _INDEX_BARRIER_TIMEOUT = 60.0    # cap, applied only once it HAS announced itself

    async def _await_indexing(self,
                              grace: float = _PROGRESS_START_GRACE,
                              timeout: float = _INDEX_BARRIER_TIMEOUT) -> None:
        """Block only while the backend says it is indexing.

        A server with nothing to index (clangd with no compilation database,
        luals on a small workspace) never sends a $/progress begin/end pair, so
        the old unconditional 60s wait could only expire. Wait a short grace
        period for indexing to announce itself (window/workDoneProgress/create or
        $/progress begin, both of which fill _active_progress); if nothing
        announced, return immediately. Semantics are unchanged when indexing is
        real: we then wait for the 'end' that empties _active_progress, capped.
        """
        loop = asyncio.get_running_loop()
        announce_deadline = loop.time() + grace
        while not self._active_progress and not self._indexing_done.is_set():
            if loop.time() >= announce_deadline:
                log.debug("No indexing progress announced in %.1fs - proceeding", grace)
                return
            await asyncio.sleep(0.05)
        try:
            await asyncio.wait_for(self._indexing_done.wait(), timeout=timeout)
            log.debug("Background indexing done.")
        except asyncio.TimeoutError:
            log.debug("Indexing wait capped at %.0fs - proceeding", timeout)

    async def _reader_loop(self) -> None:
        """Background task: read all LSP messages and route them. Shared cases
        ($/progress, publishDiagnostics, request/response correlation) are
        handled here; backend-specific server->client requests and unknown
        notifications dispatch to the protected hooks."""
        assert self.process and self.process.stdout
        reader = self.process.stdout
        while True:
            msg = await read_lsp_message(reader)
            if msg is None:
                log.debug("LSP backend stdout EOF")
                # Mark the backend dead so _client_is_alive evicts it and the
                # next semantic call auto-restarts, even if the OS process has
                # not been reaped yet (returncode still None) or lingers with a
                # closed stdout.
                self._backend_dead = True
                # Fail every outstanding request so callers get a prompt error
                # instead of blocking until their per-request timeout when the
                # backend dies mid-flight.
                for req_id, fut in list(self._pending.items()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("LSP backend terminated unexpectedly"))
                    self._pending.pop(req_id, None)
                break

            msg_id = msg.get("id")
            method = msg.get("method", "")

            if msg_id is not None and ("result" in msg or "error" in msg):
                fut = self._pending.pop(msg_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)

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

            elif msg_id is not None and method:
                # Server -> client REQUEST (needs a reply); backend-specific.
                await self._handle_server_request(msg)

            elif method:
                self._handle_unknown_notification(msg)

    async def _handle_server_request(self, msg: dict) -> None:
        """Reply to a server->client request. No-op base (logs only); subclasses
        answer backend-specific requests (clangd's
        window/workDoneProgress/create, luals's workspace/configuration)."""
        log.debug(f"Unhandled server request: {msg.get('method')}")

    def _handle_unknown_notification(self, msg: dict) -> None:
        """Handle a notification the base loop does not recognise. No-op base;
        subclasses drop backend-specific chatter (luals's $/status/*)."""
        log.debug(f"Unhandled notification: {msg.get('method')}")

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
        """Open a document (first sight) or refresh it if it changed on disk,
        and revalidate every other already-open document so a sibling file's
        edit can't leave the index stale."""
        abs_path = pathlib.Path(path)
        if not abs_path.is_absolute():
            abs_path = pathlib.Path(self.project_root) / abs_path
        abs_path = abs_path.resolve()
        # Containment gate: skip any path that resolves outside project_root.
        # This closes the open_document re-feed sites (definition/references/
        # call-hierarchy) so an LSP-returned out-of-root URI never reaches
        # read_text, mirroring the _lsp_path_in_root / _prime_index guards.
        # Fail-safe when project_root is not yet set (empty string).
        if not self.project_root or not _path_within_root(abs_path, self.project_root):
            log.debug("open_document: skipping out-of-root path %s", abs_path)
            return
        uri = abs_path.as_uri()
        # 1) cross-file freshness: revalidate the OTHER already-open documents so
        #    a sibling file's on-disk change can't stay stale in the index.
        await self._refresh_stale_documents(exclude=str(abs_path))
        # 2) open / refresh the requested document itself.
        await self._sync_document(abs_path, uri)

    async def _sync_document(self, abs_path: pathlib.Path, uri: str) -> None:
        """Open a doc on first sight, or push a full-text didChange (+ watched-
        files event) if its on-disk mtime changed. No-op when unchanged.

        NOTE: _doc_state is mutated and notifications are sent without an
        enclosing lock. This is safe ONLY because the MCP main dispatch loop
        (run()) processes stdin messages strictly serially - no per-query task
        is spawned, so two queries never touch the same client concurrently. If
        dispatch is ever made concurrent, this becomes a TOCTOU/double-didChange
        race and needs a lock.
        """
        try:
            mtime_ns = abs_path.stat().st_mtime_ns
        except OSError:
            return
        # SECURITY: re-check containment on EVERY read path. open_document gates
        # new paths, but _refresh_stale_documents re-reads stored paths directly
        # - guard against a stored path being swapped for an out-of-root symlink
        # (TOCTOU), mirroring the _path_within_root / _lsp_path_in_root family.
        if not self.project_root or not _path_within_root(abs_path, self.project_root):
            log.debug("_sync_document: skipping out-of-root path %s", abs_path)
            return
        key = str(abs_path)
        state = self._doc_state.get(key)

        if state is not None and state["mtime_ns"] == mtime_ns:
            return  # unchanged -> no-op (replaces the old _opened_files early-return)

        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            log.debug(f"Cannot read {abs_path}: {e}")
            return

        if state is None:
            # first open
            self._doc_state[key] = {"uri": uri, "mtime_ns": mtime_ns, "version": 1}
            await self._notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": self._language_id(str(abs_path)),
                    "version": 1,
                    "text": content,
                }
            })
        else:
            # changed on disk -> full-text didChange + watched-files event
            version = state["version"] + 1
            state.update(mtime_ns=mtime_ns, version=version)
            await self._notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],   # full-sync (no range)
            })
            await self._notify("workspace/didChangeWatchedFiles", {
                "changes": [{"uri": uri, "type": 2}],     # FileChangeType.Changed
            })

    async def _refresh_stale_documents(self, exclude: Optional[str] = None) -> None:
        """Re-stat every already-open document; push didChange+watched-files for
        any that changed on disk. Cheap (stat per open doc); notifications only
        on change. Calls _sync_document directly (NOT open_document) so it never
        recurses back into the refresh fan-out."""
        for key in list(self._doc_state.keys()):
            if key == exclude:
                continue
            p = pathlib.Path(key)
            await self._sync_document(p, self._doc_state[key]["uri"])

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

    async def type_definition(self, path: str, line: int, char: int) -> Any:
        """Go to the definition of the TYPE of the symbol under the cursor.

        Shared by both backends: clangd answers it for C/C++/ObjC/CUDA (variable
        -> its struct/class/enum/typedef), lua-language-server for Lua (value ->
        its ``@class`` / annotated declaration). Same Location | Location[] |
        LocationLink[] payload shape as textDocument/definition, so the caller
        normalizes it with the same helper.
        """
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/typeDefinition", {
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
# ClangdClient  (C/C++/CUDA in one backend; language is config)
# ===========================================================================

class ClangdClient(BaseLspClient):
    """Async LSP client for clangd with background reader and push-notification
    support. CUDA-aware: when cuda_path is supplied to start(), compile_commands
    are translated for clangd via the CUDA CONFIG helpers above. Inherits the
    shared transport / reader / textDocument wrappers and the C++ hook defaults
    (supports_call_hierarchy=True, the C/C++/ObjC fallback_extensions, the 8-ext
    prime set, _detect_language / _infer_type) from BaseLspClient; adds only the
    clangd handshake, the workDoneProgress/create reply, and call hierarchy.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cuda_path: str = ""
        self.cuda_arch: str = ""

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
                    # Explicit didOpen/didChange signalling so full-text syncs
                    # are protocol-correct; willSave/didSave unused (we never
                    # mutate buffers, only mirror on-disk content).
                    "synchronization": {
                        "dynamicRegistration": False,
                        "didSave": False,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                    },
                },
                # We statically send workspace/didChangeWatchedFiles ourselves;
                # dynamicRegistration: False keeps the server from issuing a
                # client/registerCapability (which _handle_server_request does
                # NOT ack - it would hang). clangd processes the event either
                # way (preamble invalidation + background-index rebuild).
                "workspace": {
                    "didChangeWatchedFiles": {"dynamicRegistration": False},
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

        await self._await_indexing()

        await self._prime_index()

        version = response.get("result", {}).get("serverInfo", {})
        mode = "CUDA" if cuda_mode else "C/C++"
        return f"clangd initialized ({mode}) at {self.project_root} - {version}"

    async def _handle_server_request(self, msg: dict) -> None:
        """clangd's window/workDoneProgress/create: register the progress token
        (so a $/progress 'end' can flip _indexing_done) and acknowledge the
        request. Any other server->client request falls through to a debug log."""
        if msg.get("method") == "window/workDoneProgress/create":
            token = msg.get("params", {}).get("token", "")
            if token:
                self._active_progress.add(token)
            await self._send({"jsonrpc": "2.0", "id": msg.get("id"), "result": None})
        else:
            log.debug(f"Unhandled server request: {msg.get('method')}")

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


# ===========================================================================
# LuaLsClient  (Lua via lua-language-server)
# ===========================================================================

class LuaLsClient(BaseLspClient):
    """Async LSP client for lua-language-server. Folded from mcp-lua-lsp.py.

    Divergence from BaseLspClient / ClangdClient:
    * supports_call_hierarchy = False  — luals does not implement call hierarchy.
    * fallback_extensions / prime_extensions = ('.lua',)
    * _language_id always returns 'lua'.
    * infer_type returns raw hover text (luals hover is already human-readable).
    * supplemental_references calls _lua_text_references (grep supplement) to
      cover dynamic-dispatch patterns that LSP can miss.
    * start() sends BOTH the initializationOptions.Lua config block AND a
      post-init workspace/didChangeConfiguration re-push — dropping either
      stalls luals up to the 90-second timeout.
    * _handle_server_request answers luals's workspace/configuration requests
      with [None]*len(items) (required; without it luals blocks).
    * _handle_unknown_notification silently drops $/status/report|refresh|click.

    NOTE: the shutil.which missing-binary guard lives in _init_backend
    (task-008), not here.
    """

    # --- per-language divergence layer ------------------------------------
    supports_call_hierarchy: bool = False
    fallback_extensions: tuple = (".lua",)
    prime_extensions: tuple = (".lua",)

    def __init__(self) -> None:
        super().__init__()

    def _language_id(self, path: str) -> str:  # noqa: ARG002
        """luals only handles Lua; languageId is always 'lua'."""
        return "lua"

    def infer_type(self, text: str) -> str:
        """luals hover payloads are already human-readable; return raw text."""
        return text.strip()

    async def supplemental_references(
        self, symbol_name: str, seen: set, remaining: int,
        preferred_path: Optional[str] = None,  # noqa: ARG002
    ) -> List[dict]:
        """Lua dynamic-dispatch grep supplement merged into name-based queries."""
        return await self._lua_text_references(symbol_name, seen, remaining)

    # --- handshake --------------------------------------------------------
    async def start(self, project_root: str,
                    luals_path: str = "lua-language-server",
                    config_path: Optional[str] = None) -> str:
        """Launch lua-language-server, perform LSP handshake, wait for indexing.

        Two config pushes are intentional and REQUIRED:
        1. initializationOptions.Lua — picked up before the server reads the
           workspace; some versions honour only this path.
        2. workspace/didChangeConfiguration re-push after 'initialized' —
           required by servers that ignored initializationOptions (e.g. when
           the client negotiates workspace/configuration capability). Dropping
           either push silently stalls luals for up to 90 seconds.
        """
        if self.process is not None:
            return "already initialized"

        self.project_root = str(pathlib.Path(project_root).resolve())
        self._indexing_done.clear()

        args = [luals_path]
        if config_path:
            args.extend(["--configpath", str(pathlib.Path(config_path).resolve())])

        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
            cwd=self.project_root,
        )
        log.debug(f"lua-language-server PID: {self.process.pid}")

        self._reader_task = asyncio.create_task(self._reader_loop())

        init_params = {
            "processId": os.getpid(),
            "rootUri": path_to_uri(self.project_root),
            "workspaceFolders": [{"uri": path_to_uri(self.project_root), "name": "workspace"}],
            # Config push #1: initializationOptions — honoured before the
            # server reads workspace files in some luals versions.
            "initializationOptions": {
                "Lua": {
                    "hint": {
                        "enable": True,
                        "paramName": "All",
                        "paramType": True,
                        "setType": True,
                        "arrayIndex": "Auto",
                        "await": True,
                    },
                    "diagnostics": {"enable": True},
                    "workspace": {"checkThirdParty": False},
                }
            },
            "capabilities": {
                "general": {"positionEncodings": ["utf-8", "utf-16"]},
                "textDocument": {
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {"linkSupport": True},
                    "typeDefinition": {"linkSupport": True},
                    "implementation": {"linkSupport": True},
                    "references": {},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "publishDiagnostics": {"relatedInformation": True},
                    "inlayHint": {"dynamicRegistration": True},
                    # Explicit didOpen/didChange signalling so full-text syncs
                    # are protocol-correct; willSave/didSave unused (we never
                    # mutate buffers, only mirror on-disk content).
                    "synchronization": {
                        "dynamicRegistration": False,
                        "didSave": False,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                    },
                },
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                    "symbol": {"symbolKind": {"valueSet": list(range(1, 27))}},
                    # Statically-sent watched-files notifications (see clangd
                    # note); dynamicRegistration: False avoids an unacked
                    # client/registerCapability round-trip.
                    "didChangeWatchedFiles": {"dynamicRegistration": False},
                },
                "window": {"workDoneProgress": True},
            },
        }

        response = await self._request("initialize", init_params, timeout=30.0)
        if "error" in response:
            raise RuntimeError(f"lua-language-server initialize failed: {response['error']}")

        await self._notify("initialized", {})

        # Config push #2: workspace/didChangeConfiguration re-push — required
        # by luals versions that ignored initializationOptions (e.g. when the
        # client negotiates the workspace/configuration capability). Dropping
        # this push stalls luals for up to 90 seconds.
        await self._notify("workspace/didChangeConfiguration", {
            "settings": {
                "Lua": {
                    "hint": {
                        "enable": True,
                        "paramName": "All",
                        "paramType": True,
                        "setType": True,
                        "arrayIndex": "Auto",
                        "await": True,
                    },
                    "diagnostics": {"enable": True},
                    "workspace": {"checkThirdParty": False},
                }
            }
        })

        await self._await_indexing()

        await self._prime_index()

        version = response.get("result", {}).get("serverInfo", {})
        return f"lua-language-server initialized at {self.project_root} - {version}"

    # --- reader-loop hooks ------------------------------------------------
    async def _handle_server_request(self, msg: dict) -> None:
        """Answer luals's workspace/configuration requests and the shared
        window/workDoneProgress/create request.

        workspace/configuration: luals sends this during and after init to
        collect per-folder settings.  We reply with [None]*len(items) (one null
        per requested item) — the equivalent of 'no override, use defaults'.
        Without this reply luals blocks waiting for an answer.
        """
        method = msg.get("method", "")
        if method == "workspace/configuration":
            req_id = msg.get("id")
            items = msg.get("params", {}).get("items", [])
            await self._send({"jsonrpc": "2.0", "id": req_id, "result": [None] * len(items)})
        elif method == "window/workDoneProgress/create":
            token = msg.get("params", {}).get("token", "")
            if token:
                self._active_progress.add(token)
            await self._send({"jsonrpc": "2.0", "id": msg.get("id"), "result": None})
        else:
            log.debug(f"Unhandled server request: {method}")

    def _handle_unknown_notification(self, msg: dict) -> None:
        """Drop luals's status-bar chatter; log everything else.

        luals emits $/status/report, $/status/refresh and $/status/click as
        UI-only progress signals.  They carry no actionable information for a
        headless client, so we silently discard them.
        """
        method = msg.get("method", "")
        if method in ("$/status/report", "$/status/refresh", "$/status/click"):
            return  # status-bar noise — discard silently
        log.debug(f"Unhandled notification: {method}")

    # --- Lua text-grep supplement -----------------------------------------
    async def _lua_text_references(
        self, symbol_name: str, seen: set, remaining: int
    ) -> List[dict]:
        """Fallback text scan over .lua files for word-boundary occurrences.

        Supplements LSP textDocument/references results to cover dynamic-
        dispatch patterns that a static server may miss (e.g. method calls
        through a table stored in a variable whose type luals cannot resolve).

        Parameters
        ----------
        symbol_name:
            The bare symbol name to search for (word-boundary match).
        seen:
            Set of already-collected reference keys (``uri:line`` strings).
            Entries in *seen* are skipped to avoid duplication.
        remaining:
            Maximum number of NEW references to return (0 = unlimited).

        Returns a list of {uri, range} dicts in LSP shape so the caller can
        format them identically to LSP-sourced references.
        """
        loop = asyncio.get_running_loop()
        # Run the synchronous walk in the default thread executor so we don't
        # block the event loop on large Lua workspaces.
        return await loop.run_in_executor(
            None,
            self._lua_text_references_sync,
            symbol_name, seen, remaining,
        )

    def _lua_text_references_sync(
        self, symbol_name: str, existing_keys: set, max_remaining: int
    ) -> List[dict]:
        """Synchronous inner worker called from _lua_text_references."""
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        results: List[dict] = []
        skip_dirs = {"build", "out", "dist", ".git", "node_modules", "vendor"}

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip_dirs]
            for fname in files:
                if not fname.endswith(".lua"):
                    continue
                abs_path = os.path.join(root, fname)
                # F6 fix / CWE-22: re-contain walked path so a regular-file
                # symlink inside the repo resolving outside project_root is not
                # opened/read.  In-root files and in-root-resolving symlinks pass.
                if not _path_within_root(pathlib.Path(abs_path), self.project_root):
                    log.debug(
                        "Skipping out-of-root symlink in lua-text-refs walk: %s",
                        abs_path,
                    )
                    continue
                try:
                    with open(abs_path, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except Exception:
                    continue

                uri = pathlib.Path(abs_path).as_uri()
                for lineno, line_text in enumerate(content.splitlines()):
                    match = pattern.search(line_text)
                    if not match:
                        continue
                    stripped = line_text.lstrip()
                    if stripped.startswith("--") and not stripped.startswith("---"):
                        # Pure line comment — skip; but keep luadoc lines '---'.
                        continue
                    key = f"{uri}:{lineno}"
                    if key in existing_keys:
                        continue
                    col = match.start()
                    results.append({
                        "uri": uri,
                        "range": {
                            "start": {"line": lineno, "character": col},
                            "end": {"line": lineno, "character": col + len(symbol_name)},
                        },
                    })
                    if max_remaining > 0 and len(results) >= max_remaining:
                        return results

        return results


# ===========================================================================
# Location formatting helpers
# ===========================================================================

def _lsp_path_in_root(uri: str, project_root: str) -> Optional[str]:
    """Return the absolute path for *uri* only if it resolves inside
    *project_root*; return None otherwise (F6 / CWE-22).

    Used to re-contain LSP-returned URIs before opening them so that a
    malicious indexed repo cannot coerce the server into reading files
    outside the project root.
    """
    abs_path = os.path.realpath(uri_to_path(uri))
    root = os.path.realpath(project_root)
    if abs_path == root or abs_path.startswith(root + os.sep):
        return abs_path
    return None


def _format_location(uri: str, lsp_range: dict, project_root: str) -> dict:
    rel = _relative_path(uri, project_root)
    start = lsp_range.get("start", {})
    end = lsp_range.get("end", {})
    # F6: only read line_text for paths that resolve inside the project root.
    _safe_abs = _lsp_path_in_root(uri, project_root)
    return {
        "path": rel,
        "uri": uri,
        "range": lsp_range,
        "range_human": {
            "start": {"line": start.get("line", 0) + 1, "character": start.get("character", 0) + 1},
            "end": {"line": end.get("line", 0) + 1, "character": end.get("character", 0) + 1},
        },
        "line_text": _get_line(_safe_abs, start.get("line", 0)) if _safe_abs else None,
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
# (_FALLBACK_EXTS / _FALLBACK_SKIP_DIRS are defined above next to BaseLspClient,
#  whose fallback_extensions default references _FALLBACK_EXTS.)


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
            # F6 fix / CWE-22: re-contain walked path so a regular-file symlink
            # inside the repo that resolves outside root is not read.  In-root
            # files and in-root-resolving symlinks pass (realpath stays in root).
            if not _path_within_root(pathlib.Path(full), root):
                log.debug("Skipping out-of-root symlink in word-search walk: %s", full)
                continue
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


async def _fallback_workspace_symbols(client: LspBackend, query: str,
                                      limit: int = 50) -> List[dict]:
    """Locate symbols clangd's global index drops (notably static-inline in
    headers) by grepping the project for the identifier, then asking clangd for
    the real DocumentSymbol of each candidate file and filtering by name.
    """
    candidates = _find_files_with_word(client.project_root, query, exts=client.fallback_extensions, limit=20)
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

async def _symbol_to_location(client: LspBackend, symbol_name: str,
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
        candidates = _find_files_with_word(client.project_root, symbol_name, exts=client.fallback_extensions, limit=10)
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

async def _collect_call_hierarchy(client: LspBackend, path: str, line: int,
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


async def _expand_hierarchy_item(client: LspBackend, item: dict, depth: int,
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

_backends: Dict[str, LspBackend] = {}
_backend_init_tasks: Dict[str, "asyncio.Task"] = {}
_backend_init_failed: Dict[str, float] = {}     # backend_type -> loop.time() of last failure
_INIT_FAILURE_BACKOFF = 30.0                    # seconds
_CLANGD_FILETYPES = {"c", "cpp", "cuda"}
_LUALS_FILETYPES = {"lua"}
# CWE-426 mitigation: resolved absolute paths for LSP binaries.  Set by main()
# from --clangd-path / --luals-path CLI overrides before the server starts.
# None means "use shutil.which at init time" (PATH-ordering fallback; still
# pinned to the absolute path that shutil.which returns).
_clangd_binary_override: Optional[str] = None
_luals_binary_override: Optional[str] = None


# ---------------------------------------------------------------------------
# Backend liveness & recovery (crash auto-restart + explicit restart/reindex)
# ---------------------------------------------------------------------------

# Background reaper tasks for crashed/replaced backends, referenced here so the
# event loop does not GC them mid-terminate ("Task was destroyed but it is
# pending").  Entries remove themselves via an add_done_callback.
_reaper_tasks: set = set()


def _client_is_alive(client: Optional[LspBackend]) -> bool:
    """True only when *client* has a spawned process that has NOT exited.

    Liveness used to be a bare ``client.process is not None`` check, which
    treated a crashed clangd (process object still referenced, but exited or
    stdout-EOF'd) as live - so every later call reused the corpse until a manual
    MCP restart.  We now additionally require the OS process to still be running
    (``returncode is None``, kept current by asyncio's child watcher) and the
    reader loop not to have seen a stdout EOF (``_backend_dead``).
    """
    if client is None:
        return False
    proc = getattr(client, "process", None)
    if proc is None or proc.returncode is not None:
        return False
    return not getattr(client, "_backend_dead", False)


def _drop_backend(backend_type: str) -> Optional[LspBackend]:
    """Synchronously unregister *backend_type* and clear its init/backoff
    bookkeeping, returning the removed client (the caller reaps its process) or
    None when nothing was registered.

    Deliberately contains no ``await``: it keeps _ensure_backend's coalescing
    guard race-free (that check-and-create relies on there being no await
    between the _backends lookup and the init-task creation).  Clearing
    _backend_init_failed here means a crash gets an immediate re-init attempt
    instead of being masked by the init-failure backoff window - that window
    still re-engages if the fresh init itself fails (see _ensure_backend).
    """
    _backend_init_tasks.pop(backend_type, None)
    _backend_init_failed.pop(backend_type, None)
    return _backends.pop(backend_type, None)


async def _safe_stop(client: LspBackend) -> None:
    """Best-effort teardown of a client's subprocess; never propagates."""
    try:
        await client.stop()
    except Exception as exc:      # teardown must not surface to callers
        log.warning("Error stopping LSP backend: %s", exc)


def _reap_backend_process(client: LspBackend) -> None:
    """Terminate a dead/replaced client's process in the background so the
    auto-recovery path - which must stay non-blocking to preserve the
    coalescing invariant - does not wait on clangd's terminate/kill."""
    task = asyncio.ensure_future(_safe_stop(client))
    _reaper_tasks.add(task)
    task.add_done_callback(_reaper_tasks.discard)


def _wipe_clangd_index_cache(project_root: str) -> bool:
    """Delete clangd's on-disk background-index cache so the next start rebuilds
    the index from scratch.  clangd defaults this to ``<root>/.cache/clangd``;
    we only ever remove that exact in-root directory.  Returns True iff a cache
    directory was actually removed.
    """
    cache_dir = pathlib.Path(project_root) / ".cache" / "clangd"
    # CWE-22/CWE-59: only remove when the realpath stays inside project_root, so
    # a symlinked .cache/clangd cannot be used to delete an out-of-root target.
    if not _path_within_root(cache_dir, project_root):
        log.warning("Refusing to wipe clangd cache outside project root: %s", cache_dir)
        return False
    real = pathlib.Path(os.path.realpath(str(cache_dir)))
    if real.is_dir():
        shutil.rmtree(real, ignore_errors=True)
        log.debug("Wiped clangd index cache: %s", real)
        return True
    return False


def _route_filetype(filetype: str) -> Optional[str]:
    """Map a languageId (_detect_language output) to a backend TYPE, or None."""
    if filetype in _CLANGD_FILETYPES:
        return "clangd"
    if filetype in _LUALS_FILETYPES:
        return "luals"
    return None


# ---------------------------------------------------------------------------
# Dispatcher backend-hint helpers (task-009)
# ---------------------------------------------------------------------------

# Maps a called function-name prefix to the filetype that selects the right
# backend.  luals_* legacy names (registered as direct HANDLERS entries in
# task-010) carry this hint so path-less calls reach luals instead of "cpp".
_PREFIX_BACKEND: Dict[str, str] = {
    "luals_": "lua",
}


def _backend_hint(function: str) -> Optional[str]:
    """Return the filetype hint for *function* based on its name prefix, or None."""
    for prefix, filetype in _PREFIX_BACKEND.items():
        if function.startswith(prefix):
            return filetype
    return None


def _select_filetype(params: dict, abs_path: str) -> str:
    """Choose the filetype for backend selection.

    Priority order:
    1. Explicit ``_backend`` hint injected by the dispatcher (luals_* prefix).
    2. Language detected from *abs_path* when a path is available.
    3. Fall back to "cpp" (preserves existing clangd behaviour for callers
       that provide no path and no prefix hint).
    """
    hint = params.get("_backend")
    if hint:
        return hint
    if abs_path:
        return _detect_language(abs_path)
    return "cpp"


def _require_backend(filetype: str) -> LspBackend:
    """Return the live backend for *filetype* or raise if not initialized.

    Sync accessor for callers that must NOT trigger init (status, no-op handlers).
    The semantic handlers use the async _ensure_backend trigger instead.
    """
    backend_type = _route_filetype(filetype)
    if backend_type is None:
        raise ValueError(f"No LSP backend for filetype '{filetype}'")
    client = _backends.get(backend_type)
    if not _client_is_alive(client):
        raise RuntimeError(f"LSP backend '{backend_type}' not initialized for filetype '{filetype}'")
    return client


# CWE-426 hardening: operator-trusted install locations, scanned BEFORE the
# PATH lookup so a hostile earlier-PATH entry cannot hijack the spawn when the
# binary exists in a standard system location.  Order = most-specific first.
_TRUSTED_LSP_BIN_DIRS = (
    "/opt/homebrew/bin",   # Homebrew (Apple Silicon)
    "/usr/local/bin",      # Homebrew (Intel) / common local installs
    "/usr/bin",            # system package manager
    "/opt/local/bin",      # MacPorts
)


def _resolve_lsp_binary(override: Optional[str], default_name: str) -> str:
    """CWE-426 helper: return a validated absolute path for an LSP binary.

    Resolution order:
    1. If *override* is set (from --clangd-path / --luals-path): validate that
       the path is a regular file and is executable; raise RuntimeError if not
       (feeds the 30s init-failure backoff, honest error, no silent hang).
    2. Otherwise: scan _TRUSTED_LSP_BIN_DIRS and return the first regular,
       executable match.  These pinned system locations take precedence over
       PATH so a hostile earlier-PATH entry cannot win when the binary exists
       in a standard install dir.
    3. Last resort: shutil.which(default_name) for a PATH lookup, returning its
       absolute result with a WARNING (unpinned, PATH-order dependent); raise
       RuntimeError if not found.

    The --clangd-path / --luals-path CLI overrides remain the hard mitigation
    for fully untrusted-PATH environments (CWE-426).
    """
    if override is not None:
        if not os.path.isfile(override):
            raise RuntimeError(
                f"LSP binary override path does not exist or is not a file: {override!r}"
            )
        if not os.access(override, os.X_OK):
            raise RuntimeError(
                f"LSP binary override path is not executable: {override!r}"
            )
        return override
    for trusted_dir in _TRUSTED_LSP_BIN_DIRS:
        candidate = os.path.join(trusted_dir, default_name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    resolved = shutil.which(default_name)
    if resolved is None:
        raise RuntimeError(f"{default_name!r} binary not found on PATH")
    log.warning(
        "LSP binary %r resolved via unpinned PATH lookup: %s "
        "(not found in a trusted install dir; pass --clangd-path/--luals-path "
        "to pin the binary in untrusted-PATH environments)",
        default_name, resolved,
    )
    return resolved  # shutil.which already returns an absolute path


async def _init_backend(backend_type: str, project_root: str) -> Optional[LspBackend]:
    """Spawn and initialize one backend. Returns the live client, or None on
    failure (failure is cached by the caller via _backend_init_failed).
    """
    if backend_type == "clangd":
        # CWE-426: resolve to an absolute, validated path before spawning.
        clangd_abs = _resolve_lsp_binary(_clangd_binary_override, "clangd")
        log.debug("clangd binary resolved to: %s", clangd_abs)
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
        msg = await client.start(project_root, clangd_path=clangd_abs,
                                 cuda_path=cuda_path, cuda_arch=cuda_arch)
        log.debug("Backend '%s' init: %s", backend_type, msg)
        return client
    elif backend_type == "luals":
        # CWE-426: resolve to an absolute, validated path before spawning.
        luals_abs = _resolve_lsp_binary(_luals_binary_override, "lua-language-server")
        log.debug("lua-language-server binary resolved to: %s", luals_abs)
        client = LuaLsClient()
        msg = await client.start(project_root, luals_path=luals_abs)
        log.debug("Backend '%s' init: %s", backend_type, msg)
        return client
    else:
        raise RuntimeError(f"Unknown backend type: '{backend_type}'")


async def _ensure_backend(filetype: str, project_root: str) -> LspBackend:
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
    if _client_is_alive(client):
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

    if client is not None:
        # Registered but not alive: clangd crashed or stdout-EOF'd after a
        # successful init.  Drop it synchronously (no await -> the coalescing
        # guard below stays race-free) and reap its process in the background,
        # then fall through to a fresh init on THIS call - the auto-recovery the
        # bare process-not-None check never provided.  _drop_backend also clears
        # the init-failure backoff so this crash recovery is immediate.
        log.warning(
            "LSP backend '%s' died (returncode=%s); auto-restarting on this call.",
            backend_type,
            getattr(getattr(client, "process", None), "returncode", None),
        )
        _drop_backend(backend_type)
        _reap_backend_process(client)

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

async def _def_by_name(client: LspBackend, symbol_name: str,
                       preferred_path: Optional[str], context_lines: int) -> Any:
    loc = await _symbol_to_location(client, symbol_name, preferred_path=preferred_path or None)
    if not loc:
        return {"error": f"Symbol '{_sanitize_log(symbol_name)}' not found in workspace"}
    def_result = await client.definition(loc["path"], loc["line"], loc["char"])
    if not def_result:
        return {"error": f"No definition found for '{_sanitize_log(symbol_name)}'"}
    locations = def_result if isinstance(def_result, list) else [def_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        # F6: only open paths that resolve inside the project root.
        abs_path = _lsp_path_in_root(location["uri"], client.project_root)
        def_line = location["range"]["start"]["line"]
        results.append({
            "symbol": symbol_name,
            "location": location,
            "context": extract_surrounding_code(abs_path, def_line, context_lines),
        })
    return results


async def _def_at(client: LspBackend, abs_path: str, line: int, char: int,
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
        # F6: only open paths that resolve inside the project root.
        ap = _lsp_path_in_root(location["uri"], client.project_root)
        dl = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(ap, dl, context_lines),
        })
    return results


async def _type_def_at(client: LspBackend, abs_path: str, line: int, char: int,
                       context_lines: int) -> Any:
    """textDocument/typeDefinition at a position, in _def_at's result shape."""
    td_result = await client.type_definition(abs_path, line, char)
    if not td_result:
        return {"error": "No type definition found at this position"}
    locations = td_result if isinstance(td_result, list) else [td_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        # F6: only open paths that resolve inside the project root.
        ap = _lsp_path_in_root(location["uri"], client.project_root)
        tl = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(ap, tl, context_lines),
        })
    return results


async def _refs_by_name(client: LspBackend, symbol_name: str,
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
            # F6: only open paths that resolve inside the project root.
            ap = _lsp_path_in_root(location["uri"], client.project_root)
            rl = location["range"]["start"]["line"]
            all_refs.append({
                "symbol": symbol_name,
                "location": location,
                "context": extract_surrounding_code(ap, rl, context_lines) if context_lines > 0 else None,
            })
        if max_results > 0 and len(all_refs) >= max_results:
            break
    # Cross-source merge: supplement LSP hits with backend-specific text refs
    # (e.g. Lua dynamic-dispatch grep via LuaLsClient.supplemental_references).
    # BaseLspClient returns [] so clangd/CUDA behavior is unchanged.
    line_seen = {
        f"{r['location']['uri']}:{r['location']['range']['start']['line']}"
        for r in all_refs
    }
    remaining = (max_results - len(all_refs)) if max_results > 0 else 0
    extra = await client.supplemental_references(symbol_name, line_seen, remaining,
                                                 preferred_path)
    for hit in extra:
        hit_uri = hit.get("uri", "")
        hit_line = hit.get("range", {}).get("start", {}).get("line", 0)
        hit_key = f"{hit_uri}:{hit_line}"
        if hit_key in line_seen:
            continue
        line_seen.add(hit_key)
        location = _location_from_payload(hit, client.project_root)
        if not location:
            continue
        # F6: only open paths that resolve inside the project root.
        ap = _lsp_path_in_root(location["uri"], client.project_root)
        rl = location["range"]["start"]["line"]
        all_refs.append({
            "symbol": symbol_name,
            "location": location,
            "context": extract_surrounding_code(ap, rl, context_lines) if context_lines > 0 else None,
        })
        if max_results > 0 and len(all_refs) >= max_results:
            break
    return {"symbol": symbol_name, "count": len(all_refs), "references": all_refs}


async def _refs_at(client: LspBackend, abs_path: str, line: int, char: int,
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
        # F6: only open paths that resolve inside the project root.
        ap = _lsp_path_in_root(location["uri"], client.project_root)
        rl = location["range"]["start"]["line"]
        all_refs.append({
            "location": location,
            "context": extract_surrounding_code(ap, rl, context_lines) if context_lines > 0 else None,
        })
    return {"count": len(all_refs), "references": all_refs}


def _md(text: str) -> dict:
    """Wrap a pre-rendered markdown string as a purity __raw_text__ payload.

    Semantic handlers emit markdown (not JSON) to keep the token cost low: the
    same `path:line:col` refs and code-context blocks that JSON would nest in
    `range`/`range_human`/`uri` keys collapse to a single compact line. The wire
    convention is identical to the file-op handlers (envelope at the dispatch
    layer unwraps `__raw_text__` into the MCP `content:[{type:text}]` block).
    """
    return {"__raw_text__": text}


# ---------------------------------------------------------------------------
# Markdown render atoms (shared by the per-handler renderers below)
# ---------------------------------------------------------------------------

def _md_loc(location: Optional[dict], *, line_text: bool = True) -> str:
    """Render a location dict as a compact `path:line:col` reference.

    Consumes the `_format_location` shape (path + range_human + line_text);
    appends the source line when *line_text* is set and the text is present.
    """
    if not location:
        return "_(no location)_"
    start = location.get("range_human", {}).get("start", {})
    ref = f"`{location.get('path', '?')}:{start.get('line', '?')}:{start.get('character', '?')}`"
    if line_text:
        lt = (location.get("line_text") or "").strip()
        if lt:
            ref += f" — `{lt}`"
    return ref


def _md_ctx(context: Optional[str]) -> str:
    """Render a `>>>`-marked surrounding-code string as a fenced code block."""
    return f"```\n{context}\n```" if context else ""


def _md_def_blocks(items: list, heading: str = "##") -> list:
    """Render a list of {location, context} items as heading + code-fence blocks."""
    out: list = []
    for d in items:
        out.append(f"{heading} {_md_loc(d.get('location'), line_text=False)}")
        ctx = _md_ctx(d.get("context"))
        if ctx:
            out.append(ctx)
    return out


def _md_ref_bullets(data: dict) -> list:
    """Render a references payload's sites as a compact `path:line:col` bullet list."""
    return [f"- {_md_loc(r.get('location'))}" for r in data.get("references", [])]


def _md_call_hierarchy(ch: Any) -> list:
    """Render call-hierarchy nodes as an indented incoming(←)/outgoing(→) tree."""
    out: list = []

    def label(n: dict) -> str:
        return f"**{n.get('kind', '?')}** `{n.get('symbol', '')}` — {_md_loc(n.get('location'), line_text=False)}"

    def walk(node: dict, depth: int, arrow: str = "") -> None:
        out.append(f"{'  ' * depth}- {arrow}{label(node)}")
        for c in (node.get("incoming") or []):
            walk(c, depth + 1, "← ")
        for c in (node.get("outgoing") or []):
            walk(c, depth + 1, "→ ")

    for item in (ch if isinstance(ch, list) else [ch]):
        if isinstance(item, dict) and "roots" in item:
            for root in item["roots"]:
                walk(root, 0)
        elif isinstance(item, dict):
            walk(item, 0)
    return out


# ---------------------------------------------------------------------------
# Per-handler markdown renderers (one per semantic handler family)
# ---------------------------------------------------------------------------

def _md_definition(data: list) -> str:
    if not data:
        return "_(no definition found)_"
    sym = data[0].get("symbol")
    head = f"# Definition: `{sym}` ({len(data)})" if sym else f"# Definition ({len(data)})"
    return "\n".join([head, "", *_md_def_blocks(data)])


def _md_type_definition(data: list) -> str:
    if not data:
        return "_(no type definition found)_"
    return "\n".join([f"# Type definition ({len(data)})", "", *_md_def_blocks(data)])


def _md_implementations(data: list) -> str:
    if not data:
        return "_(no implementations found)_"
    return "\n".join([f"# Implementations ({len(data)})", "", *_md_def_blocks(data)])


def _md_references(data: dict) -> str:
    sym = data.get("symbol")
    count = data.get("count", 0)
    head = f"# References: `{sym}` — {count} found" if sym else f"# References — {count} found"
    if count == 0:
        return f"{head}\n\n_(none)_"
    return "\n".join([head, "", *_md_ref_bullets(data)])


def _md_type_at(data: dict) -> str:
    out = ["# Type"]
    if data.get("location"):
        out.append(f"\nAt {_md_loc(data['location'], line_text=False)}")
    if data.get("deduced_type"):
        out.append(f"\n**Deduced type**: `{data['deduced_type']}`")
    text = (data.get("text") or "").strip()
    if text:
        out.append(f"\n```\n{text}\n```")
    return "\n".join(out)


def _md_diagnostics(data: dict) -> str:
    path = data.get("path", "?")
    count = data.get("count", 0)
    head = f"# Diagnostics: `{path}` — {count}"
    if count == 0:
        return f"{head}\n\n_(no diagnostics)_"
    out = [head, ""]
    for d in data.get("diagnostics", []):
        extra = []
        if d.get("code") is not None:
            extra.append(str(d["code"]))
        if d.get("source"):
            extra.append(str(d["source"]))
        suffix = f" ({', '.join(extra)})" if extra else ""
        msg = (d.get("message") or "").strip()
        out.append(f"- **{d.get('severity', '?')}** {_md_loc(d.get('location'), line_text=False)}{suffix}: {msg}")
    return "\n".join(out)


def _md_outline(nodes: list) -> str:
    if not nodes:
        return "_(no symbols)_"
    first = nodes[0].get("selection") or nodes[0].get("location") or {}
    path = first.get("path", "")
    out = [f"# Outline: `{path}`" if path else "# Outline", ""]

    def line_of(loc: Optional[dict]) -> Any:
        return loc.get("range_human", {}).get("start", {}).get("line", "?") if loc else "?"

    def walk(node: dict, depth: int) -> None:
        loc = node.get("selection") or node.get("location")
        s = f"{'  ' * depth}- **{node.get('kind', '?')}** `{node.get('symbol', '')}` (L{line_of(loc)})"
        if node.get("detail"):
            s += f" — {node['detail']}"
        out.append(s)
        for c in (node.get("children") or []):
            walk(c, depth + 1)

    for n in nodes:
        walk(n, 0)
    return "\n".join(out)


def _md_symbols(data: dict) -> str:
    query = data.get("query", "")
    count = data.get("count", 0)
    out = [f"# Symbols: `{query}` — {count}"]
    if data.get("source"):
        out.append(f"_source: {data['source']}_")
    out.append("")
    if count == 0:
        out.append("_(none)_")
        return "\n".join(out)
    for s in data.get("symbols", []):
        cont = f" _{s['container']}_" if s.get("container") else ""
        out.append(f"- **{s.get('kind', '?')}** `{s.get('symbol', '')}`{cont} — {_md_loc(s.get('location'), line_text=False)}")
    return "\n".join(out)


def _md_symbol_context(data: dict) -> str:
    out = [f"# Symbol context: `{data.get('symbol', '')}`", "", "## Definition", ""]
    definition = data.get("definition")
    if isinstance(definition, list) and definition:
        out += _md_def_blocks(definition, heading="###")
    elif isinstance(definition, dict) and definition.get("error"):
        out.append(f"_{definition['error']}_")
    else:
        out.append("_(not found)_")
    references = data.get("references") if isinstance(data.get("references"), dict) else {}
    rcount = references.get("count", 0)
    out += ["", f"## References ({rcount})", ""]
    out += _md_ref_bullets(references) if rcount else ["_(none)_"]
    return "\n".join(out)


def _md_change_impact(data: dict) -> str:
    out = [f"# Change impact: `{data.get('symbol', '')}`", "", "## Definition", ""]
    definition = data.get("definition")
    if isinstance(definition, list) and definition:
        out += _md_def_blocks(definition, heading="###")
    elif isinstance(definition, dict) and definition.get("error"):
        out.append(f"_{definition['error']}_")
    else:
        out.append("_(not found)_")

    rs = data.get("reference_summary", {})
    files = rs.get("files", [])
    out += ["", f"## References — {rs.get('count', 0)} in {len(files)} file(s)", ""]
    out += [f"- `{f}`" for f in files] or ["_(none)_"]

    references = data.get("references") if isinstance(data.get("references"), dict) else {}
    if references.get("references"):
        out += ["", "### Reference sites", "", *_md_ref_bullets(references)]

    ch = data.get("call_hierarchy", [])
    suffix = " _(partial: none found)_" if data.get("partial") else ""
    out += ["", f"## Call hierarchy{suffix}", ""]
    out += _md_call_hierarchy(ch) if ch else ["_(none)_"]
    return "\n".join(out)


def _md_inlay_hints(results: list) -> str:
    if not results:
        return "_(no inlay hints)_"
    out = [f"# Inlay hints ({len(results)})", ""]
    for h in results:
        hum = h.get("position", {}).get("human", {})
        kind = f"[{h['kind']}] " if h.get("kind") else ""
        out.append(f"- `L{hum.get('line', '?')}:{hum.get('character', '?')}` {kind}`{h.get('label', '')}`")
    return "\n".join(out)


# ===========================================================================
# Semantic handlers (11 canonical; async; signature (params, project_root, strict))
# ===========================================================================
#
# A-class (grep-degradable): find_definition, find_references, symbol,
#   symbol_context fall back to the grep net on empty/no-index.
# B-class (honest error): type_at, diagnostics, outline, find_implementations,
#   find_type_definition return an explicit error when the LSP yields nothing -
#   never a grep guess.
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
        ft = _select_filetype(params, abs_path or "")
        client = await _ensure_backend(ft, project_root)
        data = await _def_by_name(client, symbol_name, abs_path, context_lines)
    else:
        return {"error": "find_definition requires either 'symbol' (name) or 'at' (path + line + character)"}

    if isinstance(data, dict) and "error" in data:
        return data
    return _md(_md_definition(data))


async def handle_find_type_definition(params: dict, project_root: str,
                                      strict: bool = False) -> dict:
    """Find the TYPE definition at a file position (B-class; positional only).

    One hop past find_definition: from a variable / expression to where its type
    is declared. clangd serves C/C++/ObjC/CUDA, luals serves Lua, so the single
    handler covers every language purity supports.
    """
    path = params.get("relative_path", "")
    if not path:
        return {"error": "find_type_definition requires 'path' with 'line'/'character'"}
    abs_path = safe_path(project_root, path, strict)
    client = await _ensure_backend(_detect_language(abs_path), project_root)
    line = int(params.get("line", 1)) - 1
    char = int(params.get("character", 1)) - 1
    data = await _type_def_at(client, client._abs_path(abs_path), line, char,
                              int(params.get("context_lines", 5)))
    if isinstance(data, dict) and "error" in data:
        return data
    if not data:
        return {"error": "No type definition found at this position"}
    return _md(_md_type_definition(data))


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
        ft = _select_filetype(params, abs_path or "")
        client = await _ensure_backend(ft, project_root)
        data = await _refs_by_name(client, symbol_name, abs_path, max_results, context_lines)
    else:
        return {"error": "find_references requires either 'symbol' (name) or 'at' (path + line + character)"}

    return _md(_md_references(data))


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
        # F6: only open paths that resolve inside the project root.
        ap = _lsp_path_in_root(location["uri"], client.project_root)
        il = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(ap, il, int(params.get("context_lines", 5))),
        })
    if not results:
        return {"error": "No implementations found at this position"}
    return _md(_md_implementations(results))


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
    deduced = client.infer_type(raw_text)
    hover_range = result.get("range")
    location = None
    if hover_range:
        uri = pathlib.Path(client._abs_path(abs_path)).as_uri()
        location = _format_location(uri, hover_range, client.project_root)
    return _md(_md_type_at({"text": raw_text, "deduced_type": deduced, "location": location}))


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
    return _md(_md_diagnostics({
        "path": _relative_path(pathlib.Path(cpath).as_uri(), client.project_root),
        "count": len(results),
        "diagnostics": results,
    }))


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

    return _md(_md_outline([fmt(s) for s in symbols]))


async def handle_symbol(params: dict, project_root: str, strict: bool = False) -> dict:
    """Search workspace symbols by query (A-class; grep fallback unless strict)."""
    query = params.get("query") or params.get("symbol_name") or ""
    if not query:
        return {"error": "symbol requires 'query' (or 'symbol')"}
    limit = int(params.get("limit") or params.get("max_results") or 50)
    # 'strict' here disables the filesystem fallback (clangd API); it is a params
    # key, distinct from the server-level sandbox 'strict' arg used by safe_path.
    disable_fallback = _bool_param(params.get("strict"), default=False)
    client = await _ensure_backend(_select_filetype(params, ""), project_root)

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
    return _md(_md_symbols(out))


async def handle_symbol_context(params: dict, project_root: str, strict: bool = False) -> dict:
    """Definition + references for a symbol in one call (A-class)."""
    symbol_name = params.get("symbol_name", "")
    if not symbol_name:
        return {"error": "symbol_context requires 'symbol' (name)"}
    path = params.get("relative_path", "")
    max_references = int(params.get("max_references", 20))
    context_lines = int(params.get("context_lines", 5))
    abs_path = safe_path(project_root, path, strict) if path else None
    ft = _select_filetype(params, abs_path or "")
    client = await _ensure_backend(ft, project_root)
    definition = await _def_by_name(client, symbol_name, abs_path, context_lines)
    references = await _refs_by_name(client, symbol_name, abs_path, max_references, 2)
    return _md(_md_symbol_context({"symbol": symbol_name, "definition": definition, "references": references}))


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
    return _md(_md_inlay_hints(results))


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
    ft = _select_filetype(params, abs_path or "")
    client = await _ensure_backend(ft, project_root)

    definition = await _def_by_name(client, symbol_name, abs_path, 3)
    references = await _refs_by_name(client, symbol_name, abs_path, max_references, 2)

    call_hierarchies = []
    if isinstance(definition, list) and client.supports_call_hierarchy:
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

    return _md(_md_change_impact({
        "symbol": symbol_name,
        "definition": definition,
        "references": references,
        "reference_summary": {
            "count": references.get("count", 0) if isinstance(references, dict) else 0,
            "files": ref_files,
        },
        "call_hierarchy": call_hierarchies,
        "partial": not call_hierarchies,
    }))


def handle_lsp_init_noop(params: dict, project_root: str, strict: bool = False) -> dict:
    """Deprecation no-op for legacy clangd_init / cuda_init names.

    Under lazy init there is no explicit init step - the LSP backend spins up on
    first semantic call. This returns a benign notice (NOT an error) so existing
    callers that still send an init do not break.
    """
    return {"__raw_text__":
            "LSP backends initialize lazily on first semantic call - no explicit "
            "init is needed. (clangd_init/cuda_init are accepted as no-ops.)"}


async def handle_restart_lsp(params: dict, project_root: str, strict: bool = False) -> dict:
    """Explicitly tear down and re-initialize an LSP backend - the programmatic
    recovery path for a wedged or stale clangd/luals, complementing the
    automatic crash-recovery in _ensure_backend.

    params:
      backend / filetype : which backend to restart.  "clangd"/"cpp"/"c"/"cuda"
                           (default) -> clangd; "luals"/"lua" -> luals.
      reindex            : bool (default False).  When restarting clangd, also
                           delete clangd's on-disk background-index cache
                           (<root>/.cache/clangd) so the fresh start rebuilds
                           the index from scratch.

    Unlike the clangd_init/cuda_init/luals_init no-ops (which stay no-ops), this
    forces a real restart and clears the init-failure backoff, so it recovers a
    backend even from inside a backoff window.  Re-init is eager: any spawn or
    handshake error surfaces here rather than on a later semantic call.
    """
    raw = str(params.get("backend") or params.get("filetype") or "clangd").strip().lower()
    if raw in ("luals", "lua"):
        backend_type, filetype = "luals", "lua"
    else:
        backend_type, filetype = "clangd", "cpp"
    reindex = _bool_param(params.get("reindex"), False)

    resolved_root = str(pathlib.Path(project_root).resolve())

    # Cancel any in-flight init, unregister, and fully stop the running client.
    # Capture the task ref BEFORE _drop_backend pops it from the registry.
    task = _backend_init_tasks.get(backend_type)
    client = _drop_backend(backend_type)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if client is not None:
        await _safe_stop(client)

    wiped = False
    if reindex and backend_type == "clangd":
        wiped = _wipe_clangd_index_cache(resolved_root)

    try:
        new_client = await _ensure_backend(filetype, resolved_root)
    except Exception as exc:
        return {"__raw_text__":
                f"LSP backend '{backend_type}' torn down"
                f"{' (index cache wiped)' if wiped else ''}, but re-init failed: "
                f"{_sanitize_log(str(exc))}. It will retry on the next semantic call."}

    pid = getattr(getattr(new_client, "process", None), "pid", None)
    return {"__raw_text__":
            f"LSP backend '{backend_type}' restarted"
            f"{' with reindex (index cache wiped)' if wiped else ''} at "
            f"{resolved_root} (pid={pid})."}


# ---------------------------------------------------------------------------
# clang-tidy (subprocess-backed static analysis)
# ---------------------------------------------------------------------------

# CWE-426 mitigation: explicit absolute path override for the clang-tidy binary.
# Set by main() from --clang-tidy-path. None means resolve at call time from the
# build dir's CMakeCache CLANG_TIDY_EXE, then PATH (see _resolve_clang_tidy_binary).
_clang_tidy_binary_override: Optional[str] = None


def _find_compile_commands_dir(project_root: str,
                               override: Optional[str] = None) -> Optional[str]:
    """Return the directory holding a compile_commands.json, or None.

    Search order mirrors _prepare_compile_commands: an explicit caller-supplied
    dir first, then <root>/build, then the project root. Only dirs resolving
    inside project_root are considered (CWE-22). The result is what feeds
    `clang-tidy -p <dir>` so the linter sees the project's real compile flags.
    """
    candidates: List[str] = []
    if override:
        candidates.append(override)
    candidates.append(os.path.join(project_root, "build"))
    candidates.append(project_root)
    root = os.path.realpath(project_root)
    for d in candidates:
        resolved = os.path.realpath(os.path.join(project_root, d))
        if not (resolved == root or resolved.startswith(root + os.sep)):
            continue
        if os.path.isfile(os.path.join(resolved, "compile_commands.json")):
            return resolved
    return None


def _read_cmake_cache_var(build_dir: str, var_name: str) -> Optional[str]:
    """Return the value of a CMake cache variable from <build_dir>/CMakeCache.txt.

    Cache lines look like `NAME:TYPE=VALUE`. Returns the raw VALUE (stripped), or
    None if the cache or the variable is absent / unreadable. CMake's own
    "not found" sentinel (`<VAR>-NOTFOUND`) is passed through unchanged for the
    caller to reject.
    """
    cache = os.path.join(build_dir, "CMakeCache.txt")
    if not os.path.isfile(cache):
        return None
    try:
        text = pathlib.Path(cache).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(rf"^{re.escape(var_name)}:[^=]*=(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _resolve_clang_tidy_binary(override: Optional[str],
                               build_dirs: List[str]) -> str:
    """Resolve the clang-tidy binary. Order: override → CMake → PATH → error.

    1. `--clang-tidy-path` override (validated file + executable; CWE-426 pin).
    2. `CLANG_TIDY_EXE` from the build dir's CMakeCache.txt — how a CMake project
       that located clang-tidy at configure time records the binary. First build
       dir with a usable (existing, executable) value wins.
    3. `clang-tidy` on PATH (shutil.which).
    4. RuntimeError with an actionable message if none of the above resolve.
    """
    if override is not None:
        if not os.path.isfile(override):
            raise RuntimeError(f"clang-tidy override path does not exist or is not a file: {override!r}")
        if not os.access(override, os.X_OK):
            raise RuntimeError(f"clang-tidy override path is not executable: {override!r}")
        return override

    for bd in build_dirs:
        if not bd:
            continue
        exe = _read_cmake_cache_var(bd, "CLANG_TIDY_EXE")
        if not exe or exe.endswith("-NOTFOUND"):
            continue
        if os.path.isfile(exe) and os.access(exe, os.X_OK):
            log.debug("clang-tidy resolved via CLANG_TIDY_EXE in %s: %s", bd, exe)
            return exe
        log.warning("CLANG_TIDY_EXE in %s points to a non-executable path: %r", bd, exe)

    resolved = shutil.which("clang-tidy")
    if resolved:
        return resolved

    raise RuntimeError(
        "clang-tidy not found: no --clang-tidy-path override, no usable "
        "CLANG_TIDY_EXE in the build dir's CMakeCache.txt, and 'clang-tidy' is "
        "not on PATH. Install clang-tidy or configure CMake so it locates one."
    )


async def handle_clang_tidy(params: dict, project_root: str, strict: bool = False) -> dict:
    """Run clang-tidy on one or more C/C++ files.

    Auto-parameterizes against a compile database: if a build dir with a
    compile_commands.json exists (explicit `build_dir`, else <root>/build, else
    <root>), clang-tidy is invoked with `-p <dir>` so it sees the project's real
    compile flags. With no database it still runs (empty flag section after a
    bare `--`) but with reduced accuracy — the report states which mode was used.

    Skill-aligned defaults (see the p:clang-tidy skill): always `--quiet`; the
    project's `<root>/.clang-tidy` is pinned via `--config-file` when present
    (unless the caller passes explicit `checks`); and `--header-filter` defaults
    to `<root>/src/.*` so project-header diagnostics surface. Callers can
    override the header filter and check set through params.
    """
    rel = params.get("relative_path")
    if not rel:
        return {"error": "clang_tidy requires 'path' (a C/C++ source file, or a list of them)"}
    rels = rel if isinstance(rel, list) else [rel]
    abs_paths: List[str] = []
    for r in rels:
        ap = safe_path(project_root, str(r), strict)
        if not os.path.isfile(ap):
            return {"error": f"File not found: {_sanitize_log(str(r))}"}
        abs_paths.append(ap)

    build_dir = params.get("build_dir")
    if build_dir is not None:
        # Containment-validate an explicit build dir before handing it to -p.
        build_dir = safe_path(project_root, str(build_dir), strict)
    cc_dir = _find_compile_commands_dir(project_root, build_dir)

    # Resolve clang-tidy from the same build dirs we found the compile DB in:
    # CLANG_TIDY_EXE (CMakeCache) → PATH → error.
    binary = _resolve_clang_tidy_binary(
        _clang_tidy_binary_override,
        [build_dir, cc_dir, os.path.join(project_root, "build")],
    )

    cmd = [binary]
    if cc_dir:
        cmd += ["-p", cc_dir]
    # --quiet: drop the "NNNN warnings generated." / suppression-summary noise
    # (skill-mandated). Diagnostics themselves are unaffected.
    cmd.append("--quiet")
    checks = params.get("checks")
    if checks:
        # Explicit caller override; takes precedence over the project config.
        cmd.append(f"-checks={checks}")
    else:
        # Pin the project's .clang-tidy when present at the root. clang-tidy's
        # own auto-discovery walks up from each source file and would miss a
        # config that lives outside the sources' ancestry; being explicit makes
        # the intended check set deterministic regardless of file location.
        cfg = os.path.join(project_root, ".clang-tidy")
        if os.path.isfile(cfg):
            cmd.append(f"--config-file={cfg}")
    # Default the header filter to the project's src tree so diagnostics from
    # project headers surface (clang-tidy's default is main-file-only).
    header_filter = params.get("header_filter") or f"{project_root}/src/.*"
    cmd.append(f"-header-filter={header_filter}")
    if params.get("fix"):
        cmd.append("-fix")
    cmd += abs_paths
    if not cc_dir:
        # No compilation database: append an empty flag section so clang-tidy
        # doesn't abort hunting for one (it emits a warning and carries on).
        cmd.append("--")

    timeout = float(params.get("timeout", 60.0))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"clang-tidy timed out after {timeout:g}s"}
    except OSError as exc:
        return {"error": f"Failed to run clang-tidy: {exc}"}

    stdout = out_b.decode("utf-8", "replace").strip()
    stderr = err_b.decode("utf-8", "replace").strip()

    files_disp = ", ".join(os.path.relpath(p, project_root) for p in abs_paths)
    db_note = (f"compile db: {os.path.relpath(cc_dir, project_root)}/compile_commands.json"
               if cc_dir else "compile db: none (reduced accuracy)")
    lines = [f"# clang-tidy — {files_disp}", db_note, f"exit code: {proc.returncode}", ""]

    body = stdout or "(no diagnostics)"
    max_chars = params.get("max_answer_chars", 40000)
    if max_chars and max_chars > 0 and len(body) > max_chars:
        body = body[:max_chars] + "\n... (truncated)"
    lines.append(body)
    if stderr:
        lines.append(f"\n--- stderr ---\n{stderr}")
    return _md("\n".join(lines))


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Callable[..., dict]] = {
    "read_file": handle_read_file,
    "create_text_file": handle_create_text_file,
    "create_temp_dir": handle_create_temp_dir,
    "temp_dir": handle_create_temp_dir,
    "mktemp": handle_create_temp_dir,
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
    "find_type_definition": handle_find_type_definition,
    "find_references": handle_find_references,
    "find_implementations": handle_find_implementations,
    "type_at": handle_type_at,
    "diagnostics": handle_diagnostics,
    "outline": handle_outline,
    "symbol": handle_symbol,
    "symbol_context": handle_symbol_context,
    "inlay_hints": handle_inlay_hints,
    "symbol_change_impact": handle_symbol_change_impact,
    # --- subprocess-backed static analysis ---
    "clang_tidy": handle_clang_tidy,
    # --- backend lifecycle: explicit restart / reindex escape hatch. The
    #     *_init names below stay no-ops; this is the real recovery path. ---
    "restart_lsp": handle_restart_lsp,
    # --- legacy clangd_* names as DIRECT keys ([inspector C1]: FUNCTION_ALIASES
    #     does NOT route; the dispatcher looks up the RAW function name) ---
    "clangd_find_definition": handle_find_definition,
    "clangd_find_definition_at": handle_find_definition,
    "clangd_find_type_definition_at": handle_find_type_definition,
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
    "cuda_find_type_definition_at": handle_find_type_definition,
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
    # --- legacy luals_* names as DIRECT keys ---
    "luals_find_definition": handle_find_definition,
    "luals_find_definition_at": handle_find_definition,
    "luals_find_type_definition_at": handle_find_type_definition,
    "luals_find_references": handle_find_references,
    "luals_find_references_at": handle_find_references,
    "luals_find_implementations_at": handle_find_implementations,
    "luals_workspace_symbols": handle_symbol,
    "luals_document_outline": handle_outline,
    "luals_symbol_context": handle_symbol_context,
    "luals_symbol_change_impact": handle_symbol_change_impact,
    "luals_inlay_hints": handle_inlay_hints,
    "luals_hover": handle_type_at,
    "luals_diagnostics": handle_diagnostics,
    "luals_init": handle_lsp_init_noop,
}

# Non-destructive handlers, keyed by the handler CALLABLE (not its name) so
# every alias — short, `ls`/`glob`/`grep`, and the prefixed clangd_*/cuda_*/
# luals_* legacy names — is covered in one place. These may resolve paths
# outside the project root when the server is not in --strict mode; the
# dispatcher opts them in via _ALLOW_OUTSIDE_ROOT. This is an ALLOWLIST by
# design: the destructive handlers (create_text_file, replace_content,
# delete_lines, replace_lines, insert_at_line) are absent, so a future handler
# added to HANDLERS stays sandboxed until it is explicitly listed here.
_READONLY_HANDLERS: frozenset = frozenset({
    handle_read_file,
    handle_list_dir,
    handle_find_file,
    handle_search_for_pattern,
    handle_find_definition,
    handle_find_type_definition,
    handle_find_references,
    handle_find_implementations,
    handle_type_at,
    handle_diagnostics,
    handle_outline,
    handle_symbol,
    handle_symbol_context,
    handle_inlay_hints,
    handle_symbol_change_impact,
    handle_clang_tidy,
    handle_lsp_init_noop,
})

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
        "relative_path", "content", "overwrite",
    },
    "create_temp_dir": {"subpath", "unique"},
    "restart_lsp": {"backend", "filetype", "reindex"},
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
        "substring_pattern", "context_lines", "context_lines_before", "context_lines_after",
        "paths_include_glob", "paths_exclude_glob", "relative_path",
        "max_answer_chars", "head_limit", "offset", "output_mode",
        "max_file_size", "skip_ignored_files", "restrict_search_to_code_files",
    },
    # --- semantic (LSP) functions: POST-alias canonical param names ---
    "find_definition": {
        "relative_path", "line", "character", "symbol_name", "context_lines",
    },
    "find_type_definition": {
        "relative_path", "line", "character", "context_lines",
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
    "clang_tidy": {
        "relative_path", "build_dir", "checks", "header_filter", "fix",
        "timeout", "max_answer_chars",
    },
}


# Short canonical name -> handler, restricted to names that carry an
# accepted-param set. Lets a prefixed legacy name resolve its accepted-set by
# matching on the shared handler object.
_CANONICAL_HANDLERS: Dict[str, Callable[..., dict]] = {
    name: HANDLERS[name]
    for name in HANDLER_ACCEPTED_PARAMS
    if name in HANDLERS
}

# Reserved keys the dispatcher injects into `params` AFTER alias resolution.
# They are not caller-supplied and must be excluded from unknown-param checks.
_RESERVED_PARAM_KEYS = frozenset({"_backend"})


def _accepted_params_for(function: str, canonical_func: str) -> Optional[set]:
    """Resolve the accepted-param set for a (possibly prefixed) function name.

    Short/aliased names hit HANDLER_ACCEPTED_PARAMS directly via canonical_func.
    Prefixed legacy names (clangd_*/cuda_*/luals_*) are NOT in that map, so we
    map them through their registered handler to the short canonical handler
    whose accepted-set we DO know. Names whose handler has no accepted-set
    (e.g. the *_init no-ops) return None, which callers treat as "skip check".
    """
    accepted = HANDLER_ACCEPTED_PARAMS.get(canonical_func)
    if accepted is not None:
        return accepted
    handler = HANDLERS.get(function)
    if handler is not None:
        for short_name, short_handler in _CANONICAL_HANDLERS.items():
            if short_handler is handler:
                return HANDLER_ACCEPTED_PARAMS.get(short_name)
    return None


def _unknown_params(params: dict, accepted: set) -> list:
    """Sorted list of caller params not in `accepted` (reserved keys excluded)."""
    return sorted(set(params.keys()) - accepted - _RESERVED_PARAM_KEYS)


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

    # Inject the backend hint so path-less luals_* calls reach the luals backend
    # instead of defaulting to "cpp".  The hint is resolved from the function's
    # name prefix and stored as a reserved key that handlers read via
    # _select_filetype(params, abs_path).  It must be injected BEFORE the
    # handler is called and AFTER alias resolution (so it won't be aliased away).
    params.pop("_backend", None)          # reserved: only the dispatcher may set this
    hint = _backend_hint(function)
    if hint:
        params["_backend"] = hint

    if not function:
        func_list = "\n".join(f"  {name}" for name in sorted(HANDLERS.keys()))
        return {"__raw_text__": f"mcp-purity OK — project: {project_root}\nAvailable functions:\n{func_list}"}

    handler = HANDLERS.get(function)
    if not handler:
        func_list = ", ".join(sorted(HANDLERS.keys()))
        return {"error": f"Unknown function: {_sanitize_log(function)}. Available: {func_list}"}

    # Reject unknown params up-front. Without this, an unrecognized key (a
    # grep-style `paths`/`-n`/`-i`) is silently dropped and the handler runs with
    # surprising defaults — the failure that turned a single-file search into a
    # tree-wide dump. params are post-alias here, so the check is on canonical
    # names.
    accepted = _accepted_params_for(function, canonical_func)
    if accepted is not None:
        unknown = _unknown_params(params, accepted)
        if unknown:
            return {"error": (
                f"Unknown params for '{canonical_func}': {', '.join(unknown)}."
                f" Accepted: {', '.join(sorted(accepted))}."
            )}

    # Non-destructive handlers may reach outside the project root; destructive
    # ones stay sandboxed. Set the flag for this call only. The sync handlers run
    # in an executor thread that starts with a fresh (default) context, so the
    # value is carried across via copy_context().run() rather than relying on
    # the ContextVar leaking into the thread (it does not).
    outside_token = _ALLOW_OUTSIDE_ROOT.set(handler in _READONLY_HANDLERS)
    try:
        if asyncio.iscoroutinefunction(handler):
            return await handler(params, project_root, strict)
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(
            None, lambda: ctx.run(handler, params, project_root, strict)
        )
    except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
        err = str(exc)
        accepted = _accepted_params_for(function, canonical_func)
        if accepted is not None:
            unknown = _unknown_params(params, accepted)
            if unknown:
                err += (
                    f" | Unknown params for '{canonical_func}': {', '.join(unknown)}."
                    f" Accepted: {', '.join(sorted(accepted))}."
                )
        return {"error": err}
    except Exception as exc:
        log.exception("Unhandled exception in handler '%s'", canonical_func)
        return {"error": f"Internal error in '{canonical_func}': {type(exc).__name__}: {exc}"}
    finally:
        _ALLOW_OUTSIDE_ROOT.reset(outside_token)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

PURITY_CALL_TOOL = {
    "name": "purity_call",
    "description": (
        "Project file operations: search, glob, list (ls), write, surgical edits.\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "ABSOLUTE PROHIBITION — Bash(\"find ...\") AND Bash(\"ls ...\") ARE FORBIDDEN\n"
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
        "The SAME iron rule applies to `ls`: NEVER list a directory through\n"
        "Bash — not `ls`, not `ls -la`, not `ls -R`, not globbed, not piped,\n"
        "not via `$(ls ...)`. ANY appearance of the literal `ls` in a Bash\n"
        "invocation is a VIOLATION. `list_dir` (alias `ls`) covers every\n"
        "listing need — recursive, long format (size + mtime), fnmatch filter,\n"
        "regex-on-output, hidden files, `head_limit` / `offset` pagination —\n"
        "and stays project-root-aware. If you catch yourself typing `ls` into\n"
        "Bash, STOP — call `list_dir` instead.\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "SEMANTIC CODE NAVIGATION — purity_call now ALSO does symbol work\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "purity_call now provides compiler-accurate (clangd-backed) symbol\n"
        "navigation for C / C++ / CUDA and type-aware (luals-backed) symbol\n"
        "navigation for Lua (.lua paths, luals_* functions), alongside the file ops above:\n\n"
        "  find_definition       - by symbol name OR file position (symbol/at)\n"
        "  find_type_definition   - at a file position: where the TYPE is defined\n"
        "  find_references        - by symbol name OR file position\n"
        "  find_implementations   - at a file position\n"
        "  type_at                - hover + deduced (auto/template) type\n"
        "  diagnostics            - compiler warnings/errors for a file\n"
        "  outline                - document symbol outline\n"
        "  symbol                 - workspace symbol search (index + grep fallback)\n"
        "  symbol_context         - definition + references in one call\n"
        "  inlay_hints            - parameter/type hints for a range\n"
        "  symbol_change_impact   - definition + references + call hierarchy\n"
        "  clang_tidy             - clang-tidy static analysis; auto -p from compile_commands.json\n\n"
        "The clangd and luals LSPs spin up lazily on first use. For symbol work PREFER these\n"
        "over grepping source - text matching misses overloads, macros, and\n"
        "indirect references. search_for_pattern remains free-text (literal/regex)\n"
        "search over ANY filetype (comments, log strings, build text) - use it when\n"
        "you want text, not a symbol. The former standalone clangd_call / cuda_call /\n"
        "luals_call TOOLS are retired and unregistered - they do not exist in any\n"
        "session, and purity_call is the only entry point. Their legacy\n"
        "clangd_*/cuda_*/luals_* FUNCTION names do still resolve, as aliases here.\n\n"
        "When NOT to use purity:\n"
        "  - Plain reading        -> built-in Read (less MCP overhead).\n"
        "  - Lua symbols          -> purity_call itself (in-process luals, luals_* aliases).\n"
        "  - Git                  -> mcp-git.\n"
        "  - Build / test / clean -> mcp-forge.\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "MANDATORY Bash → purity mappings (NEVER call these via Bash):\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "  Bash(\"ls ...\")                   → function=\"list_dir\" (alias \"ls\")  [FORBIDDEN via Bash]\n"
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
        "create_text_file, create_temp_dir (mktemp), replace_content, replace_lines,\n"
        "delete_lines, insert_at_line, read_file. Semantic (clangd-backed): find_definition,\n"
        "find_type_definition, find_references,\n"
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

                # F12/CWE-532: log protocol structure only, never payload
                # values (params.content / result text can carry file contents).
                # Defensive: params/arguments may be a non-dict on a malformed
                # message; this is a debug log and must never crash the loop.
                if isinstance(msg, dict):
                    _p = msg.get("params")
                    _p = _p if isinstance(_p, dict) else {}
                    _args = _p.get("arguments")
                    _args = _args if isinstance(_args, dict) else {}
                    log.debug(
                        "← method=%s id=%s fn=%s keys=%s",
                        msg.get("method"), msg.get("id"), _p.get("name"),
                        list(_args.keys()),
                    )
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
                    # F12/CWE-532: structure only (id + outcome), no body.
                    log.debug(
                        "→ id=%s %s", response.get("id"),
                        "error" if "error" in response else "ok",
                    )
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
    "find_type_definition": "Find where the TYPE at a file position is defined (textDocument/typeDefinition)",
    "find_references":      "Find references to a symbol by name OR file position",
    "find_implementations": "Find implementations at a file position",
    "type_at":              "Type/hover at a position (incl. deduced auto/template type)",
    "diagnostics":          "Compiler diagnostics (warnings/errors) for a file",
    "outline":              "Document symbol outline for a file",
    "symbol":               "Search workspace symbols by query (clangd index + grep fallback)",
    "symbol_context":       "Definition + references for a symbol in one call",
    "inlay_hints":          "Inlay hints (parameter/type) for a file range",
    "symbol_change_impact": "Definition + references + call hierarchy for impact analysis",
    "clang_tidy":           "Run clang-tidy on C/C++ file(s); auto-uses compile_commands.json from build_dir/<root>/build/<root> via -p; defaults --quiet, --config-file=<root>/.clang-tidy (unless checks given), --header-filter=<root>/src/.* (checks, header_filter, fix, timeout)",
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
    parser.add_argument("--strict", action="store_true", help="Hard-sandbox: reject every path outside project root, reads included")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    # CWE-426 (untrusted-PATH hijack): explicit absolute-path overrides for LSP
    # binaries.  When supplied, _resolve_lsp_binary() validates and pins the
    # binary before the first spawn.  When omitted, shutil.which() is used at
    # init time — still pinned to its absolute result, but PATH-order dependent.
    parser.add_argument(
        "--clangd-path",
        default=None,
        help="Absolute path to the clangd binary (overrides PATH lookup; hard mitigation for CWE-426)",
    )
    parser.add_argument(
        "--luals-path",
        default=None,
        help="Absolute path to the lua-language-server binary (overrides PATH lookup; hard mitigation for CWE-426)",
    )
    parser.add_argument(
        "--clang-tidy-path",
        default=None,
        help="Absolute path to the clang-tidy binary (overrides PATH lookup; hard mitigation for CWE-426)",
    )
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

    # Store CWE-426 binary overrides in module-level vars so _init_backend can
    # read them without threading them through every call site.
    global _clangd_binary_override, _luals_binary_override, _clang_tidy_binary_override
    _clangd_binary_override = args.clangd_path
    _luals_binary_override = args.luals_path
    _clang_tidy_binary_override = args.clang_tidy_path

    server = McpServer(args.project_root, strict=args.strict)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
