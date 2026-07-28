#!/usr/bin/env python3
"""Functional suite for the semantic-navigation half of `purity_call` (groups A-I).

The question this suite answers: did `purity_call` really absorb the retired
`mcp-clangd` / `mcp-cuda` / `mcp-lua-lsp` servers, or did capability quietly go
missing when they were unregistered?

It drives the REAL server -- one long-lived `python3 Scripts/mcp-purity.py
--project-root <repo>` child over line-delimited JSON-RPC 2.0 -- against the
committed fixtures in tests/files/, and asserts on the rendered Markdown.

The one capability that HAD gone missing -- `textDocument/typeDefinition`, which
only the retired mcp-lua-lsp ever wrapped -- has since been ported into purity
as `find_type_definition` and is now available for EVERY backend, clangd
included. Groups B/D assert it on the C and Lua fixtures, group G pins the gap
as CLOSED, and group H proves parity with the retired implementation.

Coverage by group:
  A  dispatcher surface: the function inventory lists every legacy prefixed
     name (including all four find_type_definition spellings); an unknown name
     is rejected
  B  C via the clangd backend: go-to-definition (by name AND cross-file by
     position), go-to-TYPE-definition, reference COUNT + exact site set,
     type_at, outline, workspace symbol, inlay hints, symbol_context, change
     impact, honest-error class
  C  C diagnostics: tf_broken.c reports EXACTLY the expected diagnostic set,
     covering BOTH planted defects at their planted LINES; three healthy C
     files report clean (the primary one twice-sampled)
  D  Lua via the luals backend: same shape as B
  E  Lua diagnostics: tf_broken.lua reports the planted defects; healthy Lua
     files report clean
  F  legacy alias routing matrix: all 14 clangd_*, 14 cuda_* and 14 luals_*
     names the retired skills advertised resolve to a real handler, plus the
     two prefixed type-definition spellings the retired clangd never had
  G  GAPS: names the retired servers had that purity does NOT resolve -- and
     the one that has since CLOSED, asserted as a real answer
  H  A/B against the still-on-disk retired servers, so "purity returns nothing
     here" can be told apart from "the retired server returned nothing too"
  I  hygiene: no repo writes (including LSP index caches), no .pyc, fixtures
     byte-identical, plus the measured warm-up latencies

Why the wall clock is ~45 seconds: purity's `_ensure_backend` awaits the FULL
backend handshake before serving the first semantic call -- deliberately, so a
query cannot reach a server that has not answered `initialize` yet. It no longer
costs a fixed minute per backend: that handshake used to end in an unconditional
`wait_for(self._indexing_done.wait(), timeout=60.0)`, and since this repo has no
compile_commands.json neither backend ever emits an indexing `$/progress`, so the
event could never be set and the 60s deadline could only EXPIRE -- twice, once
per backend. The wait is now gated on indexing actually announcing itself, which
took the first useful answer from ~61.6s to ~3.7s per backend. Group I records
the measured numbers, so a regression here shows up as a timing, not a hunch.

Skipping is graceful, never fatal: with no `clangd` on the box the C groups
degrade to INFO, same for `lua-language-server` and the Lua groups, so
`tests/run.py` stays exit-0 on a machine without the toolchain.

Usage:
  python3 tests/test_purity_lsp.py
  python3 tests/test_purity_lsp.py --show <case-id-substring> [...]
Exit code 0 iff every case passes.
"""

import os
import re
import shutil
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "purity_lsp"
SERVER = H.repo_path("Scripts", "mcp-purity.py")

# The retired servers, still on disk but no longer registered as MCP servers.
# Group H spawns them directly for A/B evidence; absent files simply SKIP.
RETIRED_LUALS = H.repo_path("Scripts", "mcp-lua-lsp.py")
RETIRED_CLANGD = H.repo_path("Scripts", "mcp-clangd.py")
SKILL_LUALS = H.repo_path("ClaudeCode", "skills", "mcp-luals", "SKILL.md")

FIXTURE_C = H.repo_path("tests", "files", "c")
FIXTURE_LUA = H.repo_path("tests", "files", "lua")

# Fixture paths as the server sees them (relative to --project-root).
C_HDR = "tests/files/c/tf_math.h"
C_DEF = "tests/files/c/tf_math.c"
C_MAIN = "tests/files/c/tf_main.c"
C_BROKEN = "tests/files/c/tf_broken.c"
L_LIB = "tests/files/lua/tf_mathlib.lua"
L_CONS = "tests/files/lua/tf_consumer.lua"
L_BROKEN = "tests/files/lua/tf_broken.lua"

# Wall-clock budgets. Generous but FINITE: a hang must surface as a failure.
RPC_TIMEOUT = 300.0        # per JSON-RPC response, covers a cold 60s handshake
WARMUP_DEADLINE = 200.0    # poll budget for the first answer from a backend
POLL_DEADLINE = 40.0       # poll budget once a backend is warm
POLL_INTERVAL = 2.0
DIAG_TIMEOUT = 2.0         # per-call LSP diagnostics wait (values are cached)

# purity resolves LSP binaries from these dirs BEFORE falling back to PATH
# (_TRUSTED_LSP_BIN_DIRS in Scripts/mcp-purity.py); mirror that so our
# skip-detection matches what the server will actually find.
TRUSTED_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
                    "/opt/local/bin")

# ---------------------------------------------------------------------------
# Repo-hygiene exceptions -- constraint: never silently accept a repo write.
#
# EMPIRICAL FINDING (clangd 19.1.7 / lua-language-server 3.17.1, this repo):
# a full run creates ZERO new paths under the repo root. clangd is launched
# with --background-index and cwd=<project_root>, which normally persists index
# shards to `<project_root>/.cache/clangd/`, but it only does that for files
# that come from a compilation database. This repo ships no
# compile_commands.json (see tests/files/README.md), clangd logs "Failed to
# find compilation database" and indexes the fixtures from in-memory fallback
# commands only, so nothing is written. lua-language-server writes neither a
# log nor a cache dir here.
#
# Therefore this list stays EMPTY and group I asserts strictly. If a future
# change (e.g. committing a compile_commands.json) makes `.cache/clangd`
# appear, add the exact relative path here WITH a comment saying why it is
# unavoidable -- do not delete the assertion.
# ---------------------------------------------------------------------------
LSP_CACHE_EXCEPTIONS = ()

# Directories excluded from the repo tree snapshot: .git churns on its own and
# .claude/tmp is the sanctioned scratch area.
HYGIENE_SKIP_PREFIXES = (".git", os.path.join(".claude", "tmp"))


# ---------------------------------------------------------------------------
# The inventories the retired servers exposed, transcribed from their own
# HANDLERS tables (Scripts/mcp-clangd.py:1536, Scripts/mcp-cuda.py:1782,
# Scripts/mcp-lua-lsp.py:1426) and cross-checked against the skills that
# documented them (ClaudeCode/skills/mcp-{clangd,cuda,luals}/SKILL.md).
# ---------------------------------------------------------------------------

RETIRED_CLANGD_NAMES = (
    "clangd_init", "clangd_find_definition", "clangd_find_definition_at",
    "clangd_find_references", "clangd_find_references_at",
    "clangd_find_implementations_at", "clangd_workspace_symbols",
    "clangd_document_outline", "clangd_symbol_context", "clangd_inlay_hints",
    "clangd_symbol_change_impact", "clangd_hover", "clangd_diagnostics",
    "clangd_deduced_type_at",
)

RETIRED_CUDA_NAMES = tuple(n.replace("clangd_", "cuda_", 1)
                           for n in RETIRED_CLANGD_NAMES)

RETIRED_LUALS_NAMES = (
    "luals_init", "luals_find_definition", "luals_find_definition_at",
    # Once the suite's headline GAP, now a direct HANDLERS key: purity ported
    # textDocument/typeDefinition (see CLOSED_CAPABILITY below).
    "luals_find_type_definition_at",
    "luals_find_references", "luals_find_implementations_at",
    "luals_workspace_symbols", "luals_document_outline",
    "luals_symbol_context", "luals_inlay_hints",
    "luals_symbol_change_impact", "luals_hover", "luals_diagnostics",
)

# The capability gap that CLOSED. `luals_find_type_definition_at` was the ONE
# thing the retired mcp-lua-lsp could do that purity could not; it is now
# handle_find_type_definition, registered under the canonical short name plus
# all three legacy prefixes -- so clangd (C/C++/ObjC/CUDA) gained a capability
# the retired mcp-clangd never had. Group G asserts every spelling ANSWERS,
# group H asserts parity with the retired implementation.
CLOSED_CAPABILITY = ("luals_find_type_definition_at",)

# All four registered spellings of the ported handler. Group A asserts the
# dispatcher inventory lists every one of them.
TYPEDEF_SPELLINGS = ("find_type_definition", "clangd_find_type_definition_at",
                     "cuda_find_type_definition_at",
                     "luals_find_type_definition_at")

