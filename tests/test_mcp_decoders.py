#!/usr/bin/env python3
"""The ctypes content decoders -- `Scripts/_mcp_brotli.py` and `Scripts/_mcp_zstd.py`.

Both sources are canonical generation sources REGISTERED in `Scripts/amalgamate.py`
(`CANONICAL_NAMES`, and `WHOLE_SOURCES`: a host takes each whole or not at all).
`mcp-webfetch.py`, `search_duckduckgo.py` and `search_github.py` carry them between
generated markers and inject them into the Chrome client as `_DECODERS`; the drift
gate (`amalgamate.py --check`, the generated_region suite) proves those copies
equal the source, and THIS suite proves the source right. It loads each file by
path with `H.load_module_from_path` and drives it.

THE ORACLE IS NOT THE MODULE. The expected load orders are typed here from the
module docstrings' written contract (the per-platform list), the expected
decodes are the plain originals the `brotli`/`zstd` CLIs compressed
(`tests/files/enc/README.md`), the zstd frame header is parsed by this file's
own reader written from RFC 8878, and the parameter id 100 is `ZSTD_d_windowLogMax`
from zstd.h. The published LIMITS (`ZSTD_WINDOW_LOG_MAX`, `ZSTD_MAX_CHUNK_BYTES`)
are read off the loaded module rather than typed, because the rows assert the
module honours its OWN published bound.

WHERE A HOST CAN SAY NO. The decode, limit and race rows need the real system
library. When `libbrotlidec` / `libzstd` is absent, each such row is recorded as
INFO ("SKIPPED: ...") rather than omitted, so the case count is the same on
every host, and nothing that depends on the host's library set can FAIL. The
load-order, missing-library, negative-control and structure rows run against
stubs and cannot flap; they are the ones allowed to FAIL.

Groups:
  A. LOAD ORDER:   per-platform attempts under a stubbed sys.platform and stub
                   hooks -- linux x86_64/aarch64, darwin homebrew/intel/macports,
                   freebsd, absent-everywhere on linux AND darwin; Linux makes
                   ZERO find_library calls in every scenario (FLAG-1); darwin
                   hands dlopen ONLY absolute paths -- no bare soname attempt,
                   and the real `_cdll` hook refuses a bare or relative name
                   there (F36: dyld searches the cwd for a slash-less name)
  B. DECODE:       the committed fixtures byte-exact, the empty stream included
  C. LIMITS:       the output budget at its exact boundary, bombs, truncated /
                   corrupt / trailing input, and window-27.zst: its header
                   parsed HERE first, then the refusal, then the self-check
                   that the same frame decodes once the cap is lifted to 27;
                   legacy zstd frames (v0.5-v0.7 magics, which skip
                   windowLogMax, F13) refused at the stream start AND
                   mid-stream, skippable frames still accepted
  D. MISSING LIB:  a one-line LookupError with no traceback; the host's own
                   library reported as INFO
  E. NEGATIVE:     lying stub libraries -- every broken invariant is a
                   ValueError, never bytes -- beside an honest stub that decodes
  R. RACE:         two threads on the first load, a sleeping stub loader and
                   function objects that refuse a call before their restype is
                   set; 50 rounds per codec
  K. STRUCTURE:    generator block shapes, prefixes, tab safety, free names,
                   no `assert`, with planted controls the checker must refuse
  F. HYGIENE:      no bytecode, no new repo paths

Usage:
  python3 tests/test_mcp_decoders.py            # standalone
  python3 tests/run.py mcp_decoders             # through the fleet runner
  python3 tests/test_mcp_decoders.py --brief    # one line per case

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every case passes. Writes nothing.
"""

import ast
import ctypes
import os
import re
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_decoders"

BROTLI_SOURCE = H.repo_path("Scripts", "_mcp_brotli.py")
ZSTD_SOURCE = H.repo_path("Scripts", "_mcp_zstd.py")
GENERATOR = H.repo_path("Scripts", "amalgamate.py")
ENC = H.repo_path("tests", "files", "enc")

GA = "A. LOAD ORDER: per-platform attempts under a stubbed platform"
GB = "B. DECODE: the committed fixtures byte-exact"
GC = "C. LIMITS: output budget, malformed input, the zstd window cap"
GD = "D. MISSING LIBRARY: one-line LookupError, host library as INFO"
GE = "E. NEGATIVE CONTROL: lying libraries refused, never bytes"
GF = "F. HYGIENE: no bytecode, no new repo paths"
GK = "K. STRUCTURE: block shapes, prefixes, tab safety, no assert"
GR = "R. RACE: two threads on the first load, 50 rounds"

# The Chrome client's cumulative decode budget (plan: CH_DEFAULT_DECODE_CAP).
DECODE_CAP = 64 * 1024 * 1024

RACE_ROUNDS = 50

# The load-order contract, typed from the module docstrings (not read off the
# module's tuples): Debian/Ubuntu multiarch x86_64 and aarch64, RPM lib64, plain
# /usr/lib, a local build; Homebrew Apple silicon, Homebrew Intel, MacPorts.
LINUX_DIRS = ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")
MACOS_DIRS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")

# ZSTD_dParameter ZSTD_d_windowLogMax, from zstd.h.
ZSTD_D_WINDOWLOGMAX = 100

# RFC 8878 section 3.1.1: the zstd frame magic, little-endian 0xFD2FB528.
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def legacy(low):
    """A legacy zstd frame magic, little-endian 0xFD2FB5<low> (v0.5 = 0x25 .. v0.7 = 0x27)."""
    return bytes((low,)) + b"\xb5\x2f\xfd"


def skippable(low, payload):
    """RFC 8878 section 3.1.2: magic 0x184D2A5? little-endian, 4-byte LE size, payload."""
    return bytes((low,)) + b"\x2a\x4d\x18" + len(payload).to_bytes(4, "little") + payload


def legacy_refusal(offset):
    """The decoder's one-line refusal of a frame magic it does not admit (F13)."""
    return "zstd: legacy or unknown frame refused at byte %d" % offset


class Codec:
    """Everything the shared rows need to know about one decoder source."""

    def __init__(self, key, source, hook, state, load, decompress, attempts,
                 stem, lib_label, soname_linux, soname_macos, files_linux,
                 files_macos, symbols, skip_symbol, prefixes, exceptions):
        self.key = key
        self.source = source
        self.hook = hook
        self.state = state
        self.load = load
        self.decompress = decompress
        self.attempts = attempts
        self.stem = stem
        self.lib_label = lib_label
        self.soname_linux = soname_linux
        self.soname_macos = soname_macos
        self.files_linux = files_linux
        self.files_macos = files_macos
        self.symbols = symbols
        self.skip_symbol = skip_symbol
        self.prefixes = prefixes
        self.exceptions = exceptions

    def fresh(self):
        return H.load_module_from_path("mcp_%s_under_test" % self.key, self.source)

    def linux_paths(self):
        return [d + "/" + f for d in LINUX_DIRS for f in self.files_linux]

    def macos_paths(self):
        return [d + "/" + f for d in MACOS_DIRS for f in self.files_macos]

    def expected_attempts(self, platform):
        if platform.startswith("linux"):
            return [("soname", self.soname_linux)] + [("path", p) for p in self.linux_paths()]
        if platform == "darwin":
            # No bare soname on darwin: dyld would search the cwd for it (F36).
            return [("find_library", self.stem)] + [("path", p) for p in self.macos_paths()]
        return [("soname", self.soname_linux), ("soname", self.soname_macos)]


