#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
mcp-cuda — CUDA code intelligence MCP server via clangd LSP.

Design:
  tools/list → exposes only 'cuda_call' (minimal token footprint)
  tools/call → dispatches all 14 cuda tools (documented in SKILL.md)

CUDA SDK auto-discovery order:
  1. Explicit cuda_path parameter
  2. CUDA_PATH environment variable
  3. CUDA_HOME environment variable
  4. nvcc on PATH → derive SDK root
  5. Glob /usr/local/cuda-* (sorted descending, prefer 12.x)
  6. /usr/local/cuda symlink
  7. CMakeCache.txt in build/ → CMAKE_CUDA_COMPILER

Usage:
  python3 mcp-cuda.py [--debug] [--project-root /path/to/project]
"""

import asyncio
import contextlib
import contextvars
import glob as glob_mod
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import argparse
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional


# ============================================================
# Parameter aliases (liberal matching for LLM-generated params)
# ============================================================

PARAM_ALIASES = {
    "file": "path",
    "file_path": "path",
    "filepath": "path",
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
    "arch": "cuda_arch",
    "gpu_arch": "cuda_arch",
    "cuda_sdk": "cuda_path",
    "sdk_path": "cuda_path",
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
        resolved[canonical] = value
    return resolved


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
# Logging
# ============================================================

MARKDOWN_MODE = False

log = logging.getLogger("mcp-cuda")


# ============================================================
# CUDA SDK auto-discovery
# ============================================================

def _find_cuda_sdk(explicit_path: Optional[str] = None,
                   project_root: Optional[str] = None) -> Optional[str]:
    """
    Discover the CUDA SDK root directory.
    Returns the path to the SDK root (e.g. /usr/local/cuda-12.9) or None.
    """
    # 1. Explicit parameter
    if explicit_path:
        p = pathlib.Path(explicit_path)
        if (p / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (explicit): {p}")
            return str(p)
        log.debug(f"CUDA SDK explicit path invalid (no nvcc): {p}")

    # 2. CUDA_PATH env
    env_path = os.environ.get("CUDA_PATH")
    if env_path:
        p = pathlib.Path(env_path)
        if (p / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (CUDA_PATH): {p}")
            return str(p)

    # 3. CUDA_HOME env
    env_home = os.environ.get("CUDA_HOME")
    if env_home:
        p = pathlib.Path(env_home)
        if (p / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (CUDA_HOME): {p}")
            return str(p)

    # 4. nvcc on PATH
    nvcc = shutil.which("nvcc")
    if nvcc:
        sdk_root = pathlib.Path(nvcc).resolve().parent.parent
        if (sdk_root / "bin" / "nvcc").exists():
            log.debug(f"CUDA SDK (PATH nvcc): {sdk_root}")
            return str(sdk_root)

    # 5. Glob /usr/local/cuda-* sorted descending, prefer 12.x
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

    # 6. /usr/local/cuda symlink
    default = pathlib.Path("/usr/local/cuda")
    if default.exists() and (default / "bin" / "nvcc").exists():
        log.debug(f"CUDA SDK (default symlink): {default}")
        return str(default.resolve())

    # 7. CMakeCache.txt in build/
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
    """
    Auto-detect GPU architecture. Returns e.g. "sm_86" or None.
    """
    # 1. CMakeCache.txt
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

    # 2. compile_commands.json entries
    if compile_commands:
        for entry in compile_commands:
            cmd = entry.get("command", "") or " ".join(entry.get("arguments", []))
            m = re.search(r"compute_(\d+)", cmd)
            if m:
                arch = m.group(1)
                log.debug(f"CUDA arch (compile_commands): sm_{arch}")
                return f"sm_{arch}"

    return None


# ============================================================
# compile_commands.json translation (nvcc → clangd-compatible)
# ============================================================

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
    """
    Translate nvcc compile_commands.json entries to clangd-compatible ones.
    Filters to .cu/.cuh files, strips nvcc flags, adds clangd CUDA flags.
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

            # --options-file <path> (two-arg form)
            if arg == "--options-file" and i + 1 < len(args):
                expanded = _expand_rsp_file(args[i + 1], entry.get("directory", base_dir))
                new_args.extend(expanded)
                skip_next = True
                continue

            # @rsp_file
            if arg.startswith("@"):
                expanded = _expand_rsp_file(arg[1:], entry.get("directory", base_dir))
                new_args.extend(expanded)
                continue

            # Skip nvcc binary path or -x cu (we add our own)
            if arg == "-x" and i + 1 < len(args) and args[i + 1] == "cu":
                skip_next = True
                continue

            # Skip nvcc-specific flags with values
            if arg in ("-arch", "-code", "--gpu-architecture", "--gpu-code") and i + 1 < len(args):
                skip_next = True
                continue

            new_args.append(arg)

        # Add clangd CUDA flags
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
    """
    Read the original compile_commands.json, translate CUDA entries,
    write to a cache dir. Returns the path to the cache dir.
    """
    cache_dir = os.path.join(project_root, ".cache", "mcp-cuda")
    os.makedirs(cache_dir, exist_ok=True)

    # Find original compile_commands.json
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
        log.debug("No compile_commands.json found — generating minimal entries from .cu files")
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
    """True if the project is actually a CUDA project.

    A project counts as CUDA if its compile_commands.json references at least
    one .cu/.cuh translation unit, or if the source tree contains any .cu/.cuh
    file. A bare CUDA SDK installed on the host is NOT sufficient — that only
    means clangd *could* run, not that there is anything for it to index.
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


# ============================================================
# LSP framing
# ============================================================

def encode_lsp_message(body: dict) -> bytes:
    text = json.dumps(body)
    encoded = text.encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n"
    return header.encode("ascii") + encoded


async def read_lsp_message(reader: asyncio.StreamReader) -> Optional[dict]:
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


# ============================================================
# Utility helpers
# ============================================================

def uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def path_to_uri(path: str) -> str:
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
    abs_path = pathlib.Path(uri_to_path(uri))
    try:
        return str(abs_path.relative_to(project_root))
    except ValueError:
        return str(abs_path)


def _detect_language(file_path: str) -> str:
    suffix = pathlib.Path(file_path).suffix.lower()
    if suffix in (".cu", ".cuh"):
        return "cuda"
    if suffix == ".c":
        return "c"
    return "cpp"


# ============================================================
# ClangdClient (CUDA-aware)
# ============================================================

class ClangdClient:

    def __init__(self) -> None:
        self.project_root: str = ""
        self.cuda_path: str = ""
        self.cuda_arch: str = ""
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_id: int = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._diagnostics: Dict[str, List] = {}
        # Diagnostics are a LEVEL, not an edge. _diag_gen counts publishes per
        # uri and is bumped in the same breath as _diagnostics[uri], so a caller
        # can tell "already arrived" from "still owed" instead of clearing an
        # Event and then waiting forever for a push that already arrived and
        # will never repeat (see get_diagnostics).
        self._diag_gen: Dict[str, int] = {}           # uri -> publish count
        # One future PER WAITER, not one shared Event: two concurrent
        # diagnostics calls on the same uri must both be woken by one publish,
        # and neither may clear a signal out from under the other.
        self._diag_waiters: Dict[str, List[asyncio.Future]] = {}
        self._opened_files: set = set()
        # uri -> count of notifications this client actually SENT for the doc.
        # open_document bumps it only when it really emitted a didOpen, which is
        # what makes it a truthful answer to "is a publish owed?".
        self._doc_versions: Dict[str, int] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._indexing_done: asyncio.Event = asyncio.Event()
        self._active_progress: set = set()
        self._send_lock = asyncio.Lock()

    async def start(self, project_root: str, cuda_path: str, cuda_arch: str,
                    clangd_path: str = "clangd",
                    compile_commands_dir: Optional[str] = None) -> str:
        if self.process is not None:
            return "already initialized"

        self.project_root = str(pathlib.Path(project_root).resolve())
        self.cuda_path = cuda_path
        self.cuda_arch = cuda_arch
        self._indexing_done.clear()

        if compile_commands_dir:
            compile_commands_dir = str(pathlib.Path(compile_commands_dir).resolve())

        # Translate compile_commands.json for CUDA
        translated_dir = _prepare_compile_commands(
            self.project_root, cuda_path, cuda_arch, compile_commands_dir
        )

        args = [
            clangd_path,
            "--background-index",
            "--clang-tidy=false",
            "--header-insertion=never",
            "--pch-storage=memory",
            f"--compile-commands-dir={translated_dir}",
        ]

        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
            cwd=self.project_root,
        )
        log.debug(f"clangd PID: {self.process.pid} (CUDA mode, SDK={cuda_path}, arch={cuda_arch})")

        self._reader_task = asyncio.create_task(self._reader_loop())

        init_params = {
            "processId": self.process.pid,
            "rootUri": path_to_uri(self.project_root),
            "workspaceFolders": [{"uri": path_to_uri(self.project_root), "name": "workspace"}],
            "initializationOptions": {
                "compilationDatabasePath": translated_dir,
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
            log.debug("Indexing wait timed out — priming index by opening source files...")

        await self._prime_index()

        version = response.get("result", {}).get("serverInfo", {})
        return f"clangd initialized (CUDA) at {self.project_root} — SDK={cuda_path} arch={cuda_arch} — {version}"

    async def _prime_index(self) -> None:
        source_files = []
        for root, _, files in os.walk(self.project_root):
            if any(part.startswith(".") or part in ("build", "out", "dist", ".git")
                   for part in pathlib.Path(root).parts):
                continue
            for fname in files:
                if pathlib.Path(fname).suffix.lower() in {".cu", ".cuh"}:
                    source_files.append(os.path.join(root, fname))
                    if len(source_files) >= 10:
                        break
            if len(source_files) >= 10:
                break

        if source_files:
            log.debug(f"Priming index with {len(source_files)} CUDA file(s)")
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
        self._doc_versions.clear()
        self._diagnostics.clear()
        self._diag_gen.clear()
        # Waiters outlive the backend only as stranded futures; wake them so a
        # request parked on a publish that will now never come is not left to
        # burn its full timeout against a dead client.
        for waiters in self._diag_waiters.values():
            for fut in waiters:
                if not fut.done():
                    fut.set_result(None)
        self._diag_waiters.clear()

    async def _reader_loop(self) -> None:
        assert self.process and self.process.stdout
        reader = self.process.stdout
        while True:
            msg = await read_lsp_message(reader)
            if msg is None:
                log.debug("clangd stdout EOF")
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
                        log.debug("All progress tokens finished — indexing done")
                        self._indexing_done.set()

            elif method == "textDocument/publishDiagnostics":
                params = msg.get("params", {})
                uri = params.get("uri", "")
                diags = params.get("diagnostics", [])
                self._diagnostics[uri] = diags
                # Bump the generation in the SAME breath as the payload, and
                # wake every waiter rather than setting one shared Event.
                self._diag_gen[uri] = self._diag_gen.get(uri, 0) + 1
                for fut in self._diag_waiters.pop(uri, ()):
                    if not fut.done():
                        fut.set_result(None)
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
        # Record the notification BEFORE awaiting it out: get_diagnostics reads
        # this to decide whether a publish is owed, and a didOpen that is queued
        # but not yet drained still owes one.
        self._doc_versions[uri] = self._doc_versions.get(uri, 0) + 1
        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": _detect_language(str(abs_path)),
                "version": 1,
                "text": content,
            }
        })

    # ── LSP method wrappers ──────────────────────────────────

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
        """Open the document, wait only for a publish that is actually OWED,
        and return the diagnostics.

        This used to clear a per-uri Event and then wait for the push that would
        set it again. For an already-open file that push had ALREADY arrived —
        _prime_index opens up to ten CUDA sources at startup, so the common case
        is a document clangd published for long ago — and open_document returns
        early for a uri in _opened_files, so nothing would ever re-publish. The
        clear discarded the only signal there was: every such call burned its
        full timeout and then returned the correct cached answer anyway.

        The signal is a LEVEL, so it is read as one. Two samples taken around
        the open answer the two separate questions:

        * is a publish owed?  -> the document's own version counter. Only
          open_document bumps it, and only when it actually sent a didOpen. An
          already-open file means nothing was sent, so nothing is owed and the
          cache is already the answer.
        * has it landed yet?  -> _diag_gen, bumped by the reader task next to
          the payload. Sampled BEFORE the open, so a publish that races in
          while we open is still seen as fresh rather than waited for twice.

        Freshness is not traded away relative to what this file could ever
        deliver: a first open still sends a didOpen carrying the file's current
        on-disk text, still bumps the version, and still makes this wait for the
        publish that answers it.
        """
        uri = self._abs_uri(path)
        gen_before = self._diag_gen.get(uri, 0)
        ver_before = self._doc_versions.get(uri, 0)
        await self.open_document(path)
        ver_after = self._doc_versions.get(uri, 0)

        if ver_after > ver_before and self._diag_gen.get(uri, 0) == gen_before:
            # A notification went out and its publish has not arrived yet.
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._diag_waiters.setdefault(uri, []).append(fut)
            try:
                # Passive wait: our didOpen is already sent and no exchange of
                # ours is in flight, so the backend lock is dropped for its
                # duration (see _backend_lock_released) and retaken before the
                # cache read below. Held, it would make concurrent diagnostics
                # calls wait one after another instead of together.
                async with _backend_lock_released():
                    await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                pass
            finally:
                waiters = self._diag_waiters.get(uri)
                if waiters is not None and fut in waiters:
                    waiters.remove(fut)
                    if not waiters:
                        self._diag_waiters.pop(uri, None)
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


# ============================================================
# Global state
# ============================================================

_client: Optional[ClangdClient] = None


def _require_client() -> ClangdClient:
    if _client is None or _client.process is None:
        raise RuntimeError("CUDA clangd not initialized — call cuda_init first")
    return _client


# ============================================================
# Backend serialization
# ============================================================
#
# run() now dispatches every request as its own task, so two requests can be
# inside the client at once. Nothing in ClangdClient tolerates that on its own:
# _request reads and increments _next_id and then stores _pending[req_id], the
# reader task routes a reply purely by that id, and _opened_files / _diagnostics
# / _doc_versions are one flat cache per client. Interleaving two requests there
# does not merely slow clangd down, it can hand a reply to the WRONG waiter —
# and a clangd session corrupted that way answers wrong rather than failing
# loudly, the worst failure mode available here.
#
# So: one lock per backend, held for the whole request that touched the backend.
# This server has exactly ONE backend (the CUDA clangd behind _client), so the
# registry has a single fixed key; it is written as a per-backend map anyway to
# match mcp-purity, from which this design is ported, and so that a second
# backend cannot be added without inheriting the discipline.
#
# The lock is acquired at the single funnel that knows the backend type:
# handle_cuda_call, immediately before it invokes a handler. Every one of the
# fourteen handlers touches this one backend — thirteen via _require_client, and
# cuda_init by creating it — and the key is a constant, so unlike mcp-purity
# (where the key is derived from the file type and a drifted copy of that
# derivation would lock the WRONG backend) the dispatcher is the correct and
# safest place for it. _require_client is sync and cannot await a lock, and
# making it async would rewrite thirteen semantic handlers to no benefit.
#
# Pure non-LSP work takes no lock at all: the bare-status reply, the recursion
# guard, the unknown-function reply, and the MCP-level initialize / ping /
# tools/list are answered while a backend cold start holds the lock.
#
# Locks are created lazily inside the running loop, never at import: on Python
# 3.9 (this file's floor) asyncio.Lock() still binds get_event_loop() at
# construction time. The check-and-create in _hold_backend has no await between
# the lookup and the store, so concurrent first-callers cannot end up with two
# different locks for one backend.
_backend_locks: Dict[str, "asyncio.Lock"] = {}

# The one backend this server owns.
CLANGD_BACKEND = "clangd"

# The set of backend types whose lock the CURRENT request owns, or None when no
# request session is installed. A ContextVar because each request is its own
# task and tasks copy the context at creation, so this is per-request state that
# needs no plumbing through every handler signature.
_BACKEND_SESSION: "contextvars.ContextVar[Optional[set]]" = contextvars.ContextVar(
    "cuda_backend_session", default=None
)


async def _hold_backend(backend_type: str = CLANGD_BACKEND) -> None:
    """Take *backend_type*'s lock for the REST OF THIS REQUEST.

    Idempotent: a request that already owns the lock returns at once, so a
    caller may funnel through here more than once without self-deadlocking on a
    non-reentrant asyncio.Lock.

    Outside a request session there is no scope that could release the lock, so
    this warns and proceeds unserialized — exactly the pre-concurrency behaviour
    an in-process caller already had.
    """
    session = _BACKEND_SESSION.get()
    if session is None:
        log.warning("No request session installed; backend %r runs unserialized",
                    backend_type)
        return
    if backend_type in session:
        return
    lock = _backend_locks.get(backend_type)
    if lock is None:
        lock = asyncio.Lock()
        _backend_locks[backend_type] = lock
    await lock.acquire()
    # No await between acquire() returning and this line, so a cancellation can
    # never strand an acquired-but-unrecorded lock.
    session.add(backend_type)


def _release_backends(session: set) -> None:
    """Release every backend lock *session* acquired.

    Called from the dispatcher's finally so a handler that raised or was
    cancelled cannot strand a lock and wedge the backend for the life of the
    process.
    """
    while session:
        backend_type = session.pop()
        lock = _backend_locks.get(backend_type)
        if lock is not None and lock.locked():
            lock.release()


@contextlib.contextmanager
def _backend_session():
    """Install a fresh request session and release whatever it acquired.

    Used by the dispatcher and by the auto-init task, which reaches handle_init
    without passing through the dispatcher and would otherwise race a wire-borne
    cuda_init into a second clangd process.
    """
    session: set = set()
    token = _BACKEND_SESSION.set(session)
    try:
        yield session
    finally:
        # Release before the ContextVar reset: a stranded backend lock would
        # wedge the backend for the life of the process, and this finally is the
        # only scope that runs on the raise/cancel paths too.
        _release_backends(session)
        _BACKEND_SESSION.reset(token)


@contextlib.asynccontextmanager
async def _backend_lock_released():
    """Drop this request's backend lock(s) across a PASSIVE wait, then retake.

    "Passive" means precisely this: no request/response exchange of ours is in
    flight for the duration, we are parked on a push the BACKGROUND reader task
    will deliver. Holding the lock across such a wait serializes waiting itself,
    turning a batch of diagnostics calls from overlapping waits into consecutive
    ones.

    What is NOT protected across the gap: another request may take the backend,
    open documents and issue its own exchanges. Callers must therefore have
    finished all of their own protocol traffic BEFORE entering, and must treat
    anything they re-read afterwards as possibly refreshed. The one caller
    (get_diagnostics) satisfies this: its didOpen is already sent, and all it
    does on the far side is read the _diagnostics cache the reader task fills.

    Deadlock-free by construction: the reader task that fires the awaited future
    never takes a backend lock, so the wakeup cannot depend on the lock we just
    dropped. Retaking goes through _hold_backend, so it may queue behind another
    request but can never self-deadlock (that call is idempotent).

    Cancellation-safe: the locks leave _BACKEND_SESSION on release and only
    rejoin it on a successful retake, so a handler cancelled or raising inside
    the gap leaves nothing for the dispatcher's finally to strand, and one
    cancelled while retaking never holds what it did not record.
    """
    session = _BACKEND_SESSION.get()
    held = sorted(session) if session else []
    for backend_type in held:
        lock = _backend_locks.get(backend_type)
        if lock is not None and lock.locked():
            lock.release()
        session.discard(backend_type)
    try:
        yield
    finally:
        for backend_type in held:
            await _hold_backend(backend_type)


# ============================================================
# Location formatting helpers
# ============================================================

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
    for s in symbols:
        if not isinstance(s, dict):
            continue
        yield s
        for child in _iter_document_symbols(s.get("children") or []):
            yield child


# ============================================================
# Filesystem fallback for workspace_symbols (static-inline / __device__
# helpers in headers — clangd excludes internal-linkage symbols from
# the global workspace/symbol index)
# ============================================================

_FALLBACK_EXTS = (".cu", ".cuh", ".c", ".cc", ".cpp", ".cxx",
                  ".h", ".hpp", ".hh", ".hxx")
_FALLBACK_SKIP_DIRS = {
    "build", "vendor", "third_party", "third-party", "node_modules",
    ".git", ".cache", ".clangd", ".ccache", "_deps",
}


def _find_files_with_word(root: str, word: str, exts=_FALLBACK_EXTS,
                          limit: int = 20) -> List[str]:
    """
    Walk *root* and return up to *limit* source files whose content contains
    *word* as a whole identifier (\\bword\\b). Pure Python, no shell deps.
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