# Retired luals names that purity still does NOT register. Group G pins each one
# so the gap cannot silently change shape; the report calls them out. These are
# the five singular spellings -- FUNCTION_ALIASES entries in
# Scripts/mcp-lua-lsp.py:1442-1446; purity registers only the plural forms, and
# no skill documents the loss.
GAP_SINGULAR_ALIASES = ("luals_workspace_symbol", "luals_find_reference",
                        "luals_find_implementation_at", "luals_inlay_hint",
                        "luals_diagnostic")


# ---------------------------------------------------------------------------
# Expected fixture facts. Derived from tests/files/ and asserted EXACTLY.
# ---------------------------------------------------------------------------

# tf_math.h declares, tf_math.c defines, tf_main.c calls twice.
C_REFS_ADD = {
    "tests/files/c/tf_math.h:35:10",   # declaration
    "tests/files/c/tf_math.c:12:10",   # definition
    "tests/files/c/tf_main.c:18:17",   # call 1
    "tests/files/c/tf_main.c:19:19",   # call 2
}
C_REFS_SCALE = {
    "tests/files/c/tf_math.h:43:10",
    "tests/files/c/tf_math.c:23:10",
    "tests/files/c/tf_main.c:20:18",
}
C_REFS_LENGTH = {
    "tests/files/c/tf_math.h:50:8",
    "tests/files/c/tf_math.c:34:8",
    "tests/files/c/tf_broken.c:20:15",
    "tests/files/c/tf_main.c:22:43",
}
# Positional (textDocument/references from a call site) omits the header
# declaration -- clangd answers for the definition's symbol, not the decl.
C_REFS_ADD_AT = {
    "tests/files/c/tf_main.c:18:17",
    "tests/files/c/tf_main.c:19:19",
    "tests/files/c/tf_math.c:12:10",
}

# textDocument/typeDefinition through clangd. NOTE the two DIFFERENT answers,
# which is exactly why this is not the same query as find_definition:
#   * from a VARIABLE (or a call's return value) -> the typedef NAME,
#     `} tf_vec_t;` at tf_math.h:27:3;
#   * from the typedef name ITSELF -> one hop further, to the underlying struct
#     TAG, `typedef struct tf_vec {` at tf_math.h:22:16.
# The enum behaves the same way: the `tf_unit` FIELD resolves to `} tf_unit_t;`.
C_TYPEDEF_VEC = "tests/files/c/tf_math.h:27:3"      # } tf_vec_t;
C_TYPEDEF_VEC_TAG = "tests/files/c/tf_math.h:22:16"  # struct tf_vec tag
C_TYPEDEF_UNIT = "tests/files/c/tf_math.h:19:3"     # } tf_unit_t;

# textDocument/typeDefinition through lua-language-server. Column 1, not the
# name column find_definition reports (tf_mathlib.lua:24:20): luals points at
# the whole `function ...` declaration that carries the annotated type, not at
# the identifier.
L_TYPEDEF_ADD = "tests/files/lua/tf_mathlib.lua:24:1"
L_TYPEDEF_SCALE = "tests/files/lua/tf_mathlib.lua:31:1"
L_TYPEDEF_NEWVEC = "tests/files/lua/tf_mathlib.lua:17:1"
L_TYPEDEF_LENGTH = "tests/files/lua/tf_mathlib.lua:37:1"

# tfAdd: two call sites in tf_consumer.lua plus the definition -- and a FOURTH
# hit at tf_consumer.lua:4:48, which is inside a doc COMMENT. That comes from
# LuaLsClient.supplemental_references -> _lua_text_references, the deliberate
# text-grep supplement purity merges into name-based Lua queries to cover
# dynamic dispatch. It is not an LSP reference; the positional query below has
# only the three real ones. Asserted, not tolerated.
L_REFS_ADD = {
    "tests/files/lua/tf_consumer.lua:12:24",
    "tests/files/lua/tf_consumer.lua:13:26",
    "tests/files/lua/tf_mathlib.lua:24:20",
    "tests/files/lua/tf_consumer.lua:4:48",   # doc comment, grep supplement
}
L_REFS_ADD_AT = {
    "tests/files/lua/tf_consumer.lua:12:24",
    "tests/files/lua/tf_consumer.lua:13:26",
    "tests/files/lua/tf_mathlib.lua:24:20",
}
L_REFS_SCALE = {
    "tests/files/lua/tf_consumer.lua:14:25",
    "tests/files/lua/tf_mathlib.lua:31:20",
}
L_REFS_NEWVEC = {
    "tests/files/lua/tf_consumer.lua:10:22",
    "tests/files/lua/tf_consumer.lua:11:22",
    "tests/files/lua/tf_mathlib.lua:17:20",
    "tests/files/lua/tf_mathlib.lua:25:19",
    "tests/files/lua/tf_mathlib.lua:32:19",
}

# The EXACT diagnostic set each broken fixture must produce, as
# (severity, line, character, "code, source") -- the identity of a diagnostic,
# independent of how many times the language server happens to push it.
#
# Why a SET and not a raw row count: lua-language-server sometimes emits the
# same `unused-function` Hint twice in one publishDiagnostics payload (observed
# when its workspace cache is warm and indexing finishes in ~2s instead of
# timing out at 60s). A raw `len(rows) == 5` therefore flakes between 5 and 6.
# The set below is a STRICTER assertion, not a looser one: any genuinely
# different diagnostic -- a new code, a moved line, a changed column -- breaks
# it. Byte-identical repeats are collapsed and then reported separately, so the
# duplication stays visible instead of being swallowed.
C_DIAG_SET = {
    ("Error", 20, 29, "typecheck_convert_incompatible, clang"),
    ("Error", 22, 15, "-Wimplicit-function-declaration, clang"),
}
L_DIAG_SET = {
    ("Error", 14, 3, "action-after-return, Lua Syntax Check."),
    ("Error", 19, 21, "miss-symbol, Lua Syntax Check."),
    ("Error", 12, 7, "miss-end, Lua Syntax Check."),
    ("Hint", 12, 7, "unused-function, Lua Diagnostics."),
    ("Warning", 14, 10, "undefined-global, Lua Diagnostics."),
}

# tf_broken.c planted defects, from its own header comment.
C_PLANTED = [
    (20, "struct passed by value where const tf_vec_t * is required",
     ["const tf_vec_t *", "incompatible"]),
    (22, "call to the never-declared tf_undeclared_helper",
     ["tf_undeclared_helper", "undeclared"]),
]

# tf_broken.lua planted defects. NOTE the line attribution: the unclosed `if`
# opens on line 13, but lua-language-server does not point at 13 -- it reports
# `miss-end` against the enclosing function header (12) and `miss-symbol`
# against EOF (19). The undefined global IS reported on its own line (14).
L_PLANTED = [
    (12, "miss-end", "unclosed `if` swallows the function's `end`", ["end"]),
    (19, "miss-symbol", "EOF reached with an `end` still missing", ["end"]),
    (14, "undefined-global", "read of an undefined global",
     ["tfUndefinedGlobal"]),
]


# ---------------------------------------------------------------------------
# Markdown parsing -- the server answers rendered Markdown, not JSON
# ---------------------------------------------------------------------------

RX_REF_COUNT = re.compile(r"^# References.*?— (\d+) found", re.M)
RX_DIAG_COUNT = re.compile(r"^# Diagnostics: `[^`]+` — (\d+)", re.M)
RX_SYM_COUNT = re.compile(r"^# Symbols: `[^`]+` — (\d+)", re.M)
RX_DEF_COUNT = re.compile(r"^# Definition(?:: `[^`]+`)? \((\d+)\)", re.M)
RX_TYPEDEF_COUNT = re.compile(r"^# Type definition \((\d+)\)", re.M)
RX_IMPL_COUNT = re.compile(r"^# Implementations \((\d+)\)", re.M)
RX_HINT_COUNT = re.compile(r"^# Inlay hints \((\d+)\)", re.M)
RX_BULLET_LOC = re.compile(r"^- (?:\*\*\w+\*\* )?`([^`]+:\d+:\d+)`", re.M)
RX_HEAD_LOC = re.compile(r"^#{2,3} `([^`]+:\d+:\d+)`", re.M)
RX_OUTLINE_ROW = re.compile(r"^\s*- \*\*(\w+)\*\* `([^`]+)` \(L(\d+)\)", re.M)
RX_DIAG_ROW = re.compile(
    r"^- \*\*(\w+)\*\* `([^`]+):(\d+):(\d+)`(?: \(([^)]*)\))?: (.*)$", re.M)
RX_OLD_HINT_COUNT = re.compile(r"^## Inlay Hints \((\d+)\)", re.M)


def num(rx, text):
    """First integer captured by `rx`, or None."""
    m = rx.search(text)
    return int(m.group(1)) if m else None