BROTLI = Codec(
    "br", BROTLI_SOURCE, "_br_", "_BR_STATE", "_br_load", "_brotli_decompress",
    "_br_attempts", "brotlidec", "libbrotlidec", "libbrotlidec.so.1",
    "libbrotlidec.1.dylib", ("libbrotlidec.so.1", "libbrotlidec.so"),
    ("libbrotlidec.1.dylib", "libbrotlidec.dylib"),
    # brotli/decode.h
    ("BrotliDecoderCreateInstance", "BrotliDecoderDecompressStream",
     "BrotliDecoderGetErrorCode", "BrotliDecoderErrorString",
     "BrotliDecoderDestroyInstance"),
    "BrotliDecoderDecompressStream",
    ("_br_", "BR_", "_BR_"), ("_brotli_decompress",))

ZSTD = Codec(
    "zstd", ZSTD_SOURCE, "_zstd_", "_ZSTD_STATE", "_zstd_load", "_zstd_decompress",
    "_zstd_attempts", "zstd", "libzstd", "libzstd.so.1", "libzstd.1.dylib",
    ("libzstd.so.1", "libzstd.so"), ("libzstd.1.dylib", "libzstd.dylib"),
    # zstd.h
    ("ZSTD_createDStream", "ZSTD_initDStream", "ZSTD_DCtx_setParameter",
     "ZSTD_decompressStream", "ZSTD_isError", "ZSTD_getErrorName",
     "ZSTD_freeDStream", "ZSTD_DStreamOutSize"),
    # a libzstd older than 1.4.0 has no ZSTD_DCtx_setParameter
    "ZSTD_DCtx_setParameter",
    ("_zstd_", "_Zstd", "ZSTD_", "_ZSTD_"), ())

CODECS = (BROTLI, ZSTD)


# --- helpers ------------------------------------------------------------------

def problem_if(condition, message):
    return [message] if condition else []


def skip(suite, group, cid, reason):
    """Record a SKIP as an INFO case: informative, never a failure."""
    return suite.record(group, cid, (), status=H.INFO,
                        detail=["SKIPPED: %s" % reason],
                        brief="INFO | %s (skipped: %s)" % (cid, reason))


def fixture(name):
    with open(os.path.join(ENC, name), "rb") as fh:
        return fh.read()


def outcome(fn):
    """(kind, value): ("bytes", b) or ("exc", exception). Never raises."""
    try:
        return "bytes", fn()
    except Exception as exc:  # the rows below judge the type
        return "exc", exc


def expect_exc(result, exc_type, message=None, prefix=None):
    """Problems unless *result* is *exc_type* (exactly that message/prefix)."""
    kind, value = result
    if kind == "bytes":
        return ["returned %d bytes instead of raising %s" % (len(value), exc_type.__name__)]
    if type(value) is not exc_type:
        return ["raised %s(%r), wanted %s" % (type(value).__name__, str(value)[:160], exc_type.__name__)]
    text = str(value)
    if message is not None and text != message:
        return ["message %r, wanted %r" % (text, message)]
    if prefix is not None and not text.startswith(prefix):
        return ["message %r does not start with %r" % (text, prefix)]
    if "\n" in text:
        return ["message is not one line: %r" % text[:160]]
    return []


def expect_bytes(result, want):
    kind, value = result
    if kind == "exc":
        return ["raised %s(%r)" % (type(value).__name__, str(value)[:160])]
    if value != want:
        return ["decoded %d bytes, wanted %d (first mismatch at %s)" % (len(value), len(want), first_mismatch(value, want))]
    return []


