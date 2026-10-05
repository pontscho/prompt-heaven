#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""mcp-search -- MCP server for web search (DuckDuckGo lite, Bing on a DDG block) and code search (grep.app).

Single-tool dispatcher: search_call(function, params)

Functions:
    web     queries (str or list), limit -- DDG lite first; a DDG block moves
            this and every remaining query of the call to Bing
    code    queries (str or list), lang, repo, path, limit -- grep.app over
            public GitHub code

Usage:
    python3 mcp-search.py [--debug] [--log-file <path>]

The search itself is generated, not written here: Scripts/_mcp_websearch.py and
Scripts/_mcp_codesearch.py are the same blocks Scripts/search_duckduckgo.py and
Scripts/search_github.py carry, so the server answers with the markdown the
CLIs print. The HTTP client is Scripts/_mcp_chrome.py on its Chrome path (the
captured Chrome TLS ClientHello and header profiles; certificates are NOT
verified, private/loopback/metadata addresses are refused, no proxy).

What is this file's own: the per-endpoint session layer. A _ChSession is not
thread-safe, and the pool runs up to MAX_INFLIGHT_REQUESTS calls at once, so
each endpoint (ddg, bing, grep.app) owns ONE session, used only under that
endpoint's lock; the lock is also what makes the pacing process-wide. A lock
not acquired within ENDPOINT_LOCK_TIMEOUT answers `endpoint busy, retry later`,
and a call past CALL_DEADLINE stops; both keep the results already gathered.

Reply contract (FR-6): a query blocked at the end of its ladder (Bing for web,
grep.app for code) makes the call isError, with every other query's results
still in the text; no results is a success; a transport failure is the fixed
notice `transport error` and stays a success. No exception text, path or
traceback ever reaches the reply, and the search notes are logged as structure
only (endpoint, event, query index, status code or exception class).
"""

import argparse
import asyncio
import base64
import codecs  # the generated Chrome client
import ctypes.util  # the generated brotli/zstd decoders
import hashlib  # the generated Chrome client
import hmac  # the generated Chrome client
import http.client  # the generated Chrome client
import ipaddress  # the generated Chrome client
import json
import logging
import os
import random
import re
import socket  # the generated Chrome client
import ssl  # the generated Chrome client
import struct  # the generated Chrome client
import sys
import threading
import time
import unicodedata
import urllib.parse
import zlib  # the generated Chrome client
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

log = logging.getLogger("mcp-search")


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_logging.py :: _configure_logging
def _configure_logging(debug, log_file):
    """DEBUG to *log_file* (mode 0600) or to stderr, else WARNING. Never stdout.

    Either flag enables DEBUG: `--log-file` does not redirect the log, it turns
    it on. `Scripts/_mcp_logging.py` carries the rest -- why the 0600 pair needs
    both calls, and what deliberately stays out of this block.
    """
    level = logging.DEBUG if (debug or log_file) else logging.WARNING
    handlers = []
    if log_file:
        fd = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        handlers.append(logging.StreamHandler(os.fdopen(fd, "a")))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
# END GENERATED: f77d402d6254


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

# A fresh session every ROTATE_EVERY queries an endpoint serves: a new
# connection, a new cookie jar and new per-connection randomness (GREASE, key
# shares), so a long run does not ride one connection's identity. The same
# value as the CLIs; the count here is process-wide per endpoint.
ROTATE_EVERY = 4

# The body cap of every search session (_ch_session_new max_bytes: the wire AND
# the decoded limit), the same as the CLIs. A results page is tens of KiB; past
# the cap the call raises ChromeBodyTooLarge, which the search functions treat
# as a transport failure.
SEARCH_MAX_BYTES = 2 * 1024 * 1024

# How long a call waits for an endpoint's lock before answering `endpoint busy,
# retry later`. Bounds the residual the pool cannot otherwise avoid: every
# worker parked on one endpoint's lock.
ENDPOINT_LOCK_TIMEOUT = 120

# The whole call's budget, across all of its queries (F22): the lock timeout is
# per with_session call, so without it one 10-query call could hold a worker
# for ~10 x (lock wait + pacing + search). Checked before every lock wait (the
# wait itself is cut to what is left) and before every pacing sleep; a search
# already running is not interrupted, so a call ends at most one search (its
# own 15 s timeout, plus a warm-up) past it. On the deadline the call answers
# the results gathered so far plus a fixed notice.
CALL_DEADLINE = 180

# FR-10 caps, checked before any lock is taken.
MAX_QUERIES = 10
MAX_QUERY_CHARS = 512
MAX_FILTER_CHARS = 512
MAX_LIMIT = 50
DEFAULT_LIMIT = 10

# Upper bound of the caller's max_answer_chars.
MAX_ANSWER_CHARS_CEILING = 100000


# ---------------------------------------------------------------------------
# HTTP client (generated)
# ---------------------------------------------------------------------------

# The brotli decoder, generated from Scripts/_mcp_brotli.py: a ctypes binding
# to the system libbrotlidec, so `content-encoding: br` needs no third-party
# package. The source is taken whole (every block, in source order): a host
# carries all of a WHOLE_SOURCES source or none of it.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
# BEGIN GENERATED: _mcp_brotli.py :: BR_SONAMES_LINUX, BR_SONAMES_MACOS, BR_DIRS_LINUX, BR_DIRS_MACOS, BR_FILES_LINUX, BR_FILES_MACOS, BR_CHUNK_BYTES, _BR_RESULT_ERROR, _BR_RESULT_SUCCESS, _BR_RESULT_NEEDS_MORE_INPUT, _BR_RESULT_NEEDS_MORE_OUTPUT, _BR_SYMBOLS, _BR_STATE, _br_platform, _br_exists, _br_cdll, _br_find_library, _br_attempts, _br_configure, _br_load, _brotli_decompress
BR_SONAMES_LINUX = ("libbrotlidec.so.1",)


BR_SONAMES_MACOS = ("libbrotlidec.1.dylib",)


BR_DIRS_LINUX = ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")


BR_DIRS_MACOS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")


BR_FILES_LINUX = ("libbrotlidec.so.1", "libbrotlidec.so")


BR_FILES_MACOS = ("libbrotlidec.1.dylib", "libbrotlidec.dylib")


BR_CHUNK_BYTES = 64 * 1024


_BR_RESULT_ERROR = 0


_BR_RESULT_SUCCESS = 1


_BR_RESULT_NEEDS_MORE_INPUT = 2


_BR_RESULT_NEEDS_MORE_OUTPUT = 3


_BR_SYMBOLS = ("BrotliDecoderCreateInstance", "BrotliDecoderDecompressStream", "BrotliDecoderGetErrorCode", "BrotliDecoderErrorString", "BrotliDecoderDestroyInstance")


_BR_STATE = {}


def _br_platform():
    """The platform the load order is chosen for (stub hook)."""
    return sys.platform


def _br_exists(path):
    """Whether an absolute library path is a regular file (stub hook)."""
    return os.path.isfile(path)


def _br_cdll(name):
    """Open one shared library (stub hook).

    On darwin a name that is not absolute is refused before dlopen: dyld would
    resolve it against the process cwd, which a cloned repository controls (F36).
    """
    if _br_platform() == "darwin" and not os.path.isabs(name):
        raise OSError("refused: %r is not an absolute path (dyld searches the cwd for it)" % name)
    return ctypes.CDLL(name)


def _br_find_library(stem):
    """ctypes.util.find_library (stub hook) -- reached on darwin ONLY."""
    return ctypes.util.find_library(stem)


def _br_attempts():
    """The ordered load attempts for this platform, as (kind, arg) pairs.

    Pure: it reads only `_br_platform()` and the BR_ tuples. `kind` is
    "soname", "find_library" or "path". Linux carries no "find_library"
    entry at all.
    """
    platform = _br_platform()
    attempts = []
    if platform.startswith("linux"):
        for soname in BR_SONAMES_LINUX:
            attempts.append(("soname", soname))
        for directory in BR_DIRS_LINUX:
            for filename in BR_FILES_LINUX:
                attempts.append(("path", directory + "/" + filename))
    elif platform == "darwin":
        attempts.append(("find_library", "brotlidec"))
        for directory in BR_DIRS_MACOS:
            for filename in BR_FILES_MACOS:
                attempts.append(("path", directory + "/" + filename))
    else:
        for soname in BR_SONAMES_LINUX + BR_SONAMES_MACOS:
            attempts.append(("soname", soname))
    return attempts


def _br_configure(lib):
    """Set argtypes/restype of every symbol on a LOCAL, unpublished handle.

    Raises LookupError naming the first missing symbol. Pointers are c_void_p
    and sizes c_size_t: ctypes' default int restype truncates a 64-bit pointer.
    """
    for symbol in _BR_SYMBOLS:
        if not hasattr(lib, symbol):
            raise LookupError("libbrotlidec: missing symbol %s" % symbol)
    size_p = ctypes.POINTER(ctypes.c_size_t)
    void_pp = ctypes.POINTER(ctypes.c_void_p)
    lib.BrotliDecoderCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lib.BrotliDecoderCreateInstance.restype = ctypes.c_void_p
    lib.BrotliDecoderDecompressStream.argtypes = [ctypes.c_void_p, size_p, void_pp, size_p, void_pp, size_p]
    lib.BrotliDecoderDecompressStream.restype = ctypes.c_int
    lib.BrotliDecoderGetErrorCode.argtypes = [ctypes.c_void_p]
    lib.BrotliDecoderGetErrorCode.restype = ctypes.c_int
    lib.BrotliDecoderErrorString.argtypes = [ctypes.c_int]
    lib.BrotliDecoderErrorString.restype = ctypes.c_char_p
    lib.BrotliDecoderDestroyInstance.argtypes = [ctypes.c_void_p]
    lib.BrotliDecoderDestroyInstance.restype = None
    return lib


def _br_load():
    """Return the published (lib, path), loading it on first use.

    Raises LookupError("libbrotlidec not found; tried: ...") when no attempt
    yields a usable library; the failure is cached the same way a success is.
    """
    result = _BR_STATE.get("result")
    if result is None:
        tried = []
        for kind, arg in _br_attempts():
            name = arg
            if kind == "find_library":
                name = _br_find_library(arg)
                if not name or not os.path.isabs(name):
                    tried.append("find_library(%s)=%s" % (arg, name))
                    continue
            elif kind == "path":
                if not os.path.isabs(arg) or not _br_exists(arg):
                    tried.append(arg + " (absent)")
                    continue
            try:
                lib = _br_configure(_br_cdll(name))
            except (OSError, LookupError, AttributeError) as exc:
                tried.append("%s (%s)" % (name, str(exc).splitlines()[0] if str(exc) else type(exc).__name__))
                continue
            result = (lib, name)
            break
        if result is None:
            result = ("error", "libbrotlidec not found; tried: " + ", ".join(tried))
        _BR_STATE["result"] = result
    if isinstance(result[0], str) and result[0] == "error":
        raise LookupError(result[1])
    return result


def _brotli_decompress(data, max_output):
    """Decode one brotli stream; the decoder contract of the Chrome client.

    Returns the decoded bytes. Raises OverflowError when the output would
    exceed `max_output`, ValueError when the stream is corrupt, truncated,
    followed by trailing bytes, or the library breaks the call's invariants,
    and LookupError when libbrotlidec is absent.
    """
    lib = _br_load()[0]
    # `source`, `source_p` and `buf` own the memory the library reads and writes
    # through raw addresses (next_in, base); these locals must stay bound until
    # the loop below is done -- never drop or rebind them inside it.
    source = bytes(data)
    buf = ctypes.create_string_buffer(BR_CHUNK_BYTES)
    size = len(buf)
    base = ctypes.addressof(buf)
    source_p = ctypes.c_char_p(source)
    avail_in = ctypes.c_size_t(len(source))
    next_in = ctypes.c_void_p(ctypes.cast(source_p, ctypes.c_void_p).value)
    avail_out = ctypes.c_size_t(0)
    next_out = ctypes.c_void_p(0)
    total_out = ctypes.c_size_t(0)
    output = bytearray()
    total = 0
    last_in = len(source)
    state = lib.BrotliDecoderCreateInstance(None, None, None)
    if not state:
        raise ValueError("br: decoder instance could not be created")
    try:
        while True:
            avail_out.value = size
            next_out.value = base
            code = lib.BrotliDecoderDecompressStream(state, ctypes.byref(avail_in), ctypes.byref(next_in), ctypes.byref(avail_out), ctypes.byref(next_out), ctypes.byref(total_out))
            produced = size - avail_out.value
            if produced < 0 or produced > size:
                raise ValueError("br: decoder reported %d bytes into a %d-byte buffer" % (produced, size))
            if avail_in.value > last_in or total_out.value != total + produced:
                raise ValueError("br: decoder state inconsistent")
            progressed = produced > 0 or avail_in.value < last_in
            last_in = avail_in.value
            total += produced
            if total > max_output:
                raise OverflowError("br: output exceeds %d bytes" % max_output)
            if produced:
                output += ctypes.string_at(base, produced)
            if code == _BR_RESULT_SUCCESS:
                if avail_in.value:
                    raise ValueError("br: %d trailing bytes after the end of the stream" % avail_in.value)
                break
            if code == _BR_RESULT_NEEDS_MORE_OUTPUT:
                if not progressed:
                    raise ValueError("br: decoder state inconsistent")
                continue
            if code == _BR_RESULT_NEEDS_MORE_INPUT:
                if avail_in.value == 0:
                    raise ValueError("br: truncated stream")
                raise ValueError("br: decoder state inconsistent")
            if code == _BR_RESULT_ERROR:
                reason = lib.BrotliDecoderErrorString(lib.BrotliDecoderGetErrorCode(state))
                raise ValueError("br: %s" % (reason.decode("ascii", "replace") if reason else "decoder error"))
            raise ValueError("br: unknown decoder result %d" % code)
    finally:
        lib.BrotliDecoderDestroyInstance(state)
    return bytes(output)
# END GENERATED: b43e58bc4d20

# The zstd decoder, generated from Scripts/_mcp_zstd.py: a ctypes binding to
# the system libzstd for `content-encoding: zstd`, with the window and output
# ceilings the source argues. Taken whole, like the brotli region above.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
# BEGIN GENERATED: _mcp_zstd.py :: ZSTD_SONAMES_LINUX, ZSTD_SONAMES_MACOS, ZSTD_DIRS_LINUX, ZSTD_DIRS_MACOS, ZSTD_FILES_LINUX, ZSTD_FILES_MACOS, ZSTD_WINDOW_LOG_MAX, ZSTD_MAX_CHUNK_BYTES, _ZSTD_D_WINDOW_LOG_MAX, _ZSTD_FRAME_MAGIC, _ZSTD_SKIPPABLE_TAIL, _ZSTD_SYMBOLS, _ZSTD_STATE, _ZstdInBuffer, _ZstdOutBuffer, _zstd_platform, _zstd_exists, _zstd_cdll, _zstd_find_library, _zstd_attempts, _zstd_configure, _zstd_load, _zstd_error, _zstd_check_magic, _zstd_decompress
ZSTD_SONAMES_LINUX = ("libzstd.so.1",)


ZSTD_SONAMES_MACOS = ("libzstd.1.dylib",)


ZSTD_DIRS_LINUX = ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")


ZSTD_DIRS_MACOS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")


ZSTD_FILES_LINUX = ("libzstd.so.1", "libzstd.so")


ZSTD_FILES_MACOS = ("libzstd.1.dylib", "libzstd.dylib")


ZSTD_WINDOW_LOG_MAX = 23


ZSTD_MAX_CHUNK_BYTES = 1024 * 1024


_ZSTD_D_WINDOW_LOG_MAX = 100


_ZSTD_FRAME_MAGIC = b"\x28\xb5\x2f\xfd"


_ZSTD_SKIPPABLE_TAIL = b"\x2a\x4d\x18"


_ZSTD_SYMBOLS = ("ZSTD_createDStream", "ZSTD_initDStream", "ZSTD_DCtx_setParameter", "ZSTD_decompressStream", "ZSTD_isError", "ZSTD_getErrorName", "ZSTD_freeDStream", "ZSTD_DStreamOutSize")


_ZSTD_STATE = {}


class _ZstdInBuffer(ctypes.Structure):
    """ZSTD_inBuffer: {const void* src; size_t size; size_t pos}."""
    _fields_ = [("src", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


class _ZstdOutBuffer(ctypes.Structure):
    """ZSTD_outBuffer: {void* dst; size_t size; size_t pos}."""
    _fields_ = [("dst", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


def _zstd_platform():
    """The platform the load order is chosen for (stub hook)."""
    return sys.platform


def _zstd_exists(path):
    """Whether an absolute library path is a regular file (stub hook)."""
    return os.path.isfile(path)


def _zstd_cdll(name):
    """Open one shared library (stub hook).

    On darwin a name that is not absolute is refused before dlopen: dyld would
    resolve it against the process cwd, which a cloned repository controls (F36).
    """
    if _zstd_platform() == "darwin" and not os.path.isabs(name):
        raise OSError("refused: %r is not an absolute path (dyld searches the cwd for it)" % name)
    return ctypes.CDLL(name)


def _zstd_find_library(stem):
    """ctypes.util.find_library (stub hook) -- reached on darwin ONLY."""
    return ctypes.util.find_library(stem)


def _zstd_attempts():
    """The ordered load attempts for this platform, as (kind, arg) pairs.

    Pure: it reads only `_zstd_platform()` and the ZSTD_ tuples. `kind` is
    "soname", "find_library" or "path". Linux carries no "find_library"
    entry at all.
    """
    platform = _zstd_platform()
    attempts = []
    if platform.startswith("linux"):
        for soname in ZSTD_SONAMES_LINUX:
            attempts.append(("soname", soname))
        for directory in ZSTD_DIRS_LINUX:
            for filename in ZSTD_FILES_LINUX:
                attempts.append(("path", directory + "/" + filename))
    elif platform == "darwin":
        attempts.append(("find_library", "zstd"))
        for directory in ZSTD_DIRS_MACOS:
            for filename in ZSTD_FILES_MACOS:
                attempts.append(("path", directory + "/" + filename))
    else:
        for soname in ZSTD_SONAMES_LINUX + ZSTD_SONAMES_MACOS:
            attempts.append(("soname", soname))
    return attempts


def _zstd_configure(lib):
    """Set argtypes/restype of every symbol on a LOCAL, unpublished handle.

    Raises LookupError naming the first missing symbol. Pointers are c_void_p
    and sizes c_size_t: ctypes' default int restype truncates a 64-bit pointer.
    """
    for symbol in _ZSTD_SYMBOLS:
        if not hasattr(lib, symbol):
            raise LookupError("libzstd: missing symbol %s" % symbol)
    lib.ZSTD_createDStream.argtypes = []
    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_DCtx_setParameter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.ZSTD_DCtx_setParameter.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZstdOutBuffer), ctypes.POINTER(_ZstdInBuffer)]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
    lib.ZSTD_getErrorName.restype = ctypes.c_char_p
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_freeDStream.restype = ctypes.c_size_t
    lib.ZSTD_DStreamOutSize.argtypes = []
    lib.ZSTD_DStreamOutSize.restype = ctypes.c_size_t
    return lib


def _zstd_load():
    """Return the published (lib, path), loading it on first use.

    Raises LookupError("libzstd not found; tried: ...") when no attempt
    yields a usable library; the failure is cached the same way a success is.
    """
    result = _ZSTD_STATE.get("result")
    if result is None:
        tried = []
        for kind, arg in _zstd_attempts():
            name = arg
            if kind == "find_library":
                name = _zstd_find_library(arg)
                if not name or not os.path.isabs(name):
                    tried.append("find_library(%s)=%s" % (arg, name))
                    continue
            elif kind == "path":
                if not os.path.isabs(arg) or not _zstd_exists(arg):
                    tried.append(arg + " (absent)")
                    continue
            try:
                lib = _zstd_configure(_zstd_cdll(name))
            except (OSError, LookupError, AttributeError) as exc:
                tried.append("%s (%s)" % (name, str(exc).splitlines()[0] if str(exc) else type(exc).__name__))
                continue
            result = (lib, name)
            break
        if result is None:
            result = ("error", "libzstd not found; tried: " + ", ".join(tried))
        _ZSTD_STATE["result"] = result
    if isinstance(result[0], str) and result[0] == "error":
        raise LookupError(result[1])
    return result


def _zstd_error(lib, code):
    """The one-line ValueError for a zstd error code."""
    reason = lib.ZSTD_getErrorName(code)
    return ValueError("zstd: %s" % (reason.decode("ascii", "replace") if reason else "decoder error"))


def _zstd_check_magic(source, offset):
    """Raise unless a zstd1 or skippable frame magic starts at *offset* (F13).

    A legacy v0.x frame is decoded by a library path that never consults
    ZSTD_d_windowLogMax, so it is refused before the library sees it; so is
    anything shorter than a magic.
    """
    head = source[offset:offset + 4]
    if head == _ZSTD_FRAME_MAGIC:
        return
    if len(head) == 4 and head[1:] == _ZSTD_SKIPPABLE_TAIL and 0x50 <= head[0] <= 0x5F:
        return
    raise ValueError("zstd: legacy or unknown frame refused at byte %d" % offset)


def _zstd_decompress(data, max_output):
    """Decode one or more concatenated zstd frames; the decoder contract of the Chrome client.

    Returns the decoded bytes. Raises OverflowError when the output would
    exceed `max_output`, ValueError when a frame is corrupt, truncated,
    demands a window above `1 << ZSTD_WINDOW_LOG_MAX`, is followed by bytes
    that are not a frame, or the library breaks the call's invariants, and
    LookupError when libzstd is absent.
    """
    lib = _zstd_load()[0]
    # `source`, `source_p` and `buf` own the memory the library reads and writes
    # through raw addresses (inb.src, out.dst); these locals must stay bound until
    # the loop below is done -- never drop or rebind them inside it.
    source = bytes(data)
    length = len(source)
    source_p = ctypes.c_char_p(source)
    src = ctypes.cast(source_p, ctypes.c_void_p).value
    size = lib.ZSTD_DStreamOutSize()
    if size < 1 or size > ZSTD_MAX_CHUNK_BYTES:
        raise ValueError("zstd: decoder state inconsistent")
    buf = ctypes.create_string_buffer(size)
    base = ctypes.addressof(buf)
    inb = _ZstdInBuffer(src, length, 0)
    out = _ZstdOutBuffer(base, size, 0)
    output = bytearray()
    total = 0
    last_in = 0
    frame_start = True
    stream = lib.ZSTD_createDStream()
    if not stream:
        raise ValueError("zstd: decoder stream could not be created")
    try:
        code = lib.ZSTD_initDStream(stream)
        if lib.ZSTD_isError(code):
            raise _zstd_error(lib, code)
        code = lib.ZSTD_DCtx_setParameter(stream, _ZSTD_D_WINDOW_LOG_MAX, ZSTD_WINDOW_LOG_MAX)
        if lib.ZSTD_isError(code):
            raise _zstd_error(lib, code)
        while True:
            if frame_start:
                # F13: a frame begins at inb.pos -- byte 0, or right after a 0
                # return (frame fully decoded and flushed; the library stops
                # there). A boundary inside one call is never observed, but the
                # library returns at every frame end, so none is skipped.
                _zstd_check_magic(source, inb.pos)
                frame_start = False
            out.dst = base
            out.size = size
            out.pos = 0
            code = lib.ZSTD_decompressStream(stream, ctypes.byref(out), ctypes.byref(inb))
            if lib.ZSTD_isError(code):
                raise _zstd_error(lib, code)
            produced = out.pos
            if produced < 0 or produced > size:
                raise ValueError("zstd: decoder reported %d bytes into a %d-byte buffer" % (produced, size))
            if inb.pos < last_in or inb.pos > length:
                raise ValueError("zstd: decoder state inconsistent")
            progressed = produced > 0 or inb.pos > last_in
            last_in = inb.pos
            total += produced
            if total > max_output:
                raise OverflowError("zstd: output exceeds %d bytes" % max_output)
            if produced:
                output += ctypes.string_at(base, produced)
            if code == 0 and inb.pos == length:
                break
            if not progressed:
                if inb.pos == length:
                    raise ValueError("zstd: truncated stream")
                raise ValueError("zstd: decoder state inconsistent")
            frame_start = code == 0
    finally:
        lib.ZSTD_freeDStream(stream)
    return bytes(output)
# END GENERATED: 0505c5a3de8a

# The HTTP client, generated from Scripts/_mcp_chrome.py. This server uses only
# its Chrome path (transport="chrome": the captured Chrome ClientHello and
# header profiles, certificate NOT verified). Taken whole, like the regions
# above.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
# BEGIN GENERATED: _mcp_chrome.py :: ChromeClientError, ChromeTls12Error, ChromeBodyTooLarge, _chrome_profile, CHROME_PROFILE, CH_MAX_RECORD_BYTES, CH_MAX_HANDSHAKE_BYTES, CH_MAX_FRAME_BYTES, CH_MAX_HEADER_LIST_BYTES, CH_MAX_HEADER_BLOCK_BYTES, CH_HPACK_TABLE_BYTES, CH_MAX_HPACK_INT, CH_MAX_HPACK_INT_CONTINUATIONS, CH_MAX_H2_CONTROL_FRAMES, CH_MAX_H2_EMPTY_FRAMES, CH_MAX_H2_CONTINUATION_FRAMES, CH_MAX_H1_HEAD_BYTES, CH_MAX_H1_HEADERS, CH_MAX_H1_CHUNK_LINE_BYTES, CH_MAX_BODY_BYTES, CH_DEFAULT_DECODE_CAP, CH_MAX_CONTENT_CODINGS, CH_MAX_REDIRECTS, CH_MAX_COOKIES_PER_DOMAIN, CH_MAX_COOKIES, CH_MAX_COOKIE_BYTES, _CH_X25519_P, _CH_X25519_A24, _ch_x25519_cswap, _ch_x25519, _ch_x25519_keypair, _CH_P256_P, _CH_P256_B, _CH_P256_N, _CH_P256_GX, _CH_P256_GY, _ch_p256_double, _ch_p256_add, _ch_p256_mul, _ch_p256_keypair, _ch_p256_shared, _ChMlKem768, _ch_aes_tables, _CH_AES_TABLES, _ChAesGcm, _ChChaCha20Poly1305, _ch_hkdf_extract, _ch_hkdf_expand, _ch_hkdf_expand_label, _ch_derive_secret, CH_MAX_PLAINTEXT_BYTES, _ChReader, _ChRecordReader, _ChHandshakeReader, _ChRecordCipher, _CH_KEY_SHARE_BYTES, _ch_vec, _ch_draw, _ch_idna_encode, _ch_sni_name, _ch_grease, _ch_permutation, _ch_key_share_entry, _ch_hello_extensions, _ch_hello_wire, _ch_client_hello, _CH_ALERT_NAMES, _ch_alert_name, _CH_HRR_RANDOM, _CH_TLS13_SUITES, _CH_SERVER_SHARE_BYTES, _CH_EE_FORBIDDEN, _ch_parse_server_hello, _ch_key_share_new, _ch_key_share_secret, _ch_traffic_cipher, CH_MAX_KEY_UPDATES, _ChTls, _ChTlsStream, _ch_huffman_table, _CH_HUFFMAN, _ch_huffman_decode_table, _CH_HUFFMAN_DECODE, _ch_hpack_static, _CH_HPACK_STATIC, _ch_hpack_int, _ch_huffman_encode, _ch_huffman_size, _ch_hpack_string, _ch_cookie_crumbs, _ChHpackEncoder, _ch_hpack_decode_int, _ch_huffman_decode, _ch_hpack_decode_string, _ChHpackDecoder, _CH_BAD_PORTS, _ch_split_url, _CH_TCHAR_SYMBOLS, _CH_FINGERPRINT_NAMES, _CH_FINGERPRINT_PREFIXES, _CH_FRAMING_NAMES, _CH_FRAMING_PREFIXES, _ch_header_pairs, _ch_check_caller_headers, _ch_origin_of, _ch_origin_text, _CH_PUBLIC_SUFFIXES, _ch_is_ip_host, _ch_is_public_suffix, _ch_site_of, _ch_sec_fetch_site, _ch_referer_for, _ch_profile_headers, _CH_H2_ERROR_NAMES, _ch_h2_frame, _ChH2Connection, _ChH1Connection, _ChHeaders, _ch_leading_digits, _ch_cookie_date, _ChCookieJar, _ChResponse, _ch_zlib_decode, _ch_decode_body, _CH_TRANSLATION_PREFIXES, _ch_embedded_ipv4, _ch_address_refused, _ch_public_only_policy, _ch_open_socket, _CH_REDIRECT_CODES, _CH_SITE_RANK, _ch_transport_error, _ch_unvetted_policy, _ChDeadlineSocket, _ChFallbackConnection, _ChSession, _ch_session_new
class ChromeClientError(ConnectionError):
    """A refused or failed exchange on the Chrome client path.

    A `ConnectionError` on purpose: to every caller that already treats a dead
    link as an `OSError`, a server that broke the protocol, crossed a ceiling or
    was refused by the connect policy is the same event. The message is ONE line,
    `<phase>: <what>`, and never carries a header value, a cookie or a body.
    """


class ChromeTls12Error(ChromeClientError):
    """The server chose TLS 1.2; the session may fall back.

    Raised from the ServerHello's `supported_versions`, before any key is
    derived. It is a subclass so a caller that does not opt into the fallback
    sees an ordinary `ChromeClientError`; a session built with
    `tls12_fallback=True` catches exactly this type and retries through the
    verified stdlib path.
    """


class ChromeBodyTooLarge(ChromeClientError):
    """A response body crossed its byte limit; nothing was truncated silently.

    `.limit` is the int limit that bit: the caller's `max_bytes`, or, when
    `max_bytes` is 0, `CH_MAX_BODY_BYTES` for the wire body and
    `CH_DEFAULT_DECODE_CAP` for the decoded output. A distinct type, so a host
    can report "too large" without parsing the message.
    """

    def __init__(self, limit):
        self.limit = limit
        super().__init__("body: exceeds %d bytes" % limit)


def _chrome_profile():
    """Every Chrome-version-bearing value, one key per line, each citing its capture.

    Source of record: tests/files/chrome/154/ (Chrome 154.0.8037.58, macOS
    14.2.1, captured 2026-10-02 against the loopback server of
    Scripts/chrome_capture.py; its README.md holds the profile table). A
    citation `<dir>/` names that fixture subdirectory. R-0017
    (docs/concepts/spec-ddg.md section 2.9) is the historical origin where most
    values were first seen; the fixtures re-measured every one of them. A key
    or slot marked UNVERIFIED is not in any fixture and says why.
    """
    p = {}
    # navigate/ (every set): Chrome/154.0.0.0 in user-agent and v="154" in sec-ch-ua.
    p["major"] = 154
    # Capture date of tests/files/chrome/154/ (meta.captured_on, UTC); the profile-age INFO row counts from it.
    p["pinned_on"] = "2026-10-02"
    # navigate/, HEADERS user-agent (macOS Chrome freezes the OS token at 10_15_7).
    p["user_agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
    # navigate/, HEADERS sec-ch-ua (the GREASE brand and the brand order are part of the version's fingerprint).
    p["sec_ch_ua"] = "\"Chromium\";v=\"154\", \"Google Chrome\";v=\"154\", \"Not A(Brand\";v=\"99\""
    # navigate/, HEADERS sec-ch-ua-mobile.
    p["sec_ch_ua_mobile"] = "?0"
    # navigate/, HEADERS sec-ch-ua-platform.
    p["sec_ch_ua_platform"] = "\"macOS\""
    # navigate/, HEADERS accept-language.
    p["accept_language"] = "en-GB,en-US;q=0.9,en;q=0.8"
    # navigate/, HEADERS accept-encoding; h1-plain/ sends the same value over cleartext http://.
    p["accept_encoding"] = "gzip, deflate, br, zstd"
    # navigate/, HEADERS accept.
    p["nav_accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    # navigate/, HEADERS priority (RFC 9218 header on a navigation).
    p["nav_priority_header"] = "u=0, i"
    # navigate/, ClientHello cipher_suites without GREASE (JA3 4865-...-53).
    p["ciphers"] = (0x1301, 0x1302, 0x1303, 0xC02B, 0xC02F, 0xC02C, 0xC030, 0xCCA9, 0xCCA8, 0xC013, 0xC014, 0x009C, 0x009D, 0x002F, 0x0035)
    # navigate/, ClientHello signature_algorithms without the leading GREASE.
    p["sigalgs"] = (0x0904, 0x0905, 0x0906, 0x0403, 0x0804, 0x0401, 0x0503, 0x0805, 0x0501, 0x0806, 0x0601)
    # navigate/, ClientHello supported_groups without the leading GREASE.
    p["groups"] = (0x11EC, 0x001D, 0x0017, 0x0018)
    # navigate/, ClientHello key_share after the 1-byte GREASE share: X25519MLKEM768 (1216) then X25519 (32).
    p["key_share_groups"] = (0x11EC, 0x001D)
    # navigate/, ClientHello supported_versions without the leading GREASE.
    p["versions"] = (0x0304, 0x0303)
    # navigate/, ClientHello ALPN; h1-tls/ offers the same list and the server picked http/1.1.
    p["alpn"] = ("h2", "http/1.1")
    # navigate/, ClientHello application_settings (17613) payload 0003026832 = ["h2"].
    p["alps_protocols"] = ("h2",)
    # navigate/, ClientHello compress_certificate: brotli (2).
    p["cert_compression"] = (2,)
    # Every fixture set, the 17 non-GREASE extension types (16 on ip-literal/: no server_name); BoringSSL
    # permutes their order per connection (20 distinct orders of 20 in every set).
    p["extension_types"] = (0, 5, 10, 11, 13, 16, 18, 23, 27, 35, 43, 45, 51, 17613, 51764, 65037, 65281)
    # navigate/, ECH GREASE outer (65037): HKDF-SHA256 (0x0001) + AES-128-GCM (0x0001).
    p["ech_cipher_suite"] = (0x0001, 0x0001)
    # ECH GREASE payload lengths, one drawn per connection; every fixture set draws from exactly these four.
    p["ech_payload_buckets"] = (144, 176, 208, 240)
    # Extension 51764 payload, byte-identical on all 180 TLS connections of tests/files/chrome/154/ and on
    # every hrr/ CH2 (same 186-byte length as 153, different payload).
    p["tai_payload_hex"] = "00b80582df1302010582df1302060582df13020d0582df13020e0582df13020f0582df1302120582df1302130582df13021408839a648c9b2d010708839a648c9b2d010808839a648c9b2d010908839a648c9b2d010a08839a648c9b2d010b08839a648c9b2d010c08839a648c9b2d010d08839a648c9b2d011208839a648c9b2d011304d679090104d679090404d679090504d679090604d679090704d679090804d679090a04d679090b04d679090c04d679090d04d679090f"
    # The client's raw ALPS value for h2 (the key keeps its historical name; it holds no SETTINGS): the WHOLE
    # extension 17613 body of the client EncryptedExtensions, copied verbatim -- EMPTY (zero length) for h2.
    # VERIFIED from source, not from a capture (the client EE is encrypted): Chromium main
    # 43502f9b0389c0b1e81b6e379180086d637bf7be, net/http/http_network_session.cc sets
    # `application_settings_[NextProto::kProtoHTTP2] = {};` ("Enable ALPS for HTTP/2 with empty data");
    # BoringSSL main 98df68178dcf8a75b230503951b8a3b4f5c51aa6, ssl/tls13_client.cc
    # do_send_client_encrypted_extensions copies local_application_settings verbatim. Only the SERVER's ALPS
    # data is h2 frames (net/spdy/alps_decoder.cc); that side is read, never sent, by this module.
    p["alps_h2_settings_hex"] = ""
    # Every h2 fixture set, SETTINGS in wire order (Akamai 1:65536;2:0;4:6291456;6:262144).
    p["h2_settings"] = ((1, 65536), (2, 0), (4, 6291456), (6, 262144))
    # Every h2 fixture set, the connection WINDOW_UPDATE sent right after SETTINGS (Akamai |15663105|).
    p["h2_window_increment"] = 15663105
    # navigate/ stream 1, HEADERS priority field: (exclusive, dependency, weight); weight 256 is wire byte 255
    # (the weight is the RFC 9113 value, wire byte + 1). HEADERS flags 0x25: END_STREAM|END_HEADERS|PRIORITY.
    p["nav_priority"] = (True, 0, 256)
    # Every name an h2 navigation can carry, in Chrome's order; a name the request does not carry is skipped.
    # navigate/ (1a, typed, 20/20) is this list without cache-control and cookie. cache-control: max-age=0 is
    # emitted ONLY on a reload: navigate-reload/ (1b) has it right after :path on 20/20, and it is the one
    # difference between 1a and 1b (ip-literal/ 1 of 20, a reload). cookie: cookie/ (19/20, once the server
    # has set it) puts it after accept-language, before priority. referer: UNVERIFIED on navigate (no navigation
    # fixture carries one); placed as on cors (cors-get/, cors-post/), after sec-fetch-dest.
    p["navigate_order"] = (":method", ":authority", ":scheme", ":path", "cache-control", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "upgrade-insecure-requests", "user-agent", "accept", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-user", "sec-fetch-dest", "referer", "accept-encoding", "accept-language", "cookie", "priority")
    # A same-origin fetch()'s h2 order with a CALLER-SET Accept (stream 5; a name not carried is skipped).
    # cors-post/ (20/20) is exactly this list without origin and cookie; cors-get/ (20/20) is it without
    # content-length, content-type, origin and cookie. content-length/content-type only with a body.
    # origin: NOT sent on a same-origin fetch -- no h2 stream of any tests/files/chrome/154/ set carries one
    # (Chrome 153's 22 POST, 22 GET and 22 HEAD raw connections, re-decoded from their HEADERS blocks, carried
    # none either). The slot is kept for a CROSS-origin fetch only, UNVERIFIED (no cross-origin capture).
    # cookie: UNVERIFIED on cors (no cors fixture carries one); placed as on navigate (cookie/).
    p["cors_order"] = (":method", ":authority", ":scheme", ":path", "content-length", "sec-ch-ua-platform", "user-agent", "accept", "sec-ch-ua", "content-type", "sec-ch-ua-mobile", "origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "referer", "accept-encoding", "accept-language", "cookie", "priority")
    # The same fetch when the CALLER SET NO Accept: Chrome's default accept sits after sec-ch-ua-mobile, not
    # after user-agent (cors-head/ 20/20, which is exactly this list without the body names, origin and cookie).
    # content-length/content-type here are UNVERIFIED (no body-carrying fetch without a caller Accept was
    # captured); origin and cookie as in cors_order.
    p["cors_order_default_accept"] = (":method", ":authority", ":scheme", ":path", "content-length", "sec-ch-ua-platform", "user-agent", "sec-ch-ua", "content-type", "sec-ch-ua-mobile", "accept", "origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "referer", "accept-encoding", "accept-language", "cookie", "priority")
    # cors-head/, Chrome's default fetch() Accept (not set by the page).
    p["cors_accept"] = "*/*"
    # cors-post/ cors-get/ cors-head/ stream 5, HEADERS priority field as nav_priority: weight 220 is wire
    # byte 219. Flags 0x25 on GET/HEAD; on POST 0x24 (no END_STREAM) and the body follows as ONE DATA frame
    # with END_STREAM (cors-post/: length 10, q=test&kl=).
    p["cors_priority"] = (True, 0, 220)
    # cors-post/ cors-get/ cors-head/ stream 5, HEADERS priority (RFC 9218 header on a fetch).
    p["cors_priority_header"] = "u=1, i"
    # cors-post/, the URLSearchParams body's content-type exactly as Chrome spells it.
    p["cors_post_content_type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    # A caller header the profile does not emit goes immediately BEFORE this name. Consistent with the one
    # page-set header measured: a caller Accept lands right after user-agent (cors-post/, cors-get/). Where a
    # CUSTOM (non-Accept) caller header lands is UNVERIFIED: no fixture carries one.
    p["extra_slot"] = "accept"
    # h1-tls/ (20/20) and h1-plain/ (16/16), identical: the navigation request-head order and casing (sec-ch-*
    # lowercase, the rest canonical; no Priority over HTTP/1.1). Cache-Control and Cookie are carried only
    # when present (a reload / a jar hit) and their slots are UNVERIFIED on h1: neither set carries them;
    # they are placed as on h2 (navigate-reload/, cookie/). Referer is UNVERIFIED on an h1 NAVIGATION the same
    # way (placed as in navigate_order); the slot and casing agree with the one h1 Referer captured, the favicon
    # subresource on each h1 connection (h1-plain/ and h1-tls/ request 3: `Referer` after Sec-Fetch-Dest).
    # That request also shows h1 keeping a fetch's h2 order (sec-ch-ua-platform, User-Agent, sec-ch-ua, ...),
    # which is why _ChH1Connection keeps _ch_profile_headers' order and takes only the casing from this tuple.
    p["h1_order"] = ("Host", "Connection", "Cache-Control", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "Upgrade-Insecure-Requests", "User-Agent", "Accept", "Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-User", "Sec-Fetch-Dest", "Referer", "Accept-Encoding", "Accept-Language", "Cookie")
    # h1-tls/ (20/20) and h1-plain/ (16/16), every request on the connection: the Connection header value.
    p["h1_connection"] = "keep-alive"
    # HelloRetryRequest groups we answer: P-256 only (0x0017, the one group offered without a share). hrr/
    # (20/20): after an HRR for 0x0017 the CH2 key_share holds ONE entry, 0x0017 (65-byte key, no GREASE share).
    p["hrr_groups"] = (0x0017,)
    # hrr/ (20/20): a compat ChangeCipherSpec record (type 20, 1 byte) precedes CH2 every time.
    p["hrr_compat_ccs"] = True
    return p


CHROME_PROFILE = _chrome_profile()


CH_MAX_RECORD_BYTES = 16384 + 256


CH_MAX_HANDSHAKE_BYTES = 256 * 1024


CH_MAX_FRAME_BYTES = 16384


CH_MAX_HEADER_LIST_BYTES = dict(CHROME_PROFILE["h2_settings"])[6]


CH_MAX_HEADER_BLOCK_BYTES = CH_MAX_HEADER_LIST_BYTES


CH_HPACK_TABLE_BYTES = dict(CHROME_PROFILE["h2_settings"])[1]


CH_MAX_HPACK_INT = 2 ** 32


CH_MAX_HPACK_INT_CONTINUATIONS = 6


CH_MAX_H2_CONTROL_FRAMES = 1000


CH_MAX_H2_EMPTY_FRAMES = 100


CH_MAX_H2_CONTINUATION_FRAMES = 64


CH_MAX_H1_HEAD_BYTES = 64 * 1024


CH_MAX_H1_HEADERS = 256


CH_MAX_H1_CHUNK_LINE_BYTES = 4096


CH_MAX_BODY_BYTES = 64 * 1024 * 1024


CH_DEFAULT_DECODE_CAP = 64 * 1024 * 1024


CH_MAX_CONTENT_CODINGS = 2


CH_MAX_REDIRECTS = 20


CH_MAX_COOKIES_PER_DOMAIN = 180


CH_MAX_COOKIES = 3000


CH_MAX_COOKIE_BYTES = 4096


_CH_X25519_P = 2 ** 255 - 19


_CH_X25519_A24 = 121665


def _ch_x25519_cswap(swap, a, b):
    """Swap a and b when swap is 1, by masking rather than branching (RFC 7748 section 5).

    Python big integers are not constant-time anyway; the mask keeps the ladder
    in the RFC's shape, it does not make a timing claim.
    """
    dummy = (-swap) & ((1 << 256) - 1) & (a ^ b)
    return a ^ dummy, b ^ dummy


def _ch_x25519(k_bytes, u_bytes):
    """X25519(k, u) per RFC 7748 section 5: the 32-byte little-endian u of k*u.

    Both inputs must be 32 bytes, else ChromeClientError("tls: invalid X25519
    key share"). The scalar is clamped and the top bit of u masked, as the RFC
    says. An all-zero result (the peer sent a small-order point) is refused
    with ChromeClientError("tls: X25519 shared secret is all zero") -- RFC 7748
    section 6.1 lets a protocol check for it, and RFC 8446 section 7.4.2 says a
    TLS 1.3 client MUST. Not constant-time (declared in the module docstring).
    """
    if len(k_bytes) != 32 or len(u_bytes) != 32:
        raise ChromeClientError("tls: invalid X25519 key share")
    p = _CH_X25519_P
    k = bytearray(k_bytes)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k = int.from_bytes(bytes(k), "little")
    u = int.from_bytes(bytes(u_bytes), "little") % (2 ** 255)
    x1 = u
    x2, z2, x3, z3 = 1, 0, u, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        x2, x3 = _ch_x25519_cswap(swap, x2, x3)
        z2, z3 = _ch_x25519_cswap(swap, z2, z3)
        swap = kt
        a = (x2 + z2) % p
        aa = a * a % p
        b = (x2 - z2) % p
        bb = b * b % p
        e = (aa - bb) % p
        c = (x3 + z3) % p
        d = (x3 - z3) % p
        da = d * a % p
        cb = c * b % p
        x3 = (da + cb) % p
        x3 = x3 * x3 % p
        z3 = (da - cb) % p
        z3 = x1 * (z3 * z3 % p) % p
        x2 = aa * bb % p
        z2 = e * ((aa + _CH_X25519_A24 * e) % p) % p
    x2, x3 = _ch_x25519_cswap(swap, x2, x3)
    z2, z3 = _ch_x25519_cswap(swap, z2, z3)
    res = x2 * pow(z2, p - 2, p) % p
    if res == 0:
        raise ChromeClientError("tls: X25519 shared secret is all zero")
    return res.to_bytes(32, "little")


def _ch_x25519_keypair(rand=None):
    """A fresh X25519 key pair: (32-byte private scalar, 32-byte public u).

    `rand(n) -> bytes` defaults to os.urandom; tests inject a deterministic
    source. The public key is X25519(priv, 9), the RFC 7748 base point.
    """
    if rand is None:
        rand = os.urandom
    priv = bytes(rand(32))
    pub = _ch_x25519(priv, (9).to_bytes(32, "little"))
    return priv, pub


_CH_P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF


_CH_P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B


_CH_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


_CH_P256_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296


_CH_P256_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5


def _ch_p256_double(pt):
    """2*pt in Jacobian coordinates (X, Y, Z); Z == 0 is the point at infinity.

    The a = -3 doubling (EFD dbl-2001-b): delta = Z^2, gamma = Y^2,
    beta = X*gamma, alpha = 3*(X - delta)*(X + delta).
    """
    p = _CH_P256_P
    x, y, z = pt
    if z == 0 or y == 0:
        return (1, 1, 0)
    delta = z * z % p
    gamma = y * y % p
    beta = x * gamma % p
    alpha = 3 * (x - delta) * (x + delta) % p
    x3 = (alpha * alpha - 8 * beta) % p
    z3 = ((y + z) * (y + z) - gamma - delta) % p
    y3 = (alpha * (4 * beta - x3) - 8 * gamma * gamma) % p
    return (x3, y3, z3)


def _ch_p256_add(p1, p2):
    """p1 + p2 in Jacobian coordinates (EFD add-2007-bl shape, complete by cases).

    Handles the cases the formula does not: either input at infinity, p1 == p2
    (falls through to doubling) and p1 == -p2 (infinity).
    """
    p = _CH_P256_P
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    if z1 == 0:
        return p2
    if z2 == 0:
        return p1
    z1z1 = z1 * z1 % p
    z2z2 = z2 * z2 % p
    u1 = x1 * z2z2 % p
    u2 = x2 * z1z1 % p
    s1 = y1 * z2 * z2z2 % p
    s2 = y2 * z1 * z1z1 % p
    h = (u2 - u1) % p
    r = (s2 - s1) % p
    if h == 0:
        if r == 0:
            return _ch_p256_double(p1)
        return (1, 1, 0)
    hh = h * h % p
    hhh = h * hh % p
    v = u1 * hh % p
    x3 = (r * r - hhh - 2 * v) % p
    y3 = (r * (v - x3) - s1 * hhh) % p
    z3 = z1 * z2 * h % p
    return (x3, y3, z3)


def _ch_p256_mul(k, x, y):
    """k * (x, y) -> affine (x, y), or None for the point at infinity.

    A fixed 4-bit window over a 16-entry table (0*P .. 15*P) in Jacobian
    coordinates, one inversion at the end. NOT constant-time: the table lookup
    and the infinity branches depend on the scalar (declared in the module
    docstring; the keys are ephemeral per connection).
    """
    p = _CH_P256_P
    base = (x, y, 1)
    table = [(1, 1, 0), base]
    for i in range(2, 16):
        table.append(_ch_p256_add(table[i - 1], base))
    acc = (1, 1, 0)
    for shift in range(252, -4, -4):
        acc = _ch_p256_double(_ch_p256_double(_ch_p256_double(_ch_p256_double(acc))))
        acc = _ch_p256_add(acc, table[(k >> shift) & 0xF])
    ax, ay, az = acc
    if az == 0:
        return None
    zinv = pow(az, p - 2, p)
    zinv2 = zinv * zinv % p
    return (ax * zinv2 % p, ay * zinv2 * zinv % p)


def _ch_p256_keypair(rand=None):
    """A fresh P-256 ECDH key pair: (private scalar int, 65-byte uncompressed public point).

    `rand(n) -> bytes` defaults to os.urandom. The scalar is drawn by rejection
    sampling into [1, n - 1] (FIPS 186-5 A.2.2 shape); a source that fails 64
    draws in a row -- probability about 2^-2048 for a real RNG -- is refused
    rather than looped on forever.
    """
    if rand is None:
        rand = os.urandom
    n = _CH_P256_N
    for _ in range(64):
        d = int.from_bytes(bytes(rand(32)), "big")
        if 1 <= d < n:
            pt = _ch_p256_mul(d, _CH_P256_GX, _CH_P256_GY)
            return d, b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")
    raise ChromeClientError("tls: P-256 key generation failed")


def _ch_p256_shared(priv_int, peer_pub_bytes):
    """The P-256 ECDH shared secret: the 32-byte big-endian x of priv * peer.

    The peer point is validated before it is used (SP 800-56A 5.6.2.3.4 partial
    public-key validation; cofactor 1 makes it full): uncompressed 0x04 form of
    exactly 65 bytes, both coordinates < p, on y^2 = x^3 - 3x + b. The point at
    infinity has no uncompressed encoding, and a product at infinity is refused
    too. Any failure is ChromeClientError("tls: invalid P-256 key share"); a
    private scalar outside [1, n - 1] is ChromeClientError("tls: invalid P-256
    private key"). Not constant-time (declared in the module docstring).
    """
    p = _CH_P256_P
    if not 1 <= priv_int < _CH_P256_N:
        raise ChromeClientError("tls: invalid P-256 private key")
    data = bytes(peer_pub_bytes)
    if len(data) != 65 or data[0] != 4:
        raise ChromeClientError("tls: invalid P-256 key share")
    x = int.from_bytes(data[1:33], "big")
    y = int.from_bytes(data[33:65], "big")
    if x >= p or y >= p:
        raise ChromeClientError("tls: invalid P-256 key share")
    if (y * y - (x * x * x - 3 * x + _CH_P256_B)) % p != 0:
        raise ChromeClientError("tls: invalid P-256 key share")
    pt = _ch_p256_mul(priv_int, x, y)
    if pt is None:
        raise ChromeClientError("tls: invalid P-256 key share")
    return pt[0].to_bytes(32, "big")


class _ChMlKem768:
    """ML-KEM-768 (FIPS 203): the post-quantum half of the X25519MLKEM768 share.

    The client needs `keygen` and `decaps`: its key_share carries the 1184-byte
    encapsulation key followed by the 32-byte X25519 public key, the server
    answers with the 1088-byte ciphertext followed by its 32-byte X25519 key,
    and the hybrid shared secret is the 32-byte ML-KEM secret followed by the
    32-byte X25519 one (draft-ietf-tls-ecdhe-mlkem, the order Chrome and the
    loopback PoC use). `encaps` exists for the scripted test peer and for the
    cross-check against OpenSSL, which proves it in both directions.

    Matrix order is FIPS 203's and nothing else: KeyGen samples
    A_hat[i][j] = SampleNTT(rho || j || i) (Algorithm 13) and K-PKE.Encrypt
    uses its transpose, A_hat[i][j] = SampleNTT(rho || i || j) (Algorithm 14).
    The R-0017 PoC carried this as a flag (MLKEM_TRANSPOSE_KEYGEN = False); the
    OpenSSL 3.6 cross-check passed with that value in both directions
    (docs/concepts/spec-ddg.md section 2.9), and the flag is gone.

    Refusals are ChromeClientError("tls: ..."): an encapsulation key, a
    decapsulation key or a ciphertext of the wrong length, an encapsulation key
    that fails the FIPS 203 section 7.2 modulus check, a decapsulation key that
    fails the section 7.3 hash check, and a seed or random draw of the wrong
    length. A well-formed ciphertext that does not re-encrypt is NOT refused:
    decaps returns the implicit-rejection secret J(z || c), as the standard
    requires, and the handshake then fails at Finished.

    The zeta and gamma tables are built in __init__ (256 modular powers, well
    under a millisecond) rather than as module-level comprehensions, so the
    class is one generator block with nothing to co-list. Pure Python and NOT
    constant-time (declared in the module docstring).
    """

    Q = 3329
    K = 3
    ETA1 = 2
    ETA2 = 2
    DU = 10
    DV = 4
    EK_BYTES = 1184
    DK_BYTES = 2400
    CT_BYTES = 1088

    def __init__(self):
        q = self.Q
        rev = [int(format(i, "07b")[::-1], 2) for i in range(128)]
        self.zetas = [pow(17, r, q) for r in rev]
        self.gammas = [pow(17, 2 * r + 1, q) for r in rev]

    def _ntt(self, f):
        """NTT (FIPS 203 Algorithm 9) of a 256-coefficient list; returns a new list."""
        q = self.Q
        zetas = self.zetas
        f = list(f)
        i = 1
        length = 128
        while length >= 2:
            start = 0
            while start < 256:
                z = zetas[i]
                i += 1
                for j in range(start, start + length):
                    t = z * f[j + length] % q
                    f[j + length] = (f[j] - t) % q
                    f[j] = (f[j] + t) % q
                start += 2 * length
            length //= 2
        return f

    def _intt(self, f):
        """Inverse NTT (FIPS 203 Algorithm 10); 3303 is 128^-1 mod q."""
        q = self.Q
        zetas = self.zetas
        f = list(f)
        i = 127
        length = 2
        while length <= 128:
            start = 0
            while start < 256:
                z = zetas[i]
                i -= 1
                for j in range(start, start + length):
                    t = f[j]
                    f[j] = (t + f[j + length]) % q
                    f[j + length] = z * (f[j + length] - t) % q
                start += 2 * length
            length *= 2
        return [x * 3303 % q for x in f]

    def _mul_acc(self, acc, f, g):
        """acc += f * g in the NTT domain (FIPS 203 Algorithms 11 and 12), unreduced."""
        q = self.Q
        gammas = self.gammas
        for i in range(128):
            a0 = f[2 * i]
            a1 = f[2 * i + 1]
            b0 = g[2 * i]
            b1 = g[2 * i + 1]
            acc[2 * i] += a0 * b0 + a1 * b1 % q * gammas[i]
            acc[2 * i + 1] += a0 * b1 + a1 * b0

    def _sample_ntt(self, seed):
        """SampleNTT (FIPS 203 Algorithm 7): rejection-sample 256 coefficients from SHAKE128.

        A SHAKE digest of n bytes is a prefix of the digest of 2n, so doubling
        the squeeze when the buffer runs dry reads the same stream the standard
        reads. 1008 bytes are 672 candidates, of which about 315 are needed on
        average (a candidate is kept with probability 3329/4096), so the first
        squeeze suffices for all but a vanishing fraction of seeds.
        """
        q = self.Q
        xof = hashlib.shake_128(seed)
        n = 1008
        buf = xof.digest(n)
        pos = 0
        a = []
        while len(a) < 256:
            if pos + 3 > n:
                n *= 2
                buf = xof.digest(n)
            d1 = buf[pos] | ((buf[pos + 1] & 0x0F) << 8)
            d2 = (buf[pos + 1] >> 4) | (buf[pos + 2] << 4)
            pos += 3
            if d1 < q:
                a.append(d1)
            if d2 < q and len(a) < 256:
                a.append(d2)
        return a

    def _cbd(self, data, eta):
        """SamplePolyCBD_eta (FIPS 203 Algorithm 8) over 64 * eta bytes.

        Bits are counted with bin().count because int.bit_count is Python 3.10+.
        """
        q = self.Q
        bits = int.from_bytes(data, "little")
        lo = (1 << eta) - 1
        width = 2 * eta
        f = []
        for i in range(256):
            t = bits >> (width * i)
            x = bin(t & lo).count("1")
            y = bin((t >> eta) & lo).count("1")
            f.append((x - y) % q)
        return f

    def _prf(self, eta, s, b):
        """PRF_eta(s, b) = SHAKE256(s || b, 64 * eta) (FIPS 203 section 4.1)."""
        return hashlib.shake_256(s + bytes([b])).digest(64 * eta)

    def _encode(self, f, d):
        """ByteEncode_d (FIPS 203 Algorithm 5): 256 d-bit values, little-endian bit order."""
        mask = (1 << d) - 1
        acc = 0
        for i in range(255, -1, -1):
            acc = (acc << d) | (f[i] & mask)
        return acc.to_bytes(32 * d, "little")

    def _decode(self, data, d):
        """ByteDecode_d (FIPS 203 Algorithm 6); d == 12 reduces modulo q as the standard says."""
        mask = (1 << d) - 1
        v = int.from_bytes(data, "little")
        f = [(v >> (d * i)) & mask for i in range(256)]
        if d == 12:
            q = self.Q
            f = [x % q for x in f]
        return f

    def _compress(self, f, d):
        """Compress_d (FIPS 203 section 4.2.1): round(2^d / q * x) mod 2^d; 1664 is q // 2."""
        q = self.Q
        mask = (1 << d) - 1
        return [(((x << d) + 1664) // q) & mask for x in f]

    def _decompress(self, f, d):
        """Decompress_d (FIPS 203 section 4.2.1): round(q / 2^d * y)."""
        q = self.Q
        half = 1 << (d - 1)
        return [(q * y + half) >> d for y in f]

    def _matrix(self, rho, transposed):
        """A_hat (transposed=False, KeyGen) or its transpose (True, Encrypt); FIPS 203 order."""
        k = self.K
        rows = []
        for i in range(k):
            row = []
            for j in range(k):
                if transposed:
                    row.append(self._sample_ntt(rho + bytes([i, j])))
                else:
                    row.append(self._sample_ntt(rho + bytes([j, i])))
            rows.append(row)
        return rows

    def _pke_encrypt(self, ek, m, r):
        """K-PKE.Encrypt (FIPS 203 Algorithm 14): the 1088-byte ciphertext of m under ek."""
        q = self.Q
        k = self.K
        t_hat = [self._decode(ek[384 * i:384 * (i + 1)], 12) for i in range(k)]
        a_t = self._matrix(ek[384 * k:384 * k + 32], True)
        n = 0
        y_hat = []
        for _ in range(k):
            y_hat.append(self._ntt(self._cbd(self._prf(self.ETA1, r, n), self.ETA1)))
            n += 1
        e1 = []
        for _ in range(k):
            e1.append(self._cbd(self._prf(self.ETA2, r, n), self.ETA2))
            n += 1
        e2 = self._cbd(self._prf(self.ETA2, r, n), self.ETA2)
        c1 = b""
        for i in range(k):
            acc = [0] * 256
            for j in range(k):
                self._mul_acc(acc, a_t[i][j], y_hat[j])
            u = self._intt([x % q for x in acc])
            u = [(u[x] + e1[i][x]) % q for x in range(256)]
            c1 += self._encode(self._compress(u, self.DU), self.DU)
        acc = [0] * 256
        for j in range(k):
            self._mul_acc(acc, t_hat[j], y_hat[j])
        v = self._intt([x % q for x in acc])
        mu = self._decompress(self._decode(m, 1), 1)
        v = [(v[x] + e2[x] + mu[x]) % q for x in range(256)]
        return c1 + self._encode(self._compress(v, self.DV), self.DV)

    def _pke_decrypt(self, dk_pke, c):
        """K-PKE.Decrypt (FIPS 203 Algorithm 15): the 32-byte message inside c."""
        q = self.Q
        k = self.K
        du = self.DU
        dv = self.DV
        acc = [0] * 256
        for i in range(k):
            u = self._decompress(self._decode(c[32 * du * i:32 * du * (i + 1)], du), du)
            s_hat = self._decode(dk_pke[384 * i:384 * (i + 1)], 12)
            self._mul_acc(acc, s_hat, self._ntt(u))
        su = self._intt([x % q for x in acc])
        v = self._decompress(self._decode(c[32 * du * k:32 * du * k + 32 * dv], dv), dv)
        w = [(v[x] - su[x]) % q for x in range(256)]
        return self._encode(self._compress(w, 1), 1)

    def keygen_internal(self, d, z):
        """ML-KEM.KeyGen_internal (FIPS 203 Algorithm 16): (1184-byte ek, 2400-byte dk) from seeds d, z."""
        d = bytes(d)
        z = bytes(z)
        if len(d) != 32 or len(z) != 32:
            raise ChromeClientError("tls: invalid ML-KEM-768 seed")
        q = self.Q
        k = self.K
        g = hashlib.sha3_512(d + bytes([k])).digest()
        rho = g[:32]
        sigma = g[32:]
        a_hat = self._matrix(rho, False)
        n = 0
        s_hat = []
        for _ in range(k):
            s_hat.append(self._ntt(self._cbd(self._prf(self.ETA1, sigma, n), self.ETA1)))
            n += 1
        e_hat = []
        for _ in range(k):
            e_hat.append(self._ntt(self._cbd(self._prf(self.ETA1, sigma, n), self.ETA1)))
            n += 1
        ek = b""
        dk_pke = b""
        for i in range(k):
            acc = [0] * 256
            for j in range(k):
                self._mul_acc(acc, a_hat[i][j], s_hat[j])
            t = [(acc[x] + e_hat[i][x]) % q for x in range(256)]
            ek += self._encode(t, 12)
            dk_pke += self._encode(s_hat[i], 12)
        ek += rho
        dk = dk_pke + ek + hashlib.sha3_256(ek).digest() + z
        return ek, dk

    def keygen(self, rand=None):
        """ML-KEM.KeyGen (FIPS 203 Algorithm 19): (ek, dk), seeds d then z drawn from rand.

        `rand(n) -> bytes` defaults to os.urandom; tests inject a deterministic
        source.
        """
        if rand is None:
            rand = os.urandom
        d = bytes(rand(32))
        z = bytes(rand(32))
        return self.keygen_internal(d, z)

    def encaps_internal(self, ek, m):
        """ML-KEM.Encaps_internal (FIPS 203 Algorithm 17) with the input checks of section 7.2.

        Returns (1088-byte ciphertext, 32-byte shared secret). The modulus
        check re-encodes the decoded t_hat and refuses a key that does not
        round-trip (a coefficient >= q).
        """
        ek = bytes(ek)
        m = bytes(m)
        if len(ek) != self.EK_BYTES:
            raise ChromeClientError("tls: invalid ML-KEM-768 encapsulation key")
        if len(m) != 32:
            raise ChromeClientError("tls: invalid ML-KEM-768 seed")
        for i in range(self.K):
            chunk = ek[384 * i:384 * (i + 1)]
            if self._encode(self._decode(chunk, 12), 12) != chunk:
                raise ChromeClientError("tls: invalid ML-KEM-768 encapsulation key")
        g = hashlib.sha3_512(m + hashlib.sha3_256(ek).digest()).digest()
        ct = self._pke_encrypt(ek, m, g[32:])
        return ct, g[:32]

    def encaps(self, ek, rand=None):
        """ML-KEM.Encaps (FIPS 203 Algorithm 20): (ct, ss), the message m drawn from rand."""
        if rand is None:
            rand = os.urandom
        return self.encaps_internal(ek, bytes(rand(32)))

    def decaps(self, dk, ct):
        """ML-KEM.Decaps (FIPS 203 Algorithms 18 and 21): the 32-byte shared secret.

        Checks the lengths and the section 7.3 hash check (H(ek) stored in dk),
        then decrypts, re-encrypts and compares in constant time
        (hmac.compare_digest); a mismatch yields the implicit-rejection secret
        SHAKE256(z || c, 32), never an error.
        """
        dk = bytes(dk)
        ct = bytes(ct)
        if len(dk) != self.DK_BYTES:
            raise ChromeClientError("tls: invalid ML-KEM-768 decapsulation key")
        if len(ct) != self.CT_BYTES:
            raise ChromeClientError("tls: invalid ML-KEM-768 ciphertext")
        k = self.K
        dk_pke = dk[:384 * k]
        ek = dk[384 * k:768 * k + 32]
        h = dk[768 * k + 32:768 * k + 64]
        z = dk[768 * k + 64:768 * k + 96]
        if not hmac.compare_digest(hashlib.sha3_256(ek).digest(), h):
            raise ChromeClientError("tls: invalid ML-KEM-768 decapsulation key")
        m2 = self._pke_decrypt(dk_pke, ct)
        g = hashlib.sha3_512(m2 + h).digest()
        k_bar = hashlib.shake_256(z + ct).digest(32)
        ct2 = self._pke_encrypt(ek, m2, g[32:])
        if hmac.compare_digest(ct, ct2):
            return g[:32]
        return k_bar


def _ch_aes_tables():
    """Every table AES encryption needs, built once: the S-box, four T-tables and Rcon.

    Returns a dict: "sbox" (256 ints), "te0".."te3" (256 32-bit words each)
    and "rcon" (10 ints). FIPS 197 section 5.1.1 defines the S-box as the
    multiplicative inverse in GF(2^8) modulo x^8 + x^4 + x^3 + x + 1 (0x11B)
    followed by the affine map; the inverse is read off log/antilog tables
    over the generator 3. te0[x] is the MixColumns column (2*S[x], S[x],
    S[x], 3*S[x]) as a big-endian word and te1..te3 are its byte rotations to
    the right, so SubBytes + ShiftRows + MixColumns is four lookups and XORs
    per column (the classic T-table layout). There is no inverse S-box: GCM
    only ever runs the cipher forward.

    A function bound ONCE as _CH_AES_TABLES, because a table assigned at
    module level and then rebound would collapse to its last binding in the
    generator (the R-0017 PoC's `_SBOX = []` ... `_SBOX = _init_aes()`).
    """
    alog = [0] * 256
    log = [0] * 256
    p = 1
    for i in range(255):
        alog[i] = p
        log[p] = i
        p ^= (p << 1) ^ (0x11B if p & 0x80 else 0)
    sbox = [0] * 256
    for i in range(256):
        if i == 0:
            x = 0
        else:
            x = alog[(255 - log[i]) % 255]
        s = x
        for _ in range(4):
            s = ((s << 1) | (s >> 7)) & 0xFF
            x ^= s
        sbox[i] = x ^ 0x63
    te0 = []
    te1 = []
    te2 = []
    te3 = []
    for s in sbox:
        s2 = ((s << 1) ^ (0x11B if s & 0x80 else 0)) & 0xFF
        w = (s2 << 24) | (s << 16) | (s << 8) | (s2 ^ s)
        te0.append(w)
        te1.append(((w >> 8) | (w << 24)) & 0xFFFFFFFF)
        te2.append(((w >> 16) | (w << 16)) & 0xFFFFFFFF)
        te3.append(((w >> 24) | (w << 8)) & 0xFFFFFFFF)
    rcon = []
    r = 1
    for _ in range(10):
        rcon.append(r)
        r = ((r << 1) ^ (0x11B if r & 0x80 else 0)) & 0xFF
    tables = {}
    tables["sbox"] = tuple(sbox)
    tables["te0"] = tuple(te0)
    tables["te1"] = tuple(te1)
    tables["te2"] = tuple(te2)
    tables["te3"] = tuple(te3)
    tables["rcon"] = tuple(rcon)
    return tables


_CH_AES_TABLES = _ch_aes_tables()


class _ChAesGcm:
    """AES-GCM (NIST SP 800-38D) for one key: the TLS 1.3 AEAD of 0x1301 and 0x1302.

    The key is expanded and the GHASH multiplication table for H = E(K, 0^128)
    is built ONCE, in __init__, and reused for every record sealed or opened
    under this key -- the R-0017 PoC rebuilt both per record (D15). GHASH uses
    a per-key table of 16 x 256 precomputed products (one per byte position of
    the 128-bit operand), so a block multiply is 16 lookups and XORs instead
    of a 128-step bit loop; the CTR keystream is XORed onto the data as one
    wide integer rather than byte by byte.

    `seal(nonce, aad, pt)` returns ciphertext || 16-byte tag; `open(nonce,
    aad, ct_and_tag)` returns the plaintext. The nonce is 12 bytes (the only
    size TLS 1.3 uses, J0 = nonce || 0^31 || 1). Refusals are
    ChromeClientError("tls: ..."): a key that is not 16, 24 or 32 bytes, a
    nonce that is not 12, a plaintext past the 2^32 - 2 block counter limit,
    input shorter than the tag, and a tag that does not verify ("tls: bad
    record MAC"), which is compared ONLY with hmac.compare_digest and checked
    before any plaintext is produced. Pure Python and NOT constant-time
    (declared in the module docstring).
    """

    TAG_BYTES = 16
    NONCE_BYTES = 12
    MAX_PLAINTEXT = (2 ** 32 - 2) * 16

    def __init__(self, key):
        key = bytes(key)
        if len(key) not in (16, 24, 32):
            raise ChromeClientError("tls: invalid AES-GCM key length")
        tables = _CH_AES_TABLES
        sbox = tables["sbox"]
        rcon = tables["rcon"]
        nk = len(key) // 4
        nr = nk + 6
        w = list(struct.unpack(">%dI" % nk, key))
        for i in range(nk, 4 * (nr + 1)):
            t = w[i - 1]
            if i % nk == 0:
                t = ((t << 8) & 0xFFFFFFFF) | (t >> 24)
                t = (sbox[t >> 24] << 24) | (sbox[(t >> 16) & 0xFF] << 16) | (sbox[(t >> 8) & 0xFF] << 8) | sbox[t & 0xFF]
                t ^= rcon[i // nk - 1] << 24
            elif nk > 6 and i % nk == 4:
                t = (sbox[t >> 24] << 24) | (sbox[(t >> 16) & 0xFF] << 16) | (sbox[(t >> 8) & 0xFF] << 8) | sbox[t & 0xFF]
            w.append(w[i - nk] ^ t)
        self._rk = tuple(w)
        self._nr = nr
        self._rk_pairs = tuple(tuple(w[4 * r:4 * r + 8]) for r in range(2, nr, 2))
        self._sbox = sbox
        self._te = (tables["te0"], tables["te1"], tables["te2"], tables["te3"])
        h = int.from_bytes(self.encrypt_block(bytes(16)), "big")
        self._gh = self._ghash_tables(h)

    def _ghash_tables(self, h):
        """The 16 per-byte-position tables: tables[pos][b] = (b placed at byte pos) * H in GF(2^128).

        GCM's bit order puts x^0 at the most significant bit, so multiplying by
        x is a right shift, reduced by R = 0xE1 || 0^120 when a bit falls off.
        basis[i] = H * x^i; a byte's value is the XOR of the basis entries of
        its set bits, filled by linearity (tab[b] = tab[b without its lowest
        bit] ^ tab[lowest bit]).
        """
        r = 0xE1 << 120
        basis = []
        v = h
        for _ in range(128):
            basis.append(v)
            if v & 1:
                v = (v >> 1) ^ r
            else:
                v >>= 1
        tables = []
        for pos in range(16):
            tab = [0] * 256
            for k in range(8):
                tab[0x80 >> k] = basis[8 * pos + k]
            for b in range(1, 256):
                low = b & -b
                if b != low:
                    tab[b] = tab[b ^ low] ^ tab[low]
            tables.append(tuple(tab))
        return tuple(tables)

    def _encrypt_words(self, s0, s1, s2, s3):
        """The AES block cipher (FIPS 197) on four big-endian column words; returns four words."""
        rk = self._rk
        te0, te1, te2, te3 = self._te
        sbox = self._sbox
        s0 ^= rk[0]
        s1 ^= rk[1]
        s2 ^= rk[2]
        s3 ^= rk[3]
        k = 4
        for _ in range(self._nr - 1):
            t0 = te0[s0 >> 24] ^ te1[(s1 >> 16) & 0xFF] ^ te2[(s2 >> 8) & 0xFF] ^ te3[s3 & 0xFF] ^ rk[k]
            t1 = te0[s1 >> 24] ^ te1[(s2 >> 16) & 0xFF] ^ te2[(s3 >> 8) & 0xFF] ^ te3[s0 & 0xFF] ^ rk[k + 1]
            t2 = te0[s2 >> 24] ^ te1[(s3 >> 16) & 0xFF] ^ te2[(s0 >> 8) & 0xFF] ^ te3[s1 & 0xFF] ^ rk[k + 2]
            t3 = te0[s3 >> 24] ^ te1[(s0 >> 16) & 0xFF] ^ te2[(s1 >> 8) & 0xFF] ^ te3[s2 & 0xFF] ^ rk[k + 3]
            s0 = t0
            s1 = t1
            s2 = t2
            s3 = t3
            k += 4
        t0 = ((sbox[s0 >> 24] << 24) | (sbox[(s1 >> 16) & 0xFF] << 16) | (sbox[(s2 >> 8) & 0xFF] << 8) | sbox[s3 & 0xFF]) ^ rk[k]
        t1 = ((sbox[s1 >> 24] << 24) | (sbox[(s2 >> 16) & 0xFF] << 16) | (sbox[(s3 >> 8) & 0xFF] << 8) | sbox[s0 & 0xFF]) ^ rk[k + 1]
        t2 = ((sbox[s2 >> 24] << 24) | (sbox[(s3 >> 16) & 0xFF] << 16) | (sbox[(s0 >> 8) & 0xFF] << 8) | sbox[s1 & 0xFF]) ^ rk[k + 2]
        t3 = ((sbox[s3 >> 24] << 24) | (sbox[(s0 >> 16) & 0xFF] << 16) | (sbox[(s1 >> 8) & 0xFF] << 8) | sbox[s2 & 0xFF]) ^ rk[k + 3]
        return t0, t1, t2, t3

    def encrypt_block(self, block):
        """AES-ECB of exactly one 16-byte block (the raw cipher: H, the tag mask, KATs)."""
        block = bytes(block)
        if len(block) != 16:
            raise ChromeClientError("tls: AES block must be 16 bytes")
        s0, s1, s2, s3 = struct.unpack(">4I", block)
        return struct.pack(">4I", *self._encrypt_words(s0, s1, s2, s3))

    def _ctr(self, n0, n1, n2, data):
        """GCTR from counter block nonce || 2 (SP 800-38D inc32): data XOR keystream, one wide XOR.

        The cipher is inlined here, not called through _encrypt_words: this
        loop is the throughput floor (G3). Only the fourth input word (the
        counter) changes between blocks, and each word of round 1 reads
        exactly one byte of it, so the other three terms of every round-1
        word are computed once per record (c0..c3). The middle rounds run in
        pairs over pre-split round keys (nr - 2 is even for every key size),
        so no index arithmetic and no state shuffling remain in the loop.
        """
        n = len(data)
        if n == 0:
            return b""
        te0, te1, te2, te3 = self._te
        sbox = self._sbox
        rk = self._rk
        pairs = self._rk_pairs
        nr = self._nr
        f0, f1, f2, f3 = rk[4 * nr:4 * nr + 4]
        a0 = n0 ^ rk[0]
        a1 = n1 ^ rk[1]
        a2 = n2 ^ rk[2]
        r3 = rk[3]
        c0 = te0[a0 >> 24] ^ te1[(a1 >> 16) & 0xFF] ^ te2[(a2 >> 8) & 0xFF] ^ rk[4]
        c1 = te0[a1 >> 24] ^ te1[(a2 >> 16) & 0xFF] ^ te3[a0 & 0xFF] ^ rk[5]
        c2 = te0[a2 >> 24] ^ te2[(a0 >> 8) & 0xFF] ^ te3[a1 & 0xFF] ^ rk[6]
        c3 = te1[(a0 >> 16) & 0xFF] ^ te2[(a1 >> 8) & 0xFF] ^ te3[a2 & 0xFF] ^ rk[7]
        pack = struct.pack
        blocks = []
        ctr = 2
        for _ in range((n + 15) // 16):
            s3 = ctr ^ r3
            s0 = c0 ^ te3[s3 & 0xFF]
            s1 = c1 ^ te2[(s3 >> 8) & 0xFF]
            s2 = c2 ^ te1[(s3 >> 16) & 0xFF]
            s3 = c3 ^ te0[s3 >> 24]
            for k0, k1, k2, k3, k4, k5, k6, k7 in pairs:
                t0 = te0[s0 >> 24] ^ te1[(s1 >> 16) & 0xFF] ^ te2[(s2 >> 8) & 0xFF] ^ te3[s3 & 0xFF] ^ k0
                t1 = te0[s1 >> 24] ^ te1[(s2 >> 16) & 0xFF] ^ te2[(s3 >> 8) & 0xFF] ^ te3[s0 & 0xFF] ^ k1
                t2 = te0[s2 >> 24] ^ te1[(s3 >> 16) & 0xFF] ^ te2[(s0 >> 8) & 0xFF] ^ te3[s1 & 0xFF] ^ k2
                t3 = te0[s3 >> 24] ^ te1[(s0 >> 16) & 0xFF] ^ te2[(s1 >> 8) & 0xFF] ^ te3[s2 & 0xFF] ^ k3
                s0 = te0[t0 >> 24] ^ te1[(t1 >> 16) & 0xFF] ^ te2[(t2 >> 8) & 0xFF] ^ te3[t3 & 0xFF] ^ k4
                s1 = te0[t1 >> 24] ^ te1[(t2 >> 16) & 0xFF] ^ te2[(t3 >> 8) & 0xFF] ^ te3[t0 & 0xFF] ^ k5
                s2 = te0[t2 >> 24] ^ te1[(t3 >> 16) & 0xFF] ^ te2[(t0 >> 8) & 0xFF] ^ te3[t1 & 0xFF] ^ k6
                s3 = te0[t3 >> 24] ^ te1[(t0 >> 16) & 0xFF] ^ te2[(t1 >> 8) & 0xFF] ^ te3[t2 & 0xFF] ^ k7
            w0 = ((sbox[s0 >> 24] << 24) | (sbox[(s1 >> 16) & 0xFF] << 16) | (sbox[(s2 >> 8) & 0xFF] << 8) | sbox[s3 & 0xFF]) ^ f0
            w1 = ((sbox[s1 >> 24] << 24) | (sbox[(s2 >> 16) & 0xFF] << 16) | (sbox[(s3 >> 8) & 0xFF] << 8) | sbox[s0 & 0xFF]) ^ f1
            w2 = ((sbox[s2 >> 24] << 24) | (sbox[(s3 >> 16) & 0xFF] << 16) | (sbox[(s0 >> 8) & 0xFF] << 8) | sbox[s1 & 0xFF]) ^ f2
            w3 = ((sbox[s3 >> 24] << 24) | (sbox[(s0 >> 16) & 0xFF] << 16) | (sbox[(s1 >> 8) & 0xFF] << 8) | sbox[s2 & 0xFF]) ^ f3
            blocks.append(pack(">4I", w0, w1, w2, w3))
            ctr = (ctr + 1) & 0xFFFFFFFF
        ks = b"".join(blocks)
        return (int.from_bytes(data, "big") ^ int.from_bytes(ks[:n], "big")).to_bytes(n, "big")

    def _ghash(self, aad, ct):
        """GHASH_H(aad || pad || ct || pad || len64(aad) || len64(ct)) as a 128-bit int."""
        t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15 = self._gh
        lens = struct.pack(">QQ", len(aad) * 8, len(ct) * 8)
        y = 0
        for data in (aad, ct, lens):
            n = len(data)
            for i in range(0, n, 16):
                blk = data[i:i + 16]
                if len(blk) < 16:
                    blk = blk + bytes(16 - len(blk))
                b = (y ^ int.from_bytes(blk, "big")).to_bytes(16, "big")
                y = t0[b[0]] ^ t1[b[1]] ^ t2[b[2]] ^ t3[b[3]] ^ t4[b[4]] ^ t5[b[5]] ^ t6[b[6]] ^ t7[b[7]] ^ t8[b[8]] ^ t9[b[9]] ^ t10[b[10]] ^ t11[b[11]] ^ t12[b[12]] ^ t13[b[13]] ^ t14[b[14]] ^ t15[b[15]]
        return y

    def _tag(self, n0, n1, n2, aad, ct):
        """The 16-byte tag: GHASH ^ E(K, J0), J0 = nonce || 0^31 || 1."""
        mask = int.from_bytes(struct.pack(">4I", *self._encrypt_words(n0, n1, n2, 1)), "big")
        return (self._ghash(aad, ct) ^ mask).to_bytes(16, "big")

    def _nonce_words(self, nonce):
        nonce = bytes(nonce)
        if len(nonce) != self.NONCE_BYTES:
            raise ChromeClientError("tls: invalid AES-GCM nonce length")
        return struct.unpack(">3I", nonce)

    def seal(self, nonce, aad, pt):
        """Encrypt and authenticate: returns ciphertext || 16-byte tag."""
        n0, n1, n2 = self._nonce_words(nonce)
        aad = bytes(aad)
        pt = bytes(pt)
        if len(pt) > self.MAX_PLAINTEXT:
            raise ChromeClientError("tls: AES-GCM plaintext too long")
        ct = self._ctr(n0, n1, n2, pt)
        return ct + self._tag(n0, n1, n2, aad, ct)

    def open(self, nonce, aad, ct_and_tag):
        """Verify, then decrypt: the plaintext, or ChromeClientError("tls: bad record MAC")."""
        n0, n1, n2 = self._nonce_words(nonce)
        aad = bytes(aad)
        data = bytes(ct_and_tag)
        if len(data) < self.TAG_BYTES:
            raise ChromeClientError("tls: AEAD input shorter than its tag")
        ct = data[:-self.TAG_BYTES]
        received = data[-self.TAG_BYTES:]
        if len(ct) > self.MAX_PLAINTEXT:
            raise ChromeClientError("tls: AES-GCM ciphertext too long")
        if not hmac.compare_digest(self._tag(n0, n1, n2, aad, ct), received):
            raise ChromeClientError("tls: bad record MAC")
        return self._ctr(n0, n1, n2, ct)


class _ChChaCha20Poly1305:
    """ChaCha20-Poly1305 (RFC 8439 section 2.8) for one key: the TLS 1.3 AEAD of 0x1303.

    The key's eight words are unpacked once in __init__. The ChaCha20 block
    (section 2.3) is written out as sixteen local words and unrolled quarter
    rounds -- no per-round list indexing -- and the keystream is XORed onto
    the data as one wide integer. The Poly1305 one-time key is the first 32
    bytes of block 0 (section 2.6); the payload starts at counter 1.

    `seal(nonce, aad, pt)` returns ciphertext || 16-byte tag; `open(nonce,
    aad, ct_and_tag)` returns the plaintext. Refusals are
    ChromeClientError("tls: ..."): a key that is not 32 bytes, a nonce that is
    not 12, a plaintext past the 32-bit block counter, input shorter than the
    tag, and a tag that does not verify ("tls: bad record MAC"), compared ONLY
    with hmac.compare_digest and checked before any plaintext is produced.
    """

    TAG_BYTES = 16
    NONCE_BYTES = 12
    MAX_PLAINTEXT = (2 ** 32 - 1) * 64

    def __init__(self, key):
        key = bytes(key)
        if len(key) != 32:
            raise ChromeClientError("tls: invalid ChaCha20-Poly1305 key length")
        self._kw = struct.unpack("<8I", key)

    def _block(self, counter, n0, n1, n2):
        """The 64-byte ChaCha20 block (RFC 8439 section 2.3): 20 rounds, then the input added back."""
        m = 0xFFFFFFFF
        k0, k1, k2, k3, k4, k5, k6, k7 = self._kw
        x0 = 0x61707865
        x1 = 0x3320646E
        x2 = 0x79622D32
        x3 = 0x6B206574
        x4 = k0
        x5 = k1
        x6 = k2
        x7 = k3
        x8 = k4
        x9 = k5
        x10 = k6
        x11 = k7
        x12 = counter
        x13 = n0
        x14 = n1
        x15 = n2
        for _ in range(10):
            # column round: QR(0, 4, 8, 12)
            x0 = (x0 + x4) & m
            x12 ^= x0
            x12 = ((x12 << 16) & m) | (x12 >> 16)
            x8 = (x8 + x12) & m
            x4 ^= x8
            x4 = ((x4 << 12) & m) | (x4 >> 20)
            x0 = (x0 + x4) & m
            x12 ^= x0
            x12 = ((x12 << 8) & m) | (x12 >> 24)
            x8 = (x8 + x12) & m
            x4 ^= x8
            x4 = ((x4 << 7) & m) | (x4 >> 25)
            # QR(1, 5, 9, 13)
            x1 = (x1 + x5) & m
            x13 ^= x1
            x13 = ((x13 << 16) & m) | (x13 >> 16)
            x9 = (x9 + x13) & m
            x5 ^= x9
            x5 = ((x5 << 12) & m) | (x5 >> 20)
            x1 = (x1 + x5) & m
            x13 ^= x1
            x13 = ((x13 << 8) & m) | (x13 >> 24)
            x9 = (x9 + x13) & m
            x5 ^= x9
            x5 = ((x5 << 7) & m) | (x5 >> 25)
            # QR(2, 6, 10, 14)
            x2 = (x2 + x6) & m
            x14 ^= x2
            x14 = ((x14 << 16) & m) | (x14 >> 16)
            x10 = (x10 + x14) & m
            x6 ^= x10
            x6 = ((x6 << 12) & m) | (x6 >> 20)
            x2 = (x2 + x6) & m
            x14 ^= x2
            x14 = ((x14 << 8) & m) | (x14 >> 24)
            x10 = (x10 + x14) & m
            x6 ^= x10
            x6 = ((x6 << 7) & m) | (x6 >> 25)
            # QR(3, 7, 11, 15)
            x3 = (x3 + x7) & m
            x15 ^= x3
            x15 = ((x15 << 16) & m) | (x15 >> 16)
            x11 = (x11 + x15) & m
            x7 ^= x11
            x7 = ((x7 << 12) & m) | (x7 >> 20)
            x3 = (x3 + x7) & m
            x15 ^= x3
            x15 = ((x15 << 8) & m) | (x15 >> 24)
            x11 = (x11 + x15) & m
            x7 ^= x11
            x7 = ((x7 << 7) & m) | (x7 >> 25)
            # diagonal round: QR(0, 5, 10, 15)
            x0 = (x0 + x5) & m
            x15 ^= x0
            x15 = ((x15 << 16) & m) | (x15 >> 16)
            x10 = (x10 + x15) & m
            x5 ^= x10
            x5 = ((x5 << 12) & m) | (x5 >> 20)
            x0 = (x0 + x5) & m
            x15 ^= x0
            x15 = ((x15 << 8) & m) | (x15 >> 24)
            x10 = (x10 + x15) & m
            x5 ^= x10
            x5 = ((x5 << 7) & m) | (x5 >> 25)
            # QR(1, 6, 11, 12)
            x1 = (x1 + x6) & m
            x12 ^= x1
            x12 = ((x12 << 16) & m) | (x12 >> 16)
            x11 = (x11 + x12) & m
            x6 ^= x11
            x6 = ((x6 << 12) & m) | (x6 >> 20)
            x1 = (x1 + x6) & m
            x12 ^= x1
            x12 = ((x12 << 8) & m) | (x12 >> 24)
            x11 = (x11 + x12) & m
            x6 ^= x11
            x6 = ((x6 << 7) & m) | (x6 >> 25)
            # QR(2, 7, 8, 13)
            x2 = (x2 + x7) & m
            x13 ^= x2
            x13 = ((x13 << 16) & m) | (x13 >> 16)
            x8 = (x8 + x13) & m
            x7 ^= x8
            x7 = ((x7 << 12) & m) | (x7 >> 20)
            x2 = (x2 + x7) & m
            x13 ^= x2
            x13 = ((x13 << 8) & m) | (x13 >> 24)
            x8 = (x8 + x13) & m
            x7 ^= x8
            x7 = ((x7 << 7) & m) | (x7 >> 25)
            # QR(3, 4, 9, 14)
            x3 = (x3 + x4) & m
            x14 ^= x3
            x14 = ((x14 << 16) & m) | (x14 >> 16)
            x9 = (x9 + x14) & m
            x4 ^= x9
            x4 = ((x4 << 12) & m) | (x4 >> 20)
            x3 = (x3 + x4) & m
            x14 ^= x3
            x14 = ((x14 << 8) & m) | (x14 >> 24)
            x9 = (x9 + x14) & m
            x4 ^= x9
            x4 = ((x4 << 7) & m) | (x4 >> 25)
        return struct.pack("<16I", (x0 + 0x61707865) & m, (x1 + 0x3320646E) & m, (x2 + 0x79622D32) & m, (x3 + 0x6B206574) & m, (x4 + k0) & m, (x5 + k1) & m, (x6 + k2) & m, (x7 + k3) & m, (x8 + k4) & m, (x9 + k5) & m, (x10 + k6) & m, (x11 + k7) & m, (x12 + counter) & m, (x13 + n0) & m, (x14 + n1) & m, (x15 + n2) & m)

    def _xor(self, n0, n1, n2, data):
        """ChaCha20 encryption (RFC 8439 section 2.4) from block counter 1: data XOR keystream."""
        n = len(data)
        if n == 0:
            return b""
        block = self._block
        blocks = []
        for i in range((n + 63) // 64):
            blocks.append(block(1 + i, n0, n1, n2))
        ks = b"".join(blocks)
        return (int.from_bytes(data, "big") ^ int.from_bytes(ks[:n], "big")).to_bytes(n, "big")

    def _tag(self, n0, n1, n2, aad, ct):
        """Poly1305 (RFC 8439 sections 2.5, 2.6, 2.8) over aad || pad || ct || pad || le64 lengths."""
        otk = self._block(0, n0, n1, n2)
        r = int.from_bytes(otk[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
        s = int.from_bytes(otk[16:32], "little")
        p = (1 << 130) - 5
        hibit = 1 << 128
        mac_data = aad + bytes(-len(aad) % 16) + ct + bytes(-len(ct) % 16) + struct.pack("<QQ", len(aad), len(ct))
        acc = 0
        for i in range(0, len(mac_data), 16):
            acc = (acc + int.from_bytes(mac_data[i:i + 16], "little") + hibit) * r % p
        return ((acc + s) & (hibit - 1)).to_bytes(16, "little")

    def _nonce_words(self, nonce):
        nonce = bytes(nonce)
        if len(nonce) != self.NONCE_BYTES:
            raise ChromeClientError("tls: invalid ChaCha20-Poly1305 nonce length")
        return struct.unpack("<3I", nonce)

    def seal(self, nonce, aad, pt):
        """Encrypt and authenticate: returns ciphertext || 16-byte tag."""
        n0, n1, n2 = self._nonce_words(nonce)
        aad = bytes(aad)
        pt = bytes(pt)
        if len(pt) > self.MAX_PLAINTEXT:
            raise ChromeClientError("tls: ChaCha20-Poly1305 plaintext too long")
        ct = self._xor(n0, n1, n2, pt)
        return ct + self._tag(n0, n1, n2, aad, ct)

    def open(self, nonce, aad, ct_and_tag):
        """Verify, then decrypt: the plaintext, or ChromeClientError("tls: bad record MAC")."""
        n0, n1, n2 = self._nonce_words(nonce)
        aad = bytes(aad)
        data = bytes(ct_and_tag)
        if len(data) < self.TAG_BYTES:
            raise ChromeClientError("tls: AEAD input shorter than its tag")
        ct = data[:-self.TAG_BYTES]
        received = data[-self.TAG_BYTES:]
        if len(ct) > self.MAX_PLAINTEXT:
            raise ChromeClientError("tls: ChaCha20-Poly1305 ciphertext too long")
        if not hmac.compare_digest(self._tag(n0, n1, n2, aad, ct), received):
            raise ChromeClientError("tls: bad record MAC")
        return self._xor(n0, n1, n2, ct)


def _ch_hkdf_extract(hashmod, salt, ikm):
    """HKDF-Extract (RFC 5869 section 2.2): PRK = HMAC-Hash(salt, IKM).

    `hashmod` is the hashlib constructor of the negotiated suite (sha256 for
    0x1301/0x1303, sha384 for 0x1302). A salt of None means HashLen zero
    bytes, as the RFC says.
    """
    if salt is None:
        salt = bytes(hashmod().digest_size)
    return hmac.new(bytes(salt), bytes(ikm), hashmod).digest()


def _ch_hkdf_expand(hashmod, prk, info, length):
    """HKDF-Expand (RFC 5869 section 2.3): T(1) || T(2) || ... truncated to length bytes.

    A length above 255 * HashLen (the RFC's bound) or below zero is refused
    with ChromeClientError("tls: HKDF output length out of range").
    """
    hlen = hashmod().digest_size
    if length < 0 or length > 255 * hlen:
        raise ChromeClientError("tls: HKDF output length out of range")
    prk = bytes(prk)
    info = bytes(info)
    out = b""
    t = b""
    i = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashmod).digest()
        out += t
        i += 1
    return out[:length]


def _ch_hkdf_expand_label(hashmod, secret, label, context, length):
    """HKDF-Expand-Label (RFC 8446 section 7.1) with the "tls13 " prefix.

    HkdfLabel = uint16 length || opaque label<7..255> ("tls13 " + label) ||
    opaque context<0..255>. `label` and `context` are bytes. A label, context
    or length the structure cannot encode is refused with
    ChromeClientError("tls: invalid HKDF label").
    """
    full = b"tls13 " + bytes(label)
    context = bytes(context)
    if len(full) < 7 or len(full) > 255 or len(context) > 255 or length < 0 or length > 0xFFFF:
        raise ChromeClientError("tls: invalid HKDF label")
    info = struct.pack(">H", length) + bytes([len(full)]) + full + bytes([len(context)]) + context
    return _ch_hkdf_expand(hashmod, secret, info, length)


def _ch_derive_secret(hashmod, secret, label, transcript_hash):
    """Derive-Secret (RFC 8446 section 7.1): HKDF-Expand-Label(secret, label, transcript_hash, HashLen).

    Takes the transcript HASH, not the messages: the engine keeps a running
    hash object and passes its digest (for the "derived" steps that is
    Hash(""), i.e. hashmod(b"").digest()). A hash of the wrong length is
    refused with ChromeClientError("tls: transcript hash length mismatch").
    """
    hlen = hashmod().digest_size
    th = bytes(transcript_hash)
    if len(th) != hlen:
        raise ChromeClientError("tls: transcript hash length mismatch")
    return _ch_hkdf_expand_label(hashmod, secret, label, th, hlen)


CH_MAX_PLAINTEXT_BYTES = 16384


class _ChReader:
    """A bounded cursor over one handshake message or extension block.

    Every read names the field it reads, and a read past the end raises
    ChromeClientError("tls: truncated <field>") instead of returning a short
    slice -- the R-0017 PoC sliced `r[pos:pos + n]` and parsed whatever came
    back (D3). `u8/u16/u24` are big-endian integers, `bytes(n)` is n raw
    bytes, `vec8/vec16/vec24` are the RFC 8446 section 3.4 length-prefixed
    vectors, `sub16` wraps a 16-bit vector in its own reader (an extension
    list), and `end(field)` refuses trailing bytes ("tls: trailing bytes
    after <field>"). The reader never grows: it holds the one buffer it was
    given, whose size the record and handshake ceilings already bound.
    """

    def __init__(self, buf):
        self._buf = bytes(buf)
        self._pos = 0

    def remaining(self):
        """Bytes not yet read."""
        return len(self._buf) - self._pos

    def bytes(self, n, field="bytes"):
        """The next n raw bytes, or ChromeClientError("tls: truncated <field>")."""
        if n < 0 or n > len(self._buf) - self._pos:
            raise ChromeClientError("tls: truncated %s" % field)
        start = self._pos
        self._pos = start + n
        return self._buf[start:start + n]

    def u8(self, field="u8"):
        """One byte as an int."""
        return self.bytes(1, field)[0]

    def u16(self, field="u16"):
        """A big-endian 16-bit int."""
        return int.from_bytes(self.bytes(2, field), "big")

    def u24(self, field="u24"):
        """A big-endian 24-bit int (a handshake message length)."""
        return int.from_bytes(self.bytes(3, field), "big")

    def vec8(self, field="vector"):
        """opaque field<0..2^8-1>: an 8-bit length, then that many bytes."""
        return self.bytes(self.u8(field), field)

    def vec16(self, field="vector"):
        """opaque field<0..2^16-1>: a 16-bit length, then that many bytes."""
        return self.bytes(self.u16(field), field)

    def vec24(self, field="vector"):
        """opaque field<0..2^24-1>: a 24-bit length, then that many bytes."""
        return self.bytes(self.u24(field), field)

    def sub16(self, field="vector"):
        """A 16-bit vector as its own _ChReader, so a nested list cannot read past its own end."""
        return _ChReader(self.vec16(field))

    def end(self, field="message"):
        """Refuse anything left unread: a structure that ends early is as malformed as one cut short."""
        if self._pos != len(self._buf):
            raise ChromeClientError("tls: trailing bytes after %s" % field)


class _ChRecordReader:
    """The sans-IO TLS record splitter: bytes in, (content_type, body) out.

    `feed(data)` appends what the socket returned; `next_record()` returns the
    next complete record or None when more bytes are needed. The ceiling is
    checked from the 5-byte header, BEFORE the body is waited for, so a peer
    announcing a record of 65535 bytes is refused with its header and not
    after we have buffered it -- the R-0017 PoC accumulated `inbuf` until the
    announced length arrived, whatever it was (D1). A protected record
    (outer type 23, application_data) may carry CH_MAX_RECORD_BYTES
    (2^14 + 256: 16640 accepted, 16641 refused); any other type is a
    TLSPlaintext and may carry CH_MAX_PLAINTEXT_BYTES (RFC 8446 sections 5.1,
    5.2). Past either: ChromeClientError("tls: record_overflow: ..."). A
    content type TLS 1.3 does not define (not 20, 21, 22 or 23) is
    ChromeClientError("tls: unexpected_message: ..."). The legacy record
    version is ignored, as RFC 8446 section 5.1 says. What stays buffered is
    at most one partial record plus the caller's last feed, provided the
    caller drains after every feed.
    """

    def __init__(self):
        self._buf = b""

    def feed(self, data):
        """Append bytes read from the transport."""
        self._buf += bytes(data)

    def pending(self):
        """Bytes buffered that do not yet form a complete record."""
        return len(self._buf)

    def next_record(self):
        """The next (content_type, body), None when incomplete; refuses from the header alone."""
        buf = self._buf
        if len(buf) < 5:
            return None
        ctype = buf[0]
        length = int.from_bytes(buf[3:5], "big")
        if ctype not in (20, 21, 22, 23):
            raise ChromeClientError("tls: unexpected_message: record content type %d" % ctype)
        if ctype == 23:
            limit = CH_MAX_RECORD_BYTES
        else:
            limit = CH_MAX_PLAINTEXT_BYTES
        if length > limit:
            raise ChromeClientError("tls: record_overflow: record of %d bytes exceeds %d" % (length, limit))
        if len(buf) < 5 + length:
            return None
        body = buf[5:5 + length]
        self._buf = buf[5 + length:]
        return ctype, body


class _ChHandshakeReader:
    """Reassembles handshake messages from record fragments, bounded by CH_MAX_HANDSHAKE_BYTES.

    `feed(fragment)` appends the content of a handshake record (plaintext or
    decrypted); `next_message()` returns (msg_type, message) -- the message
    INCLUDING its 4-byte header, which is what the transcript hashes -- or
    None while the message is incomplete. The 24-bit length is checked as
    soon as the 4-byte header is present, in `feed` and again in
    `next_message`, so a peer that announces a 16 MiB message is refused
    before its body is buffered: ChromeClientError("tls: handshake message
    of N bytes exceeds CH_MAX_HANDSHAKE_BYTES") (D2; the R-0017 PoC's
    `next_handshake` waited for any announced length). `pending()` lets the
    engine refuse a message that spans a key change (RFC 8446 section 5.1).
    """

    def __init__(self):
        self._buf = b""

    def _check(self):
        buf = self._buf
        if len(buf) >= 4:
            length = int.from_bytes(buf[1:4], "big")
            if length > CH_MAX_HANDSHAKE_BYTES:
                raise ChromeClientError("tls: handshake message of %d bytes exceeds %d" % (length, CH_MAX_HANDSHAKE_BYTES))
        return buf

    def feed(self, fragment):
        """Append one handshake record's content; refuses an over-ceiling header at once."""
        self._buf += bytes(fragment)
        self._check()

    def pending(self):
        """Bytes of an incomplete handshake message still buffered."""
        return len(self._buf)

    def next_message(self):
        """The next complete (msg_type, message incl. header), or None."""
        buf = self._check()
        if len(buf) < 4:
            return None
        end = 4 + int.from_bytes(buf[1:4], "big")
        if len(buf) < end:
            return None
        msg = buf[:end]
        self._buf = buf[end:]
        return msg[0], msg


class _ChRecordCipher:
    """One direction's TLS 1.3 record protection (RFC 8446 sections 5.2-5.4).

    `aead` is a _ChAesGcm or _ChChaCha20Poly1305 built from the traffic key;
    `iv` is the 12-byte write_iv. The per-record nonce is iv XOR the 64-bit
    sequence number left-padded to 12 bytes (section 5.3); `seq` starts at 0
    (a test may preset it) and advances once per record sealed or opened.

    `seal(content_type, content, pad=0)` returns the whole protected record:
    the header 23 | 0x0303 | length, which is also the AAD, then the AEAD
    output over TLSInnerPlaintext = content || content_type || pad zeros.
    `open(outer_type, body)` takes the record's outer type and body, and
    returns (content_type, content) with the padding stripped: the inner
    type is the last non-zero byte (section 5.4).

    Refusals, each ChromeClientError("tls: ..."). A body over
    CH_MAX_RECORD_BYTES, or a decrypted TLSInnerPlaintext over
    CH_MAX_PLAINTEXT_BYTES + 1, is `record_overflow` (sections 5.2, 5.4). An
    outer type other than 23, and an inner plaintext of zeros only, is
    `unexpected_message`. A tag that does not verify is "tls: bad record
    MAC", compared inside the AEAD with hmac.compare_digest before any
    plaintext exists. The sequence number 2^64 - 1 is "tls: record sequence
    number exhausted": section 5.3 forbids a wrap, and this refuses one
    record early, so no record is ever protected under the value after which
    the counter could only wrap (a KeyUpdate replaces the cipher long
    before). On seal: content over CH_MAX_PLAINTEXT_BYTES, padding that
    takes the inner plaintext past CH_MAX_PLAINTEXT_BYTES + 1, and a content
    type outside 1..255.
    """

    SEQ_LIMIT = 2 ** 64 - 1

    def __init__(self, aead, iv, seq=0):
        iv = bytes(iv)
        if len(iv) != 12:
            raise ChromeClientError("tls: invalid record IV length")
        if seq < 0 or seq > self.SEQ_LIMIT:
            raise ChromeClientError("tls: invalid record sequence number")
        self._aead = aead
        self._iv = int.from_bytes(iv, "big")
        self.seq = seq

    def _nonce(self):
        """The nonce for the current seq; refuses the last 64-bit value (see the class docstring)."""
        if self.seq >= self.SEQ_LIMIT:
            raise ChromeClientError("tls: record sequence number exhausted")
        return (self._iv ^ self.seq).to_bytes(12, "big")

    def seal(self, content_type, content, pad=0):
        """Protect one record: header || AEAD(TLSInnerPlaintext), and advance seq."""
        if content_type < 1 or content_type > 255:
            raise ChromeClientError("tls: invalid record content type")
        content = bytes(content)
        if len(content) > CH_MAX_PLAINTEXT_BYTES:
            raise ChromeClientError("tls: record plaintext of %d bytes exceeds %d" % (len(content), CH_MAX_PLAINTEXT_BYTES))
        if pad < 0 or len(content) + 1 + pad > CH_MAX_PLAINTEXT_BYTES + 1:
            raise ChromeClientError("tls: invalid record padding")
        inner = content + bytes([content_type]) + bytes(pad)
        nonce = self._nonce()
        header = b"\x17\x03\x03" + (len(inner) + self._aead.TAG_BYTES).to_bytes(2, "big")
        body = self._aead.seal(nonce, header, inner)
        self.seq += 1
        return header + body

    def open(self, outer_type, body):
        """Verify and decrypt one record body: (content_type, content), padding stripped."""
        if outer_type != 23:
            raise ChromeClientError("tls: unexpected_message: protected record of content type %d" % outer_type)
        body = bytes(body)
        if len(body) > CH_MAX_RECORD_BYTES:
            raise ChromeClientError("tls: record_overflow: record of %d bytes exceeds %d" % (len(body), CH_MAX_RECORD_BYTES))
        nonce = self._nonce()
        header = b"\x17\x03\x03" + len(body).to_bytes(2, "big")
        inner = self._aead.open(nonce, header, body)
        self.seq += 1
        if len(inner) > CH_MAX_PLAINTEXT_BYTES + 1:
            raise ChromeClientError("tls: record_overflow: inner plaintext of %d bytes exceeds %d" % (len(inner), CH_MAX_PLAINTEXT_BYTES + 1))
        stripped = inner.rstrip(b"\x00")
        if not stripped:
            raise ChromeClientError("tls: unexpected_message: record carries no content type")
        return stripped[-1], stripped[:-1]


_CH_KEY_SHARE_BYTES = {0x11EC: 1216, 0x001D: 32, 0x0017: 65, 0x0018: 97}


def _ch_vec(width, data):
    """An RFC 8446 section 3.4 vector: `data` behind a `width`-byte big-endian length.

    A body the length field cannot express is refused with
    ChromeClientError("tls: vector of N bytes exceeds a W-byte length"), never
    left to surface as to_bytes' OverflowError.
    """
    data = bytes(data)
    if len(data) >= 1 << (8 * width):
        raise ChromeClientError("tls: vector of %d bytes exceeds a %d-byte length" % (len(data), width))
    return len(data).to_bytes(width, "big") + data


def _ch_draw(rand, n):
    """Exactly n bytes from the injected `rand(n)`; a source that returns any other length is refused."""
    out = bytes(rand(n))
    if len(out) != n:
        raise ChromeClientError("tls: random source returned %d bytes, wanted %d" % (len(out), n))
    return out


def _ch_idna_encode(name):
    """`name.encode("idna")`, refusing the four UTS-46 deviation characters first (R-0056, CWE-176).

    The stdlib codec is IDNA2003 (transitional): it maps U+00DF to "ss" and
    U+03C2, U+200D, U+200C to sigma or nothing, while Chrome (UTS-46
    nontransitional) keeps them -- so the encoded name would be a different
    host than the one Chrome reaches. A name carrying one raises UnicodeError,
    the codec's own refusal, so every caller keeps its existing failure path.
    """
    if any(ord(c) in (0x00DF, 0x03C2, 0x200D, 0x200C) for c in name):
        raise UnicodeError("IDNA deviation character")
    return name.encode("idna")


def _ch_sni_name(host):
    """The server_name host_name bytes for `host`, or None when Chrome sends no SNI.

    An IP literal (IPv4, IPv6, with or without the URL brackets) gets no
    server_name extension at all: RFC 6066 section 3 forbids a literal there,
    and Chrome omits it (tests/files/chrome/154/ip-literal/, 16 non-GREASE
    extensions). Any other host is encoded with the stdlib `idna` codec, so a
    non-ASCII name goes out as its punycode A-label (the R-0017 PoC sent
    latin-1, D13), then lowercased as Chrome's URL canonicaliser does, with
    one trailing dot dropped (RFC 6066: HostName carries no trailing dot).
    Refusals: an empty or non-str host is ChromeClientError("tls: empty host
    name"); a name the codec cannot encode (an empty or over-long label, a
    character nameprep prohibits) is ChromeClientError("tls: host name cannot
    be IDNA-encoded").
    """
    if not isinstance(host, str) or not host:
        raise ChromeClientError("tls: empty host name")
    name = host
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    try:
        ipaddress.ip_address(name)
        return None
    except ValueError:
        pass
    if name.endswith("."):
        name = name[:-1]
    if not name:
        raise ChromeClientError("tls: empty host name")
    try:
        encoded = _ch_idna_encode(name)
    except UnicodeError:
        raise ChromeClientError("tls: host name cannot be IDNA-encoded") from None
    return encoded.lower()


def _ch_grease(rand):
    """The six GREASE values of one ClientHello (RFC 8701), drawn the way BoringSSL draws them.

    One random byte per slot, value (b & 0xF0) | 0x0A repeated in both bytes:
    "cipher" (first cipher suite), "group" (first supported_groups entry AND
    the 1-byte key share, which BoringSSL ties to the same slot), "sigalg"
    (first signature algorithm), "version" (first supported_versions entry),
    "ext_first" (the empty leading extension) and "ext_last" (the trailing
    extension with one zero byte). The two extension values must differ -- a
    ClientHello may not repeat an extension type (RFC 8446 section 4.2) -- so
    a collision is resolved as BoringSSL resolves it, by XOR with 0x1010,
    which maps one GREASE value onto another.
    """
    seed = _ch_draw(rand, 6)
    names = ("cipher", "group", "sigalg", "version", "ext_first", "ext_last")
    grease = {}
    for i in range(6):
        b = (seed[i] & 0xF0) | 0x0A
        grease[names[i]] = (b << 8) | b
    if grease["ext_first"] == grease["ext_last"]:
        grease["ext_last"] ^= 0x1010
    return grease


def _ch_permutation(types, rand):
    """BoringSSL's per-connection extension order: a Fisher-Yates shuffle of `types`.

    BoringSSL draws one 32-bit seed per step and swaps element i with element
    seed % (i + 1), from the last index down. The shuffle covers every
    extension the client could send on the connection -- including `cookie`
    (44), which only a second ClientHello carries -- and each ClientHello
    emits the permuted types it has a body for. That is why the cookie lands at
    a position that varies per connection while the other 17 keep CH1's order
    (tests/files/chrome/154/hrr/, 20 of 20).
    """
    perm = list(types)
    n = len(perm)
    if n < 2:
        return tuple(perm)
    seeds = _ch_draw(rand, 4 * (n - 1))
    for i in range(n - 1, 0, -1):
        j = int.from_bytes(seeds[4 * (i - 1):4 * i], "big") % (i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    return tuple(perm)


def _ch_key_share_entry(group, shares):
    """One KeyShareEntry: group || key_exchange<1..2^16-1> from `shares[group]`.

    A group missing from `shares` is ChromeClientError("tls: no key share for
    group 0x...."); a key whose length is not the group's (_CH_KEY_SHARE_BYTES)
    is ChromeClientError("tls: invalid key share for group 0x....").
    """
    if group not in shares:
        raise ChromeClientError("tls: no key share for group 0x%04x" % group)
    key = bytes(shares[group])
    if len(key) != _CH_KEY_SHARE_BYTES.get(group, -1):
        raise ChromeClientError("tls: invalid key share for group 0x%04x" % group)
    return group.to_bytes(2, "big") + _ch_vec(2, key)


def _ch_hello_extensions(sni, shares, profile, grease, rand):
    """The non-GREASE extension bodies of a first ClientHello: (type -> body, ECH payload length).

    Every list comes from `profile`; the fixed-shape bodies are the RFC's own
    encodings, each identical on every capture in tests/files/chrome/154/:
    status_request (5) 0100000000 (OCSP, empty responder and extension
    lists), ec_point_formats (11) 0100 (uncompressed), psk_key_exchange_modes
    (45) 0101 (psk_dhe_ke), renegotiation_info (65281) 00, and the empty
    signed_certificate_timestamp (18), extended_master_secret (23) and
    session_ticket (35). The key share leads with the GREASE group's 1-byte
    share and then one entry per profile["key_share_groups"]. The ECH GREASE
    (65037) is BoringSSL's shape: outer type 0, the profile's HPKE suite, a
    random config id, a random 32-byte enc and a random payload whose length
    is one of profile["ech_payload_buckets"], drawn uniformly per connection.
    No pre_shared_key (41) and no early_data (42): session resumption is out
    of scope.
    """
    bodies = {}
    if sni is not None:
        bodies[0] = _ch_vec(2, b"\x00" + _ch_vec(2, sni))
    bodies[5] = b"\x01\x00\x00\x00\x00"
    groups = grease["group"].to_bytes(2, "big")
    for g in profile["groups"]:
        groups += g.to_bytes(2, "big")
    bodies[10] = _ch_vec(2, groups)
    bodies[11] = b"\x01\x00"
    sigalgs = grease["sigalg"].to_bytes(2, "big")
    for s in profile["sigalgs"]:
        sigalgs += s.to_bytes(2, "big")
    bodies[13] = _ch_vec(2, sigalgs)
    alpn = b""
    for proto in profile["alpn"]:
        alpn += _ch_vec(1, proto.encode("ascii"))
    bodies[16] = _ch_vec(2, alpn)
    bodies[18] = b""
    bodies[23] = b""
    algs = b""
    for a in profile["cert_compression"]:
        algs += a.to_bytes(2, "big")
    bodies[27] = _ch_vec(1, algs)
    bodies[35] = b""
    versions = grease["version"].to_bytes(2, "big")
    for v in profile["versions"]:
        versions += v.to_bytes(2, "big")
    bodies[43] = _ch_vec(1, versions)
    bodies[45] = b"\x01\x01"
    ks = grease["group"].to_bytes(2, "big") + _ch_vec(2, b"\x00")
    for g in profile["key_share_groups"]:
        ks += _ch_key_share_entry(g, shares)
    bodies[51] = _ch_vec(2, ks)
    alps = b""
    for proto in profile["alps_protocols"]:
        alps += _ch_vec(1, proto.encode("ascii"))
    bodies[17613] = _ch_vec(2, alps)
    bodies[51764] = bytes.fromhex(profile["tai_payload_hex"])
    buckets = profile["ech_payload_buckets"]
    payload_len = buckets[_ch_draw(rand, 1)[0] % len(buckets)]
    kdf, aead = profile["ech_cipher_suite"]
    ech = b"\x00" + kdf.to_bytes(2, "big") + aead.to_bytes(2, "big") + _ch_draw(rand, 1)
    ech += _ch_vec(2, _ch_draw(rand, 32))
    ech += _ch_vec(2, _ch_draw(rand, payload_len))
    bodies[65037] = ech
    bodies[65281] = b"\x00"
    return bodies, payload_len


def _ch_hello_wire(random, session_id, grease, profile, order, bodies, record_version):
    """Assemble a ClientHello: (records, handshake message, wire extension order).

    The extension block is the leading empty GREASE extension, the types of
    `order` with their `bodies`, and the trailing GREASE extension carrying one
    zero byte. The handshake message is fragmented into TLSPlaintext records of
    at most CH_MAX_PLAINTEXT_BYTES (RFC 8446 section 5.1) with the legacy record
    version `record_version`: 0x0301 on the first ClientHello, 0x0303 on the
    second (the raw captures and hrr/ records).
    """
    ext = grease["ext_first"].to_bytes(2, "big") + _ch_vec(2, b"")
    wire_order = [grease["ext_first"]]
    for t in order:
        ext += t.to_bytes(2, "big") + _ch_vec(2, bodies[t])
        wire_order.append(t)
    ext += grease["ext_last"].to_bytes(2, "big") + _ch_vec(2, b"\x00")
    wire_order.append(grease["ext_last"])
    ciphers = grease["cipher"].to_bytes(2, "big")
    for c in profile["ciphers"]:
        ciphers += c.to_bytes(2, "big")
    body = b"\x03\x03" + random + _ch_vec(1, session_id) + _ch_vec(2, ciphers) + b"\x01\x00" + _ch_vec(2, ext)
    hs = b"\x01" + _ch_vec(3, body)
    records = b""
    for start in range(0, len(hs), CH_MAX_PLAINTEXT_BYTES):
        fragment = hs[start:start + CH_MAX_PLAINTEXT_BYTES]
        records += b"\x16" + record_version.to_bytes(2, "big") + _ch_vec(2, fragment)
    return records, hs, wire_order


def _ch_client_hello(host, shares, profile, rand, hrr=None):
    """Chrome's ClientHello for `host`: (record bytes, handshake message bytes, meta).

    `shares` maps a group to its key_exchange bytes: for a first ClientHello
    every group of profile["key_share_groups"] (X25519MLKEM768 then X25519),
    for a second one the group the HelloRetryRequest selected. `profile` is
    CHROME_PROFILE (None means it); every cipher, group, sigalg, version,
    ALPN and ALPS protocol, certificate compression algorithm, ECH suite and
    bucket and the 51764 payload come from it. `rand(n) -> bytes` supplies
    every random value (None means os.urandom; tests inject a deterministic
    source): GREASE, random, legacy session id, the extension permutation and
    the ECH GREASE fields. The handshake message is what the transcript
    hashes; the records are what goes on the wire.

    `hrr=None` builds the first ClientHello. Its meta holds "ech_payload_len",
    "ext_order" (the wire order, GREASE values included), "hs_len",
    "record_version", "sni" (bytes, or None for an IP literal), "hrr" (False)
    and the state a second ClientHello reuses: "random", "session_id",
    "grease", "permutation" (the BoringSSL order, cookie included) and
    "ext_bodies".

    `hrr={"group": g, "cookie": <bytes or None>, "ch1_meta": <first meta>}`
    builds the second ClientHello in the shape of tests/files/chrome/154/hrr/
    (20 of 20): the first one's random, session id, cipher list, GREASE values
    and extension order; every extension byte-identical except key_share,
    which holds ONE entry for g (no GREASE share); the ECH GREASE unchanged;
    and the cookie echoed as extension 44 at its place in the permutation
    (omitted when the HelloRetryRequest carried none). g must be in
    profile["hrr_groups"]. Its meta adds "cookie_len" and sets "hrr" True.
    The compat ChangeCipherSpec Chrome sends before it
    (profile["hrr_compat_ccs"]) is a record-layer fact the engine writes; it
    is not part of these bytes.

    Refusals, each ChromeClientError("tls: ..."): the host refusals of
    _ch_sni_name, a missing or wrong-length key share, a random source that
    returns the wrong length, a profile whose extension_types the builder has
    no body for (or a body the profile does not list), a malformed hrr
    argument, a second ClientHello built from a second one ("tls: second
    HelloRetryRequest") or for another host, a group outside hrr_groups and
    an empty cookie.
    """
    if profile is None:
        profile = CHROME_PROFILE
    if rand is None:
        rand = os.urandom
    sni = _ch_sni_name(host)
    if hrr is None:
        grease = _ch_grease(rand)
        random = _ch_draw(rand, 32)
        session_id = _ch_draw(rand, 32)
        bodies, payload_len = _ch_hello_extensions(sni, shares, profile, grease, rand)
        listed = sorted(t for t in profile["extension_types"] if t != 0 or sni is not None)
        if sorted(bodies) != listed:
            raise ChromeClientError("tls: profile extension_types do not match the ClientHello builder")
        permutation = _ch_permutation(tuple(profile["extension_types"]) + (44,), rand)
        order = [t for t in permutation if t in bodies]
        records, hs, wire_order = _ch_hello_wire(random, session_id, grease, profile, order, bodies, 0x0301)
        meta = {}
        meta["ech_payload_len"] = payload_len
        meta["ext_order"] = wire_order
        meta["hs_len"] = len(hs)
        meta["record_version"] = 0x0301
        meta["sni"] = sni
        meta["hrr"] = False
        meta["random"] = random
        meta["session_id"] = session_id
        meta["grease"] = grease
        meta["permutation"] = permutation
        meta["ext_bodies"] = bodies
        return records, hs, meta
    if not isinstance(hrr, dict) or not isinstance(hrr.get("ch1_meta"), dict) or not isinstance(hrr.get("group"), int):
        raise ChromeClientError("tls: invalid HelloRetryRequest state")
    ch1 = hrr["ch1_meta"]
    if ch1.get("hrr") is not False:
        raise ChromeClientError("tls: second HelloRetryRequest")
    if sni != ch1.get("sni"):
        raise ChromeClientError("tls: second ClientHello for a different host")
    group = hrr["group"]
    if group not in profile["hrr_groups"]:
        raise ChromeClientError("tls: HelloRetryRequest selected unsupported group 0x%04x" % group)
    bodies = dict(ch1["ext_bodies"])
    bodies[51] = _ch_vec(2, _ch_key_share_entry(group, shares))
    cookie = hrr.get("cookie")
    cookie_len = 0
    if cookie is not None:
        cookie = bytes(cookie)
        if not cookie:
            raise ChromeClientError("tls: empty HelloRetryRequest cookie")
        bodies[44] = _ch_vec(2, cookie)
        cookie_len = len(cookie)
    order = [t for t in ch1["permutation"] if t in bodies]
    records, hs, wire_order = _ch_hello_wire(ch1["random"], ch1["session_id"], ch1["grease"], profile, order, bodies, 0x0303)
    meta = {}
    meta["ech_payload_len"] = ch1["ech_payload_len"]
    meta["ext_order"] = wire_order
    meta["hs_len"] = len(hs)
    meta["record_version"] = 0x0303
    meta["sni"] = sni
    meta["hrr"] = True
    meta["random"] = ch1["random"]
    meta["session_id"] = ch1["session_id"]
    meta["grease"] = ch1["grease"]
    meta["permutation"] = ch1["permutation"]
    meta["ext_bodies"] = bodies
    meta["cookie_len"] = cookie_len
    return records, hs, meta


_CH_ALERT_NAMES = {0: "close_notify", 10: "unexpected_message", 20: "bad_record_mac", 22: "record_overflow", 40: "handshake_failure", 42: "bad_certificate", 43: "unsupported_certificate", 44: "certificate_revoked", 45: "certificate_expired", 46: "certificate_unknown", 47: "illegal_parameter", 48: "unknown_ca", 49: "access_denied", 50: "decode_error", 51: "decrypt_error", 70: "protocol_version", 71: "insufficient_security", 80: "internal_error", 86: "inappropriate_fallback", 90: "user_canceled", 109: "missing_extension", 110: "unsupported_extension", 112: "unrecognized_name", 113: "bad_certificate_status_response", 115: "unknown_psk_identity", 116: "certificate_required", 120: "no_application_protocol", 121: "ech_required"}


def _ch_alert_name(description):
    """The RFC name of an alert description; an unregistered one is "alert_<n>"."""
    return _CH_ALERT_NAMES.get(description, "alert_%d" % description)


_CH_HRR_RANDOM = hashlib.sha256(b"HelloRetryRequest").digest()


_CH_TLS13_SUITES = {0x1301: (hashlib.sha256, 16, _ChAesGcm), 0x1302: (hashlib.sha384, 32, _ChAesGcm), 0x1303: (hashlib.sha256, 32, _ChChaCha20Poly1305)}


_CH_SERVER_SHARE_BYTES = {0x11EC: 1120, 0x001D: 32, 0x0017: 65}


_CH_EE_FORBIDDEN = (5, 11, 13, 18, 23, 27, 35, 41, 43, 44, 45, 51, 65281)


def _ch_parse_server_hello(msg):
    """Parse a ServerHello or HelloRetryRequest message (header included) with bounds.

    Returns a dict: "legacy_version", "random", "session_id", "cipher",
    "compression", "ext" (type -> body), "is_hrr" (the random equals
    _CH_HRR_RANDOM). A TLS 1.2 ServerHello may end after the compression
    method (its extension block is optional), so an absent block is an empty
    dict here and the version check decides. Refusals: every short field is
    "tls: truncated <field>", bytes after the extension block "tls: trailing
    bytes after ServerHello", a session id over 32 bytes illegal_parameter
    and an extension type repeated decode_error (RFC 8446 section 4.2). No
    field is interpreted here; the engine checks each against its offer.
    """
    r = _ChReader(bytes(msg)[4:])
    sh = {}
    sh["legacy_version"] = r.u16("ServerHello legacy_version")
    sh["random"] = r.bytes(32, "ServerHello random")
    sh["session_id"] = r.vec8("ServerHello legacy_session_id_echo")
    if len(sh["session_id"]) > 32:
        raise ChromeClientError("tls: illegal_parameter: ServerHello session id of %d bytes" % len(sh["session_id"]))
    sh["cipher"] = r.u16("ServerHello cipher_suite")
    sh["compression"] = r.u8("ServerHello legacy_compression_method")
    exts = {}
    if r.remaining():
        block = r.sub16("ServerHello extensions")
        r.end("ServerHello")
        while block.remaining():
            etype = block.u16("ServerHello extension type")
            body = block.vec16("ServerHello extension")
            if etype in exts:
                raise ChromeClientError("tls: decode_error: ServerHello repeats extension %d" % etype)
            exts[etype] = body
    sh["ext"] = exts
    sh["is_hrr"] = sh["random"] == _CH_HRR_RANDOM
    return sh


def _ch_key_share_new(group, rand):
    """A fresh client key share for `group`: (key_exchange bytes, private state).

    0x11EC X25519MLKEM768: the 1184-byte ML-KEM-768 encapsulation key followed
    by a 32-byte X25519 public key of its OWN (not the standalone X25519
    share's), private state (kem, dk, x25519 scalar). 0x001D X25519: 32 bytes,
    the scalar. 0x0017 P-256: the 65-byte uncompressed point, the scalar int.
    Every random byte comes from `rand`. Any other group is
    ChromeClientError("tls: internal_error: no key generation for group ...").
    """
    if group == 0x11EC:
        kem = _ChMlKem768()
        ek, dk = kem.keygen(rand)
        xpriv, xpub = _ch_x25519_keypair(rand)
        return ek + xpub, (kem, dk, xpriv)
    if group == 0x001D:
        priv, pub = _ch_x25519_keypair(rand)
        return pub, priv
    if group == 0x0017:
        d, pub = _ch_p256_keypair(rand)
        return pub, d
    raise ChromeClientError("tls: internal_error: no key generation for group 0x%04x" % group)


def _ch_key_share_secret(group, private, server_share):
    """The (EC)DHE shared secret for `group` from our private state and the server's share.

    X25519MLKEM768 is the 32-byte ML-KEM-768 secret (decapsulating the first
    1088 bytes) followed by the 32-byte X25519 secret over the last 32
    (draft-ietf-tls-ecdhe-mlkem; the order the R-0017 PoC completed live
    handshakes with). The share length is checked against
    _CH_SERVER_SHARE_BYTES first, and every failure of the primitives -- an
    all-zero X25519 result, an off-curve P-256 point -- is re-raised as
    ChromeClientError("tls: illegal_parameter: ..."), the alert RFC 8446
    section 4.2.8 assigns to an invalid share.
    """
    share = bytes(server_share)
    want = _CH_SERVER_SHARE_BYTES.get(group, -1)
    if len(share) != want:
        raise ChromeClientError("tls: illegal_parameter: server key share for group 0x%04x is %d bytes, expected %d" % (group, len(share), want))
    try:
        if group == 0x11EC:
            kem, dk, xpriv = private
            return kem.decaps(dk, share[:1088]) + _ch_x25519(xpriv, share[1088:])
        if group == 0x001D:
            return _ch_x25519(private, share)
        return _ch_p256_shared(private, share)
    except ChromeClientError as exc:
        text = str(exc)
        if text.startswith("tls: "):
            text = text[5:]
        raise ChromeClientError("tls: illegal_parameter: %s" % text) from None


def _ch_traffic_cipher(aead_cls, hashmod, key_len, secret):
    """A _ChRecordCipher for one traffic secret (RFC 8446 section 7.3): key and iv by HKDF-Expand-Label."""
    key = _ch_hkdf_expand_label(hashmod, secret, b"key", b"", key_len)
    iv = _ch_hkdf_expand_label(hashmod, secret, b"iv", b"", 12)
    return _ChRecordCipher(aead_cls(key), iv)


CH_MAX_KEY_UPDATES = 32


class _ChTls:
    """The sans-IO TLS 1.3 client engine: bytes in, bytes out, no socket.

    `_ChTls(host, profile=None, rand=None, log=None)`. `profile` is
    CHROME_PROFILE when None; `rand(n) -> bytes` supplies every random byte
    (key shares, GREASE, randoms; os.urandom when None); `log(str)` receives
    ONE structure-only line per completed handshake
    (`tls: group=0x11ec cipher=0x1301 alpn=h2 alps=0 hrr=0 ch_len=1805
    t_hs=41ms`) and nothing else -- never the host, never a byte of payload.

    The API a transport drives. `start() -> bytes` generates the key shares
    (X25519MLKEM768 and X25519), builds Chrome's first ClientHello and
    returns its records. `feed(data) -> bytes` takes what the socket
    returned and gives back what must be written (CCS + second ClientHello
    after a HelloRetryRequest; CCS + client Finished once the server flight
    verifies; b"" otherwise); decrypted application data accumulates in
    `app_data` (a bytearray; `read_app()` drains it). `send_app(data) ->
    bytes` seals application data into records of at most
    CH_MAX_PLAINTEXT_BYTES, refused before the handshake completes and
    after `close_notify()`, which returns the sealed close_notify alert
    once. Attributes: `handshake_done`, `alpn` (str, or None when the
    server picked none), `group`, `cipher`, `alps_negotiated`,
    `alps_server_settings` (the raw EE payload), `hrr`, `app_data`,
    `closed` (the server's close_notify arrived; later bytes are ignored),
    `failed` (a refusal was raised; every later call is refused), `ch_len`,
    `t_hs_ms`.

    What it checks (every refusal is ChromeClientError("tls: <alert>: ...")
    naming the RFC 8446 alert, except the three wordings the plan fixes).
    ServerHello: `supported_versions` must be 0x0304 -- a TLS 1.2 answer is
    ChromeTls12Error("tls: server chose TLS 1.2") before any key is derived;
    the session id echo, compression 0, a cipher we offered, only
    supported_versions and key_share present, a key share for a group we
    sent one for, of exactly _CH_SERVER_SHARE_BYTES. HelloRetryRequest
    (the random is _CH_HRR_RANDOM): the same version and cipher checks; a
    group we already sent a share for, or one not in our supported_groups
    (a GREASE value included), is illegal_parameter; secp384r1 is "tls:
    HelloRetryRequest selected secp384r1 (not implemented)" (P-256 only,
    FLAG-2), preceded by a WARNING on the stdlib `chrome-client` logger that
    names the host and roadmap R-0048; a second one is "tls: second HelloRetryRequest"; the transcript
    restarts as message_hash(Hash(CH1)) || HRR (section 4.4.1), a compat
    ChangeCipherSpec precedes CH2 (profile["hrr_compat_ccs"], hrr/ 20/20)
    and the ServerHello that follows must repeat the HRR's cipher. Server
    flight, in order: EncryptedExtensions (only offered extensions, none of
    _CH_EE_FORBIDDEN; ALPN must be one protocol we offered; ALPS 17613
    only with an ALPN protocol we offered ALPS for), Certificate or
    CompressedCertificate (25, kept UNDECODED), CertificateVerify, Finished
    -- compared with hmac.compare_digest, "tls: decrypt_error: ..." on a
    mismatch. The chain and the signature are parsed with bounds and hashed
    into the transcript but NOT verified (module docstring). A
    CertificateRequest is refused (client certificates are out of scope). A
    handshake message may not span a key change. A server ChangeCipherSpec
    (one byte 0x01, unprotected) is dropped until the server Finished, per
    appendix D.4; anything else of type 20 is unexpected_message. A fatal
    alert is "tls: <name>: alert received from the server".

    Compat ChangeCipherSpec: Chrome sends ONE, immediately before its second
    flight -- before CH2 after an HRR (measured, hrr/), otherwise before the
    client Finished (BoringSSL's tls13_client.cc shape; that position is not
    in any capture: UNVERIFIED).

    Client ALPS: when the server's EncryptedExtensions negotiated ALPS
    (17613), `_client_flight()` answers with the client's own
    EncryptedExtensions -- handshake type 8 holding one extension 17613
    whose body is profile["alps_h2_settings_hex"] verbatim (empty for h2,
    so the message is 08 000006 0004 44cd 0000) -- sealed under the client
    handshake key after the compat CCS and BEFORE the client Finished, and
    hashed into the transcript the Finished covers (BoringSSL's
    do_send_client_encrypted_extensions shape; source-verified, see the
    profile comment). ALPS for a protocol other than h2 is refused: the
    profile carries an ALPS value for h2 only.

    Post-handshake (`_on_post_handshake`): NewSessionTicket is dropped
    (resumption is out of scope). KeyUpdate (RFC 8446 section 4.6.3) must be
    one byte, 0 or 1 (else decode_error / illegal_parameter), and must end
    its record (a message after it in the same record would span the key
    change: unexpected_message); it moves the READ side to the next server
    application traffic secret ("traffic upd"), and on update_requested
    returns our own KeyUpdate(update_not_requested) sealed under the
    CURRENT write key, then moves the write side -- unless close_notify was
    already sent, after which nothing is written. More than
    CH_MAX_KEY_UPDATES in a row without server application data is
    unexpected_message. Any other post-handshake type is "tls:
    unexpected_message: post-handshake message type <n>".

    Record-layer refusals beyond the ceilings: an unprotected alert once the
    read side is protected (after the ServerHello) is "tls:
    unexpected_message: unprotected alert after the key change" -- RFC 8446
    section 5 has every alert after it encrypted. Records that make no
    progress -- a dropped ChangeCipherSpec, a user_canceled alert, an empty
    application data record, a dropped NewSessionTicket -- are counted in a
    row; a record carrying application data resets the count, and the one
    past MAX_EMPTY_RECORDS (BoringSSL's kMaxEmptyRecords, 32) is "tls:
    unexpected_message: too many empty records (more than 32 without
    application data)". A class attribute, not a CH_ limit, so a host's
    generated region needs no new block name.
    """

    CCS_RECORD = b"\x14\x03\x03\x00\x01\x01"
    MAX_EMPTY_RECORDS = 32

    def __init__(self, host, profile=None, rand=None, log=None):
        if profile is None:
            profile = CHROME_PROFILE
        if rand is None:
            rand = os.urandom
        self._host = host
        self._profile = profile
        self._rand = rand
        self._log = log
        self.handshake_done = False
        self.alpn = None
        self.group = None
        self.cipher = None
        self.alps_negotiated = False
        self.alps_server_settings = None
        self.hrr = False
        self.app_data = bytearray()
        self.closed = False
        self.failed = False
        self.ch_len = 0
        self.t_hs_ms = None
        self._state = "new"
        self._records = _ChRecordReader()
        self._hs = _ChHandshakeReader()
        self._t0 = None
        self._ch1 = None
        self._ch1_meta = None
        self._offer = None
        self._keys = {}
        self._hrr_cipher = None
        self._hashmod = None
        self._key_len = 0
        self._aead_cls = None
        self._th = None
        self._hs_secret = None
        self._c_hs = None
        self._s_hs = None
        self._c_ap = None
        self._s_ap = None
        self._read = None
        self._write = None
        self._ccs_sent = False
        self._close_sent = False
        self._key_updates = 0
        self._empty_records = 0

    def start(self):
        """Generate the key shares and return the first ClientHello's records."""
        if self._state != "new":
            raise ChromeClientError("tls: internal_error: handshake already started")
        self._t0 = time.monotonic()
        profile = self._profile
        shares = {}
        keys = {}
        for group in profile["key_share_groups"]:
            pub, priv = _ch_key_share_new(group, self._rand)
            shares[group] = pub
            keys[group] = priv
        records, hs, meta = _ch_client_hello(self._host, shares, profile, self._rand)
        offer = {}
        offer["session_id"] = meta["session_id"]
        offer["ciphers"] = tuple(profile["ciphers"])
        offer["groups"] = tuple(profile["groups"])
        offer["sigalgs"] = tuple(profile["sigalgs"])
        offer["ext_types"] = frozenset(meta["ext_bodies"])
        offer["alpn"] = tuple(profile["alpn"])
        offer["alps"] = tuple(profile["alps_protocols"])
        offer["cert_compression"] = tuple(profile["cert_compression"])
        self._begin(hs, meta, keys, offer)
        return records

    def _begin(self, ch_hs, meta, keys, offer):
        """Enter the wait-for-ServerHello state for a ClientHello already built.

        Split from start() so a known-answer test can substitute a published
        ClientHello and its private key (RFC 8448 section 3) and drive the
        rest of the engine unchanged. `offer` holds what the ClientHello
        offered: session_id, ciphers, groups, sigalgs, ext_types, alpn,
        alps, cert_compression.
        """
        if self._t0 is None:
            self._t0 = time.monotonic()
        self._ch1 = bytes(ch_hs)
        self._ch1_meta = meta
        self._keys = dict(keys)
        self._offer = offer
        self.ch_len = len(self._ch1)
        self._state = "wait_sh"

    def feed(self, data):
        """Consume bytes from the transport; return the bytes to write back."""
        if self.failed:
            raise ChromeClientError("tls: internal_error: the connection has already failed")
        if self._state == "new":
            raise ChromeClientError("tls: internal_error: feed before start")
        if self.closed:
            return b""
        try:
            self._records.feed(data)
            out = b""
            while not self.closed:
                rec = self._records.next_record()
                if rec is None:
                    break
                out += self._on_record(rec[0], rec[1])
            return out
        except ChromeClientError:
            self.failed = True
            raise

    def send_app(self, data):
        """Seal application data into records of at most CH_MAX_PLAINTEXT_BYTES."""
        if self.failed:
            raise ChromeClientError("tls: internal_error: the connection has already failed")
        if not self.handshake_done:
            raise ChromeClientError("tls: internal_error: application data before the handshake completed")
        if self._close_sent:
            raise ChromeClientError("tls: internal_error: application data after close_notify")
        data = bytes(data)
        out = b""
        for start in range(0, len(data), CH_MAX_PLAINTEXT_BYTES):
            out += self._write.seal(23, data[start:start + CH_MAX_PLAINTEXT_BYTES])
        return out

    def read_app(self):
        """Return and clear the application data decrypted so far."""
        data = bytes(self.app_data)
        del self.app_data[:]
        return data

    def close_notify(self):
        """The sealed close_notify alert (RFC 8446 section 6.1), once; b"" before the handshake completes."""
        if not self.handshake_done or self._close_sent or self.failed:
            return b""
        self._close_sent = True
        return self._write.seal(21, b"\x01\x00")

    def _on_record(self, ctype, body):
        """Route one record: CCS, alert, plaintext handshake, or a protected record."""
        if ctype == 20:
            if self._state in ("wait_sh", "wait_ee", "wait_cert", "wait_cv", "wait_fin") and body == b"\x01":
                self._no_progress()
                return b""
            raise ChromeClientError("tls: unexpected_message: ChangeCipherSpec record outside the handshake")
        if ctype == 21:
            if self._read is not None:
                raise ChromeClientError("tls: unexpected_message: unprotected alert after the key change")
            self._on_alert(body)
            return b""
        if self._read is None:
            if ctype != 22:
                raise ChromeClientError("tls: unexpected_message: record type %d before ServerHello" % ctype)
            if not body:
                raise ChromeClientError("tls: unexpected_message: empty handshake record")
            self._hs.feed(body)
            return self._drain_handshake()
        if ctype != 23:
            raise ChromeClientError("tls: unexpected_message: unprotected record type %d after the key change" % ctype)
        inner_type, content = self._read.open(23, body)
        if inner_type == 22:
            if not content:
                raise ChromeClientError("tls: unexpected_message: empty handshake record")
            self._hs.feed(content)
            return self._drain_handshake()
        if inner_type == 21:
            self._on_alert(content)
            return b""
        if inner_type == 23:
            if not self.handshake_done:
                raise ChromeClientError("tls: unexpected_message: application data before the server Finished")
            if content:
                self._key_updates = 0
                self._empty_records = 0
            else:
                self._no_progress()
            self.app_data += content
            return b""
        raise ChromeClientError("tls: unexpected_message: protected record of inner type %d" % inner_type)

    def _no_progress(self):
        """One more record that carried no application data; the one past MAX_EMPTY_RECORDS in a row is refused."""
        self._empty_records += 1
        if self._empty_records > self.MAX_EMPTY_RECORDS:
            raise ChromeClientError("tls: unexpected_message: too many empty records (more than %d without application data)" % self.MAX_EMPTY_RECORDS)

    def _drain_handshake(self):
        """Process every complete handshake message buffered; return the bytes they produce."""
        out = b""
        while not self.closed:
            item = self._hs.next_message()
            if item is None:
                break
            out += self._on_handshake(item[0], item[1])
        return out

    def _require_no_pending(self, what):
        """Refuse handshake bytes left over when the keys change (RFC 8446 section 5.1)."""
        if self._hs.pending():
            raise ChromeClientError("tls: unexpected_message: handshake data after the %s spans a key change" % what)

    def _on_handshake(self, msg_type, msg):
        """Dispatch one handshake message by state; anything out of order is unexpected_message."""
        state = self._state
        if state == "wait_sh":
            if msg_type != 2:
                raise ChromeClientError("tls: unexpected_message: expected ServerHello, got handshake type %d" % msg_type)
            return self._on_server_hello(msg)
        if state == "wait_ee":
            if msg_type != 8:
                raise ChromeClientError("tls: unexpected_message: expected EncryptedExtensions, got handshake type %d" % msg_type)
            return self._on_ee(msg)
        if state == "wait_cert":
            if msg_type == 13:
                raise ChromeClientError("tls: handshake_failure: server requested a client certificate (not supported)")
            if msg_type not in (11, 25):
                raise ChromeClientError("tls: unexpected_message: expected Certificate, got handshake type %d" % msg_type)
            return self._on_certificate(msg_type, msg)
        if state == "wait_cv":
            if msg_type != 15:
                raise ChromeClientError("tls: unexpected_message: expected CertificateVerify, got handshake type %d" % msg_type)
            return self._on_certificate_verify(msg)
        if state == "wait_fin":
            if msg_type != 20:
                raise ChromeClientError("tls: unexpected_message: expected Finished, got handshake type %d" % msg_type)
            return self._on_finished(msg)
        return self._on_post_handshake(msg_type, msg)

    def _on_alert(self, body):
        """Decode an alert by name: close_notify closes, user_canceled is ignored, anything else is fatal."""
        body = bytes(body)
        if len(body) != 2:
            raise ChromeClientError("tls: decode_error: alert of %d bytes" % len(body))
        description = body[1]
        if description == 0:
            self.closed = True
            if not self.handshake_done:
                raise ChromeClientError("tls: close_notify: server closed the connection during the handshake")
            return
        if description == 90:
            self._no_progress()
            return
        raise ChromeClientError("tls: %s: alert received from the server" % _ch_alert_name(description))

    def _set_suite(self, cipher):
        """Adopt the negotiated suite's hash, key length and AEAD class."""
        self._hashmod, self._key_len, self._aead_cls = _CH_TLS13_SUITES[cipher]

    def _check_hello(self, sh, what):
        """The checks a ServerHello and a HelloRetryRequest share: version, cipher, session id, compression."""
        sv = sh["ext"].get(43)
        if sv is None:
            if sh["legacy_version"] == 0x0303:
                raise ChromeTls12Error("tls: server chose TLS 1.2")
            raise ChromeClientError("tls: protocol_version: server chose legacy version 0x%04x" % sh["legacy_version"])
        r = _ChReader(sv)
        version = r.u16("supported_versions")
        r.end("supported_versions")
        if version == 0x0303:
            raise ChromeTls12Error("tls: server chose TLS 1.2")
        if version != 0x0304:
            raise ChromeClientError("tls: illegal_parameter: %s selected version 0x%04x" % (what, version))
        if sh["legacy_version"] != 0x0303:
            raise ChromeClientError("tls: illegal_parameter: %s legacy_version 0x%04x" % (what, sh["legacy_version"]))
        if sh["session_id"] != self._offer["session_id"]:
            raise ChromeClientError("tls: illegal_parameter: %s does not echo our session id" % what)
        if sh["compression"] != 0:
            raise ChromeClientError("tls: illegal_parameter: %s compression method %d" % (what, sh["compression"]))
        cipher = sh["cipher"]
        if cipher not in _CH_TLS13_SUITES or cipher not in self._offer["ciphers"]:
            raise ChromeClientError("tls: illegal_parameter: %s selected cipher 0x%04x we did not offer" % (what, cipher))

    def _on_server_hello(self, msg):
        """ServerHello (or HelloRetryRequest): validate, derive the handshake keys."""
        sh = _ch_parse_server_hello(msg)
        if sh["is_hrr"]:
            return self._on_hrr(sh, msg)
        self._check_hello(sh, "ServerHello")
        cipher = sh["cipher"]
        if self._hrr_cipher is not None and cipher != self._hrr_cipher:
            raise ChromeClientError("tls: illegal_parameter: ServerHello cipher 0x%04x differs from the HelloRetryRequest's 0x%04x" % (cipher, self._hrr_cipher))
        for etype in sh["ext"]:
            if etype not in (43, 51):
                raise ChromeClientError("tls: unsupported_extension: ServerHello carries extension %d" % etype)
        if 51 not in sh["ext"]:
            raise ChromeClientError("tls: missing_extension: ServerHello has no key_share")
        r = _ChReader(sh["ext"][51])
        group = r.u16("ServerHello key_share group")
        share = r.vec16("ServerHello key_share key_exchange")
        r.end("ServerHello key_share")
        if group not in self._keys:
            raise ChromeClientError("tls: illegal_parameter: ServerHello selected group 0x%04x we sent no key share for" % group)
        want = _CH_SERVER_SHARE_BYTES.get(group, -1)
        if len(share) != want:
            raise ChromeClientError("tls: illegal_parameter: server key share for group 0x%04x is %d bytes, expected %d" % (group, len(share), want))
        if self._th is None:
            self._set_suite(cipher)
            self._th = self._hashmod(self._ch1)
        self._th.update(msg)
        ecdhe = _ch_key_share_secret(group, self._keys[group], share)
        self._keys = {}
        hashmod = self._hashmod
        hlen = hashmod().digest_size
        early = _ch_hkdf_extract(hashmod, None, bytes(hlen))
        derived = _ch_derive_secret(hashmod, early, b"derived", hashmod(b"").digest())
        self._hs_secret = _ch_hkdf_extract(hashmod, derived, ecdhe)
        th = self._th.copy().digest()
        self._c_hs = _ch_derive_secret(hashmod, self._hs_secret, b"c hs traffic", th)
        self._s_hs = _ch_derive_secret(hashmod, self._hs_secret, b"s hs traffic", th)
        self._require_no_pending("ServerHello")
        self._read = _ch_traffic_cipher(self._aead_cls, hashmod, self._key_len, self._s_hs)
        self._write = _ch_traffic_cipher(self._aead_cls, hashmod, self._key_len, self._c_hs)
        self.group = group
        self.cipher = cipher
        self._state = "wait_ee"
        return b""

    def _on_hrr(self, sh, msg):
        """HelloRetryRequest: validate, restart the transcript, answer with CCS + CH2 for P-256."""
        if self.hrr:
            raise ChromeClientError("tls: second HelloRetryRequest")
        self._check_hello(sh, "HelloRetryRequest")
        for etype in sh["ext"]:
            if etype not in (43, 44, 51):
                raise ChromeClientError("tls: unsupported_extension: HelloRetryRequest carries extension %d" % etype)
        if 51 not in sh["ext"]:
            raise ChromeClientError("tls: illegal_parameter: HelloRetryRequest without key_share (a cookie-only retry is not implemented)")
        r = _ChReader(sh["ext"][51])
        group = r.u16("HelloRetryRequest selected_group")
        r.end("HelloRetryRequest key_share")
        if group in self._keys:
            raise ChromeClientError("tls: illegal_parameter: HelloRetryRequest selected group 0x%04x we already sent a key share for" % group)
        if group not in self._offer["groups"]:
            raise ChromeClientError("tls: illegal_parameter: HelloRetryRequest selected group 0x%04x we did not offer" % group)
        if group not in self._profile["hrr_groups"]:
            if group == 0x0018:
                logging.getLogger("chrome-client").warning("tls: a real server (%s) sent a HelloRetryRequest selecting secp384r1 (P-384), which is not implemented -- see roadmap R-0048", self._host)
                raise ChromeClientError("tls: HelloRetryRequest selected secp384r1 (not implemented)")
            raise ChromeClientError("tls: handshake_failure: HelloRetryRequest selected group 0x%04x (not implemented)" % group)
        cookie = None
        if 44 in sh["ext"]:
            r = _ChReader(sh["ext"][44])
            cookie = r.vec16("HelloRetryRequest cookie")
            r.end("HelloRetryRequest cookie")
            if not cookie:
                raise ChromeClientError("tls: decode_error: HelloRetryRequest carries an empty cookie")
        self._require_no_pending("HelloRetryRequest")
        cipher = sh["cipher"]
        self.hrr = True
        self._hrr_cipher = cipher
        self._set_suite(cipher)
        ch1_hash = self._hashmod(self._ch1).digest()
        self._th = self._hashmod(b"\xfe\x00\x00" + bytes([len(ch1_hash)]) + ch1_hash)
        self._th.update(msg)
        pub, priv = _ch_key_share_new(group, self._rand)
        hrr = {"group": group, "cookie": cookie, "ch1_meta": self._ch1_meta}
        records, hs, meta = _ch_client_hello(self._host, {group: pub}, self._profile, self._rand, hrr)
        self._keys = {group: priv}
        self._th.update(hs)
        out = b""
        if self._profile["hrr_compat_ccs"] and not self._ccs_sent:
            out += self.CCS_RECORD
            self._ccs_sent = True
        return out + records

    def _on_ee(self, msg):
        """EncryptedExtensions: only offered, EE-legal extensions; read ALPN and ALPS."""
        r = _ChReader(bytes(msg)[4:])
        block = r.sub16("EncryptedExtensions extensions")
        r.end("EncryptedExtensions")
        seen = set()
        alps = None
        while block.remaining():
            etype = block.u16("EncryptedExtensions extension type")
            body = block.vec16("EncryptedExtensions extension")
            if etype in seen:
                raise ChromeClientError("tls: decode_error: EncryptedExtensions repeats extension %d" % etype)
            seen.add(etype)
            if etype not in self._offer["ext_types"]:
                raise ChromeClientError("tls: unsupported_extension: EncryptedExtensions carries extension %d we did not offer" % etype)
            if etype in _CH_EE_FORBIDDEN:
                raise ChromeClientError("tls: illegal_parameter: extension %d is not allowed in EncryptedExtensions" % etype)
            if etype == 0 and body:
                raise ChromeClientError("tls: decode_error: EncryptedExtensions server_name is not empty")
            if etype == 16:
                outer = _ChReader(body)
                names = outer.sub16("ALPN protocol_name_list")
                outer.end("ALPN extension")
                name = names.vec8("ALPN protocol_name")
                names.end("ALPN protocol_name_list")
                proto = name.decode("latin-1")
                if proto not in self._offer["alpn"]:
                    raise ChromeClientError("tls: illegal_parameter: server selected an ALPN protocol we did not offer")
                self.alpn = proto
            if etype == 17613:
                alps = body
        if alps is not None:
            if self.alpn is None or self.alpn not in self._offer["alps"]:
                raise ChromeClientError("tls: illegal_parameter: ALPS without an ALPN protocol we offered it for")
            self.alps_negotiated = True
            self.alps_server_settings = alps
        self._th.update(msg)
        self._state = "wait_cert"
        return b""

    def _on_certificate(self, msg_type, msg):
        """Certificate (11) or CompressedCertificate (25): parsed with bounds, hashed, NOT verified."""
        r = _ChReader(bytes(msg)[4:])
        if msg_type == 11:
            context = r.vec8("Certificate request context")
            chain = _ChReader(r.vec24("Certificate certificate_list"))
            r.end("Certificate")
            if context:
                raise ChromeClientError("tls: illegal_parameter: server Certificate carries a request context")
            count = 0
            while chain.remaining():
                if not chain.vec24("Certificate cert_data"):
                    raise ChromeClientError("tls: decode_error: empty certificate in the server chain")
                chain.vec16("CertificateEntry extensions")
                count += 1
            if count == 0:
                raise ChromeClientError("tls: decode_error: server sent an empty certificate chain")
        else:
            algorithm = r.u16("CompressedCertificate algorithm")
            length = r.u24("CompressedCertificate uncompressed_length")
            data = r.vec24("CompressedCertificate compressed_certificate_message")
            r.end("CompressedCertificate")
            if algorithm not in self._offer["cert_compression"]:
                raise ChromeClientError("tls: illegal_parameter: CompressedCertificate algorithm %d we did not offer" % algorithm)
            if length == 0 or not data:
                raise ChromeClientError("tls: bad_certificate: empty CompressedCertificate")
        self._th.update(msg)
        self._state = "wait_cv"
        return b""

    def _on_certificate_verify(self, msg):
        """CertificateVerify: an offered signature scheme and a non-empty signature, NOT verified."""
        r = _ChReader(bytes(msg)[4:])
        scheme = r.u16("CertificateVerify algorithm")
        signature = r.vec16("CertificateVerify signature")
        r.end("CertificateVerify")
        if scheme not in self._offer["sigalgs"]:
            raise ChromeClientError("tls: illegal_parameter: CertificateVerify scheme 0x%04x we did not offer" % scheme)
        if not signature:
            raise ChromeClientError("tls: decode_error: empty CertificateVerify signature")
        self._th.update(msg)
        self._state = "wait_fin"
        return b""

    def _on_finished(self, msg):
        """Verify the server Finished, derive the application keys, send CCS + client flight + Finished."""
        hashmod = self._hashmod
        hlen = hashmod().digest_size
        verify_data = bytes(msg)[4:]
        if len(verify_data) != hlen:
            raise ChromeClientError("tls: decode_error: server Finished of %d bytes, expected %d" % (len(verify_data), hlen))
        s_key = _ch_hkdf_expand_label(hashmod, self._s_hs, b"finished", b"", hlen)
        expected = hmac.new(s_key, self._th.copy().digest(), hashmod).digest()
        if not hmac.compare_digest(expected, verify_data):
            raise ChromeClientError("tls: decrypt_error: server Finished does not verify")
        self._th.update(msg)
        th = self._th.copy().digest()
        derived = _ch_derive_secret(hashmod, self._hs_secret, b"derived", hashmod(b"").digest())
        master = _ch_hkdf_extract(hashmod, derived, bytes(hlen))
        self._c_ap = _ch_derive_secret(hashmod, master, b"c ap traffic", th)
        self._s_ap = _ch_derive_secret(hashmod, master, b"s ap traffic", th)
        self._require_no_pending("server Finished")
        self._read = _ch_traffic_cipher(self._aead_cls, hashmod, self._key_len, self._s_ap)
        out = b""
        if not self._ccs_sent:
            out += self.CCS_RECORD
            self._ccs_sent = True
        for extra in self._client_flight():
            out += self._seal_handshake(extra)
            self._th.update(extra)
        c_key = _ch_hkdf_expand_label(hashmod, self._c_hs, b"finished", b"", hlen)
        finished = b"\x14" + _ch_vec(3, hmac.new(c_key, self._th.copy().digest(), hashmod).digest())
        out += self._seal_handshake(finished)
        self._th.update(finished)
        self._write = _ch_traffic_cipher(self._aead_cls, hashmod, self._key_len, self._c_ap)
        self._hs_secret = None
        self._c_hs = None
        self._s_hs = None
        self.handshake_done = True
        self._state = "connected"
        self.t_hs_ms = int((time.monotonic() - self._t0) * 1000)
        if self._log is not None:
            self._log("tls: group=0x%04x cipher=0x%04x alpn=%s alps=%d hrr=%d ch_len=%d t_hs=%dms" % (self.group, self.cipher, self.alpn or "-", int(self.alps_negotiated), int(self.hrr), self.ch_len, self.t_hs_ms))
        return out

    def _seal_handshake(self, msg):
        """One handshake message under the current write key, fragmented at CH_MAX_PLAINTEXT_BYTES."""
        out = b""
        for start in range(0, len(msg), CH_MAX_PLAINTEXT_BYTES):
            out += self._write.seal(22, msg[start:start + CH_MAX_PLAINTEXT_BYTES])
        return out

    def _client_flight(self):
        """Handshake messages sent before the client Finished: the client ALPS EncryptedExtensions, if any.

        Returns a list of whole handshake messages, sealed under the client
        handshake key and added to the transcript in order. Without ALPS the
        list is empty. With ALPS it is one EncryptedExtensions (type 8):
        extensions<0..2^16-1> holding extension 17613 whose body is the
        profile's raw h2 ALPS value, verbatim -- zero-length for Chrome, and
        still sent: an empty value is a present extension, not an absent one.
        """
        if not self.alps_negotiated:
            return []
        if self.alpn != "h2":
            raise ChromeClientError("tls: handshake_failure: server negotiated ALPS for a protocol the profile has no settings for")
        value = bytes.fromhex(self._profile["alps_h2_settings_hex"])
        extension = (17613).to_bytes(2, "big") + _ch_vec(2, value)
        return [b"\x08" + _ch_vec(3, _ch_vec(2, extension))]

    def _on_post_handshake(self, msg_type, msg):
        """A handshake message after the handshake: NewSessionTicket dropped, KeyUpdate handled, anything else refused."""
        if msg_type == 4:
            self._no_progress()
            return b""
        if msg_type == 24:
            return self._on_key_update(msg)
        raise ChromeClientError("tls: unexpected_message: post-handshake message type %d" % msg_type)

    def _on_key_update(self, msg):
        """KeyUpdate (RFC 8446 section 4.6.3): rekey the read side; answer update_requested, then rekey the write side."""
        body = bytes(msg)[4:]
        if len(body) != 1:
            raise ChromeClientError("tls: decode_error: KeyUpdate of %d bytes, expected 1" % len(body))
        request = body[0]
        if request not in (0, 1):
            raise ChromeClientError("tls: illegal_parameter: KeyUpdate request_update %d" % request)
        self._key_updates += 1
        if self._key_updates > CH_MAX_KEY_UPDATES:
            raise ChromeClientError("tls: unexpected_message: more than %d KeyUpdates without application data" % CH_MAX_KEY_UPDATES)
        self._require_no_pending("KeyUpdate")
        hashmod = self._hashmod
        hlen = hashmod().digest_size
        self._s_ap = _ch_hkdf_expand_label(hashmod, self._s_ap, b"traffic upd", b"", hlen)
        self._read = _ch_traffic_cipher(self._aead_cls, hashmod, self._key_len, self._s_ap)
        if request == 0 or self._close_sent:
            return b""
        out = self._seal_handshake(b"\x18\x00\x00\x01\x00")
        self._c_ap = _ch_hkdf_expand_label(hashmod, self._c_ap, b"traffic upd", b"", hlen)
        self._write = _ch_traffic_cipher(self._aead_cls, hashmod, self._key_len, self._c_ap)
        return out


class _ChTlsStream:
    """The blocking pump around one connected socket and one _ChTls: `sendall(data)`, `recv(n)`, `close()`.

    `_ChTlsStream(sock, tls)`; `handshake()` drives `tls` to completion
    and returns the negotiated ALPN (str or None). After it, the stream is
    the `sendall/recv` object _ChH1Connection reads from over TLS, exactly
    like a plain socket on the cleartext path.

    Bounded by construction: `recv(n)` returns at most `n` bytes and reads
    the socket ONLY when nothing decrypted is left, so what it holds is at
    most the plaintext of one socket read (RECV_BYTES of ciphertext). Every
    `feed` is followed by `read_app()`, so `tls.app_data` never grows past
    that either -- the ceilings of the layer above (head, chunk line, body)
    are what bound the connection, never this pump. Bytes the engine asks
    to write while reading (a KeyUpdate answer) are written at once.

    `recv` returns b"" at the server's close_notify and at a bare TCP EOF;
    `eof_without_close_notify` tells the two apart: _ChH1Connection refuses
    an EOF-delimited body that ended without close_notify (a truncation a
    party on the path can forge with one TCP FIN), and a Content-Length or
    chunked body refuses a short read on its own. A socket timeout becomes
    ChromeClientError("timeout: ...").
    """

    RECV_BYTES = 65536

    def __init__(self, sock, tls):
        self._sock = sock
        self._tls = tls
        self._plain = bytearray()
        self.eof_without_close_notify = False

    def handshake(self):
        """Write the ClientHello, feed the server's flight until the handshake completes; return tls.alpn."""
        tls = self._tls
        try:
            self._sock.sendall(tls.start())
            while not tls.handshake_done:
                data = self._sock.recv(self.RECV_BYTES)
                if not data:
                    raise ChromeClientError("tls: connection closed during the handshake")
                out = tls.feed(data)
                if out:
                    self._sock.sendall(out)
        except socket.timeout:
            raise ChromeClientError("timeout: TLS handshake timed out") from None
        self._plain += tls.read_app()
        return tls.alpn

    def sendall(self, data):
        """Seal `data` as application records and write them."""
        try:
            self._sock.sendall(self._tls.send_app(data))
        except socket.timeout:
            raise ChromeClientError("timeout: write timed out") from None

    def recv(self, n):
        """At most `n` decrypted bytes; b"" once the server closed (close_notify or EOF)."""
        tls = self._tls
        while not self._plain:
            if tls.closed:
                return b""
            try:
                data = self._sock.recv(self.RECV_BYTES)
            except socket.timeout:
                raise ChromeClientError("timeout: read timed out") from None
            if not data:
                self.eof_without_close_notify = True
                return b""
            out = tls.feed(data)
            if out:
                self._sock.sendall(out)
            self._plain += tls.read_app()
        chunk = bytes(self._plain[:n])
        del self._plain[:n]
        return chunk

    def close(self):
        """Send close_notify once (best effort) and close the socket."""
        try:
            alert = self._tls.close_notify()
            if alert:
                self._sock.sendall(alert)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def _ch_huffman_table():
    """The RFC 7541 Appendix B Huffman code: a tuple of 257 (code, bit_length) pairs indexed by symbol.

    Symbol 256 is EOS. The Appendix B code is canonical: ordered by (bit
    length, symbol), every code is the previous code plus one, shifted left by
    the growth in length. So the 257 bit lengths alone determine every code,
    and they are stored as ONE hex string (two digits per symbol, in symbol
    order) instead of 257 hand-typed pairs. The derivation refuses lengths that
    are not a complete prefix code -- a code that outgrows its length, or a
    last code (EOS) that is not all ones -- so a typo in one length is a
    ValueError at import, not a silently wrong encoder. The test suite holds
    the full Appendix B table as the oracle, never this function.

    A function bound ONCE as _CH_HUFFMAN, for the reason _ch_aes_tables gives.
    """
    lengths = bytes.fromhex("0d171c1c1c1c1c1c1c181e1c1c1e1c1c1c1c1c1c1c1c1e1c1c1c1c1c1c1c1c1c060a0a0c0d06080b0a0a080b080606060505050606060606060607080f060c0a0d06070707070707070707070707070707070707070707070807080d130d0e060f05060506050606060507070606060506070605050607070707070f0b0e0d1c141614141616161716171717171718171818161718171717171516171617171816151416161717151716161815161717151516151716171714161616171616171a1a1413161716191a1a1a1b1b1a181913151a1b1b1a1b1815151a1a1c1b1b1b14181415161515171616191918181a171a1b1a1a1b1b1b1b1b1c1b1b1b1b1b1a1e")
    if len(lengths) != 257:
        raise ValueError("hpack: %d Huffman code lengths, expected 257" % len(lengths))
    ranked = []
    for sym in range(257):
        ranked.append((lengths[sym], sym))
    ranked.sort()
    codes = [None] * 257
    code = 0
    previous = ranked[0][0]
    for length, sym in ranked:
        code <<= length - previous
        previous = length
        if code >> length:
            raise ValueError("hpack: Huffman code lengths are not a prefix code (symbol %d)" % sym)
        codes[sym] = (code, length)
        code += 1
    if code != 1 << previous:
        raise ValueError("hpack: Huffman code lengths do not form a complete prefix code")
    return tuple(codes)


_CH_HUFFMAN = _ch_huffman_table()


def _ch_huffman_decode_table():
    """(bit_length, code) -> symbol for every entry of _CH_HUFFMAN, EOS (256) included.

    A decoder reads a Huffman string bit by bit and looks up (bits read so far,
    their value); a prefix code guarantees at most one hit per prefix. The
    R-0017 PoC filled this dict with a module-level `for`, which is not a
    generator block shape and would have vanished silently from every host; it
    is a function bound ONCE as _CH_HUFFMAN_DECODE instead.
    """
    table = {}
    for sym in range(len(_CH_HUFFMAN)):
        code, length = _CH_HUFFMAN[sym]
        table[(length, code)] = sym
    return table


_CH_HUFFMAN_DECODE = _ch_huffman_decode_table()


def _ch_hpack_static():
    """The RFC 7541 Appendix A static table: a tuple of 61 (name, value) str pairs; entry i is HPACK index i + 1.

    Built by one `append` per entry so every line stays tab-safe.
    """
    t = []
    t.append((":authority", ""))
    t.append((":method", "GET"))
    t.append((":method", "POST"))
    t.append((":path", "/"))
    t.append((":path", "/index.html"))
    t.append((":scheme", "http"))
    t.append((":scheme", "https"))
    t.append((":status", "200"))
    t.append((":status", "204"))
    t.append((":status", "206"))
    t.append((":status", "304"))
    t.append((":status", "400"))
    t.append((":status", "404"))
    t.append((":status", "500"))
    t.append(("accept-charset", ""))
    t.append(("accept-encoding", "gzip, deflate"))
    t.append(("accept-language", ""))
    t.append(("accept-ranges", ""))
    t.append(("accept", ""))
    t.append(("access-control-allow-origin", ""))
    t.append(("age", ""))
    t.append(("allow", ""))
    t.append(("authorization", ""))
    t.append(("cache-control", ""))
    t.append(("content-disposition", ""))
    t.append(("content-encoding", ""))
    t.append(("content-language", ""))
    t.append(("content-length", ""))
    t.append(("content-location", ""))
    t.append(("content-range", ""))
    t.append(("content-type", ""))
    t.append(("cookie", ""))
    t.append(("date", ""))
    t.append(("etag", ""))
    t.append(("expect", ""))
    t.append(("expires", ""))
    t.append(("from", ""))
    t.append(("host", ""))
    t.append(("if-match", ""))
    t.append(("if-modified-since", ""))
    t.append(("if-none-match", ""))
    t.append(("if-range", ""))
    t.append(("if-unmodified-since", ""))
    t.append(("last-modified", ""))
    t.append(("link", ""))
    t.append(("location", ""))
    t.append(("max-forwards", ""))
    t.append(("proxy-authenticate", ""))
    t.append(("proxy-authorization", ""))
    t.append(("range", ""))
    t.append(("referer", ""))
    t.append(("refresh", ""))
    t.append(("retry-after", ""))
    t.append(("server", ""))
    t.append(("set-cookie", ""))
    t.append(("strict-transport-security", ""))
    t.append(("transfer-encoding", ""))
    t.append(("user-agent", ""))
    t.append(("vary", ""))
    t.append(("via", ""))
    t.append(("www-authenticate", ""))
    return tuple(t)


_CH_HPACK_STATIC = _ch_hpack_static()


def _ch_hpack_int(value, prefix, flags):
    """An RFC 7541 section 5.1 integer in an N-bit prefix, OR-ed with `flags` into the first byte."""
    if value < 0:
        raise ChromeClientError("h2: hpack integer %d is negative" % value)
    mask = (1 << prefix) - 1
    if value < mask:
        return bytes((flags | value,))
    out = bytearray((flags | mask,))
    value -= mask
    while value >= 128:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _ch_huffman_encode(raw):
    """`raw` bytes Huffman-coded (RFC 7541 section 5.2): codes MSB first, the last byte padded with ones (the EOS prefix)."""
    acc = 0
    nbits = 0
    out = bytearray()
    for b in raw:
        code, length = _CH_HUFFMAN[b]
        acc = (acc << length) | code
        nbits += length
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
        acc &= (1 << nbits) - 1
    if nbits:
        pad = 8 - nbits
        out.append(((acc << pad) | ((1 << pad) - 1)) & 0xFF)
    return bytes(out)


def _ch_huffman_size(raw):
    """The Huffman-coded length of `raw` in whole bytes, without encoding it."""
    bits = 0
    for b in raw:
        bits += _CH_HUFFMAN[b][1]
    return (bits + 7) // 8


def _ch_hpack_string(raw):
    """An RFC 7541 section 5.2 string literal, Huffman-coded ONLY when that is strictly shorter than raw.

    That is quiche's HpackEncoder::EmitString (the PoC's hpack_string_auto),
    and it is what the captures show: `?0`, `"macOS"` and `1` go raw because
    their Huffman form is not shorter; a tie goes raw too.
    """
    raw = bytes(raw)
    size = _ch_huffman_size(raw)
    if size < len(raw):
        return _ch_hpack_int(size, 7, 0x80) + _ch_huffman_encode(raw)
    return _ch_hpack_int(len(raw), 7, 0x00) + raw


def _ch_cookie_crumbs(value):
    """A cookie header value split into crumbs the way quiche's HpackEncoder::CookieToCrumbs does (RFC 9113 section 8.2.3).

    Leading and trailing spaces and tabs are trimmed, the value is split on
    every `;`, and ONE space following a `;` is dropped. An empty crumb (`a=1;;b=2`)
    is kept, as quiche keeps it. Only single-crumb cookies are captured
    (cookie/: `tc=1`); the multi-crumb split is read from quiche's source and
    the PoC, UNVERIFIED against a capture.
    """
    v = value.strip(" \t")
    out = []
    pos = 0
    while True:
        end = v.find(";", pos)
        if end < 0:
            out.append(v[pos:])
            return out
        out.append(v[pos:end])
        pos = end + 1
        if pos < len(v) and v[pos] == " ":
            pos += 1


class _ChHpackEncoder:
    """Per-connection HPACK encoder that makes Chrome's choices (quiche HpackEncoder).

    For each header field, in this order:

    1. an exact (name, value) match, static table first, then the newest
        dynamic entry -> an indexed field (`1xxxxxxx`);
    2. a pseudo-header other than `:authority` -> a literal WITHOUT indexing
        (`0000xxxx`); the captures show it on every non-`/` `:path` and on
        `:method HEAD` (quiche DefaultPolicy);
    3. anything else -> a literal WITH incremental indexing (`01xxxxxx`),
        inserted into the dynamic table.

    A literal names its header by index when it can -- the static table first
    (its lowest index), then the newest dynamic entry -- and otherwise as a
    string. Every string is Huffman-coded only when strictly shorter
    (_ch_hpack_string). A `cookie` field is split into crumbs first
    (_ch_cookie_crumbs), and pseudo-headers are emitted before regular ones,
    keeping each group's order (quiche EncodeHeaderBlock).

    The dynamic table starts at 4096 bytes, the RFC 7541 default the peer's
    SETTINGS has not yet overridden. `apply_peer_table_size` follows the
    peer's SETTINGS_HEADER_TABLE_SIZE as quiche's ApplyHeaderTableSizeSetting
    does; the next block then opens with a dynamic table size update, preceded
    by a second one for the smallest bound seen when the table shrank below
    the final size in between (RFC 7541 section 4.2). An entry larger than the
    table empties it (section 4.4).

    One instance per connection: the table lives as long as the connection, so
    every HEADERS block on it must come from the same encoder, in wire order.
    Names must already be lowercase (RFC 9113 section 8.2.1) and every name and
    value latin-1; NUL, CR and LF are refused upstream by
    _ch_check_caller_headers, so quiche's split of a value on NUL never applies.
    """

    def __init__(self, max_size=4096):
        self._dynamic = []
        self._size = 0
        self._max_size = max_size
        self._bound = max_size
        self._min_bound = None
        self._emit_size_update = False

    def apply_peer_table_size(self, size):
        """The peer's SETTINGS_HEADER_TABLE_SIZE: shrink or grow the table and announce it in the next block."""
        if size == self._bound:
            return
        if size < self._bound:
            self._min_bound = size if self._min_bound is None else min(self._min_bound, size)
        self._bound = size
        self._max_size = size
        self._evict()
        self._emit_size_update = True

    def encode(self, headers):
        """One HPACK header block for `headers`, an iterable of (name, value) str pairs; updates the dynamic table."""
        out = bytearray()
        if self._emit_size_update:
            if self._min_bound is not None and self._min_bound < self._max_size:
                out += _ch_hpack_int(self._min_bound, 5, 0x20)
            out += _ch_hpack_int(self._max_size, 5, 0x20)
            self._emit_size_update = False
            self._min_bound = None
        pseudo = []
        regular = []
        for name, value in headers:
            if name == "cookie":
                for crumb in _ch_cookie_crumbs(value):
                    regular.append((name, crumb))
            elif name.startswith(":"):
                pseudo.append((name, value))
            else:
                regular.append((name, value))
        for name, value in pseudo + regular:
            out += self._field(name, value)
        return bytes(out)

    def _field(self, name, value):
        """One header field's representation, in the quiche decision order of the class docstring."""
        try:
            name_raw = name.encode("latin-1")
            value_raw = value.encode("latin-1")
        except UnicodeEncodeError:
            raise ChromeClientError("headers: a header name or value is not latin-1") from None
        exact, name_index = self._find(name, value)
        if exact is not None:
            return _ch_hpack_int(exact, 7, 0x80)
        indexed = bool(name) and (not name.startswith(":") or name == ":authority")
        if indexed:
            prefix, flags = 6, 0x40
        else:
            prefix, flags = 4, 0x00
        if name_index is not None:
            out = _ch_hpack_int(name_index, prefix, flags)
        else:
            out = _ch_hpack_int(0, prefix, flags) + _ch_hpack_string(name_raw)
        out += _ch_hpack_string(value_raw)
        if indexed:
            self._insert(name, value)
        return out

    def _find(self, name, value):
        """(exact index or None, name index or None): static table first, then dynamic newest first."""
        name_index = None
        for i in range(len(_CH_HPACK_STATIC)):
            entry_name, entry_value = _CH_HPACK_STATIC[i]
            if entry_name == name:
                if name_index is None:
                    name_index = i + 1
                if entry_value == value:
                    return i + 1, name_index
        base = len(_CH_HPACK_STATIC) + 1
        for i in range(len(self._dynamic)):
            entry_name, entry_value = self._dynamic[i]
            if entry_name == name:
                if name_index is None:
                    name_index = base + i
                if entry_value == value:
                    return base + i, name_index
        return None, name_index

    def _insert(self, name, value):
        """Add (name, value) as the newest dynamic entry, evicting the oldest; one too large empties the table."""
        entry = 32 + len(name) + len(value)
        if entry > self._max_size:
            self._dynamic = []
            self._size = 0
            return
        self._dynamic.insert(0, (name, value))
        self._size += entry
        self._evict()

    def _evict(self):
        """Drop the oldest entries until the table fits its maximum size (RFC 7541 section 4.4)."""
        while self._dynamic and self._size > self._max_size:
            name, value = self._dynamic.pop()
            self._size -= 32 + len(name) + len(value)


def _ch_hpack_decode_int(data, pos, prefix):
    """An RFC 7541 section 5.1 integer with an N-bit prefix read from `data` at `pos` -> (value, next pos).

    Bounded twice (D8): the value may not exceed CH_MAX_HPACK_INT, checked
    after every byte, and the encoding may not take more than
    CH_MAX_HPACK_INT_CONTINUATIONS continuation bytes, checked before the next
    one is read. The R-0017 PoC looped for as long as the peer set the
    continuation bit.
    """
    if pos >= len(data):
        raise ChromeClientError("h2: hpack integer truncated")
    mask = (1 << prefix) - 1
    value = data[pos] & mask
    pos += 1
    if value < mask:
        return value, pos
    shift = 0
    count = 0
    while True:
        if count == CH_MAX_HPACK_INT_CONTINUATIONS:
            raise ChromeClientError("h2: hpack integer longer than %d continuation bytes" % CH_MAX_HPACK_INT_CONTINUATIONS)
        if pos >= len(data):
            raise ChromeClientError("h2: hpack integer truncated")
        b = data[pos]
        pos += 1
        count += 1
        value += (b & 0x7F) << shift
        shift += 7
        if value > CH_MAX_HPACK_INT:
            raise ChromeClientError("h2: hpack integer exceeds %d" % CH_MAX_HPACK_INT)
        if not b & 0x80:
            return value, pos


def _ch_huffman_decode(raw):
    """`raw` Huffman-coded bytes (RFC 7541 section 5.2) -> the decoded bytes, refusing what section 5.2 calls a decoding error (D9).

    Bits are read MSB first and looked up in _CH_HUFFMAN_DECODE as (bits read,
    their value). EOS decoded anywhere is an error; so is padding (the bits
    left after the last whole symbol) longer than 7 bits or not all ones. A
    30-bit run of ones decodes as EOS, so padding of 8 to 29 ones is refused
    as too long and 30 or more as EOS. The PoC accepted all three.
    """
    out = bytearray()
    code = 0
    nbits = 0
    for byte in raw:
        for shift in (7, 6, 5, 4, 3, 2, 1, 0):
            code = (code << 1) | ((byte >> shift) & 1)
            nbits += 1
            sym = _CH_HUFFMAN_DECODE.get((nbits, code))
            if sym is not None:
                if sym == 256:
                    raise ChromeClientError("h2: hpack Huffman string contains EOS")
                out.append(sym)
                code = 0
                nbits = 0
    if nbits > 7:
        raise ChromeClientError("h2: hpack Huffman padding longer than 7 bits")
    if code != (1 << nbits) - 1:
        raise ChromeClientError("h2: hpack Huffman padding is not all ones")
    return bytes(out)


def _ch_hpack_decode_string(data, pos):
    """An RFC 7541 section 5.2 string literal read from `data` at `pos` -> (latin-1 str, next pos).

    The length is a bounded _ch_hpack_decode_int and must fit in what is left
    of the block; a Huffman-flagged string goes through _ch_huffman_decode.
    """
    if pos >= len(data):
        raise ChromeClientError("h2: hpack string truncated")
    huffman = data[pos] & 0x80
    length, pos = _ch_hpack_decode_int(data, pos, 7)
    end = pos + length
    if end > len(data):
        raise ChromeClientError("h2: hpack string truncated")
    raw = data[pos:end]
    if huffman:
        raw = _ch_huffman_decode(raw)
    return raw.decode("latin-1"), end


class _ChHpackDecoder:
    """Per-connection HPACK decoder for the server's header blocks, hardened against a hostile encoder (D8, D9).

    `decode(block)` turns one complete header block (HEADERS plus every
    CONTINUATION payload, already bounded by CH_MAX_HEADER_BLOCK_BYTES) into a
    list of (name, value) latin-1 str pairs in wire order, and updates the
    dynamic table. It refuses, each as a one-line ChromeClientError("h2: hpack
    ...") that never echoes a name or value:

    - an integer over CH_MAX_HPACK_INT or longer than
        CH_MAX_HPACK_INT_CONTINUATIONS continuation bytes, and a truncated
        integer or string;
    - a Huffman string with EOS in it, or padding longer than 7 bits or not all
        ones (RFC 7541 section 5.2);
    - index 0, and an index past the static plus dynamic table (section 2.3.3);
    - a dynamic table size update above CH_HPACK_TABLE_BYTES -- OUR advertised
        SETTINGS_HEADER_TABLE_SIZE (section 6.3) -- or one after the block's
        first header field (section 4.2);
    - a decoded header list over CH_MAX_HEADER_LIST_BYTES, counted per section
        4.1 as name + value + 32 per field, checked field by field so a block of
        indexed references to one large entry is stopped as it crosses.

    Every refusal is a connection error of type COMPRESSION_ERROR (RFC 9113
    section 4.3): the table may be half-updated, so the decoder refuses every
    later block too and the connection must end with GOAWAY.

    The table starts at 4096 bytes, the RFC 7541 default the server's encoder
    uses until it announces otherwise; our larger SETTINGS value only raises
    the ceiling a size update may name (the same reading as nghttp2's
    inflater). One instance per connection: the table spans streams, so every
    block must be decoded here in the order it arrived.
    """

    def __init__(self, max_size=4096):
        self._dynamic = []
        self._size = 0
        self._max_size = max_size
        self._failed = False

    def decode(self, block):
        """One complete header block -> [(name, value), ...]; any refusal leaves the decoder unusable."""
        if self._failed:
            raise ChromeClientError("h2: hpack decoder unusable after an earlier error")
        self._failed = True
        headers = self._decode(bytes(block))
        self._failed = False
        return headers

    def _decode(self, data):
        """The representation loop of decode(): section 6.1 indexed, 6.2 literals, 6.3 size updates."""
        pos = 0
        headers = []
        list_size = 0
        seen_field = False
        while pos < len(data):
            b = data[pos]
            if b & 0x80:
                index, pos = _ch_hpack_decode_int(data, pos, 7)
                name, value = self._entry(index)
            elif (b & 0xE0) == 0x20:
                if seen_field:
                    raise ChromeClientError("h2: hpack table size update after a header field")
                size, pos = _ch_hpack_decode_int(data, pos, 5)
                if size > CH_HPACK_TABLE_BYTES:
                    raise ChromeClientError("h2: hpack table size update %d exceeds %d" % (size, CH_HPACK_TABLE_BYTES))
                self._max_size = size
                self._evict()
                continue
            else:
                incremental = b & 0x40
                index, pos = _ch_hpack_decode_int(data, pos, 6 if incremental else 4)
                if index:
                    name = self._entry(index)[0]
                else:
                    name, pos = _ch_hpack_decode_string(data, pos)
                value, pos = _ch_hpack_decode_string(data, pos)
                if incremental:
                    self._insert(name, value)
            seen_field = True
            list_size += 32 + len(name) + len(value)
            if list_size > CH_MAX_HEADER_LIST_BYTES:
                raise ChromeClientError("h2: hpack header list exceeds %d bytes" % CH_MAX_HEADER_LIST_BYTES)
            headers.append((name, value))
        return headers

    def _entry(self, index):
        """The (name, value) at HPACK `index`: static 1-61, then the dynamic table newest first (section 2.3.3)."""
        if index == 0:
            raise ChromeClientError("h2: hpack index 0")
        if index <= len(_CH_HPACK_STATIC):
            return _CH_HPACK_STATIC[index - 1]
        slot = index - len(_CH_HPACK_STATIC) - 1
        if slot >= len(self._dynamic):
            raise ChromeClientError("h2: hpack index %d out of range (%d entries)" % (index, len(_CH_HPACK_STATIC) + len(self._dynamic)))
        return self._dynamic[slot]

    def _insert(self, name, value):
        """Add (name, value) as the newest dynamic entry, evicting the oldest; one too large empties the table (section 4.4)."""
        entry = 32 + len(name) + len(value)
        if entry > self._max_size:
            self._dynamic = []
            self._size = 0
            return
        self._dynamic.insert(0, (name, value))
        self._size += entry
        self._evict()

    def _evict(self):
        """Drop the oldest entries until the table fits its maximum size (RFC 7541 section 4.4)."""
        while self._dynamic and self._size > self._max_size:
            name, value = self._dynamic.pop()
            self._size -= 32 + len(name) + len(value)


_CH_BAD_PORTS = frozenset((0, 1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080))


def _ch_split_url(url):
    """The ONE URL normaliser: `url` -> (scheme, host, port, target), or ChromeClientError("url: ...").

    Every hop goes through it once and nothing downstream re-parses the URL:
    the `host` it returns is EXACTLY the string `connect_policy` vets, the
    SNI, the `:authority`/`Host` name and the cookie-jar key.

    - `scheme`: lowercased; anything but `http`/`https` is refused
        (`url: scheme <s> refused`).
    - userinfo (`user:pass@`, even an empty `@`) is refused
        (`url: userinfo not supported`).
    - `host`: lowercased; a non-ASCII name is IDNA-encoded
        (`_ch_idna_encode`); a trailing dot is KEPT (a distinct name, and
        Chrome keeps it); a bracketed IPv6 literal is unwrapped and written
        in its compressed form (a zone id is refused). A host of anything
        but letters, digits, `-`, `.` and `_`, or with an empty label, is
        refused, so no CR, LF, space or `%` can reach a header or the SNI.
    - `port`: the URL's, else 80 for http and 443 for https (the PoC's
        443-for-every-scheme is fixed here); 0 or out of range is refused,
        and so is a Fetch bad port in _CH_BAD_PORTS (`url: port <n> refused`).
    - `target`: the path (or "/") plus "?" and the query when there is one;
        the fragment is dropped. A space, a control character, DEL or a
        non-ASCII character is percent-encoded as UTF-8, so the target is
        printable ASCII without whitespace -- safe on an HTTP/1.1 request
        line and in `:path`.

    `urllib.parse.urlsplit` is spelled in full (block contract). Messages
    never echo the URL.
    """
    if not isinstance(url, str):
        raise ChromeClientError("url: the URL must be a str")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise ChromeClientError("url: malformed authority") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ChromeClientError("url: scheme %s refused" % (scheme[:40] or "(none)"))
    if "@" in parts.netloc:
        raise ChromeClientError("url: userinfo not supported")
    host = parts.hostname or ""
    if not host:
        raise ChromeClientError("url: no host")
    if parts.netloc.startswith("["):
        if "%" in host:
            raise ChromeClientError("url: IPv6 zone id not supported")
        try:
            host = ipaddress.IPv6Address(host).compressed
        except ValueError:
            raise ChromeClientError("url: invalid IPv6 literal") from None
    else:
        if not host.isascii():
            try:
                host = _ch_idna_encode(host).decode("ascii").lower()
            except UnicodeError:
                raise ChromeClientError("url: host is not a valid IDNA name") from None
        if not all((c.isascii() and c.isalnum()) or c in "-._" for c in host):
            raise ChromeClientError("url: invalid host")
        if host.startswith(".") or ".." in host:
            raise ChromeClientError("url: invalid host")
    if port is None:
        port = 443 if scheme == "https" else 80
    if port < 1 or port > 65535:
        raise ChromeClientError("url: invalid port")
    if port in _CH_BAD_PORTS:
        raise ChromeClientError("url: port %d refused" % port)
    raw = parts.path or "/"
    if parts.query:
        raw += "?" + parts.query
    target = ""
    try:
        for c in raw:
            if " " < c < "\x7f":
                target += c
            else:
                target += "".join("%%%02X" % b for b in c.encode("utf-8"))
    except UnicodeEncodeError:
        raise ChromeClientError("url: target is not valid Unicode") from None
    return scheme, host, port, target


_CH_TCHAR_SYMBOLS = "!#$%&'*+-.^_`|~"


_CH_FINGERPRINT_NAMES = ("user-agent", "accept-encoding", "priority")


_CH_FINGERPRINT_PREFIXES = ("sec-ch-ua", "sec-fetch-")


_CH_FRAMING_NAMES = ("host", "content-length", "transfer-encoding", "connection", "te", "upgrade", "keep-alive")


_CH_FRAMING_PREFIXES = ("proxy-",)


def _ch_header_pairs(headers):
    """Caller headers as a list: None -> [], a mapping -> its items(), anything else -> its elements as given."""
    if headers is None:
        return []
    if hasattr(headers, "items"):
        return list(headers.items())
    return list(headers)


def _ch_check_caller_headers(headers):
    """Refuse a caller header set the Chrome profile cannot send safely (FR-15); returns None.

    `headers` is a mapping or an iterable of (name, value) str pairs (None is
    empty). A pair that is not two strs is refused first. Then every pair is
    checked in this order and the FIRST failure raises ChromeClientError:

    1. CR, LF or NUL anywhere in the name or the value -> `headers: <name
        repr> contains CR, LF or NUL` (header injection, CWE-113); then
        any other control character but HTAB in the value -> `headers:
        <name repr> value contains a control character`, and a value
        that starts or ends with SP or HTAB -> `headers: <name repr> value
        has leading or trailing whitespace` (RFC 9110 section 5.5: a
        field value has no edge whitespace). A control character in a
        NAME fails rule 3 (or rule 2 for a pseudo-header);
    2. a name starting with `:` (a pseudo-header) -> `headers: <name repr>
        is fixed by the Chrome profile`;
    3. a name that is not a non-empty RFC 9110 token -> `headers: invalid
        header name <name repr>`;
    4. a fingerprint-bearing name (`user-agent`, `accept-encoding`,
        `priority`, `sec-ch-ua*`, `sec-fetch-*`) -> `headers: <name> is
        fixed by the Chrome profile`;
    5. a framing name (`host`, `content-length`, `transfer-encoding`,
        `connection`, `te`, `upgrade`, `keep-alive`, `proxy-*`) ->
        `headers: <name> is connection framing, owned by the Chrome profile`
        (request smuggling, CWE-444).

    After the five rules a value that does not encode as latin-1 is refused:
    `headers: <name> value is not latin-1`. Names are compared lowercased.

    Message invariant (M-1): every message is one line and never echoes a
    value or a raw name. Rules 1-3 show the name only as `repr(name)[:40]`
    (repr escapes every line-breaking character); rules 4, 5 and the latin-1
    refusal show the lowercased name only after it passed rule 3, so it is an
    ASCII token with no whitespace or control character.

    Coupling: _ChHpackEncoder deliberately does NOT split a value on NUL as
    quiche does; it relies on rule 1 having refused every NUL before any
    header reaches it. _ch_profile_headers calls this function first, so no
    transport ever sees an unchecked caller header.
    """
    for pair in _ch_header_pairs(headers):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], str):
            raise ChromeClientError("headers: every header must be a (name, value) pair of str")
        name, value = pair
        shown = repr(name)[:40]
        if "\r" in name or "\n" in name or "\0" in name or "\r" in value or "\n" in value or "\0" in value:
            raise ChromeClientError("headers: %s contains CR, LF or NUL" % shown)
        if any((ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F for c in value):
            raise ChromeClientError("headers: %s value contains a control character" % shown)
        if value[:1] in (" ", "\t") or value[-1:] in (" ", "\t"):
            raise ChromeClientError("headers: %s value has leading or trailing whitespace" % shown)
        if name.startswith(":"):
            raise ChromeClientError("headers: %s is fixed by the Chrome profile" % shown)
        if not name or not all((c.isascii() and c.isalnum()) or c in _CH_TCHAR_SYMBOLS for c in name):
            raise ChromeClientError("headers: invalid header name %s" % shown)
        lower = name.lower()
        if lower in _CH_FINGERPRINT_NAMES or lower.startswith(_CH_FINGERPRINT_PREFIXES):
            raise ChromeClientError("headers: %s is fixed by the Chrome profile" % lower)
        if lower in _CH_FRAMING_NAMES or lower.startswith(_CH_FRAMING_PREFIXES):
            raise ChromeClientError("headers: %s is connection framing, owned by the Chrome profile" % lower)
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            raise ChromeClientError("headers: %s value is not latin-1" % lower) from None


def _ch_origin_of(url):
    """The origin of an http(s) URL as (scheme, host, port), or None when it names none.

    The host is normalised the way _ch_split_url normalises it (lowercased,
    IDNA for a non-ASCII name, a bracketed IPv6 literal compressed, a
    trailing dot kept) and the port defaults by scheme (80/443), so
    `https://a.test` and `https://A.test:443/x` are one origin. Userinfo is
    ignored, never part of an origin. Any other scheme, a malformed
    authority or no host -> None (an opaque origin, equal to nothing).
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    if not host:
        return None
    if ":" in host:
        try:
            host = ipaddress.IPv6Address(host).compressed
        except ValueError:
            return None
    elif not host.isascii():
        try:
            host = _ch_idna_encode(host).decode("ascii").lower()
        except UnicodeError:
            return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _ch_origin_text(origin):
    """RFC 6454 serialisation of a (scheme, host, port) origin: the port only when not the scheme's default."""
    scheme, host, port = origin
    if ":" in host:
        host = "[" + host + "]"
    if port == (443 if scheme == "https" else 80):
        return "%s://%s" % (scheme, host)
    return "%s://%s:%d" % (scheme, host, port)


_CH_PUBLIC_SUFFIXES = ("ac.jp", "ac.uk", "appspot.com", "azurewebsites.net", "blogspot.com", "cloudfront.net", "co.id", "co.il", "co.in", "co.jp", "co.kr", "co.nz", "co.th", "co.uk", "co.za", "com.ar", "com.au", "com.br", "com.cn", "com.co", "com.hk", "com.mx", "com.my", "com.pl", "com.sg", "com.tr", "com.tw", "com.ua", "com.vn", "edu.au", "fly.dev", "github.io", "gitlab.io", "gov.uk", "herokuapp.com", "ltd.uk", "me.uk", "ne.jp", "net.au", "net.br", "net.cn", "netlify.app", "or.jp", "org.au", "org.br", "org.cn", "org.uk", "pages.dev", "plc.uk", "vercel.app", "web.app", "workers.dev")


def _ch_is_ip_host(host):
    """True when `host` (as _ch_split_url returns it) is an IPv4 or IPv6 literal."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _ch_is_public_suffix(domain):
    """True when `domain` is a single label or on _CH_PUBLIC_SUFFIXES (a trailing dot ignored)."""
    name = domain.rstrip(".")
    return "." not in name or name in _CH_PUBLIC_SUFFIXES


def _ch_site_of(host):
    """The registrable domain of `host` against the list-lite: an IP literal or a public suffix is its own site.

    Otherwise the last two labels, or the last three when the last two are
    on _CH_PUBLIC_SUFFIXES (`a.b.example.co.uk` -> `example.co.uk`). A
    trailing dot is ignored.
    """
    name = host.lower().rstrip(".")
    if _ch_is_ip_host(name) or _ch_is_public_suffix(name):
        return name
    labels = name.split(".")
    if ".".join(labels[-2:]) in _CH_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _ch_sec_fetch_site(initiator_url, target_url):
    """`sec-fetch-site` for a request to `target_url` initiated from `initiator_url` (Fetch Metadata).

    - no initiator (None or "") -> "none": a typed navigation;
    - the same origin -- scheme, host AND port, compared by _ch_origin_of, so
        an http:// page is same-origin with its own http:// target -> "same-origin";
    - the same scheme and the same _ch_site_of registrable domain ->
        "same-site" (schemeful: http://a.example.test -> https://b.example.test
        is cross-site; a port never matters to a site);
    - anything else, an initiator or target that names no http(s) origin
        included -> "cross-site".

    The site answer rests on the public-suffix list-LITE: a suffix missing
    from _CH_PUBLIC_SUFFIXES reads two registrants as same-site (UNVERIFIED
    beyond the list). One request answers for one hop; across a redirect
    chain Fetch keeps the WORST value seen, which is the session's job.
    """
    if not initiator_url:
        return "none"
    a = _ch_origin_of(initiator_url)
    b = _ch_origin_of(target_url)
    if a is None or b is None:
        return "cross-site"
    if a == b:
        return "same-origin"
    if a[0] == b[0] and _ch_site_of(a[1]) == _ch_site_of(b[1]):
        return "same-site"
    return "cross-site"


def _ch_referer_for(referrer, target):
    """The `Referer` Chrome sends from `referrer` to `target`: strict-origin-when-cross-origin (FR-16), or None.

    - no referrer, or one that names no http(s) origin -> None;
    - an https referrer to an http target -> None (a TLS downgrade never
        carries the referrer, not even its origin);
    - the same origin (_ch_origin_of) -> the full referrer URL with the
        fragment and any userinfo stripped, the host and port normalised
        and a space, control character or non-ASCII character in the path
        or query percent-encoded as _ch_split_url does it, so the value can
        never break a header line (`https://a.test/p?q`);
    - otherwise -> the referrer's origin with a trailing "/"
        (`https://a.test/`).

    The referrer is fixed when the request is created; the session calls
    this once per hop against that hop's target (Fetch "HTTP-redirect
    fetch"), and a navigate call passes no referrer on any hop. Chrome's
    4096-character referrer cap (longer -> origin only) is NOT applied:
    UNVERIFIED, no capture carries a referrer that long.
    """
    source = _ch_origin_of(referrer)
    if source is None:
        return None
    dest = _ch_origin_of(target)
    if source[0] == "https" and (dest is None or dest[0] != "https"):
        return None
    if source != dest:
        return _ch_origin_text(source) + "/"
    parts = urllib.parse.urlsplit(referrer)
    raw = parts.path or "/"
    if parts.query:
        raw += "?" + parts.query
    full = _ch_origin_text(source)
    for c in raw:
        if " " < c < "\x7f":
            full += c
        else:
            full += "".join("%%%02X" % b for b in c.encode("utf-8", "surrogatepass"))
    return full


def _ch_profile_headers(mode, method, authority, path, sec_fetch_site=None, origin=None, referer=None, cookie=None, extra=None, content_type=None, content_length=None, scheme="https"):
    """The h2 header list of one Chrome request, in the captured order: a list of (name, value).

    `mode` is "navigate" (CHROME_PROFILE["navigate_order"]; navigate/,
    navigate-reload/, cookie/, ip-literal/) or "cors" (a same-origin fetch():
    "cors_order" when the caller set an Accept, cors-get/ and cors-post/,
    else "cors_order_default_accept" with Chrome's default "*/*", cors-head/).
    The list is built by append, walking that order: a name with no value for
    this request is skipped. Every name is lowercase and the pseudo-headers
    come first, ready for _ChHpackEncoder; the HTTP/1.1 connection re-spells
    it in CHROME_PROFILE["h1_order"] and drops what h1 does not carry.

    `extra` (caller headers) passes _ch_check_caller_headers FIRST, before
    anything else is looked at. A caller name the order has a slot for keeps
    that slot and replaces its value (Accept, Referer, Cache-Control -- a
    reload is `cache-control: max-age=0`, navigate-reload/ -- Content-Type,
    Origin); any other name goes, lowercased and in the caller's order,
    immediately before CHROME_PROFILE["extra_slot"] (UNVERIFIED for a custom
    name, see the profile). A caller Cookie is merged, not replaced: the
    cookie slot carries `cookie` (the jar's header value) first, then the
    caller's crumbs split on "; ".

    `scheme` ("https" or "http") is the target's: the `:scheme` value (h2 is
    https-only; h1 drops pseudo-headers) and, with `authority`, the target
    origin the referer is compared with. `sec_fetch_site` None is derived
    from `referer`: "none" for a navigation without one (or with a referer
    that names no http(s) origin), "same-origin" for such a fetch, else
    _ch_sec_fetch_site(referer, <scheme>://<authority>/) -- the ONE
    derivation, scheme- and port-aware, so an http:// page is same-origin
    with its own http:// target. `origin` is cors-only. A same-origin fetch
    carries NO origin: 22 POST, 22 GET and 22 HEAD connections re-decoded
    from their raw HEADERS blocks carry none. When `origin` is None and the
    referer's origin differs from the target's, the referer's origin is sent,
    RFC 6454-serialised (UNVERIFIED: no cross-origin fetch was captured). `content_length` (an int) and
    `content_type` are cors-only; a POST with a body and no content type
    gets CHROME_PROFILE["cors_post_content_type"] (cors-post/).
    """
    _ch_check_caller_headers(extra)
    profile = CHROME_PROFILE
    if mode != "navigate" and mode != "cors":
        raise ChromeClientError("headers: mode %s is neither navigate nor cors" % repr(mode)[:40])
    if mode == "navigate" and (origin is not None or content_type is not None or content_length is not None):
        raise ChromeClientError("headers: origin, content-type and content-length are sent only in cors mode")
    staged = []
    crumbs = []
    caller_accept = False
    for name, value in _ch_header_pairs(extra):
        lower = name.lower()
        if lower == "cookie":
            crumbs.extend(c for c in value.split("; ") if c)
            continue
        if lower == "accept":
            caller_accept = True
        staged.append((lower, value))
    if mode == "navigate":
        order = profile["navigate_order"]
    elif caller_accept:
        order = profile["cors_order"]
    else:
        order = profile["cors_order_default_accept"]
    if scheme != "http" and scheme != "https":
        raise ChromeClientError("headers: scheme %s is neither http nor https" % repr(scheme)[:40])
    target_url = "%s://%s/" % (scheme, authority)
    referer_origin = _ch_origin_of(referer) if referer else None
    cross_origin = referer_origin is not None and referer_origin != _ch_origin_of(target_url)
    if sec_fetch_site is None:
        if referer_origin is None:
            sec_fetch_site = "none" if mode == "navigate" else "same-origin"
        else:
            sec_fetch_site = _ch_sec_fetch_site(referer, target_url)
    values = {}
    values[":method"] = method
    values[":authority"] = authority
    values[":scheme"] = scheme
    values[":path"] = path
    values["sec-ch-ua"] = profile["sec_ch_ua"]
    values["sec-ch-ua-mobile"] = profile["sec_ch_ua_mobile"]
    values["sec-ch-ua-platform"] = profile["sec_ch_ua_platform"]
    values["user-agent"] = profile["user_agent"]
    values["sec-fetch-site"] = sec_fetch_site
    values["accept-encoding"] = profile["accept_encoding"]
    values["accept-language"] = profile["accept_language"]
    if referer:
        values["referer"] = referer
    if mode == "navigate":
        # navigate/ stream 1: the navigation-only fields.
        values["upgrade-insecure-requests"] = "1"
        values["accept"] = profile["nav_accept"]
        values["sec-fetch-mode"] = "navigate"
        values["sec-fetch-user"] = "?1"
        values["sec-fetch-dest"] = "document"
        values["priority"] = profile["nav_priority_header"]
    else:
        # cors-get/ cors-head/ cors-post/ stream 7: the fetch-only fields.
        values["accept"] = profile["cors_accept"]
        values["sec-fetch-mode"] = "cors"
        values["sec-fetch-dest"] = "empty"
        values["priority"] = profile["cors_priority_header"]
        if origin is None and cross_origin:
            origin = _ch_origin_text(referer_origin)
        if origin is not None:
            values["origin"] = origin
        if content_length is not None:
            values["content-length"] = str(content_length)
            if content_type is None and method == "POST":
                content_type = profile["cors_post_content_type"]
        if content_type is not None:
            values["content-type"] = content_type
    extras = []
    for lower, value in staged:
        if lower in order:
            values[lower] = value
        else:
            extras.append((lower, value))
    parts = []
    if cookie:
        parts.append(cookie)
    parts.extend(crumbs)
    if parts:
        values["cookie"] = "; ".join(parts)
    out = []
    placed = False
    for name in order:
        if name == profile["extra_slot"]:
            out.extend(extras)
            placed = True
        value = values.get(name)
        if value is not None:
            out.append((name, value))
    if not placed:
        out.extend(extras)
    return out


_CH_H2_ERROR_NAMES = {0: "NO_ERROR", 1: "PROTOCOL_ERROR", 2: "INTERNAL_ERROR", 3: "FLOW_CONTROL_ERROR", 4: "SETTINGS_TIMEOUT", 5: "STREAM_CLOSED", 6: "FRAME_SIZE_ERROR", 7: "REFUSED_STREAM", 8: "CANCEL", 9: "COMPRESSION_ERROR", 10: "CONNECT_ERROR", 11: "ENHANCE_YOUR_CALM", 12: "INADEQUATE_SECURITY", 13: "HTTP_1_1_REQUIRED"}


def _ch_h2_frame(ftype, flags, sid, payload):
    """One h2 frame: the 9-byte header of RFC 9113 section 4.1 (24-bit length, type, flags, 31-bit stream id), then `payload`."""
    n = len(payload)
    if n > 0xFFFFFF:
        raise ChromeClientError("h2: frame payload too large to encode")
    return struct.pack("!BHBBI", n >> 16, n & 0xFFFF, ftype, flags, sid & 0x7FFFFFFF) + bytes(payload)


class _ChH2Connection:
    """One client HTTP/2 connection, sans-IO: bytes in through `feed`, bytes out as return values (D6, D7, D10).

    `_ChH2Connection(profile=None, log=None)`; `profile` defaults to
    CHROME_PROFILE, `log(str)` gets structure-only lines (`h2: stream=7
    status=200 body=10B`, `h2: goaway sent code=...`) -- never a header value,
    a path or a body byte. The session owns the socket and the deadline (D11):
    it writes what the methods return and feeds what it reads.

    **Chrome's first flight** (every h2 set of tests/files/chrome/154/, Akamai
    `1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p`): `preface()` is the
    client magic, ONE SETTINGS frame with CHROME_PROFILE["h2_settings"] in
    that order, and ONE connection WINDOW_UPDATE of
    CHROME_PROFILE["h2_window_increment"]; the session writes it together
    with the first `open_stream` so they leave in one flight as Chrome's do.
    `open_stream(headers, body=None, max_bytes=0, priority=None) -> (sid,
    bytes)` encodes `headers` (the _ch_profile_headers list) with the
    connection's _ChHpackEncoder into ONE HEADERS frame (CONTINUATIONs only
    past 16384 bytes) carrying the PRIORITY field: CHROME_PROFILE
    ["nav_priority"] when the list's sec-fetch-mode is `navigate`, else
    ["cors_priority"], unless `priority` (exclusive, dependency, weight)
    says otherwise. Without a body the HEADERS carries END_STREAM (flags
    0x25); with one it does not (0x24) and the body follows as ONE DATA frame
    with END_STREAM (cors-post/). A body larger than the peer's initial
    stream window, the connection send window or one 16384-byte frame is
    refused BEFORE anything is encoded ("h2: request body larger than one
    window"), so a refusal never desynchronises the HPACK table. Streams
    are 1, 3, 5, ...; the session's fetch lands on whatever id is next.

    **Reading.** `feed(data) -> bytes` parses every complete frame and
    returns what must be written in answer (SETTINGS ACK, PING ACK,
    WINDOW_UPDATE, RST_STREAM). Per stream: `response(sid)` is None until the
    final head arrived, then (status int, [(name, value), ...]) without
    pseudo-headers; `pop_body(sid)` drains the body bytes received so far;
    `done(sid)` is True once the stream ended or failed. A STREAM failure is
    kept on the stream and raised by `response`/`pop_body` -- the server's
    RST_STREAM, a GOAWAY that excludes it (the error has `retry_safe =
    True`, as has REFUSED_STREAM: RFC 9113 section 8.7 guarantees the
    request was not processed), `close()` before it ended, and a body past
    its limit: `max_bytes` (CH_MAX_BODY_BYTES when 0) is checked on every
    DATA frame, and crossing it queues RST_STREAM(CANCEL) and stores
    ChromeBodyTooLarge(limit), which `response` does not raise once the head
    arrived (pop_body does). `cancel(sid)` resets a stream the caller
    abandons. A CONNECTION failure raises from `feed` at once, after
    GOAWAY(code) was queued: `data_to_send()` returns it for the session to
    write. After that every call refuses, and `usable()` is False; it is
    also False after a received GOAWAY or `close()`.

    **What the reader refuses** (one-line ChromeClientError, never a header
    value or payload byte in it):

    - a frame whose 9-byte header announces more than CH_MAX_FRAME_BYTES,
        from the header, before its payload is waited for, so the input
        buffer never holds more than one frame plus one `feed` (FRAME_SIZE_ERROR);
    - a first frame that is not SETTINGS (RFC 9113 section 3.4), any frame
        but CONTINUATION inside an open header block (section 6.10), a
        CONTINUATION without one, padding not shorter than the payload
        (sections 6.1, 6.2), DATA before the response head, HEADERS or DATA on
        a stream the server closed (STREAM_CLOSED), a frame on a stream we
        never opened, PUSH_PROMISE (our SETTINGS_ENABLE_PUSH is 0), a server
        SETTINGS_ENABLE_PUSH other than 0, and a malformed :status or
        pseudo-header (PROTOCOL_ERROR);
    - SETTINGS with a payload not a multiple of 6 or an ACK with a payload,
        PING not 8 bytes, RST_STREAM and WINDOW_UPDATE not 4, PRIORITY not 5,
        GOAWAY under 8 (FRAME_SIZE_ERROR);
    - a header block over CH_MAX_HEADER_BLOCK_BYTES, or split into more than
        CH_MAX_H2_CONTINUATION_FRAMES non-empty CONTINUATIONs, and every
        _ChHpackDecoder refusal: COMPRESSION_ERROR or ENHANCE_YOUR_CALM, as
        the decoder is unusable after any refusal;
    - more than CH_MAX_H2_CONTROL_FRAMES frames that carry no response byte
        (SETTINGS, PING -- ACK or not, we send none -- RST_STREAM, PRIORITY,
        WINDOW_UPDATE, GOAWAY, unknown types, 1xx interim heads) with EXACTLY
        two one-shot exemptions, flags on the instance: the server's
        connection-preface SETTINGS and the ONE SETTINGS ACK answering our
        preface SETTINGS; a later SETTINGS and a second ACK count. More than
        CH_MAX_H2_EMPTY_FRAMES zero-length DATA frames without END_STREAM, or
        zero-length CONTINUATIONs, on one stream. Either is GOAWAY
        (ENHANCE_YOUR_CALM) and "h2: <kind> flood";
    - a WINDOW_UPDATE increment of 0 (PROTOCOL_ERROR), and one or a
        SETTINGS_INITIAL_WINDOW_SIZE change that takes the connection or a
        stream send window past 2^31-1 (FLOW_CONTROL_ERROR, RFC 9113 sections
        6.9, 6.9.1); DATA past our receive window (FLOW_CONTROL_ERROR).

    Unknown frame types are ignored (section 4.1) but counted. Received DATA
    is credited back with WINDOW_UPDATE once half a window is unacknowledged
    (connection and stream, as the PoC and Chromium do); frames on a stream
    we reset are decoded (the HPACK table spans streams) and discarded, their
    DATA still credited to the connection. The server's
    SETTINGS_HEADER_TABLE_SIZE goes to _ChHpackEncoder.apply_peer_table_size.
    `apply_alps(data)` applies the server's ALPS payload (ext 17613 in its
    EncryptedExtensions: whole h2 frames, SETTINGS and ACCEPT_CH, ending on a
    frame boundary, per Chromium net/spdy/alps_decoder.cc) as settings that
    are neither ACKed nor counted; ACCEPT_CH is ignored, and any other frame
    type is refused (Chromium's handling of other types: UNVERIFIED).
    """

    PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
    DEFAULT_WINDOW = 65535
    MAX_WINDOW = 2 ** 31 - 1

    def __init__(self, profile=None, log=None):
        if profile is None:
            profile = CHROME_PROFILE
        self._profile = profile
        self._log = log
        self._encoder = _ChHpackEncoder()
        self._decoder = _ChHpackDecoder()
        self._in = bytearray()
        self._out = bytearray()
        self._streams = {}
        self._next_sid = 1
        self._preface_sent = False
        self._failed = None
        self._closed = False
        self._goaway = None
        self._control = 0
        self._server_preface_seen = False
        self._ack_exempt = False
        self._hb = None
        self._hb_sid = 0
        self._hb_end_stream = False
        self._hb_frames = 0
        self._recv_stream_window = dict(profile["h2_settings"]).get(4, self.DEFAULT_WINDOW)
        self._recv_conn_window = self.DEFAULT_WINDOW
        self._conn_target = self.DEFAULT_WINDOW
        self._conn_unacked = 0
        self._send_conn_window = self.DEFAULT_WINDOW
        self._peer_initial_window = self.DEFAULT_WINDOW
        self._peer_max_streams = None

    def usable(self):
        """True while a new stream may be opened: no error, no GOAWAY received, not closed, stream ids left."""
        return self._failed is None and not self._closed and self._goaway is None and self._next_sid <= 0x7FFFFFFF

    def preface(self):
        """Chrome's connection preface: the magic, SETTINGS in profile order, the connection WINDOW_UPDATE."""
        if self._preface_sent:
            raise ChromeClientError("h2: preface already sent")
        payload = bytearray()
        for key, value in self._profile["h2_settings"]:
            payload += struct.pack("!HI", key, value)
        out = self.PREFACE + _ch_h2_frame(4, 0, 0, payload)
        increment = self._profile["h2_window_increment"]
        if increment:
            out += _ch_h2_frame(8, 0, 0, struct.pack("!I", increment))
            self._recv_conn_window += increment
        self._conn_target = self._recv_conn_window
        self._preface_sent = True
        self._ack_exempt = True
        return out

    def open_stream(self, headers, body=None, max_bytes=0, priority=None):
        """HEADERS (+ one DATA for a body) for a new stream: (sid, bytes to write). Every check runs before encoding."""
        if not self._preface_sent:
            raise ChromeClientError("h2: preface() must come before the first stream")
        if not self.usable():
            raise ChromeClientError("h2: connection is not usable for a new stream")
        pairs = list(headers)
        mode = None
        length = None
        for pair in pairs:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], str):
                raise ChromeClientError("h2: every header must be a (name, value) pair of str")
            name, value = pair
            if "\r" in name or "\n" in name or "\0" in name or "\r" in value or "\n" in value or "\0" in value:
                raise ChromeClientError("h2: header contains CR, LF or NUL")
            if any((ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F for c in name + value):
                raise ChromeClientError("h2: header contains a control character")
            if value[:1] in (" ", "\t") or value[-1:] in (" ", "\t"):
                raise ChromeClientError("h2: header value has leading or trailing whitespace")
            if not name or name != name.lower():
                raise ChromeClientError("h2: header names must be non-empty and lowercase")
            try:
                name.encode("latin-1")
                value.encode("latin-1")
            except UnicodeEncodeError:
                raise ChromeClientError("h2: header is not latin-1") from None
            if name == "sec-fetch-mode":
                mode = value
            elif name == "content-length":
                if length is not None:
                    raise ChromeClientError("h2: content-length does not match the request body")
                length = value
        if body is not None and not isinstance(body, (bytes, bytearray)):
            raise ChromeClientError("h2: the request body must be bytes")
        if length is not None and (body is None or length != str(len(body))):
            raise ChromeClientError("h2: content-length does not match the request body")
        if body is not None:
            if length is None:
                raise ChromeClientError("h2: a request body needs its content-length")
            if len(body) > min(self._peer_initial_window, self._send_conn_window, CH_MAX_FRAME_BYTES):
                raise ChromeClientError("h2: request body larger than one window")
        if self._peer_max_streams is not None:
            active = sum(1 for s in self._streams.values() if not s["done"])
            if active >= self._peer_max_streams:
                raise ChromeClientError("h2: the server's concurrent stream limit is reached")
        if priority is None:
            priority = self._profile["nav_priority"] if mode == "navigate" else self._profile["cors_priority"]
        exclusive, dependency, weight = priority
        if not 1 <= weight <= 256 or not 0 <= dependency <= 0x7FFFFFFF:
            raise ChromeClientError("h2: priority out of range")
        field = struct.pack("!IB", (0x80000000 if exclusive else 0) | dependency, weight - 1)
        limit = max_bytes if max_bytes and max_bytes > 0 else CH_MAX_BODY_BYTES
        sid = self._next_sid
        block = self._encoder.encode(pairs)
        room = CH_MAX_FRAME_BYTES - len(field)
        rest = block[room:]
        flags = 0x20
        if body is None:
            flags |= 0x01
        if not rest:
            flags |= 0x04
        out = bytearray(_ch_h2_frame(1, flags, sid, field + block[:room]))
        while rest:
            chunk = rest[:CH_MAX_FRAME_BYTES]
            rest = rest[CH_MAX_FRAME_BYTES:]
            out += _ch_h2_frame(9, 0 if rest else 0x04, sid, chunk)
        sent = 0
        if body is not None:
            sent = len(body)
            out += _ch_h2_frame(0, 0x01, sid, body)
            self._send_conn_window -= sent
        self._streams[sid] = {"state": "open", "done": False, "error": None, "status": None, "fields": None, "body": bytearray(), "received": 0, "limit": limit, "recv_window": self._recv_stream_window, "unacked": 0, "send_window": self._peer_initial_window - sent, "empty_data": 0, "empty_continuation": 0}
        self._next_sid += 2
        return sid, bytes(out)

    def feed(self, data):
        """Parse every complete frame in the buffer plus `data`; the bytes to write in answer."""
        if self._failed is not None:
            raise ChromeClientError("h2: connection unusable after an earlier error")
        if self._closed:
            raise ChromeClientError("h2: connection is closed")
        if not self._preface_sent:
            raise ChromeClientError("h2: preface() must come before feed()")
        self._in += data
        buf = self._in
        while len(buf) >= 9:
            length = (buf[0] << 16) | (buf[1] << 8) | buf[2]
            if length > CH_MAX_FRAME_BYTES:
                raise self._conn_error(6, "h2: frame of %d bytes exceeds %d" % (length, CH_MAX_FRAME_BYTES))
            if len(buf) < 9 + length:
                break
            ftype = buf[3]
            flags = buf[4]
            sid = struct.unpack_from("!I", buf, 5)[0] & 0x7FFFFFFF
            payload = bytes(buf[9:9 + length])
            del buf[:9 + length]
            self._frame(ftype, flags, sid, payload)
        return self.data_to_send()

    def data_to_send(self):
        """Drain the queued answer bytes (after a raise from feed: the GOAWAY or RST_STREAM to write)."""
        out = bytes(self._out)
        del self._out[:]
        return out

    def response(self, sid):
        """None until the final head arrived, then (status, fields); a failed stream raises its error.

        One exception (R2-M6, headers are decided first): a head that arrived
        before the body crossed max_bytes is still returned, even when the
        crossing DATA came in the same read; ChromeBodyTooLarge is then raised
        by pop_body, after the caller has seen the head.
        """
        stream = self._streams.get(sid)
        if stream is not None and stream["status"] is not None and isinstance(stream["error"], ChromeBodyTooLarge):
            return stream["status"], list(stream["fields"])
        stream = self._stream(sid)
        if stream["status"] is None:
            return None
        return stream["status"], list(stream["fields"])

    def pop_body(self, sid):
        """The body bytes received on `sid` since the last call; a failed stream raises its error."""
        stream = self._stream(sid)
        data = bytes(stream["body"])
        del stream["body"][:]
        return data

    def done(self, sid):
        """True once the stream ended (END_STREAM received) or failed."""
        stream = self._streams.get(sid)
        if stream is None:
            raise ChromeClientError("h2: no stream %d on this connection" % sid)
        return stream["done"]

    def cancel(self, sid):
        """Reset a stream the caller abandons: RST_STREAM(CANCEL) bytes to write (b"" when it already finished)."""
        stream = self._streams.get(sid)
        if stream is None or stream["done"] or self._failed is not None:
            return b""
        self._reset(stream, sid, ChromeClientError("h2: stream %d cancelled by the caller" % sid))
        return self.data_to_send()

    def close(self):
        """The transport is gone: the connection is unusable and every unfinished stream fails."""
        self._closed = True
        for sid, stream in self._streams.items():
            if not stream["done"]:
                stream["done"] = True
                stream["state"] = "reset"
                stream["error"] = ChromeClientError("h2: connection closed before stream %d ended" % sid)

    def apply_alps(self, data):
        """The server's ALPS payload: whole h2 frames; its SETTINGS applied unacknowledged, ACCEPT_CH ignored."""
        if self._failed is not None:
            raise ChromeClientError("h2: connection unusable after an earlier error")
        data = bytes(data or b"")
        pos = 0
        while pos < len(data):
            if len(data) - pos < 9:
                raise self._alps_error("h2: ALPS data does not end on a frame boundary")
            length = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
            if length > CH_MAX_FRAME_BYTES:
                raise self._alps_error("h2: ALPS frame of %d bytes exceeds %d" % (length, CH_MAX_FRAME_BYTES))
            end = pos + 9 + length
            if end > len(data):
                raise self._alps_error("h2: ALPS data does not end on a frame boundary")
            ftype = data[pos + 3]
            flags = data[pos + 4]
            sid = struct.unpack_from("!I", data, pos + 5)[0] & 0x7FFFFFFF
            if ftype == 4:
                if flags & 0x01 or sid != 0 or length % 6:
                    raise self._alps_error("h2: malformed SETTINGS frame in ALPS data")
                problem = self._apply_settings(data[pos + 9:end])
                if problem is not None:
                    raise self._alps_error(problem[1])
            elif ftype != 0x89:
                raise self._alps_error("h2: ALPS frame type %d refused" % ftype)
            pos = end

    def _alps_error(self, message):
        """Mark the connection failed by bad ALPS data (nothing is written: the preface may not be out yet)."""
        self._failed = message
        return ChromeClientError(message)

    def _stream(self, sid):
        """The stream record of `sid`, raising the stream's stored error if it failed."""
        stream = self._streams.get(sid)
        if stream is None:
            raise ChromeClientError("h2: no stream %d on this connection" % sid)
        if stream["error"] is not None:
            raise stream["error"]
        return stream

    def _conn_error(self, code, message):
        """Queue GOAWAY(code) once, fail every unfinished stream, and return the error for the caller to raise."""
        if self._failed is None:
            self._failed = message
            self._out += _ch_h2_frame(7, 0, 0, struct.pack("!II", 0, code))
            if self._log is not None:
                self._log("h2: goaway sent code=%s" % _CH_H2_ERROR_NAMES.get(code, "0x%x" % code))
        for stream in self._streams.values():
            if not stream["done"]:
                stream["done"] = True
                stream["state"] = "reset"
                stream["error"] = ChromeClientError(message)
        return ChromeClientError(message)

    def _count(self):
        """One more frame that carries no response byte; past CH_MAX_H2_CONTROL_FRAMES it is a flood."""
        self._control += 1
        if self._control > CH_MAX_H2_CONTROL_FRAMES:
            raise self._conn_error(11, "h2: control frame flood (more than %d per connection)" % CH_MAX_H2_CONTROL_FRAMES)

    def _reset(self, stream, sid, error):
        """Queue RST_STREAM(CANCEL), drop the buffered body, and fail the stream with `error`."""
        self._out += _ch_h2_frame(3, 0, sid, struct.pack("!I", 8))
        stream["state"] = "reset"
        stream["done"] = True
        stream["error"] = error
        del stream["body"][:]
        if self._log is not None:
            self._log("h2: rst sent stream=%d code=CANCEL" % sid)

    def _known(self, sid, kind):
        """The record of a stream this client opened; any other id is a connection PROTOCOL_ERROR."""
        stream = self._streams.get(sid)
        if stream is None:
            raise self._conn_error(1, "h2: %s on stream %d, which the client never opened" % (kind, sid))
        return stream

    def _unpad(self, flags, payload, skip):
        """The fragment of a DATA/HEADERS payload after the pad length, `skip` priority bytes and the padding."""
        start = 0
        pad = 0
        if flags & 0x08:
            if not payload:
                raise self._conn_error(1, "h2: padded frame without a pad length")
            pad = payload[0]
            start = 1
        start += skip
        if start + pad > len(payload):
            raise self._conn_error(1, "h2: padding and priority fields exceed the frame payload")
        return payload[start:len(payload) - pad]

    def _frame(self, ftype, flags, sid, payload):
        """Dispatch one complete frame (RFC 9113 section 6)."""
        if not self._server_preface_seen and (ftype != 4 or flags & 0x01):
            raise self._conn_error(1, "h2: the server preface is not a SETTINGS frame")
        if self._hb is not None and ftype != 9:
            raise self._conn_error(1, "h2: a frame interrupted a header block")
        if ftype == 0:
            self._on_data(flags, sid, payload)
        elif ftype == 1:
            self._on_headers(flags, sid, payload)
        elif ftype == 2:
            if len(payload) != 5:
                raise self._conn_error(6, "h2: PRIORITY frame of %d bytes, not 5" % len(payload))
            if sid == 0:
                raise self._conn_error(1, "h2: PRIORITY on stream 0")
            self._count()
        elif ftype == 3:
            self._on_rst(sid, payload)
        elif ftype == 4:
            self._on_settings(flags, sid, payload)
        elif ftype == 5:
            raise self._conn_error(1, "h2: PUSH_PROMISE refused (SETTINGS_ENABLE_PUSH is 0)")
        elif ftype == 6:
            if len(payload) != 8:
                raise self._conn_error(6, "h2: PING frame of %d bytes, not 8" % len(payload))
            if sid != 0:
                raise self._conn_error(1, "h2: PING on a stream")
            self._count()
            if not flags & 0x01:
                self._out += _ch_h2_frame(6, 0x01, 0, payload)
        elif ftype == 7:
            self._on_goaway(sid, payload)
        elif ftype == 8:
            self._on_window_update(sid, payload)
        elif ftype == 9:
            self._on_continuation(flags, sid, payload)
        else:
            self._count()

    def _on_data(self, flags, sid, payload):
        """DATA: flow control first (every byte counts), then the stream's state, flood counter and byte limit."""
        if sid == 0:
            raise self._conn_error(1, "h2: DATA on stream 0")
        stream = self._known(sid, "DATA")
        data = self._unpad(flags, payload, 0)
        end = flags & 0x01
        size = len(payload)
        if size > self._recv_conn_window:
            raise self._conn_error(3, "h2: DATA exceeds the connection receive window")
        self._recv_conn_window -= size
        self._conn_unacked += size
        if self._conn_unacked >= self._conn_target // 2:
            self._out += _ch_h2_frame(8, 0, 0, struct.pack("!I", self._conn_unacked))
            self._recv_conn_window += self._conn_unacked
            self._conn_unacked = 0
        if not data and not end:
            stream["empty_data"] += 1
            if stream["empty_data"] > CH_MAX_H2_EMPTY_FRAMES:
                raise self._conn_error(11, "h2: empty DATA flood on stream %d" % sid)
        state = stream["state"]
        if state == "reset":
            self._count()
            return
        if state != "open":
            raise self._conn_error(5, "h2: DATA on closed stream %d" % sid)
        if stream["status"] is None:
            raise self._conn_error(1, "h2: DATA before the response head on stream %d" % sid)
        if size > stream["recv_window"]:
            raise self._conn_error(3, "h2: DATA exceeds the receive window of stream %d" % sid)
        stream["recv_window"] -= size
        if stream["received"] + len(data) > stream["limit"]:
            self._reset(stream, sid, ChromeBodyTooLarge(stream["limit"]))
            return
        stream["received"] += len(data)
        stream["body"] += data
        if end:
            self._end_stream(sid, stream)
            return
        stream["unacked"] += size
        if stream["unacked"] >= self._recv_stream_window // 2:
            self._out += _ch_h2_frame(8, 0, sid, struct.pack("!I", stream["unacked"]))
            stream["recv_window"] += stream["unacked"]
            stream["unacked"] = 0

    def _on_headers(self, flags, sid, payload):
        """HEADERS: opens a header block on a stream we opened; END_HEADERS decodes it at once."""
        if sid == 0:
            raise self._conn_error(1, "h2: HEADERS on stream 0")
        stream = self._known(sid, "HEADERS")
        if stream["state"] == "ended" or stream["state"] == "peer_reset":
            raise self._conn_error(5, "h2: HEADERS on closed stream %d" % sid)
        fragment = self._unpad(flags, payload, 5 if flags & 0x20 else 0)
        self._hb = bytearray()
        self._hb_sid = sid
        self._hb_end_stream = bool(flags & 0x01)
        self._hb_frames = 0
        self._append_block(fragment, flags & 0x04)

    def _on_continuation(self, flags, sid, payload):
        """CONTINUATION: only inside an open block on its stream; bounded in bytes, frames and empty frames."""
        if self._hb is None:
            raise self._conn_error(1, "h2: CONTINUATION without an open header block")
        if sid != self._hb_sid:
            raise self._conn_error(1, "h2: CONTINUATION on another stream than its HEADERS")
        stream = self._streams[sid]
        if not payload:
            stream["empty_continuation"] += 1
            if stream["empty_continuation"] > CH_MAX_H2_EMPTY_FRAMES:
                raise self._conn_error(11, "h2: empty CONTINUATION flood on stream %d" % sid)
        else:
            self._hb_frames += 1
            if self._hb_frames > CH_MAX_H2_CONTINUATION_FRAMES:
                raise self._conn_error(11, "h2: CONTINUATION flood (more than %d in one header block)" % CH_MAX_H2_CONTINUATION_FRAMES)
        self._append_block(payload, flags & 0x04)

    def _append_block(self, fragment, end_headers):
        """Add a fragment to the open block under CH_MAX_HEADER_BLOCK_BYTES; at END_HEADERS, decode it."""
        if len(self._hb) + len(fragment) > CH_MAX_HEADER_BLOCK_BYTES:
            raise self._conn_error(9, "h2: header block exceeds %d bytes" % CH_MAX_HEADER_BLOCK_BYTES)
        self._hb += fragment
        if not end_headers:
            return
        block = bytes(self._hb)
        self._hb = None
        try:
            fields = self._decoder.decode(block)
        except ChromeClientError as exc:
            raise self._conn_error(9, str(exc)) from None
        self._on_header_list(self._hb_sid, self._hb_end_stream, fields)

    # RFC 9113 section 8.2.2: connection-specific fields a response may not carry.
    CONNECTION_SPECIFIC = ("connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade")

    def _on_header_list(self, sid, end_stream, fields):
        """A decoded block: skip 1xx interim heads, keep the final head, discard trailers.

        A block on a stream that is no longer open (reset by this client) is
        decoded -- the HPACK table must follow it -- counted as a control
        frame and dropped. A regular field whose name is empty, carries an
        uppercase letter, is not an RFC 9110 token or is connection-specific
        (CONNECTION_SPECIFIC), or whose value carries CR, LF or NUL, makes
        the response malformed (RFC 9113 section 8.2.1): the stream is reset
        and its request fails with "h2: malformed response header on stream
        <n>" -- the same fields the h1 reader refuses, never echoed.
        """
        stream = self._streams[sid]
        if stream["state"] != "open":
            self._count()
            return
        status = None
        regular = []
        for name, value in fields:
            if name.startswith(":"):
                if name != ":status" or status is not None or regular:
                    raise self._conn_error(1, "h2: malformed pseudo-header in the response on stream %d" % sid)
                status = value
            else:
                if not name or name != name.lower() or not all((c.isascii() and c.isalnum()) or c in _CH_TCHAR_SYMBOLS for c in name) or name in self.CONNECTION_SPECIFIC or "\r" in value or "\n" in value or "\0" in value:
                    self._reset(stream, sid, ChromeClientError("h2: malformed response header on stream %d" % sid))
                    return
                regular.append((name, value))
        if stream["status"] is not None:
            if status is not None or not end_stream:
                raise self._conn_error(1, "h2: malformed trailers on stream %d" % sid)
            self._end_stream(sid, stream)
            return
        if status is None or len(status) != 3 or not all(c in "0123456789" for c in status):
            raise self._conn_error(1, "h2: response on stream %d has no valid :status" % sid)
        code = int(status)
        if code < 200:
            if code == 101 or end_stream:
                raise self._conn_error(1, "h2: invalid informational response on stream %d" % sid)
            self._count()
            return
        stream["status"] = code
        stream["fields"] = regular
        if end_stream:
            self._end_stream(sid, stream)

    def _end_stream(self, sid, stream):
        """The server half-closed the stream: the response is complete."""
        stream["state"] = "ended"
        stream["done"] = True
        if self._log is not None:
            self._log("h2: stream=%d status=%d body=%dB" % (sid, stream["status"] or 0, stream["received"]))

    def _on_rst(self, sid, payload):
        """RST_STREAM: fail an open stream (REFUSED_STREAM is retry-safe); counted."""
        if len(payload) != 4:
            raise self._conn_error(6, "h2: RST_STREAM frame of %d bytes, not 4" % len(payload))
        if sid == 0:
            raise self._conn_error(1, "h2: RST_STREAM on stream 0")
        stream = self._known(sid, "RST_STREAM")
        self._count()
        if stream["state"] != "open":
            return
        code = struct.unpack("!I", payload)[0]
        error = ChromeClientError("h2: stream %d reset by the server (%s)" % (sid, _CH_H2_ERROR_NAMES.get(code, "0x%x" % code)))
        error.retry_safe = code == 7
        stream["state"] = "peer_reset"
        stream["done"] = True
        stream["error"] = error
        del stream["body"][:]

    def _on_settings(self, flags, sid, payload):
        """SETTINGS: the two one-shot exemptions, the payload checks, then apply and ACK."""
        if sid != 0:
            raise self._conn_error(1, "h2: SETTINGS on a stream")
        if flags & 0x01:
            if payload:
                raise self._conn_error(6, "h2: SETTINGS ACK with a payload")
            if self._ack_exempt:
                self._ack_exempt = False
            else:
                self._count()
            return
        if len(payload) % 6:
            raise self._conn_error(6, "h2: SETTINGS payload of %d bytes is not a multiple of 6" % len(payload))
        if not self._server_preface_seen:
            self._server_preface_seen = True
        else:
            self._count()
        problem = self._apply_settings(payload)
        if problem is not None:
            raise self._conn_error(problem[0], problem[1])
        self._out += _ch_h2_frame(4, 0x01, 0, b"")

    def _apply_settings(self, payload):
        """Apply the peer's settings (RFC 9113 section 6.5.2); None, or (error code, message) for the first bad one."""
        for pos in range(0, len(payload) - len(payload) % 6, 6):
            key, value = struct.unpack_from("!HI", payload, pos)
            if key == 1:
                self._encoder.apply_peer_table_size(value)
            elif key == 2:
                if value != 0:
                    return 1, "h2: server sent SETTINGS_ENABLE_PUSH other than 0"
            elif key == 3:
                self._peer_max_streams = value
            elif key == 4:
                if value > self.MAX_WINDOW:
                    return 3, "h2: SETTINGS_INITIAL_WINDOW_SIZE exceeds 2^31-1"
                delta = value - self._peer_initial_window
                for stream in self._streams.values():
                    if stream["send_window"] + delta > self.MAX_WINDOW:
                        return 3, "h2: SETTINGS_INITIAL_WINDOW_SIZE takes a stream window past 2^31-1"
                for stream in self._streams.values():
                    stream["send_window"] += delta
                self._peer_initial_window = value
            elif key == 5:
                if value < 16384 or value > 0xFFFFFF:
                    return 1, "h2: SETTINGS_MAX_FRAME_SIZE out of range"
        return None

    def _on_goaway(self, sid, payload):
        """GOAWAY: streams above last-stream-id fail retry-safe; the connection takes no new stream."""
        if sid != 0:
            raise self._conn_error(1, "h2: GOAWAY on a stream")
        if len(payload) < 8:
            raise self._conn_error(6, "h2: GOAWAY frame of %d bytes, under 8" % len(payload))
        self._count()
        last, code = struct.unpack_from("!II", payload, 0)
        last &= 0x7FFFFFFF
        if self._goaway is not None and last > self._goaway[0]:
            raise self._conn_error(1, "h2: GOAWAY raised its last-stream-id")
        self._goaway = (last, code)
        name = _CH_H2_ERROR_NAMES.get(code, "0x%x" % code)
        if self._log is not None:
            self._log("h2: goaway received last=%d code=%s" % (last, name))
        for stream_id, stream in self._streams.items():
            if stream_id > last and not stream["done"]:
                error = ChromeClientError("h2: stream %d not processed (GOAWAY last-stream-id %d, %s)" % (stream_id, last, name))
                error.retry_safe = True
                stream["state"] = "reset"
                stream["done"] = True
                stream["error"] = error
                del stream["body"][:]

    def _on_window_update(self, sid, payload):
        """WINDOW_UPDATE: increment 0 is PROTOCOL_ERROR, past 2^31-1 FLOW_CONTROL_ERROR (sections 6.9, 6.9.1)."""
        if len(payload) != 4:
            raise self._conn_error(6, "h2: WINDOW_UPDATE frame of %d bytes, not 4" % len(payload))
        self._count()
        increment = struct.unpack("!I", payload)[0] & 0x7FFFFFFF
        if increment == 0:
            raise self._conn_error(1, "h2: WINDOW_UPDATE with a zero increment")
        if sid == 0:
            if self._send_conn_window + increment > self.MAX_WINDOW:
                raise self._conn_error(3, "h2: WINDOW_UPDATE takes the connection window past 2^31-1")
            self._send_conn_window += increment
            return
        stream = self._known(sid, "WINDOW_UPDATE")
        if stream["done"]:
            return
        if stream["send_window"] + increment > self.MAX_WINDOW:
            raise self._conn_error(3, "h2: WINDOW_UPDATE takes the window of stream %d past 2^31-1" % sid)
        stream["send_window"] += increment


class _ChH1Connection:
    """One HTTP/1.1 connection over any object with `sendall(bytes)`, `recv(n)` and `close()`.

    `_ChH1Connection(transport, profile=None, log=None)`. The transport is
    a _ChTlsStream (TLS whose ALPN chose http/1.1) or a connected socket
    (cleartext `http://`); which one is the session's decision, never this
    class's. `log(str)` gets ONE structure-only line per response (`h1:
    status=200 framing=chunked body=5120B reuse=1`) -- never a header
    value, a target or a body byte.

    The API, one request at a time (no pipelining): `send_request(headers,
    body=None)`, then `read_head() -> (status, reason, fields)` with
    `fields` the response header lines in wire order as (name, value) str
    pairs, then `read_body(max_bytes=0) -> bytes`. `request()` chains the
    three. `usable()` is True only on an idle connection whose previous
    response was read to its last byte; a response the caller abandoned
    (`close()` after `read_head`, e.g. an `on_headers` refusal), any
    refusal, `Connection: close`, HTTP/1.0, an EOF-delimited body, a
    Transfer-Encoding with a Content-Length, and bytes left over after the
    response each close the transport instead.

    The request head is written ONLY from `headers`, the list
    _ch_profile_headers built (it already passed _ch_check_caller_headers):
    the request line from `:method`/`:path`, then `Host` (from
    `:authority`) and `Connection: keep-alive` as h1-tls/ and h1-plain/
    show, then every other field in the list's order with `priority`
    dropped (Chrome sends no Priority over HTTP/1.1). Names take their
    casing from CHROME_PROFILE["h1_order"] (sec-ch-* lowercase, the rest
    canonical); a name it does not list is spelled canonically (sec-ch-*
    stay lowercase). A `content-length` must equal the body passed; any
    other framing name is refused. Before the first `sendall`, every name
    and value is checked with an explicit `if` -- never an `assert`,
    which `python3 -O` strips -- and a CR, LF or NUL raises "h1: header
    contains CR, LF or NUL", any other control character but HTAB "h1:
    header contains a control character", a value with edge SP or HTAB
    "h1: header value has leading or trailing whitespace", each with
    nothing written: a second line of defence behind
    _ch_check_caller_headers. _ChH2Connection.open_stream applies the same
    three rules with the "h2:" prefix.

    The response (RFC 9112), each refusal a one-line ChromeClientError
    that never echoes a peer byte:

    - the head -- status line, header lines, blank line, and any 1xx
        interim heads before it, counted on the wire with their CRLFs --
        is at most CH_MAX_H1_HEAD_BYTES and CH_MAX_H1_HEADERS lines; an
        obs-fold continuation line is refused (RFC 9112 section 5.2); 101
        is refused;
    - the body: none for HEAD, 204 and 304; `Transfer-Encoding: chunked`
        (the only coding accepted) beats Content-Length; else one
        Content-Length of at most 19 digits (repeats must agree); else
        read to EOF -- and over a transport whose
        `eof_without_close_notify` is True (_ChTlsStream after a bare TCP
        FIN) that body is "h1: truncated: EOF-delimited body ended without
        close_notify", never returned as complete;
    - a chunk-size line, extensions included and its CRLF excluded, is at
        most CH_MAX_H1_CHUNK_LINE_BYTES; extensions are parsed past and
        never stored; the trailer section gets a FRESH CH_MAX_H1_HEAD_BYTES
        budget, counts its lines into the same CH_MAX_H1_HEADERS as the
        head, and is discarded;
    - the body is at most `max_bytes` (CH_MAX_BODY_BYTES when 0): a larger
        Content-Length or chunk size raises ChromeBodyTooLarge before the
        bytes are read, an EOF-delimited body as soon as it crosses.

    A socket timeout is ChromeClientError("timeout: ..."). An EOF before
    the first byte of a response is "h1: connection closed before the
    response" -- on a reused connection the session's retry signal.
    """

    RECV_BYTES = 65536

    def __init__(self, transport, profile=None, log=None):
        if profile is None:
            profile = CHROME_PROFILE
        self._transport = transport
        self._profile = profile
        self._log = log
        self._buf = bytearray()
        self._state = "idle"
        self._method = None
        self._framing = None
        self._length = 0
        self._will_close = False
        self._status = 0
        self._lines = 0
        self.requests = 0
        self.http_version = None

    def usable(self):
        """True on an idle connection: fresh, or its last response read to the end and kept alive."""
        return self._state == "idle"

    def request_head(self, headers, body=None):
        """The request head bytes for `headers` and `body`, built and checked; nothing is sent."""
        return self._build(headers, body)[1]

    def _build(self, headers, body):
        """(method, head bytes): the guard pass over every pair first, then the head."""
        pairs = list(headers)
        for pair in pairs:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], str):
                raise ChromeClientError("h1: every header must be a (name, value) pair of str")
            name, value = pair
            if "\r" in name or "\n" in name or "\0" in name or "\r" in value or "\n" in value or "\0" in value:
                raise ChromeClientError("h1: header contains CR, LF or NUL")
            if any((ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F for c in name + value):
                raise ChromeClientError("h1: header contains a control character")
            if value[:1] in (" ", "\t") or value[-1:] in (" ", "\t"):
                raise ChromeClientError("h1: header value has leading or trailing whitespace")
        if body is not None and not isinstance(body, (bytes, bytearray)):
            raise ChromeClientError("h1: the request body must be bytes")
        profile = self._profile
        spelled = {}
        for name in profile["h1_order"]:
            spelled[name.lower()] = name
        pseudo = {}
        fields = []
        for name, value in pairs:
            if name.startswith(":"):
                if name not in (":method", ":authority", ":scheme", ":path") or name in pseudo:
                    raise ChromeClientError("h1: unexpected pseudo-header")
                pseudo[name] = value
                continue
            if not name or not all((c.isascii() and c.isalnum()) or c in _CH_TCHAR_SYMBOLS for c in name):
                raise ChromeClientError("h1: invalid header name")
            fields.append((name.lower(), value))
        for key in (":method", ":path", ":authority"):
            if not pseudo.get(key):
                raise ChromeClientError("h1: request has no %s" % key)
        method = pseudo[":method"]
        if not all((c.isascii() and c.isalnum()) or c in _CH_TCHAR_SYMBOLS for c in method):
            raise ChromeClientError("h1: invalid method")
        if " " in pseudo[":path"] or "\t" in pseudo[":path"] or " " in pseudo[":authority"] or "\t" in pseudo[":authority"]:
            raise ChromeClientError("h1: request target or authority contains whitespace")
        lines = ["%s %s HTTP/1.1" % (method, pseudo[":path"])]
        lines.append("%s: %s" % (spelled.get("host", "Host"), pseudo[":authority"]))
        lines.append("%s: %s" % (spelled.get("connection", "Connection"), profile["h1_connection"]))
        has_length = False
        for lower, value in fields:
            if lower == "priority":
                continue
            if lower in _CH_FRAMING_NAMES or lower.startswith(_CH_FRAMING_PREFIXES):
                if lower != "content-length":
                    raise ChromeClientError("h1: %s is connection framing, owned by the h1 connection" % lower)
                if has_length or body is None or value != str(len(body)):
                    raise ChromeClientError("h1: content-length does not match the request body")
                has_length = True
            name = spelled.get(lower)
            if name is None:
                if lower.startswith("sec-ch-"):
                    name = lower
                else:
                    name = "-".join(w[:1].upper() + w[1:] for w in lower.split("-"))
            lines.append("%s: %s" % (name, value))
        if body is not None and not has_length:
            raise ChromeClientError("h1: a request body needs its content-length")
        try:
            head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        except UnicodeEncodeError:
            raise ChromeClientError("h1: header is not latin-1") from None
        return method, head

    def send_request(self, headers, body=None):
        """Check and write one request (head and body in one sendall); refused unless usable()."""
        if self._state != "idle":
            raise ChromeClientError("h1: connection is not idle (no pipelining)")
        method, head = self._build(headers, body)
        self._state = "sent"
        self._method = method
        try:
            self._transport.sendall(head + bytes(body or b""))
        except socket.timeout:
            self._fail()
            raise ChromeClientError("timeout: h1 write timed out") from None
        except OSError:
            self._fail()
            raise
        self.requests += 1

    def read_head(self):
        """Read the final response head: (status, reason, fields); interim 1xx heads are consumed."""
        if self._state != "sent":
            raise ChromeClientError("h1: no request is awaiting its response")
        try:
            version, status, reason, fields = self._read_head()
            self._frame(version, status, fields)
        except OSError:
            self._fail()
            raise
        self._state = "head"
        return status, reason, fields

    def read_body(self, max_bytes=0):
        """The whole body under `max_bytes` (CH_MAX_BODY_BYTES when 0), then keep-alive or close."""
        if self._state != "head":
            raise ChromeClientError("h1: no response head has been read")
        limit = max_bytes if max_bytes and max_bytes > 0 else CH_MAX_BODY_BYTES
        body = bytearray()
        try:
            if self._framing == "length":
                if self._length > limit:
                    raise ChromeBodyTooLarge(limit)
                self._read_into(body, self._length)
            elif self._framing == "chunked":
                self._read_chunked(body, limit)
            elif self._framing == "eof":
                while True:
                    if len(body) + len(self._buf) > limit:
                        raise ChromeBodyTooLarge(limit)
                    body += self._buf
                    del self._buf[:]
                    if not self._fill():
                        break
                if getattr(self._transport, "eof_without_close_notify", False):
                    raise ChromeClientError("h1: truncated: EOF-delimited body ended without close_notify")
        except OSError:
            self._fail()
            raise
        reuse = not self._will_close and not self._buf
        if reuse:
            self._state = "idle"
        else:
            self._fail()
        if self._log is not None:
            self._log("h1: status=%d framing=%s body=%dB reuse=%d" % (self._status, self._framing, len(body), int(reuse)))
        return bytes(body)

    def request(self, headers, body=None, max_bytes=0):
        """send_request + read_head + read_body: (status, reason, fields, body)."""
        self.send_request(headers, body)
        status, reason, fields = self.read_head()
        return status, reason, fields, self.read_body(max_bytes)

    def close(self):
        """Close the transport; the connection is never reused after this."""
        if self._state != "closed":
            self._fail()

    def _fail(self):
        """Mark the connection dead and close the transport (best effort)."""
        self._state = "closed"
        try:
            self._transport.close()
        except OSError:
            pass

    def _fill(self):
        """Append one transport read to the buffer; the number of bytes read (0 at EOF)."""
        try:
            data = self._transport.recv(self.RECV_BYTES)
        except socket.timeout:
            raise ChromeClientError("timeout: h1 read timed out") from None
        self._buf += data
        return len(data)

    def _read_line(self, limit, too_long, eof):
        """One line of at most `limit` bytes counting its LF: (line without CRLF or LF, bytes consumed)."""
        start = 0
        while True:
            end = self._buf.find(b"\n", start)
            if end >= 0:
                if end + 1 > limit:
                    raise ChromeClientError(too_long)
                line = bytes(self._buf[:end])
                del self._buf[:end + 1]
                if line.endswith(b"\r"):
                    line = line[:-1]
                return line, end + 1
            if len(self._buf) >= limit:
                raise ChromeClientError(too_long)
            start = len(self._buf)
            if not self._fill():
                raise ChromeClientError(eof)

    def _read_fields(self, budget, too_long, eof):
        """Header (or trailer) lines up to the blank line: (fields, bytes consumed); counts into self._lines."""
        fields = []
        used = 0
        while True:
            line, n = self._read_line(budget - used, too_long, eof)
            used += n
            if not line:
                return fields, used
            if line[:1] in (b" ", b"\t"):
                raise ChromeClientError("h1: obs-fold continuation line refused (RFC 9112 section 5.2)")
            self._lines += 1
            if self._lines > CH_MAX_H1_HEADERS:
                raise ChromeClientError("h1: more than %d header lines" % CH_MAX_H1_HEADERS)
            if b"\r" in line or b"\0" in line:
                raise ChromeClientError("h1: CR or NUL inside a header line")
            name, sep, value = line.partition(b":")
            name = name.decode("latin-1")
            if not sep or not name or not all((c.isascii() and c.isalnum()) or c in _CH_TCHAR_SYMBOLS for c in name):
                raise ChromeClientError("h1: malformed header line")
            fields.append((name, value.strip(b" \t").decode("latin-1")))

    def _read_head(self):
        """(version, status, reason, fields) of the final head; 1xx interim heads share its budgets."""
        if not self._buf and not self._fill():
            raise ChromeClientError("h1: connection closed before the response")
        budget = CH_MAX_H1_HEAD_BYTES
        too_long = "h1: response head exceeds %d bytes" % CH_MAX_H1_HEAD_BYTES
        eof = "h1: connection closed before the response head ended"
        self._lines = 0
        while True:
            line, n = self._read_line(budget, too_long, eof)
            budget -= n
            parts = line.split(b" ", 2)
            if len(parts) < 2 or parts[0] not in (b"HTTP/1.1", b"HTTP/1.0") or len(parts[1]) != 3 or not parts[1].isdigit() or parts[1][:1] == b"0":
                raise ChromeClientError("h1: malformed status line")
            status = int(parts[1])
            reason = parts[2].decode("latin-1") if len(parts) > 2 else ""
            fields, n = self._read_fields(budget, too_long, eof)
            budget -= n
            if status == 101:
                raise ChromeClientError("h1: unexpected 101 Switching Protocols")
            if status >= 200:
                return parts[0].decode("ascii"), status, reason, fields

    def _frame(self, version, status, fields):
        """Decide the body framing and whether the connection may be kept (RFC 9112 section 6.3)."""
        self.http_version = version
        self._status = status
        te = [v for n, v in fields if n.lower() == "transfer-encoding"]
        cl = [v for n, v in fields if n.lower() == "content-length"]
        tokens = set(t.strip().lower() for n, v in fields if n.lower() == "connection" for t in v.split(","))
        self._will_close = version != "HTTP/1.1" or "close" in tokens
        self._length = 0
        if self._method == "HEAD" or status in (204, 304):
            self._framing = "none"
        elif te:
            codings = [c.strip().lower() for v in te for c in v.split(",") if c.strip()]
            if codings != ["chunked"]:
                raise ChromeClientError("h1: transfer-encoding other than chunked refused")
            self._framing = "chunked"
            if cl:
                self._will_close = True
        elif cl:
            values = set(c.strip() for v in cl for c in v.split(","))
            value = values.pop() if len(values) == 1 else ""
            if not value or len(value) > 19 or not value.isascii() or not value.isdigit():
                raise ChromeClientError("h1: invalid or conflicting content-length")
            self._framing = "length"
            self._length = int(value)
        else:
            self._framing = "eof"
            self._will_close = True

    def _read_into(self, body, n):
        """Move exactly `n` body bytes from the transport into `body`."""
        while n > 0:
            if not self._buf and not self._fill():
                raise ChromeClientError("h1: connection closed before the body ended")
            take = min(n, len(self._buf))
            body += self._buf[:take]
            del self._buf[:take]
            n -= take

    def _read_chunked(self, body, limit):
        """A chunked body into `body` under `limit`; the trailer section is read and discarded."""
        too_long = "h1: chunk-size line exceeds %d bytes" % CH_MAX_H1_CHUNK_LINE_BYTES
        eof = "h1: connection closed before the chunked body ended"
        while True:
            line, _n = self._read_line(CH_MAX_H1_CHUNK_LINE_BYTES + 2, too_long, eof)
            if len(line) > CH_MAX_H1_CHUNK_LINE_BYTES:
                raise ChromeClientError(too_long)
            size = line.split(b";", 1)[0].strip(b" \t")
            if not size or len(size) > 16 or not all(c in b"0123456789abcdefABCDEF" for c in size):
                raise ChromeClientError("h1: invalid chunk size")
            size = int(size, 16)
            if size == 0:
                break
            if len(body) + size > limit:
                raise ChromeBodyTooLarge(limit)
            self._read_into(body, size)
            line, _n = self._read_line(2, "h1: chunk data not followed by CRLF", eof)
            if line:
                raise ChromeClientError("h1: chunk data not followed by CRLF")
        self._read_fields(CH_MAX_H1_HEAD_BYTES, "h1: trailer section exceeds %d bytes" % CH_MAX_H1_HEAD_BYTES, eof)


class _ChHeaders:
    """A response header list in wire order, read case-insensitively (FR-3).

    `_ChHeaders(pairs)` keeps the (name, value) str pairs exactly as the
    transport delivered them: lowercased names on h2, the server's casing
    on h1. `items()` returns them in wire order, duplicates included.
    `get(name, default=None)` matches the name case-insensitively and joins
    every value it has with ", " (RFC 9110 section 5.3) -- right for list
    headers, wrong for `set-cookie`, whose values contain commas; read that
    one with `get_all(name)`, which keeps each field separate, in order.
    """

    def __init__(self, pairs=None):
        self._pairs = list(pairs or ())

    def items(self):
        """Every (name, value) pair in wire order, as a new list."""
        return list(self._pairs)

    def get_all(self, name):
        """Every value of `name` (case-insensitive) in wire order; [] when absent."""
        lower = name.lower()
        return [v for n, v in self._pairs if n.lower() == lower]

    def get(self, name, default=None):
        """The values of `name` (case-insensitive) joined with ", ", or `default` when absent."""
        values = self.get_all(name)
        if not values:
            return default
        return ", ".join(values)

    def __contains__(self, name):
        return bool(self.get_all(name))

    def __len__(self):
        return len(self._pairs)


def _ch_leading_digits(token, lo, hi):
    """The int of `token`'s leading ASCII digits when there are `lo`..`hi` of them (RFC 6265 `n*mDIGIT ( non-digit *OCTET )`), else None."""
    n = 0
    while n < len(token) and token[n] in "0123456789":
        n += 1
    if n < lo or n > hi:
        return None
    return int(token[:n])


def _ch_cookie_date(text):
    """An RFC 6265 section 5.1.1 cookie-date -> seconds since the epoch (UTC), or None when it does not parse.

    The RFC's algorithm, not a strptime format: the string is cut into
    tokens on the delimiter set, and each token fills the FIRST still-empty
    field it matches, tried in the order time (`h:m:s`), day of month (1-2
    digits), month (the first three letters, any case), year (2-4 digits;
    70-99 -> 19xx, 0-69 -> 20xx). A missing field, a year before 1601, a
    day past its month's length or an hour/minute/second out of range ->
    None. Only the Expires attribute uses it.
    """
    tokens = []
    token = ""
    for c in text:
        o = ord(c)
        if o == 9 or 0x20 <= o <= 0x2F or 0x3B <= o <= 0x40 or 0x5B <= o <= 0x60 or 0x7B <= o <= 0x7E:
            if token:
                tokens.append(token)
            token = ""
        else:
            token += c
    if token:
        tokens.append(token)
    months = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    hms = day = month = year = None
    for token in tokens:
        if hms is None:
            fields = token.split(":", 2)
            if len(fields) == 3 and all(f and len(f) <= 2 and all(c in "0123456789" for c in f) for f in fields[:2]):
                second = _ch_leading_digits(fields[2], 1, 2)
                if second is not None:
                    hms = (int(fields[0]), int(fields[1]), second)
                    continue
        if day is None:
            value = _ch_leading_digits(token, 1, 2)
            if value is not None:
                day = value
                continue
        if month is None and token[:3].lower() in months:
            month = months.index(token[:3].lower()) + 1
            continue
        if year is None:
            value = _ch_leading_digits(token, 2, 4)
            if value is not None:
                year = value
                continue
    if hms is None or day is None or month is None or year is None:
        return None
    if 70 <= year <= 99:
        year += 1900
    elif 0 <= year <= 69:
        year += 2000
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    length = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    if year < 1601 or day < 1 or day > length or hms[0] > 23 or hms[1] > 59 or hms[2] > 59:
        return None
    # Days since 1970-01-01 of a proleptic Gregorian date (the civil-from-days inverse).
    y = year - (1 if month <= 2 else 0)
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    days = era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468
    return days * 86400 + hms[0] * 3600 + hms[1] * 60 + hms[2]


class _ChCookieJar:
    """A bounded, in-memory RFC 6265 subset cookie jar for one session (D12, L5, R2-M3); never persisted.

    `_ChCookieJar(clock=None)`: `clock` returns seconds since the epoch
    (None -> time.time, looked up at call time). Nothing is read from or
    written to disk or the environment: the jar lives and dies with its
    session. `host` everywhere is the string _ch_split_url returns and
    `target` its path-plus-query.

    `ingest(scheme, host, target, set_cookie_values)` takes the response's
    Set-Cookie field values (_ChHeaders.get_all("set-cookie")) and returns
    how many changed the jar. One value is REFUSED (ignored whole) when:
    - it has no `=` or an empty name (nameless cookies are not kept);
    - its name or value carries a control character other than HTAB;
    - len(name) + len(value) exceeds CH_MAX_COOKIE_BYTES (bytes: header
        values reach us latin-1 decoded, one char per byte);
    - it is Secure and was set by an http response, or its name starts
        with `__Secure-` / `__Host-` (any case) without Secure, or with
        `__Host-` and a Domain or a Path other than "/";
    - its Domain (one leading dot dropped, IDNA for non-ASCII) does not
        domain-match the host, or is a public suffix (one label, or on
        _CH_PUBLIC_SUFFIXES) other than the host itself -- `Domain=com`
        from a.example.com no longer leaks across .com (the PoC's D12);
        a public-suffix Domain EQUAL to the host is host-only (RFC 6265
        section 5.3 step 5);
    - an http response would set, replace or delete a cookie shadowing a
        stored Secure one (same name, domains matching either way, path
        matching) -- RFC 6265bis "leave secure cookies alone";
    - it is NEW (no stored cookie with its name, domain and path) and its
        site (_ch_site_of of the domain) already holds
        CH_MAX_COOKIES_PER_DOMAIN cookies, or the jar CH_MAX_COOKIES. At a
        cap the new cookie is refused and every stored one kept; replacing
        an existing one never counts as new. The jar never evicts.
    Caps are module globals read at call time, so a test may lower them.

    For an IP-literal host a cookie is ALWAYS host-only: a Domain attribute
    is ignored whatever it says (RFC 6265 section 5.1.3 lets only the IP
    itself domain-match). Without Domain a cookie is host-only. Path is the
    attribute when it starts with "/", else the default-path of `target`.
    Max-Age (RFC digits, optional "-"; more than 19 digits ignores the
    attribute, so no unbounded int() runs on a peer string) wins over Expires (_ch_cookie_date);
    Max-Age <= 0 or an Expires not in the future deletes the stored cookie
    and stores nothing; neither makes a session cookie. A replaced cookie
    keeps its creation order. HttpOnly and SameSite are accepted and not
    kept: no script reads this jar, and SameSite enforcement on cross-site
    subrequests is NOT modelled (UNVERIFIED against Chrome's Lax default).

    `header_for(scheme, host, target)` is the Cookie value for one request,
    or None: unexpired cookies whose host-only domain equals the host or
    whose domain domain-matches it, whose path path-matches the target's
    path, and -- when Secure -- only when `scheme` is https. A non-Secure
    cookie set over https IS sent on a same-host http request (RFC 6265;
    Chrome does the same; declared in ADR 0026). Cookies are not
    port-scoped. Longer paths first, then creation order, "; "-joined.
    """

    def __init__(self, clock=None):
        self._clock = clock
        self._cookies = []
        self._seq = 0

    def __len__(self):
        self._purge(self._now())
        return len(self._cookies)

    def _now(self):
        return (self._clock or time.time)()

    def _purge(self, now):
        self._cookies = [c for c in self._cookies if c["expiry"] is None or c["expiry"] > now]

    @staticmethod
    def _domain_match(host, domain):
        """RFC 6265 section 5.1.3: identical, or `host` ends with "." + `domain` and is not an IP literal."""
        if host == domain:
            return True
        return host.endswith("." + domain) and not _ch_is_ip_host(host)

    @staticmethod
    def _default_path(target):
        """RFC 6265 section 5.1.4 default-path of a request target (the query ignored)."""
        path = target.split("?", 1)[0]
        if not path.startswith("/") or path.count("/") == 1:
            return "/"
        return path[:path.rfind("/")]

    @staticmethod
    def _path_match(target, cpath):
        """RFC 6265 section 5.1.4 path-match of a request target (the query ignored) against a cookie path."""
        path = target.split("?", 1)[0] or "/"
        if path == cpath:
            return True
        if path.startswith(cpath):
            return cpath.endswith("/") or path[len(cpath)] == "/"
        return False

    def ingest(self, scheme, host, target, set_cookie_values):
        """Store the Set-Cookie values of one response to `scheme`://`host``target`; returns how many changed the jar."""
        now = self._now()
        self._purge(now)
        host = host.lower()
        changed = 0
        for line in set_cookie_values:
            if isinstance(line, str) and self._ingest_one(scheme, host, target, line, now):
                changed += 1
        return changed

    def _ingest_one(self, scheme, host, target, line, now):
        parts = line.split(";")
        if "=" not in parts[0]:
            return False
        name, value = parts[0].split("=", 1)
        name = name.strip(" \t")
        value = value.strip(" \t")
        if not name:
            return False
        if any((ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F for c in name + value):
            return False
        if len(name) + len(value) > CH_MAX_COOKIE_BYTES:
            return False
        domain = None
        cpath = None
        max_age = None
        expires = None
        secure = False
        for attr in parts[1:]:
            key, _, val = attr.partition("=")
            key = key.strip(" \t").lower()
            val = val.strip(" \t")
            if key == "domain":
                if val:
                    val = (val[1:] if val.startswith(".") else val).lower()
                    if not val.isascii():
                        try:
                            val = _ch_idna_encode(val).decode("ascii").lower()
                        except UnicodeError:
                            return False
                    domain = val
            elif key == "path":
                cpath = val if val.startswith("/") else None
            elif key == "max-age":
                digits = val[1:] if val.startswith("-") else val
                if digits and len(digits) <= 19 and all(c in "0123456789" for c in digits):
                    max_age = int(val)
            elif key == "expires":
                when = _ch_cookie_date(val)
                if when is not None:
                    expires = when
            elif key == "secure":
                secure = True
        lower = name.lower()
        if lower.startswith("__secure-") and not secure:
            return False
        if lower.startswith("__host-") and (not secure or domain is not None or cpath != "/"):
            return False
        if secure and scheme != "https":
            return False
        if _ch_is_ip_host(host) or domain is None:
            host_only = True
            domain = host
        elif _ch_is_public_suffix(domain):
            if domain != host:
                return False
            host_only = True
        elif self._domain_match(host, domain):
            host_only = False
        else:
            return False
        if cpath is None:
            cpath = self._default_path(target)
        delete = False
        expiry = None
        if max_age is not None:
            if max_age <= 0:
                delete = True
            else:
                expiry = now + max_age
        elif expires is not None:
            if expires <= now:
                delete = True
            else:
                expiry = expires
        if scheme != "https":
            for c in self._cookies:
                if c["secure"] and c["name"] == name and (self._domain_match(domain, c["domain"]) or self._domain_match(c["domain"], domain)) and self._path_match(cpath, c["path"]):
                    return False
        old = None
        for i, c in enumerate(self._cookies):
            if c["name"] == name and c["domain"] == domain and c["path"] == cpath:
                old = i
                break
        if delete:
            if old is None:
                return False
            del self._cookies[old]
            return True
        site = _ch_site_of(domain)
        cookie = {"name": name, "value": value, "domain": domain, "host_only": host_only, "path": cpath, "secure": secure, "expiry": expiry, "site": site, "seq": 0}
        if old is not None:
            cookie["seq"] = self._cookies[old]["seq"]
            self._cookies[old] = cookie
            return True
        if sum(1 for c in self._cookies if c["site"] == site) >= CH_MAX_COOKIES_PER_DOMAIN:
            return False
        if len(self._cookies) >= CH_MAX_COOKIES:
            return False
        self._seq += 1
        cookie["seq"] = self._seq
        self._cookies.append(cookie)
        return True

    def header_for(self, scheme, host, target):
        """The Cookie header value for one request to `scheme`://`host``target`, or None when no cookie applies."""
        self._purge(self._now())
        host = host.lower()
        chosen = []
        for c in self._cookies:
            if c["secure"] and scheme != "https":
                continue
            if c["host_only"]:
                if host != c["domain"]:
                    continue
            elif not self._domain_match(host, c["domain"]):
                continue
            if self._path_match(target, c["path"]):
                chosen.append(c)
        chosen.sort(key=lambda c: (-len(c["path"]), c["seq"]))
        return "; ".join("%s=%s" % (c["name"], c["value"]) for c in chosen) or None


class _ChResponse:
    """One finished response of the session (FR-3), requests-like.

    `_ChResponse(status_code, headers, url, body, decode_error=None,
    http_version=None, tls=None, impersonated=True, cert_verified=False)`.
    `headers` is a _ChHeaders (a list of pairs is wrapped in one); `url` is
    the final URL after redirects; `body` is the DECODED body
    (_ch_decode_body's bytes). `http_version` is "HTTP/2", "HTTP/1.1" or
    "HTTP/1.0"; `tls` is the connection's info dict (group, cipher, ALPN,
    ALPS, hrr) or None over cleartext http. The two honesty flags state
    which path answered: the Chrome path is `impersonated=True`,
    `cert_verified=False` (the defaults: this path verifies no
    certificate); the TLS 1.2 fallback is `impersonated=False`,
    `cert_verified=True`.

    `transport` (last argument, None when built by hand) is one of four
    labels: "chrome" (the Chrome TLS engine), "verified" (the default
    stdlib transport), "tls12-fallback" (the stdlib transport after a
    Chrome TLS 1.2 refusal) or "cleartext" (http). The session sets it for
    the whole redirect chain, WEAKEST hop wins: "chrome" once any hop was
    chrome, else "cleartext" once any hop was cleartext, else the hop's own
    label; `cert_verified` is True only for a "verified" or
    "tls12-fallback" chain. `impersonated` is the FINAL hop's (which
    fingerprint delivered the body: chrome and cleartext are the Chrome
    profile).

    `decode_error` is None, or the one-line reason the content coding could
    not be undone. When it is set, reading `.content` or `.text` raises
    ChromeClientError(decode_error): still-encoded bytes are never handed
    out as if they were the body. `.text` decodes `.content` with the
    `charset` parameter of `content-type` only when `codecs.lookup` resolves
    it to the same codec as one of TEXT_CHARSETS -- the WHATWG Encoding
    Standard's web charsets that Python has a codec for -- else UTF-8,
    always with errors="replace". The allowlist is the rule, not a
    `_is_text_encoding` filter: a server-chosen codec such as punycode
    (quadratic on a large body), idna (raises UnicodeError even under
    "replace"), unicode_escape, raw_unicode_escape or utf-7 is never run on
    a body.
    """

    # The WHATWG Encoding Standard's encodings, spelled as labels Python resolves (x-user-defined and
    # replacement have no codec). Compared by codecs.lookup(label).name, so every alias of one is accepted.
    TEXT_CHARSETS = ("utf-8", "utf-16le", "utf-16be", "utf-16", "ascii", "cp866", "iso-8859-1", "iso-8859-2", "iso-8859-3", "iso-8859-4", "iso-8859-5", "iso-8859-6", "iso-8859-7", "iso-8859-8", "iso-8859-10", "iso-8859-13", "iso-8859-14", "iso-8859-15", "iso-8859-16", "koi8-r", "koi8-u", "mac-roman", "mac-cyrillic", "cp874", "cp1250", "cp1251", "cp1252", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257", "cp1258", "gbk", "gb2312", "gb18030", "big5", "big5hkscs", "euc-jp", "iso-2022-jp", "shift_jis", "cp932", "euc-kr", "cp949")

    def __init__(self, status_code, headers, url, body, decode_error=None, http_version=None, tls=None, impersonated=True, cert_verified=False, transport=None):
        if not isinstance(headers, _ChHeaders):
            headers = _ChHeaders(headers)
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.decode_error = decode_error
        self.http_version = http_version
        self.tls = tls
        self.impersonated = impersonated
        self.cert_verified = cert_verified
        self.transport = transport
        self._body = bytes(body or b"")

    @property
    def content(self):
        """The decoded body bytes; ChromeClientError(decode_error) when the coding could not be undone."""
        if self.decode_error is not None:
            raise ChromeClientError(self.decode_error)
        return self._body

    @property
    def text(self):
        """`.content` decoded with the content-type charset when it is a TEXT_CHARSETS codec (else UTF-8), errors="replace"."""
        content = self.content
        charset = "utf-8"
        for param in (self.headers.get("content-type") or "").split(";")[1:]:
            key, sep, value = param.partition("=")
            if sep and key.strip().lower() == "charset":
                value = value.strip().strip("\"'").strip()
                try:
                    name = codecs.lookup(value).name
                except (LookupError, ValueError, TypeError):
                    break
                allowed = set()
                for label in self.TEXT_CHARSETS:
                    try:
                        allowed.add(codecs.lookup(label).name)
                    except LookupError:
                        continue
                if name in allowed:
                    charset = name
                break
        try:
            return content.decode(charset, "replace")
        except (LookupError, UnicodeError):
            return content.decode("utf-8", "replace")


def _ch_zlib_decode(data, wbits, label, budget):
    """Inflate `data` with `zlib.decompressobj(wbits)` into at most `budget` bytes (D16).

    Bomb-safe: every `decompress` call is capped with `max_length` at what
    remains of the budget plus one byte, so no call can allocate past it,
    and `flush()` (which is unbounded) is never called; output past the
    budget raises OverflowError. For gzip (`wbits` 16 + MAX_WBITS) every
    further member -- `unused_data` that starts with the gzip magic --
    gets a NEW decompressobj drawing on the SAME budget; bytes after the
    last member or after a deflate stream that are not a member are
    ignored, as Chromium's gzip source stream ignores them. A zlib error or
    a stream that ends before its end marker raises ValueError
    ("<label>: ..."), one line.
    """
    out = bytearray()
    rest = bytes(data)
    try:
        while True:
            stream = zlib.decompressobj(wbits)
            pending = rest
            while True:
                chunk = stream.decompress(pending, budget - len(out) + 1)
                out += chunk
                if len(out) > budget:
                    raise OverflowError("%s: output exceeds %d bytes" % (label, budget))
                pending = stream.unconsumed_tail
                if stream.eof or (not pending and not chunk):
                    break
            if not stream.eof:
                raise ValueError("%s: truncated stream" % label)
            rest = stream.unused_data
            if wbits <= zlib.MAX_WBITS or rest[:2] != b"\x1f\x8b":
                return bytes(out)
    except zlib.error as exc:
        raise ValueError("%s: %s" % (label, str(exc).splitlines()[0][:120] if str(exc) else "corrupt stream")) from None


def _ch_decode_body(headers, body, decoders, max_output):
    """Undo the response's Content-Encoding under ONE output budget: (bytes, decode_error or None) (D16).

    `headers` is a _ChHeaders or a list of (name, value) pairs; `decoders`
    maps a coding ("br", "zstd") to the injected callable `decoder(data,
    max_output) -> bytes` (None is no decoders; this module imports none).
    `max_output` is the cumulative budget (CH_DEFAULT_DECODE_CAP when 0
    or None).

    - An empty body decodes to b"" whatever the header says.
    - The codings of every Content-Encoding field (comma-joined) are
        lowercased; `identity` is skipped (it transforms nothing, so it
        cannot amplify); more than CH_MAX_CONTENT_CODINGS remaining, an
        unknown coding or a coding with no decoder is a decode_error, found
        BEFORE any layer is decoded.
    - The codings are undone in reverse order. `gzip`/`x-gzip` and
        `deflate` go through _ch_zlib_decode (every gzip member drawn from
        the same budget); `deflate` is tried zlib-wrapped when the first
        two bytes are a valid zlib header, and otherwise -- or when the
        wrapped attempt fails -- as raw deflate (the non-conformant servers
        RFC 9110 section 8.4.1.2 warns about), with the SAME remaining
        budget, never a fresh one.
    - ONE cumulative budget: each layer is called with what REMAINS after
        every earlier layer's output, so stacking codings or gzip members
        cannot multiply it. A decoder returning more than it was given, or
        a non-bytes value, is refused.

    OverflowError (the budget) PROPAGATES: the session turns it into
    ChromeBodyTooLarge(limit), never into a decode_error (R2-M6). A
    ValueError (corrupt, truncated) or LookupError (library absent) becomes
    the decode_error "body: <one line>", and the bytes returned are then
    b"": the still-encoded body is never handed on.
    """
    data = bytes(body or b"")
    if not data:
        return b"", None
    if isinstance(headers, _ChHeaders):
        values = headers.get_all("content-encoding")
    else:
        values = [v for n, v in headers if n.lower() == "content-encoding"]
    codings = [c.strip().lower() for c in ",".join(values).split(",") if c.strip()]
    codings = [c for c in codings if c != "identity"]
    if not codings:
        return data, None
    if len(codings) > CH_MAX_CONTENT_CODINGS:
        return b"", "body: %d content codings exceed the limit of %d" % (len(codings), CH_MAX_CONTENT_CODINGS)
    if decoders is None:
        decoders = {}
    for coding in codings:
        if coding in ("gzip", "x-gzip", "deflate"):
            continue
        if coding not in ("br", "zstd"):
            return b"", "body: content coding %s not supported" % repr(coding)[:40]
        if not callable(decoders.get(coding)):
            return b"", "body: no %s decoder available" % coding
    budget = max_output if max_output and max_output > 0 else CH_DEFAULT_DECODE_CAP
    used = 0
    try:
        for coding in reversed(codings):
            remaining = budget - used
            if coding in ("gzip", "x-gzip"):
                data = _ch_zlib_decode(data, 16 + zlib.MAX_WBITS, "gzip", remaining)
            elif coding == "deflate":
                wrapped = len(data) >= 2 and data[0] & 0x0F == 8 and (data[0] * 256 + data[1]) % 31 == 0
                try:
                    if not wrapped:
                        raise ValueError("deflate: no zlib header")
                    data = _ch_zlib_decode(data, zlib.MAX_WBITS, "deflate", remaining)
                except ValueError:
                    data = _ch_zlib_decode(data, -zlib.MAX_WBITS, "deflate", remaining)
            else:
                data = decoders[coding](data, remaining)
                if not isinstance(data, (bytes, bytearray)):
                    raise ValueError("%s: decoder returned %s, not bytes" % (coding, type(data).__name__))
                if len(data) > remaining:
                    raise OverflowError("%s: output exceeds %d bytes" % (coding, remaining))
                data = bytes(data)
            used += len(data)
    except (ValueError, LookupError) as exc:
        lines = str(exc).splitlines()
        what = "".join(c if c.isprintable() else "?" for c in lines[0][:160]) if lines else type(exc).__name__
        return b"", "body: " + what
    return data, None


_CH_TRANSLATION_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"), ipaddress.ip_network("2002::/16"))


def _ch_embedded_ipv4(ip):
    """The IPv4 address an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) carries, else None."""
    if ip.version != 6:
        return None
    return ip.ipv4_mapped


def _ch_address_refused(ip):
    """Why the public-only policy refuses the address `ip`, or None when it may be reached (R2-M2).

    `ip` is an address string (parsed with `ipaddress.ip_address`) or an
    IPv4Address/IPv6Address. Anything that does not parse -- including an
    int, which `ip_address` would read as an address, and a decimal, octal
    or hex IPv4 spelling -- is refused ("not an IP address"): fail closed.
    The verdict, in this order:

    1. IPv4-mapped (`::ffff:0:0/96`): ONLY the embedded IPv4 is classified,
        by rule 3. The IPv6 form's own is_reserved/is_private are not
        consulted, so `::ffff:8.8.8.8` is allowed and `::ffff:10.0.0.1`
        refused on every 3.9 patch release.
    2. Inside a _CH_TRANSLATION_PREFIXES network: refused as
        "translation prefix <net>", whatever it embeds.
    3. Otherwise refused when loopback, link-local, IPv6 site-local
        (fec0::/10, deprecated by RFC 3879 but still routed inside some
        networks, and is_global on 3.9), private, reserved, multicast,
        unspecified, or not is_global (the last catches 100.64.0.0/10,
        shared address space, which is_private misses).
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError:
            return "not an IP address"
    elif not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return "not an IP address"
    prefix = ""
    embedded = _ch_embedded_ipv4(ip)
    if embedded is not None:
        ip = embedded
        prefix = "IPv4-mapped "
    elif ip.version == 6:
        for net in _CH_TRANSLATION_PREFIXES:
            if ip in net:
                return "translation prefix %s" % net
    if ip.is_loopback:
        return prefix + "loopback"
    if ip.is_link_local:
        return prefix + "link-local"
    if ip.version == 6 and ip.is_site_local:
        return "site-local"
    if ip.is_private:
        return prefix + "private"
    if ip.is_reserved:
        return prefix + "reserved"
    if ip.is_multicast:
        return prefix + "multicast"
    if ip.is_unspecified:
        return prefix + "unspecified"
    if not ip.is_global:
        return prefix + "not globally routable"
    return None


def _ch_public_only_policy(host, port):
    """The public-only connect policy: `host` resolved ONCE, every address vetted -> the ordered address list (R2-N2).

    `host` is the normalised host _ch_split_url returned. It is resolved
    with `socket.getaddrinfo(host, port, type=SOCK_STREAM)`, once; the
    session connects only to what this returns and never resolves again,
    which closes the resolve/connect TOCTOU. Refused, each as a one-line
    ChromeClientError("policy: ..."): a name that does not resolve, an
    empty answer, and -- if ANY address is refused by _ch_address_refused,
    an unparseable one included -- the whole hop, `policy: <host> resolves
    to <addr> (<reason>)`. Otherwise the addresses in resolver order,
    duplicates dropped. The search hosts pass this block; webfetch's own
    policy calls it when allow_private is false.
    """
    shown = host if isinstance(host, str) and host.isascii() and host.isprintable() and len(host) <= 253 else repr(host)[:60]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        lines = str(exc).splitlines()
        raise ChromeClientError("policy: %s does not resolve: %s" % (shown, lines[0][:120] if lines else type(exc).__name__)) from None
    addresses = []
    for info in infos:
        addr = info[4][0] if len(info) > 4 and info[4] else None
        reason = _ch_address_refused(addr) if isinstance(addr, str) else "not an IP address"
        if reason is not None:
            raise ChromeClientError("policy: %s resolves to %s (%s)" % (shown, repr(addr)[:60] if reason == "not an IP address" else addr, reason))
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise ChromeClientError("policy: %s resolves to no address" % shown)
    return addresses


def _ch_open_socket(addresses, port, deadline):
    """A TCP socket connected to the first of `addresses` that accepts, all under ONE deadline (H2).

    `addresses` is the ordered list of IP strings a connect policy returned;
    `deadline` is an absolute `time.monotonic()` value, the call's. Each
    address is tried in order with what remains of the deadline as its
    connect timeout -- `socket.create_connection` semantics, without its
    `all_errors=` (3.11+) -- and the first to connect wins; the socket is
    returned with that timeout still set, for the caller to re-arm. This
    function NEVER resolves a name: an entry that is not an IP literal is
    a failure of that entry, not a lookup. It reads no environment
    variable (no `*_PROXY`, ever).

    Refusals: an empty list is "connect: no address to connect to"; a
    deadline that passes before an address could be tried is
    "timeout: connect deadline passed after <k> of <n> addresses"; when
    every address fails, "connect: all <n> addresses failed: <last
    error>" -- the last error, as create_connection raises it.

    Every block calls this by its global name at call time (late binding):
    a test that replaces the module's `_ch_open_socket` must reach every
    transport, so it is never bound as a default argument, an attribute
    or a closure.
    """
    addrs = list(addresses or ())
    if not addrs:
        raise ChromeClientError("connect: no address to connect to")
    last = None
    for tried, addr in enumerate(addrs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChromeClientError("timeout: connect deadline passed after %d of %d addresses" % (tried, len(addrs)))
        try:
            ip = ipaddress.ip_address(addr) if isinstance(addr, str) else None
        except ValueError:
            ip = None
        if ip is None:
            last = "%s is not an IP address" % repr(addr)[:60]
            continue
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(remaining)
            sock.connect((addr, port))
            return sock
        except socket.timeout:
            last = "%s: timed out" % addr
        except OSError as exc:
            lines = str(exc).splitlines()
            last = "%s: %s" % (addr, lines[0][:120] if lines else type(exc).__name__)
        if sock is not None:
            sock.close()
    raise ChromeClientError("connect: all %d addresses failed: %s" % (len(addrs), last))


_CH_REDIRECT_CODES = (301, 302, 303, 307, 308)


_CH_SITE_RANK = {"none": -1, "same-origin": 0, "same-site": 1, "cross-site": 2}


def _ch_transport_error(phase, exc):
    """A bare OSError from a socket (reset, broken pipe) as the one-line ChromeClientError("<phase>: transport error: ...")."""
    lines = str(exc).splitlines()
    return ChromeClientError("%s: transport error: %s" % (phase, lines[0][:120] if lines else type(exc).__name__))


def _ch_unvetted_policy(host, port):
    """The connect policy of a session built with connect_policy=None: every address, UNVETTED -- tests only.

    Resolves `host` once with `socket.getaddrinfo(host, port,
    type=SOCK_STREAM)` and returns every address in resolver order,
    duplicates dropped; a name that does not resolve or resolves to nothing
    is refused ("policy: ..."). It classifies nothing, so it admits
    loopback, private and metadata addresses: no production host uses it
    (the search hosts pass _ch_public_only_policy, webfetch its own).
    """
    shown = host if isinstance(host, str) and host.isascii() and host.isprintable() and len(host) <= 253 else repr(host)[:60]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        lines = str(exc).splitlines()
        raise ChromeClientError("policy: %s does not resolve: %s" % (shown, lines[0][:120] if lines else type(exc).__name__)) from None
    addresses = []
    for info in infos:
        addr = info[4][0] if len(info) > 4 and info[4] else None
        if isinstance(addr, str) and addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise ChromeClientError("policy: %s resolves to no address" % shown)
    return addresses


class _ChDeadlineSocket:
    """A connected socket re-armed from ONE absolute deadline before every operation (D11).

    `_ChDeadlineSocket(sock, deadline)`: `deadline` is a `time.monotonic()`
    value the session moves to each call's own deadline before it uses a
    pooled connection. `sendall` and `recv` first set the socket timeout to
    what remains of it, so a connect, a handshake and every read of one call
    share one budget however many reads it takes; once it has passed they
    raise `socket.timeout`, which every layer above turns into
    ChromeClientError("timeout: ..."). `close()` closes the socket.
    """

    def __init__(self, sock, deadline):
        self._sock = sock
        self.deadline = deadline

    def _arm(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("the call deadline passed")
        self._sock.settimeout(remaining)

    def sendall(self, data):
        self._arm()
        self._sock.sendall(data)

    def recv(self, n):
        self._arm()
        return self._sock.recv(n)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


class _ChFallbackConnection(http.client.HTTPSConnection):
    """The verified stdlib transport's one connection (the session DEFAULT and the TLS 1.2 fallback, D10, D15, R2-M4).

    `_ChFallbackConnection(host, port, addresses, context, deadline,
    label="tls12-fallback")`. It is ALSO the verified default transport: a
    `transport="verified"` session issues every https hop through it with
    `label="verified"`; a `transport="chrome"` session uses it only for the
    TLS 1.2 fallback, labelled "tls12-fallback". Any other label is refused
    ("tls12-fallback: ..."). `addresses` is the ordered list the SAME hop's
    connect policy returned;
    `context` the ssl.SSLContext the session's `ssl_context_factory()`
    built; `deadline` the call's absolute `time.monotonic()` value. The
    context must verify: CERT_REQUIRED and check_hostname, else the
    constructor refuses ("<label>: the ssl context does not verify
    ..."), because every response from this path says `cert_verified=True`;
    and its minimum_version must be TLS 1.2 or later (MINIMUM_SUPPORTED
    counts as below), else "<label>: the ssl context allows a protocol
    version below TLS 1.2" -- _ChSession._fallback raises the floor of the
    context its factory built before it gets here, since Python 3.9's
    create_default_context leaves it at MINIMUM_SUPPORTED.

    `connect()` is `_ch_open_socket(addresses, port, deadline)`, looked up by
    its global name at call time, then `context.wrap_socket(sock,
    server_hostname=host, suppress_ragged_eofs=False)` -- a TCP FIN without
    close_notify is an ssl error, never a quiet end of the body -- and
    `read_body` refuses a body shorter than its declared Content-Length
    ("<label>: the body ended after <n> of its <m> Content-Length bytes"),
    which http.client's read(amt) would return without complaint. The
    name is never resolved again, and
    http.client's own `socket.create_connection` and tunnel code are never
    reached. Nothing here reads the environment (no urllib, no `*_PROXY`).
    The connection is used for ONE request and closed by `read_body` or
    `close()`; it is never pooled.

    `start(fields, body) -> (status, fields, http_version)` sends the
    request: the head is the one _ChH1Connection.request_head builds from
    the _ch_profile_headers list (the h1 order and spelling, Host and
    Connection first, no Priority), handed to `putrequest(...,
    skip_host=True, skip_accept_encoding=True)` and `putheader` line by
    line, so http.client adds no field of its own and its header check
    stays a second line of defence. `read_body(limit)` reads the body in
    chunks, ChromeBodyTooLarge(limit) past `limit`, then closes. Every
    socket operation is re-armed with what remains of the deadline.

    Refusals, each one line, the prefix is the LABEL (shown here as
    <label>: "verified" or "tls12-fallback"): a certificate or host-name
    failure is "<label>: certificate verify failed: <reason>"; any
    ValueError (http.client's "Invalid header name/value", an invalid URL)
    is "<label>: <message up to the first quoted value>", never a bare
    ValueError and never the value itself; a malformed response is
    "<label>: malformed response (<exception type>)" with no peer
    byte; an interim 1xx other than 100, or a header carrying CR, LF or
    NUL, is refused; a timeout keeps the timeout: phase first,
    "timeout: <label> timed out"; another ssl error "<label>: tls:
    <reason>"; a socket error "<label>: transport error: ...".
    """

    RECV_BYTES = 65536

    def __init__(self, host, port, addresses, context, deadline, label="tls12-fallback"):
        if label != "verified" and label != "tls12-fallback":
            raise ChromeClientError("tls12-fallback: label %s is neither verified nor tls12-fallback" % repr(label)[:40])
        self._label = label
        if not isinstance(context, ssl.SSLContext) or context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise ChromeClientError("%s: the ssl context does not verify the certificate and the host name" % label)
        if int(context.minimum_version) < int(ssl.TLSVersion.TLSv1_2):
            raise ChromeClientError("%s: the ssl context allows a protocol version below TLS 1.2" % label)
        try:
            super().__init__(host, port, context=context)
        except ValueError as exc:
            raise ChromeClientError("%s: %s" % (label, self._one_line(str(exc)))) from None
        self._addresses = list(addresses)
        self._deadline = deadline
        self._response = None
        self._tls = None
        self._raw = None

    @staticmethod
    def _one_line(text):
        """The first line of `text`, cut before the first quoted value (never echo a header or URL), printable, bounded."""
        lines = str(text).splitlines()
        line = lines[0] if lines else ""
        for mark in ("'", '"'):
            cut = line.find(mark)
            if cut >= 0:
                line = line[:cut]
        line = line.rstrip()
        if line.endswith(" b"):
            line = line[:-2]
        line = line.rstrip(" (:.,")
        line = "".join(c if c.isprintable() else "?" for c in line[:160])
        return line or "refused"

    @staticmethod
    def _refusal(exc, label):
        """`exc` as the one-line ChromeClientError of this path, prefixed with `label`, or None when it is to propagate unchanged."""
        if isinstance(exc, ChromeClientError):
            return None
        if isinstance(exc, ssl.SSLCertVerificationError):
            what = getattr(exc, "verify_message", None) or "the chain or the host name did not verify"
            return ChromeClientError("%s: certificate verify failed: %s" % (label, _ChFallbackConnection._one_line(what)))
        if isinstance(exc, socket.timeout):
            return ChromeClientError("timeout: %s timed out" % label)
        if isinstance(exc, ssl.SSLError):
            reason = exc.reason if isinstance(exc.reason, str) and exc.reason else type(exc).__name__
            return ChromeClientError("%s: tls: %s" % (label, _ChFallbackConnection._one_line(reason)))
        if isinstance(exc, http.client.InvalidURL):
            return ChromeClientError("%s: invalid URL" % label)
        if isinstance(exc, http.client.HTTPException):
            return ChromeClientError("%s: malformed response (%s)" % (label, type(exc).__name__))
        if isinstance(exc, ValueError):
            return ChromeClientError("%s: %s" % (label, _ChFallbackConnection._one_line(str(exc))))
        if isinstance(exc, OSError):
            return _ch_transport_error(label, exc)
        return None

    def _arm(self):
        """Set the socket timeout to what remains of the deadline; socket.timeout once it has passed."""
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("the call deadline passed")
        if self._raw is not None:
            self._raw.settimeout(remaining)

    def connect(self):
        """`_ch_open_socket` over the policy's list, then the verified TLS handshake under the same deadline."""
        sock = _ch_open_socket(self._addresses, self.port, self._deadline)
        try:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("the call deadline passed")
            sock.settimeout(remaining)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host, suppress_ragged_eofs=False)
        except BaseException:
            sock.close()
            raise
        self._raw = self.sock
        cipher = self.sock.cipher()
        self._tls = {"group": None, "cipher": cipher[0] if cipher else None, "alpn": self.sock.selected_alpn_protocol(), "alps": False, "hrr": False, "version": self.sock.version()}

    def tls_info(self):
        """The connection's info dict (the Chrome path's keys plus `version`), taken at the handshake; None before one."""
        return self._tls

    def start(self, fields, body=None):
        """Send one request built from the _ch_profile_headers list and read the final head: (status, fields, version)."""
        try:
            head = _ChH1Connection(None).request_head(fields, body)
        except ChromeClientError as exc:
            raise ChromeClientError("%s: %s" % (self._label, exc)) from None
        lines = head.decode("latin-1").split("\r\n")
        method, rest = lines[0].split(" ", 1)
        target = rest.rsplit(" ", 1)[0]
        try:
            self.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            for line in lines[1:]:
                if line:
                    name, _sep, value = line.partition(": ")
                    self.putheader(name, value)
            self.endheaders(body)
            self._arm()
            response = self.getresponse()
            if 100 <= response.status < 200:
                raise ChromeClientError("%s: unexpected interim response %d" % (self._label, response.status))
            pairs = response.getheaders()
            for name, value in pairs:
                if "\r" in value or "\n" in value or "\0" in value:
                    raise ChromeClientError("%s: header contains CR, LF or NUL" % self._label)
        except BaseException as exc:
            self.close()
            refusal = self._refusal(exc, self._label)
            if refusal is None:
                raise
            raise refusal from None
        self._response = response
        version = "HTTP/1.0" if response.version == 10 else "HTTP/1.1"
        return response.status, list(pairs), version

    def read_body(self, limit):
        """The whole body under `limit` (ChromeBodyTooLarge past it), read in chunks; the connection is closed after."""
        response = self._response
        if response is None:
            raise ChromeClientError("%s: no response head has been read" % self._label)
        body = bytearray()
        declared = response.length
        try:
            if declared is not None and declared > limit:
                raise ChromeBodyTooLarge(limit)
            while not response.isclosed():
                self._arm()
                chunk = response.read(min(self.RECV_BYTES, limit - len(body) + 1))
                if not chunk:
                    break
                body += chunk
                if len(body) > limit:
                    raise ChromeBodyTooLarge(limit)
            if declared is not None and len(body) != declared:
                raise ChromeClientError("%s: the body ended after %d of its %d Content-Length bytes" % (self._label, len(body), declared))
        except BaseException as exc:
            refusal = self._refusal(exc, self._label)
            if refusal is None:
                raise
            raise refusal from None
        finally:
            self.close()
        return bytes(body)


class _ChSession:
    """The blocking, requests-like client around the sans-IO layers (FR-3, D8, D12, G-b).

    Built by _ch_session_new, which documents the arguments; every
    collaborator is injected and nothing is read from the environment
    (no `*_PROXY`, no `os.environ`, no urllib.request). Attributes kept as
    given, never reassigned: `connect_policy`, `tls12_fallback`,
    `allow_downgrade`, `transport`; also `decoders`, `timeout`, `max_bytes`, `cookies`
    (the session's _ChCookieJar) and `last_navigation_url` (the final URL
    of the most recent navigate-mode response, None before one; L8).

    `get(url, params=None, headers=None, timeout=None, mode="navigate",
    referer=None, on_headers=None)`, `head(url, ...)` and `post(url,
    data=None, ...)` (both cors: HEAD, and a POST whose dict/sequence
    `data` is urlencoded under the captured content-type), and
    `request(method, url, headers=None, body=None, timeout=None,
    mode="navigate", referer=None, on_headers=None)` return a finished
    _ChResponse. `close()` closes every pooled connection.

    One call, in order:

    - `_ch_check_caller_headers(headers)` FIRST, before any name is
        resolved or any byte written; then the URL is split
        (`url: ...` refusals) and ONE deadline is set, `timeout` (the
        session's when None) seconds from now, shared by every policy
        answer's connect, handshake, write and read of every hop.
    - Per hop: the scheme and the https->http downgrade are checked (a
        redirect's `redirect: scheme <s> refused`, `redirect: https to
        http downgrade refused` unless allow_downgrade) BEFORE the
        policy, so no policy can grant either; then `connect_policy(host,
        port)` is called -- on every hop and every retry attempt, even
        when a pooled connection is reused -- and a NEW connection is
        opened only over the list it returned, by
        `_ch_open_socket(addresses, port, deadline)` looked up by its
        global name at call time (late binding: a test that replaces it
        reaches every transport). https is TLS 1.3 (h2 or HTTP/1.1 by
        ALPN), http is cleartext HTTP/1.1.
    - The pool is keyed (scheme, host, port): h2 streams share one
        connection and its HPACK encoder; an h1 connection is reused only
        after a response read to its end (_ChH1Connection.usable).
    - Headers per hop: caller headers bind to the ORIGIN of the call's
        URL; from the first hop whose origin differs they are dropped for
        the rest of the chain, caller Cookie crumbs included (FR-16). The
        cookie slot carries the jar's cookies for the hop, then the
        caller's crumbs. `Referer` is `_ch_referer_for(referer, hop)`
        (none without a referer). `sec-fetch-site` is the worst value of
        the chain so far against the initiator -- the referer, or for a
        cors call without one the call's own URL; a navigation without a
        referer is "none" on every hop. A cors hop whose origin differs
        from the initiator's carries `Origin` (the initiator's); a
        same-origin fetch carries none (22/22 captures).
    - Redirects (_CH_REDIRECT_CODES with a Location): Set-Cookie is
        stored, the body read and discarded, and `Location` resolved
        with urljoin + urldefrag. 303 (except for GET/HEAD) and 301/302
        after a POST become a GET without a body; 307/308 keep the
        method and body, cross-origin too (Chrome fidelity, ADR 0026).
        More than CH_MAX_REDIRECTS is `redirect: more than 20 hops`.
    - The final response: its Set-Cookie is stored, then `on_headers(status,
        headers, url)` runs once, BEFORE any body byte is read or any size
        decided; if it raises, the stream is cancelled (RST_STREAM CANCEL
        on h2, the h1 connection closed) and the exception propagates
        unchanged. The body is read under the wire limit -- `max_bytes`,
        or CH_MAX_BODY_BYTES when 0 -- and decoded by _ch_decode_body
        under `max_bytes` or CH_DEFAULT_DECODE_CAP; either limit raises
        ChromeBodyTooLarge(limit). Limits are module globals read at call
        time.
    - Retry, once per hop, on a fresh connection re-vetted by the policy:
        a GET/HEAD whose REUSED connection failed before the response
        head (the server closed an idle keep-alive, reset, an idle
        GOAWAY); any method whose failure carries `retry_safe` (a GOAWAY
        whose last-stream-id excludes the stream, REFUSED_STREAM: RFC
        9113 section 8.7 proves it was not processed). A timeout is never
        retried.

    - Transport (D15): `transport="verified"` (the DEFAULT) sends every
        https hop, after the policy call, through _ChFallbackConnection
        labelled "verified" (the stdlib ssl stack, `ssl_context_factory()`
        as the verifying context, HTTP/1.1, the same header list in the h1
        order): never pooled, never retried inside the session, and its
        refusals begin "verified:" ("timeout: verified timed out").
        `tls12_fallback` is inert under it. `transport="chrome"` is the
        Chrome TLS engine described above; an http hop is cleartext h1 in
        both. Any other value is refused in the constructor. The response's
        `transport` is the chain's WEAKEST hop (chrome, then cleartext, then
        the hop's own label), `cert_verified` True only for a verified or
        tls12-fallback chain, `impersonated` the final hop's (_ChResponse).

    - TLS 1.2 (D10): a new https connection whose server chose TLS 1.2
        raises ChromeTls12Error from the handshake. Without
        `tls12_fallback` it propagates. With it, `_hop` re-issues THAT hop
        ONCE through _ChFallbackConnection, over the SAME address list the
        hop's policy call returned (never resolved again), with
        `ssl_context_factory()` as the verifying context and the same
        header list, spelled in the h1 order. on_headers, the wire and
        decoded limits, cookies and redirects are handled exactly as on
        the Chrome path; the response says `impersonated=False`,
        `cert_verified=True`; the connection is never pooled, and the next
        hop of a redirect tries the Chrome path first again. A failure of
        the fallback is not retried.
    """

    def __init__(self, decoders=None, connect_policy=None, timeout=30, max_bytes=0, log=None, tls12_fallback=False, allow_downgrade=False, rand=None, ssl_context_factory=None, transport="verified"):
        if not isinstance(transport, str) or (transport != "verified" and transport != "chrome"):
            raise ChromeClientError("transport: must be 'verified' or 'chrome'")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not timeout > 0:
            raise ChromeClientError("timeout: the timeout must be a positive number of seconds")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            raise ChromeClientError("body: max_bytes must be an int >= 0")
        if connect_policy is not None and not callable(connect_policy):
            raise ChromeClientError("policy: connect_policy must be callable")
        self.decoders = decoders
        self.connect_policy = connect_policy
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.tls12_fallback = tls12_fallback
        self.allow_downgrade = allow_downgrade
        self.transport = transport
        self.cookies = _ChCookieJar()
        self.last_navigation_url = None
        self._log = log
        self._rand = rand if rand is not None else os.urandom
        self._ssl_context_factory = ssl_context_factory if ssl_context_factory is not None else ssl.create_default_context
        self._pool = {}

    def get(self, url, params=None, headers=None, timeout=None, mode="navigate", referer=None, on_headers=None):
        """GET `url`, `params` appended with urllib.parse.urlencode; navigate mode unless `mode="cors"`."""
        if params and isinstance(url, str):
            base = urllib.parse.urldefrag(url)[0]
            query = urllib.parse.urlencode(params)
            if base.endswith("?") or base.endswith("&"):
                url = base + query
            else:
                url = base + ("&" if "?" in base else "?") + query
        return self.request("GET", url, headers=headers, timeout=timeout, mode=mode, referer=referer, on_headers=on_headers)

    def head(self, url, headers=None, timeout=None, referer=None, on_headers=None):
        """HEAD `url` with the captured cors-HEAD profile."""
        return self.request("HEAD", url, headers=headers, timeout=timeout, mode="cors", referer=referer, on_headers=on_headers)

    def post(self, url, data=None, headers=None, timeout=None, referer=None, on_headers=None):
        """A cors POST: a dict or pair sequence is urlencoded (the captured form content-type), bytes/str sent as given."""
        if data is None:
            body = b""
        elif isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = urllib.parse.urlencode(data).encode("ascii")
        return self.request("POST", url, headers=headers, body=body, timeout=timeout, mode="cors", referer=referer, on_headers=on_headers)

    def request(self, method, url, headers=None, body=None, timeout=None, mode="navigate", referer=None, on_headers=None):
        """One call, redirects followed: the final _ChResponse (see the class docstring for every rule)."""
        _ch_check_caller_headers(headers)
        if not isinstance(method, str) or not method or not all((c.isascii() and c.isalnum()) or c in _CH_TCHAR_SYMBOLS for c in method):
            raise ChromeClientError("headers: invalid method")
        method = method.upper()
        if mode != "navigate" and mode != "cors":
            raise ChromeClientError("headers: mode %s is neither navigate nor cors" % repr(mode)[:40])
        if body is not None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            if not isinstance(body, (bytes, bytearray)):
                raise ChromeClientError("body: the request body must be bytes or str")
            body = bytes(body)
            if mode != "cors":
                raise ChromeClientError("headers: a request body is sent only in cors mode")
        if on_headers is not None and not callable(on_headers):
            raise ChromeClientError("headers: on_headers must be callable")
        wait = self.timeout if timeout is None else timeout
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not wait > 0:
            raise ChromeClientError("timeout: the timeout must be a positive number of seconds")
        if referer is not None and not isinstance(referer, str):
            raise ChromeClientError("headers: referer must be a str")
        current = urllib.parse.urldefrag(url)[0] if isinstance(url, str) else url
        _ch_split_url(current)
        deadline = time.monotonic() + wait
        caller = _ch_header_pairs(headers)
        call_origin = _ch_origin_of(current)
        initiator = referer if referer else (current if mode == "cors" else None)
        initiator_origin = _ch_origin_of(initiator) if initiator else None
        worst = None
        keep_caller = True
        redirects = 0
        chain = None
        while True:
            scheme, host, port, target = _ch_split_url(current)
            hop_origin = _ch_origin_of(current)
            if hop_origin != call_origin:
                keep_caller = False
            site = "none" if initiator is None else _ch_sec_fetch_site(initiator, current)
            if worst is None or _CH_SITE_RANK[site] > _CH_SITE_RANK[worst]:
                worst = site
            origin = None
            if mode == "cors" and initiator_origin is not None and initiator_origin != hop_origin:
                origin = _ch_origin_text(initiator_origin)
            shown = "[%s]" % host if ":" in host else host
            authority = shown if port == (443 if scheme == "https" else 80) else "%s:%d" % (shown, port)
            ref = _ch_referer_for(referer, current) if referer else None
            cookie = self.cookies.header_for(scheme, host, target)
            fields = _ch_profile_headers(mode, method, authority, target, sec_fetch_site=worst, origin=origin, referer=ref, cookie=cookie, extra=caller if keep_caller else None, content_length=len(body) if body is not None else None, scheme=scheme)
            handle = self._hop(scheme, host, port, method, fields, body, deadline)
            via = handle.get("via") or ("cleartext" if scheme == "http" else "chrome")
            chain = "chrome" if "chrome" in (chain, via) else "cleartext" if "cleartext" in (chain, via) else via
            headers_in = _ChHeaders(handle["fields"])
            self.cookies.ingest(scheme, host, target, headers_in.get_all("set-cookie"))
            status = handle["status"]
            location = headers_in.get_all("location")
            if status in _CH_REDIRECT_CODES and location:
                self._discard(handle)
                if redirects >= CH_MAX_REDIRECTS:
                    raise ChromeClientError("redirect: more than %d hops" % CH_MAX_REDIRECTS)
                redirects += 1
                try:
                    nxt = urllib.parse.urldefrag(urllib.parse.urljoin(current, location[0]))[0]
                    next_scheme = urllib.parse.urlsplit(nxt).scheme.lower()
                except ValueError:
                    raise ChromeClientError("redirect: malformed Location") from None
                if next_scheme not in ("http", "https"):
                    raise ChromeClientError("redirect: scheme %s refused" % (next_scheme[:40] or "(none)"))
                if scheme == "https" and next_scheme == "http" and not self.allow_downgrade:
                    raise ChromeClientError("redirect: https to http downgrade refused")
                if (status == 303 and method not in ("GET", "HEAD")) or (status in (301, 302) and method == "POST"):
                    method = "GET"
                    body = None
                if self._log is not None:
                    self._log("session: redirect hop=%d status=%d" % (redirects, status))
                current = nxt
                continue
            if on_headers is not None:
                try:
                    on_headers(status, headers_in, current)
                except BaseException:
                    self._abandon(handle)
                    raise
            raw = self._body(handle)
            cap = self.max_bytes if self.max_bytes > 0 else CH_DEFAULT_DECODE_CAP
            try:
                content, decode_error = _ch_decode_body(headers_in, raw, self.decoders, cap)
            except OverflowError:
                raise ChromeBodyTooLarge(cap) from None
            response = _ChResponse(status, headers_in, current, content, decode_error, handle["version"], handle["conn"]["tls"], impersonated=via in ("chrome", "cleartext"), cert_verified=chain in ("verified", "tls12-fallback"), transport=chain)
            if mode == "navigate":
                self.last_navigation_url = current
            return response

    def close(self):
        """Close every pooled connection; a later call opens new ones (through the policy)."""
        for conn in list(self._pool.values()):
            self._drop(conn)
        self._pool = {}

    def _wire_limit(self):
        return self.max_bytes if self.max_bytes > 0 else CH_MAX_BODY_BYTES

    def _vet(self, host, port):
        """The policy's ordered address list for this hop (the unvetted default when connect_policy is None)."""
        if self.connect_policy is None:
            addresses = _ch_unvetted_policy(host, port)
        else:
            addresses = self.connect_policy(host, port)
        if isinstance(addresses, (str, bytes)) or not addresses:
            raise ChromeClientError("policy: the connect policy returned no address list")
        return list(addresses)

    def _hop(self, scheme, host, port, method, fields, body, deadline):
        """Policy, connection, request and response head of one hop: a handle; one retry (class docstring)."""
        retried = False
        while True:
            addresses = self._vet(host, port)
            if self.transport == "verified" and scheme == "https":
                return self._fallback(host, port, addresses, fields, body, deadline, "verified")
            try:
                conn = self._connection(scheme, host, port, addresses, deadline, retried)
            except ChromeTls12Error:
                if not self.tls12_fallback:
                    raise
                return self._fallback(host, port, addresses, fields, body, deadline)
            reused = conn["uses"] > 0
            try:
                return self._start(conn, method, fields, body, deadline)
            except ChromeClientError as exc:
                if retried or not self._retryable(exc, method, reused):
                    raise
            retried = True

    def _fallback(self, host, port, addresses, fields, body, deadline, label="tls12-fallback"):
        """The hop issued ONCE through _ChFallbackConnection over the SAME address list (D10, D15): a handle whose "via" is `label`."""
        if self._log is not None:
            self._log("session: %s transport" % label)
        try:
            context = self._ssl_context_factory()
            if isinstance(context, ssl.SSLContext) and int(context.minimum_version) < int(ssl.TLSVersion.TLSv1_2):
                context.minimum_version = ssl.TLSVersion.TLSv1_2
        except (OSError, ValueError) as exc:
            raise ChromeClientError("%s: the ssl context could not be built: %s" % (label, _ChFallbackConnection._one_line(str(exc) or type(exc).__name__))) from None
        fallback = _ChFallbackConnection(host, port, addresses, context, deadline, label)
        status, head_fields, version = fallback.start(fields, body)
        return {"conn": {"tls": fallback.tls_info()}, "sid": None, "status": status, "fields": head_fields, "version": version, "fallback": fallback, "via": label}

    @staticmethod
    def _retryable(exc, method, reused):
        if isinstance(exc, ChromeBodyTooLarge) or str(exc).startswith("timeout:"):
            return False
        if getattr(exc, "retry_safe", False):
            return True
        return reused and method in ("GET", "HEAD")

    @staticmethod
    def _usable(conn):
        if conn["h2"] is not None:
            return conn["h2"].usable()
        return conn["h1"].usable()

    def _connection(self, scheme, host, port, addresses, deadline, fresh):
        """The pooled connection for (scheme, host, port) when usable and not `fresh`, else a new one over `addresses`."""
        key = (scheme, host, port)
        conn = self._pool.get(key)
        if conn is not None and (fresh or not self._usable(conn)):
            self._drop(conn)
            conn = None
        if conn is None:
            conn = self._open(scheme, host, port, addresses, deadline)
            self._pool[key] = conn
        return conn

    def _open(self, scheme, host, port, addresses, deadline):
        """A new connection: `_ch_open_socket` over the policy's list, then TLS 1.3 + ALPN for https."""
        sock = _ch_open_socket(addresses, port, deadline)
        wrapped = _ChDeadlineSocket(sock, deadline)
        conn = {"key": (scheme, host, port), "sock": wrapped, "stream": wrapped, "tls": None, "h2": None, "h1": None, "preface": False, "uses": 0}
        if scheme == "http":
            conn["h1"] = _ChH1Connection(wrapped, None, self._log)
            return conn
        tls = _ChTls(host, None, self._rand, self._log)
        stream = _ChTlsStream(wrapped, tls)
        try:
            alpn = stream.handshake()
            if alpn == "h2":
                h2 = _ChH2Connection(None, self._log)
                if tls.alps_negotiated:
                    h2.apply_alps(tls.alps_server_settings)
                conn["h2"] = h2
            else:
                conn["h1"] = _ChH1Connection(stream, None, self._log)
        except ChromeClientError:
            wrapped.close()
            raise
        except OSError as exc:
            wrapped.close()
            raise _ch_transport_error("tls", exc) from None
        conn["stream"] = stream
        conn["tls"] = {"group": tls.group, "cipher": tls.cipher, "alpn": tls.alpn, "alps": tls.alps_negotiated, "hrr": tls.hrr}
        return conn

    def _drop(self, conn):
        """Take `conn` out of the pool and close it (best effort)."""
        if self._pool.get(conn["key"]) is conn:
            del self._pool[conn["key"]]
        if conn["h2"] is not None:
            conn["h2"].close()
            conn["stream"].close()
        else:
            conn["h1"].close()

    def _settle(self, conn):
        """After a failure: drop the connection unless it can still carry a new request."""
        if not self._usable(conn):
            self._drop(conn)

    def _start(self, conn, method, fields, body, deadline):
        """Write the request and read up to the final response head: {conn, sid, status, fields, version}."""
        conn["sock"].deadline = deadline
        conn["uses"] += 1
        h2 = conn["h2"]
        try:
            if h2 is not None:
                out = b""
                if not conn["preface"]:
                    out = h2.preface()
                    conn["preface"] = True
                sid, data = h2.open_stream(fields, body, self._wire_limit())
                conn["stream"].sendall(out + data)
                while True:
                    head = h2.response(sid)
                    if head is not None:
                        break
                    if h2.done(sid):
                        raise ChromeClientError("h2: stream %d ended without a response" % sid)
                    self._pump(conn)
                return {"conn": conn, "sid": sid, "status": head[0], "fields": head[1], "version": "HTTP/2"}
            h1 = conn["h1"]
            h1.send_request(fields, body)
            status, _reason, head_fields = h1.read_head()
            return {"conn": conn, "sid": None, "status": status, "fields": head_fields, "version": h1.http_version}
        except ChromeClientError:
            self._settle(conn)
            raise
        except OSError as exc:
            self._settle(conn)
            raise _ch_transport_error("h2" if h2 is not None else "h1", exc) from None

    def _pump(self, conn):
        """One read into the h2 connection; its answer (ACKs, WINDOW_UPDATE, RST_STREAM, GOAWAY) written back."""
        h2 = conn["h2"]
        stream = conn["stream"]
        data = stream.recv(65536)
        if not data:
            h2.close()
            return
        try:
            out = h2.feed(data)
        except ChromeClientError:
            tail = h2.data_to_send()
            if tail:
                try:
                    stream.sendall(tail)
                except OSError:
                    pass
            raise
        if out:
            stream.sendall(out)

    def _body(self, handle):
        """The whole wire body of a started response under the wire limit (ChromeBodyTooLarge past it)."""
        if handle.get("fallback") is not None:
            return handle["fallback"].read_body(self._wire_limit())
        conn = handle["conn"]
        h2 = conn["h2"]
        try:
            if h2 is None:
                return conn["h1"].read_body(self._wire_limit())
            sid = handle["sid"]
            body = bytearray()
            while True:
                body += h2.pop_body(sid)
                if h2.done(sid):
                    body += h2.pop_body(sid)
                    return bytes(body)
                self._pump(conn)
        except ChromeClientError:
            self._settle(conn)
            raise
        except OSError as exc:
            self._settle(conn)
            raise _ch_transport_error("h2" if h2 is not None else "h1", exc) from None

    def _discard(self, handle):
        """A redirect's body: read (so an h1 connection stays reusable) and dropped; a body past the limit is abandoned."""
        try:
            self._body(handle)
        except ChromeBodyTooLarge:
            pass

    def _abandon(self, handle):
        """on_headers refused the response: RST_STREAM(CANCEL) on h2, close the h1 or fallback connection (never reused)."""
        if handle.get("fallback") is not None:
            handle["fallback"].close()
            return
        conn = handle["conn"]
        h2 = conn["h2"]
        if h2 is None:
            self._drop(conn)
            return
        try:
            data = h2.cancel(handle["sid"])
            if data:
                conn["stream"].sendall(data)
        except OSError:
            self._drop(conn)


def _ch_session_new(decoders=None, connect_policy=None, timeout=30, max_bytes=0, log=None, tls12_fallback=False, allow_downgrade=False, rand=None, ssl_context_factory=None, transport="verified"):
    """The one constructor hosts call: a _ChSession (D8, D12, G-b); no argument is read from the environment.

    - `decoders`: {"br": fn, "zstd": fn}, each `fn(data, max_output) ->
        bytes` raising OverflowError past max_output, ValueError when
        corrupt, LookupError when its library is absent (hosts inject
        _brotli_decompress and the zstd one; None: gzip/deflate only).
    - `connect_policy(host, port) -> [ip, ...]`: called on every hop and
        every retry attempt with the normalised host; the ordered list is
        all the session may connect to, or it raises ChromeClientError to
        refuse. None is _ch_unvetted_policy, which vets NOTHING (tests
        only; the search hosts pass _ch_public_only_policy).
    - `timeout`: seconds per call (connect + handshake + every read, all
        hops), overridable per call. `max_bytes`: the wire AND decoded
        body limit, 0 meaning CH_MAX_BODY_BYTES / CH_DEFAULT_DECODE_CAP.
    - `log(str)`: structure-only lines (ADR 0011), None for silence.
    - `tls12_fallback`: retry a hop whose server chose TLS 1.2 once through
        the verified stdlib path (_ChFallbackConnection); False lets
        ChromeTls12Error propagate. `allow_downgrade`: follow an https->http redirect
        (R2-N3); no policy can grant it.
    - `rand(n) -> bytes`: os.urandom when None (tests inject a
        deterministic one; no production path passes it).
    - `ssl_context_factory() -> ssl.SSLContext`: for the verified
        transport and the TLS 1.2 fallback; `ssl.create_default_context`
        called with NO argument when None, so the default can never load a
        test CA.
    - `transport`: "verified" (the DEFAULT: every https hop through the
        verified stdlib transport, _ChFallbackConnection, certificate and
        host name checked, not impersonated; `tls12_fallback` inert) or
        "chrome" (the Chrome TLS engine: impersonated, certificate NOT
        verified). Anything else is ChromeClientError("transport: ...").
    """
    return _ChSession(decoders, connect_policy, timeout, max_bytes, log, tls12_fallback, allow_downgrade, rand, ssl_context_factory, transport)
# END GENERATED: 75c1b44f7ba6

_DECODERS = {"br": _brotli_decompress, "zstd": _zstd_decompress}


# ---------------------------------------------------------------------------
# Search (generated)
# ---------------------------------------------------------------------------

# Web search, generated from Scripts/_mcp_websearch.py: the DDG lite and Bing
# parsers, the two block signals, search_ddg / search_bing and the run_web
# loop, shared with search_duckduckgo.py. Taken whole (every block, in source
# order). The session is never created inside the region: _create_session and
# the with_session hook below are this file's own.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
# BEGIN GENERATED: _mcp_websearch.py :: _normalize, _WEB_LINE_SEPARATORS, _web_clean, _web_line, _WEB_URL_SAFE, _web_url, decode_duckduckgo_url, _raw_href, _has_class, _LiteParser, parse_lite_results, _decode_bing_url, _VOID_TAGS, _H, _LISTING, _FONTSTYLE, _start_close, _START_CLOSE, _end_priority, _END_PRIORITY, _END_PRIORITY_DEFAULT, _Node, _TREE_MAX_DEPTH, _TREE_SCAN_BUDGET, _TreeBuilder, _child_elements, _descendant_text, _iter_elements, parse_bing_results, warmup_session, _DDG_CHALLENGE_MARKERS, _ddg_blocked, search_ddg, _bing_blocked, search_bing, run_web, format_web_results
def _normalize(text):
    return re.sub(r'\s+', ' ', text).strip() if text else ""


_WEB_LINE_SEPARATORS = "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\x85"


def _web_clean(text, keep):
    """*text* with every invisible or display-steering code point dropped.

    A character in *keep* (e.g. "\\t\\n") survives; U+2028 U+2029 U+0085 become
    "\\n". Printable ASCII is the fast path; the rest is judged by category.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if 0x20 <= code < 0x7f or ch in keep:
            out.append(ch)
        elif ch in _WEB_LINE_SEPARATORS:
            out.append("\n")
        elif 0xfe00 <= code <= 0xfe0f or 0xe0000 <= code <= 0xe01ef:
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def _web_line(text):
    """A single-line field: _web_clean, then every whitespace run one space (F2)."""
    if text is None:
        return ""
    return " ".join(_web_clean(str(text), "\t\n\r").split())


_WEB_URL_SAFE = "!#$%&'()*+,-./:;=?@[]^_{|}~"


def _web_url(url):
    """A result URL safe to render, or None when it must not be a link (F3).

    Controls and invisibles are dropped first; then only an absolute http(s)
    URL with a host survives. A non-ASCII host is rendered in its IDNA
    (punycode) form so a homoglyph cannot pose as another site -- None when it
    has none -- and a non-ASCII path, query or fragment is percent-encoded.
    """
    if not isinstance(url, str):
        return None
    url = _web_clean(url, " ").replace("\n", "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    head = len(parts.scheme) + 3
    if url[len(parts.scheme):head] != "://" or url[head:head + len(parts.netloc)] != parts.netloc:
        return None
    netloc = parts.netloc
    if not netloc.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        userinfo = netloc.rpartition("@")[0]
        netloc = (urllib.parse.quote(userinfo, safe=_WEB_URL_SAFE) + "@" if "@" in parts.netloc else "") + host + ("" if port is None else ":%d" % port)
    return parts.scheme + "://" + urllib.parse.quote(netloc, safe=_WEB_URL_SAFE) + urllib.parse.quote(url[head + len(parts.netloc):], safe=_WEB_URL_SAFE)


def decode_duckduckgo_url(ddg_url):
    match = re.search(r'uddg=([^&]+)', ddg_url)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    if ddg_url.startswith('http'):
        return ddg_url
    if ddg_url.startswith('//'):
        return 'https:' + ddg_url
    return ddg_url


def _raw_href(start_tag):
    """The href value exactly as written in a raw start tag, entities NOT decoded.

    The regex parser this replaced captured `href=['"]([^'"]+)['"]` from the raw
    markup, so a direct (non-uddg) link kept its `&amp;`; html.parser decodes
    attribute values, so the raw tag is read instead. One left-to-right pass.

    It reads the ATTRIBUTE named href (F14), never the first "href=" substring:
    a `data-href=` or an `href=` inside another attribute's quoted value is
    skipped. The first href attribute decides; a quoted non-empty value is
    returned, an unquoted or empty one is None, as the regex had it.
    """
    space = " \t\n\r\f"
    n = len(start_tag)
    i = 1
    while i < n and start_tag[i] not in space + "/>":
        i += 1  # the tag name
    while i < n:
        while i < n and start_tag[i] in space + "/":
            i += 1
        start = i
        while i < n and start_tag[i] not in space + "/>=":
            i += 1
        name = start_tag[start:i].lower()
        if not name:
            if i < n and start_tag[i] == "=":
                i += 1
                continue
            return None
        value = None
        j = i
        while j < n and start_tag[j] in space:
            j += 1
        if j < n and start_tag[j] == "=":
            j += 1
            while j < n and start_tag[j] in space:
                j += 1
            if j < n and start_tag[j] in "'\"":
                end = start_tag.find(start_tag[j], j + 1)
                if end < 0:
                    return None
                value = start_tag[j + 1:end]
                j = end + 1
            else:
                while j < n and start_tag[j] not in space + ">":
                    j += 1
        i = j
        if name == "href":
            return value or None
    return None


def _has_class(attrs, value):
    return any(name == "class" and val == value for name, val in attrs)


class _LiteParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []  # (link_url, link_title, snippet), one per closed row
        self._in_row = False
        self._link = None  # [url, parts, closed]
        self._snippet = None  # [parts, closed]

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            if not self._in_row:
                self._in_row = True
                self._link = None
                self._snippet = None
            return
        if not self._in_row:
            return
        if tag == "a" and self._link is None and _has_class(attrs, "result-link"):
            href = _raw_href(self.get_starttag_text() or "")
            if href:
                self._link = [href, [], False]
        elif tag == "td" and self._snippet is None and _has_class(attrs, "result-snippet"):
            self._snippet = [[], False]

    def handle_endtag(self, tag):
        if not self._in_row:
            return
        if tag == "tr":
            link = self._link if self._link and self._link[2] else None
            snippet = self._snippet if self._snippet and self._snippet[1] else None
            self.rows.append((link[0] if link else None, "".join(link[1]) if link else None, "".join(snippet[0]) if snippet else None))
            self._in_row = False
            self._link = None
            self._snippet = None
        elif tag == "a" and self._link and not self._link[2]:
            self._link[2] = True
        elif tag == "td" and self._snippet and not self._snippet[1]:
            self._snippet[1] = True

    def handle_data(self, data):
        if not self._in_row:
            return
        if self._link and not self._link[2]:
            self._link[1].append(data)
        if self._snippet and not self._snippet[1]:
            self._snippet[0].append(data)


def parse_lite_results(html_content):
    results = []
    parser = _LiteParser()
    try:
        parser.feed(html_content)
        parser.close()
    except Exception:
        pass

    current = {}
    for link_url, link_title, snippet in parser.rows:
        if link_url:
            if current.get('title') and current.get('url'):
                if 'snippet' not in current:
                    current['snippet'] = 'No snippet available'
                results.append(current)
            current = {'url': decode_duckduckgo_url(link_url), 'title': link_title.strip()}
            continue

        if snippet is not None and current.get('title'):
            current['snippet'] = snippet.strip()
            results.append(current)
            current = {}

    if current.get('title') and current.get('url'):
        if 'snippet' not in current:
            current['snippet'] = 'No snippet available'
        results.append(current)

    return results


def _decode_bing_url(href):
    """Decode Bing's base64-wrapped redirect URLs."""
    if not href or not href.startswith("https://www.bing.com/ck/a"):
        return href
    try:
        u_param = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u_param and len(u_param) > 2:
            b = u_param[2:]
            return base64.urlsafe_b64decode(b + "=" * ((-len(b)) % 4)).decode()
    except Exception:
        pass
    return href


_VOID_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"))


_H = ("h1", "h2", "h3", "h4", "h5", "h6")


_LISTING = ("address", "pre", "listing", "xmp")


_FONTSTYLE = ("tt", "i", "b", "u", "s", "strike", "big", "small")


def _start_close():
    """The htmlStartClose table, one row per line (a builder: tab-safe, and a
    module-level subscript row would be dropped by the generator)."""
    t = {}
    t["form"] = frozenset(("form", "p", "hr") + _H + ("dl", "ul", "ol", "menu", "dir") + _LISTING)
    t["title"] = frozenset(("p",))
    t["body"] = frozenset(("style", "script", "title"))
    t["frameset"] = frozenset(("style", "script", "title"))
    t["li"] = frozenset(("p",) + _H + ("dl",) + _LISTING + ("li",))
    t["hr"] = frozenset(("p",))
    t["h1"] = frozenset(("p", "h2", "h3", "h4", "h5", "h6"))
    t["h2"] = frozenset(("p", "h1", "h3", "h4", "h5", "h6"))
    t["h3"] = frozenset(("p", "h1", "h2", "h4", "h5", "h6"))
    t["h4"] = frozenset(("p", "h1", "h2", "h3", "h5", "h6"))
    t["h5"] = frozenset(("p", "h1", "h2", "h3", "h4", "h6"))
    t["h6"] = frozenset(("p", "h1", "h2", "h3", "h4", "h5"))
    t["dir"] = frozenset(("p",))
    t["address"] = frozenset(("p", "ul"))
    t["pre"] = frozenset(("p", "ul"))
    t["listing"] = frozenset(("p",))
    t["xmp"] = frozenset(("p",))
    t["blockquote"] = frozenset(("p",))
    t["dl"] = frozenset(("p", "dt", "menu", "dir") + _LISTING)
    t["dt"] = frozenset(("p", "menu", "dir") + _LISTING + ("dd",))
    t["dd"] = frozenset(("p", "menu", "dir") + _LISTING + ("dt",))
    t["ul"] = frozenset(("p", "ol", "menu", "dir") + _LISTING)
    t["ol"] = frozenset(("p", "ul"))
    t["menu"] = frozenset(("p", "ul"))
    t["p"] = frozenset(("p",) + _H + _FONTSTYLE)
    t["div"] = frozenset(("p",))
    t["noscript"] = frozenset(("script",))
    t["center"] = frozenset(("font", "b", "i", "p"))
    t["a"] = frozenset(("a",))
    t["caption"] = frozenset(("p",))
    t["colgroup"] = frozenset(("caption", "colgroup", "col", "p"))
    t["col"] = frozenset(("caption", "col", "p"))
    t["table"] = frozenset(("p",) + _H + ("pre", "listing", "xmp", "a"))
    t["th"] = frozenset(("th", "td", "p", "span", "font", "a", "b", "i", "u"))
    t["td"] = frozenset(("th", "td", "p", "span", "font", "a", "b", "i", "u"))
    t["tr"] = frozenset(("th", "td", "tr", "caption", "col", "colgroup", "p"))
    t["thead"] = frozenset(("caption", "col", "colgroup"))
    t["tfoot"] = frozenset(("th", "td", "tr", "caption", "col", "colgroup", "thead", "tbody", "p"))
    t["tbody"] = frozenset(("th", "td", "tr", "caption", "col", "colgroup", "thead", "tfoot", "tbody", "p"))
    t["optgroup"] = frozenset(("option",))
    t["option"] = frozenset(("option",))
    t["fieldset"] = frozenset(("legend", "p") + _H + ("pre", "listing", "xmp", "a"))
    return t


_START_CLOSE = _start_close()


def _end_priority():
    """libxml2's end-tag priorities: an end tag may only close the open elements
    above its match if none of them outranks it, otherwise it is IGNORED. So with
    a stray <div> left open inside a result, `</li>` does not end the result and
    the next <li class="b_algo"> nests inside it. Measured: without this rule a
    single dropped </div> made 17 of the fixture's 26 variants disagree with lxml.
    """
    t = {}
    t["div"] = 150
    t["td"] = 160
    t["th"] = 160
    t["tr"] = 170
    t["thead"] = 180
    t["tbody"] = 180
    t["tfoot"] = 180
    t["table"] = 190
    t["head"] = 200
    t["body"] = 200
    t["html"] = 220
    return t


_END_PRIORITY = _end_priority()


_END_PRIORITY_DEFAULT = 100


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []  # _Node or str (a text node), in document order


_TREE_MAX_DEPTH = 512


_TREE_SCAN_BUDGET = 1000000


class _TreeBuilder(HTMLParser):
    """A minimal element tree: enough structure to answer the four XPaths."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self._stack = [self.root]
        self._open = {}  # tag -> how many of the stack's elements carry it
        self._budget = _TREE_SCAN_BUDGET

    def _close_from(self, i):
        for gone in self._stack[i:]:
            self._open[gone.tag] -= 1
        del self._stack[i:]

    def handle_starttag(self, tag, attrs):
        closes = _START_CLOSE.get(tag, ())
        while len(self._stack) > 1 and self._stack[-1].tag in closes:
            self._close_from(len(self._stack) - 1)
        node_attrs = {}
        for name, value in attrs:
            # first occurrence wins, and a bare attribute is the empty string
            node_attrs.setdefault(name, "" if value is None else value)
        node = _Node(tag, node_attrs)
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS and len(self._stack) <= _TREE_MAX_DEPTH:
            self._stack.append(node)
            self._open[tag] = self._open.get(tag, 0) + 1

    def handle_endtag(self, tag):
        # close the nearest open element of this name, unless an element above it
        # outranks the end tag (_END_PRIORITY); a stray end tag is ignored
        if not self._open.get(tag):
            return
        priority = _END_PRIORITY.get(tag, _END_PRIORITY_DEFAULT)
        for i in range(len(self._stack) - 1, 0, -1):
            if self._budget <= 0:
                return
            self._budget -= 1
            if self._stack[i].tag == tag:
                self._close_from(i)
                return
            if _END_PRIORITY.get(self._stack[i].tag, _END_PRIORITY_DEFAULT) > priority:
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def _child_elements(node, tag):
    return [c for c in node.children if not isinstance(c, str) and c.tag == tag]


def _descendant_text(node, out, only_under=None):
    """Append node's descendant text nodes in document order. With only_under,
    only text that sits inside an element of that tag (below node) counts."""
    todo = [(node, only_under is None)]
    while todo:
        current, counting = todo.pop()
        if isinstance(current, str):
            out.append(current)
            continue
        for child in reversed(current.children):
            if isinstance(child, str):
                if counting:
                    todo.append((child, True))
            else:
                todo.append((child, counting or child.tag == only_under))
    return out


def _iter_elements(root):
    """Every element below root, in document order."""
    todo = list(reversed([c for c in root.children if not isinstance(c, str)]))
    while todo:
        node = todo.pop()
        yield node
        todo.extend(reversed([c for c in node.children if not isinstance(c, str)]))


def parse_bing_results(html_text):
    """Parse Bing search results: stdlib html.parser, same fields as the old XPath."""
    results = []
    builder = _TreeBuilder()
    try:
        builder.feed(html_text)
        builder.close()
    except Exception:
        return results

    for e in _iter_elements(builder.root):
        if e.tag != "li" or "b_algo" not in e.attrs.get("class", ""):
            continue
        hrefs = []
        title_parts = []
        for child in e.children:
            if isinstance(child, str):
                continue
            if child.tag == "h2":
                for a in _child_elements(child, "a"):
                    if "href" in a.attrs:
                        hrefs.append(a.attrs["href"])
                    _descendant_text(a, title_parts)
            elif child.tag == "div" and "header" in child.attrs.get("class", ""):
                for a in _child_elements(child, "a"):
                    if "href" in a.attrs:
                        hrefs.append(a.attrs["href"])
                    for h2 in _child_elements(a, "h2"):
                        _descendant_text(h2, title_parts)
        href = hrefs[0] if hrefs else None
        if not href:
            continue

        href = _decode_bing_url(href)
        title = _normalize("".join(title_parts))
        body = _descendant_text(e, [], only_under="p")
        snippet = _normalize("".join(body)).replace("\xa0", " ")

        results.append({'url': href, 'title': title, 'snippet': snippet or 'No snippet available'})

    return results


def warmup_session(session, endpoint="ddg"):
    try:
        if endpoint == "bing":
            session.get("https://www.bing.com/", timeout=10)
        else:
            session.get("https://lite.duckduckgo.com/lite/", timeout=10)
    except Exception:
        pass
    time.sleep(random.uniform(0.8, 1.5))


_DDG_CHALLENGE_MARKERS = ("anomaly-modal", "Please complete the following")


def _ddg_blocked(resp, query, results=None):
    """DDG's bot-block signal (D15, D16 S1/S2): True only for a block.

    All of: (0) the final URL's host is lite.duckduckgo.com (a redirect
    target's answer is never a block); (a) the page parses to ZERO results, so
    a snippet carrying a marker cannot trip it (a page with a snippet has a
    result); and then EITHER (s) the status is 202 (R-0052: the challenge
    status spec-ddg recorded and the user has seen live; lite results answer
    200), with no marker needed, OR (b) a challenge marker occurs in the page
    AND the query does not contain that marker (case-insensitive), so the
    reflected query cannot trip it. An undecodable body is not judged here:
    zero results on it would mean nothing, so neither (s) nor (b) applies.

    `results` is parse_lite_results(resp.text) when the caller already has it
    (search_ddg parses each response ONCE, F32); None parses here.
    """
    # UNVERIFIED: (b) is the plan's FALLBACK predicate. task-038
    # (.claude/tmp/task038-live.txt) saw DDG answer both transports with 200 and
    # lite results; no anomaly page was recorded, so the structural marker
    # element (an element whose class/id is anomaly-modal, outside the results
    # container) is still unmeasured. Replace (b) with that structural match
    # once a live challenge page has been recorded. The 202 of (s) is the one
    # measured signal (spec-ddg; R-0052).
    if urllib.parse.urlsplit(resp.url or "").hostname != "lite.duckduckgo.com":
        return False
    if resp.decode_error is not None:
        return False
    page = resp.text
    if results is None:
        results = parse_lite_results(page)
    if results:
        return False
    if resp.status_code == 202:
        return True
    lowered_query = (query or "").lower()
    for marker in _DDG_CHALLENGE_MARKERS:
        if marker in page and marker.lower() not in lowered_query:
            return True
    return False


def search_ddg(query, session, note=lambda event, query, detail: None, on_transport_error=lambda exc: None):
    """Lite results for *query*; [] on no results or an error; None on a block (_ddg_blocked).

    `note(event, query, detail)` receives `ddg_undecodable` (detail: the decode
    error) and `ddg_error` (detail: the exception); `on_transport_error(exc)` is
    called in the except arm before the [] is returned.
    """
    # The fetch is modelled as coming from the page the warm-up loaded (L8);
    # the warm-up URL itself stands in only when the warm-up failed. The cors
    # POST profile supplies Sec-Fetch-*, the form Content-Type and (h2 only)
    # Priority;
    # no Origin goes out, because the referer is same-origin with the target
    # (Chrome's captured same-origin fetches carry none) -- the client adds one
    # only for a cross-origin initiator, i.e. after a redirect off this origin.
    page = session.last_navigation_url or "https://lite.duckduckgo.com/lite/"
    try:
        resp = session.post("https://lite.duckduckgo.com/lite/", data={"q": query, "kl": ""}, headers={"Accept": "*/*"}, referer=page, timeout=15)
        if resp.decode_error is not None:
            # Never None: an undecodable body is not taken for a CAPTCHA.
            note("ddg_undecodable", query, resp.decode_error)
            return []
        # Parsed ONCE (F32): the block signal reuses these results.
        results = parse_lite_results(resp.text)
        if _ddg_blocked(resp, query, results):
            return None
        return results
    except Exception as e:
        # A transport failure is never a block (D16 M2): a note and [], so
        # breaking the handshake cannot force a block verdict, and the run
        # does not switch to Bing either.
        note("ddg_error", query, e)
        on_transport_error(e)
        return []


def _bing_blocked(resp):
    """Bing's bot-block signal (D15, D16 M3/S2): a 403 from www.bing.com itself.

    429 (a volume verdict), 5xx (a server fault) and every other status are
    never a block, nor is any answer from another host. DEFERRED, declared
    (R25): a challenge served as a 200 that parses to [] is not detected.
    """
    # UNVERIFIED: Bing's block shape against a non-browser TLS fingerprint was
    # never observed; task-038 saw the verified transport answered 200 with results.
    return resp.status_code == 403 and urllib.parse.urlsplit(resp.url or "").hostname == "www.bing.com"


def search_bing(query, session, note=lambda event, query, detail: None, on_transport_error=lambda exc: None):
    """Bing results for *query*; [] on no results or an error; None on a block (_bing_blocked).

    `note(event, query, detail)` receives `bing_http` (detail: the status code
    of a non-200 answer that is NOT a block -- a block is reported by its None
    alone, so the CLI prints one line for it, FR-9), `bing_undecodable`
    (detail: the decode error) and `bing_error` (detail: the exception);
    `on_transport_error(exc)` is called in the except arm before the [] is
    returned.
    """
    try:
        resp = session.get("https://www.bing.com/search", params={"q": query}, timeout=15)
        if resp.status_code != 200:
            if _bing_blocked(resp):
                return None
            note("bing_http", query, resp.status_code)
            return []
        if resp.decode_error is not None:
            note("bing_undecodable", query, resp.decode_error)
            return []
        return parse_bing_results(resp.text)
    except Exception as e:
        # A transport failure is never a block (D16 M2).
        note("bing_error", query, e)
        on_transport_error(e)
        return []


def run_web(queries, with_session, note_for, out=None):
    """Search every query, DDG first; a DDG block moves this and every later
    query to Bing. Returns [(query, results_or_None, transport_failed), ...].

    The outcomes are appended to *out* (a fresh list when None) as each query
    finishes, so a host whose hook raises mid-run -- a busy endpoint, a call
    deadline -- still holds every outcome gathered before the raise.

    `with_session(endpoint, fn)` is the host's hook: `endpoint` is "ddg" or
    "bing", and the host calls `fn(session, bad)` with a ready session and its
    own transport-failure callable, returning what `fn` returns. Each query is
    one `with_session` call per endpoint it touches, so a host that locks per
    endpoint never holds the DDG lock while taking the Bing one. `fn` wraps
    `bad` so the failure is recorded here as well before the host sees it --
    without the wrap only the host would know, and `transport_failed` could not
    be computed. `note_for(i)` returns the note callable for query index i; at
    the DDG -> Bing switch it receives `ddg_captcha` (detail None). A result of
    None means Bing blocked that query too.
    """
    def call(search, query, note, failed):
        def fn(session, bad):
            def on_err(exc):
                failed.append(exc)
                bad(exc)
            return search(query, session, note, on_err)
        return fn

    out = [] if out is None else out
    using_bing = False
    for i, query in enumerate(queries):
        note = note_for(i)
        failed = []
        results = None
        if not using_bing:
            results = with_session("ddg", call(search_ddg, query, note, failed))
            if results is None:
                # CAPTCHA -- switch to Bing for this and all remaining queries
                note("ddg_captcha", query, None)
                using_bing = True
                failed = []
        if using_bing:
            results = with_session("bing", call(search_bing, query, note, failed))
        out.append((query, results, bool(failed)))
    return out


def format_web_results(results, query=None):
    """The results as markdown. Every third-party field is rendered through
    _web_line (one line, no invisible or display-steering code point: F2, the
    Unicode classes) and the URL through _web_url ("No URL" when it is not a
    safe http(s) link: F3); the query echo goes through _web_line as well."""
    output = []
    if query:
        output.append(f"## Query: {_web_line(query)}")
        output.append("")
    for i, result in enumerate(results, 1):
        title = _web_line(result.get('title', 'No title'))
        url = _web_url(result.get('url')) or 'No URL'
        snippet = _web_line(result.get('snippet', 'No snippet available'))
        output.append(f"### Result {i}: {title}")
        output.append(f"**URL**: {url}")
        output.append(f"**Snippet**: {snippet}")
        output.append("")
    return '\n'.join(output)
# END GENERATED: 9091a63602ab

# Code search, generated from Scripts/_mcp_codesearch.py: grep.app's request,
# its JSON and snippet parsers, the block signal, search_github, the fence and
# the markdown, shared with search_github.py. Taken whole.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
# BEGIN GENERATED: _mcp_codesearch.py :: _CODE_LINE_SEPARATORS, _code_clean, _code_line, _code_field, _ext_to_lang, EXT_TO_LANG, detect_language, _SnippetParser, extract_code_from_snippet, build_github_url, _grep_hits, _grep_str, parse_grep_results, warmup_code_session, _body_is_json, _grep_app_blocked, search_github, _code_fence, format_code_results
_CODE_LINE_SEPARATORS = "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\x85"


def _code_clean(text, keep):
    """*text* with every invisible or display-steering code point dropped.

    A character in *keep* survives; U+2028 U+2029 U+0085 become a newline.
    Printable ASCII is the fast path; the rest is judged by category.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if 0x20 <= code < 0x7f or ch in keep:
            out.append(ch)
        elif ch in _CODE_LINE_SEPARATORS:
            out.append("\n")
        elif 0xfe00 <= code <= 0xfe0f or 0xe0000 <= code <= 0xe01ef:
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def _code_line(text):
    """A single-line field: _code_clean, then every whitespace run one space."""
    if text is None:
        return ""
    return " ".join(_code_clean(str(text), "\t\n\r").split())


def _code_field(text):
    """A header field (repo, path, branch): one line and no backtick (F4)."""
    return _code_line(str(text).replace("`", ""))


def _ext_to_lang():
    """File extension -> language name, one row per line (a builder: tab-safe)."""
    t = {}
    t['.py'] = 'Python'
    t['.js'] = 'JavaScript'
    t['.ts'] = 'TypeScript'
    t['.tsx'] = 'TypeScript'
    t['.jsx'] = 'JavaScript'
    t['.java'] = 'Java'
    t['.cpp'] = 'C++'
    t['.cc'] = 'C++'
    t['.c'] = 'C'
    t['.h'] = 'C/C++'
    t['.hpp'] = 'C++'
    t['.cs'] = 'C#'
    t['.go'] = 'Go'
    t['.rs'] = 'Rust'
    t['.rb'] = 'Ruby'
    t['.php'] = 'PHP'
    t['.swift'] = 'Swift'
    t['.kt'] = 'Kotlin'
    t['.scala'] = 'Scala'
    t['.sh'] = 'Shell'
    t['.bash'] = 'Bash'
    t['.html'] = 'HTML'
    t['.css'] = 'CSS'
    t['.scss'] = 'SCSS'
    t['.json'] = 'JSON'
    t['.xml'] = 'XML'
    t['.yaml'] = 'YAML'
    t['.yml'] = 'YAML'
    t['.md'] = 'Markdown'
    t['.sql'] = 'SQL'
    t['.r'] = 'R'
    t['.m'] = 'Objective-C'
    t['.vim'] = 'Vim Script'
    t['.lua'] = 'Lua'
    t['.pl'] = 'Perl'
    return t


EXT_TO_LANG = _ext_to_lang()


def detect_language(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    return EXT_TO_LANG.get(ext)


class _SnippetParser(HTMLParser):

    _TR_OPEN = '<tr data-line="'

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self._line_no = None
        self._parts = None  # the open <pre>'s text, None outside one

    def handle_starttag(self, tag, attrs):
        if self._parts is not None:
            return
        raw = self.get_starttag_text() or ""
        if tag == "tr" and self._line_no is None:
            digits = raw[len(self._TR_OPEN):-2]
            # at most 9 digits (F13): int() of a huge decimal string is
            # quadratic on CPython before 3.9.14, and no file has 10^9 lines
            if raw.startswith(self._TR_OPEN) and raw.endswith('">') and digits and len(digits) <= 9 and digits.isdecimal():
                self._line_no = int(digits)
        elif tag == "pre" and self._line_no is not None and raw == "<pre>":
            self._parts = []

    def handle_endtag(self, tag):
        if tag == "pre" and self._parts is not None:
            code_text = "".join(self._parts).strip()
            if code_text:
                self.lines.append((self._line_no, code_text))
            self._line_no = None
            self._parts = None

    def handle_data(self, data):
        if self._parts is not None:
            self._parts.append(data)


def extract_code_from_snippet(html_snippet):
    parser = _SnippetParser()
    try:
        parser.feed(html_snippet)
        parser.close()
    except Exception:
        pass
    return parser.lines


def build_github_url(repo, path, branch, line_number=None):
    """The blob URL; repo, branch and path are percent-encoded with "/" kept (F4),
    so a path carrying a space, "#", "?", a newline or non-ASCII stays one URL."""
    base = "https://github.com/%s/blob/%s/%s" % (urllib.parse.quote(repo, safe="/"), urllib.parse.quote(branch, safe="/"), urllib.parse.quote(path, safe="/"))
    if line_number:
        return f"{base}#L{line_number}"
    return base


def _grep_hits(data):
    """data["hits"]["hits"] when the answer has grep.app's shape, else None (F8).

    A shape mismatch is a parse failure, not a transport error: search_github
    notes it as `grep_schema` and keeps the session."""
    hits = data.get('hits', {}) if isinstance(data, dict) else None
    hits = hits.get('hits', []) if isinstance(hits, dict) else None
    return hits if isinstance(hits, list) else None


def _grep_str(hit, key, default):
    value = hit.get(key, default)
    return value if isinstance(value, str) else default


def parse_grep_results(data, limit):
    """The results of a grep.app answer. Every level is type-checked (F8): a hit
    that is not an object is skipped, a repo / path / branch that is not a string
    takes its default, and a content or snippet of the wrong type is no snippet."""
    results = []
    for hit in (_grep_hits(data) or [])[:limit]:
        if not isinstance(hit, dict):
            continue
        file_path = _grep_str(hit, 'path', 'Unknown')
        result = {'repo': _grep_str(hit, 'repo', 'Unknown'), 'file_path': file_path, 'branch': _grep_str(hit, 'branch', 'main'), 'language': detect_language(file_path) or 'Unknown', 'code_lines': []}

        content = hit.get('content', {})
        snippet_html = _grep_str(content, 'snippet', '') if isinstance(content, dict) else ''
        if snippet_html:
            result['code_lines'] = extract_code_from_snippet(snippet_html)

        first_line = result['code_lines'][0][0] if result['code_lines'] else None
        result['url'] = build_github_url(result['repo'], result['file_path'], result['branch'], first_line)
        results.append(result)

    return results


def warmup_code_session(session):
    try:
        session.get("https://grep.app/", timeout=10)
    except Exception:
        pass
    time.sleep(random.uniform(0.8, 1.5))


def _body_is_json(resp):
    """True when the (decoded) body parses as JSON; False otherwise, never raises.

    ConnectionError is the base of the Chrome client's ChromeClientError, which
    reading `resp.text` may raise; the base is caught because the subclass is a
    name of another canonical source.
    """
    try:
        json.loads(resp.text)
    except (ValueError, ConnectionError):
        return False
    return True


def _grep_app_blocked(resp):
    """grep.app's bot-block signal (D15, D16 S2/M3, D17): True only for a block.

    Judged on grep.app's own host only (a redirect target's answer is never a
    block), then: 403; a 429 whose body is a non-JSON text/html page; a 200
    whose body is not JSON. A 429 with a JSON body is a rate limit and 5xx a
    server fault -- never a block. An undecodable body is not judged here.
    """
    # task-038 measured (.claude/tmp/task038-live.txt): the verified transport got 429 text/html non-JSON on both / and /api/search while the Chrome path got 200 JSON seconds apart -- a fingerprint block, not a rate limit.
    if urllib.parse.urlsplit(resp.url or "").hostname != "grep.app":
        return False
    if resp.decode_error is not None:
        return False
    if resp.status_code == 403:
        return True
    if resp.status_code == 429:
        content_type = (resp.headers.get("content-type") or "").lower()
        return "text/html" in content_type and not _body_is_json(resp)
    if resp.status_code == 200:
        return not _body_is_json(resp)
    return False


def search_github(query, session, lang=None, repo=None, path=None, limit=10, note=lambda event, query, detail: None, on_transport_error=lambda exc: None):
    """Results for *query*; [] on no results or an error; None on a block (_grep_app_blocked).

    `note(event, query, detail)` receives `grep_undecodable` (detail: the
    decode error), `grep_rate_limited` (detail: 429), `grep_http` (detail: the
    status code), `grep_schema` (detail None: a JSON answer not in grep.app's
    shape) and `grep_error` (detail: the exception);
    `on_transport_error(exc)` is called in the except arm before the [] is
    returned.
    """
    params = {'q': query}
    if lang:
        params['f.lang'] = lang
    if repo:
        params['f.repo'] = repo
    if path:
        params['f.path'] = path

    url = f"https://grep.app/api/search?{urlencode(params)}"
    # The fetch is modelled as coming from the page the warm-up loaded; the
    # warm-up URL itself stands in only when the warm-up failed (R2-L4).
    page = session.last_navigation_url or "https://grep.app/"

    try:
        resp = session.get(url, headers={"Accept": "application/json, text/plain, */*"}, mode="cors", referer=page, timeout=15)
        if resp.decode_error is not None:
            note("grep_undecodable", query, resp.decode_error)
            return []
        if _grep_app_blocked(resp):
            return None
        if resp.status_code == 429:
            note("grep_rate_limited", query, resp.status_code)
            return []
        if resp.status_code != 200:
            note("grep_http", query, resp.status_code)
            return []
        data = json.loads(resp.text)
        if _grep_hits(data) is None:
            # Not grep.app's shape (F8): a parse failure, not a transport
            # error -- noted, [] returned, and the session kept.
            note("grep_schema", query, None)
            return []
        return parse_grep_results(data, limit)
    except Exception as e:
        # A transport failure is never a block (D16 M2): a note and [].
        note("grep_error", query, e)
        on_transport_error(e)
        return []


def _code_fence(lines):
    """A backtick fence no line of *lines* can close: one backtick longer than
    the longest backtick run in them, and never shorter than three."""
    longest = 0
    for line in lines:
        run = 0
        for ch in line:
            if ch == "`":
                run += 1
                if run > longest:
                    longest = run
            else:
                run = 0
    return "`" * max(3, longest + 1)


def format_code_results(results, query=None):
    """The results as markdown. The header fields go through _code_field (one
    line, no backtick, no invisible or display-steering code point: F4 and the
    Unicode classes), the URL and the query echo through _code_line, and the
    code through _code_clean keeping tab and newline, before the fence is
    measured over exactly the text it encloses."""
    if not results:
        return "No results found."

    output = []
    if query:
        output.append(f"## Query: {_code_line(query)}")
        output.append("")

    for i, result in enumerate(results, 1):
        output.append(f"### Result {i}: {_code_field(result['repo'])} - {_code_field(result['file_path'])}")
        output.append(f"**URL**: {_code_line(result['url'])}")
        output.append(f"**Branch**: {_code_field(result['branch'])}")
        output.append(f"**Language**: {result['language']}")

        if result['code_lines']:
            first_line = result['code_lines'][0][0]
            last_line = result['code_lines'][-1][0]
            output.append(f"**Line {first_line}-{last_line}:**")
            codes = [_code_clean(code, "\t\n") for _num, code in result['code_lines']]
            fence = _code_fence(codes)
            output.append(fence)
            for code in codes:
                output.append(code)
            output.append(fence)
        else:
            output.append("No code snippet available")

        output.append("")

    return '\n'.join(output)
# END GENERATED: 037533ef0628


# ---------------------------------------------------------------------------
# Per-endpoint sessions
# ---------------------------------------------------------------------------

def _create_session():
    """A new session on the Chrome path (certificate NOT verified).

    connect_policy is _ch_public_only_policy: the Chrome path verifies no
    certificate, so a MITM could answer with a redirect into private or
    metadata space, which the policy refuses. allow_downgrade is False, no
    tls12_fallback and no rand= are passed. max_bytes is SEARCH_MAX_BYTES.
    """
    return _ch_session_new(decoders=_DECODERS, connect_policy=_ch_public_only_policy, timeout=20, max_bytes=SEARCH_MAX_BYTES, allow_downgrade=False, transport="chrome")


class _EndpointBusy(Exception):
    """An endpoint's lock was not acquired within ENDPOINT_LOCK_TIMEOUT."""


class _CallDeadline(Exception):
    """The call's CALL_DEADLINE ran out before its next lock wait or pacing sleep."""


class _Endpoint:
    """One search endpoint's session and pacing state.

    Every field below `lock` is read and written ONLY while `lock` is held:
    the session is not thread-safe (one HPACK state per h2 connection), and the
    pacing is meaningful only if it is process-wide.
    """

    def __init__(self, name: str, jitter: Tuple[float, float]):
        self.name = name
        self.lock = threading.Lock()
        self.session = None          # created lazily, dropped on a transport error
        self.last_request = None     # time.monotonic() of the last search, or None
        self.count = 0               # searches served, for the rotation
        self.jitter = jitter         # (low, high) seconds between two searches


def _new_endpoints() -> Dict[str, _Endpoint]:
    return {
        "ddg": _Endpoint("ddg", (2.5, 5.0)),
        "bing": _Endpoint("bing", (1.5, 3.0)),
        "grep.app": _Endpoint("grep.app", (1.5, 3.0)),
    }


_ENDPOINTS = _new_endpoints()


def _close_quietly(session) -> None:
    try:
        session.close()
    except Exception:  # noqa: BLE001 -- a close that fails leaves nothing to keep
        log.debug("session close failed")


def _warm_up(name: str, session) -> None:
    if name == "grep.app":
        warmup_code_session(session)
    else:
        warmup_session(session, name)


def _pace(ep: _Endpoint, deadline: Optional[float] = None) -> None:
    """Sleep what is left of a random gap since the endpoint's last request.

    Raises _CallDeadline instead when the sleep would end past *deadline*.
    """
    if ep.last_request is None:
        return
    gap = random.uniform(ep.jitter[0], ep.jitter[1])
    wait = gap - (time.monotonic() - ep.last_request)
    if deadline is not None and time.monotonic() + max(wait, 0.0) > deadline:
        raise _CallDeadline(ep.name)
    if wait > 0:
        time.sleep(wait)


def with_session(endpoint: str, fn: Callable[[Any, Callable[[BaseException], None]], Any],
                 deadline: Optional[float] = None) -> Any:
    """The search blocks' host hook: fn(session, bad) under *endpoint*'s lock.

    Under the lock, in order: rotate (close; a new session follows) every
    ROTATE_EVERY searches, sleep the rest of the pacing gap, create and warm up
    a session if there is none -- the pacing comes FIRST, so the warm-up GET
    is paced like a search (F21, NFR-2) -- call fn, and if fn's search
    reported a transport failure through `bad` (or fn raised) close and drop
    the session so the next search opens a new one. The lock is released in
    every case. run_web makes one call per endpoint per query, so the DDG and
    Bing locks are never held together.

    *deadline* (a time.monotonic() value, the call's CALL_DEADLINE) cuts the
    lock wait to what is left of it and raises _CallDeadline when nothing is
    left, before the lock or before the pacing sleep.
    """
    ep = _ENDPOINTS[endpoint]
    timeout = ENDPOINT_LOCK_TIMEOUT
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _CallDeadline(endpoint)
        timeout = min(timeout, remaining)
    if not ep.lock.acquire(timeout=timeout):
        if deadline is not None and time.monotonic() >= deadline:
            raise _CallDeadline(endpoint)
        raise _EndpointBusy(endpoint)
    try:
        if ep.session is not None and ep.count > 0 and ep.count % ROTATE_EVERY == 0:
            _close_quietly(ep.session)
            ep.session = None
        _pace(ep, deadline)
        if ep.session is None:
            ep.session = _create_session()
            _warm_up(ep.name, ep.session)
        ep.count += 1
        broken = []
        finished = False
        try:
            result = fn(ep.session, broken.append)
            finished = True
            return result
        finally:
            ep.last_request = time.monotonic()
            if (broken or not finished) and ep.session is not None:
                _close_quietly(ep.session)
                ep.session = None
    finally:
        ep.lock.release()


# The endpoint a note event belongs to, keyed on the event's prefix.
_NOTE_ENDPOINTS = {"ddg": "ddg", "bing": "bing", "grep": "grep.app"}


def _note_detail(detail: Any) -> str:
    """A status code as itself; anything else (a decode error, an exception) as
    its CLASS name only -- its message can carry the query, a body or a path."""
    if isinstance(detail, int) and not isinstance(detail, bool):
        return str(detail)
    return type(detail).__name__


def note_for(i: int) -> Callable[[str, str, Any], None]:
    """The note callable for query index *i*: one structure-only log line.

    Logged: the endpoint, the fixed event token, the query INDEX and the
    detail as _note_detail renders it. Never the query text, a body or an
    exception message (NFR-4, CWE-532).
    """
    def note(event: str, query: str, detail: Any) -> None:
        endpoint = _NOTE_ENDPOINTS.get(event.split("_", 1)[0], "unknown")
        if detail is None:
            log.warning("search note endpoint=%s event=%s query=%d", endpoint, event, i)
        else:
            log.warning("search note endpoint=%s event=%s query=%d detail=%s", endpoint, event, i, _note_detail(detail))
    return note


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

# The composed-answer ceiling (the COMPOSED payload class): the default of
# max_answer_chars.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_paging.py :: DEFAULT_MAX_ANSWER_CHARS
DEFAULT_MAX_ANSWER_CHARS = 24000
# END GENERATED: 25da79526dcc

ACCEPTED_WEB_PARAMS = frozenset({"queries", "limit", "max_answer_chars"})
ACCEPTED_CODE_PARAMS = frozenset({"queries", "lang", "repo", "path", "limit", "max_answer_chars"})

PARAM_ALIASES = {
    "query": "queries",
    "q": "queries",
}


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_json.py :: _int_param
def _int_param(value, default: int) -> int:
    """Coerce a wire value to int, falling back instead of raising.

    `OverflowError` is in the list because the wire can carry a float infinity:
    `json.loads` reads both `1e999` and the bare `Infinity` token as one, and
    `int()` on an infinity raises an error that is neither a TypeError nor a
    ValueError. Without it the one value a caller is most likely to send as
    "no limit" was the only bad value that did not fall back -- it escaped the
    handler as an opaque internal error. NaN needs no entry: `int(nan)` raises
    ValueError, which this already catches.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default
# END GENERATED: 9184041df86b


def _resolve_aliases(params: dict) -> dict:
    resolved = {}
    claimed = {}
    for key, value in params.items():
        canonical = PARAM_ALIASES.get(key, key)
        if canonical in resolved:
            first, second = sorted((claimed[canonical], key))
            raise ValueError(
                f"Ambiguous parameters: '{first}' and '{second}' both set "
                f"'{canonical}'. Pass exactly one."
            )
        resolved[canonical] = value
        claimed[canonical] = key
    return resolved


def _queries_param(params: dict) -> Tuple[Optional[List[str]], Optional[str]]:
    """(queries, None) or (None, the fixed refusal)."""
    raw = params.get("queries")
    if isinstance(raw, str):
        raw = [raw]
    if raw is None or (isinstance(raw, list) and not raw):
        return None, "queries: at least one query is required"
    if not isinstance(raw, list):
        return None, "queries: a string or a list of strings"
    if len(raw) > MAX_QUERIES:
        return None, f"queries: at most {MAX_QUERIES} per call"
    for query in raw:
        if not isinstance(query, str):
            return None, "queries: every query must be a string"
    for query in raw:
        if not query.strip():
            return None, "queries: a query may not be empty"
        if len(query) > MAX_QUERY_CHARS:
            return None, f"queries: each query at most {MAX_QUERY_CHARS} chars"
        if _refused_char(query):
            return None, "queries: a query may not contain control, bidi or tag characters"
    return raw, None


# The Unicode audit, input side. A query or filter is the caller's own text,
# sent to the endpoint and echoed in `## Query:`, so it is REFUSED with a fixed
# message -- as every other FR-10 breach is -- rather than silently rewritten,
# when it carries a character no one-line search text has a use for: a C0 or
# C1 control or DEL (tab and newline included), a bidi control (U+200E U+200F
# U+061C U+202A-202E U+2066-2069, the Trojan Source set), a Unicode tag
# (U+E0000-E007F, invisible ASCII) or a line / paragraph separator. The
# zero-width joiners and the variation selectors are NOT refused: ZWNJ is
# Persian orthography and VS16 / ZWJ are emoji; the echo drops them (render
# sanitizer), the endpoint receives the text as written.
_BIDI_CONTROLS = frozenset(chr(c) for c in (0x200e, 0x200f, 0x061c, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069))


def _refused_char(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if code < 0x20 or 0x7f <= code <= 0x9f or code in (0x2028, 0x2029):
            return True
        if ch in _BIDI_CONTROLS or 0xe0000 <= code <= 0xe007f:
            return True
    return False


def _limit_param(params: dict) -> Tuple[Optional[int], Optional[str]]:
    raw = params.get("limit")
    if raw is None:
        return DEFAULT_LIMIT, None
    limit = None if isinstance(raw, bool) else _int_param(raw, None)
    if limit is None or limit < 1 or limit > MAX_LIMIT:
        return None, f"limit: an integer from 1 to {MAX_LIMIT}"
    return limit, None


# F6: the shapes the code filters may take. lang is a grep.app language name
# ("Python", "C++", "C#", "Objective-C", "Vim Script"); repo an owner or an
# owner/repo; path a path prefix, any text without a refused character.
_LANG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 +#._-]*")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?")

_FILTER_SHAPE = {
    "lang": "lang: letters, digits, spaces and + # . _ - only",
    "repo": "repo: owner or owner/repo, letters, digits and . _ - only",
    "path": "path: may not contain control, bidi or tag characters",
}


def _filter_param(params: dict, name: str) -> Tuple[Optional[str], Optional[str]]:
    """(the filter or None, None) or (None, the fixed refusal): a string of at
    most MAX_FILTER_CHARS in its name's shape (F6); an empty string is no filter."""
    raw = params.get(name)
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, f"{name}: must be a string"
    if not raw:
        return None, None
    if len(raw) > MAX_FILTER_CHARS:
        return None, f"{name}: at most {MAX_FILTER_CHARS} chars"
    if name == "lang" and not _LANG_RE.fullmatch(raw):
        return None, _FILTER_SHAPE[name]
    if name == "repo" and not _REPO_RE.fullmatch(raw):
        return None, _FILTER_SHAPE[name]
    if name == "path" and _refused_char(raw):
        return None, _FILTER_SHAPE[name]
    return raw, None


def _max_answer_chars(params: dict) -> int:
    return min(MAX_ANSWER_CHARS_CEILING, max(1, _int_param(params.get("max_answer_chars"), DEFAULT_MAX_ANSWER_CHARS)))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# The titles the CLIs print above a multi-query answer.
_WEB_TITLE = "# DuckDuckGo Search Results"
_CODE_TITLE = "# GitHub Search Results"

# The only per-query notices a reply may carry (FR-6): fixed strings, never an
# exception's text.
_NOTICE_BLOCKED_BING = "blocked by Bing"
_NOTICE_BLOCKED_GREP = "blocked by grep.app"
_NOTICE_TRANSPORT = "transport error"
_NOTICE_NO_RESULTS = "No results found."
_NOTICE_BUSY = "endpoint busy, retry later"


def _notice_section(query: str, notice: str, multi: bool) -> str:
    # the query echo goes through the render sanitizer, as format_web_results' does
    return f"## Query: {_web_line(query)}\n\n{notice}\n" if multi else notice


def _compose(title: str, sections: List[str]) -> Tuple[str, List[int]]:
    """One section alone, as the CLI prints it; several under the CLI's title.

    Also returns where each section ENDS in the text, so _reply can tell which
    block notices a cut removed."""
    if not sections:
        return "", []
    if len(sections) == 1:
        return sections[0], [len(sections[0])]
    ends = []
    at = len(title) + 2
    for section in sections:
        ends.append(at + len(section))
        at = ends[-1] + len("\n---\n\n")
    return title + "\n\n" + "\n---\n\n".join(sections), ends


def _cap(text: str, max_answer_chars: int) -> str:
    """text[:max_answer_chars], never left inside an open code fence (F17).

    A trailing partial line made only of backticks (a fence cut in half) is
    dropped; then, if a fence opened by a whole line of three or more backticks
    is still open, its closing line is appended. Only a fence produces such a
    line: every other rendered line carries a prefix, and a code line is never
    equal to the fence around it (_code_fence)."""
    kept = text[:max_answer_chars]
    tail = kept.rsplit("\n", 1)[-1]
    if tail and set(tail) == {"`"}:
        kept = kept[:len(kept) - len(tail)]
    fence = None
    for line in kept.split("\n"):
        if fence is None:
            if len(line) >= 3 and set(line) == {"`"}:
                fence = line
        elif line == fence:
            fence = None
    if fence is not None:
        kept += ("" if kept.endswith("\n") else "\n") + fence
    return kept


def _stop_notice(stopped: Optional[str], missing: int, total: int) -> Optional[str]:
    """The fixed notice of a call that stopped early (busy endpoint, deadline)."""
    if stopped == "busy":
        return _NOTICE_BUSY if missing == total else f"{_NOTICE_BUSY}: {missing} of {total} queries not searched"
    if stopped == "deadline":
        return f"call deadline ({CALL_DEADLINE:g} s) reached: {missing} of {total} queries not searched"
    return None


def _reply(title: str, sections: List[str], kept: List[bool], is_error: bool,
           max_answer_chars: int, trailer: Optional[str] = None) -> dict:
    """Compose, cap, then flag: a blocked query, a busy endpoint or the call
    deadline makes the call isError, with the whole text -- every gathered
    result included -- as the error.

    Only the BODY is capped (Marple 3): a block notice section (kept[i]) the
    cut removed is appended after the cap note, and the stop notice (*trailer*)
    always comes last, outside the cap, so truncation never hides why a call
    failed."""
    text, ends = _compose(title, sections)
    if len(text) > max_answer_chars:
        text = _cap(text, max_answer_chars) + f"\n[output capped at {max_answer_chars} chars; use fewer queries or a lower limit]"
        for section, end, keep in zip(sections, ends, kept):
            if keep and end > max_answer_chars:
                text += "\n\n" + section.rstrip("\n")
    if trailer:
        text = text + "\n\n" + trailer if text else trailer
    return {"error": text} if is_error else {"result": text}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_web(params: dict) -> dict:
    queries, refusal = _queries_param(params)
    if refusal:
        return {"error": refusal}
    limit, refusal = _limit_param(params)
    if refusal:
        return {"error": refusal}
    max_answer_chars = _max_answer_chars(params)

    # run_web appends to `outcomes` as it goes, so a busy endpoint or the call
    # deadline mid-run still leaves every query gathered before it (Marple 4).
    deadline = time.monotonic() + CALL_DEADLINE
    outcomes: list = []
    stopped = None
    try:
        run_web(queries, lambda endpoint, fn: with_session(endpoint, fn, deadline), note_for, outcomes)
    except _EndpointBusy:
        stopped = "busy"
    except _CallDeadline:
        stopped = "deadline"
    multi = len(queries) > 1
    sections = []
    kept = []
    blocked = False
    for query, results, transport_failed in outcomes:
        if results is None:
            blocked = True
            sections.append(_notice_section(query, _NOTICE_BLOCKED_BING, multi))
        elif not results:
            notice = _NOTICE_TRANSPORT if transport_failed else _NOTICE_NO_RESULTS
            sections.append(_notice_section(query, notice, multi))
        else:
            sections.append(format_web_results(results[:limit], query=query if multi else None))
        kept.append(results is None)
    trailer = _stop_notice(stopped, len(queries) - len(outcomes), len(queries))
    return _reply(_WEB_TITLE, sections, kept, blocked or stopped is not None, max_answer_chars, trailer)


def handle_code(params: dict) -> dict:
    queries, refusal = _queries_param(params)
    if refusal:
        return {"error": refusal}
    limit, refusal = _limit_param(params)
    if refusal:
        return {"error": refusal}
    filters = {}
    for name in ("lang", "repo", "path"):
        filters[name], refusal = _filter_param(params, name)
        if refusal:
            return {"error": refusal}
    max_answer_chars = _max_answer_chars(params)

    deadline = time.monotonic() + CALL_DEADLINE
    multi = len(queries) > 1
    sections = []
    kept = []
    blocked = False
    stopped = None
    searched = 0
    for i, query in enumerate(queries):
        failed = []

        def fn(session, bad, query=query, note=note_for(i), failed=failed):
            def on_err(exc):
                failed.append(exc)
                bad(exc)
            return search_github(query, session, lang=filters["lang"], repo=filters["repo"], path=filters["path"], limit=limit, note=note, on_transport_error=on_err)

        # A busy endpoint or the call deadline stops the loop, keeping every
        # section gathered so far (Marple 4, F22).
        try:
            results = with_session("grep.app", fn, deadline)
        except _EndpointBusy:
            stopped = "busy"
            break
        except _CallDeadline:
            stopped = "deadline"
            break
        searched += 1
        if results is None:
            blocked = True
            sections.append(_notice_section(query, _NOTICE_BLOCKED_GREP, multi))
        elif not results:
            notice = _NOTICE_TRANSPORT if failed else _NOTICE_NO_RESULTS
            sections.append(_notice_section(query, notice, multi))
        else:
            sections.append(format_code_results(results, query=query if multi else None))
        kept.append(results is None)
    trailer = _stop_notice(stopped, len(queries) - searched, len(queries))
    return _reply(_CODE_TITLE, sections, kept, blocked or stopped is not None, max_answer_chars, trailer)


HANDLERS: Dict[str, Callable[[dict], dict]] = {
    "web": handle_web,
    "code": handle_code,
}

ACCEPTED_PARAMS = {
    "web": ACCEPTED_WEB_PARAMS,
    "code": ACCEPTED_CODE_PARAMS,
}


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_json.py :: _json_error_window, _ensure_dict
def _json_error_window(text: str, pos: int, radius: int = 48) -> str:
    """Return a repr'd slice of *text* centred on *pos*.

    A JSONDecodeError reports a character offset ("char 1530"), which the
    caller that produced the string cannot count to; the one broken escape is
    only actionable if it is shown. ``repr`` is what makes it visible -- the
    typical defect is a quote escaped one level too shallow, and a raw slice
    renders that identically to a correct one.
    """
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    lead = "..." if start > 0 else ""
    tail = "..." if end < len(text) else ""
    return f"{lead}{text[start:end]!r}{tail}"


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
            msg = f"'{name}' was a string but not valid JSON: {exc}. "
            msg += f"Near the failure: {_json_error_window(value, exc.pos)}. "
            msg += f"Pass '{name}' as an object, not a JSON-encoded string."
            raise ValueError(msg)
    if not isinstance(value, dict):
        msg = f"'{name}' must be an object (dict) or a JSON-encoded object string; "
        msg += f"got {type(value).__name__}."
        raise ValueError(msg)
    return value
# END GENERATED: e5ba86fb2715


def _status_text() -> str:
    served = ", ".join(f"{name} {ep.count}" for name, ep in _ENDPOINTS.items())
    return (
        "mcp-search OK\n"
        f"Functions: {', '.join(sorted(HANDLERS))}\n"
        f"Searches served: {served}"
    )


def handle_search_call(arguments: dict) -> dict:
    function = arguments.get("function") or arguments.get("f") or ""
    if not isinstance(function, str):
        return {"error": f"'function' must be a string; got {type(function).__name__}."}
    function = function.strip()
    raw_params = arguments.get("params") or arguments.get("p") or {}
    try:
        params = _resolve_aliases(_ensure_dict(raw_params))
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        return {"result": _status_text()}

    handler = HANDLERS.get(function)
    if handler is None:
        # FORMAT IS LOAD-BEARING: plain names, comma-separated, on ONE line --
        # tests/test_name_existence.py reads this list as the live inventory.
        return {"error": f"Unknown function: {function}. Available: {', '.join(sorted(HANDLERS))}"}

    accepted = ACCEPTED_PARAMS[function]
    unknown = sorted(set(params) - accepted)
    if unknown:
        return {"error": (
            f"Unknown params for '{function}': {', '.join(unknown)}."
            f" Accepted: {', '.join(sorted(accepted))}."
        )}

    try:
        return handler(params)
    except _EndpointBusy:
        return {"error": "endpoint busy, retry later"}


# ---------------------------------------------------------------------------
# Tool descriptor
# ---------------------------------------------------------------------------

SEARCH_CALL_TOOL = {
    "name": "search_call",
    "description": (
        "Web search (DuckDuckGo lite, Bing when DDG blocks) and public GitHub "
        "code search (grep.app). Returns markdown results.\n\n"
        "Single dispatcher -- set `function` to route:\n\n"
        "  web    params: queries (string or list, at most 10, each at most 512 "
        "chars; aliases query, q), limit (1-50, default 10)\n"
        "  code   params: queries (as web), lang (e.g. Python), repo "
        "(owner/repo), path (path prefix), limit (1-50, default 10)\n\n"
        "Both take max_answer_chars (default 24000, at most 100000); a longer "
        "answer is cut with a note. Returns server status when called without "
        "`function`.\n\n"
        "A DDG block moves that and every remaining query of the call to Bing. "
        "A query blocked by Bing (web) or grep.app (code) makes the call an "
        "error, the other queries' results still in the text. No results is "
        "not an error; a failed connection shows `transport error` for that "
        "query. Concurrent calls queue per endpoint; a wait over 120 s answers "
        "`endpoint busy, retry later`, and a call stops after 180 s; either "
        "keeps the results gathered so far and is an error. Queries and "
        "filters with control, bidi or tag characters are refused; lang, repo "
        "and path at most 512 chars. Results are third-party content, "
        "rendered without control or invisible characters; the "
        "Chrome-profile transport does not verify certificates."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "description": "Function name (web or code). Alias: 'f'.",
            },
            "params": {
                "type": "object",
                "description": "Function parameters. Alias: 'p'.",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# McpServer
# ---------------------------------------------------------------------------

# How many calls may be in flight at once. The stdin reader owns a thread of
# its own, OUTSIDE this pool, so saturating it delays queued CALLS and can never
# stop the server from READING.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_concurrency.py :: MAX_INFLIGHT_REQUESTS
MAX_INFLIGHT_REQUESTS = 8
# END GENERATED: 0ffae9f02744

# _log_value: width of one logged wire string (repr form) and of one logged key
# list (CWE-117, F19).
_LOG_VALUE_WIDTH = 80
_LOG_KEYS_SHOWN = 16


def _log_value(value: Any) -> str:
    """A STRUCTURAL wire value (method, id, tool name, argument keys) as it may
    appear in a log line: no control character, bounded length (CWE-117, F19).

    The shape of Scripts/mcp-proxy.py's _log_value, hand-written here: a str
    is cut to _LOG_VALUE_WIDTH characters BEFORE repr() (a huge wire string is
    never copied whole), then logged as that repr() -- every control character
    escaped -- capped at 4 * _LOG_VALUE_WIDTH, with "..." whenever either cut
    happened. An int or None as itself (a huge int as its bit length); a list
    (the argument KEYS) item by item, at most _LOG_KEYS_SHOWN items; anything
    else as its type name only. Never handed a payload VALUE (ADR 0011).
    """
    if isinstance(value, str):
        cut = len(value) > _LOG_VALUE_WIDTH
        r = repr(value[:_LOG_VALUE_WIDTH] if cut else value)
        text = r[:4 * _LOG_VALUE_WIDTH]
        return text + "..." if (cut or len(r) > 4 * _LOG_VALUE_WIDTH) else text
    if value is None or isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value) if value.bit_length() <= 64 else "int(%d bits)" % value.bit_length()
    if isinstance(value, list):
        shown = [type(item).__name__ if isinstance(item, list) else _log_value(item) for item in value[:_LOG_KEYS_SHOWN]]
        if len(value) > _LOG_KEYS_SHOWN:
            shown.append("+%d more" % (len(value) - _LOG_KEYS_SHOWN))
        return "[" + ", ".join(shown) + "]"
    return type(value).__name__


class McpServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        # Handlers run in executor threads; every reply is written from the
        # event-loop thread, and the lock is kept as webfetch keeps it.
        self._write_lock = threading.Lock()
        # In-flight dispatches: a strong reference for the loop, and what the
        # shutdown drains.
        self._inflight: set = set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting")
        # TWO executors and one task per request (ADR 0008): the readline owns
        # a thread no handler can take, so calls parked on a socket or on an
        # endpoint lock never stop the server from reading stdin.
        #
        # Handlers are safe to run concurrently because the only mutable
        # module state a handler reaches is _ENDPOINTS, and every _Endpoint's
        # session, count and last_request are read and written only under that
        # endpoint's own lock (with_session). A _ChSession is never touched
        # outside it, and run_web takes one endpoint lock at a time. The
        # decoders' lazy _BR_STATE/_ZSTD_STATE are each published by one dict
        # store, so a race on the first load at worst loads twice.
        #
        # The residual: all MAX_INFLIGHT_REQUESTS workers can wait on one
        # endpoint lock. Each wait is bounded by ENDPOINT_LOCK_TIMEOUT and a
        # whole call by CALL_DEADLINE (plus at most the one search running
        # when it expires), and that is accepted.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search-stdin")
        workers = ThreadPoolExecutor(max_workers=MAX_INFLIGHT_REQUESTS,
                                     thread_name_prefix="search-call")
        by_id: dict = {}  # R-0006: request id -> its in-flight task
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    # A closed or detached stdin RAISES rather than returning "".
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
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue
                # F12/CWE-532: log protocol structure only, never payload
                # values (the queries are the caller's search text).
                # Defensive: params/arguments may be a non-dict on a malformed
                # message; this is a debug log and must never crash the loop.
                _p = msg.get("params")
                _p = _p if isinstance(_p, dict) else {}
                _args = _p.get("arguments")
                _args = _args if isinstance(_args, dict) else {}
                log.debug(
                    "← method=%s id=%s fn=%s keys=%s",
                    _log_value(msg.get("method")), _log_value(msg.get("id")),
                    _log_value(_p.get("name")), _log_value(list(_args.keys())),
                )
                # R-0006: a cancel is handled HERE, on the loop thread and
                # before anything is dispatched. Reply-only: the call runs on
                # in its worker thread (it may hold an endpoint lock), only its
                # reply is suppressed.
                if (msg.get("method") == "notifications/cancelled"
                        and msg.get("id") is None):
                    self._cancel_request(msg.get("params"), by_id)
                    continue
                task = asyncio.ensure_future(self._dispatch(loop, workers, msg))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
                self._track_request(msg, task, by_id)
        finally:
            # Drain BEFORE shutting the pools down, and drain rather than
            # cancel: cancelling a task does not stop the worker thread holding
            # an endpoint lock, it only stops anyone waiting for it. Once the
            # gather returns every worker is idle, so shutdown(wait=False)
            # reclaims both pools without blocking.
            if self._inflight:
                log.debug("draining %d in-flight request(s)", len(self._inflight))
                await asyncio.gather(*self._inflight, return_exceptions=True)
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)
            log.info("MCP server shutting down")

    # R-0006: notifications/cancelled. Hand-written per server (ADR 0012).
    @staticmethod
    def _request_key(value):
        """`value` as an in-flight registry key, or None if it cannot be one.

        A bool is refused although Python calls it an int: True == 1 and they
        hash alike, so accepting it would let `requestId: true` cancel request 1.
        """
        if isinstance(value, bool):
            return None
        if isinstance(value, (str, int)):
            return value
        return None

    def _track_request(self, msg: dict, task, by_id: dict) -> None:
        """Register `task` under its request id; forget it once it is done.

        `initialize` is never registered, so the handshake cannot be cancelled.
        """
        if msg.get("method") == "initialize":
            return
        key = self._request_key(msg.get("id"))
        if key is None:
            return
        by_id[key] = task

        def _forget(done) -> None:
            # Only while the slot is still THIS task: a reused id owns it now.
            if by_id.get(key) is done:
                del by_id[key]

        task.add_done_callback(_forget)

    def _cancel_request(self, params, by_id: dict) -> None:
        """notifications/cancelled, on the loop thread. Never replies.

        An unknown, finished or malformed requestId is ignored silently.
        """
        if not isinstance(params, dict):
            return
        key = self._request_key(params.get("requestId"))
        if key is None:
            return
        task = by_id.get(key)
        if task is not None:
            log.debug("cancelling id=%s", _log_value(key))
            task.cancel()

    async def _dispatch(self, loop: asyncio.AbstractEventLoop,
                        workers: ThreadPoolExecutor, msg: dict) -> None:
        try:
            response = await loop.run_in_executor(workers, self._handle_message, msg)
        except Exception as exc:
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}",
            )
        if response is None:
            return
        self._write(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message (event-loop thread only)."""
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {type(exc).__name__}"))
        # F12/CWE-532: structure only (id + outcome), no body.
        log.debug(
            "→ id=%s %s", response.get("id"),
            "error" if "error" in response else "ok",
        )
        with self._write_lock:
            try:
                sys.stdout.write(out + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError) as exc:
                log.warning("stdout write failed: %s", exc)

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params")

        if msg_id is None:
            log.debug("Notification: %s", _log_value(method))
            return None

        # F11: params is an object or absent; anything else is the caller's
        # error (-32602), not an AttributeError reported as -32603.
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            return self._error(msg_id, -32602, f"Invalid params: expected an object, got {type(params).__name__}")

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-search", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [SEARCH_CALL_TOOL]})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)
        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                return self._tool_error(
                    msg_id,
                    f"'arguments' was a string but not valid JSON: {exc}. "
                    f"Near the failure: {_json_error_window(arguments, exc.pos)}. "
                    "Pass arguments as an object, not a JSON-encoded string.")
        if tool_name != "search_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
        if not isinstance(arguments, dict):
            return self._tool_error(
                msg_id, f"'arguments' must be an object; got {type(arguments).__name__}.")
        try:
            result = handle_search_call(arguments)
        except Exception as exc:
            # The class name only: an exception raised under a search can carry
            # the query, a body or a path in its message (FR-6).
            log.exception("Unhandled exception in handle_search_call")
            result = {"error": f"Internal server error: {type(exc).__name__}"}
        is_error = "error" in result
        text = result["error"] if is_error else result.get("result", "")
        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    @staticmethod
    def _result(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @classmethod
    def _tool_error(cls, msg_id: Any, message: str) -> dict:
        """A tool-level failure: an isError envelope, not a JSON-RPC error."""
        return cls._result(msg_id, {
            "content": [{"type": "text", "text": message}],
            "isError": True,
        })


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="mcp-search MCP server (web and code search)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()
    _configure_logging(args.debug, args.log_file)
    asyncio.run(McpServer().run())


if __name__ == "__main__":
    main()