def bullet_locs(text):
    return RX_BULLET_LOC.findall(text)


def head_locs(text):
    return RX_HEAD_LOC.findall(text)


def outline_rows(text):
    """[(kind, symbol, line), ...] from an outline reply."""
    return [(k, s, int(n)) for k, s, n in RX_OUTLINE_ROW.findall(text)]


def diag_rows(text):
    """[{severity, path, line, character, code, message}, ...]."""
    out = []
    for sev, path, line, char, code, msg in RX_DIAG_ROW.findall(text):
        out.append({"severity": sev, "path": path, "line": int(line),
                    "character": int(char), "code": code or "", "message": msg})
    return out


def diag_identity(rows):
    """{(severity, line, character, code), ...} -- what was reported, once each."""
    return {(r["severity"], r["line"], r["character"], r["code"]) for r in rows}


def diag_dedup(rows):
    """Rows with byte-identical repeats collapsed (full row as the key)."""
    seen, out = set(), []
    for r in rows:
        key = (r["severity"], r["line"], r["character"], r["code"], r["message"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def unknown_function(text):
    return text.startswith("Unknown function:")


# ---------------------------------------------------------------------------
# Toolchain / hygiene probes
# ---------------------------------------------------------------------------

def have_binary(name):
    """Absolute path to `name` the way purity resolves it, or None.

    Never raises and never runs the binary -- a missing toolchain must turn
    into a SKIP, not a crash (shutil.which, per the suite contract).
    """
    for directory in TRUSTED_BIN_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name)


def repo_tree(root=H.REPO_ROOT):
    """Set of repo-relative paths (dirs end in '/'), minus the skip prefixes."""
    out = set()
    for dirpath, dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        rel = "" if rel == "." else rel
        if rel.startswith(HYGIENE_SKIP_PREFIXES):
            dirs[:] = []
            continue
        for name in dirs:
            out.add(os.path.join(rel, name) + "/")
        for name in files:
            out.add(os.path.join(rel, name))
    return {p for p in out if not p.startswith(HYGIENE_SKIP_PREFIXES)}


# ---------------------------------------------------------------------------
# Server driver: a JsonRpcClient plus a stderr drainer
# ---------------------------------------------------------------------------

class Driver:
    """One MCP server child, with its stderr drained on a background thread.

    Draining matters: `H.JsonRpcClient` gives the child a stderr PIPE that
    nothing reads, and purity forwards its clangd / lua-language-server child's
    stderr straight onto it. clangd logs ~6 kB per run here -- comfortably
    under the 64 kB pipe buffer, but a chattier toolchain would eventually
    block the LSP on a full pipe, which would look like a mystery hang. The
    drained text is also evidence: group I reports its size.
    """

    def __init__(self, argv, tool, timeout=RPC_TIMEOUT):
        self.spawned_at = time.time()
        self.cli = H.JsonRpcClient(argv, tool=tool, cwd=H.REPO_ROOT,
                                   timeout=timeout, client_name="ph-purity-lsp")
        self._err = []
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            for line in self.cli.proc.stderr:
                self._err.append(line)
        except Exception as exc:                       # pipe closed on shutdown
            self._err.append("<stderr drain ended: %r>\n" % (exc,))

    @property
    def stderr_text(self):
        return "".join(self._err)

    def call(self, function, params=None):
        """(is_error, text). A dead/hung server becomes a readable error text,
        never an exception that aborts the whole suite."""
        try:
            return self.cli.call_tool(function, params or {})
        except Exception as exc:
            return True, "DRIVER-ERROR %s: %s" % (type(exc).__name__, exc)

    def close(self):
        try:
            self.cli.close()
        except Exception:
            pass


def poll(driver, function, params, ready, deadline=POLL_DEADLINE,
         interval=POLL_INTERVAL):
    """Call until `ready(text)` or the deadline expires.

    An LSP backend may legitimately answer empty before its index is warm, so
    every semantic assertion goes through here rather than trusting the first
    reply. Returns (is_error, text, elapsed_seconds, attempts).
    """
    started = time.time()
    attempts = 0
    err, text = True, ""
    while True:
        attempts += 1
        err, text = driver.call(function, params)
        if ready(text):
            break
        if time.time() - started >= deadline:
            break
        time.sleep(interval)
    return err, text, time.time() - started, attempts


def nonempty(text):
    """Default readiness: a real answer, not an empty/`not found` placeholder."""
    if not text or text.startswith("DRIVER-ERROR"):
        return False
    low = text.lower()
    for marker in ("not found in workspace", "_(none)_", "_(no ",
                   "no hover/type information", "no implementations found",
                   "no type definition found"):
        if marker in low:
            return False
    return True


# ---------------------------------------------------------------------------
# Case recorders
# ---------------------------------------------------------------------------

def check(suite, group, cid, problems, detail=(), status=None, text=""):
    return suite.record(group, cid, problems, status=status, detail=list(detail),
                        text=text, showable=True)


def expect(suite, driver, group, cid, function, params, ready=nonempty,
           must=(), must_not=(), want_error=False, deadline=POLL_DEADLINE,
           detail=()):
    """Poll one function and assert isError plus substrings of the Markdown."""
    err, text, secs, tries = poll(driver, function, params, ready,
                                  deadline=deadline)
    problems = []
    if err != want_error:
        problems.append("isError=%s expected %s" % (err, want_error))
    for needle in must:
        if needle not in text:
            problems.append("MISSING %r" % needle)
    for needle in must_not:
        if needle in text:
            problems.append("UNEXPECTED %r" % needle)
    check(suite, group, cid, problems,
          detail=list(detail) + ["%.2fs, %d attempt(s)" % (secs, tries)],
          text=text)
    return text


def expect_locs(suite, driver, group, cid, function, params, want,
                extract=bullet_locs, deadline=POLL_DEADLINE, detail=()):
    """Assert the EXACT set of `path:line:character` sites in a reply."""
    want = set(want)

    def ready(text):
        return nonempty(text) and set(extract(text)) == want

    err, text, secs, tries = poll(driver, function, params, ready,
                                  deadline=deadline)
    got = set(extract(text))
    problems = []
    if err:
        problems.append("isError=True")
    if got != want:
        missing = sorted(want - got)
        extra = sorted(got - want)
        problems.append("site set mismatch: got %d want %d%s%s"
                        % (len(got), len(want),
                           ("; missing=%s" % missing) if missing else "",
                           ("; unexpected=%s" % extra) if extra else ""))
    check(suite, group, cid, problems,
          detail=list(detail) + ["%d site(s), %.2fs, %d attempt(s)"
                                 % (len(got), secs, tries)],
          text=text)
    return text


def expect_count(suite, driver, group, cid, function, params, rx, want,
                 deadline=POLL_DEADLINE, detail=()):
    """Assert the count in a rendered header equals `want`, exactly."""
    def ready(text):
        return num(rx, text) == want

    err, text, secs, tries = poll(driver, function, params, ready,
                                  deadline=deadline)
    got = num(rx, text)
    problems = []
    if err:
        problems.append("isError=True")
    if got != want:
        problems.append("count=%r expected %d" % (got, want))
    check(suite, group, cid, problems,
          detail=list(detail) + ["count=%r, %.2fs, %d attempt(s)"
                                 % (got, secs, tries)],
          text=text)
    return text


def record_diag_set(suite, group, cid, driver, function, path, want_set, label):
    """Poll a diagnostics function until it reports exactly `want_set`.

    Asserts the identity set AND that nothing survives deduplication beyond it,
    then records the raw-vs-deduplicated row counts as a separate INFO case, so
    a language server that repeats a diagnostic stays visible instead of being
    silently swallowed.
    """
    def ready(text):
        return diag_identity(diag_rows(text)) == want_set

    err, text, secs, tries = poll(driver, function,
                                  {"relative_path": path,
                                   "timeout": DIAG_TIMEOUT}, ready)
    rows = diag_rows(text)
    unique = diag_dedup(rows)
    identity = diag_identity(rows)
    problems = []
    if err:
        problems.append("isError=True")
    if identity != want_set:
        problems.append("diagnostic set mismatch: missing=%s unexpected=%s"
                        % (sorted(want_set - identity),
                           sorted(identity - want_set)))
    if len(unique) != len(want_set):
        problems.append("%d distinct diagnostic(s) after dedup, expected %d"
                        % (len(unique), len(want_set)))
    check(suite, group, cid, problems,
          detail=["%.2fs, %d attempt(s)" % (secs, tries)]
                 + ["L%-3d %-7s %-36s %s" % (r["line"], r["severity"], r["code"],
                                             r["message"][:56]) for r in rows],
          text=text)
    for row in rows:
        suite.note("      %-14s L%-3d %-8s %-36s %s"
                   % (label, row["line"], row["severity"], row["code"],
                      row["message"][:56]))
    suite.record(group, cid + "-repeats", (), status=H.INFO,
                 detail=["%d row(s) pushed, %d distinct -> %d byte-identical "
                         "repeat(s) from the language server"
                         % (len(rows), len(unique), len(rows) - len(unique))])
    return rows


def skip(suite, group, cid, reason):
    """Record a SKIP as an INFO case: informative, never a failure."""
    return suite.record(group, cid, (), status=H.INFO,
                        detail=["SKIPPED: %s" % reason],
                        brief="INFO | %s (skipped: %s)" % (cid, reason))


# ---------------------------------------------------------------------------
# Group A -- dispatcher surface
# ---------------------------------------------------------------------------

def group_a(suite, driver):
    err, inventory = driver.call("", {})
    names = {ln.strip() for ln in inventory.splitlines() if ln.startswith("  ")}
    problems = []
    if err:
        problems.append("isError=True")
    if "Available functions:" not in inventory:
        problems.append("MISSING 'Available functions:' header")
    check(suite, "A", "inventory-header", problems,
          detail=["%d function name(s) listed" % len(names)], text=inventory)

    for label, expected in (("clangd", RETIRED_CLANGD_NAMES),
                            ("cuda", RETIRED_CUDA_NAMES),
                            ("luals", RETIRED_LUALS_NAMES)):
        absent = sorted(set(expected) - names)
        check(suite, "A", "inventory-lists-%s" % label,
              [] if not absent else ["not listed: %s" % absent],
              detail=["%d/%d retired %s_* name(s) present"
                      % (len(expected) - len(absent), len(expected), label)])

    # The ported capability: every spelling must be in the inventory, including
    # the two clangd/cuda prefixes the retired mcp-clangd never had.
    absent = sorted(set(TYPEDEF_SPELLINGS) - names)
    check(suite, "A", "inventory-lists-type-definition",
          [] if not absent else ["not listed: %s" % absent],
          detail=["%d/%d find_type_definition spelling(s) present: %s"
                  % (len(TYPEDEF_SPELLINGS) - len(absent),
                     len(TYPEDEF_SPELLINGS), ", ".join(TYPEDEF_SPELLINGS))])

    _, text = driver.call("tf_no_such_function", {})
    check(suite, "A", "unknown-function-rejected",
          [] if unknown_function(text) else ["did not report an unknown function"],
          text=text)

    # The *_init names were the retired servers' explicit handshake step; purity
    # keeps them as no-ops so old callers do not break. No backend needed.
    for cid, fn in (("init-noop-clangd", "clangd_init"),
                    ("init-noop-cuda", "cuda_init"),
                    ("init-noop-luals", "luals_init")):
        err, text = driver.call(fn, {})
        problems = []
        if err:
            problems.append("isError=True")
        if "lazily" not in text:
            problems.append("MISSING 'lazily' notice; got %r" % text[:90])
        check(suite, "A", cid, problems, text=text)


# ---------------------------------------------------------------------------
# Group B -- C navigation through the clangd backend
# ---------------------------------------------------------------------------

def group_b(suite, driver, timings):
    # Warm-up: the first semantic call blocks on the whole clangd handshake.
    started = time.time()
    text = expect(suite, driver, "B", "warmup-def-by-name", "find_definition",
                  {"symbol_name": "tf_vec_add"},
                  must=["# Definition", "tests/files/c/tf_math.h:35:10"],
                  deadline=WARMUP_DEADLINE,
                  detail=["cold clangd handshake; by-name lookup resolves the "
                          "declaration in the header"])
    timings["clangd_first_answer"] = time.time() - started
    suite.note("      clangd  first useful answer after %.2fs (cold handshake)"
               % timings["clangd_first_answer"])
    if RX_DEF_COUNT.search(text):
        check(suite, "B", "def-by-name-single",
              [] if num(RX_DEF_COUNT, text) == 1
              else ["count=%r expected 1" % num(RX_DEF_COUNT, text)], text=text)

    # Cross-file go-to-definition from a CALL SITE in another translation unit:
    # tf_main.c:18 calls tf_vec_add -> the definition lives in tf_math.c.
    expect_locs(suite, driver, "B", "def-at-crossfile", "find_definition",
                {"relative_path": C_MAIN, "line": 18, "character": 24},
                {"tests/files/c/tf_math.c:12:10"}, extract=head_locs,
                detail=["tf_main.c:18 call site -> tf_math.c definition"])

    # textDocument/typeDefinition -- the capability ported into purity after the
    # merge. The retired mcp-clangd never had it, so these are NEW for C: one
    # hop past find_definition, from a value to where its TYPE is declared.
    expect_locs(suite, driver, "B", "typedef-at-variable", "find_type_definition",
                {"relative_path": C_MAIN, "line": 16, "character": 11},
                {C_TYPEDEF_VEC}, extract=head_locs,
                detail=["local `a` -> `} tf_vec_t;`, cross-file into the header"])
    expect_locs(suite, driver, "B", "typedef-at-call-return",
                "find_type_definition",
                {"relative_path": C_MAIN, "line": 18, "character": 17},
                {C_TYPEDEF_VEC}, extract=head_locs,
                detail=["tf_vec_add call -> the RETURN type's declaration; "
                        "find_definition at the same spot answers tf_math.c:12"])
    expect_locs(suite, driver, "B", "typedef-at-typedef-name",
                "find_type_definition",
                {"relative_path": C_MAIN, "line": 16, "character": 4},
                {C_TYPEDEF_VEC_TAG}, extract=head_locs,
                detail=["on `tf_vec_t` itself -> one hop further, the struct tag"])
    expect_locs(suite, driver, "B", "typedef-at-enum-field",
                "find_type_definition",
                {"relative_path": C_HDR, "line": 26, "character": 12},
                {C_TYPEDEF_UNIT}, extract=head_locs,
                detail=["struct field `tf_unit` -> the enum typedef"])
    text = expect(suite, driver, "B", "typedef-at-variable-prefixed",
                  "clangd_find_type_definition_at",
                  {"relative_path": C_MAIN, "line": 16, "character": 11},
                  must=["# Type definition", C_TYPEDEF_VEC],
                  detail=["clangd_ spelling of a name the retired clangd server "
                          "never registered"])
    check(suite, "B", "typedef-single-result",
          [] if num(RX_TYPEDEF_COUNT, text) == 1
          else ["count=%r expected 1" % num(RX_TYPEDEF_COUNT, text)], text=text)
    expect(suite, driver, "B", "typedef-honest-error", "find_type_definition",
           {"relative_path": C_MAIN, "line": 3, "character": 3},
           ready=lambda t: "No type definition" in t, want_error=True,
           must=["No type definition found at this position"],
           detail=["B-class: inside a comment there is no type, and the server "
                   "says so rather than guessing"])

    # References: exact COUNT and exact site set, per tests/files/README.md.
    expect_locs(suite, driver, "B", "refs-add-4", "find_references",
                {"symbol_name": "tf_vec_add"}, C_REFS_ADD,
                detail=["decl + def + 2 call sites"])
    expect_locs(suite, driver, "B", "refs-scale-3", "find_references",
                {"symbol_name": "tf_vec_scale"}, C_REFS_SCALE,
                detail=["decl + def + 1 call site"])
    expect_locs(suite, driver, "B", "refs-length-4", "find_references",
                {"symbol_name": "tf_vec_length"}, C_REFS_LENGTH,
                detail=["decl + def + tf_main.c + tf_broken.c"])
    expect_locs(suite, driver, "B", "refs-at-callsite-3", "find_references",
                {"relative_path": C_MAIN, "line": 18, "character": 24},
                C_REFS_ADD_AT,
                detail=["positional query omits the header declaration"])

    # Types.
    expect(suite, driver, "B", "type-at-function", "type_at",
           {"relative_path": C_MAIN, "line": 18, "character": 24},
           must=["# Type", "tf_vec_add", "tf_vec_t"])
    expect(suite, driver, "B", "type-at-variable", "type_at",
           {"relative_path": C_MAIN, "line": 16, "character": 11},
           must=["variable a", "tf_vec_t", "struct tf_vec"])
    expect(suite, driver, "B", "type-at-in-comment", "type_at",
           {"relative_path": C_MAIN, "line": 3, "character": 3},
           ready=lambda t: "No hover/type" in t, want_error=True,
           must=["No hover/type information"],
           detail=["B-class: honest error, never a grep guess"])

    # Outline: the header's three function declarations plus the struct fields.
    text = expect(suite, driver, "B", "outline-header", "outline",
                  {"relative_path": C_HDR},
                  must=["# Outline", "tf_vec_add", "tf_vec_scale",
                        "tf_vec_length", "tf_unit", "tf_vec"])
    rows = outline_rows(text)
    want_rows = {("Function", "tf_vec_add", 35), ("Function", "tf_vec_scale", 43),
                 ("Function", "tf_vec_length", 50), ("Field", "tf_x", 23),
                 ("Field", "tf_y", 24), ("Field", "tf_z", 25),
                 ("Field", "tf_unit", 26)}
    missing = sorted(want_rows - set(rows))
    check(suite, "B", "outline-header-rows",
          [] if not missing else ["missing rows: %s" % missing],
          detail=["%d outline row(s)" % len(rows)], text=text)

    text = expect(suite, driver, "B", "outline-impl", "outline",
                  {"relative_path": C_DEF}, must=["# Outline"])
    rows = set(outline_rows(text))
    want_rows = {("Function", "tf_vec_add", 12), ("Function", "tf_vec_scale", 23),
                 ("Function", "tf_vec_length", 34)}
    check(suite, "B", "outline-impl-rows",
          [] if rows == want_rows else ["rows=%s want=%s"
                                        % (sorted(rows), sorted(want_rows))],
          text=text)

    # Workspace symbol lookup.
    expect_count(suite, driver, "B", "symbol-func-2", "symbol",
                 {"query": "tf_vec_add"}, RX_SYM_COUNT, 2,
                 detail=["header declaration + implementation"])
    expect(suite, driver, "B", "symbol-macro", "symbol",
           {"query": "TF_VEC_DIM"},
           must=["TF_VEC_DIM", "tests/files/c/tf_math.h:13:9"])

    # Combined helpers the retired server also exposed.
    expect(suite, driver, "B", "symbol-context", "symbol_context",
           {"symbol_name": "tf_vec_scale"},
           must=["# Symbol context", "## Definition", "## References (3)",
                 "tests/files/c/tf_main.c:20:18"])
    expect(suite, driver, "B", "change-impact", "symbol_change_impact",
           {"symbol_name": "tf_vec_add"},
           must=["# Change impact", "## References — 4 in 3 file(s)",
                 "## Call hierarchy"], must_not=["_(partial: none found)_"])
    expect_count(suite, driver, "B", "inlay-hints-13", "inlay_hints",
                 {"relative_path": C_MAIN, "start_line": 14, "end_line": 24},
                 RX_HINT_COUNT, 13,
                 detail=["8 designated-initialiser + 5 parameter hints"])

    # find_implementations is B-class: a plain C function has none, and the
    # server must say so rather than inventing a result.
    expect(suite, driver, "B", "implementations-honest-error",
           "find_implementations",
           {"relative_path": C_MAIN, "line": 18, "character": 24},
           ready=lambda t: "No implementations" in t, want_error=True,
           must=["No implementations found"])


def group_c(suite, driver):
    """C diagnostics: the planted defects, then a clean bill of health."""
    rows = record_diag_set(suite, "C", "broken-diag-set", driver, "diagnostics",
                           C_BROKEN, C_DIAG_SET, "tf_broken.c")
    text = "\n".join(r["message"] for r in rows)

    for line, what, needles in C_PLANTED:
        hits = [r for r in rows if r["line"] == line]
        problems = []
        if not hits:
            problems.append("no diagnostic on line %d (planted: %s)" % (line, what))
        else:
            body = " ".join(r["severity"] + " " + r["code"] + " " + r["message"]
                            for r in hits)
            if "Error" not in body:
                problems.append("line %d not reported as an Error" % line)
            for needle in needles:
                if needle.lower() not in body.lower():
                    problems.append("line %d text missing %r" % (line, needle))
        check(suite, "C", "broken-planted-L%d" % line, problems,
              detail=[what], text=text)

    # A validator that always reports problems is useless: the same channel,
    # in the same run, must return clean for healthy translation units. Sampled
    # twice on the primary file so a not-yet-arrived push cannot pass as clean.
    for cid, path, samples in (("clean-tf_math_c", C_DEF, 2),
                               ("clean-tf_main_c", C_MAIN, 1),
                               ("clean-tf_math_h", C_HDR, 1)):
        counts = []
        for _ in range(samples):
            _, txt = driver.call("diagnostics", {"relative_path": path,
                                                 "timeout": DIAG_TIMEOUT})
            counts.append(num(RX_DIAG_COUNT, txt))
        bad = [c for c in counts if c != 0]
        check(suite, "C", cid,
              [] if not bad else ["expected 0 diagnostics, got %s" % counts],
              detail=["%d sample(s): %s" % (samples, counts)])


# ---------------------------------------------------------------------------
# Groups D / E -- Lua through the lua-language-server backend
# ---------------------------------------------------------------------------

def group_d(suite, driver, timings):
    started = time.time()
    text = expect(suite, driver, "D", "warmup-def-by-name",
                  "luals_find_definition", {"symbol_name": "tfAdd"},
                  must=["# Definition", "tests/files/lua/tf_mathlib.lua:24:20"],
                  deadline=WARMUP_DEADLINE,
                  detail=["cold luals handshake; path-less call must reach the "
                          "luals backend via the luals_ prefix hint, not the "
                          "cpp default"])
    timings["luals_first_answer"] = time.time() - started
    suite.note("      luals   first useful answer after %.2fs (cold handshake)"
               % timings["luals_first_answer"])
    if RX_DEF_COUNT.search(text):
        check(suite, "D", "def-by-name-single",
              [] if num(RX_DEF_COUNT, text) == 1
              else ["count=%r expected 1" % num(RX_DEF_COUNT, text)], text=text)

    # Cross-file: a call site in tf_consumer.lua -> the definition in the
    # required module tf_mathlib.lua.
    expect_locs(suite, driver, "D", "def-at-crossfile", "find_definition",
                {"relative_path": L_CONS, "line": 12, "character": 26},
                {"tests/files/lua/tf_mathlib.lua:24:20"}, extract=head_locs,
                detail=["canonical name routes by .lua extension"])
    expect_locs(suite, driver, "D", "def-at-crossfile-prefixed",
                "luals_find_definition_at",
                {"relative_path": L_CONS, "line": 14, "character": 26},
                {"tests/files/lua/tf_mathlib.lua:31:20"}, extract=head_locs,
                detail=["retired server's _at spelling, tfScale call site"])

    # textDocument/typeDefinition -- the ONE capability the merge had dropped,
    # now back. Both the canonical short name and the retired _at spelling must
    # answer, and they must land on the annotated declaration (column 1), NOT on
    # the identifier column find_definition reports.
    expect_locs(suite, driver, "D", "typedef-at-crossfile",
                "find_type_definition",
                {"relative_path": L_CONS, "line": 12, "character": 26},
                {L_TYPEDEF_ADD}, extract=head_locs,
                detail=["canonical name routes to luals by .lua extension"])
    text = expect_locs(suite, driver, "D", "typedef-at-crossfile-prefixed",
                       "luals_find_type_definition_at",
                       {"relative_path": L_CONS, "line": 14, "character": 26},
                       {L_TYPEDEF_SCALE}, extract=head_locs,
                       detail=["the retired server's exact spelling, tfScale "
                               "call site"])
    check(suite, "D", "typedef-single-result",
          [] if num(RX_TYPEDEF_COUNT, text) == 1
          else ["count=%r expected 1" % num(RX_TYPEDEF_COUNT, text)], text=text)
    expect_locs(suite, driver, "D", "typedef-at-newvec",
                "luals_find_type_definition_at",
                {"relative_path": L_CONS, "line": 10, "character": 22},
                {L_TYPEDEF_NEWVEC}, extract=head_locs,
                detail=["tfNewVec call site -> its annotated declaration"])
    expect_locs(suite, driver, "D", "typedef-at-length",
                "luals_find_type_definition_at",
                {"relative_path": L_CONS, "line": 16, "character": 19},
                {L_TYPEDEF_LENGTH}, extract=head_locs,
                detail=["tfLength, reached from inside a return expression"])
    expect(suite, driver, "D", "typedef-honest-error",
           "luals_find_type_definition_at",
           {"relative_path": L_CONS, "line": 2, "character": 5},
           ready=lambda t: "No type definition" in t, want_error=True,
           must=["No type definition found at this position"],
           detail=["B-class: a doc comment has no type"])

    expect_locs(suite, driver, "D", "refs-tfAdd-4", "luals_find_references",
                {"symbol_name": "tfAdd"}, L_REFS_ADD,
                detail=["3 LSP refs + 1 doc-comment hit from purity's Lua "
                        "text-grep supplement (tf_consumer.lua:4:48)"])
    expect_locs(suite, driver, "D", "refs-tfScale-2", "luals_find_references",
                {"symbol_name": "tfScale"}, L_REFS_SCALE,
                detail=["def + 1 call site; no comment mentions tfScale"])
    expect_locs(suite, driver, "D", "refs-tfNewVec-5", "luals_find_references",
                {"symbol_name": "tfNewVec"}, L_REFS_NEWVEC,
                detail=["def + 2 external + 2 internal call sites"])
    expect_locs(suite, driver, "D", "refs-at-callsite-3", "luals_find_references",
                {"relative_path": L_CONS, "line": 12, "character": 26},
                L_REFS_ADD_AT,
                detail=["positional query = LSP only, no grep supplement"])

    expect(suite, driver, "D", "hover-function", "luals_hover",
           {"relative_path": L_CONS, "line": 16, "character": 19},
           must=["# Type", "function tfMathlib.tfLength(v: table)",
                 "-> length: number"],
           detail=["annotated signature recovered at a cross-file call site"])
    expect(suite, driver, "D", "hover-field", "luals_hover",
           {"relative_path": L_LIB, "line": 11, "character": 11},
           must=["(field) tfMathlib.tfVecDim: integer = 3"])

    text = expect(suite, driver, "D", "outline-module", "luals_document_outline",
                  {"relative_path": L_LIB}, must=["# Outline", "tfMathlib"])
    rows = set(outline_rows(text))
    want_rows = {("Object", "tfMathlib", 8), ("Number", "tfMathlib.tfVecDim", 11),
                 ("Function", "tfMathlib.tfNewVec", 17),
                 ("Function", "tfMathlib.tfAdd", 24),
                 ("Function", "tfMathlib.tfScale", 31),
                 ("Function", "tfMathlib.tfLength", 37)}
    missing = sorted(want_rows - rows)
    check(suite, "D", "outline-module-rows",
          [] if not missing else ["missing rows: %s" % missing],
          detail=["%d outline row(s)" % len(rows)], text=text)

    expect(suite, driver, "D", "workspace-symbol", "luals_workspace_symbols",
           {"query": "tfLength"},
           must=["# Symbols", "tests/files/lua/tf_mathlib.lua:37:20"])
    expect(suite, driver, "D", "symbol-context", "luals_symbol_context",
           {"symbol_name": "tfScale"},
           must=["# Symbol context", "## References (2)",
                 "tests/files/lua/tf_consumer.lua:14:25"])
    # luals has no call hierarchy (LuaLsClient.supports_call_hierarchy = False),
    # so change impact is expected to come back flagged partial -- that is the
    # documented contract, and the definition + references halves must be there.
    expect(suite, driver, "D", "change-impact-partial",
           "luals_symbol_change_impact", {"symbol_name": "tfAdd"},
           ready=lambda t: "# Change impact" in t,
           must=["# Change impact", "## References — 4 in 2 file(s)",
                 "_(partial: none found)_"],
           detail=["luals exposes no call hierarchy; partial flag is correct"])
    expect(suite, driver, "D", "implementations-at",
           "luals_find_implementations_at",
           {"relative_path": L_CONS, "line": 12, "character": 26},
           must=["# Implementations", "tests/files/lua/tf_mathlib.lua:24:20"])


def group_e(suite, driver):
    """Lua diagnostics: planted defects, then a clean bill of health."""
    rows = record_diag_set(suite, "E", "broken-diag-set", driver,
                           "luals_diagnostics", L_BROKEN, L_DIAG_SET,
                           "tf_broken.lua")
    text = "\n".join(r["message"] for r in rows)

    for line, code, what, needles in L_PLANTED:
        hits = [r for r in rows if r["line"] == line and code in r["code"]]
        problems = []
        if not hits:
            problems.append("no %r diagnostic on line %d (planted: %s)"
                            % (code, line, what))
        else:
            body = " ".join(r["message"] for r in hits)
            for needle in needles:
                if needle not in body:
                    problems.append("line %d text missing %r" % (line, needle))
        check(suite, "E", "broken-planted-L%d-%s" % (line, code), problems,
              detail=[what], text=text)

    for cid, path, samples in (("clean-tf_mathlib", L_LIB, 2),
                               ("clean-tf_consumer", L_CONS, 1)):
        counts = []
        for _ in range(samples):
            _, txt = driver.call("luals_diagnostics",
                                 {"relative_path": path, "timeout": DIAG_TIMEOUT})
            counts.append(num(RX_DIAG_COUNT, txt))
        bad = [c for c in counts if c != 0]
        check(suite, "E", cid,
              [] if not bad else ["expected 0 diagnostics, got %s" % counts],
              detail=["%d sample(s): %s" % (samples, counts)])

    # WART, recorded not asserted-away: a path that does not exist comes back
    # as "0 diagnostics" -- i.e. indistinguishable from healthy. Group H proves
    # the retired servers did exactly the same, so this is inherited, not new.
    _, txt = driver.call("luals_diagnostics",
                         {"relative_path": "tests/files/lua/tf_absent.lua",
                          "timeout": DIAG_TIMEOUT})
    count = num(RX_DIAG_COUNT, txt)
    suite.record("E", "wart-missing-file-reads-clean", (), status=H.INFO,
                 detail=["nonexistent path -> count=%r (no error): '0 "
                         "diagnostics' alone is NOT proof of health" % count],
                 text=txt)


# ---------------------------------------------------------------------------
# Group F -- the legacy alias routing matrix
# ---------------------------------------------------------------------------

def alias_matrix(have_clangd, have_luals):
    """[(name, params, must_substring_or_None, needs_backend), ...]."""
    c_at = {"relative_path": C_MAIN, "line": 20, "character": 20}
    l_at = {"relative_path": L_CONS, "line": 14, "character": 26}
    rows = []
    for prefix in ("clangd_", "cuda_"):
        rows += [
            (prefix + "init", {}, "lazily", False),
            (prefix + "find_definition", {"symbol_name": "tf_vec_scale"},
             "tests/files/c/tf_math.h:43:10", True),
            (prefix + "find_definition_at", c_at,
             "tests/files/c/tf_math.c:23:10", True),
            # Not a retired name -- the ported capability, newly available to
            # the clangd backend under both legacy prefixes.
            (prefix + "find_type_definition_at",
             {"relative_path": C_MAIN, "line": 16, "character": 11},
             C_TYPEDEF_VEC, True),
            (prefix + "find_references", {"symbol_name": "tf_vec_scale"},
             "— 3 found", True),
            (prefix + "find_references_at", c_at, "# References", True),
            (prefix + "find_implementations_at", c_at, None, True),
            (prefix + "workspace_symbols", {"query": "tf_vec_scale"},
             "# Symbols", True),
            (prefix + "document_outline", {"relative_path": C_HDR},
             "# Outline", True),
            (prefix + "symbol_context", {"symbol_name": "tf_vec_scale"},
             "# Symbol context", True),
            (prefix + "inlay_hints",
             {"relative_path": C_MAIN, "start_line": 14, "end_line": 24},
             "# Inlay hints", True),
            (prefix + "symbol_change_impact", {"symbol_name": "tf_vec_scale"},
             "# Change impact", True),
            (prefix + "hover", c_at, "# Type", True),
            (prefix + "diagnostics",
             {"relative_path": C_DEF, "timeout": DIAG_TIMEOUT},
             "# Diagnostics", True),
            (prefix + "deduced_type_at", c_at, "**Deduced type**", True),
        ]
    rows += [
        ("luals_init", {}, "lazily", False),
        ("luals_find_definition", {"symbol_name": "tfScale"},
         "tests/files/lua/tf_mathlib.lua:31:20", True),
        ("luals_find_definition_at", l_at,
         "tests/files/lua/tf_mathlib.lua:31:20", True),
        ("luals_find_type_definition_at", l_at, L_TYPEDEF_SCALE, True),
        ("luals_find_references", {"symbol_name": "tfScale"},
         "— 2 found", True),
        ("luals_find_references_at", l_at, "# References", True),
        ("luals_find_implementations_at", l_at, "# Implementations", True),
        ("luals_workspace_symbols", {"query": "tfScale"}, "# Symbols", True),
        ("luals_document_outline", {"relative_path": L_LIB}, "# Outline", True),
        ("luals_symbol_context", {"symbol_name": "tfScale"},
         "# Symbol context", True),
        ("luals_inlay_hints",
         {"relative_path": L_CONS, "start_line": 1, "end_line": 20}, None, True),
        ("luals_symbol_change_impact", {"symbol_name": "tfScale"},
         "# Change impact", True),
        ("luals_hover", {"relative_path": L_LIB, "line": 11, "character": 11},
         "# Type", True),
        ("luals_diagnostics", {"relative_path": L_LIB, "timeout": DIAG_TIMEOUT},
         "# Diagnostics", True),
    ]
    out = []
    for name, params, must, needs in rows:
        if needs and name.startswith("luals_") and not have_luals:
            continue
        if needs and not name.startswith("luals_") and not have_clangd:
            continue
        out.append((name, params, must, needs))
    return out


def group_f(suite, driver, have_clangd, have_luals):
    rows = alias_matrix(have_clangd, have_luals)
    if not rows:
        skip(suite, "F", "alias-matrix", "no LSP backend available")
        return
    for name, params, must, _needs in rows:
        problems = []
        if must is None:
            # Routing-only row: the handler may honestly report "nothing here"
            # (no implementations / no inlay hints). Only a dispatcher miss is
            # a failure.
            _, text = driver.call(name, params)
        else:
            _, text, _s, _t = poll(driver, name, params,
                                   lambda t, m=must: m in t)
            if must not in text:
                problems.append("MISSING %r" % must)
        if unknown_function(text):
            problems.append("DID NOT ROUTE: dispatcher reports unknown function")
        check(suite, "F", "routes-" + name, problems,
              detail=[" ".join(text.split())[:100]], text=text)

    for label, expected, present in (("clangd", RETIRED_CLANGD_NAMES, have_clangd),
                                     ("cuda", RETIRED_CUDA_NAMES, have_clangd),
                                     ("luals", RETIRED_LUALS_NAMES, have_luals)):
        if not present:
            skip(suite, "F", "coverage-%s" % label,
                 "backend binary missing, matrix rows not exercised")
            continue
        covered = {n for n, _p, _m, _x in rows}
        gap = sorted(set(expected) - covered)
        check(suite, "F", "coverage-%s" % label,
              [] if not gap else ["retired %s_* names not exercised: %s"
                                  % (label, gap)],
              detail=["%d/%d exercised" % (len(expected) - len(gap),
                                           len(expected))])


# ---------------------------------------------------------------------------
# Group G -- the gaps, and the one that closed
# ---------------------------------------------------------------------------

def group_g(suite, driver, have_luals):
    """Pin every retired name purity does NOT resolve, plus the one it now does.

    The remaining gaps are recorded as INFO with the exact name in the detail
    line: they are real losses, but they are losses in the SERVER, and this suite
    must not be the thing that decides to fail the repo over them. The report
    shouts. A case only carries a problem when the gap is UNDOCUMENTED-and-
    unpinned in a way this suite can check, or when a gap silently changes shape.

    CLOSED_CAPABILITY is the opposite: a hard PASS assertion. It used to be an
    INFO recording that `luals_find_type_definition_at` had no purity
    equivalent for ANY language. purity now ports textDocument/typeDefinition, so
    the name must RESOLVE and must answer the right location -- a reopened gap is
    a FAILURE here, not a note.
    """
    skill = ""
    if os.path.exists(SKILL_LUALS):
        with open(SKILL_LUALS, encoding="utf-8") as fh:
            skill = fh.read()

    for name in CLOSED_CAPABILITY:
        if not have_luals:
            skip(suite, "G", "closed-gap-" + name,
                 "lua-language-server not installed")
            continue
        _, text, secs, tries = poll(driver, name,
                                    {"relative_path": L_CONS, "line": 12,
                                     "character": 26},
                                    lambda t: L_TYPEDEF_ADD in t)
        problems = []
        if unknown_function(text):
            problems.append("GAP REOPENED: dispatcher no longer registers %s"
                            % name)
        elif L_TYPEDEF_ADD not in text:
            problems.append("resolved but did not answer %s; got %r"
                            % (L_TYPEDEF_ADD, " ".join(text.split())[:80]))
        check(suite, "G", "closed-gap-" + name, problems,
              detail=["CLOSED: %s (textDocument/typeDefinition) is ported and "
                      "answers for clangd AND luals" % name,
                      "%.2fs, %d attempt(s)" % (secs, tries)], text=text)
        suite.note("      CLOSED (capability) %-29s resolves -> %s"
                   % (name, L_TYPEDEF_ADD))

        # The skill text is NOT this suite's to fix (ClaudeCode/** is owned
        # elsewhere), so a stale "no purity_call equivalent" note is reported as
        # INFO -- a sweep signal, never a failure.
        stale = name in skill and "no `purity_call` equivalent" in skill
        suite.record("G", "closed-gap-doc-sweep-" + name, (), status=H.INFO,
                     detail=["ClaudeCode/skills/mcp-luals/SKILL.md still calls "
                             "it unavailable: %s" % ("YES -- needs a sweep"
                                                     if stale else "no")])
        suite.note("      CLOSED (docs)       %-29s SKILL.md still says "
                   "unavailable: %s" % (name, "YES" if stale else "no"))

    for name in GAP_SINGULAR_ALIASES:
        _, text = driver.call(name, {})
        problems = []
        if not unknown_function(text):
            problems.append("gap changed shape: name now resolves -- update "
                            "GAP_SINGULAR_ALIASES")
        suite.record("G", "gap-" + name, problems,
                     status=H.INFO if not problems else H.FAIL,
                     detail=["ALIAS GAP: FUNCTION_ALIASES entry of the retired "
                             "Scripts/mcp-lua-lsp.py; purity registers only the "
                             "plural spelling, and no skill documents the loss"],
                     text=text)
        suite.note("      GAP (alias)      %-32s not registered (retired "
                   "singular spelling, undocumented)" % name)


# ---------------------------------------------------------------------------
# Group H -- A/B against the retired servers still on disk
# ---------------------------------------------------------------------------

def group_h(suite, purity, have_clangd, have_luals, timings):
    """Spawn the retired servers and compare, so an empty purity answer can be
    told apart from a capability that never worked here in the first place.

    The retired servers are NOT registered as MCP servers any more; they are
    invoked here read-only, purely as a reference implementation. Their param
    key is `path` (they never learned purity's `relative_path` alias).
    """
    if have_luals and os.path.exists(RETIRED_LUALS):
        old = Driver([sys.executable, RETIRED_LUALS, "--project-root",
                      H.REPO_ROOT, "--markdown"], tool="luals_call")
        try:
            started = time.time()
            _, text, secs, tries = poll(
                old, "luals_find_definition", {"symbol_name": "tfAdd"},
                lambda t: "tf_mathlib.lua:24" in t, deadline=WARMUP_DEADLINE)
            timings["retired_luals_first_answer"] = time.time() - started
            suite.note("      retired mcp-lua-lsp first useful answer after "
                       "%.2fs (vs purity %.2fs)"
                       % (timings["retired_luals_first_answer"],
                          timings.get("luals_first_answer", float("nan"))))
            check(suite, "H", "retired-luals-answers",
                  [] if "tf_mathlib.lua:24" in text
                  else ["retired server did not resolve tfAdd"],
                  detail=["reference implementation is alive",
                          "%.2fs to first answer, %d attempt(s)" % (secs, tries)],
                  text=text)

            # The capability purity had dropped and has since regained. This was
            # a CONFIRMED REGRESSION recorded as INFO; it is now a hard parity
            # assertion -- BOTH implementations must land on tf_mathlib.lua:24.
            _, old_td = old.call("luals_find_type_definition_at",
                                 {"path": L_CONS, "line": 12, "character": 26})
            _, new_td, secs, tries = poll(
                purity, "luals_find_type_definition_at",
                {"relative_path": L_CONS, "line": 12, "character": 26},
                lambda t: L_TYPEDEF_ADD in t)
            problems = []
            if "tf_mathlib.lua:24" not in old_td:
                problems.append("retired server did not answer; parity cannot be "
                                "judged -- the reference may be environmental")
            if unknown_function(new_td):
                problems.append("GAP REOPENED: purity no longer registers "
                                "luals_find_type_definition_at")
            elif L_TYPEDEF_ADD not in new_td:
                problems.append("purity answered %r, expected %s"
                                % (" ".join(new_td.split())[:70], L_TYPEDEF_ADD))
            check(suite, "H", "ab-type-definition-parity", problems,
                  detail=["retired: %s" % " ".join(old_td.split())[:70],
                          "purity : %s" % " ".join(new_td.split())[:70],
                          "REGRESSION REPAIRED: both answer tf_mathlib.lua:24",
                          "%.2fs, %d attempt(s)" % (secs, tries)],
                  text=old_td + "\n---\n" + new_td)
            suite.note("      A/B typeDefinition -> retired: %s | purity: %s"
                       % (" ".join(old_td.split())[:44],
                          " ".join(new_td.split())[:44]))

            # Lua inlay hints come back empty from purity. Is that a regression?
            _, old_ih = old.call("luals_inlay_hints",
                                 {"path": L_CONS, "start_line": 1,
                                  "end_line": 20, "limit": 100})
            _, new_ih = purity.call("luals_inlay_hints",
                                    {"relative_path": L_CONS, "start_line": 1,
                                     "end_line": 20})
            old_empty = num(RX_OLD_HINT_COUNT, old_ih) in (None, 0)
            new_empty = num(RX_HINT_COUNT, new_ih) in (None, 0)
            problems = []
            if old_empty != new_empty:
                problems.append("PARITY BROKEN: retired empty=%s purity empty=%s"
                                % (old_empty, new_empty))
            check(suite, "H", "ab-lua-inlay-hints-parity", problems,
                  detail=["retired: %s" % " ".join(old_ih.split())[:60],
                          "purity : %s" % " ".join(new_ih.split())[:60],
                          "both empty on this fixture -> not a purity gap"],
                  text=old_ih + "\n---\n" + new_ih)
            suite.note("      A/B lua inlay hints -> retired empty=%s, purity "
                       "empty=%s (parity)" % (old_empty, new_empty))

            # The missing-file wart, inherited rather than introduced.
            _, old_md = old.call("luals_diagnostics",
                                 {"path": "tests/files/lua/tf_absent.lua",
                                  "timeout": DIAG_TIMEOUT})
            reports_clean = "(0)" in old_md or "— 0" in old_md
            check(suite, "H", "ab-missing-file-wart-parity",
                  [] if reports_clean
                  else ["retired server did NOT report clean; purity's "
                        "behaviour would then be new"],
                  detail=["retired: %s" % " ".join(old_md.split())[:70],
                          "inherited wart, not introduced by the merge"],
                  text=old_md)
        finally:
            old.close()
    else:
        reason = ("lua-language-server missing" if not have_luals
                  else "Scripts/mcp-lua-lsp.py absent")
        for cid in ("retired-luals-answers", "ab-type-definition-parity",
                    "ab-lua-inlay-hints-parity", "ab-missing-file-wart-parity"):
            skip(suite, "H", cid, reason)

    if have_clangd and os.path.exists(RETIRED_CLANGD):
        old = Driver([sys.executable, RETIRED_CLANGD, "--project-root",
                      H.REPO_ROOT, "--markdown"], tool="clangd_call")
        try:
            _, old_ih, secs, tries = poll(
                old, "clangd_inlay_hints",
                {"path": C_MAIN, "start_line": 14, "end_line": 24, "limit": 100},
                lambda t: num(RX_OLD_HINT_COUNT, t) == 13)
            _, new_ih = purity.call("inlay_hints",
                                    {"relative_path": C_MAIN, "start_line": 14,
                                     "end_line": 24})
            old_n = num(RX_OLD_HINT_COUNT, old_ih)
            new_n = num(RX_HINT_COUNT, new_ih)
            check(suite, "H", "ab-c-inlay-hints-parity",
                  [] if old_n == new_n == 13
                  else ["retired=%r purity=%r, expected 13 both" % (old_n, new_n)],
                  detail=["retired mcp-clangd=%r, purity=%r" % (old_n, new_n),
                          "%.2fs, %d attempt(s)" % (secs, tries)],
                  text=old_ih)
            suite.note("      A/B C inlay hints   -> retired=%r, purity=%r "
                       "(parity)" % (old_n, new_n))
        finally:
            old.close()
    else:
        skip(suite, "H", "ab-c-inlay-hints-parity",
             "clangd missing" if not have_clangd
             else "Scripts/mcp-clangd.py absent")


# ---------------------------------------------------------------------------
# Group I -- hygiene and timings
# ---------------------------------------------------------------------------

def _pyc_problems(pyc_after, new_pyc, touched):
    """Reasons the tree is not bytecode-clean, or [] if it is.

    Assert ZERO .pyc, not "the count did not change".  A file that already
    existed when the run started reads as "1 before, 1 after" and sails through
    a pure delta check -- which is precisely how one hid in Scripts/__pycache__
    until it was spotted by hand.  The repo's rule is no bytecode in the tree at
    all (23 stale ones were deleted and two post-edit hooks were rewritten to
    stop producing them), so anything present is a finding regardless of when it
    appeared.  Pre-existing files are reported separately from ones this run
    created, because the fix differs: delete the litter vs. stop writing it.
    """
    problems = []
    if new_pyc or touched:
        problems.append("this run wrote bytecode: new=%s touched=%s"
                        % (new_pyc[:6], touched[:6]))
    pre_existing = sorted(set(pyc_after) - set(new_pyc))
    if pre_existing:
        problems.append("pre-existing .pyc a delta check would miss: %s"
                        % pre_existing[:6])
    return problems


def group_i(suite, before, digests_before, pyc_before, timings, stderr_bytes):
    after = repo_tree()
    new = sorted(p for p in after - before
                 if p not in LSP_CACHE_EXCEPTIONS
                 and not any(p.startswith(e) for e in LSP_CACHE_EXCEPTIONS))
    gone = sorted(before - after)
    check(suite, "I", "no-new-repo-paths",
          [] if not new else
          ["LSP/server wrote into the repo tree: %s (add to "
           "LSP_CACHE_EXCEPTIONS with a justification, do NOT delete this "
           "assertion)" % new[:12]],
          detail=["%d path(s) before, %d after, %d named exception(s)"
                  % (len(before), len(after), len(LSP_CACHE_EXCEPTIONS))])
    check(suite, "I", "no-removed-repo-paths",
          [] if not gone else ["paths disappeared: %s" % gone[:12]])

    pyc_after = H.pycache_snapshot()
    new_pyc = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    check(suite, "I", "pycache-clean",
          _pyc_problems(pyc_after, new_pyc, touched),
          detail=["%d .pyc before, %d after" % (len(pyc_before), len(pyc_after))])

    for label, dirpath in (("c", FIXTURE_C), ("lua", FIXTURE_LUA)):
        after_digests = H.file_digests(dirpath)
        changed = sorted(k for k in digests_before[label]
                         if after_digests.get(k) != digests_before[label][k])
        check(suite, "I", "fixtures-unchanged-" + label,
              [] if not changed else ["modified fixture(s): %s" % changed],
              detail=["%d file(s) verified byte-identical"
                      % len(digests_before[label])])

    suite.record("I", "warmup-latencies", (), status=H.INFO,
                 detail=["%-28s %s" % (k, ("%.2fs" % v) if v is not None
                                       else "n/a")
                         for k, v in sorted(timings.items())]
                        or ["no timings recorded"])
    suite.record("I", "child-stderr-volume", (), status=H.INFO,
                 detail=["%d byte(s) drained from the server child's stderr "
                         "(pipe buffer is ~64 kB; an undrained pipe this size "
                         "would eventually stall the LSP)" % stderr_bytes])
    suite.note("      repo tree: %d path(s) before, %d after, %d new, "
               "%d named cache exception(s)"
               % (len(before), len(after), len(new), len(LSP_CACHE_EXCEPTIONS)))
    suite.note("      .pyc files: %d before, %d after"
               % (len(pyc_before), len(pyc_after)))
    suite.note("      server child stderr drained: %d byte(s)" % stderr_bytes)
    for key in sorted(timings):
        suite.note("      timing %-30s %.2fs" % (key, timings[key]))


# ---------------------------------------------------------------------------

def run(opts=None):
    """Drive the real server, record every case, return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="purity_call semantic navigation (clangd + luals)",
                    opts=opts, mode="stream", group_width=3, cid_width=38)

    clangd = have_binary("clangd")
    luals = have_binary("lua-language-server")
    suite.note("      clangd              : %s" % (clangd or "NOT FOUND -> C groups SKIP"))
    suite.note("      lua-language-server : %s" % (luals or "NOT FOUND -> Lua groups SKIP"))
    suite.note("      server              : %s" % SERVER)
    suite.note("      NOTE: a cold backend blocks the first semantic call for "
               "~3-4s -- grace period + index priming (see the module "
               "docstring; it was ~60s before the indexing wait was gated)")

    before = repo_tree()
    pyc_before = H.pycache_snapshot()
    digests_before = {"c": H.file_digests(FIXTURE_C),
                      "lua": H.file_digests(FIXTURE_LUA)}
    timings = {}
    stderr_bytes = 0

    driver = Driver([sys.executable, SERVER, "--project-root", H.REPO_ROOT],
                    tool="purity_call")
    try:
        group_a(suite, driver)

        if clangd:
            group_b(suite, driver, timings)
            group_c(suite, driver)
        else:
            for cid in ("warmup-def-by-name", "def-at-crossfile", "refs-add-4",
                        "type-at-function", "outline-header", "symbol-func-2",
                        "inlay-hints-13", "typedef-at-variable",
                        "typedef-at-typedef-name", "typedef-at-enum-field"):
                skip(suite, "B", cid, "clangd not installed")
            for cid in ("broken-diag-set", "clean-tf_math_c"):
                skip(suite, "C", cid, "clangd not installed")

        if luals:
            group_d(suite, driver, timings)
            group_e(suite, driver)
        else:
            for cid in ("warmup-def-by-name", "def-at-crossfile", "refs-tfAdd-4",
                        "hover-function", "outline-module", "workspace-symbol",
                        "typedef-at-crossfile", "typedef-at-crossfile-prefixed"):
                skip(suite, "D", cid, "lua-language-server not installed")
            for cid in ("broken-diag-set", "clean-tf_mathlib"):
                skip(suite, "E", cid, "lua-language-server not installed")

        group_f(suite, driver, bool(clangd), bool(luals))
        group_g(suite, driver, bool(luals))
        group_h(suite, driver, bool(clangd), bool(luals), timings)
        stderr_bytes = len(driver.stderr_text)
    finally:
        driver.close()

    group_i(suite, before, digests_before, pyc_before, timings, stderr_bytes)
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