async def _fallback_workspace_symbols(client: ClangdClient, query: str,
                                       limit: int = 50) -> List[dict]:
    """
    Locate symbols clangd's global index drops (notably static-inline,
    `__device__`, and other internal-linkage helpers in CUDA headers) by
    grepping the project for the identifier, then asking clangd for the
    DocumentSymbol of each candidate file and filtering by name.
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
        for sym in _iter_document_symbols(doc_syms):
            name = sym.get("name", "")
            if query not in name:
                continue
            # Reshape into SymbolInformation-like dict so the existing
            # handler/formatter code paths work unchanged.
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


# ============================================================
# Symbol lookup: name → position
# ============================================================

async def _symbol_to_location(client: ClangdClient, symbol_name: str,
                               preferred_path: Optional[str] = None,
                               max_retries: int = 3) -> Optional[dict]:
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

    return None


# ============================================================
# Handler functions
# ============================================================

async def handle_init(args: dict) -> Any:
    global _client
    project_root = args.get("project_root", "")
    if not project_root:
        return {"error": "project_root is required"}

    clangd_path = args.get("clangd_path", "clangd")
    compile_commands_dir = args.get("compile_commands_dir")
    explicit_cuda_path = args.get("cuda_path")
    explicit_cuda_arch = args.get("cuda_arch")

    if _client is not None and _client.process is not None:
        return {"status": "already initialized", "project_root": _client.project_root,
                "cuda_path": _client.cuda_path, "cuda_arch": _client.cuda_arch}

    # Discover CUDA SDK
    cuda_path = _find_cuda_sdk(explicit_cuda_path, project_root)
    if not cuda_path:
        return {"error": "CUDA SDK not found. Provide cuda_path or set CUDA_PATH/CUDA_HOME."}

    # Load compile_commands to help detect arch
    cc_entries = None
    for d in [compile_commands_dir, os.path.join(project_root, "build"), project_root]:
        if d and os.path.isfile(os.path.join(d, "compile_commands.json")):
            try:
                with open(os.path.join(d, "compile_commands.json")) as f:
                    cc_entries = json.load(f)
                break
            except Exception:
                pass

    # A CUDA SDK on the host is not a reason to spin up clangd or write a
    # .cache/mcp-cuda dir — only do that for projects that actually contain
    # CUDA sources (or reference them in compile_commands.json).
    if not _has_cuda_sources(project_root, cc_entries):
        log.debug("No .cu/.cuh sources and no CUDA compile entries — skipping CUDA init")
        return {"status": "skipped", "reason": "no CUDA sources in project"}

    cuda_arch = explicit_cuda_arch or _detect_cuda_arch(project_root, cc_entries) or "sm_86"

    _client = ClangdClient()
    try:
        msg = await _client.start(project_root, cuda_path, cuda_arch,
                                  clangd_path, compile_commands_dir)
        return {"status": "ok", "message": msg, "project_root": _client.project_root,
                "cuda_path": cuda_path, "cuda_arch": cuda_arch}
    except Exception as e:
        _client = None
        return {"error": str(e)}


async def handle_find_definition(args: dict) -> Any:
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    path = args.get("path", "")
    context_lines = int(args.get("context_lines", 5))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    loc = await _symbol_to_location(client, symbol_name, preferred_path=path or None)
    if not loc:
        return {"error": f"Symbol '{symbol_name}' not found in workspace"}

    path, line, char = loc["path"], loc["line"], loc["char"]
    def_result = await client.definition(path, line, char)
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


async def handle_find_definition_at(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    context_lines = int(args.get("context_lines", 5))
    if not path:
        return {"error": "path is required"}

    def_result = await client.definition(client._abs_path(path), line, char)
    if not def_result:
        return {"error": "No definition found"}

    locations = def_result if isinstance(def_result, list) else [def_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        abs_path = uri_to_path(location["uri"])
        def_line = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(abs_path, def_line, context_lines),
        })
    return results


async def handle_find_references(args: dict) -> Any:
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    path = args.get("path", "")
    max_results = int(args.get("max_results", 50))
    context_lines = int(args.get("context_lines", 3))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    if path:
        await client.open_document(client._abs_path(path))

    symbols = await client.workspace_symbol(symbol_name)
    all_refs = []
    seen = set()

    for sym in symbols:
        if sym.get("name") != symbol_name:
            continue
        kind = symbol_kind_name(sym.get("kind", 0))
        if kind not in DEFINITION_KINDS:
            continue
        loc = sym.get("location", {})
        uri = loc.get("uri", "")
        start = loc.get("range", {}).get("start", {})
        path = uri_to_path(uri)
        line = start.get("line", 0)
        char = start.get("character", 0)
        refs = await client.references(path, line, char)
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
            abs_path = uri_to_path(location["uri"])
            ref_line = location["range"]["start"]["line"]
            all_refs.append({
                "symbol": symbol_name,
                "location": location,
                "context": extract_surrounding_code(abs_path, ref_line, context_lines) if context_lines > 0 else None,
            })
        if max_results > 0 and len(all_refs) >= max_results:
            break

    return {
        "symbol": symbol_name,
        "count": len(all_refs),
        "references": all_refs,
    }


async def handle_find_references_at(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    max_results = int(args.get("max_results", 50))
    context_lines = int(args.get("context_lines", 3))
    if not path:
        return {"error": "path is required"}

    abs_path = client._abs_path(path)
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
        ref_abs_path = uri_to_path(location["uri"])
        ref_line = location["range"]["start"]["line"]
        all_refs.append({
            "location": location,
            "context": extract_surrounding_code(ref_abs_path, ref_line, context_lines) if context_lines > 0 else None,
        })

    return {
        "count": len(all_refs),
        "references": all_refs,
    }


async def handle_find_implementations_at(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    context_lines = int(args.get("context_lines", 5))
    if not path:
        return {"error": "path is required"}

    impls = await client.implementation(client._abs_path(path), line, char)
    results = []
    for payload in impls:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        abs_path = uri_to_path(location["uri"])
        impl_line = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(abs_path, impl_line, context_lines),
        })
    return results


async def handle_workspace_symbols(args: dict) -> Any:
    """
    Search workspace symbols by query string.
    params: { query, limit?, strict? }

    When clangd's global index returns no hits (e.g. for static-inline or
    `__device__` helpers in CUDA headers — internal-linkage symbols are
    excluded from workspace/symbol), a filesystem fallback runs: grep the
    project for the identifier, then re-query via documentSymbol on each
    candidate file. Pass strict=true to disable the fallback.
    """
    client = _require_client()
    query = args.get("query", "")
    limit = int(args.get("limit", 50))
    strict = _bool_param(args.get("strict"), default=False)
    if not query:
        return {"error": "query is required"}

    symbols = await client.workspace_symbol(query)
    fallback_used = False
    if not symbols and not strict and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", query):
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
    return out


async def handle_document_outline(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    if not path:
        return {"error": "path is required"}

    abs_path = client._abs_path(path)
    symbols = await client.document_symbol(abs_path)
    file_uri = pathlib.Path(abs_path).as_uri()

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

    return [fmt(s) for s in symbols]


async def handle_symbol_context(args: dict) -> Any:
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    path = args.get("path", "")
    max_references = int(args.get("max_references", 20))
    context_lines = int(args.get("context_lines", 5))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    definition = await handle_find_definition({"symbol_name": symbol_name, "path": path, "context_lines": context_lines})
    references = await handle_find_references({
        "symbol_name": symbol_name,
        "path": path,
        "max_results": max_references,
        "context_lines": 2,
    })
    return {
        "symbol": symbol_name,
        "definition": definition,
        "references": references,
    }


async def handle_inlay_hints(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    start_line = int(args.get("start_line", 1)) - 1
    end_line = int(args.get("end_line", 9999)) - 1
    limit = int(args.get("limit", 100))
    if not path:
        return {"error": "path is required"}

    hints = await client.inlay_hints(client._abs_path(path), start_line, end_line)
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
    return results


async def handle_symbol_change_impact(args: dict) -> Any:
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    path = args.get("path", "")
    max_references = int(args.get("max_references", 50))
    depth = int(args.get("call_hierarchy_depth", 1))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    definition = await handle_find_definition({"symbol_name": symbol_name, "path": path, "context_lines": 3})
    references = await handle_find_references({
        "symbol_name": symbol_name,
        "path": path,
        "max_results": max_references,
        "context_lines": 2,
    })

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

    return {
        "symbol": symbol_name,
        "definition": definition,
        "references": references,
        "reference_summary": {
            "count": references.get("count", 0) if isinstance(references, dict) else 0,
            "files": ref_files,
        },
        "call_hierarchy": call_hierarchies,
    }


async def handle_hover(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    if not path:
        return {"error": "path is required"}

    result = await client.hover(client._abs_path(path), line, char)
    if not result:
        return {"error": "No hover information at this position"}

    contents = result.get("contents")
    text = _flatten_hover(contents)
    hover_range = result.get("range")
    location = None
    if hover_range:
        uri = pathlib.Path(client._abs_path(path)).as_uri()
        location = _format_location(uri, hover_range, client.project_root)
    return {
        "text": text,
        "location": location,
    }


async def handle_diagnostics(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    timeout = float(args.get("timeout", 10.0))
    if not path:
        return {"error": "path is required"}

    abs_path = client._abs_path(path)
    diags = await client.get_diagnostics(abs_path, timeout=timeout)
    severity_map = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}
    results = []
    for d in diags:
        lsp_range = d.get("range", {})
        start = lsp_range.get("start", {})
        uri = pathlib.Path(abs_path).as_uri()
        results.append({
            "message": d.get("message", ""),
            "severity": severity_map.get(d.get("severity", 0), "Unknown"),
            "code": d.get("code"),
            "source": d.get("source"),
            "location": _format_location(uri, lsp_range, client.project_root),
        })
    return {
        "path": _relative_path(pathlib.Path(abs_path).as_uri(), client.project_root),
        "count": len(results),
        "diagnostics": results,
    }


async def handle_deduced_type_at(args: dict) -> Any:
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    if not path:
        return {"error": "path is required"}

    result = await client.hover(client._abs_path(path), line, char)
    if not result:
        return {"error": "No hover information at this position"}

    contents = result.get("contents")
    raw_text = _flatten_hover(contents).strip()
    deduced = _infer_type(raw_text)

    hover_range = result.get("range")
    location = None
    if hover_range:
        uri = pathlib.Path(client._abs_path(path)).as_uri()
        location = _format_location(uri, hover_range, client.project_root)

    return {
        "type": deduced,
        "raw": raw_text,
        "location": location,
    }


# ============================================================
# Call hierarchy helper
# ============================================================

async def _collect_call_hierarchy(
    client: ClangdClient, path: str, line: int, char: int, depth: int
) -> Optional[dict]:
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


async def _expand_hierarchy_item(
    client: ClangdClient, item: dict, depth: int, seen: set
) -> Optional[dict]:
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


# ============================================================
# Handler registry
# ============================================================

ALL_HANDLERS = {
    "cuda_init":                    handle_init,
    "cuda_find_definition":         handle_find_definition,
    "cuda_find_definition_at":      handle_find_definition_at,
    "cuda_find_references":         handle_find_references,
    "cuda_find_references_at":      handle_find_references_at,
    "cuda_find_implementations_at": handle_find_implementations_at,
    "cuda_workspace_symbols":       handle_workspace_symbols,
    "cuda_document_outline":        handle_document_outline,
    "cuda_symbol_context":          handle_symbol_context,
    "cuda_inlay_hints":             handle_inlay_hints,
    "cuda_symbol_change_impact":    handle_symbol_change_impact,
    "cuda_hover":                   handle_hover,
    "cuda_diagnostics":             handle_diagnostics,
    "cuda_deduced_type_at":         handle_deduced_type_at,
}


# ============================================================
# MCP dispatcher
# ============================================================

async def handle_cuda_call(args: dict, server: Optional["McpServer"] = None) -> str:
    function = args.get("function", "")
    raw_params = args.get("params") or {}
    try:
        params = _resolve_aliases(raw_params)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    def _serialize(fn: str, data: Any) -> str:
        if MARKDOWN_MODE:
            return _result_to_markdown(fn, data)
        return json.dumps(data, ensure_ascii=False)

    if not function:
        if server and server._init_task and not server._init_task.done():
            return _serialize("", {"status": "initializing", "project_root": server.auto_project_root or "—"})
        status = "running" if (_client and _client.process) else "not initialized"
        root = _client.project_root if _client else "—"
        cuda_path = _client.cuda_path if _client else "—"
        cuda_arch = _client.cuda_arch if _client else "—"
        return _serialize("", {"status": status, "project_root": root,
                               "cuda_path": cuda_path, "cuda_arch": cuda_arch})

    if function == "cuda_call":
        return _serialize("", {"error": "Cannot dispatch cuda_call recursively"})

    handler = ALL_HANDLERS.get(function)
    if handler is None:
        available = ", ".join(sorted(ALL_HANDLERS.keys()))
        return _serialize("", {"error": f"Unknown function: '{function}'. Available: {available}"})

    # The auto-init gate runs BEFORE the backend lock is taken, and must: the
    # init task holds that same lock for its whole cold start, so waiting for it
    # while holding the lock would deadlock. Waiting first and locking second
    # only ever queues.
    if (function != "cuda_init"
            and server and server._init_task and not server._init_task.done()
            and (_client is None or _client.process is None)):
        log.debug(f"Waiting for auto-init to complete before {function}...")
        try:
            await asyncio.wait_for(asyncio.shield(server._init_task), timeout=90.0)
        except (asyncio.TimeoutError, Exception) as e:
            log.debug(f"Auto-init wait failed: {e}")

    # The funnel: every dispatched function touches the one backend, so the lock
    # is taken here and held until the reply is shaped. Everything answered
    # above this line — status, the recursion guard, an unknown function — takes
    # no lock and stays answerable during a cold start.
    with _backend_session():
        await _hold_backend()
        result = await handler(params)
        return _serialize(function, result)


# ============================================================
# Markdown output formatter
# ============================================================

def _md_loc(loc: dict) -> str:
    if not loc:
        return "?"
    path = loc.get("path", "?")
    start = loc.get("range_human", {}).get("start", {})
    line = start.get("line", "?")
    return f"{path}:{line}"


def _md_context(ctx: Any) -> str:
    if not ctx:
        return ""
    return f"\n```cuda\n{ctx.strip()}\n```"


def _md_location_block(loc: dict, context: Any = None) -> str:
    line_text = loc.get("line_text", "")
    ref = _md_loc(loc)
    out = f"{ref}"
    if line_text:
        out += f" — `{line_text.strip()}`"
    if context:
        out += _md_context(context)
    return out


def _md_call_hierarchy(ch: Any) -> list:
    """Render call-hierarchy nodes as an indented incoming(←)/outgoing(→) tree."""
    out: list = []

    def node(n: dict, depth: int, arrow: str = "") -> None:
        out.append(f"{'  ' * depth}- {arrow}{n.get('kind', '')} **{n.get('symbol', '')}** {_md_loc(n.get('location', {}))}")
        for c in (n.get("incoming") or []):
            node(c, depth + 1, "← ")
        for c in (n.get("outgoing") or []):
            node(c, depth + 1, "→ ")

    for item in (ch if isinstance(ch, list) else [ch]):
        if isinstance(item, dict) and "roots" in item:
            for root in item["roots"]:
                node(root, 0)
        elif isinstance(item, dict):
            node(item, 0)
    return out


def _result_to_markdown(function: str, result: Any) -> str:
    sep = "\n"

    if isinstance(result, dict) and "error" in result:
        return f"ERROR: {result['error']}"

    if not function:
        if isinstance(result, dict) and "status" in result:
            return f"status: {result['status']} | root: {result.get('project_root', '—')} | sdk: {result.get('cuda_path', '—')} | arch: {result.get('cuda_arch', '—')}"
        return json.dumps(result, ensure_ascii=False)

    if function in ("cuda_find_definition", "cuda_find_definition_at"):
        if not result:
            return "No definition found"
        parts = []
        for item in (result if isinstance(result, list) else [result]):
            sym = item.get("symbol", "")
            header = f"## Definition{': ' + sym if sym else ''}"
            parts.append(header + sep + _md_location_block(item.get("location", {}), item.get("context")))
        return sep.join(parts)

    if function == "cuda_find_references":
        sym = result.get("symbol", "")
        count = result.get("count", 0)
        refs = result.get("references", [])
        lines = [f"## References: {sym} ({count})"]
        for r in refs:
            lines.append("- " + _md_location_block(r.get("location", {})))
        return sep.join(lines)

    if function == "cuda_find_references_at":
        count = result.get("count", 0)
        refs = result.get("references", [])
        lines = [f"## References at position ({count})"]
        for r in refs:
            lines.append("- " + _md_location_block(r.get("location", {})))
        return sep.join(lines)

    if function == "cuda_find_implementations_at":
        if not result:
            return "No implementations found"
        lines = ["## Implementations"]
        for item in (result if isinstance(result, list) else [result]):
            lines.append("- " + _md_location_block(item.get("location", {}), item.get("context")))
        return sep.join(lines)

    if function == "cuda_workspace_symbols":
        # Handler returns envelope: {query, count, symbols, source?}
        if isinstance(result, dict):
            query = result.get("query", "")
            symbols = result.get("symbols") or []
            source = result.get("source")
        else:
            query = ""
            symbols = result if isinstance(result, list) else []
            source = None
        if not symbols:
            tail = f" for `{query}`" if query else ""
            return f"## Workspace Symbols\nNo symbols found{tail}"
        lines = ["## Workspace Symbols"]
        meta = []
        if query:
            meta.append(f"query=`{query}`")
        meta.append(f"count={len(symbols)}")
        if source:
            meta.append(f"source={source}")
        lines.append("_" + " · ".join(meta) + "_")
        for s in symbols:
            kind = s.get("kind", "")
            sym = s.get("symbol", s.get("name", ""))
            loc = _md_loc(s.get("location", {}))
            lines.append(f"- {kind} **{sym}** {loc}")
        return sep.join(lines)

    if function == "cuda_document_outline":
        lines = ["## Outline"]
        def _fmt_node(node: dict, indent: int = 0) -> None:
            kind = node.get("kind", "")
            sym = node.get("symbol", "")
            detail = node.get("detail", "")
            loc_node = node.get("selection") or node.get("location") or {}
            path = loc_node.get("path", "")
            rh = loc_node.get("range_human", {})
            start = rh.get("start", {})
            end = rh.get("end", {})
            line = start.get("line", "?")
            end_line = end.get("line", "?")
            line_text = loc_node.get("line_text", "")
            prefix = "  " * indent + "- "
            detail_str = f" `{detail}`" if detail else ""
            loc_str = f" {path}:{line}-{end_line}" if path else f" :{line}"
            text_str = f" — `{line_text.strip()}`" if line_text else ""
            lines.append(f"{prefix}{kind} **{sym}**{detail_str}{loc_str}{text_str}")
            for child in node.get("children", []):
                _fmt_node(child, indent + 1)
        for node in (result if isinstance(result, list) else [result]):
            _fmt_node(node)
        return sep.join(lines)

    if function == "cuda_symbol_context":
        sym = result.get("symbol", "")
        parts = [f"## Symbol: {sym}"]
        defs = result.get("definition", [])
        if defs:
            parts.append("### Definition")
            for d in (defs if isinstance(defs, list) else [defs]):
                parts.append(_md_location_block(d.get("location", {}), d.get("context")))
        refs_data = result.get("references", {})
        refs = refs_data.get("references", []) if isinstance(refs_data, dict) else []
        count = refs_data.get("count", len(refs)) if isinstance(refs_data, dict) else len(refs)
        if refs:
            parts.append(f"### References ({count})")
            for r in refs:
                parts.append("- " + _md_location_block(r.get("location", {})))
        return sep.join(parts)

    if function == "cuda_symbol_change_impact":
        sym = result.get("symbol", "")
        parts = [f"## Change Impact: {sym}"]
        defs = result.get("definition", [])
        if defs:
            parts.append("### Definition")
            for d in (defs if isinstance(defs, list) else [defs]):
                parts.append(_md_location_block(d.get("location", {}), d.get("context")))
        refs_data = result.get("references", {})
        refs = refs_data.get("references", []) if isinstance(refs_data, dict) else []
        summary = result.get("reference_summary", {})
        count = summary.get("count", len(refs))
        files = summary.get("files", [])
        if refs:
            parts.append(f"### References ({count})")
            for r in refs:
                parts.append("- " + _md_location_block(r.get("location", {})))
        if files:
            parts.append(f"### Affected Files ({len(files)})")
            for f in files:
                parts.append(f"- {f}")
        ch = result.get("call_hierarchy", [])
        if ch:
            parts.append("### Call Hierarchy")
            parts.extend(_md_call_hierarchy(ch))
        return sep.join(parts)

    if function == "cuda_inlay_hints":
        hints = result if isinstance(result, list) else result.get("hints", [])
        if not hints:
            return "No inlay hints"
        lines = [f"## Inlay Hints ({len(hints)})"]
        for h in hints:
            pos = h.get("position", {}).get("human", {})
            line = pos.get("line", "?")
            char = pos.get("character", "?")
            label = h.get("label", "")
            kind = h.get("kind", "")
            lines.append(f"- {line}:{char} `{label}` ({kind})")
        return sep.join(lines)

    if function == "cuda_hover":
        text = result.get("text", "")
        loc = result.get("location")
        header = "## Hover"
        if loc:
            header += f": {_md_loc(loc)}"
        return f"{header}{sep}```cuda{sep}{text.strip()}{sep}```"

    if function == "cuda_diagnostics":
        path = result.get("path", "")
        count = result.get("count", 0)
        diags = result.get("diagnostics", [])
        lines = [f"## Diagnostics: {path} ({count})"]
        for d in diags:
            sev = d.get("severity", "")
            msg = d.get("message", "")
            loc = _md_loc(d.get("location", {}))
            lines.append(f"- {sev}:{loc} {msg}")
        return sep.join(lines)

    if function == "cuda_deduced_type_at":
        type_str = result.get("type", result.get("text", ""))
        loc = result.get("location")
        header = "## Deduced Type"
        if loc:
            header += f": {_md_loc(loc)}"
        return f"{header}{sep}`{type_str}`"

    return json.dumps(result, ensure_ascii=False)


# ============================================================
# MCP Tool registry
# ============================================================

LISTED_TOOLS = [
    {
        "name": "cuda_call",
        "description": (
            "Call any CUDA code intelligence function by name. "
            "Returns server status if called without 'function'. "
            "Invoke the cuda-mcp skill for the full API reference."
            "\n\n"
            "When NOT to use:\n"
            "  - Build/compile → mcp-compile. Git → mcp-git. File search/edit → mcp-purity.\n"
            "  - Plain C/C++ (non-CUDA) → mcp-clangd.\n\n"
            "Prefer this OVER grep/Read-and-search for CUDA (.cu/.cuh) symbol navigation — "
            "clangd gives compiler-accurate definitions, references, types, and diagnostics "
            "that grep cannot.\n\n"
            "NEVER use grep, awk, sed, python scripts, ctags, cscope, or any ad-hoc "
            "text-matching hack for CUDA code navigation. This tool exists to replace them all "
            "with compiler-accurate results.\n\n"
            "IMPORTANT: Before first use, load the p:mcp-cuda skill for full API reference "
            "and parameter schemas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "Function name (e.g. cuda_init, cuda_find_definition, cuda_hover)",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the function (see cuda-mcp skill for schema)",
                },
            },
            "required": [],
        },
    }
]


# ============================================================
# MCP Server — JSON-RPC 2.0 over stdio
# ============================================================

class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, auto_project_root: Optional[str] = None,
                 auto_compile_commands_dir: Optional[str] = None,
                 auto_clangd_path: str = "clangd",
                 auto_cuda_path: Optional[str] = None,
                 auto_cuda_arch: Optional[str] = None) -> None:
        self.auto_project_root = auto_project_root
        self.auto_compile_commands_dir = auto_compile_commands_dir
        self.auto_clangd_path = auto_clangd_path
        self.auto_cuda_path = auto_cuda_path
        self.auto_cuda_arch = auto_cuda_arch
        self._init_task: Optional[asyncio.Task] = None

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(msg_id: Any, text: str) -> dict:
        return McpServer._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    async def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        log.debug(f"← {method} (id={msg_id})")

        if msg_id is None:
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-cuda", "version": "1.0.0"},
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
        tool_args = params.get("arguments") or {}
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except json.JSONDecodeError as exc:
                return self._tool_error(msg_id, f"'arguments' was a string but not valid JSON: {exc}")

        if name == "cuda_call":
            if not isinstance(tool_args, dict):
                return self._tool_error(
                    msg_id,
                    f"'arguments' must be an object; got {type(tool_args).__name__}."
                )
            try:
                result = await handle_cuda_call(tool_args, server=self)
                return self._result(msg_id, {"content": [{"type": "text", "text": result}]})
            except Exception as e:
                log.debug(f"cuda_call error: {e}")
                return self._tool_error(msg_id, f"Error: {e}")

        return self._tool_error(
            msg_id,
            f"Unknown tool: '{name}'. This server exposes 'cuda_call'. Invoke the cuda-mcp skill."
        )

    async def _auto_init(self) -> Any:
        """Run the startup init inside a backend session.

        The auto-init task reaches handle_init without passing through
        handle_cuda_call's funnel, so it must take the lock itself. Without it a
        cuda_init arriving on the wire during the cold start would sail past
        handle_init's `_client.process is not None` guard — _client is assigned
        before start() ever sets .process — and spawn a SECOND clangd, orphaning
        the first and leaving _client pointing at whichever finished last.
        """
        with _backend_session():
            await _hold_backend()
            return await handle_init({
                "project_root": self.auto_project_root,
                "clangd_path": self.auto_clangd_path,
                "compile_commands_dir": self.auto_compile_commands_dir,
                "cuda_path": self.auto_cuda_path,
                "cuda_arch": self.auto_cuda_arch,
            })

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.debug("mcp-cuda server ready (stdio)")

        if self.auto_project_root:
            log.debug(f"Auto-init: {self.auto_project_root}")
            self._init_task = asyncio.create_task(self._auto_init())

        # One task per request, and a reader executor of its OWN, on purpose.
        # This loop used to await the handler on the same line of control it
        # later awaited the readline, so for the whole duration of one call the
        # server did not READ: every other request sat unread in the pipe, timed
        # out client-side (~60s) and was then answered against an id the client
        # had already abandoned. From the caller's chair that is a dead server.
        # And the blocking calls here are the ORDINARY ones, not the failure
        # ones: the auto-init gate is 90s, the index barrier 60s, the clangd
        # handshake 30s, and diagnostics wait 10s by default.
        #
        # The executor must be dedicated rather than the default one, which is
        # shared with anything else that parks work there, so a saturated pool
        # can never delay the reader and reintroduce the deafness this fixes.
        #
        # No handler thread pool: _request does `req_id = self._next_id;
        # self._next_id += 1` and then stores _pending[req_id] with no await
        # between them, which is atomic ONLY on a single event-loop thread. A
        # worker pool would break exactly that invariant. Handlers are already
        # coroutines, so each becomes a task on this one loop, and every request
        # that reaches the backend is serialized by _hold_backend anyway.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cuda-stdin")
        inflight: set = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    # A closed/detached stdin raises rather than returning "";
                    # unguarded it escaped run() as a traceback.
                    log.debug(f"stdin read failed, shutting down: {exc}")
                    break
                if not line:
                    log.debug("stdin EOF — shutting down")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    # Answering is not optional: the bare `continue` that used to
                    # stand here left the caller's id unanswered until it timed
                    # out, which is indistinguishable from a hung server.
                    log.debug(f"JSON parse error: {e}")
                    self._write(self._error(None, -32700, f"Parse error: {e}"))
                    continue
                if not isinstance(msg, dict):
                    # `5` is valid JSON. It used to reach msg.get() below and
                    # take the process down with an AttributeError escaping
                    # run() — and an MCP client does not respawn a dead stdio
                    # server.
                    log.debug(f"Request was {type(msg).__name__}, not an object")
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue

                log.debug(f"← RAW: {line}")

                task = loop.create_task(self._serve(msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)

        finally:
            for task in inflight:
                task.cancel()
            reader.shutdown(wait=False)
            log.debug("Shutting down clangd (CUDA)...")
            # Graceful, NOT os._exit: this is what terminates the clangd child
            # (terminate, 3s grace, then kill) and lets asyncio.run() tear down
            # its subprocess transport. Exiting hard here would orphan a wedged
            # clangd — the process this server spawned outliving the server that
            # spawned it. The in-flight tasks are cancelled first so none is
            # still mid-_request against a pipe that is about to close.
            if _client is not None:
                await _client.stop()

    async def _serve(self, msg: dict) -> None:
        """One request, from dispatch to written reply. Runs as its own task."""
        try:
            response = await self.handle_message(msg)
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            self._write(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message.

        Called only from the event-loop thread: handlers may await, but _serve
        resumes on the loop after its await, so two replies cannot interleave on
        stdout and this needs no lock.
        """
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            # A non-serialisable payload used to kill the loop mid-write, i.e.
            # after some bytes were already on the wire.
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {exc}"))
        log.debug("→ RAW: %s", out)
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            # Unguarded, a client that hung up mid-reply escaped run().
            log.debug(f"stdout write failed: {exc}")


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="mcp-cuda — CUDA code intelligence MCP server via clangd"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Write debug output to this file instead of stderr")
    parser.add_argument("--markdown", action="store_true", help="Output tool results as markdown instead of JSON")
    parser.add_argument("--project-root", help="Auto-initialize for this project on startup")
    parser.add_argument("--compile-commands-dir", help="Directory containing compile_commands.json")
    parser.add_argument("--clangd-path", default="clangd", help="Path to clangd binary (default: clangd)")
    parser.add_argument("--cuda-path", help="Explicit CUDA SDK path (auto-detected if omitted)")
    parser.add_argument("--cuda-arch", help="GPU architecture, e.g. sm_86 (auto-detected if omitted)")
    parsed = parser.parse_args()

    global MARKDOWN_MODE
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
    if parsed.markdown:
        MARKDOWN_MODE = True

    server = McpServer(
        auto_project_root=parsed.project_root,
        auto_compile_commands_dir=parsed.compile_commands_dir,
        auto_clangd_path=parsed.clangd_path,
        auto_cuda_path=parsed.cuda_path,
        auto_cuda_arch=parsed.cuda_arch,
    )
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        log.debug("Server stopped")


if __name__ == "__main__":
    main()