def first_mismatch(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def describe(result):
    kind, value = result
    if kind == "bytes":
        return "-> %d bytes" % len(value)
    return "-> %s: %s" % (type(value).__name__, str(value)[:160])


def host_library(codec):
    """(module, None) with the real library loaded, or (module, reason)."""
    mod = codec.fresh()
    try:
        getattr(mod, codec.load)()
    except LookupError as exc:
        return mod, "%s absent on this host: %s" % (codec.lib_label, str(exc)[:200])
    return mod, None


def zstd_window_log(data):
    """(windowLog, descriptor_byte, None) or (None, None, reason) -- RFC 8878.

    Frame_Header_Descriptor bit 5 is Single_Segment_flag; when it is 0 a
    Window_Descriptor byte follows the descriptor, and
    windowLog = 10 + Exponent, Exponent = Window_Descriptor >> 3.
    """
    if len(data) < 6 or data[:4] != ZSTD_MAGIC:
        return None, None, "not a zstd frame (magic %r)" % data[:4]
    fhd = data[4]
    if (fhd >> 5) & 1:
        return None, None, "Single_Segment_flag is set: no Window_Descriptor"
    return 10 + (data[5] >> 3), data[5], None


class FakeLib:
    """A stub library carrying every symbol except *missing*."""

    def __init__(self, codec, missing=()):
        for symbol in codec.symbols:
            if symbol not in missing:
                setattr(self, symbol, self._fn())

    @staticmethod
    def _fn():
        def fn(*_args):
            return 0
        return fn


class Stubbed:
    """sys.platform + the module's host hooks replaced; ALL restored on exit.

    `_<hook>platform` stays the module's own function, so the rows also prove
    it reads sys.platform. The cache is emptied on entry and restored on exit.
    """

    def __init__(self, mod, codec, platform, present=(), libs=None, fl_answer=None):
        self.mod = mod
        self.codec = codec
        self.platform = platform
        self.present = set(present)
        self.libs = dict(libs or {})
        self.fl_answer = fl_answer
        self.events = []

    def _exists(self, path):
        self.events.append(("exists", path))
        return path in self.present

    def _cdll(self, name):
        self.events.append(("cdll", name))
        if name in self.libs:
            return self.libs[name]
        raise OSError("dlopen(%s, 0x0006): image not found" % name)

    def _find_library(self, stem):
        self.events.append(("find_library", stem))
        return self.fl_answer

    def __enter__(self):
        m, p = self.mod, self.codec.hook
        state = getattr(m, self.codec.state)
        self._saved = (sys.platform, getattr(m, p + "exists"), getattr(m, p + "cdll"), getattr(m, p + "find_library"), dict(state))
        sys.platform = self.platform
        setattr(m, p + "exists", self._exists)
        setattr(m, p + "cdll", self._cdll)
        setattr(m, p + "find_library", self._find_library)
        state.clear()
        return self

    def __exit__(self, *exc):
        m, p = self.mod, self.codec.hook
        platform, exists, cdll, find_library, saved = self._saved
        sys.platform = platform
        setattr(m, p + "exists", exists)
        setattr(m, p + "cdll", cdll)
        setattr(m, p + "find_library", find_library)
        state = getattr(m, self.codec.state)
        state.clear()
        state.update(saved)
        return False

    def load(self):
        try:
            return getattr(self.mod, self.codec.load)()
        except LookupError as exc:
            return exc

    def find_library_calls(self):
        return sum(1 for kind, _a in self.events if kind == "find_library")


# --- A. load order --------------------------------------------------------------

def load_scenarios(codec):
    """(cid, platform, present, libs, fl_answer, expected events, loaded name or None)."""
    c = codec
    lin, mac = c.linux_paths(), c.macos_paths()
    so_l, so_m = c.soname_linux, c.soname_macos
    x86, arm = lin[0], lin[2]
    lib64 = LINUX_DIRS[2] + "/" + c.files_linux[0]
    hb, intel, mp_dev = mac[0], mac[2], mac[5]
    fl = ("find_library", c.stem)
    out = []
    out.append(("linux-x86_64", "linux", {x86}, {x86: FakeLib(c)}, None,
                [("cdll", so_l), ("exists", x86), ("cdll", x86)], x86))
    out.append(("linux-aarch64", "linux", {arm}, {arm: FakeLib(c)}, None,
                [("cdll", so_l), ("exists", lin[0]), ("exists", lin[1]), ("exists", arm), ("cdll", arm)], arm))
    out.append(("linux-soname-hit", "linux", set(), {so_l: FakeLib(c)}, None,
                [("cdll", so_l)], so_l))
    out.append(("linux-absent-everywhere", "linux", set(), {}, None,
                [("cdll", so_l)] + [("exists", p) for p in lin], None))
    # F36: the bare soname WOULD load if it were tried (it is in `libs`, as a
    # dylib planted in the cwd would be); the row proves darwin never hands it
    # to dlopen and fails over to the absolute directories instead.
    out.append(("darwin-bare-soname-never-opened", "darwin", set(), {so_m: FakeLib(c)}, None,
                [fl] + [("exists", p) for p in mac], None))
    out.append(("darwin-homebrew", "darwin", {hb}, {hb: FakeLib(c)}, None,
                [fl, ("exists", hb), ("cdll", hb)], hb))
    out.append(("darwin-intel", "darwin", {intel}, {intel: FakeLib(c)}, None,
                [fl, ("exists", mac[0]), ("exists", mac[1]), ("exists", intel), ("cdll", intel)], intel))
    out.append(("darwin-macports-dev-symlink", "darwin", {mp_dev}, {mp_dev: FakeLib(c)}, None,
                [fl] + [("exists", p) for p in mac] + [("cdll", mp_dev)], mp_dev))
    out.append(("darwin-find_library-absolute", "darwin", set(), {intel: FakeLib(c)}, intel,
                [fl, ("cdll", intel)], intel))
    # The relative answer WOULD load if it were tried (it is in `libs`); the
    # row proves it is never handed to the loader.
    out.append(("darwin-find_library-relative-refused", "darwin", {mp_dev}, {c.files_macos[1]: FakeLib(c), mp_dev: FakeLib(c)}, c.files_macos[1],
                [fl] + [("exists", p) for p in mac] + [("cdll", mp_dev)], mp_dev))
    out.append(("darwin-absent-everywhere", "darwin", set(), {}, None,
                [fl] + [("exists", p) for p in mac], None))
    out.append(("freebsd-sonames-only", "freebsd13", set(), {}, None,
                [("cdll", so_l), ("cdll", so_m)], None))
    out.append(("missing-%s-skipped" % c.skip_symbol, "linux", {lib64}, {so_l: FakeLib(c, missing=(c.skip_symbol,)), lib64: FakeLib(c)}, None,
                [("cdll", so_l), ("exists", lin[0]), ("exists", lin[1]), ("exists", lin[2]), ("exists", lin[3]), ("exists", lib64), ("cdll", lib64)], lib64))
    return out


def group_load_order(suite, codec):
    k = codec.key
    mod = codec.fresh()
    suite.record(GA, "%s-no-load-at-import" % k, problem_if(
        getattr(mod, codec.state) != {},
        "the cache is %r right after import" % getattr(mod, codec.state)))

    for platform in ("linux", "darwin", "freebsd13"):
        with Stubbed(mod, codec, platform) as stub:
            got = getattr(mod, codec.attempts)()
            touched = list(stub.events)
        want = codec.expected_attempts(platform)
        problems = problem_if(got != want, "attempts %r, wanted %r" % (got, want))
        problems += problem_if(touched, "computing the attempts touched the host: %r" % touched)
        suite.record(GA, "%s-attempts-%s" % (k, platform), problems,
                     detail=["%d attempts: %s" % (len(got), ", ".join(kind for kind, _a in got))])

    # F36, the attempts half: whatever host runs the suite, the darwin branch
    # names nothing dlopen would resolve against the cwd. The find_library
    # entry carries a stem, and its ANSWER is held to isabs by the loader (the
    # darwin-find_library-relative-refused row below).
    with Stubbed(mod, codec, "darwin"):
        got = getattr(mod, codec.attempts)()
    kinds = sorted(set(kind for kind, _a in got))
    relative = [arg for kind, arg in got if kind == "path" and not os.path.isabs(arg)]
    problems = problem_if(kinds != ["find_library", "path"], "darwin attempt kinds %r, wanted only ['find_library', 'path']" % kinds)
    problems += problem_if(relative, "darwin path attempts that are not absolute: %r" % relative)
    suite.record(GA, "%s-darwin-attempts-absolute-only" % k, problems,
                 detail=["%d darwin attempts, kinds %r" % (len(got), kinds)])
    group_cdll_guard(suite, codec, mod)

    linux_fl = 0
    for cid, platform, present, libs, fl_answer, events, loaded in load_scenarios(codec):
        with Stubbed(mod, codec, platform, present, libs, fl_answer) as stub:
            result = stub.load()
            again = stub.load()
            events_after = list(stub.events)
        problems = []
        if platform.startswith("linux"):
            linux_fl += stub.find_library_calls()
            problems += problem_if(stub.find_library_calls(), "Linux called find_library %d time(s)" % stub.find_library_calls())
        problems += problem_if(events_after != events, "host probes %r, wanted %r" % (events_after, events))
        if loaded is None:
            if not isinstance(result, LookupError):
                problems.append("loaded %r where every attempt should fail" % (result,))
            else:
                text = str(result)
                problems += problem_if(not text.startswith("%s not found; tried: " % codec.lib_label), "message %r" % text[:120])
                problems += problem_if("\n" in text, "message is not one line")
                named = [arg for kind, arg in codec.expected_attempts(platform) if kind != "find_library"]
                missing = [n for n in named if n not in text]
                problems += problem_if(missing, "the message does not list %r" % missing)
                if platform == "darwin":
                    problems += problem_if("find_library(%s)=None" % codec.stem not in text, "the message does not record the find_library answer")
                problems += problem_if(not isinstance(again, LookupError) or str(again) != text, "the failure was not cached: second load %r" % (again,))
        else:
            if isinstance(result, Exception):
                problems.append("raised %r, wanted a load of %s" % (result, loaded))
            else:
                problems += problem_if(result[1] != loaded, "loaded %r, wanted %r" % (result[1], loaded))
                problems += problem_if(result[0] is not libs[loaded], "the published handle is not the one opened for %s" % loaded)
                problems += problem_if(again is not result and again != result, "the second load did not return the cached result")
        suite.record(GA, "%s-%s" % (k, cid), problems,
                     detail=["probes: %d, find_library calls: %d" % (len(events_after), stub.find_library_calls())])

    suite.record(GA, "%s-linux-zero-find_library" % k, problem_if(
        linux_fl, "the Linux scenarios made %d find_library call(s) in total" % linux_fl),
        detail=["FLAG-1: summed over every linux scenario, absent-everywhere included"])


class CdllShim:
    """Stands in for the module's `ctypes` inside the REAL `_cdll` hook: records
    every CDLL name and refuses it, so no row here ever dlopens anything."""

    def __init__(self):
        self.calls = []

    def CDLL(self, name):
        self.calls.append(name)
        raise OSError("shim: dlopen(%s) not performed" % name)


def guard_outcome(mod, codec, platform, name):
    """(result, CDLL calls) of the module's own `_cdll` hook under *platform*."""
    shim = CdllShim()
    saved = (sys.platform, mod.ctypes)
    sys.platform = platform
    mod.ctypes = shim
    try:
        result = outcome(lambda: getattr(mod, codec.hook + "cdll")(name))
    finally:
        sys.platform, mod.ctypes = saved
    return result, shim.calls


def group_cdll_guard(suite, codec, mod):
    """F36, the last-line half: the real `_cdll` hook refuses a non-absolute name on darwin."""
    k = codec.key
    problems = []
    details = []
    for name in (codec.soname_macos, codec.files_macos[1], "lib/" + codec.soname_macos, "./" + codec.soname_macos):
        result, calls = guard_outcome(mod, codec, "darwin", name)
        problems += ["%s: %s" % (name, p) for p in expect_exc(result, OSError, prefix="refused: ")]
        problems += problem_if(calls, "%s: reached ctypes.CDLL %r" % (name, calls))
        details.append("%s %s" % (name, describe(result)))
    suite.record(GA, "%s-darwin-cdll-refuses-non-absolute-name" % k, problems, detail=details)

    # Control: the guard is darwin-only and passes an absolute name, or the row
    # above could be green because the hook refuses everything.
    problems = []
    details = []
    for platform, name in (("darwin", MACOS_DIRS[0] + "/" + codec.soname_macos), ("linux", codec.soname_linux), ("freebsd13", codec.soname_macos)):
        result, calls = guard_outcome(mod, codec, platform, name)
        problems += problem_if(calls != [name], "%s %s: CDLL calls %r, wanted [%r]" % (platform, name, calls, name))
        problems += ["%s %s: %s" % (platform, name, p) for p in expect_exc(result, OSError, prefix="shim: ")]
        details.append("%s %s %s" % (platform, name, describe(result)))
    suite.record(GA, "%s-cdll-control-absolute-darwin-and-bare-linux-reach-dlopen" % k, problems, detail=details)


# --- B. decode ------------------------------------------------------------------

def readme_pins():
    with open(os.path.join(ENC, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    return text, dict((name, digest) for digest, name in re.findall(r"^([0-9a-f]{64})  (\S+)$", text, re.M))


def group_decode(suite, hosts):
    _text, pins = readme_pins()
    on_disk = sorted(n for n in os.listdir(ENC) if n != "README.md" and os.path.isfile(os.path.join(ENC, n)))
    problems = problem_if(sorted(pins) != on_disk, "README pins %r, directory holds %r" % (sorted(pins), on_disk))
    bad = [n for n in on_disk if n in pins and H.sha256_file(os.path.join(ENC, n)) != pins[n]]
    problems += problem_if(bad, "sha256 differs from the README pin: %r" % bad)
    suite.record(GB, "fixtures-match-readme-sha256", problems,
                 detail=["%d fixtures pinned" % len(pins)])

    rows = {
        "br": (("tf_page.html.br", "tf_page.html"), ("tf_text_1m.txt.br", "tf_text_1m.txt"), ("tf_empty.txt.br", "tf_empty.txt")),
        "zstd": (("tf_page.html.zst", "tf_page.html"), ("tf_text_1m.txt.zst", "tf_text_1m.txt"), ("tf_empty.txt.zst", "tf_empty.txt"), ("multi-frame.zst", "tf_text_1m.txt")),
    }
    for codec in CODECS:
        mod, reason = hosts[codec.key]
        fn = getattr(mod, codec.decompress)
        for comp, plain in rows[codec.key]:
            cid = "%s-%s" % (codec.key, comp)
            if reason:
                skip(suite, GB, cid, reason)
                continue
            result = outcome(lambda: fn(fixture(comp), DECODE_CAP))
            suite.record(GB, cid, expect_bytes(result, fixture(plain)), detail=[describe(result)])
        cid = "%s-bytearray-and-memoryview-input" % codec.key
        page = rows[codec.key][0][0]
        if reason:
            skip(suite, GB, cid, reason)
        else:
            problems = expect_bytes(outcome(lambda: fn(bytearray(fixture(page)), DECODE_CAP)), fixture("tf_page.html"))
            problems += expect_bytes(outcome(lambda: fn(memoryview(fixture(page)), DECODE_CAP)), fixture("tf_page.html"))
            suite.record(GB, cid, problems)
    mod, reason = hosts["zstd"]
    if reason:
        skip(suite, GB, "zstd-two-concatenated-page-frames", reason)
    else:
        result = outcome(lambda: mod._zstd_decompress(fixture("tf_page.html.zst") * 2, DECODE_CAP))
        suite.record(GB, "zstd-two-concatenated-page-frames", expect_bytes(result, fixture("tf_page.html") * 2), detail=[describe(result)])


# --- C. limits ------------------------------------------------------------------

def group_limits(suite, hosts):
    page = fixture("tf_page.html")
    text = fixture("tf_text_1m.txt")

    mod, reason = hosts["br"]
    br_rows = [
        ("br-bomb-refused-by-budget", lambda: mod._brotli_decompress(fixture("bomb.br"), DECODE_CAP), (OverflowError, "br: output exceeds %d bytes" % DECODE_CAP, None)),
        ("br-cap-equal-to-output-accepted", lambda: mod._brotli_decompress(fixture("tf_page.html.br"), len(page)), page),
        ("br-cap-one-below-output-refused", lambda: mod._brotli_decompress(fixture("tf_page.html.br"), len(page) - 1), (OverflowError, "br: output exceeds %d bytes" % (len(page) - 1), None)),
        ("br-multichunk-cap-equal-accepted", lambda: mod._brotli_decompress(fixture("tf_text_1m.txt.br"), len(text)), text),
        ("br-multichunk-cap-one-below-refused", lambda: mod._brotli_decompress(fixture("tf_text_1m.txt.br"), len(text) - 1), (OverflowError, "br: output exceeds %d bytes" % (len(text) - 1), None)),
        ("br-truncated-refused", lambda: mod._brotli_decompress(fixture("truncated.br"), DECODE_CAP), (ValueError, "br: truncated stream", None)),
        ("br-empty-input-refused", lambda: mod._brotli_decompress(b"", DECODE_CAP), (ValueError, None, "br: ")),
        ("br-garbage-refused", lambda: mod._brotli_decompress(b"\xff\xff\xff\xff garbage", DECODE_CAP), (ValueError, None, "br: ")),
        ("br-trailing-bytes-refused", lambda: mod._brotli_decompress(fixture("tf_page.html.br") + b"X", DECODE_CAP), (ValueError, "br: 1 trailing bytes after the end of the stream", None)),
    ]
    run_limit_rows(suite, br_rows, reason)

    mod, reason = hosts["zstd"]
    zs_rows = [
        ("zstd-bomb-refused-by-budget", lambda: mod._zstd_decompress(fixture("bomb.zst"), DECODE_CAP), (OverflowError, "zstd: output exceeds %d bytes" % DECODE_CAP, None)),
        ("zstd-cap-equal-to-output-accepted", lambda: mod._zstd_decompress(fixture("tf_page.html.zst"), len(page)), page),
        ("zstd-cap-one-below-output-refused", lambda: mod._zstd_decompress(fixture("tf_page.html.zst"), len(page) - 1), (OverflowError, "zstd: output exceeds %d bytes" % (len(page) - 1), None)),
        ("zstd-multichunk-cap-equal-accepted", lambda: mod._zstd_decompress(fixture("tf_text_1m.txt.zst"), len(text)), text),
        ("zstd-multichunk-cap-one-below-refused", lambda: mod._zstd_decompress(fixture("tf_text_1m.txt.zst"), len(text) - 1), (OverflowError, "zstd: output exceeds %d bytes" % (len(text) - 1), None)),
        ("zstd-multi-frame-over-cap-refused", lambda: mod._zstd_decompress(fixture("multi-frame.zst"), 300000), (OverflowError, "zstd: output exceeds 300000 bytes", None)),
        ("zstd-truncated-refused", lambda: mod._zstd_decompress(fixture("truncated.zst"), DECODE_CAP), (ValueError, None, "zstd: ")),
        ("zstd-empty-input-refused", lambda: mod._zstd_decompress(b"", DECODE_CAP), (ValueError, None, "zstd: ")),
        ("zstd-garbage-refused", lambda: mod._zstd_decompress(b"this is not a zstd frame at all", DECODE_CAP), (ValueError, None, "zstd: ")),
        ("zstd-corrupt-byte-refused", lambda: mod._zstd_decompress(flip_middle(fixture("tf_page.html.zst")), DECODE_CAP), (ValueError, None, "zstd: ")),
        ("zstd-trailing-garbage-refused", lambda: mod._zstd_decompress(fixture("tf_page.html.zst") + b"xx", DECODE_CAP), (ValueError, None, "zstd: ")),
        ("zstd-trailing-partial-frame-refused", lambda: mod._zstd_decompress(fixture("tf_page.html.zst") + fixture("truncated.zst"), DECODE_CAP), (ValueError, None, "zstd: ")),
        # F13 against the real library: a legacy frame never reaches it, at the
        # start or after a complete zstd1 frame; skippable frames still pass.
        ("zstd-legacy-v0.7-refused-at-start", lambda: mod._zstd_decompress(legacy(0x27) + fixture("tf_page.html.zst")[4:], DECODE_CAP), (ValueError, legacy_refusal(0), None)),
        ("zstd-legacy-v0.5-refused-mid-stream", lambda: mod._zstd_decompress(fixture("tf_page.html.zst") + legacy(0x25) + fixture("tf_page.html.zst")[4:], DECODE_CAP), (ValueError, legacy_refusal(len(fixture("tf_page.html.zst"))), None)),
        ("zstd-skippable-then-frame-decodes", lambda: mod._zstd_decompress(skippable(0x50, b"metadata") + fixture("tf_page.html.zst"), DECODE_CAP), page),
        ("zstd-frame-then-skippable-decodes", lambda: mod._zstd_decompress(fixture("tf_page.html.zst") + skippable(0x5F, b"x" * 300), DECODE_CAP), page),
    ]
    run_limit_rows(suite, zs_rows, reason)
    group_window(suite, mod, reason)


def flip_middle(data):
    bad = bytearray(data)
    bad[len(bad) // 2] ^= 0xFF
    return bytes(bad)


def run_limit_rows(suite, rows, reason):
    for cid, fn, want in rows:
        if reason:
            skip(suite, GC, cid, reason)
            continue
        result = outcome(fn)
        if isinstance(want, bytes):
            problems = expect_bytes(result, want)
        else:
            exc_type, message, prefix = want
            problems = expect_exc(result, exc_type, message, prefix)
        suite.record(GC, cid, problems, detail=[describe(result)])


def group_window(suite, mod, reason):
    readme, _pins = readme_pins()
    recorded = re.search(r"windowLog = 10 \+ \(0x([0-9a-fA-F]{2}) >> 3\) = (\d+)", readme)
    data = fixture("window-27.zst")
    log, descriptor, why = zstd_window_log(data)
    limit = mod.ZSTD_WINDOW_LOG_MAX
    problems = problem_if(why is not None, "window-27.zst: %s" % why)
    problems += problem_if(recorded is None, "tests/files/enc/README.md records no windowLog for window-27.zst")
    if why is None and recorded is not None:
        problems += problem_if(log != int(recorded.group(2)), "the header says windowLog %d, the README records %s" % (log, recorded.group(2)))
        problems += problem_if(descriptor != int(recorded.group(1), 16), "Window_Descriptor 0x%02x, the README records 0x%s" % (descriptor, recorded.group(1)))
        problems += problem_if(not log > limit, "windowLog %d does not exceed ZSTD_WINDOW_LOG_MAX %d: the refusal row would pass vacuously" % (log, limit))
    header_ok = not problems
    suite.record(GC, "zstd-window-27-header-parsed-first", problems,
                 detail=["windowLog %s (descriptor %s) vs ZSTD_WINDOW_LOG_MAX %d read off the module" % (log, "0x%02x" % descriptor if descriptor is not None else None, limit)])

    blog, _bdesc, bwhy = zstd_window_log(fixture("bomb.zst"))
    suite.record(GC, "zstd-bomb-window-under-the-cap", problem_if(
        bwhy is not None or not blog <= limit,
        "bomb.zst windowLog %s (%s): the budget row would be measuring the window cap" % (blog, bwhy)),
        detail=["bomb.zst windowLog %s <= %d, so its refusal is the size budget" % (blog, limit)])

    for cid in ("zstd-window-27-refused", "zstd-window-27-decodes-when-cap-lifted"):
        if reason:
            skip(suite, GC, cid, reason)
    if reason:
        return
    result = outcome(lambda: mod._zstd_decompress(data, DECODE_CAP))
    problems = expect_exc(result, ValueError, "zstd: Frame requires too much memory for decoding")
    problems += problem_if(not header_ok, "the header row failed, so this refusal proves nothing")
    suite.record(GC, "zstd-window-27-refused", problems, detail=[describe(result)])

    saved = mod.ZSTD_WINDOW_LOG_MAX
    try:
        mod.ZSTD_WINDOW_LOG_MAX = log if log is not None else 27
        lifted = outcome(lambda: mod._zstd_decompress(data, DECODE_CAP))
    finally:
        mod.ZSTD_WINDOW_LOG_MAX = saved
    suite.record(GC, "zstd-window-27-decodes-when-cap-lifted", expect_bytes(lifted, fixture("tf_page.html")),
                 detail=["self-check: at windowLogMax %s the same frame decodes, so the refusal above is the window cap alone" % log, describe(lifted)])


# --- D. missing library ---------------------------------------------------------

def group_missing(suite, hosts):
    for codec in CODECS:
        mod = codec.fresh()
        fn = getattr(mod, codec.decompress)
        for platform in ("linux", "darwin"):
            with Stubbed(mod, codec, platform) as stub:
                first = outcome(lambda: fn(b"\x00", DECODE_CAP))
                second = outcome(lambda: fn(b"\x00", DECODE_CAP))
                probes = len(stub.events)
            problems = expect_exc(first, LookupError, prefix="%s not found; tried: " % codec.lib_label)
            if first[0] == "exc":
                text = str(first[1])
                problems += problem_if("Traceback" in text or "File \"" in text, "the message carries a traceback: %r" % text[:160])
                problems += problem_if(len(text) > 2000, "the message is %d characters" % len(text))
            problems += expect_exc(second, LookupError, prefix="%s not found; tried: " % codec.lib_label)
            suite.record(GD, "%s-absent-%s-one-line-lookuperror" % (codec.key, platform), problems,
                         detail=["probes %d, %s" % (probes, describe(first))])
        mod, reason = hosts[codec.key]
        if reason:
            suite.record(GD, "%s-host-library" % codec.key, (), status=H.INFO,
                         detail=[reason], brief="INFO | %s-host-library (%s)" % (codec.key, reason))
        else:
            path = getattr(mod, codec.state)["result"][1]
            suite.record(GD, "%s-host-library" % codec.key, (), status=H.INFO,
                         detail=["loaded %s on %s" % (path, sys.platform)],
                         brief="INFO | %s-host-library (%s)" % (codec.key, path))


# --- E. negative control: lying libraries ---------------------------------------

BR_SUCCESS, BR_NEEDS_MORE_INPUT, BR_NEEDS_MORE_OUTPUT, BR_ERROR = 1, 2, 3, 0


class BrLiar:
    """A stub libbrotlidec whose DecompressStream answers per *mode*."""

    def __init__(self, mode):
        self.mode = mode
        self.calls = 0
        self.created = 0
        self.destroyed = 0
        self.BrotliDecoderCreateInstance = self._create
        self.BrotliDecoderDecompressStream = self._stream
        self.BrotliDecoderGetErrorCode = lambda state: -2
        self.BrotliDecoderErrorString = lambda code: b"_ERROR_FORMAT_STUB"
        self.BrotliDecoderDestroyInstance = self._destroy

    def _create(self, *_a):
        if self.mode == "create-null":
            return None
        self.created += 1
        return 0x1234

    def _destroy(self, state):
        self.destroyed += 1

    def _stream(self, state, ai, ni, ao, no, to):
        self.calls += 1
        if self.calls > 1000:
            raise RuntimeError("the decoder looped past 1000 calls")
        avail_in, avail_out, total_out = ai._obj, ao._obj, to._obj
        mode = self.mode
        if mode == "honest":
            ctypes.memmove(no._obj.value, b"abc", 3)
            avail_out.value -= 3
            total_out.value += 3
            avail_in.value = 0
            return BR_SUCCESS
        if mode == "error-result":
            return BR_ERROR
        if mode == "avail-out-over-buffer":
            avail_out.value += 1
            return BR_NEEDS_MORE_OUTPUT
        if mode == "total-out-lie":
            avail_out.value = 0
            total_out.value = 1 << 40
            return BR_NEEDS_MORE_OUTPUT
        if mode == "avail-in-grows":
            avail_in.value += 1
            return BR_NEEDS_MORE_OUTPUT
        if mode == "more-output-no-progress":
            return BR_NEEDS_MORE_OUTPUT
        if mode == "more-input-with-input-left":
            return BR_NEEDS_MORE_INPUT
        if mode == "success-with-input-left":
            return BR_SUCCESS
        if mode == "unknown-result":
            avail_in.value = 0
            return 7
        raise RuntimeError("unknown liar mode %s" % mode)


BR_LIES = (
    ("error-result", "br: _ERROR_FORMAT_STUB"),
    ("avail-out-over-buffer", None),
    ("total-out-lie", "br: decoder state inconsistent"),
    ("avail-in-grows", "br: decoder state inconsistent"),
    ("more-output-no-progress", "br: decoder state inconsistent"),
    ("more-input-with-input-left", "br: decoder state inconsistent"),
    ("success-with-input-left", "br: 10 trailing bytes after the end of the stream"),
    ("unknown-result", "br: unknown decoder result 7"),
    ("create-null", "br: decoder instance could not be created"),
)

ZSTD_ERR = (1 << 64) - 20


class ZstdLiar:
    """A stub libzstd whose streaming calls answer per *mode*."""

    def __init__(self, mode, chunk=131072):
        self.mode = mode
        self.calls = 0
        self.created = 0
        self.freed = 0
        self.params = []
        self.chunk = chunk
        self.ZSTD_createDStream = self._create
        self.ZSTD_initDStream = lambda ds: ZSTD_ERR if mode == "init-error" else 0
        self.ZSTD_DCtx_setParameter = self._set
        self.ZSTD_isError = lambda code: 1 if code == ZSTD_ERR else 0
        self.ZSTD_getErrorName = lambda code: b"Stub error"
        self.ZSTD_DStreamOutSize = lambda: self.chunk
        self.ZSTD_decompressStream = self._stream
        self.ZSTD_freeDStream = self._free

    def _create(self):
        if self.mode == "create-null":
            return None
        self.created += 1
        return 0x1234

    def _free(self, ds):
        self.freed += 1
        return 0

    def _set(self, ds, param, value):
        self.params.append((param, value))
        return ZSTD_ERR if self.mode == "setparameter-error" else 0

    def _stream(self, ds, outp, inp):
        self.calls += 1
        if self.calls > 1000:
            raise RuntimeError("the decoder looped past 1000 calls")
        out, inb = outp._obj, inp._obj
        mode = self.mode
        if mode == "honest":
            ctypes.memmove(out.dst, b"abc", 3)
            out.pos = 3
            inb.pos = inb.size
            return 0
        if mode == "error-code":
            return ZSTD_ERR
        if mode == "out-pos-over-size":
            out.pos = out.size + 1
            return 1
        if mode == "in-pos-backwards":
            inb.pos = 5 if self.calls == 1 else 2
            return 1
        if mode == "in-pos-over-size":
            inb.pos = inb.size + 7
            return 1
        if mode == "no-progress":
            return 1
        if mode == "zero-return-input-left":
            return 0
        if mode == "frame-per-10-bytes":
            # Every 10 input bytes are one complete "frame": 3 bytes out, 0 back.
            ctypes.memmove(out.dst, b"abc", 3)
            out.pos = 3
            inb.pos = min(inb.pos + 10, inb.size)
            return 0
        raise RuntimeError("unknown liar mode %s" % mode)


def group_negative(suite):
    mod = BROTLI.fresh()
    for mode, message in (("honest", None),) + BR_LIES:
        liar = BrLiar(mode)
        mod._BR_STATE["result"] = (liar, "stub")
        try:
            result = outcome(lambda: mod._brotli_decompress(b"0123456789", DECODE_CAP))
        finally:
            mod._BR_STATE.clear()
        if mode == "honest":
            problems = expect_bytes(result, b"abc")
        elif message is None:
            problems = expect_exc(result, ValueError, prefix="br: decoder reported -1 bytes into a")
        else:
            problems = expect_exc(result, ValueError, message)
        problems += problem_if(liar.destroyed != liar.created, "%d instance(s) created, %d destroyed" % (liar.created, liar.destroyed))
        suite.record(GE, "br-%s" % mode, problems,
                     detail=["%s; calls %d, created %d, destroyed %d" % (describe(result), liar.calls, liar.created, liar.destroyed)])

    mod = ZSTD.fresh()
    cap_chunk = mod.ZSTD_MAX_CHUNK_BYTES
    zrows = (
        ("honest", ZstdLiar("honest"), b"abc"),
        ("max-chunk-accepted", ZstdLiar("honest", chunk=cap_chunk), b"abc"),
        ("chunk-over-max-refused", ZstdLiar("honest", chunk=cap_chunk + 1), "zstd: decoder state inconsistent"),
        ("zero-chunk-refused", ZstdLiar("honest", chunk=0), "zstd: decoder state inconsistent"),
        ("error-code", ZstdLiar("error-code"), "zstd: Stub error"),
        ("init-error", ZstdLiar("init-error"), "zstd: Stub error"),
        ("setparameter-error", ZstdLiar("setparameter-error"), "zstd: Stub error"),
        ("create-null", ZstdLiar("create-null"), "zstd: decoder stream could not be created"),
        ("out-pos-over-size", ZstdLiar("out-pos-over-size"), None),
        ("in-pos-backwards", ZstdLiar("in-pos-backwards"), "zstd: decoder state inconsistent"),
        ("in-pos-over-size", ZstdLiar("in-pos-over-size"), "zstd: decoder state inconsistent"),
        ("no-progress", ZstdLiar("no-progress"), "zstd: decoder state inconsistent"),
        ("zero-return-input-left", ZstdLiar("zero-return-input-left"), "zstd: decoder state inconsistent"),
    )
    for cid, liar, want in zrows:
        mod._ZSTD_STATE["result"] = (liar, "stub")
        try:
            # A zstd1 magic first, so the liar -- not the F13 magic gate -- answers.
            result = outcome(lambda: mod._zstd_decompress(ZSTD_MAGIC + b"456789", DECODE_CAP))
        finally:
            mod._ZSTD_STATE.clear()
        if isinstance(want, bytes):
            problems = expect_bytes(result, want)
            problems += problem_if(liar.params != [(ZSTD_D_WINDOWLOGMAX, mod.ZSTD_WINDOW_LOG_MAX)],
                                   "setParameter calls %r, wanted [(%d, ZSTD_WINDOW_LOG_MAX=%d)]" % (liar.params, ZSTD_D_WINDOWLOGMAX, mod.ZSTD_WINDOW_LOG_MAX))
        elif want is None:
            problems = expect_exc(result, ValueError, prefix="zstd: decoder reported")
        else:
            problems = expect_exc(result, ValueError, want)
        problems += problem_if(liar.freed != liar.created, "%d stream(s) created, %d freed" % (liar.created, liar.freed))
        suite.record(GE, "zstd-%s" % cid, problems,
                     detail=["%s; calls %d, created %d, freed %d" % (describe(result), liar.calls, liar.created, liar.freed)])
    group_zstd_magic_gate(suite, mod)


def zstd_liar_run(mod, liar, data):
    mod._ZSTD_STATE["result"] = (liar, "stub")
    try:
        return outcome(lambda: mod._zstd_decompress(data, DECODE_CAP))
    finally:
        mod._ZSTD_STATE.clear()


def group_zstd_magic_gate(suite, mod):
    """F13 against an HONEST stub: a legacy magic is refused before the library
    sees a byte of it, so these rows cannot flap with the host's libzstd. Before
    the gate the honest stub decoded every one of them to b"abc"."""
    tail = b"456789"
    for low, version in ((0x25, "v0.5"), (0x26, "v0.6"), (0x27, "v0.7")):
        liar = ZstdLiar("honest")
        result = zstd_liar_run(mod, liar, legacy(low) + tail)
        problems = expect_exc(result, ValueError, legacy_refusal(0))
        problems += problem_if(liar.calls, "the library was called %d time(s) on a legacy frame" % liar.calls)
        problems += problem_if(liar.freed != liar.created, "%d stream(s) created, %d freed" % (liar.created, liar.freed))
        suite.record(GE, "zstd-legacy-%s-magic-refused" % version, problems, detail=[describe(result)])

    problems = []
    for low in range(0x50, 0x60):
        result = zstd_liar_run(mod, ZstdLiar("honest"), skippable(low, b"")[:4] + tail)
        problems += ["0x184D2A%02X: %s" % (low, p) for p in expect_bytes(result, b"abc")]
    suite.record(GE, "zstd-all-16-skippable-magics-admitted", problems,
                 detail=["0x184D2A50..0x184D2A5F each handed to an honest stub"])

    problems = []
    for head in (legacy(0x24), legacy(0x29), skippable(0x4F, b"")[:4], skippable(0x60, b"")[:4], b"", b"\x28\xb5\x2f"):
        liar = ZstdLiar("honest")
        result = zstd_liar_run(mod, liar, head + (tail if len(head) == 4 else b""))
        problems += ["%r: %s" % (head, p) for p in expect_exc(result, ValueError, legacy_refusal(0))]
        problems += problem_if(liar.calls, "%r: the library was called" % head)
    suite.record(GE, "zstd-neighbour-and-short-magics-refused", problems,
                 detail=["v0.4 0x24, 0x29, skippable 0x4F and 0x60, empty, a 3-byte zstd1 prefix"])

    # Mid-stream: the gate runs again at every frame boundary the loop sees
    # (a 0 return with input left), not only at byte 0.
    liar = ZstdLiar("frame-per-10-bytes")
    result = zstd_liar_run(mod, liar, ZSTD_MAGIC + tail + legacy(0x26) + tail)
    problems = expect_exc(result, ValueError, legacy_refusal(10))
    problems += problem_if(liar.calls != 1, "the library was called %d time(s), wanted 1 (the first frame only)" % liar.calls)
    suite.record(GE, "zstd-legacy-refused-at-second-frame", problems, detail=["%s; calls %d" % (describe(result), liar.calls)])

    liar = ZstdLiar("frame-per-10-bytes")
    result = zstd_liar_run(mod, liar, ZSTD_MAGIC + tail + skippable(0x5A, b"")[:4] + tail + ZSTD_MAGIC + tail)
    suite.record(GE, "zstd-control-three-admitted-frames-decode", expect_bytes(result, b"abc" * 3),
                 detail=["%s; calls %d" % (describe(result), liar.calls)])


# --- R. race on the first load --------------------------------------------------

class GuardFn:
    """A real ctypes function that refuses a call before its restype is set."""

    def __init__(self, real, name, violations):
        self.__dict__.update(_real=real, _name=name, _violations=violations, _restype_set=False)

    def __setattr__(self, key, value):
        if key in ("argtypes", "restype"):
            setattr(self._real, key, value)
            if key == "restype":
                self.__dict__["_restype_set"] = True
            return
        self.__dict__[key] = value

    def __call__(self, *args):
        if not self._restype_set:
            self._violations.append(self._name)
            raise RuntimeError("%s called before its restype was set" % self._name)
        return self._real(*args)


class GuardLib:
    """A real CDLL whose symbols are handed out as GuardFn objects."""

    def __init__(self, lib, violations):
        self.__dict__.update(_lib=lib, _violations=violations, _fns={})

    def __getattr__(self, name):
        fns = self.__dict__["_fns"]
        if name not in fns:
            fns[name] = GuardFn(getattr(self.__dict__["_lib"], name), name, self.__dict__["_violations"])
        return fns[name]


def group_race(suite, hosts):
    # Control: the guard itself must refuse a call on an unconfigured handle,
    # or a green "no unconfigured call" row below would prove nothing.
    host, reason = hosts["br"]
    if reason:
        skip(suite, GR, "control-guard-refuses-unconfigured-call", reason)
    else:
        violations = []
        lib = GuardLib(ctypes.CDLL(host._BR_STATE["result"][1]), violations)
        early = outcome(lambda: lib.BrotliDecoderCreateInstance(None, None, None))
        problems = expect_exc(early, RuntimeError, "BrotliDecoderCreateInstance called before its restype was set")
        problems += problem_if(violations != ["BrotliDecoderCreateInstance"], "the guard recorded %r" % violations)
        suite.record(GR, "control-guard-refuses-unconfigured-call", problems, detail=[describe(early)])
    cases ={"br": ("tf_page.html.br", "tf_page.html"), "zstd": ("tf_page.html.zst", "tf_page.html")}
    for codec in CODECS:
        cids = ["%s-race-both-threads-decode" % codec.key, "%s-race-no-unconfigured-call" % codec.key, "%s-race-one-result-key" % codec.key]
        host, reason = hosts[codec.key]
        if reason:
            for cid in cids:
                skip(suite, GR, cid, reason)
            continue
        real_name = getattr(host, codec.state)["result"][1]
        comp, plain = cases[codec.key]
        data, want = fixture(comp), fixture(plain)
        mod = codec.fresh()
        fn = getattr(mod, codec.decompress)
        state = getattr(mod, codec.state)
        violations = []
        opened = []
        wrong, bad_keys, both_loaded = [], [], 0

        def sleepy_cdll(name):
            if name != real_name:
                raise OSError("dlopen(%s): not the library under test" % name)
            time.sleep(0.005)
            lib = GuardLib(ctypes.CDLL(name), violations)
            opened.append(lib)
            return lib

        saved = getattr(mod, codec.hook + "cdll")
        setattr(mod, codec.hook + "cdll", sleepy_cdll)
        try:
            for round_no in range(RACE_ROUNDS):
                state.clear()
                del opened[:]
                results = [None, None]
                barrier = threading.Barrier(2)

                def worker(slot):
                    barrier.wait()
                    results[slot] = outcome(lambda: fn(data, DECODE_CAP))

                threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(30)
                for slot, res in enumerate(results):
                    if res is None or expect_bytes(res, want):
                        wrong.append("round %d thread %d: %s" % (round_no, slot, "hung" if res is None else describe(res)))
                if list(state) != ["result"] or not any(state["result"][0] is lib for lib in opened):
                    bad_keys.append("round %d: %r" % (round_no, list(state)))
                if len(opened) == 2:
                    both_loaded += 1
        finally:
            setattr(mod, codec.hook + "cdll", saved)
            state.clear()
        suite.record(GR, cids[0], problem_if(wrong, "%d wrong result(s): %s" % (len(wrong), wrong[:3])),
                     detail=["%d rounds x 2 threads; both threads dlopen'ed in %d rounds" % (RACE_ROUNDS, both_loaded)])
        suite.record(GR, cids[1], problem_if(violations, "%d call(s) reached a handle before its restype was set: %r" % (len(violations), sorted(set(violations)))))
        suite.record(GR, cids[2], problem_if(bad_keys, "the cache is not one published 'result' key: %s" % bad_keys[:3]))


# --- K. structure ---------------------------------------------------------------

def structure_problems(gen, label, text, prefixes, exceptions):
    """rule -> problems for one source text. Every rule is always present."""
    rules = dict((r, []) for r in ("shapes", "duplicates", "prefixes", "exceptions", "tab-safe", "free-names", "no-assert"))
    tree = ast.parse(text)
    blocks = gen.load_blocks_text(label, text)
    imports = set()
    shaped = []
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.add((alias.asname or alias.name).split(".")[0])
            continue
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shaped.append(node.name)
            continue
        name = gen.assign_name(node)
        if name is None:
            rules["shapes"].append("line %d: a %s is not a generator block shape" % (node.lineno, type(node).__name__))
            continue
        shaped.append(name)
    dupes = sorted(set(n for n in shaped if shaped.count(n) > 1))
    if dupes or len(shaped) != len(blocks):
        rules["duplicates"].append("%d block-shaped statements, %d blocks; duplicated: %r" % (len(shaped), len(blocks), dupes))
    stray = sorted(n for n in blocks if not n.startswith(tuple(prefixes)) and n not in exceptions)
    if stray:
        rules["prefixes"].append("undeclared prefix: %r" % stray)
    gone = sorted(n for n in exceptions if n not in blocks)
    if gone:
        rules["exceptions"].append("declared exception no longer exists: %r" % gone)
    unsafe = sorted(n for n, b in blocks.items() if not gen.block_is_tab_safe(b))
    if unsafe:
        rules["tab-safe"].append("not tab safe: %r" % unsafe)
    allowed = imports | set(blocks)
    leaks = dict((n, sorted(gen.free_names(b) - allowed)) for n, b in blocks.items() if gen.free_names(b) - allowed)
    if leaks:
        rules["free-names"].append("reads names outside its imports and blocks: %r" % leaks)
    asserts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if asserts:
        rules["no-assert"].append("assert at line(s) %r: python3 -O strips it" % asserts)
    return rules


SYNTH_OK = "\"\"\"doc.\"\"\"\n\nimport os\n\n_br_x = 1\n\n\ndef _br_f():\n    return os.sep\n"

SYNTH_CONTROLS = (
    ("assert", SYNTH_OK + "\n\ndef _br_g(x):\n    assert x\n    return x\n", (), "no-assert"),
    ("stray-statement", SYNTH_OK + "\nif os.sep:\n    _br_y = 2\n", (), "shapes"),
    ("bad-prefix", SYNTH_OK + "\n\ndef helper():\n    return 1\n", (), "prefixes"),
    ("duplicate-name", SYNTH_OK + "\n_br_x = 2\n", (), "duplicates"),
    ("free-name-leak", SYNTH_OK + "\n\ndef _br_h():\n    return json.dumps(1)\n", (), "free-names"),
    ("tab-unsafe", SYNTH_OK + "\n_br_t = (1,\n        2)\n", (), "tab-safe"),
    ("vanished-exception", SYNTH_OK, ("_brotli_gone",), "exceptions"),
)


def group_structure(suite):
    gen = H.load_module_from_path("amalgamate_under_test", GENERATOR)
    for codec in CODECS:
        with open(codec.source, encoding="utf-8") as fh:
            text = fh.read()
        rules = structure_problems(gen, os.path.basename(codec.source), text, codec.prefixes, codec.exceptions)
        for rule in ("shapes", "duplicates", "prefixes", "exceptions", "tab-safe", "free-names", "no-assert"):
            suite.record(GK, "%s-%s" % (codec.key, rule), rules[rule])

    br, zs = BROTLI.fresh(), ZSTD.fresh()
    problems = problem_if(br.BR_DIRS_LINUX != zs.ZSTD_DIRS_LINUX, "linux dirs differ: %r vs %r" % (br.BR_DIRS_LINUX, zs.ZSTD_DIRS_LINUX))
    problems += problem_if(br.BR_DIRS_MACOS != zs.ZSTD_DIRS_MACOS, "macOS dirs differ: %r vs %r" % (br.BR_DIRS_MACOS, zs.ZSTD_DIRS_MACOS))
    suite.record(GK, "directory-tuples-equal", problems)

    rules = structure_problems(gen, "synthetic.py", SYNTH_OK, BROTLI.prefixes, ())
    fired = sorted(r for r, p in rules.items() if p)
    suite.record(GK, "control-clean-synthetic-accepted", problem_if(fired, "a clean source tripped %r" % fired))
    for cid, text, exceptions, rule in SYNTH_CONTROLS:
        rules = structure_problems(gen, "synthetic.py", text, BROTLI.prefixes, exceptions)
        fired = sorted(r for r, p in rules.items() if p)
        suite.record(GK, "control-%s-refused" % cid, problem_if(fired != [rule], "fired %r, wanted exactly [%r]" % (fired, rule)),
                     detail=["planted defect caught by rule %s" % rule])


# --- F. hygiene -----------------------------------------------------------------

def group_hygiene(suite, pyc_before, tree_before):
    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before) if pyc_after[k] != pyc_before[k])
    suite.record(GF, "no .pyc written anywhere in the repo tree",
                 problem_if(new or touched, "new=%r touched=%r" % (new, touched)),
                 detail=["pyc before=%d after=%d" % (len(pyc_before), len(pyc_after))])
    added = sorted(H.repo_tree() - tree_before)
    suite.record(GF, "no new repo paths", problem_if(added, "%d new path(s): %s" % (len(added), added[:5])),
                 detail=["the scratch area is excluded"])


# ---------------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="the ctypes brotli and zstd decoders",
                    opts=opts, mode="grouped")
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    hosts = dict((codec.key, host_library(codec)) for codec in CODECS)
    for codec in CODECS:
        group_load_order(suite, codec)
    group_decode(suite, hosts)
    group_limits(suite, hosts)
    group_missing(suite, hosts)
    group_negative(suite)
    group_race(suite, hosts)
    group_structure(suite)
    group_hygiene(suite, pyc_before, tree_before)
    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
