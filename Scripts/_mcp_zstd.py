#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the zstd content decoder (RFC 8878) over ctypes.

The domain is ONE question: decoding one compression format through the system
shared library. It is its own source and not a corner of the Chrome client
because a decoder is a collaborator the client is HANDED (`decoders=`), not a
part of the wire it speaks, and because `_mcp_brotli.py` answers the same
question for another format with another library -- two domains, duplicated on
purpose, never one shelf (ADR 0014).

**Why ctypes.** The standard library has no zstd decoder on Python 3.9, and ADR
0024 keeps every shipped file on a bare Python 3.9 interpreter with the stdlib
alone. `ctypes` IS the stdlib; `libzstd` is a system library the addendum to ADR
0024 allows to be absent. When it is absent the decoder raises `LookupError`
and the caller reports a one-line decode error -- nothing else stops working.

**Lazy, race-free load.** Nothing is opened at import time: `_zstd_load` runs
on the first `_zstd_decompress` call. webfetch runs handlers on a pool, so two
threads may race that first call. `_zstd_load` reads `_ZSTD_STATE.get("result")`
ONCE into a local; if nothing is published it walks the attempts, configures
every `argtypes`/`restype` on a LOCAL handle, and publishes a fully configured
handle -- or the one-line failure -- with ONE dict item store. Under the GIL a
single item store is atomic, so a reader sees no result or a complete one, never
a handle without its `restype`. Two racing threads may both `dlopen` the same
file; `dlopen` is reference-counted, both handles are configured identically,
and the last store wins with an equivalent value. `.update()` and a two-key
publication are forbidden: a reader could see the first key without the second.

**Load order, per platform** (`_zstd_attempts`, pure):

