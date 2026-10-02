#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the brotli content decoder (RFC 7932) over ctypes.

The domain is ONE question: decoding one compression format through the system
shared library. It is its own source and not a corner of the Chrome client
because a decoder is a collaborator the client is HANDED (`decoders=`), not a
part of the wire it speaks, and because `_mcp_zstd.py` answers the same question
for another format with another library -- two domains, duplicated on purpose,
never one shelf (ADR 0014).

**Why ctypes.** The standard library has no brotli decoder, and ADR 0024 keeps
every shipped file on a bare Python 3.9 interpreter with the stdlib alone.
`ctypes` IS the stdlib; `libbrotlidec` is a system library the addendum to ADR
0024 allows to be absent. When it is absent the decoder raises `LookupError`
and the caller reports a one-line decode error -- nothing else stops working.

**Lazy, race-free load.** Nothing is opened at import time: `_br_load` runs on
the first `_brotli_decompress` call. webfetch runs handlers on a pool, so two
threads may race that first call. `_br_load` reads `_BR_STATE.get("result")`
ONCE into a local; if nothing is published it walks the attempts, configures
every `argtypes`/`restype` on a LOCAL handle, and publishes a fully configured
handle -- or the one-line failure -- with ONE dict item store. Under the GIL a
single item store is atomic, so a reader sees no result or a complete one, never
a handle without its `restype`. Two racing threads may both `dlopen` the same
file; `dlopen` is reference-counted, both handles are configured identically,
and the last store wins with an equivalent value. `.update()` and a two-key
publication are forbidden: a reader could see the first key without the second.

**Load order, per platform** (`_br_attempts`, pure):

* `linux*`: the bare soname `libbrotlidec.so.1` (the dynamic loader's own
  search), then each of `BR_DIRS_LINUX` x `BR_FILES_LINUX` as an absolute path.
  Linux NEVER calls `ctypes.util.find_library`: on Linux it shells out to
  `ldconfig`/`gcc`/`ld`, a subprocess per miss inside a request handler, and the
  bare soname plus the explicit multiarch directories already cover what it
  would find.
* `darwin`: `find_library("brotlidec")` (in-process on macOS, no subprocess),
  then each of `BR_DIRS_MACOS` x `BR_FILES_MACOS`. NO bare soname: dyld
  searches the process cwd for a name without a slash, so a
  `libbrotlidec.1.dylib` planted in a cloned repository would be loaded and its
  constructor run (measured, F36). Every name darwin hands `ctypes.CDLL` is
  absolute, and `_br_cdll` refuses any other one there as a last line.
* any other platform: the bare sonames of both lists, nothing else.

A path attempt is tried only if the file exists, and a `find_library` answer only
if it is absolute: this module never builds a relative path. The first handle
that loads AND carries every symbol wins.

**What the decoder refuses**: output over `max_output` (`OverflowError` -- the
budget, a distinct type so the caller can tell it from corruption), a corrupt
stream (`ValueError` naming `BrotliDecoderErrorString`), a truncated stream,
trailing bytes after the end of the stream, and a library whose answers break
the call's own invariants (`ValueError("br: decoder state inconsistent")`). We
own the output buffer, reset `avail_out`/`next_out` before every call, and copy
only `produced` bytes after validating `produced`, so a wrong answer from
the library can never become an out-of-bounds read. Output accumulates in ONE
bytearray, copied `produced` bytes per pass with `ctypes.string_at`, so the
working set is the output plus one chunk until the final `bytes()` -- the one
copy a bytes return cannot avoid without knowing the size up front (F18). The large-window extension
stays OFF (the library default), so the window is bounded by the format's
16 MiB.

**Stub hooks.** `_br_platform`, `_br_exists`, `_br_cdll` and `_br_find_library`
are the only places the loader touches the host; tests replace them by name.

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
# version is pinned in the name because the decoder ABI used here is the 1.x one;
# an unversioned name could bind a future 2.x.
BR_SONAMES_LINUX = ("libbrotlidec.so.1",)


# NOT an attempt on darwin (dyld searches the cwd for a slash-less name, F36);
# only the platforms that are neither linux nor darwin try it bare.
BR_SONAMES_MACOS = ("libbrotlidec.1.dylib",)


# Explicit absolute directories, tried after the soname. Debian/Ubuntu multiarch
# (x86_64, aarch64), the RPM lib64 layout, the plain /usr/lib layout, and a local
# build. The list is duplicated in _mcp_zstd.py on purpose and a test pins the two
# equal.
BR_DIRS_LINUX = ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")


# Homebrew on Apple silicon, Homebrew on Intel, MacPorts.
BR_DIRS_MACOS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")


# The file names tried inside each directory: the versioned runtime name first,
# then the development symlink a -dev package installs.
BR_FILES_LINUX = ("libbrotlidec.so.1", "libbrotlidec.so")


BR_FILES_MACOS = ("libbrotlidec.1.dylib", "libbrotlidec.dylib")


# The output chunk handed to the decoder per call. 64 KiB bounds the overshoot
# past `max_output` to one chunk, keeps the call count for a 64 MiB body near a
# thousand, and is a buffer we allocate and own -- the library only writes into it.
BR_CHUNK_BYTES = 64 * 1024


# BrotliDecoderResult (brotli/decode.h, verified against brotli 1.2.0).
_BR_RESULT_ERROR = 0


_BR_RESULT_SUCCESS = 1


_BR_RESULT_NEEDS_MORE_INPUT = 2


_BR_RESULT_NEEDS_MORE_OUTPUT = 3


# Every symbol _br_configure requires; a library missing one is unusable.
_BR_SYMBOLS = ("BrotliDecoderCreateInstance", "BrotliDecoderDecompressStream", "BrotliDecoderGetErrorCode", "BrotliDecoderErrorString", "BrotliDecoderDestroyInstance")


# The lazy load cache. ONE key, "result", published with ONE item store:
# (lib, path) on success, ("error", message) on failure.
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