* `linux*`: the bare soname `libzstd.so.1` (the dynamic loader's own search),
  then each of `ZSTD_DIRS_LINUX` x `ZSTD_FILES_LINUX` as an absolute path.
  Linux NEVER calls `ctypes.util.find_library`: on Linux it shells out to
  `ldconfig`/`gcc`/`ld`, a subprocess per miss inside a request handler, and the
  bare soname plus the explicit multiarch directories already cover what it
  would find.
* `darwin`: `find_library("zstd")` (in-process on macOS, no subprocess), then
  each of `ZSTD_DIRS_MACOS` x `ZSTD_FILES_MACOS`. NO bare soname: dyld searches
  the process cwd for a name without a slash, so a `libzstd.1.dylib` planted in
  a cloned repository would be loaded and its constructor run (measured, F36).
  Every name darwin hands `ctypes.CDLL` is absolute, and `_zstd_cdll` refuses
  any other one there as a last line.
* any other platform: the bare sonames of both lists, nothing else.

A path attempt is tried only if the file exists, and a `find_library` answer only
if it is absolute: this module never builds a relative path. The first handle
that loads AND carries every symbol wins; a libzstd older than 1.4.0 has no
`ZSTD_DCtx_setParameter`, fails configuration, and the next attempt is tried.

**What the decoder refuses**: output over `max_output` (`OverflowError` -- the
budget, a distinct type so the caller can tell it from corruption), a frame whose
header demands a window above `1 << ZSTD_WINDOW_LOG_MAX` (`ValueError` naming
`ZSTD_getErrorName`), a frame whose magic is neither zstd1 nor skippable
(`ValueError("zstd: legacy or unknown frame refused at byte N")` -- a legacy
v0.x frame is decoded by a path that ignores `ZSTD_d_windowLogMax`, F13), a
corrupt frame, a truncated frame, trailing bytes that are not a frame, and a
library whose answers break the call's own invariants
(`ValueError("zstd: decoder state inconsistent")`). Concatenated frames are
accepted and decoded in order; the magic is checked at byte 0 and again at every
frame boundary the loop observes (a 0 return with input left). We own the output
buffer, reset `size`/`pos`/`dst` of the output descriptor before every call, and
copy only `produced` bytes after validating `produced`, so a wrong answer from
the library can never become an out-of-bounds read. Output accumulates in ONE
bytearray, copied `produced` bytes per pass with `ctypes.string_at`, so the
working set is the output plus one chunk until the final `bytes()` -- the one
copy a bytes return cannot avoid without knowing the size up front (F18).

**Stub hooks.** `_zstd_platform`, `_zstd_exists`, `_zstd_cdll` and
`_zstd_find_library` are the only places the loader touches the host; tests
replace them by name.

**Tab safety is a constraint on how this file is WRITTEN.** Hosts that indent
with tabs receive these blocks through `Scripts/amalgamate.py`, whose
`block_is_tab_safe` refuses a line joined inside an open bracket, so every call
and literal here fits one physical line.

**Block contract.** A block reads only builtins, the stdlib names this module
imports (`ctypes`, `os`, `sys`), its own arguments and the blocks co-listed on
the same marker. No annotation names a `typing` symbol.

**No server imports this module**, for the reasons `_mcp_json.py` gives. The
test fleet does: `tests/test_mcp_decoders.py` loads it directly.
"""

import ctypes.util
import os
import sys


# Step (1) on Linux and the other non-darwin platforms: the bare soname, handed to
# the dynamic loader's own search (glibc never searches the cwd). The major
# version is pinned in the name because the streaming ABI used here is the 1.x
# one; an unversioned name could bind a future 2.x.
ZSTD_SONAMES_LINUX = ("libzstd.so.1",)


# NOT an attempt on darwin (dyld searches the cwd for a slash-less name, F36);
# only the platforms that are neither linux nor darwin try it bare.
ZSTD_SONAMES_MACOS = ("libzstd.1.dylib",)


# Explicit absolute directories, tried after the soname. Debian/Ubuntu multiarch
# (x86_64, aarch64), the RPM lib64 layout, the plain /usr/lib layout, and a local
# build. The list is duplicated in _mcp_brotli.py on purpose and a test pins the
# two equal.
ZSTD_DIRS_LINUX = ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")


# Homebrew on Apple silicon, Homebrew on Intel, MacPorts.
ZSTD_DIRS_MACOS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")


# The file names tried inside each directory: the versioned runtime name first,
# then the development symlink a -dev package installs.
ZSTD_FILES_LINUX = ("libzstd.so.1", "libzstd.so")


ZSTD_FILES_MACOS = ("libzstd.1.dylib", "libzstd.dylib")


# The largest window a frame may demand, as a power of two: 23 is 8 MiB. RFC 9659
# lets an HTTP `zstd` decoder refuse a window above 8 MB, and the streaming decoder
# allocates the window up front, so this is the ceiling on what one response can
# make us allocate before a single byte is produced. Without it the library default
# (ZSTD_WINDOWLOG_LIMIT_DEFAULT, zstd.h) would accept 1 << 27 = 128 MiB.
ZSTD_WINDOW_LOG_MAX = 23


# The largest output chunk we will allocate on the library's say-so. The chunk is
# ZSTD_DStreamOutSize() (128 KiB in 1.5.x, one full block); an answer outside
# 1..1 MiB is a library breaking its own contract, refused rather than allocated.
ZSTD_MAX_CHUNK_BYTES = 1024 * 1024


# ZSTD_dParameter ZSTD_d_windowLogMax (zstd.h, verified against zstd 1.5.7).
_ZSTD_D_WINDOW_LOG_MAX = 100


# RFC 8878 3.1.1: the zstd1 frame magic 0xFD2FB528, little-endian on the wire.
_ZSTD_FRAME_MAGIC = b"\x28\xb5\x2f\xfd"


# RFC 8878 3.1.2: skippable frames are 0x184D2A50..0x184D2A5F little-endian, so
# bytes 1..3 are fixed and byte 0 is 0x50..0x5F. Every other magic -- the legacy
# v0.x ones (0xFD2FB525..0xFD2FB527 and older) in particular -- is refused.
_ZSTD_SKIPPABLE_TAIL = b"\x2a\x4d\x18"


# Every symbol _zstd_configure requires; a library missing one is unusable.
_ZSTD_SYMBOLS = ("ZSTD_createDStream", "ZSTD_initDStream", "ZSTD_DCtx_setParameter", "ZSTD_decompressStream", "ZSTD_isError", "ZSTD_getErrorName", "ZSTD_freeDStream", "ZSTD_DStreamOutSize")


# The lazy load cache. ONE key, "result", published with ONE item store:
# (lib, path) on success, ("error", message) on failure.
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
