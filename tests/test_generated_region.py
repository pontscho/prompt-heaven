#!/usr/bin/env python3
"""Generated-region drift gate -- groups A-I.

`Scripts/_mcp_brotli.py`, `Scripts/_mcp_chrome.py`,
`Scripts/_mcp_codesearch.py`, `Scripts/_mcp_concurrency.py`,
`Scripts/_mcp_httpfront.py`, `Scripts/_mcp_json.py`,
`Scripts/_mcp_logging.py`, `Scripts/_mcp_lsp.py`, `Scripts/_mcp_oauth.py`,
`Scripts/_mcp_paging.py`, `Scripts/_mcp_websearch.py`,
`Scripts/_mcp_websocket.py` and `Scripts/_mcp_zstd.py` are the canonical
sources for the helpers the MCP servers share, and
`Scripts/amalgamate.py` inlines their named blocks into each server between
`# BEGIN GENERATED` / `# END GENERATED` markers -- and into the non-server
hosts its `DECLARED_HOSTS` names, which group A gates alongside the fleet. The
websocket and Chrome blocks have behaviour suites of their own
(`tests/test_mcp_websocket.py`, `tests/test_mcp_chrome.py`,
`tests/test_mcp_decoders.py`) that drive them end to end, so group E does not
repeat them. The servers stay
single-file on purpose (an imported sibling would write `Scripts/__pycache__`
into a tree every suite that snapshots bytecode asserts stays empty, and would
move the helpers out of the module attributes `test_mcp_footprint` reaches
for), so the shared code is duplicated on disk BY DESIGN -- and duplication on
disk is exactly what rots.

This suite is the thing that stops it rotting: it re-renders every live region
from the canonical source in memory and demands byte identity. A region that
drifted, or whose recorded hash no longer matches its body, fails here.

MORE THAN ONE SOURCE, AND THE SOURCE IS LOAD-BEARING. The filename on a BEGIN
line selects which block map the region's names are resolved against -- it is
not a comment. Two rules follow, and both are asserted rather than assumed: an
unknown source is refused by name, and a name defined in ANOTHER canonical file
does not resolve. That second one is the dangerous direction: a block served
out of the wrong domain compiles, runs, and looks correct in every server that
carries it, so nothing downstream would ever report it.

Each source is a DOMAIN, and the domains are narrowed as blocks earn their own
home -- LSP framing and row accounting both started in the JSON source and both
left it. Group E asserts the departures as well as the arrivals: a block that
came back would otherwise be tested in its new home and still be sitting in its
old one.

WHY THE MARKERS ARE SPELLED OUT BELOW instead of imported from the generator:
they are an ON-DISK FORMAT CONTRACT, not an implementation detail. A test that
imported `BEGIN_PREFIX` would sail through a rename that orphaned every region
already written into a server. So this file carries its own copy and asserts the
generator agrees -- the same reasoning `test_checkpoint` applies to its table-of-
contents markers.

TEXT, NOT AST -- deliberately. The fleet's rule is that source SCANNING uses
`ast`, never regex, because finding a call site is a semantic question. This
suite asks a different question: whether a delimited region of text is
byte-identical to what a generator emits. That is legitimately a text operation.
The generator itself does use `tokenize` to find its markers, and group C proves
it: a marker quoted inside a docstring must be inert.

SCOPE. Group A gates the live tree. Group B gates the format contract. Group C
is the negative control -- synthetic sources, each mutation asserted DETECTED,
plus bait that must stay silent, because a checker that silently matches nothing
is indistinguishable from a clean tree. Group D is hygiene.

Every case here is a gated FAIL but one: comparing two in-memory strings cannot
flap on ordinary work, so a failure can only be a regression. The exception is
group A's `hand-copies-are-named`, an INFO census -- the comment beside it
gives the reason, that "this server keeps its own" is a legitimate answer and
so not a thing to fail on. Its walk and its roster of declared reasons live in
the generator (`hand_copies`, `HAND_COPY_REASONS`), because `--census
hand-copies` renders both into a page; this suite consumes them, and group G
gates that it carries no second copy of the walk.

IN-MEMORY ONLY. No subprocess, no external binary, no network, and this suite
writes NOTHING -- not into the repo, not into a sandbox. The generator exposes
`audit_text` precisely so the control group needs no scratch directory.

Group E is the other half, and it is not optional: a drift gate on its own would
only ever prove that every host agrees on the same bug. E imports every canonical
module and exercises each block's BEHAVIOUR, so a shared helper is unit-tested
once -- here, and nowhere else in the fleet. (These three sentences used to say
"fourteen" twice, standing in for the fleet. The fleet is fifteen, and per block
the host count runs from four to fifteen, so one number could never have been
right for the group as a whole.)

Groups:
  A. GATE:    the live regions in Scripts/mcp-*.py and the declared hosts match
              their canonical source
  B. CONTRACT: marker spelling, hashing, anchoring, layout, source disjointness
  C. CONTROL: mutations are detected; quoted markers are not regions; a name
              does not resolve against a source that does not define it
  D. HYGIENE: no bytecode written, no source file touched
  E. BLOCKS:  what each shared block actually does
  F. TABS:    which blocks may be re-indented for a tab-indented host, decided
              per BLOCK and mechanically; one that may not is refused by name;
              a space-indented host is served exactly what it was served before
  G. CENSUS:  `--census`, the generator's READ path -- the counts a page used to
              type, derived from the same walk, sorted rather than merely
              stable, and unable to write
  H. STRICT:  R-0067 + R-0068 -- every host carries the strict JSON blocks and
              routes its frame read and write through them; every other bare
              json parse, and every bare emit on the frame tier, is declared in
              STRICT_JSON_EXCEPTIONS with its reason (ast, against the live
              tree, with a synthetic control first)
  I. HTTP FRONT: R-0072 -- the two hosts of `_mcp_httpfront.py`: every stdlib
              override in their front classes is generated or declared in
              HTTPFRONT_ADAPTATIONS with a reason, and the seams the generated
              members read are provided (with planted controls)

THE CENSUS IS GATED ON THREE PROPERTIES, NOT ON ITS PROSE. Its consumer is
`docs/measurements.json`, which hands the argv to `mcp-wiki`'s `measure`: the
body is rendered INTO a page and a digest of it is recorded on the page's END
marker. So the output must be DERIVED (group G re-does the arithmetic and
refuses a number this walk cannot produce), SORTED (asserted by re-rendering
with the targets reversed and the block maps re-inserted backwards -- byte
identity across two input ORDERS, where identity across two runs would pass a
renderer that iterates a set), and UNABLE TO WRITE (`apply_regions` is replaced
by a recorder for the duration, so the case can fail without the suite having
rewritten fifteen servers to discover it). The sentences themselves are not
pinned: a typed copy of them would fail on every rewording nobody is gating, and
would pass the one defect that matters -- the right sentence carrying the wrong
number.

Usage:
  python3 tests/test_generated_region.py            # standalone
  python3 tests/run.py generated_region             # through the fleet runner
  python3 tests/test_generated_region.py --brief    # one line per group

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every non-informational case passes.
"""

import ast
import asyncio
import contextlib
import hashlib
import http.server
import io
import json
import logging
import os
import socket
import stat
import sys
import time
import types
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "generated_region"

SOURCE = H.repo_path("Scripts", "_mcp_json.py")
CONCURRENCY_SOURCE = H.repo_path("Scripts", "_mcp_concurrency.py")
LOGGING_SOURCE = H.repo_path("Scripts", "_mcp_logging.py")
LSP_SOURCE = H.repo_path("Scripts", "_mcp_lsp.py")
PAGING_SOURCE = H.repo_path("Scripts", "_mcp_paging.py")
WEBSOCKET_SOURCE = H.repo_path("Scripts", "_mcp_websocket.py")
OAUTH_SOURCE = H.repo_path("Scripts", "_mcp_oauth.py")
HTTPFRONT_SOURCE = H.repo_path("Scripts", "_mcp_httpfront.py")
GENERATOR = H.repo_path("Scripts", "amalgamate.py")
TARGET = H.repo_path("Scripts", "mcp-purity.py")
SCRIPTS = H.repo_path("Scripts")

# The on-disk format contract. Spelled here, asserted against the generator in
# group B -- see the docstring for why these are not imported.
BEGIN_PREFIX = "# BEGIN GENERATED:"
END_PREFIX = "# END GENERATED:"
TARGET_GLOB = "mcp-*.py"
CANONICAL_NAME = "_mcp_json.py"
CONCURRENCY_CANONICAL_NAME = "_mcp_concurrency.py"
LOGGING_CANONICAL_NAME = "_mcp_logging.py"
LSP_CANONICAL_NAME = "_mcp_lsp.py"
PAGING_CANONICAL_NAME = "_mcp_paging.py"
WEBSOCKET_CANONICAL_NAME = "_mcp_websocket.py"
BROTLI_CANONICAL_NAME = "_mcp_brotli.py"
CHROME_CANONICAL_NAME = "_mcp_chrome.py"
ZSTD_CANONICAL_NAME = "_mcp_zstd.py"
WEBSEARCH_CANONICAL_NAME = "_mcp_websearch.py"
CODESEARCH_CANONICAL_NAME = "_mcp_codesearch.py"
OAUTH_CANONICAL_NAME = "_mcp_oauth.py"
HTTPFRONT_CANONICAL_NAME = "_mcp_httpfront.py"
# The registry is part of the same contract: it is written out by hand in the
# generator precisely so a new `_mcp_*.py` file cannot become a generation
# source by existing, and a test that read it back off a glob would agree with
# whatever the glob found. Mirrored here rather than imported for the same
# reason the markers are, and the mirror earns its keep on exactly this kind of
# edit: a fifth source added to the generator and not to this tuple fails
# `sources-registered` by name instead of being adopted silently.
CANONICAL_NAMES = (BROTLI_CANONICAL_NAME, CHROME_CANONICAL_NAME,
                   CODESEARCH_CANONICAL_NAME,
                   CANONICAL_NAME, CONCURRENCY_CANONICAL_NAME,
                   HTTPFRONT_CANONICAL_NAME, LOGGING_CANONICAL_NAME,
                   LSP_CANONICAL_NAME, OAUTH_CANONICAL_NAME,
                   PAGING_CANONICAL_NAME, WEBSEARCH_CANONICAL_NAME,
                   WEBSOCKET_CANONICAL_NAME, ZSTD_CANONICAL_NAME)

# The hosts OUTSIDE `TARGET_GLOB`, mirrored for the reason the source registry
# is: the generator names them by hand so a script cannot become a target by
# existing, and a test that read them back off the generator would agree with
# any edit to it. `target-glob` asserts the two spellings agree, and
# `fleet-ok` gates these alongside the servers -- a declared host the gate did
# not walk would be a target whose drift nothing reports.
DECLARED_HOSTS = ("search_duckduckgo.py", "search_github.py", "llm-router.py")

# The sources a host takes WHOLE or not at all (G-c): the Chrome client and its
# two decoders are one state machine whose blocks call one another, so a host
# carrying some of a source's blocks and not the rest is a violation, never a
# choice. The generator does not enforce it -- `whole-source-regions` does --
# and it is mirrored for the reason the registry is: a test that read the tuple
# back off the generator would agree with a source quietly dropped from it.
# `sources-registered` asserts the two spellings agree.
WHOLE_SOURCES = (BROTLI_CANONICAL_NAME, CHROME_CANONICAL_NAME,
                 CODESEARCH_CANONICAL_NAME, WEBSEARCH_CANONICAL_NAME,
                 ZSTD_CANONICAL_NAME)

# The one row `--census hosts` renders for a whole source whose blocks all share
# one host set. Spelled here, not imported, because it is a page format the
# census-hosts case parses; `%d` is the source's block count.
WHOLE_SOURCE_ROW = "all %d blocks (whole source)"

# The websocket blocks call one another, and `host_provides` offers a region
# only the host's imports -- so no websocket block can stand in a region of its
# own, and every real host spells ONE marker: the core below, dependency first,
# then the wrapper it uses. The tab fixture renders each block inside exactly
# that shape, because rendering one alone would assert a region no host may use.
WEBSOCKET_CORE = ("WebSocketError", "WS_MAX_HANDSHAKE_BYTES",
                  "WS_MAX_FRAME_BYTES", "WS_MAX_MESSAGE_BYTES",
                  "_ws_parse_url", "_ws_handshake_request",
                  "_ws_handshake_split", "_ws_handshake_verify", "_ws_mask",
                  "_ws_encode_frame", "_ws_parse_frame", "_ws_assemble",
                  "_ws_control_reply", "_WsConnection", "_ws_step")

# The log-value sanitiser and the two bounds it reads (R-0070): one marker in
# every host, constants first, because `free_names` refuses `_log_value` in a
# region that does not also define the names it reads.
LOG_VALUE_NAMES = ("_LOG_VALUE_WIDTH", "_LOG_KEYS_SHOWN", "_log_value")

# The OAuth blocks call one another the same way, so every real host spells ONE
# marker for them too: the sans-IO core below, in source order, then the I/O
# wrapper it uses (`_oauth_listen`, `_oauth_sync_accept_callback`). The tab
# fixture renders each block inside exactly that shape, for the reason it does
# the websocket ones.
OAUTH_CORE = ("OAUTH_TOKEN_LIMIT", "OAUTH_BODY_LIMIT", "OAUTH_JWT_LIMIT",
              "OAUTH_INT_LITERAL_LIMIT", "OAUTH_REFRESH_SKEW_S", "OAUTH_MIN_REFRESH_INTERVAL_S",
              "OAUTH_CALLBACK_HEAD_LIMIT",
              "OAUTH_CALLBACK_BAD_LIMIT", "OAUTH_CALLBACK_CONN_TIMEOUT_S",
              "OAUTH_ERROR_KINDS", "OAUTH_RELOGIN_CODES", "OAUTH_ERROR_CODES",
              "OAUTH_TRANSIENT_CODES", "OAUTH_CODE_ALPHABET",
              "OAUTH_B64URL_ALPHABET", "OAUTH_ID_ALPHABET", "OAuthError",
              "_oauth_pairs_ok", "OAuthProvider", "_oauth_b64url",
              "_oauth_b64url_decode", "_oauth_pkce_pair", "_oauth_new_state",
              "_oauth_uuid4_urn", "_oauth_token_ok", "_oauth_state_matches",
              "_oauth_authorize_url", "_oauth_form_headers",
              "_oauth_json_headers", "_oauth_exchange_request",
              "_oauth_refresh_request", "_oauth_device_start_request",
              "_oauth_device_poll_request", "_oauth_error_code",
              "_oauth_no_duplicate_keys", "_oauth_no_constant",
              "_oauth_bounded_int", "_oauth_json_object", "_oauth_status_refusal",
              "_oauth_error_refusal", "_oauth_parse_token_response",
              "_oauth_device_interval", "_oauth_parse_device_start",
              "_oauth_parse_device_poll", "_oauth_id_ok", "_oauth_jwt_claims",
              "_oauth_check_id_token", "_oauth_scope_has",
              "_oauth_account_claims", "_oauth_token_due",
              "_oauth_parse_callback", "_oauth_callback_page",
              "_oauth_callback_verdict")

# The hosts of _mcp_httpfront.py (R-0072, ADR 0029): the server and handler class each takes members into.
HTTPFRONT_HOSTS = {"mcp-proxy.py": ("_ProxyHttpServer", "_ProxyHttpHandler"),
                   "llm-router.py": ("_RouterHttpServer", "_RouterHandler")}
HTTPFRONT_SERVER = ("server_bind", "process_request", "handle_error")
HTTPFRONT_HANDLER = ("parse_request", "handle_expect_100", "_single_header")
HTTPFRONT_MODULE = ("_HTTP_STDLIB_REFUSAL_HEADERS", "_http_token_value",
                    "_http_write_ready_file", "_http_remove_ready_file", "_HeaderDeadlineReader")
# Every stdlib member a host front class overrides BY HAND, with its measured reason.
# The override set is computed (dir() of the stdlib base x the class body), never typed;
# an override neither generated nor listed fails GI1, and so does a row whose override is gone.
HTTPFRONT_ADAPTATIONS = {
    ("mcp-proxy.py", "_ProxyHttpServer", "__init__"):
        "takes (address, handler, settings); sets core and loop for the asyncio bridge",
    ("mcp-proxy.py", "_ProxyHttpServer", "process_request_thread"):
        "releases conn_sem in a finally and nothing else",
    ("mcp-proxy.py", "_ProxyHttpHandler", "setup"):
        "installs _HeaderDeadlineReader with the header bound as its cap",
    ("mcp-proxy.py", "_ProxyHttpHandler", "handle_one_request"):
        "one header bound for every request: the proxy has no pre-auth bound",
    ("mcp-proxy.py", "_ProxyHttpHandler", "log_message"):
        "method and path without query, structure only (ADR 0011)",
    ("mcp-proxy.py", "_ProxyHttpHandler", "send_error"):
        "fixed body-less refusal through _refuse (R-0069)",
    ("llm-router.py", "_RouterHttpServer", "__init__"):
        "takes (addr, settings, cfg, tokens) with the handler fixed; adds inflight, the "
        "token store, active/active_cond for the drain and the sampling tables",
    ("llm-router.py", "_RouterHttpServer", "process_request_thread"):
        "counts active under active_cond for the shutdown drain (KD-4), then releases "
        "conn_sem in the finally",
    ("llm-router.py", "_RouterHandler", "setup"):
        "installs _HeaderDeadlineReader with the socket timeout as its cap (V4) and "
        "starts every connection unauthenticated (V3)",
    ("llm-router.py", "_RouterHandler", "handle_one_request"):
        "a shorter header bound until the connection has authenticated (V3)",
    ("llm-router.py", "_RouterHandler", "log_message"):
        "masks an unrouted path and an unhandled method (V21)",
    ("llm-router.py", "_RouterHandler", "send_error"):
        "Anthropic envelope with _RT_STDLIB_REFUSAL's fixed text plus the generated "
        "_HTTP_STDLIB_REFUSAL_HEADERS (B21, D3)",
}

GA = "A. GATE: live regions match their canonical source"
GB = "B. CONTRACT: marker spelling, hashing, anchoring, layout, disjointness"
GC = "C. CONTROL: mutations detected, quoted markers inert, sources not crossed"
GD = "D. HYGIENE: no bytecode, no source touched"
GE = "E. BLOCKS: what each shared block actually does"
GF = "F. TABS: per-block safety, host detection, space hosts untouched"
GG = "G. CENSUS: the read path a page renders -- derived, sorted, writes nothing"
GH = "H. STRICT: peer JSON is parsed and emitted through the strict blocks"
GI = "I. HTTP FRONT: every stdlib override generated or declared, seams provided"

# The census subjects, mirrored here for the reason CANONICAL_NAMES is: a fifth
# subject added to the generator and not to this tuple leaves the new one with
# no case at all, which is the failure a census silently rendering an empty
# block would produce on a page. `census-sources-is-the-registry` asserts the
# two spellings agree.
CENSUS_KINDS = ("fleet", "sources", "hosts", "hand-copies")

# --- synthetic material for group C -------------------------------------------

SYNTH_BODY = 'def _helper():\n    return "ok"\n'
SYNTH_BLOCKS = {"_helper": SYNTH_BODY}
SYNTH_SOURCES = {CANONICAL_NAME: SYNTH_BLOCKS}
RENDER_BLOCKS = {"a": "def a():\n    pass\n", "b": "def b():\n    pass\n"}

# A second synthetic domain that defines the SAME name with a different body.
# The collision is the point: it makes "which source did the marker say" an
# observable question instead of a stylistic one.
OTHER_BODY = 'def _helper():\n    return "OTHER"\n'
OTHER_ONLY_BODY = 'def _elsewhere():\n    return 1\n'
TWO_SOURCES = {
    CANONICAL_NAME: SYNTH_BLOCKS,
    LSP_CANONICAL_NAME: {"_helper": OTHER_BODY, "_elsewhere": OTHER_ONLY_BODY},
}


def synth_host(body, recorded=None, names="_helper", source=CANONICAL_NAME):
    """Build a synthetic target file carrying exactly one region."""
    tail = " %s" % recorded if recorded else ""
    return (
        '"""A synthetic target."""\n'
        "%s %s :: %s\n" % (BEGIN_PREFIX, source, names)
        + body
        + "%s%s\n" % (END_PREFIX, tail)
    )


# The fleet-wide sentence `_json_error_window` was extracted to serve. Spelled
# out here for the same reason the markers are: it is a CONTRACT across every
# server now, and a test that derived it from one of them would only ever prove
# they all agree -- including on a drift.
#
# The SENTENCE is one string; what varies is only the LOCAL NAME holding the
# text that failed to parse, and the fleet spells that name four ways: `params`
# in a param normalizer, and `args` / `arguments` / `tool_args` in the three
# flavours of tools/call handler. Every one is listed DELIBERATELY, and the list
# is meant to be edited when a fifth appears. Do NOT relax this into a substring
# or an identifier wildcard: the whole point is that "Near the error", a missing
# period and a lost trailing space are each caught, and a wildcard would wave
# all three through while still looking like a test.
#
# `value` was the fifth spelling and has LEFT the list -- which is how a name
# should leave it. It was the local inside `_ensure_dict`, and that function is
# now a generated block co-listed with the window, so its call site is single-
# sourced and compared byte for byte by group A instead of by this census. A
# hand copy coming back would surface in `hand-copies-are-named` first, and a
# drift inside the block cannot reach here at all.
WINDOW_NAME = "_json_error_window"
WINDOW_SENTENCE = 'f"Near the failure: {%s(%%s, exc.pos)}. "' % WINDOW_NAME
WINDOW_FIELDS = ("params", "args", "arguments", "tool_args")
WINDOW_MIN_HOSTS = 8

# The one block that calls the window from INSIDE a region. `host_provides`
# offers a region only the host's module-level IMPORTS, never a name another
# region defines, so `_ensure_dict` in a region of its own is refused in every
# host -- it travels co-listed with the window on one marker. That co-listing is
# why the liveness case below asks about the PAIR rather than about one name.
WINDOW_RELAY = "_ensure_dict"

# R-0067 + R-0068: the strict JSON blocks, in the order every host's marker
# lists them (dependency first). `_strict_loads` refuses NaN, Infinity,
# -Infinity, a float that overflows to an infinity and an integer literal over
# JSON_INT_LITERAL_LIMIT characters, each as a json.JSONDecodeError; and
# `_strict_dumps` is json.dumps with allow_nan=False.
STRICT_NAMES = ("JSON_INT_LITERAL_LIMIT", "_json_no_constant", "_json_finite_float",
                "_json_bounded_int", "_strict_loads", "_strict_dumps")

# Group H's scope. Every PARSE in a host is gated, wherever it sits: a parse is
# where both defects bite (NaN accepted, a long digit run parsed at quadratic
# cost on 3.9.6), and a new `json.loads` should have to say why it may be lax.
# An EMIT is gated only on the frame tier -- every method of `McpServer`, and
# the whole of the one host whose every byte is a relayed frame -- because a
# `json.dumps` that renders tool output INTO a text result never reaches the
# wire as a JSON number: the frame emit around it is the strict one.
JSON_PARSE_ATTRS = ("load", "loads")
JSON_EMIT_ATTRS = ("dump", "dumps")
FRAME_CLASS = "McpServer"
WHOLE_FILE_FRAME_HOSTS = ("mcp-proxy.py",)

# Every bare json call group H tolerates, keyed (host, enclosing qualname,
# json attribute), with the MEASURED reason it is not a peer's frame. A row
# whose site has gone fails `strict-exceptions-not-stale`, so the table cannot
# outlive what it excuses.
#
# Two families are declared OUT OF SCOPE rather than safe: an upstream service's
# HTTP body (context7, jenkins, Chrome's /json endpoints) and a non-MCP child's
# stream (an LSP server's Content-Length body, Chrome's CDP websocket). Both are
# peer bytes, and the quadratic int parse reaches them on 3.9.6 too; R-0067 and
# R-0068 scope the MCP frame tier, so these are recorded here, by name, as the
# next candidates instead of being widened silently into this change.
_UPSTREAM = ("out of scope (R-0067/R-0068 cover MCP frames): an upstream "
             "service's HTTP body, not a peer's MCP frame")
_LSP_CHILD = ("out of scope (R-0067/R-0068 cover MCP frames): the LSP child's "
              "Content-Length body, not a peer's MCP frame")
STRICT_JSON_EXCEPTIONS = {
    ("mcp-clangd.py", "read_lsp_message", "loads"): _LSP_CHILD,
    ("mcp-cuda.py", "read_lsp_message", "loads"): _LSP_CHILD,
    ("mcp-lua-lsp.py", "read_lsp_message", "loads"): _LSP_CHILD,
    ("mcp-purity.py", "read_lsp_message", "loads"): _LSP_CHILD,
    ("mcp-context7.py", "_parse_error_response", "loads"): _UPSTREAM,
    ("mcp-context7.py", "handle_context7_resolve_library_id", "loads"): _UPSTREAM,
    ("mcp-jenkins.py", "_read_response", "loads"): _UPSTREAM,
    ("mcp-gdc.py", "GdcManager.get_targets", "loads"): _UPSTREAM,
    ("mcp-gdc.py", "handle_new_page", "loads"): _UPSTREAM,
    ("mcp-gdc.py", "CdpSession._recv_loop", "loads"):
        "out of scope (R-0067/R-0068 cover MCP frames): Chrome's CDP websocket "
        "message, not a peer's MCP frame",
    ("mcp-inspect.py", "_v_json", "loads"):
        "the `validate` tool's verdict on a user's file: it reports what the "
        "stdlib parser accepts, and making it strict would change the verdict, "
        "not harden a frame",
    ("mcp-purity.py", "_RegexWorker._ask", "loads"):
        "the server's own regex worker subprocess answering on a private "
        "length-prefixed pipe the server spawned; not a peer",
    ("mcp-cuda.py", "_prepare_compile_commands", "load"):
        "a compile_commands.json in the user's project, read as a build input",
    ("mcp-purity.py", "_prepare_compile_commands", "load"):
        "a compile_commands.json in the user's project, read as a build input",
    ("mcp-cuda.py", "handle_init", "load"):
        "a compile_commands.json in the user's project, read as a build input",
    ("mcp-tshark.py", "handle_config", "load"):
        "the server's own saved-config file, written by this server",
    ("mcp-webfetch.py", "_cache_load", "load"):
        "the server's own cache entry, written by this server",
    ("mcp-wiki.py", "load_measurements", "loads"):
        "the repository's docs/measurements.json, a checked-in file",
    ("mcp-proxy.py", "load_config", "loads"):
        "the operator's --config / --config-json, refused on any parse error "
        "including ValueError and RecursionError; not a peer's frame",
}


@contextlib.contextmanager
def int_digits_unlimited():
    """Lift 3.11+'s int-digit limit for the duration (0 = none, as on 3.9.6)."""
    get = getattr(sys, "get_int_max_str_digits", None)
    if get is None:
        yield
        return
    saved = get()
    sys.set_int_max_str_digits(0)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(saved)


def bare_json_calls(mod, label, text, sources):
    """(qualname, attr, lineno) for every `json.<attr>(...)` call outside every region.

    Read with `ast`, the fleet's rule for finding a call site; the region spans
    come from the generator's own `audit_text`, so a call inside a generated
    block -- `_ensure_dict` calling `_strict_loads`, `_strict_loads` calling
    `json.loads` -- is the block, not a site.
    """
    spans = [(r.begin + 1, r.end + 1) for r in mod.audit_text(label, text, sources)]
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, scope + [child.name])
                continue
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "json"
                    and child.func.attr in JSON_PARSE_ATTRS + JSON_EMIT_ATTRS
                    and not any(b <= child.lineno <= e for b, e in spans)):
                found.append((".".join(scope) or "<module>", child.func.attr, child.lineno))
            visit(child, scope)

    visit(ast.parse(text, filename=label), [])
    return found


def strict_violations(mod, label, text, sources, exceptions):
    """(undeclared sites, declared keys seen) for one host under group H's scope."""
    bad, seen = [], set()
    for qualname, attr, lineno in bare_json_calls(mod, label, text, sources):
        frame = (label in WHOLE_FILE_FRAME_HOSTS
                 or qualname == FRAME_CLASS or qualname.startswith(FRAME_CLASS + "."))
        if attr in JSON_EMIT_ATTRS and not frame:
            continue
        key = (label, qualname, attr)
        if key in exceptions:
            seen.add(key)
            continue
        bad.append("%s:%d %s: bare json.%s" % (label, lineno, qualname, attr))
    return bad, seen


def outside_regions(mod, label, text, sources):
    """*text* with every generated region's marker-to-marker span removed.

    A call INSIDE a region is the block calling itself, not a caller; counting
    it would make "this region has a user" true the moment the region exists.
    """
    lines = text.splitlines(keepends=True)
    for region in sorted(mod.audit_text(label, text, sources),
                         key=lambda r: r.begin, reverse=True):
        del lines[region.begin:region.end + 1]
    return "".join(lines)


def tab_host(names, source=PAGING_CANONICAL_NAME):
    """A synthetic TAB-indented target carrying exactly one region at column 0.

    The imports are not decoration: the block contract check runs against the
    host, and `_result`'s annotation needs `Any`, `encode_lsp_message` needs
    `json`, `_FENCE_LINE_RE` -- the first CONSTANT block -- needs `re`,
    `_configure_logging` needs `logging`, `os` and `sys`, the LSP URI pair
    needs `pathlib` (`path_to_uri`) plus `urlparse` and `url2pathname`
    (`uri_to_path`), and the LSP CLIENT half needs `asyncio` (`_request`) on top
    of `Any` and `pathlib`. A fixture without them would be refused for the
    wrong reason and the tab cases would pass on an error that has nothing to do
    with tabs.

    `tab-emission-is-all-tabs` drives EVERY tab-safe block through this one
    fixture, so the import list is not "what today's cases happen to need" but
    the union of what every canonical block reads. A block added upstream with
    a new free name lands here as a refusal naming that name.

    That last sentence has now been paid out THREE times rather than merely
    promised. The logging source arrived with three free names this fixture did
    not import, and the suite stopped with a refusal naming `logging`, `os` and
    `sys`. The LSP URI pair then did the same for `pathlib`, `urlparse` and
    `url2pathname` -- and that one is the more instructive of the three, because
    the same refusal had already fired against three REAL hosts: `mcp-clangd`,
    `mcp-cuda` and `mcp-lua-lsp` each had to be given the two `urllib` imports
    by hand before their regions would render at all. The LSP CLIENT half paid
    it a third time, for `asyncio` alone, and that one is the reverse case worth
    noting: all four real hosts already imported every name those blocks read,
    so the fixture was the ONLY thing the lift refused. A gate that only ever
    fires when a server is also broken is a gate nobody has separated from the
    servers.

    The websocket source paid it a fourth time, for `base64`, `hashlib` and
    `socket` -- and its one tab host is the search script, the first host that
    is not a server at all.

    The Chrome client and its two decoders paid it a fifth time, for `codecs`,
    `ctypes`, `hmac`, `http`, `ipaddress`, `ssl`, `struct`, `time`, `urllib`
    and `zlib` -- and the first time for sources a host takes WHOLE, so the
    fixture is driven with one marker per source rather than one per block.

    The two search sources paid it a sixth time, for `random`, `parse_qs`,
    `urlencode` and `HTMLParser` -- both taken whole, like the Chrome client --
    and then a seventh, for `unicodedata`, when both took a render sanitizer,
    and an eighth, for `secrets` and `select`, when the OAuth core arrived,
    and a ninth time, for `io`, `socketserver` and `Optional`, when the HTTP front
    arrived.
    """
    return (
        '"""A tab-indented target."""\n'
        "import asyncio\n"
        "import base64\n"
        "import codecs\n"
        "import ctypes.util\n"
        "import hashlib\n"
        "import hmac\n"
        "import http.client\n"
        "import io\n"
        "import ipaddress\n"
        "import json\n"
        "import logging\n"
        "import os\n"
        "import pathlib\n"
        "import random\n"
        "import re\n"
        "import secrets\n"
        "import select\n"
        "import socket\n"
        "import socketserver\n"
        "import ssl\n"
        "import struct\n"
        "import sys\n"
        "import time\n"
        "import unicodedata\n"
        "import urllib.parse\n"
        "import zlib\n"
        "from html.parser import HTMLParser\n"
        "from typing import Any, Optional\n"
        "from urllib.parse import parse_qs, urlencode, urlparse\n"
        "from urllib.request import url2pathname\n"
        "\n\n"
        "def _existing():\n"
        "\tif True:\n"
        "\t\treturn asyncio, json, logging, os, pathlib, re, sys, Any\n"
        "\treturn urlparse, url2pathname, secrets, select\n"
        "\n\n"
        "%s %s :: %s\n" % (BEGIN_PREFIX, source, names)
        + "%s\n" % END_PREFIX
    )


def untab(text, width=4):
    """Reverse of the emitter's conversion: one leading tab back to 4 spaces."""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.lstrip("\t")
        out.append(" " * (width * (len(line) - len(body))) + body)
    return "".join(out)


def expect_exit(fn):
    """Run *fn*; return its SystemExit message, or None if it did not raise."""
    try:
        fn()
    except SystemExit as exc:
        return str(exc)
    return None


def problem_if(condition, message):
    return [message] if condition else []


def whole_source_problems(host, source, names, sources):
    """G-c for one region: *names* must be ALL of *source*'s blocks, in order.

    The order is the block map's insertion order, which is source order. One
    checker serves the live walk and the negative controls, so a checker that
    waved everything through would fail its own controls first.
    """
    want = list(sources.get(source, {}))
    names = list(names)
    if names == want:
        return []
    missing = [name for name in want if name not in names]
    extra = [name for name in names if name not in want]
    kept = [name for name in names if name in want]
    order = [name for name in want if name in kept]
    brk = next((i for i, (a, b) in enumerate(zip(kept, order)) if a != b), None)
    return ["%s: its %s region is not the whole source -- missing %s, extra %s, "
            "first order break %s"
            % (host, source, missing, extra,
               "none" if brk is None
               else "at #%d (%s where the source has %s)"
                    % (brk, kept[brk], order[brk]))]


# --- groups -------------------------------------------------------------------

def group_gate(suite, mod):
    sources = mod.load_all_blocks()
    regions = mod.audit(Path(TARGET), sources)

    # The registry itself, before anything is resolved against it: an explicit
    # tuple of filenames, each one a real file. A source silently missing from
    # the registry would not fail below -- every region naming it would be
    # refused, which reads as a marker defect rather than a registry one.
    registry = sorted(mod.CANONICAL_SOURCES)
    problems = problem_if(
        registry != sorted(CANONICAL_NAMES),
        "generator registry is %s, the on-disk contract is %s"
        % (registry, sorted(CANONICAL_NAMES)),
    )
    problems += ["%s: not a file at %s" % (name, path)
                 for name, path in sorted(mod.CANONICAL_SOURCES.items())
                 if not path.is_file()]
    # The whole-source list is part of the same registry: a source dropped from
    # the generator's tuple would stop being checked by `whole-source-regions`
    # without anything else noticing.
    problems += problem_if(
        tuple(getattr(mod, "WHOLE_SOURCES", ())) != WHOLE_SOURCES,
        "generator WHOLE_SOURCES is %r, the on-disk contract is %r"
        % (getattr(mod, "WHOLE_SOURCES", None), WHOLE_SOURCES),
    )
    problems += ["%s: a whole source that is not a registered source" % name
                 for name in WHOLE_SOURCES if name not in CANONICAL_NAMES]
    suite.record(GA, "sources-registered", problems,
                 detail=["registered: %s" % ", ".join(registry),
                         "whole: %s" % ", ".join(WHOLE_SOURCES)])

    # The COUNT is not asserted: it grows every time a block is extracted, and a
    # number typed here would fail on progress rather than on a regression.
    suite.record(GA, "region-present", problem_if(
        not regions,
        "mcp-purity.py carries no generated region at all",
    ), detail=["%d region(s): %s"
               % (len(regions), "; ".join(r.label for r in regions))])

    drifted = ["%s: body differs from the canonical render" % r.label
               for r in regions if r.body != r.wanted]
    suite.record(GA, "body-identical", drifted, detail=drifted)

    unsummed = ["%s: recorded %r, body hashes to %r"
                % (r.label, r.recorded, mod.body_hash(r.body))
                for r in regions if r.recorded != mod.body_hash(r.body)]
    suite.record(GA, "sum-recorded", unsummed, detail=unsummed)

    stale = []
    listed = set()
    declared = [Path(SCRIPTS) / name for name in DECLARED_HOSTS]
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)) + declared:
        for region in mod.audit(path, sources):
            listed.update((region.source, name) for name in region.names)
            if region.state != "ok":
                stale.append("%s [%s]: %s" % (path.name, region.label, region.state))
    suite.record(GA, "fleet-ok", stale, detail=stale)

    # Resolved PER SOURCE, not against the union: a name that exists only in the
    # other canonical file is exactly the defect this pair of loops must see.
    missing = sorted("%s :: %s" % (source, name) for source, name in listed
                     if name not in sources[source])
    suite.record(GA, "names-exist", problem_if(
        missing,
        "regions list names their own source does not define: %s" % missing,
    ), detail=["%s :: %s" % pair for pair in sorted(listed)])

    # ONE WRITER, with the exceptions NAMED rather than hidden. A canonical block
    # name a host binds at top level or as a direct class member, outside every
    # generated region, is a hand copy. The walk and the roster of MEASURED
    # reasons both live in the generator -- `hand_copies` and
    # `HAND_COPY_REASONS` -- because `--census hand-copies` renders them into a
    # page, and a page cannot render a comment in this file. This case consumes
    # that one implementation; group G gates it (`census-hand-copies-is-the-
    # generator-walk`), including that no second copy of the walk lives here.
    #
    # Scoped like the census: the fleet AND the declared hosts, every file the
    # generator writes.
    #
    # INFO, not FAIL: "this server keeps its own" is a legitimate answer, so
    # this censuses rather than judges -- but an UNDECLARED hand copy, or a new
    # one, cannot appear without landing on this line, and the census is what
    # makes the next fan-out decision a reading rather than a survey.
    hand = ["%s: %s%s" % (host, name,
                          "" if (Path(host).name, name) in mod.HAND_COPY_REASONS
                          else "  [UNDECLARED]")
            for host, name in mod.hand_copies(
                sources, sorted(Path(SCRIPTS).glob(TARGET_GLOB)) + declared)]
    suite.record(GA, "hand-copies-are-named", status=H.INFO,
                 detail=hand or ["no host keeps a hand copy of a canonical "
                                 "block name"])

    # `_json_error_window` went fleet-wide, and that creates two invariants
    # nothing measured before. FAIL rather than INFO for both: they compare text
    # already in the repo, with no environment, no binary and no ordering, so
    # neither can flap on ordinary work -- the fleet's bar for a gated FAIL.
    hosts, callers, sentences = set(), set(), []
    relay_hosts, relay_callers = set(), set()
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        text = path.read_text(encoding="utf-8")
        regioned = set()
        for region in mod.audit_text(path.name, text, sources):
            regioned.update(region.names)
        if WINDOW_NAME in regioned:
            hosts.add(path.name)
        if WINDOW_RELAY in regioned:
            relay_hosts.add(path.name)
        body = outside_regions(mod, path.name, text, sources)
        if "%s(" % WINDOW_NAME in body:
            callers.add(path.name)
        if "%s(" % WINDOW_RELAY in body:
            relay_callers.add(path.name)
        sentences += [(path.name, line.strip()) for line in body.splitlines()
                      if "Near the failure:" in line]

    # A region with no caller is dead code -- and worse than ordinary dead code,
    # because the drift gate would then faithfully prove that N files agree on
    # carrying it. The reverse is a NameError the moment that path is taken.
    #
    # Reached THROUGH THE RELAY, which is not the same as waived for it. Once
    # `_ensure_dict` joined the window on one marker, the three servers whose
    # only window call site was the one inside that function -- mcp-forge,
    # mcp-git and mcp-inspect -- call the window from INSIDE the region, where
    # `outside_regions` deliberately cannot see it. What keeps their region
    # alive is a call to `_ensure_dict`, and it is required rather than assumed:
    # a server that stopped calling BOTH still lands on this line.
    reached = callers | (relay_hosts & relay_callers)
    problems = ["%s: hosts %s, and neither it nor %s is called from outside a "
                "region" % (name, WINDOW_NAME, WINDOW_RELAY)
                for name in sorted(hosts - reached)]
    problems += ["%s: calls %s without hosting the region -- NameError"
                 % (name, WINDOW_NAME) for name in sorted(callers - hosts)]
    problems += ["%s: calls %s without hosting it -- NameError"
                 % (name, WINDOW_RELAY)
                 for name in sorted(relay_callers - relay_hosts)]
    problems += problem_if(
        len(hosts) < WINDOW_MIN_HOSTS,
        "only %d server(s) host %s; this case examined too few to mean "
        "anything" % (len(hosts), WINDOW_NAME),
    )
    # Without this the relay arm is vacuous: if nothing hosted `_ensure_dict`
    # the union above would collapse back to `callers` and read green forever.
    problems += problem_if(
        not relay_hosts,
        "no server hosts %s, so the relay arm of this case proves nothing"
        % WINDOW_RELAY,
    )
    suite.record(GA, "window-region-has-a-caller", problems,
                 detail=["%d host(s): %d call %s directly, %d reach it only "
                         "through %s"
                         % (len(hosts), len(callers), WINDOW_NAME,
                            len((relay_hosts & relay_callers) - callers),
                            WINDOW_RELAY)])

    # One writer for the code was the easy half. The SENTENCE is the half that
    # rots: it is hand-written at each call site, says the same thing in a dozen
    # files, and nothing but this case compares them.
    #
    # Paired against the servers that call the window BY HAND, not against the
    # servers that HOST it. Those two sets used to coincide and stopped the day
    # `_ensure_dict` became a block: the sentence inside it is generated now,
    # and group A compares it byte for byte. What is left under this case is
    # exactly the hand-written population it was written for.
    allowed = {WINDOW_SENTENCE % field for field in WINDOW_FIELDS}
    problems = ["%s: %s" % (name, line) for name, line in sentences
                if line not in allowed]
    carrying = {name for name, _line in sentences}
    problems += problem_if(
        carrying != callers,
        "the servers carrying the sentence and the servers calling %s by hand "
        "differ: %s" % (WINDOW_NAME, sorted(carrying ^ callers)),
    )
    problems += problem_if(
        len(sentences) < WINDOW_MIN_HOSTS,
        "only %d call site(s) found; the wording comparison is vacuous"
        % len(sentences),
    )
    suite.record(GA, "window-sentence-identical", problems,
                 detail=["%d call site(s), %d distinct wording(s)"
                         % (len(sentences),
                            len({line for _n, line in sentences}))])

    # Every registered source must be ASKED FOR by a live region. A canonical
    # file nobody names is not harmless: it is a shelf the drift gate cannot
    # reach, so a block rotting in it reads green here forever.
    used = {source for source, _name in listed}
    suite.record(GA, "every-source-in-use", problem_if(
        sorted(used) != sorted(CANONICAL_NAMES),
        "registered but unrequested: %s"
        % sorted(set(CANONICAL_NAMES) - used),
    ), detail=["%s: %d name(s) requested"
               % (source, len([1 for s, _n in listed if s == source]))
               for source in sorted(CANONICAL_NAMES)])

    # G-c: a host takes a WHOLE_SOURCES source whole, in source order, or not at
    # all. `listed` above is a set of pairs, which forgets both the host and the
    # order, so this walks the regions again. The checker is proven on two
    # synthetic markers first -- one name missing, two names swapped -- because
    # a checker that silently matches nothing is indistinguishable from a clean
    # tree.
    problems, walked = [], []
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)) + declared:
        for region in mod.audit(path, sources):
            if region.source in WHOLE_SOURCES:
                walked.append("%s: %s (%d names)"
                              % (path.name, region.source, len(region.names)))
                problems += whole_source_problems(path.name, region.source,
                                                  region.names, sources)
    controls = []
    for source in WHOLE_SOURCES:
        names = list(sources.get(source, {}))
        if len(names) < 2:
            problems.append("%s: fewer than two blocks, so the controls below "
                            "prove nothing" % source)
            continue
        dropped = names[:-1]
        swapped = [names[1], names[0]] + names[2:]
        for what, bad, needle in (("one name missing", dropped, names[-1]),
                                  ("two names swapped", swapped, names[1])):
            found = whole_source_problems("control.py", source, bad, sources)
            if not found or needle not in found[0] or "control.py" not in found[0]:
                problems.append("%s: the checker let a marker with %s through "
                                "(or did not name it): %r" % (source, what, found))
        problems += ["%s: the checker refused the whole source itself: %s"
                     % (source, found)
                     for found in [whole_source_problems("control.py", source,
                                                         names, sources)] if found]
        controls.append("%s: %d names, 2 mutations refused" % (source, len(names)))
    suite.record(GA, "whole-source-regions", problems,
                 detail=(walked or ["no whole-source region in any host yet"])
                 + controls)


def group_contract(suite, mod):
    suite.record(GB, "begin-prefix", problem_if(
        mod.BEGIN_PREFIX != BEGIN_PREFIX,
        "generator BEGIN_PREFIX is %r, on-disk contract is %r"
        % (mod.BEGIN_PREFIX, BEGIN_PREFIX),
    ))
    suite.record(GB, "end-prefix", problem_if(
        mod.END_PREFIX != END_PREFIX,
        "generator END_PREFIX is %r, on-disk contract is %r"
        % (mod.END_PREFIX, END_PREFIX),
    ))
    # The glob and the hand-declared hosts are ONE question -- what the default
    # run rewrites and `--check` gates -- so they are asserted in one case.
    problems = problem_if(
        mod.TARGET_GLOB != TARGET_GLOB,
        "generator TARGET_GLOB is %r, expected %r" % (mod.TARGET_GLOB, TARGET_GLOB),
    )
    problems += problem_if(
        tuple(mod.DECLARED_HOSTS) != DECLARED_HOSTS,
        "generator DECLARED_HOSTS is %r, the on-disk contract is %r"
        % (mod.DECLARED_HOSTS, DECLARED_HOSTS),
    )
    problems += ["%s: declared but not a file" % name for name in DECLARED_HOSTS
                 if not os.path.isfile(H.repo_path("Scripts", name))]
    problems += ["%s: declared although the glob already covers it" % name
                 for name in DECLARED_HOSTS if Path(name).match(TARGET_GLOB)]
    suite.record(GB, "target-glob", problems,
                 detail=["glob: Scripts/%s" % TARGET_GLOB,
                         "declared: %s" % ", ".join(DECLARED_HOSTS)])

    sample = b"generated-region-hash-sample"
    expected = hashlib.sha256(sample).hexdigest()[:12]
    actual = mod.body_hash(sample.decode("ascii"))
    suite.record(GB, "hash-is-sha256-12", problem_if(
        actual != expected,
        "body_hash gave %r, sha256[:12] is %r" % (actual, expected),
    ))

    # The generator resolves its paths from __file__, never from getcwd() --
    # that is what makes `python3 ../../Scripts/amalgamate.py` work.
    anchored = [
        "%s -> %s" % (name, path)
        for name, path in sorted(mod.CANONICAL_SOURCES.items())
        if os.path.realpath(str(path)) != os.path.realpath(
            H.repo_path("Scripts", name))
    ]
    suite.record(GB, "anchored-on-file", problem_if(
        os.path.realpath(str(mod.SCRIPTS_DIR)) != os.path.realpath(SCRIPTS)
        or anchored,
        "SCRIPTS_DIR=%s / %s do not resolve to the repo's Scripts/"
        % (mod.SCRIPTS_DIR, anchored),
    ))

    # No name may be defined by two canonical sources. A duplicate would make a
    # marker's meaning depend on a field readers skim past, and copying a region
    # from one server to another would then change what it emits.
    seen = {}
    collisions = []
    for source in sorted(mod.CANONICAL_SOURCES):
        for name in sorted(mod.load_blocks(mod.CANONICAL_SOURCES[source])):
            if name in seen:
                collisions.append("%r is defined by both %s and %s"
                                  % (name, seen[name], source))
            seen[name] = source
    suite.record(GB, "sources-disjoint", collisions,
                 detail=["%d block(s) across %d source(s)"
                         % (len(seen), len(mod.CANONICAL_SOURCES))])

    rendered = mod.render(CANONICAL_NAME, ["a", "b"], RENDER_BLOCKS)
    wanted = "def a():\n    pass\n\n\ndef b():\n    pass\n"
    suite.record(GB, "render-layout", problem_if(
        rendered != wanted,
        "render() gave %r, expected two blank lines and one trailing newline"
        % rendered,
    ))

    # An indent shifts every non-blank line and must NOT leave whitespace on a
    # blank one -- trailing whitespace is drift the byte comparison would catch
    # forever after.
    #
    # The indent also chooses the separator: ONE blank line between members of a
    # class body, against the two that `render-layout` above pins at column 0.
    # That is PEP 8's own split, and the indent is already the signal for which
    # side of it we are on -- a region hosted in a class needs no extra flag.
    indented = mod.render(CANONICAL_NAME, ["a", "b"], RENDER_BLOCKS, "    ")
    suite.record(GB, "render-indented", problem_if(
        indented != "    def a():\n        pass\n\n    def b():\n        pass\n",
        "indented render gave %r" % indented,
    ))


def group_control(suite, mod):
    def audit(text, sources=SYNTH_SOURCES):
        return mod.audit_text("synthetic.py", text, sources)

    good_sum = mod.body_hash(SYNTH_BODY)
    mutated = SYNTH_BODY.replace('"ok"', '"OK"')

    # 1. The control fires at all: a drifted body whose hash was recorded from
    #    that same drifted body is STALE -- the generator may rewrite it.
    regions = audit(synth_host(mutated, mod.body_hash(mutated)))
    suite.record(GC, "drift-detected", problem_if(
        len(regions) != 1 or regions[0].state != "stale",
        "expected one stale region, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 2. A body that no longer matches its OWN recorded hash was hand-edited,
    #    and must be refused rather than silently overwritten.
    regions = audit(synth_host(mutated, good_sum))
    suite.record(GC, "hand-edit-detected", problem_if(
        len(regions) != 1 or regions[0].state != "hand-edited",
        "expected one hand-edited region, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 3. An in-sync region must NOT be reported -- the other half of vacuity.
    regions = audit(synth_host(SYNTH_BODY, good_sum))
    suite.record(GC, "in-sync-silent", problem_if(
        len(regions) != 1 or regions[0].state != "ok",
        "an in-sync region should read ok, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 4-7. Every structural marker defect must be LOUD, never a silent skip.
    open_region = '"""x"""\n%s %s :: _helper\n%s' % (
        BEGIN_PREFIX, CANONICAL_NAME, SYNTH_BODY)
    open_message = expect_exit(lambda: audit(open_region))
    suite.record(GC, "missing-end", problem_if(
        not open_message,
        "a BEGIN without an END was accepted",
    ), detail=[str(open_message)])

    suite.record(GC, "missing-begin", problem_if(
        not expect_exit(lambda: audit('"""x"""\n%s %s\n' % (END_PREFIX, good_sum))),
        "an END without a BEGIN was accepted",
    ))

    reversed_pair = '"""x"""\n%s %s\n%s %s :: _helper\n%s' % (
        END_PREFIX, good_sum, BEGIN_PREFIX, CANONICAL_NAME, SYNTH_BODY)
    suite.record(GC, "reversed-markers", problem_if(
        not expect_exit(lambda: audit(reversed_pair)),
        "END-before-BEGIN was accepted",
    ))

    nested = '"""x"""\n%s %s :: _helper\n%s %s :: _helper\n%s%s\n' % (
        BEGIN_PREFIX, CANONICAL_NAME, BEGIN_PREFIX, CANONICAL_NAME,
        SYNTH_BODY, END_PREFIX)
    suite.record(GC, "nested-begin", problem_if(
        not expect_exit(lambda: audit(nested)),
        "a BEGIN inside an open region was accepted",
    ))

    # 8-9. Malformed marker payloads.
    suite.record(GC, "malformed-marker", problem_if(
        not expect_exit(lambda: audit(
            '"""x"""\n%s no-separator-here\n%s%s\n'
            % (BEGIN_PREFIX, SYNTH_BODY, END_PREFIX))),
        "a BEGIN without the '::' separator was accepted",
    ))
    unknown_source = expect_exit(lambda: audit(
        synth_host(SYNTH_BODY, good_sum, source="somewhere-else.py")))
    suite.record(GC, "unknown-source", problem_if(
        not unknown_source,
        "a region naming an unknown source was accepted",
    ), detail=[str(unknown_source)])

    # The refusal has to be actionable: whoever typed the wrong filename learns
    # the right ones here, or goes reading the generator to find them.
    listed_known = expect_exit(lambda: mod.audit_text(
        "synthetic.py",
        synth_host(SYNTH_BODY, good_sum, source="somewhere-else.py"),
        TWO_SOURCES))
    suite.record(GC, "unknown-source-lists-known", problem_if(
        not listed_known or not all(n in listed_known for n in TWO_SOURCES),
        "the refusal must name every source in play; got %r" % listed_known,
    ), detail=[str(listed_known)])

    suite.record(GC, "unknown-name", problem_if(
        not expect_exit(lambda: audit(
            synth_host(SYNTH_BODY, good_sum, names="_not_in_source"))),
        "a region naming a symbol absent from the source was accepted",
    ))

    # A name defined ONLY in the other canonical file must not resolve, and the
    # refusal must name the source that was actually asked -- not the file that
    # happens to define the name, which is the reading that would send somebody
    # to "fix" the wrong end.
    crossed = expect_exit(lambda: mod.audit_text(
        "synthetic.py",
        synth_host(SYNTH_BODY, good_sum, names="_elsewhere"),
        TWO_SOURCES))
    suite.record(GC, "cross-source-name-refused", problem_if(
        not crossed or CANONICAL_NAME not in str(crossed),
        "a name defined only in %s resolved against %s, or the refusal named "
        "the wrong file: %r" % (LSP_CANONICAL_NAME, CANONICAL_NAME, crossed),
    ), detail=[str(crossed)])

    # And the positive half: when BOTH sources define the name, the marker's
    # source decides which body is emitted. Without this, "refused" above could
    # be satisfied by a generator that simply merged the two namespaces and got
    # lucky on the collision.
    picked = mod.audit_text(
        "synthetic.py",
        synth_host(OTHER_BODY, mod.body_hash(OTHER_BODY),
                   source=LSP_CANONICAL_NAME),
        TWO_SOURCES)
    suite.record(GC, "source-selects-the-body", problem_if(
        len(picked) != 1 or picked[0].wanted != OTHER_BODY
        or picked[0].source != LSP_CANONICAL_NAME,
        "the marker's source did not choose the body: %s"
        % [(r.source, r.wanted) for r in picked],
    ))

    # 10. An empty region is drift, not "nothing to do".
    regions = audit(synth_host(""))
    suite.record(GC, "empty-region", problem_if(
        len(regions) != 1 or regions[0].state == "ok",
        "an empty region should not read ok, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 11. Whitespace-only drift is drift: byte identity, not "looks the same".
    spaced = SYNTH_BODY.rstrip("\n") + " \n"
    regions = audit(synth_host(spaced, mod.body_hash(spaced)))
    suite.record(GC, "whitespace-drift", problem_if(
        len(regions) != 1 or regions[0].state == "ok",
        "trailing-whitespace drift read as ok",
    ))

    # 12-14. BAIT: a marker that is not a comment is not a marker. This is what
    #        `tokenize` buys over a line scan, and the hazard is real -- the
    #        generator's own docstring carries a full BEGIN/END pair.
    bait_docstring = (
        '"""Documentation.\n\n'
        "    %s %s :: _helper\n"
        "    def _helper(): ...\n"
        "    %s deadbeefcafe\n"
        '"""\n'
    ) % (BEGIN_PREFIX, CANONICAL_NAME, END_PREFIX)
    suite.record(GC, "bait-docstring", problem_if(
        audit(bait_docstring),
        "a marker quoted in a docstring was treated as a region",
    ))

    bait_literal = 'NOTE = "%s %s :: _helper"\nOTHER = "%s ff00ff00ff00"\n' % (
        BEGIN_PREFIX, CANONICAL_NAME, END_PREFIX)
    suite.record(GC, "bait-string-literal", problem_if(
        audit(bait_literal),
        "a marker inside a string literal was treated as a region",
    ))

    with open(GENERATOR, encoding="utf-8") as handle:
        generator_source = handle.read()
    suite.record(GC, "bait-generator-itself", problem_if(
        mod.audit_text("amalgamate.py", generator_source, SYNTH_SOURCES),
        "the generator's own docstring markers were treated as regions",
    ), detail=["the live proof: amalgamate.py documents both markers"])

    # 16. A decorator must travel WITH its function. ast puts a decorated
    #     function's lineno on the `def`, so a naive slice would drop
    #     @staticmethod and turn _result into an instance method that eats the
    #     class as its first argument -- a server that raises on its first reply.
    decorated = mod.load_blocks_text(
        "synthetic_source.py",
        "@staticmethod\ndef _result(msg_id, payload):\n    return payload\n",
    )
    suite.record(GC, "decorator-travels", problem_if(
        decorated.get("_result", "").splitlines()[:1] != ["@staticmethod"],
        "the decorator was dropped from the block: %r" % decorated.get("_result"),
    ))

    # 17. A region inside a class body. The BEGIN marker's own column is the
    #     only thing that places it, and that is what makes the _result/_error
    #     methods -- byte-identical in fourteen servers -- reachable at all.
    nested_body = "".join(
        "    %s" % line if line.strip() else line
        for line in SYNTH_BODY.splitlines(keepends=True)
    )
    nested_host = (
        "class Server:\n"
        '    """doc"""\n'
        "    %s %s :: _helper\n" % (BEGIN_PREFIX, CANONICAL_NAME)
        + nested_body
        + "    %s %s\n" % (END_PREFIX, mod.body_hash(nested_body))
    )
    regions = audit(nested_host)
    suite.record(GC, "indented-region", problem_if(
        len(regions) != 1 or regions[0].state != "ok"
        or regions[0].indent != "    ",
        "an indented region should read ok at a 4-space indent, got %s"
        % [(r.state, r.indent) for r in regions],
    ))

    # THE CLASS-MEMBER WALK the hand-copy census runs on (the generator's
    # `bound_names`, through `hand_copies_text`), both directions. The
    # generator's `load_blocks_text` visits `tree.body` only, so a census built
    # on it is blind in exactly the place the fleet's shared METHODS live: it
    # would report no hand copy of `_result` or `_error` in any server, ever,
    # and read green while saying so.
    #
    # Case 17's fixture is reused deliberately, because the two texts differ by
    # two lines and nothing else. `nested_host` is the name held INSIDE an
    # indented region -- what a server that took the marker looks like; strip
    # the markers and the identical class body is a HAND COPY. The census must
    # answer differently on each, or it is reporting "there is a class here"
    # rather than "this name is held by hand".
    def census(text):
        return set(mod.hand_copies_text("synthetic.py", text, SYNTH_SOURCES))

    hand_held = 'class Server:\n    """doc"""\n%s' % nested_body
    seen, silent = census(hand_held), census(nested_host)
    problems = problem_if(
        seen != set(SYNTH_BLOCKS),
        "a canonical name held as a CLASS MEMBER outside every region was not "
        "reported: %s" % sorted(seen),
    )
    problems += problem_if(
        silent,
        "the same name inside an INDENTED generated region was reported as a "
        "hand copy: %s" % sorted(silent),
    )
    suite.record(GC, "class-member-hand-copy-is-seen", problems,
                 detail=["held by hand: %s; generated: %s"
                         % (sorted(seen), sorted(silent))])

    # 18. THE tree.body FILTER, which decides what may be a block at all. It
    #     used to admit definitions only; it now also admits a module-level
    #     CONSTANT, and every other assignment shape is still turned down --
    #     each for its own reason, spelled out on `assign_name`. Both arms are
    #     here because neither proves anything alone: a loader that extracted
    #     nothing would satisfy the refusals, and one that extracted every
    #     statement would satisfy the acceptance.
    #
    #     `GOOD += 1` is placed AFTER `GOOD = 1` on purpose. If the augmented
    #     form were ever admitted it would overwrite the good entry under the
    #     same key, so the name list would still read right and only the BODY
    #     check below would see it -- which is how this shape would slip in.
    assign_source = (
        "GOOD = 1\n"
        "MULTI = ALSO = 2\n"
        "TUPLE_A, TUPLE_B = 3, 4\n"
        "[LIST_A, LIST_B] = 5, 6\n"
        "ANNOTATED: int = 7\n"
        "DECLARED: int\n"
        "GOOD += 1\n"
        "holder.attr = 8\n"
        "holder[0] = 9\n"
        "def _fn():\n"
        "    pass\n"
    )
    extracted = mod.load_blocks_text("synthetic_source.py", assign_source)
    problems = problem_if(
        sorted(extracted) != ["GOOD", "_fn"],
        "the loader extracted %s; exactly the single-Name constant and the "
        "def may be blocks" % sorted(extracted),
    )
    problems += problem_if(
        extracted.get("GOOD") != "GOOD = 1\n",
        "a constant block must be its own statement and nothing else, got %r"
        % extracted.get("GOOD"),
    )
    suite.record(GC, "single-name-assign-only", problems,
                 detail=["extracted: %s" % ", ".join(sorted(extracted))])

    # 19. THE FREE-NAME REFUSAL -- the check that makes a region refuse rather
    #     than emit a server that dies at startup. On the live tree it fires
    #     only incidentally, because every region in it passes, so a silent
    #     failure here would leave the whole suite green. Exercised directly,
    #     and on a CONSTANT: that is the block shape most likely to need a host
    #     import, since a compiled pattern reads `re` where a def usually reads
    #     nothing but its own arguments.
    #
    #     Both arms again, and the second is the load-bearing one: "refused"
    #     alone is satisfied by a generator that refuses every region, which is
    #     indistinguishable from a working check until somebody adds a block.
    pattern_body = '_PATTERN = re.compile(r"^x")\n'
    needs_re = {CANONICAL_NAME: {"_PATTERN": pattern_body}}

    def pattern_host(imports):
        return (
            '"""A synthetic target."""\n' + imports
            + "%s %s :: _PATTERN\n" % (BEGIN_PREFIX, CANONICAL_NAME)
            + "%s\n" % END_PREFIX
        )

    refused = expect_exit(lambda: mod.audit_text(
        "synthetic.py", pattern_host(""), needs_re))
    problems = problem_if(
        not refused,
        "a host that never imports `re` was served a block that reads it -- "
        "that server dies at its first import",
    )
    # The missing symbol is reported as a LIST, so the quotes are part of the
    # match: a bare "re" would also be satisfied by the word "region".
    problems += problem_if(
        refused and "['re']" not in str(refused),
        "the refusal must name the missing symbol; got %r" % refused,
    )
    accepted = mod.audit_text("synthetic.py", pattern_host("import re\n"),
                              needs_re)
    problems += problem_if(
        len(accepted) != 1,
        "the same region was refused to a host that DOES import `re`, so the "
        "refusal above is not about the free name",
    )
    problems += problem_if(
        accepted and accepted[0].wanted != pattern_body,
        "the importing host was served %r"
        % (accepted[0].wanted if accepted else None),
    )
    suite.record(GC, "free-name-refusal", problems, detail=[str(refused)])

    # 15. A file with no region at all is legitimate -- servers convert one at
    #     a time, so this must be silence, not an error.
    suite.record(GC, "no-region-is-fine", problem_if(
        audit('"""Nothing generated here."""\nX = 1\n'),
        "a target with no markers reported a region",
    ))


def group_blocks(suite, blocks, lsp, paging, logmod):
    """Unit-test the canonical modules themselves -- imported, not read as text."""
    falsy = ["", "false", "0", "no", "off", "none", "FALSE", "  Off  "]
    wrong = [v for v in falsy if blocks._bool_param(v) is not False]
    suite.record(GE, "bool-falsy-strings", problem_if(
        wrong, "these should read False: %s" % wrong,
    ))

    # The blacklist semantics are deliberate and load-bearing: an unrecognised
    # string reads True. mcp-webfetch's allow-list variant is the opposite, which
    # is exactly why it never asks for this block.
    truthy = ["1", "true", "yes", "on", "y", "enabled", "2", "anything"]
    wrong = [v for v in truthy if blocks._bool_param(v) is not True]
    suite.record(GE, "bool-unrecognised-is-true", problem_if(
        wrong, "these should read True under blacklist semantics: %s" % wrong,
    ))

    problems = []
    if blocks._bool_param(True) is not True or blocks._bool_param(False) is not False:
        problems.append("a real bool must pass through unchanged")
    if blocks._bool_param(None) is not False:
        problems.append("None must fall back to the default (False)")
    if blocks._bool_param(None, True) is not True:
        problems.append("None must fall back to an explicit True default")
    suite.record(GE, "bool-passthrough-and-default", problems)

    problems = []
    for value, want in (("42", 42), (7, 7), ("-3", -3), (3.9, 3)):
        got = blocks._int_param(value, 99)
        if got != want:
            problems.append("_int_param(%r) gave %r, wanted %r" % (value, got, want))
    suite.record(GE, "int-valid", problems)

    # The infinities are the reason this case is guarded rather than a bare call.
    # `json.loads` turns both `1e999` and the bare `Infinity` token into a float
    # infinity, and `int()` on one raises OverflowError -- neither a TypeError
    # nor a ValueError, so it escaped the fallback and surfaced as an opaque
    # internal error. The case name promises "never raises"; an escape has to be
    # a recorded failure naming the exception, not a traceback that takes the
    # whole suite down before the remaining values are tried.
    problems = []
    for value in ("abc", None, "", [], {}, "1.5",
                  float("inf"), float("-inf"), float("nan")):
        try:
            got = blocks._int_param(value, 99)
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_int_param(%r) RAISED %s: %s"
                            % (value, type(exc).__name__, exc))
            continue
        if got != 99:
            problems.append("_int_param(%r) gave %r instead of the fallback" % (value, got))
    suite.record(GE, "int-falls-back-never-raises", problems)

    # Row accounting moved OUT of the JSON source too: the line it renders is a
    # sentence for a reader on the last line of a text payload, not a field in
    # an envelope. Same shape of claim as the framing one below, and the same
    # reason to state it where somebody would look for the function.
    suite.record(GE, "json-source-drops-row-accounting", problem_if(
        hasattr(blocks, "_rows_note"),
        "%s still defines _rows_note; it belongs to %s"
        % (CANONICAL_NAME, PAGING_CANONICAL_NAME),
    ))

    note = paging._rows_note
    suite.record(GE, "rows-complete-set", problem_if(
        (note(0, 3, 3), note(0, 1, 1)) != ("[3 rows]", "[1 row]"),
        "complete-set wording drifted: %r / %r" % (note(0, 3, 3), note(0, 1, 1)),
    ))
    # The empty set reaches a DIFFERENT branch from the two above -- `shown <= 0`
    # with `start == 0` -- and that branch is hardcoded plural, which is correct
    # for zero and is the one place the pluralisation is not computed. Nothing
    # else in this group enters it.
    suite.record(GE, "rows-empty-set", problem_if(
        note(0, 0, 0) != "[0 rows]",
        "empty-set wording drifted: %r" % note(0, 0, 0),
    ))
    suite.record(GE, "rows-more-remain", problem_if(
        note(0, 20, 347) != "[showing rows 1-20 of 347; offset=20 for more]",
        "resume-hint wording drifted: %r" % note(0, 20, 347),
    ))
    suite.record(GE, "rows-window-ends", problem_if(
        note(4, 2, 6) != "[showing rows 5-6 of 6; no rows left]",
        "end-of-window wording drifted: %r" % note(4, 2, 6),
    ))
    # Spelled out rather than a 1-based range, which would invert past the end.
    suite.record(GE, "rows-past-end-never-inverts", problem_if(
        note(99, 0, 6) != "[no rows at offset 99 of 6]",
        "offset-past-end wording drifted: %r" % note(99, 0, 6),
    ))
    inexact = note(0, 20, 347, exact=False)
    suite.record(GE, "rows-lower-bound", problem_if(
        "347+ (scan stopped at the ceiling; true total unknown)" not in inexact
        or "offset=20 for more" not in inexact,
        "lower-bound wording drifted: %r" % inexact,
    ))

    # `_offset` is the READ side of the line `_rows_note` prints, which is why
    # the two share a source. Ordinary values first.
    offset = paging._offset
    problems = []
    for args, want in (({}, 0), ({"offset": 7}, 7), ({"offset": "12"}, 12),
                       ({"offset": 0}, 0), ({"offset": 3.9}, 3)):
        got = offset(args)
        if got != want:
            problems.append("_offset(%r) gave %r, wanted %r" % (args, got, want))
    suite.record(GE, "offset-default-and-valid", problems)

    # The 0 floor is the entire reason this is not a bare int(): a negative
    # offset indexes a list from its END, so an unfloored -5 answers "before the
    # first item" with the LAST five -- a wrong answer shaped like a right one.
    problems = []
    for value in (-1, -5, "-5", -0.5, -99999):
        got = offset({"offset": value})
        if got != 0:
            problems.append("_offset(offset=%r) gave %r, not the 0 floor"
                            % (value, got))
    suite.record(GE, "offset-floors-negative", problems)

    # Junk falls back rather than raising: this value comes straight off the
    # wire, and a handler that dies on a typo is worse than one that starts at
    # the beginning. The infinities are junk of the one kind that does not look
    # like a typo -- `1e999` and the bare `Infinity` token both reach this as a
    # float, and `int()` on one raises OverflowError, which is neither of the two
    # this used to catch. It is the same escape `_int_param` carried, in the
    # sibling block, and both are read straight off the wire.
    problems = []
    for value in ("abc", None, "", [], {}, "1.5", " ", True,
                  float("inf"), float("-inf"), float("nan")):
        try:
            got = offset({"offset": value})
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_offset(offset=%r) RAISED %s: %s"
                            % (value, type(exc).__name__, exc))
            continue
        # True is an int in Python and legitimately reads as 1; everything else
        # here is junk and must read 0.
        want = 1 if value is True else 0
        if got != want:
            problems.append("_offset(offset=%r) gave %r, wanted %r"
                            % (value, got, want))
    suite.record(GE, "offset-junk-never-raises", problems)

    # The two halves closing the loop, on the actual printed text rather than on
    # a number typed twice: take the hint `_rows_note` emits, parse it back out
    # of the line, and feed it to `_offset`. A drift in either half fails here,
    # and the guard below stops the case going quiet if the hint disappears.
    line = note(0, 20, 347)
    problems = []
    if "offset=" not in line:
        problems.append("the page line emits no offset= hint, so this case "
                        "parsed nothing: %r" % line)
    else:
        hinted = line.split("offset=", 1)[1].split(" ", 1)[0]
        back = offset({"offset": hinted})
        if back != 20:
            problems.append("the hint %r read back as %r, not 20" % (hinted, back))
    suite.record(GE, "offset-round-trips-the-page-line", problems,
                 detail=[line])

    # LSP framing moved OUT of the JSON source: it is a different protocol, and
    # the region markers in the four LSP servers now name `_mcp_lsp.py`. If it
    # came back here the two sources would collide, group B would see it -- but
    # this says so at the point a reader is looking for the function.
    suite.record(GE, "json-source-drops-framing", problem_if(
        hasattr(blocks, "encode_lsp_message"),
        "%s still defines encode_lsp_message; it belongs to %s"
        % (CANONICAL_NAME, LSP_CANONICAL_NAME),
    ))

    framed = lsp.encode_lsp_message({"jsonrpc": "2.0", "id": 1})
    header, _, body = framed.partition(b"\r\n\r\n")
    problems = []
    if not header.startswith(b"Content-Length: "):
        problems.append("missing Content-Length header: %r" % header)
    else:
        declared = int(header.split(b": ", 1)[1])
        if declared != len(body):
            problems.append(
                "Content-Length says %d, body is %d BYTES long" % (declared, len(body))
            )
    if json.loads(body.decode("utf-8")) != {"jsonrpc": "2.0", "id": 1}:
        problems.append("body does not round-trip through json.loads")
    suite.record(GE, "lsp-framing-counts-bytes", problems)

    # A non-ASCII payload, and the honest finding: this CANNOT discriminate a
    # byte count from a character count, because `json.dumps` escapes to \\uXXXX
    # by default, so the wire form is ASCII and the two numbers coincide. Saying
    # that out loud is the case's job. The block's `len(encoded)` is already
    # byte-based and only starts to MATTER the day somebody passes
    # ensure_ascii=False -- at which point a character count would under-declare
    # and the peer would read a short frame and desynchronise the stream rather
    # than report anything. Pinning the escaping here is what stops that change
    # landing quietly; relax this case only together with the length arithmetic.
    payload = {"text": "hello — 日本語"}
    framed = lsp.encode_lsp_message(payload)
    header, _, body = framed.partition(b"\r\n\r\n")
    declared = int(header.split(b": ", 1)[1])
    as_text = body.decode("utf-8")
    problems = []
    if declared != len(body):
        problems.append("Content-Length says %d, body is %d bytes"
                        % (declared, len(body)))
    try:
        body.decode("ascii")
    except UnicodeDecodeError:
        problems.append(
            "the wire form is no longer ASCII-escaped, so Content-Length now "
            "has to count ENCODED BYTES -- re-read the block's arithmetic "
            "before relaxing this case"
        )
    if json.loads(as_text) != payload:
        problems.append("a non-ASCII payload does not round-trip")
    suite.record(GE, "lsp-framing-non-ascii-payload", problems,
                 detail=["%d bytes for %d characters on the wire"
                         % (len(body), len(as_text))])

    # One header field, CRLFCRLF-terminated, and BYTES out -- a str return would
    # satisfy every assertion above and then fail at the first writer.write().
    framed = lsp.encode_lsp_message({"id": 2})
    problems = []
    if not isinstance(framed, bytes):
        problems.append("encode_lsp_message returned %s" % type(framed).__name__)
    if framed.count(b"\r\n\r\n") != 1:
        problems.append("expected exactly one header terminator: %r" % framed)
    head = framed.split(b"\r\n\r\n", 1)[0]
    if b"\r\n" in head:
        problems.append("the header carries more than one field: %r" % head)
    suite.record(GE, "lsp-framing-header-shape", problems)

    # The URI pair is the wire's other half, and unlike the framing above it did
    # not arrive as a straight lift. Measured across the four LSP hosts before
    # it was shared: FOUR copies of `uri_to_path` with THREE distinct bodies,
    # against four of `path_to_uri` with one real one. So the ENCODE side had
    # agreed fleet-wide while the DECODE side had not, and the round trip is the
    # case that would have caught it -- `as_uri()` percent-encodes in every copy
    # there has ever been, and only a decoding `uri_to_path` is its inverse.
    # Three of the four stripped the `file://` prefix and handed back a path
    # still carrying `%20`.
    #
    # Every character below is one `as_uri()` escapes, and `#` is the
    # load-bearing one: left raw it is a FRAGMENT delimiter, so `urlparse` would
    # truncate the path there before any decoding ran. The ENCODE half is
    # asserted too, because a pair that neither encodes nor decodes round-trips
    # perfectly and is still wrong -- that is precisely the shape the three
    # predecessors were in with each other.
    hairy = "/tmp/a dir/weird #1 & 2/ünïcode.c"
    encoded = lsp.path_to_uri(hairy)
    back = lsp.uri_to_path(encoded)
    problems = problem_if(
        "%20" not in encoded or "%23" not in encoded,
        "path_to_uri did not percent-encode, so the round trip below would "
        "pass on a pair that does NEITHER: %r" % encoded,
    )
    problems += problem_if(
        back != hairy,
        "the pair is not an inverse: %r came back as %r" % (hairy, back),
    )
    suite.record(GE, "lsp-uri-round-trips-reserved-chars", problems,
                 detail=[encoded])

    # SECURITY, and the reason this pair was worth one writer rather than four.
    # Decoding is what turns an LSP-returned `%2e%2e` back into a `..` the
    # downstream realpath / `_path_within_root` containment checks can see and
    # collapse; the prefix-strip left it encoded, where those same checks read
    # it as an ordinary directory name and let it through. Both halves are
    # asserted -- what must come back AND what must not survive -- because "not
    # the escaped form" alone is satisfied by any mangling at all.
    decoded = lsp.uri_to_path("file:///root/%2e%2e/escape")
    problems = problem_if(
        decoded != "/root/../escape",
        "encoded traversal did not decode: %r" % decoded,
    )
    problems += problem_if(
        "%2e" in decoded.lower(),
        "a percent escape survived into the path handed to realpath: %r"
        % decoded,
    )
    suite.record(GE, "lsp-uri-decodes-encoded-traversal", problems,
                 detail=[decoded])

    # A scheme that is not `file` comes back UNCHANGED rather than decoded: a
    # language server answers with `untitled:` for an unsaved buffer and with
    # its own scheme for a synthesised source, and none of those is a path. The
    # prefix-strip already had this behaviour, and the hardening had to keep it
    # -- reaching `url2pathname` unconditionally would hand back a path-shaped
    # string for a thing that is not a file, which is worse than not answering.
    problems = []
    for uri in ("untitled:Untitled-1", "git:/x/y.c?ref=HEAD", "jdt://contents"):
        got = lsp.uri_to_path(uri)
        if got != uri:
            problems.append("%r came back as %r" % (uri, got))
    suite.record(GE, "lsp-uri-passes-a-foreign-scheme-through", problems)

    # THE CLIENT HALF. `_request`, `_notify`, `_abs_uri` and `_abs_path` are
    # METHODS at their destination -- emitted at an INDENTED marker inside four
    # differently-named client classes -- so they are driven here with a stub
    # receiver rather than a real backend. That is the honest shape of the
    # contract and not a shortcut: the blocks promise nothing about `self`
    # beyond the four attributes they touch, and a fixture that stood up clangd
    # would be measuring clangd.
    #
    # Group A already proves the four hosts carry the same body; what it cannot
    # prove is that the body is right. Three of the properties below are exactly
    # the kind a drift gate RATIFIES rather than checks -- a notification that
    # is one only because it has no `id`, a timeout that answers instead of
    # raising, and a pending entry removed on the way out -- and each would read
    # green in all four servers forever if it were wrong.

    class _Receiver:
        """Exactly the attributes `_request` and `_notify` reach for."""

        def __init__(self, reply=None):
            self._next_id = 1
            self._pending = {}
            self._reply = reply
            self.sent = []

        async def _send(self, body):
            self.sent.append(body)
            # What the real reader loop does when the answer comes back.
            if self._reply is not None and "id" in body:
                self._pending[body["id"]].set_result(self._reply)

    recv = _Receiver(reply={"result": {"symbols": []}})
    params = {"textDocument": {"uri": "file:///x.c"}}
    answer = asyncio.run(lsp._request(recv, "textDocument/documentSymbol",
                                      params))
    problems = problem_if(
        answer != {"result": {"symbols": []}},
        "_request did not hand back the future's value: %r" % (answer,),
    )
    problems += problem_if(
        len(recv.sent) != 1,
        "_request sent %d message(s), expected exactly one" % len(recv.sent),
    )
    if recv.sent:
        problems += problem_if(
            recv.sent[0] != {"jsonrpc": "2.0", "id": 1,
                             "method": "textDocument/documentSymbol",
                             "params": params},
            "the request envelope drifted: %r" % (recv.sent[0],),
        )
    # The id must be CONSUMED, and the waiter registered under the id that went
    # out -- the correlation is the whole job, and an envelope carrying an id
    # the table was never keyed on is a reply nothing can match.
    problems += problem_if(
        recv._next_id != 2,
        "the id counter read %r after one request, expected 2" % recv._next_id,
    )
    problems += problem_if(
        list(recv._pending) != [1],
        "the waiter was not registered under the id that was sent: %r"
        % sorted(recv._pending),
    )
    suite.record(GE, "lsp-request-allocates-correlates-and-answers", problems,
                 detail=[str(recv.sent)])

    # The timeout arm, and the two things it owes. It ANSWERS rather than
    # raising -- a slow backend costs one call, not the handler above it -- and
    # it takes the future back OUT of `_pending`. The second half is the one a
    # drift gate would ratify forever: that table has no sweeper, so an entry
    # left behind is a future nothing will ever resolve or collect.
    #
    # Deterministic despite the clock: nothing in this fixture can resolve the
    # future, so the wait cannot finish early on any machine.
    recv = _Receiver()
    answer = None
    problems = []
    try:
        answer = asyncio.run(lsp._request(recv, "textDocument/hover", {},
                                          timeout=0.05))
    except Exception as exc:                       # noqa: BLE001 -- the point
        problems.append("_request RAISED %s instead of answering on timeout: %s"
                        % (type(exc).__name__, exc))
    if answer is not None:
        problems += problem_if(
            answer != {"error": {"message":
                                 "timeout waiting for textDocument/hover"}},
            "the timeout reply drifted: %r" % (answer,),
        )
    problems += problem_if(
        recv._pending,
        "the timed-out future was left in _pending, where nothing sweeps it: "
        "%r" % sorted(recv._pending),
    )
    suite.record(GE, "lsp-request-timeout-answers-and-unregisters", problems,
                 detail=[repr(answer)])

    # The CANCEL arm (R-0006 phase 2). A cancelled MCP request cancels the task
    # awaiting `_request`, and the block owes three things: the waiter comes
    # back OUT of `_pending` (before this arm only the timeout took it out, so
    # every cancel left a future behind), the language server is told with
    # `$/cancelRequest` naming the LSP id that went out -- a notification, so
    # no id of its own -- and the CancelledError is RE-RAISED, never turned into
    # an answer. Parked on a future nothing resolves, so the cancel always lands
    # inside the wait, on every machine.
    async def _cancel_inflight(receiver):
        task = asyncio.ensure_future(lsp._request(
            receiver, "textDocument/references", {"x": 1}, timeout=30.0))
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled", None
        except Exception as exc:                   # noqa: BLE001 -- the point
            return "raised", exc
        return "answered", task.result()

    recv = _Receiver()
    recv._next_id = 7
    outcome, value = asyncio.run(_cancel_inflight(recv))
    problems = problem_if(
        outcome != "cancelled",
        "_request %s instead of re-raising CancelledError: %r" % (outcome, value),
    )
    problems += problem_if(
        recv._pending,
        "the cancelled waiter was left in _pending: %r" % sorted(recv._pending),
    )
    problems += problem_if(
        len(recv.sent) != 2,
        "_request sent %d message(s) on a cancel, expected the request and "
        "one $/cancelRequest" % len(recv.sent),
    )
    if len(recv.sent) >= 2:
        problems += problem_if(
            recv.sent[1] != {"jsonrpc": "2.0", "method": "$/cancelRequest",
                             "params": {"id": 7}},
            "the cancel notice drifted or named the wrong id: %r" % (recv.sent[1],),
        )
    suite.record(GE, "lsp-request-cancel-unregisters-and-notifies", problems,
                 detail=[str(recv.sent)])

    # Best-effort means best-effort: a transport that fails on the notice (the
    # backend died, the pipe is closed) must not turn the cancel into that
    # failure, and must not stop the waiter from being unregistered.
    class _BrokenNotice(_Receiver):
        async def _send(self, body):
            self.sent.append(body)
            if "id" not in body:
                raise BrokenPipeError("the backend went away")

    recv = _BrokenNotice()
    outcome, value = asyncio.run(_cancel_inflight(recv))
    problems = problem_if(
        outcome != "cancelled",
        "a failing $/cancelRequest surfaced as %s instead of the "
        "CancelledError: %r" % (outcome, value),
    )
    problems += problem_if(
        recv._pending,
        "the cancelled waiter was left in _pending: %r" % sorted(recv._pending),
    )
    suite.record(GE, "lsp-request-cancel-notice-is-best-effort", problems,
                 detail=[str(recv.sent)])

    # A notification is a notification BECAUSE it carries no `id`. Give it one
    # and the backend replies to a request nothing is waiting for: the answer
    # reaches the reader loop, finds no `_pending` entry and is dropped in
    # silence. That is the whole of the difference from `_request`, and it is a
    # key's ABSENCE -- which no amount of "four files agree" can establish.
    recv = _Receiver()
    asyncio.run(lsp._notify(recv, "textDocument/didOpen", {"x": 1}))
    problems = problem_if(
        len(recv.sent) != 1,
        "_notify sent %d message(s), expected exactly one" % len(recv.sent),
    )
    if recv.sent:
        problems += problem_if(
            "id" in recv.sent[0],
            "a notification carried an id, so the backend will reply to it: %r"
            % (recv.sent[0],),
        )
        problems += problem_if(
            recv.sent[0] != {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                             "params": {"x": 1}},
            "the notification envelope drifted: %r" % (recv.sent[0],),
        )
    problems += problem_if(
        recv._next_id != 1 or recv._pending,
        "_notify consumed an id or registered a waiter: next_id=%r pending=%r"
        % (recv._next_id, sorted(recv._pending)),
    )
    suite.record(GE, "lsp-notify-carries-no-id", problems)

    # `_abs_uri` and `_abs_path` are ONE resolution with two endings, so they
    # are driven together: a case exercising either alone would pass on a pair
    # that had stopped agreeing about the path in the middle.
    class _Rooted:
        def __init__(self, root):
            self.project_root = root

    root = os.path.realpath(H.repo_path("Scripts"))
    # A leaf that does not exist, deliberately: `resolve()` is non-strict, and a
    # real file would make this case depend on whether the checkout happens to
    # sit behind a symlink.
    rooted = _Rooted(root)
    joined = lsp._abs_path(rooted, "no_such_translation_unit.c")
    already = lsp._abs_path(rooted,
                            os.path.join(root, "no_such_translation_unit.c"))
    problems = problem_if(
        joined != os.path.join(root, "no_such_translation_unit.c"),
        "a relative path was not resolved against project_root: %r" % joined,
    )
    problems += problem_if(
        already != joined,
        "an already-absolute path was re-rooted: %r against %r"
        % (already, joined),
    )
    problems += problem_if(
        not lsp._abs_uri(rooted, "no_such_translation_unit.c").startswith(
            "file://"),
        "_abs_uri did not answer with a file:// DocumentUri: %r"
        % lsp._abs_uri(rooted, "no_such_translation_unit.c"),
    )
    suite.record(GE, "lsp-abs-resolves-against-project-root", problems,
                 detail=[joined])

    # The tie between the new pair and the one already in this source, and the
    # reason both belong to one domain: `_abs_uri` is the WIRE spelling of
    # exactly what `_abs_path` returns, so `uri_to_path` has to undo it. The
    # reserved characters are in the fixture deliberately -- `as_uri()`
    # percent-encodes them, and only a DECODING `uri_to_path` arrives back at
    # the same string, which is the defect that pair was lifted to fix.
    #
    # The `..` in the fixture is what stops this case being satisfied by
    # `_abs_uri` quietly becoming `path_to_uri` with a root bolted on. That one
    # uses `absolute()`, which leaves `..` standing, while these two use
    # `resolve()`, which collapses it -- and on a path without one the two calls
    # are indistinguishable, so a plain filename here would ratify the
    # convergence instead of catching it. The difference is not cosmetic: an
    # uncollapsed `..` is exactly what `uri_to_path`'s own SECURITY note is
    # about, a traversal reaching `_path_within_root` in a form that check reads
    # as an ordinary directory name.
    rooted = _Rooted(os.path.join(root, "a dir", "weird #1 & 2"))
    wire = lsp._abs_uri(rooted, "sub/../ünïcode.c")
    local = lsp._abs_path(rooted, "sub/../ünïcode.c")
    problems = problem_if(
        "%20" not in wire or "%23" not in wire,
        "_abs_uri did not percent-encode, so the round trip below would pass "
        "on a pair that does NEITHER: %r" % wire,
    )
    problems += problem_if(
        "/sub/.." in wire or "/sub/.." in local,
        "the `..` was not collapsed, so this pair is running absolute() where "
        "it promises resolve(): %r / %r" % (wire, local),
    )
    problems += problem_if(
        lsp.uri_to_path(wire) != local,
        "_abs_uri is not the wire spelling of _abs_path: %r decodes to %r, "
        "not %r" % (wire, lsp.uri_to_path(wire), local),
    )
    suite.record(GE, "lsp-abs-uri-is-the-wire-spelling-of-abs-path", problems,
                 detail=[wire])

    # _result/_error are staticmethod DESCRIPTORS here, not callables -- they are
    # methods only at their destination. Reaching through __func__ is the point,
    # not a workaround: if the decorator were ever tidied away this would break,
    # and so would every server's first reply.
    result = getattr(blocks._result, "__func__", blocks._result)
    error = getattr(blocks._error, "__func__", blocks._error)
    suite.record(GE, "result-envelope", problem_if(
        result(7, {"a": 2}) != {"jsonrpc": "2.0", "id": 7, "result": {"a": 2}},
        "result envelope drifted: %r" % (result(7, {"a": 2}),),
    ))
    suite.record(GE, "error-envelope", problem_if(
        error(7, -32601, "nope") != {
            "jsonrpc": "2.0", "id": 7,
            "error": {"code": -32601, "message": "nope"},
        },
        "error envelope drifted: %r" % (error(7, -32601, "nope"),),
    ))

    window = blocks._json_error_window
    suite.record(GE, "window-short-no-ellipsis", problem_if(
        window("abc", 1) != "'abc'",
        "a text shorter than the radius should carry no ellipsis: %r"
        % window("abc", 1),
    ))
    long_window = window("x" * 500, 250)
    suite.record(GE, "window-long-both-ellipses", problem_if(
        not (long_window.startswith("...") and long_window.endswith("...")),
        "a mid-text window needs an ellipsis on both sides: %r" % long_window,
    ))
    # The whole reason repr() is in there: a correctly escaped quote must LOOK
    # different from a bare one, which a raw slice renders identically.
    both = window(r'said \"ok\" then rung "1 CB/token" here', 26)
    suite.record(GE, "window-repr-discriminates", problem_if(
        '\\\\"' not in both or '"1 CB/token"' not in both,
        "repr must show the escaped quote as backslash-quote and the broken one "
        "bare; got %r" % both,
    ))
    suite.record(GE, "window-pos-at-end", problem_if(
        window("abcdef", 6) != "'abcdef'",
        "a pos at end-of-text (truncated JSON) must degrade to a left window: %r"
        % window("abcdef", 6),
    ))

    # `_ensure_dict` is the window's one in-region caller, and these three cases
    # are where the co-listing stops being a claim about the generator and turns
    # into a claim about behaviour: the third message below can only be built if
    # the block really does reach `_json_error_window` at run time.
    ensure = blocks._ensure_dict
    problems = []
    for value, want in ((None, {}), ({"a": 1}, {"a": 1}), ({}, {}),
                        ('{"a": 1}', {"a": 1}), ("{}", {})):
        try:
            got = ensure(value)
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_ensure_dict(%r) RAISED %s: %s"
                            % (value, type(exc).__name__, exc))
            continue
        if got != want:
            problems.append("_ensure_dict(%r) gave %r, wanted %r"
                            % (value, got, want))
    suite.record(GE, "ensure-dict-accepts-none-dict-and-json", problems)

    # Everything that is not an OBJECT must raise, and raise a ValueError that a
    # handler can turn into a reply. The JSON array and the bare JSON scalar are
    # the two that a plain `json.loads` would wave through, which is the whole
    # reason the isinstance check sits after the decode rather than instead of it.
    problems = []
    for value in ("[1, 2]", "5", '"text"', "null", 5, [], ("a",), object()):
        try:
            got = ensure(value)
        except ValueError:
            continue
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_ensure_dict(%r) raised %s, not ValueError"
                            % (value, type(exc).__name__))
            continue
        problems.append("_ensure_dict(%r) returned %r instead of raising"
                        % (value, got))
    suite.record(GE, "ensure-dict-rejects-non-objects", problems)

    # The message carries the two things a caller cannot reconstruct: WHICH
    # parameter was wrong -- hosts pass their own field name, the default is
    # "params" -- and WHERE the JSON broke. The window is reachable at all only
    # because `value` still holds the caller's string in the except branch:
    # `value = json.loads(value)` binds nothing when it raises.
    problems = []
    try:
        ensure("{broken", "filter")
    except ValueError as exc:
        message = str(exc)
        if "'filter'" not in message:
            problems.append("the message does not name the parameter: %r" % message)
        if "Near the failure:" not in message:
            problems.append("the message carries no window: %r" % message)
        if repr("{broken") not in message:
            problems.append("the window did not repr the caller's own text, so "
                            "`value` was rebound before it was read: %r" % message)
    else:
        problems.append("a non-JSON string did not raise")
    suite.record(GE, "ensure-dict-message-names-field-and-window", problems)

    # R-0067 + R-0068: the strict pair every host parses and emits a peer's
    # frame through. Each refusal must be a json.JSONDecodeError and not merely
    # a ValueError, because that is the ONE exception every host's frame loop
    # and `_ensure_dict` catch -- anything wider would escape as a crash rather
    # than reach the -32700 answer an unparseable line already gets.
    missing = [name for name in STRICT_NAMES if not hasattr(blocks, name)]
    absent = ["%s defines no %s" % (CANONICAL_NAME, name) for name in missing]
    strict_loads = getattr(blocks, "_strict_loads", None)
    strict_dumps = getattr(blocks, "_strict_dumps", None)

    problems = list(absent)
    if strict_loads is not None:
        for text in ('{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
                     '[1, -2, 3.5, -0.0, 1e300, true, null, "\\u00e9"]',
                     '{"a": {"b": [1, 2, {"c": "NaN Infinity"}]}}',
                     b'{"id": "bytes in", "n": -12}'):
            try:
                got = strict_loads(text)
            except Exception as exc:                   # noqa: BLE001 -- the point
                problems.append("_strict_loads(%r) RAISED %s: %s"
                                % (text, type(exc).__name__, exc))
                continue
            if got != json.loads(text):
                problems.append("_strict_loads(%r) gave %r, json.loads %r"
                                % (text, got, json.loads(text)))
    suite.record(GE, "strict-loads-accepts-json", problems)

    # The literal is FOUND, not merely refused: `exc.pos` is what a host hands
    # `_json_error_window`, so it has to land on the token that was refused.
    problems = list(absent)
    if strict_loads is not None:
        for token in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            text = '{"id": 1, "note": "a string", "n": %s}' % token
            try:
                got = strict_loads(text)
            except json.JSONDecodeError as exc:
                if not text[exc.pos:].startswith(token):
                    problems.append("%s: refused, but pos %d points at %r"
                                    % (token, exc.pos, text[exc.pos:exc.pos + 12]))
                continue
            except Exception as exc:                   # noqa: BLE001 -- the point
                problems.append("%s: raised %s, not json.JSONDecodeError"
                                % (token, type(exc).__name__))
                continue
            problems.append("%s: parsed to %r instead of being refused" % (token, got))
    suite.record(GE, "strict-loads-refuses-non-finite", problems)

    # The bound is in CHARACTERS, sign included, as `_rt_bounded_int` and
    # `_oauth_bounded_int` count it. 3.11+ carries its own digit limit, so it is
    # lifted for the duration: on 3.9.6 there is none, and a case that passed
    # because the INTERPRETER refused the literal would prove nothing.
    problems = list(absent)
    if strict_loads is not None:
        limit = getattr(blocks, "JSON_INT_LITERAL_LIMIT", None)
        problems += problem_if(limit != 4300, "JSON_INT_LITERAL_LIMIT is %r, not 4300" % (limit,))
        with int_digits_unlimited():
            for literal, ok in (("7" * 4300, True), ("-" + "7" * 4299, True),
                                ("7" * 4301, False), ("-" + "7" * 4300, False),
                                ("7" * 50000, False)):
                text = '{"id": 1, "n": %s}' % literal
                try:
                    got = strict_loads(text)
                except json.JSONDecodeError as exc:
                    if ok:
                        problems.append("a %d-character literal was refused: %s"
                                        % (len(literal), str(exc)[:80]))
                    elif not text[exc.pos:].startswith(literal):
                        problems.append("a %d-character literal: pos %d is not on it"
                                        % (len(literal), exc.pos))
                    continue
                except Exception as exc:               # noqa: BLE001 -- the point
                    problems.append("a %d-character literal raised %s, not "
                                    "json.JSONDecodeError" % (len(literal), type(exc).__name__))
                    continue
                if not ok:
                    problems.append("a %d-character literal parsed instead of being refused"
                                    % len(literal))
                elif got != {"id": 1, "n": int(literal)}:
                    problems.append("a %d-character literal parsed wrong" % len(literal))
    suite.record(GE, "strict-loads-int-literal-bound", problems)

    problems = list(absent)
    if strict_dumps is not None:
        for value in (float("nan"), float("inf"), float("-inf")):
            try:
                got = strict_dumps({"id": 1, "result": {"n": value}})
            except ValueError:
                continue
            except Exception as exc:                   # noqa: BLE001 -- the point
                problems.append("%r raised %s, not ValueError" % (value, type(exc).__name__))
                continue
            problems.append("%r was emitted: %r" % (value, got))
        # Every frame that serialised before must be byte-identical after.
        frame = {"jsonrpc": "2.0", "id": "x", "result": {"text": "é \"q\"", "n": [1, 2.5]}}
        problems += problem_if(strict_dumps(frame) != json.dumps(frame),
                               "a finite frame is not byte-identical to json.dumps: %r"
                               % strict_dumps(frame))
    suite.record(GE, "strict-dumps-refuses-non-finite", problems)

    # The relay rides the same rule: a stringified `params` carrying NaN is
    # refused through the existing ValueError route, field named and window shown.
    problems = []
    try:
        got = ensure('{"x": NaN}', "filter")
    except ValueError as exc:
        message = str(exc)
        if "'filter'" not in message or "Near the failure:" not in message:
            problems.append("refused, but the message lost the field or the window: %r"
                            % message)
    except Exception as exc:                           # noqa: BLE001 -- the point
        problems.append("raised %s, not ValueError" % type(exc).__name__)
    else:
        problems.append("a NaN inside a string params was accepted: %r" % (got,))
    suite.record(GE, "ensure-dict-refuses-non-finite", problems)

    # `_configure_logging` is measured on the one property that has already cost
    # a fleet-wide commit: `c8b74d0`, "every log file was created world-readable
    # in a world-writable directory", had to touch every server because the mode
    # was written down fifteen times. It is written down once now, so it is
    # pinned once -- and pinned against a real file rather than against a reading
    # of the source, because the mode that matters is the one on disk.
    #
    # What this does NOT assert is deliberate. `logging.basicConfig` is a no-op
    # when the root logger already carries a handler, so a case asserting on the
    # level or the handler would pass or fail on the order the fleet runner
    # happens to use. The `os.open` runs first and unconditionally, which is why
    # the descriptor's mode is the honest thing to measure here.
    log_dir = H.repo_path(".claude", "tmp", "test_generated_region")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "mode.log")
    if os.path.exists(log_path):
        os.unlink(log_path)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        logmod._configure_logging(False, log_path)
        mode = stat.S_IMODE(os.stat(log_path).st_mode)
    finally:
        for handler in root.handlers:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    suite.record(GE, "log-file-created-0600", problem_if(
        mode != 0o600,
        "the log file was created %04o, not 0600" % mode,
    ), detail=["path: %s" % log_path, "mode: %04o" % mode])

    # `_log_value` (R-0070, CWE-117): a peer-chosen method, id, tool name or
    # argument key on its way into a log line. Three properties, each pinned on
    # the canonical module once, for every host that carries the region.
    #
    # 1. Nothing that ends or rewrites a line survives: CR, LF, the C0 and C1
    #    controls, DEL, and the Unicode line and paragraph separators all come
    #    out as backslash escapes, because a str is logged as its repr().
    log_value = getattr(logmod, "_log_value", None)
    width = getattr(logmod, "_LOG_VALUE_WIDTH", None)
    shown = getattr(logmod, "_LOG_KEYS_SHOWN", None)
    if log_value is None or width is None or shown is None:
        for cid in ("log-value-escapes-line-breaks", "log-value-bounded-and-cut",
                    "log-value-non-str-structure"):
            suite.record(GE, cid, ["Scripts/_mcp_logging.py defines no "
                                   "_log_value / _LOG_VALUE_WIDTH / _LOG_KEYS_SHOWN"])
        return
    forged = ("tools/call\r\n2026-10-07 mcp-git INFO forged"
              "\x00\x1b[2J\x7f\x85" + chr(0x2028) + chr(0x2029))
    out = log_value(forged)
    bad = sorted({"U+%04X" % ord(ch) for ch in out
                  if ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f
                  or ord(ch) in (0x2028, 0x2029)})
    problems = problem_if(bad, "control characters reached the line: %s" % bad)
    problems += problem_if(out != repr(forged),
                           "a short value is not logged as its repr(): %r" % out)
    suite.record(GE, "log-value-escapes-line-breaks", problems,
                 detail=["in : %r" % forged, "out: %s" % out])

    # 2. Bounded, and every cut marked. A long str is cut BEFORE repr() and ends
    #    in "..."; a str short enough to keep whole whose repr() still overruns
    #    the 4 x width cap (every char an escape) is capped and ALSO ends in
    #    "..."; a value at exactly the width is whole and unmarked.
    problems, detail = [], []
    for what, value, cut in (("long printable", "x" * (width * 10), True),
                             ("escapes overrun the cap", "\x00" * width, True),
                             ("exactly the width", "y" * width, False),
                             ("short", "tools/call", False)):
        got = log_value(value)
        detail.append("%-24s: %d chars, ends %r" % (what, len(got), got[-4:]))
        if len(got) > 4 * width + 3:
            problems.append("%s: %d chars, cap %d" % (what, len(got), 4 * width + 3))
        if got.endswith("...") is not cut:
            problems.append("%s: cut marker %s" % (what, "missing" if cut else "on a whole value"))
        if not cut and got != repr(value):
            problems.append("%s: logged %r, not its repr()" % (what, got[:40]))
    suite.record(GE, "log-value-bounded-and-cut", problems, detail=detail)

    # 3. The non-str structure: an id is an int (a huge one as its bit length,
    #    never 4300 digits), a key list is bounded at _LOG_KEYS_SHOWN items with
    #    a "+N more" tail and one level deep, and anything else -- a dict, a
    #    float, a nested list -- is its TYPE NAME, never its contents.
    problems = []
    for value, want in ((7, "7"), (None, "None"), (True, "True"),
                        (2 ** 100, "int(101 bits)"), ({"k": "v"}, "dict"),
                        (1.5, "float")):
        got = log_value(value)
        if got != want:
            problems.append("_log_value(%r) gave %r, wanted %r" % (value, got, want))
    keys = log_value(["k\n%d" % i for i in range(shown + 4)])
    problems += problem_if(not keys.endswith(", +4 more]"),
                           "a key list over %d items is not bounded: %r" % (shown, keys[-40:]))
    problems += problem_if("\n" in keys, "a key's line break reached the line")
    problems += problem_if(log_value([["a\n"], "b"]) != "[list, 'b']",
                           "a nested list is not logged as its type name: %r"
                           % log_value([["a\n"], "b"]))
    suite.record(GE, "log-value-non-str-structure", problems)


def group_tabs(suite, mod):
    """The tab refusal is PER BLOCK now, so both arms have to be live."""
    sources = mod.load_all_blocks()
    verdicts = {name: mod.block_is_tab_safe(body)
                for blocks in sources.values() for name, body in blocks.items()}
    unsafe = sorted(n for n, ok in verdicts.items() if not ok)
    safe = sorted(n for n, ok in verdicts.items() if ok)

    # Driven by the REAL canonical text, not a synthetic, so the case cannot
    # drift away from the thing it protects. Both arms asserted: a detector that
    # called everything safe would pass a "_rows_note is unsafe" check written
    # the other way round, and one that called everything unsafe would pass a
    # refusal check.
    problems = problem_if(
        unsafe != ["_rows_note"],
        "expected exactly _rows_note to be tab-unsafe, got %s" % unsafe,
    )
    problems += problem_if(
        len(safe) < 2,
        "no tab-SAFE blocks left, so the accept arm proves nothing: %s" % safe,
    )
    suite.record(GF, "tab-safety-real-blocks", problems,
                 detail=["unsafe: %s" % ", ".join(unsafe),
                         "safe: %s" % ", ".join(safe)])

    # The mechanism itself, on shapes the canonical set does not contain -- a
    # backslash continuation and an off-level indent have no live example, and
    # an unexercised branch of a safety check is where the next hole opens.
    cases = [
        ("plain nesting", "def f():\n    if x:\n        return 1\n", True),
        ("bracket join", "def f():\n    return (1 if x\n            else 2)\n", False),
        ("backslash join", "def f():\n    return 1 + \\\n        2\n", False),
        ("off-level indent", "def f():\n      return 1\n", False),
        ("already tabbed", "def f():\n\treturn 1\n", False),
    ]
    problems = ["%s: tab-safe read %r, wanted %r" % (what, mod.block_is_tab_safe(src), want)
                for what, src, want in cases
                if mod.block_is_tab_safe(src) is not want]
    suite.record(GF, "tab-safety-mechanism", problems)

    # The refusal, and the SAME fixture accepting a safe block one line later.
    # Without the second half this case would pass on a generator that refused
    # every block in a tab host, which is the old per-file rule.
    refusal = expect_exit(lambda: mod.audit_text(
        "tabby.py", tab_host("_rows_note"), sources))
    problems = problem_if(not refusal, "a tab host was served _rows_note")
    problems += problem_if(
        refusal and "_rows_note" not in str(refusal),
        "the refusal does not name the block: %r" % refusal,
    )
    accepted = mod.audit_text("tabby.py", tab_host("_offset"), sources)
    problems += problem_if(
        len(accepted) != 1,
        "the same tab fixture refused a tab-SAFE block too, so the refusal "
        "above is the old per-FILE rule, not a per-block one",
    )
    suite.record(GF, "tab-host-refuses-unsafe-by-name", problems,
                 detail=[str(refusal)])

    # Emission: tabs at every structural level, and NOT ONE leading space. A
    # half-converted body would still import and run, and would be invisible in
    # a diff viewer that renders both the same width.
    # Two blocks are driven through PAIRED with the name they read. That is not
    # a fixture convenience: `host_provides` offers a region only the host's
    # module-level IMPORTS, never the names another region defines, so
    # `free_names` refuses either of these in a region of its own and every real
    # host spells them as a two-name marker. Rendering one alone here would
    # assert a shape no server is allowed to use. Dependency first in both, so
    # the fixture emits what a server emits.
    paired = {
        "_max_answer_chars": "DEFAULT_MAX_ANSWER_CHARS, _max_answer_chars",
        WINDOW_RELAY: ", ".join(STRICT_NAMES + (WINDOW_NAME, WINDOW_RELAY)),
    }
    # `_log_value` reads both of its bounds, so every host spells the three on
    # one marker, constants first (R-0070); the two constants are rendered in
    # that same shape rather than alone.
    for name in LOG_VALUE_NAMES:
        paired[name] = ", ".join(LOG_VALUE_NAMES)
    # The strict-JSON blocks call one another, so each is rendered inside the
    # one run every real host spells (R-0067/R-0068); `_ensure_dict` parses
    # through `_strict_loads`, which is why its marker above carries the run too.
    for name in STRICT_NAMES:
        paired[name] = ", ".join(STRICT_NAMES)
    # Every websocket block, inside the one marker shape its hosts spell: the
    # core, plus the block itself when it is a wrapper.
    for name in sources.get(WEBSOCKET_CANONICAL_NAME, {}):
        extra = () if name in WEBSOCKET_CORE else (name,)
        paired[name] = ", ".join(WEBSOCKET_CORE + extra)
    # Every OAuth block, the same way: the core, plus the block itself when it
    # is a wrapper.
    for name in sources.get(OAUTH_CANONICAL_NAME, {}):
        extra = () if name in OAUTH_CORE else (name,)
        paired[name] = ", ".join(OAUTH_CORE + extra)
    problems, emitted = [], 0
    for name in safe:
        source = next(s for s, blocks in sources.items() if name in blocks)
        # A whole source's blocks are rendered below, once per SOURCE: a marker
        # per block carrying the whole source each time would be O(N^2) in a
        # source of a hundred-odd blocks, and a single-block marker is a shape
        # no host may use for it (R2-L2).
        if source in WHOLE_SOURCES:
            continue
        emitted += 1
        regions = mod.audit_text(
            "tabby.py", tab_host(paired.get(name, name), source), sources)
        if len(regions) != 1:
            problems.append("%s: expected one region, got %d" % (name, len(regions)))
            continue
        body = regions[0].wanted
        if any(line.startswith(" ") for line in body.splitlines()):
            problems.append("%s: a line still begins with a SPACE" % name)
        # The join is DECLARED here rather than asked of the generator, which
        # would only prove it agrees with itself: two blank lines between
        # top-level definitions is what `render` owes an unindented region.
        expected = "\n\n\n".join(sources[source][n].rstrip("\n")
                                 for n in regions[0].names) + "\n"
        if untab(body) != expected:
            problems.append("%s: de-tabbing does not round-trip to canonical" % name)
    # Each whole source ONCE, as the one marker every host spells: all of its
    # names in source order. The case then asserts every one of them landed in
    # that single region (L3), so a render that quietly dropped a block cannot
    # pass on the blocks it kept.
    for source in WHOLE_SOURCES:
        names = list(sources.get(source, {}))
        if not names:
            problems.append("%s: no blocks loaded, so nothing was emitted" % source)
            continue
        refused = [n for n in names if not verdicts.get(n)]
        if refused:
            problems.append("%s: a whole source carries tab-unsafe block(s) %s"
                            % (source, refused))
            continue
        regions = mod.audit_text(
            "tabby.py", tab_host(", ".join(names), source), sources)
        if len(regions) != 1:
            problems.append("%s: expected one region, got %d" % (source, len(regions)))
            continue
        emitted += len(names)
        problems += problem_if(
            list(regions[0].names) != names,
            "%s: the region holds %d name(s), the source defines %d; missing %s"
            % (source, len(regions[0].names), len(names),
               [n for n in names if n not in regions[0].names]))
        body = regions[0].wanted
        if any(line.startswith(" ") for line in body.splitlines()):
            problems.append("%s: a line still begins with a SPACE" % source)
        expected = "\n\n\n".join(sources[source][n].rstrip("\n")
                                 for n in names) + "\n"
        if untab(body) != expected:
            problems.append("%s: de-tabbing does not round-trip to canonical" % source)
    suite.record(GF, "tab-emission-is-all-tabs", problems,
                 detail=["%d block(s) emitted into a tab host, %d whole source(s) "
                         "rendered once each" % (emitted, len(WHOLE_SOURCES))])

    # Host style comes from the host's own INDENT tokens. The marker's column
    # cannot answer it -- every fixture above puts the marker at column 0, which
    # is exactly the case that carries no signal.
    styles = {}
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        styles[path.name] = mod.host_indent(path.read_text(encoding="utf-8"))
    tabbed = sorted(n for n, s in styles.items() if s == "tab")
    problems = problem_if(
        tabbed != ["mcp-forge.py", "mcp-webfetch.py"],
        "expected forge and webfetch to be the tab-indented servers, got %s" % tabbed,
    )
    problems += problem_if(
        mod.host_indent(tab_host("_offset")) != "tab",
        "a column-0 marker in a tab file was not detected as a tab host",
    )
    # The declared hosts are outside the glob above. The two search scripts are
    # tabs throughout -- the fleet's tab hosts that are not servers -- and
    # llm-router.py is a space host.
    declared_styles = {name: mod.host_indent(Path(SCRIPTS, name).read_text(encoding="utf-8"))
                       for name in DECLARED_HOSTS}
    problems += problem_if(
        declared_styles != {"search_duckduckgo.py": "tab",
                            "search_github.py": "tab",
                            "llm-router.py": "space"},
        "expected both search scripts to be tab hosts and the router a space host, got %s"
        % declared_styles,
    )
    suite.record(GF, "host-indent-from-tokens", problems,
                 detail=["tab: %s" % ", ".join(tabbed),
                         "declared: %s" % ", ".join("%s=%s" % kv for kv in sorted(declared_styles.items()))])

    # The invariant a careless indent refactor breaks first: a SPACE host must
    # be untouched by any of the above. Not one leading tab may appear in what
    # a space host is served, and what it is served must already be on disk.
    problems = []
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        text = path.read_text(encoding="utf-8")
        if mod.host_indent(text) != "space":
            continue
        for region in mod.audit_text(path.name, text, sources):
            if any(line.startswith("\t") for line in region.wanted.splitlines()):
                problems.append("%s [%s]: a TAB leaked into a space host"
                                % (path.name, region.label))
            if region.body != region.wanted:
                problems.append("%s [%s]: no longer byte-identical on disk"
                                % (path.name, region.label))
    suite.record(GF, "space-hosts-take-no-tabs", problems,
                 detail=["%d space-indented server(s) re-rendered"
                         % sum(1 for s in styles.values() if s == "space")])


# --- group G helpers ----------------------------------------------------------
#
# The census output is READ back here rather than compared against a typed copy
# of its sentences. A typed copy would fail on every rewording of prose nobody
# is gating, and would pass a renderer that printed the right sentence with the
# wrong number in it -- which is the only defect that matters, because the body
# is written into a page a human then quotes.
#
# Parsed with `str` methods and not with `re`, deliberately, and for the reason
# in this file's docstring: the fleet's rule is that SOURCE is read with `ast`,
# and what is read here is neither source nor a place a regex earns anything. A
# block name cannot contain a backtick and a table cell cannot contain a pipe
# (adr 0016, and the census relies on the same fact), so splitting on those two
# characters is exact rather than approximate.

def census_spans(line):
    """The backticked code spans of *line*, in order; [] if they are unbalanced."""
    parts = line.split("`")
    return parts[1::2] if len(parts) % 2 else []


def census_block_spans(line):
    """The spans of *line* that are BLOCK names -- a path is a host, not a block.

    Structural rather than positional: a block name is a Python identifier and
    can never contain a `/`, so the holdout line's leading server path filters
    itself out without the case having to know the sentence's word order.
    """
    return [span for span in census_spans(line) if "/" not in span]


def census_cells(line):
    """The cells of a Markdown table row, or None when *line* is not one."""
    if not (line.startswith("| ") and line.endswith(" |")):
        return None
    return [cell.strip() for cell in line[1:-1].split("|")]


def census_numbers(text):
    """Every integer in *text* that is NOT inside a code span.

    A block name or a path may legitimately carry a digit; a COUNT is what this
    group audits, and every count is written outside the spans.
    """
    out = []
    for line in text.splitlines():
        digits = ""
        for ch in "".join(line.split("`")[0::2]) + " ":
            if ch.isdigit():
                digits += ch
            elif digits:
                out.append(int(digits))
                digits = ""
    return out


def group_census(suite, mod):
    """`--census`: the numbers a page renders, and the three properties it owes.

    The consumer is `docs/measurements.json`, which hands this argv to
    `mcp-wiki`'s `measure`: the body is rendered into a page and a digest of it
    is recorded on the END marker. That makes two ordinary-looking properties
    load-bearing -- the output must be DERIVED (a number nobody can re-derive is
    the defect being removed, not automated) and SORTED (an iteration order that
    merely happens to repeat turns every page carrying the block into a false
    `stale`) -- and one that is a safety property: the mode a documentation tool
    runs unattended must not be able to write.
    """
    sources = mod.load_all_blocks()
    targets = sorted(Path(SCRIPTS).glob(TARGET_GLOB))

    # The oracle: the same walk, done here. Not a second implementation of the
    # RENDERER -- that is the thing under test -- but of the arithmetic, so a
    # census that dropped a count or invented one is visible as a number.
    walked = {path.name: mod.audit(path, sources) for path in targets}
    hosting = [name for name, regions in walked.items() if regions]
    region_count = sum(len(regions) for regions in walked.values())
    instances = sum(len(r.names) for regions in walked.values() for r in regions)
    named = {n for regions in walked.values() for r in regions for n in r.names}
    defined = {n for blocks in sources.values() for n in blocks}
    arity, markers, hosts = {}, {}, {}
    for name, regions in walked.items():
        for region in regions:
            size = len(region.names)
            arity[size] = arity.get(size, 0) + 1
            if size > 1:
                markers[tuple(region.names)] = \
                    markers.get(tuple(region.names), 0) + 1
            for block in region.names:
                hosts.setdefault(block, set()).add("Scripts/%s" % name)

    fleet = mod.census_text("fleet", sources, targets)
    source_text = mod.census_text("sources", sources, targets)

    # ---- every number is derived, and every number has a subject -------------
    subjects = [(len(targets), "servers scanned"),
                (len(hosting), "servers carrying a region"),
                (region_count, "live regions"),
                (instances, "block instances emitted"),
                (len(named), "distinct blocks named on a marker"),
                (len(defined), "blocks the sources define"),
                (len(sources), "canonical sources")]
    legitimate = ({value for value, _subject in subjects} | set(arity)
                  | set(arity.values()) | set(markers.values())
                  | {sum(markers.values())})
    printed = census_numbers(fleet)
    problems = ["the census prints %d, which this walk cannot produce -- a "
                "count nobody can re-derive is the defect being removed, not "
                "the one being automated" % value
                for value in sorted(set(printed) - legitimate)]
    problems += ["the census never prints %d, so the count of %s is missing"
                 % (value, subject) for value, subject in subjects
                 if value not in printed]
    problems += ["%r carries a number and not one word, so the count has no "
                 "subject" % line for line in fleet.splitlines()
                 if any(ch.isdigit() for ch in line)
                 and not any(ch.isalpha() for ch in line)]

    # A TYPED NUMBER THAT IS RIGHT TODAY is invisible to every check above, and
    # it is the precise defect this mechanism exists to remove: the page's three
    # headline counts were typed for as long as they were right, and two of them
    # had to be corrected by hand once they were not. The only way to see it is
    # to hand the census a DIFFERENT input and require the numbers to move, so
    # the census is rendered again over a proper subset of the servers.
    few = targets[:3]
    few_names = [path.name for path in few]
    subset = census_numbers(mod.census_text("fleet", sources, few))
    problems += ["rendered over %d of the %d servers the census still does not "
                 "print %d, the %s it was handed -- a number that does not move "
                 "with its input was typed, not counted"
                 % (len(few), len(targets), value, subject)
                 for value, subject in (
                     (len(few), "server count"),
                     (sum(len(walked[name]) for name in few_names),
                      "live region count"),
                     (sum(len(r.names) for name in few_names
                          for r in walked[name]), "block instance count"))
                 if value not in subset]
    suite.record(GG, "census-counts-are-the-region-walk", problems,
                 detail=["%s: %d" % (subject, value)
                         for value, subject in subjects] +
                        ["over %s alone: %r" % (", ".join(few_names), subset)],
                 text=fleet)

    # ---- sorted, not merely stable ------------------------------------------
    # Byte identity across two RUNS is the weak half and it is asserted first.
    # The strong half is byte identity across two input ORDERS: a renderer that
    # iterated a set would agree with itself all afternoon and disagree the
    # moment a filename or a hash seed moved, which on a measured page reads as
    # a stale region nobody can explain.
    problems = []
    if fleet != mod.census_text("fleet", sources, targets):
        problems.append("two fleet renders in one process differ")
    if source_text != mod.census_text("sources", sources, targets):
        problems.append("two source renders in one process differ")
    if fleet != mod.census_text("fleet", sources, list(reversed(targets))):
        problems.append("the fleet census depends on the ORDER its targets "
                        "arrive in, so it is stable rather than sorted")
    if source_text != mod.census_text(
            "sources", dict(reversed(list(sources.items()))), targets):
        problems.append("the source census depends on the INSERTION ORDER of "
                        "the block maps, so it is stable rather than sorted")

    rows = []
    for line in fleet.splitlines():
        cells = census_cells(line)
        if cells and len(cells) == 2 and cells[1].isdigit():
            rows.append((tuple(census_spans(cells[0])), int(cells[1])))
    if rows != sorted(rows, key=lambda row: (-row[1], row[0])):
        problems.append("the multi-name table is not ordered by count "
                        "descending with ties broken by name: %r" % (rows,))
    if dict(rows) != markers:
        problems.append("the multi-name table is %r, the walk finds %r"
                        % (dict(rows), markers))
    for line in fleet.splitlines():
        blocks = census_block_spans(line)
        if line.startswith("|") or len(blocks) < 2:
            continue                    # the table keeps the MARKER's order
        if blocks != sorted(blocks):
            problems.append("a block list is unsorted: %r" % line)
    source_rows = []
    for line in source_text.splitlines():
        cells = census_cells(line)
        if cells and len(cells) == 3 and cells[1].isdigit():
            source_rows.append((census_spans(cells[0])[0], int(cells[1]),
                                census_spans(cells[2])))
    if [row[0] for row in source_rows] != sorted(row[0] for row in source_rows):
        problems.append("the source rows are not in path order: %r"
                        % [row[0] for row in source_rows])
    problems += ["%s lists its blocks unsorted: %r" % (path, names)
                 for path, _count, names in source_rows if names != sorted(names)]
    suite.record(GG, "census-is-sorted-not-merely-stable", problems,
                 detail=["two runs identical, and identical again with the "
                         "targets reversed and the block maps re-inserted "
                         "backwards",
                         "multi-name rows: %d, ordered by count desc then name"
                         % len(rows),
                         "the table alone keeps the MARKER's order "
                         "(dependency first), which is why it is asserted "
                         "against the walk rather than against sorted()"])

    # ---- the read path cannot write -----------------------------------------
    # `apply_regions` is REPLACED for the duration, so a census that fell
    # through into the rewrite loop is caught by a recorder instead of by a
    # diff of the repository. The case must be able to fail without the suite
    # having edited fifteen servers to find out.
    digests_before = {path: H.sha256_file(str(path))
                      for path in sorted(Path(SCRIPTS).glob("*.py"))}
    recorded, real_apply = [], mod.apply_regions
    mod.apply_regions = lambda path, regions: recorded.append(str(path))
    printed_by_main = {}
    try:
        for argv in (["--census", "fleet"], ["--census", "sources"],
                     ["--census", "fleet", "--check", "--force"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = mod.main(argv)
            printed_by_main[tuple(argv)] = (code, buf.getvalue())
    finally:
        mod.apply_regions = real_apply

    problems = ["a census run reached apply_regions: %r" % recorded] \
        if recorded else []
    problems += ["%s returned %d, not 0" % (" ".join(argv), code)
                 for argv, (code, _out) in printed_by_main.items() if code]
    for argv, want in ((("--census", "fleet"), fleet),
                       (("--census", "sources"), source_text),
                       (("--census", "fleet", "--check", "--force"), fleet)):
        got = printed_by_main[argv][1]
        if got != want:
            problems.append("%s printed %d bytes, census_text produces %d"
                            % (" ".join(argv), len(got), len(want)))
    changed = [str(path) for path, digest in digests_before.items()
               if H.sha256_file(str(path)) != digest]
    if changed:
        problems.append("the census modified %r" % [os.path.basename(p)
                                                    for p in changed])
    suite.record(GG, "census-writes-nothing-and-preempts-the-writers", problems,
                 detail=["apply_regions calls during three census runs: %d"
                         % len(recorded),
                         "--check and --force alongside --census: still a "
                         "report, still rc=0",
                         "%d file(s) under Scripts/ re-digested, 0 changed"
                         % len(digests_before)])

    # ---- the source census is the source registry ---------------------------
    # The row set is asserted against `CANONICAL_SOURCES`, not against whatever
    # the census found: a source that stopped being rendered would otherwise
    # agree with a census that stopped rendering it.
    problems = []
    if len(source_rows) != len(CANONICAL_NAMES):
        problems.append("%d row(s) for %d canonical source(s)"
                        % (len(source_rows), len(CANONICAL_NAMES)))
    by_path = {row[0]: row for row in source_rows}
    for name in CANONICAL_NAMES:
        want_path = "Scripts/%s" % name
        row = by_path.get(want_path)
        if row is None:
            problems.append("no row for %s; the census rendered %r"
                            % (want_path, sorted(by_path)))
            continue
        if not os.path.isfile(H.repo_path(*want_path.split("/"))):
            problems.append("%s is rendered as an anchor and is not a file"
                            % want_path)
        if row[1] != len(sources[name]) or row[2] != sorted(sources[name]):
            problems.append("%s: the row says %d %r, the block map holds %d %r"
                            % (want_path, row[1], row[2], len(sources[name]),
                               sorted(sources[name])))
    total = sum(row[1] for row in source_rows)
    if total not in census_numbers(source_text.splitlines()[-1]):
        problems.append("the closing line does not state the %d-block total: %r"
                        % (total, source_text.splitlines()[-1]))
    if total != len(defined):
        problems.append("the rows sum to %d and the sources define %d distinct "
                        "names, so two sources share one" % (total, len(defined)))
    if tuple(mod.CENSUS_KINDS) != CENSUS_KINDS:
        problems.append("generator CENSUS_KINDS is %r, the mirror is %r"
                        % (tuple(mod.CENSUS_KINDS), CENSUS_KINDS))
    for kind in CENSUS_KINDS:
        refusal = expect_exit(lambda: mod.census_text(kind, sources, targets))
        if refusal is not None:
            problems.append("the %r census is refused: %s" % (kind, refusal))
        elif not mod.census_text(kind, sources, targets).strip():
            problems.append("the %r census renders an EMPTY body, which on a "
                            "page is a block that says nothing where a number "
                            "is owed" % kind)
    unknown = expect_exit(lambda: mod.census_text("no-such-kind", sources,
                                                  targets))
    if not unknown or "no-such-kind" not in str(unknown):
        problems.append("an unknown census subject was not refused by name: %r"
                        % unknown)
    suite.record(GG, "census-sources-is-the-registry", problems,
                 detail=["rows: %d, blocks: %d" % (len(source_rows), total),
                         "refusal: %s" % unknown],
                 text=source_text)

    # ---- the two host-side subjects, through `main` -------------------------
    # Their default target set is NOT the fleet census's: a block's host count
    # and a hand copy are questions about every file the generator WRITES, so
    # the declared non-server hosts are in scope, where `--census fleet` is
    # about the server fleet and says so. The default argv is driven here, with
    # the same recorder standing in for `apply_regions`, so the case can see
    # both a wrong default and a census that fell through into the writers.
    hosts_targets = targets + [Path(SCRIPTS) / name for name in DECLARED_HOSTS]
    recorded, by_main = [], {}
    mod.apply_regions = lambda path, regions: recorded.append(str(path))
    try:
        for kind in ("hosts", "hand-copies"):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = mod.main(["--census", kind])
            except SystemExit as exc:
                code = exc.code
            by_main[kind] = (code, buf.getvalue())
    finally:
        mod.apply_regions = real_apply

    def main_problems(kind, want):
        code, got = by_main[kind]
        out = problem_if(recorded, "--census %s reached apply_regions: %r"
                         % (kind, recorded))
        out += problem_if(code != 0, "--census %s exited %r, not 0"
                          % (kind, code))
        out += problem_if(code == 0 and got != want,
                          "--census %s printed %d bytes by default; over the "
                          "fleet plus the declared hosts census_text produces "
                          "%d" % (kind, len(got), len(want)))
        return out

    # ---- `--census hosts`: every canonical block, and who carries it ---------
    # The oracle is the region walk again, keyed by (source, block) because a
    # region resolves its names against its OWN source -- a count keyed by the
    # bare name would agree with a census that credited the wrong domain.
    def hosts_oracle(paths):
        found = {(src, block): set()
                 for src, blocks in sources.items() for block in blocks}
        for path in paths:
            for region in mod.audit(path, sources):
                for block in region.names:
                    found.setdefault((region.source, block), set()).add(
                        "Scripts/%s" % path.name)
        return found

    def hosts_rows(text):
        rows = []
        for line in text.splitlines():
            cells = census_cells(line)
            if cells and len(cells) == 4 and cells[2].isdigit():
                block, src = census_spans(cells[0]), census_spans(cells[1])
                rows.append(((src[0] if src else "", block[0] if block else ""),
                             int(cells[2]), census_spans(cells[3])))
        return rows

    def whole_collapsed(want):
        """The WHOLE_SOURCES whose blocks all share one oracle host set."""
        out = {}
        for src in WHOLE_SOURCES:
            sets = [held for (s, _b), held in want.items() if s == src]
            if sets and all(held == sets[0] for held in sets):
                out[src] = sets[0]
        return out

    def hosts_check(paths, text, where):
        want = hosts_oracle(paths)
        rows = hosts_rows(text)
        # A whole source whose blocks all share one host set is ONE row; the
        # moment a block's set differs it falls back to a row per block, so a
        # partial host is visible on the page.
        collapsed = whole_collapsed(want)
        keys = sorted(set(
            ("Scripts/%s" % src, WHOLE_SOURCE_ROW % len(sources[src])
             if src in collapsed else block)
            for src, block in want))
        # Membership AND order in one comparison: exactly one row per block
        # every source defines, hosted or not, in (source, block) order.
        out = problem_if(
            [key for key, _count, _names in rows] != keys,
            "%s: the rows are %r, every canonical block in (source, block) "
            "order is %r" % (where, [key for key, _c, _n in rows], keys))
        got = {key: (count, names) for key, count, names in rows}
        for (src, block), held in sorted(want.items()):
            row = got.get(("Scripts/%s" % src, block))
            if row is not None and row != (len(held), sorted(held)):
                out.append("%s: %s says %d %r, the walk finds %d %r"
                           % (where, block, row[0], row[1], len(held),
                              sorted(held)))
        # M-2: the loop above looks rows up by BLOCK name and skips a key with
        # no row, so a collapsed row -- whose key is no block name -- would
        # never have its numbers compared. Each one is compared here.
        for (spelled, block), count, names in rows:
            if not block.endswith("blocks (whole source)"):
                continue
            src = spelled.split("/", 1)[-1]
            if src not in WHOLE_SOURCES:
                out.append("%s: %s is collapsed to one row but is not a whole "
                           "source" % (where, spelled))
                continue
            # (a) every block of the source carries the same host set.
            if src not in collapsed:
                out.append("%s: %s is collapsed to one row although its blocks' "
                           "host sets differ" % (where, src))
                continue
            # (b) the row's numbers are that shared set's.
            shared = collapsed[src]
            if (count, names) != (len(shared), sorted(shared)):
                out.append("%s: %s's whole-source row says %d %r, the walk "
                           "finds %d %r" % (where, src, count, names,
                                            len(shared), sorted(shared)))
            # (c) N is the source's block count, parsed, not assumed.
            digits = block.split(" ")[1] if block.startswith("all ") else ""
            if not digits.isdigit() or int(digits) != len(sources[src]):
                out.append("%s: %s's row claims %r, the source defines %d "
                           "blocks" % (where, src, block, len(sources[src])))
        return out, want

    def whole_row_controls():
        """Negative controls for the collapsed whole-source row (M-2).

        Two planted hosts exist only in memory: `mod.audit` is wrapped for the
        duration, because `census_hosts` reaches it by its global name and this
        suite writes nothing. One takes a whole source whole; the other drops
        that source's last block, which is exactly a partial host.
        """
        out = []
        src = WHOLE_SOURCES[0]
        names = list(sources.get(src, {}))
        if len(names) < 2:
            return ["%s: fewer than two blocks, so the whole-row controls prove "
                    "nothing" % src]
        whole_path = Path(SCRIPTS) / "planted_whole.py"
        part_path = Path(SCRIPTS) / "planted_partial.py"
        planted = {whole_path: tab_host(", ".join(names), src),
                   part_path: tab_host(", ".join(names[:-1]), src)}
        real_audit = mod.audit
        mod.audit = lambda path, srcs: (
            mod.audit_text(path.name, planted[path], srcs) if path in planted
            else real_audit(path, srcs))
        try:
            whole_text = mod.census_text("hosts", sources, [whole_path])
            part_text = mod.census_text("hosts", sources, [part_path])
            found, _w = hosts_check([whole_path], whole_text, "planted whole host")
            out += found
            spelled = "Scripts/%s" % src
            lines = whole_text.splitlines(keepends=True)
            at = next((i for i, line in enumerate(lines)
                       if "`%s`" % (WHOLE_SOURCE_ROW % len(names)) in line
                       and "`%s`" % spelled in line), None)
            if at is None:
                out.append("a host carrying all of %s did not collapse it to one "
                           "row: %r" % (src, whole_text))
            else:
                row = lines[at]
                for what, mutant in (
                        ("the count changed by one",
                         row.replace("| 1 |", "| 2 |", 1)),
                        ("one host name replaced",
                         row.replace("`Scripts/planted_whole.py`",
                                     "`Scripts/mcp-purity.py`", 1))):
                    if mutant == row:
                        out.append("control %r could not be built from %r"
                                   % (what, row))
                        continue
                    found, _w = hosts_check(
                        [whole_path],
                        "".join(lines[:at] + [mutant] + lines[at + 1:]),
                        "control")
                    if not any(src in problem for problem in found):
                        out.append("a whole-source row with %s was not refused "
                                   "by name: %r" % (what, found))
            # The partial host: the census must fall back to a row per block
            # for that source, and the checker must agree with the fallback.
            blocks = [block for (s, block), _c, _n in hosts_rows(part_text)
                      if s == spelled]
            out += problem_if(
                sorted(blocks) != sorted(names),
                "a partial host of %s did not make the census fall back to "
                "per-block rows: %r" % (src, blocks))
            found, _w = hosts_check([part_path], part_text, "planted partial host")
            out += found
        except SystemExit as exc:
            out.append("a planted whole-source host was refused: %s" % exc)
        finally:
            mod.audit = real_audit
        return out

    hosts_text = ""
    refusal = expect_exit(lambda: mod.census_text("hosts", sources,
                                                  hosts_targets))
    problems = problem_if(refusal is not None,
                          "`--census hosts` is refused: %s" % refusal)
    detail = []
    if refusal is None:
        hosts_text = mod.census_text("hosts", sources, hosts_targets)
        found, want = hosts_check(hosts_targets, hosts_text, "full host set")
        problems += found
        # A number that does not move with its input was typed: a proper
        # subset of the hosts must re-derive, row by row.
        few = hosts_targets[:3]
        found, _few_want = hosts_check(
            few, mod.census_text("hosts", sources, few),
            "over %s alone" % ", ".join(p.name for p in few))
        problems += found
        if hosts_text != mod.census_text(
                "hosts", dict(reversed(list(sources.items()))),
                list(reversed(hosts_targets))):
            problems.append("the host census depends on the ORDER of its "
                            "targets or of the block maps, so it is stable "
                            "rather than sorted")
        closing = census_numbers(hosts_text.rstrip("\n").splitlines()[-1])
        hosted = sum(1 for held in want.values() if held)
        problems += ["the closing line never states the %d %s" % (value, what)
                     for value, what in ((len(hosts_targets), "hosts scanned"),
                                         (len(want), "canonical blocks"),
                                         (hosted, "blocks with a host"))
                     if value not in closing]
        problems += main_problems("hosts", hosts_text)
        problems += whole_row_controls()
        detail = ["%s: %d" % (block, len(held))
                  for (_src, block), held in sorted(want.items(),
                                                    key=lambda kv: kv[0][1])]
    suite.record(GG, "census-hosts-is-the-region-walk", problems,
                 detail=detail, text=hosts_text)

    # ---- `--census hand-copies`: ONE implementation, and it is the generator's
    # The walk used to live in this file, beside a roster of reasons written as
    # a comment. A page cannot render a comment, so both moved into the
    # generator, and this case gates the move itself: a `bound_names` still
    # defined HERE is a second implementation of the census, which is the
    # drift this suite exists to prevent, turned on itself.
    api = ("bound_names", "hand_copies_text", "hand_copies", "HAND_COPY_REASONS")
    missing = [name for name in api if not hasattr(mod, name)]
    problems = problem_if(
        missing, "the generator does not define %s, so the hand-copy walk "
        "still lives only in this suite" % missing)
    problems += problem_if(
        "bound_names" in globals(),
        "this suite still defines its own bound_names -- two implementations "
        "of one census")
    hand_text, detail = "", []
    refusal = expect_exit(lambda: mod.census_text("hand-copies", sources,
                                                  hosts_targets))
    problems += problem_if(refusal is not None,
                           "`--census hand-copies` is refused: %s" % refusal)
    if not missing and refusal is None:
        defined_names = {n for blocks in sources.values() for n in blocks}
        entries = mod.hand_copies(sources, hosts_targets)
        hand_text = mod.census_text("hand-copies", sources, hosts_targets)
        roster = dict(mod.HAND_COPY_REASONS)
        problems += problem_if(entries != sorted(entries),
                               "hand_copies() is not sorted: %r" % entries)
        bullets = [line for line in hand_text.splitlines()
                   if line.startswith("- ")]
        parsed = [tuple(census_spans(line)[:2]) for line in bullets]
        problems += problem_if(
            parsed != [tuple(entry) for entry in entries],
            "the census rows %r are not hand_copies()'s entries %r, one row "
            "each and in its order" % (parsed, entries))
        for line, (host, name) in zip(bullets, entries):
            reason = roster.get((Path(host).name, name))
            if reason is None and "UNDECLARED" not in line:
                problems.append("%s: %s has no declared reason and its row "
                                "does not say so: %r" % (host, name, line))
            if reason is not None and reason not in line:
                problems.append("%s: %s -- the row does not carry its "
                                "declared reason: %r" % (host, name, line))
        problems += ["the roster declares %s for %s, which no canonical "
                     "source defines" % (name, host)
                     for host, name in sorted(roster)
                     if name not in defined_names]
        # The control: an empty roster must turn every row UNDECLARED, and a
        # declared reason naming a copy that is not there must be NAMED, not
        # dropped -- a reason with nothing left to explain is a false sentence
        # on the page. Restored in `finally`, so a failure cannot leak.
        ghost_host = hosts_targets[0].name
        ghost = sorted(defined_names - {n for h, n in entries
                                        if Path(h).name == ghost_host})[0]
        real_roster = mod.HAND_COPY_REASONS
        try:
            mod.HAND_COPY_REASONS = {}
            bare = mod.census_text("hand-copies", sources, hosts_targets)
            mod.HAND_COPY_REASONS = {(ghost_host, ghost): "planted"}
            planted = mod.census_text("hand-copies", sources, hosts_targets)
        finally:
            mod.HAND_COPY_REASONS = real_roster
        bare_rows = [line for line in bare.splitlines() if line.startswith("- ")]
        problems += problem_if(
            len(bare_rows) != len(entries)
            or not all("UNDECLARED" in line for line in bare_rows),
            "with an empty roster every row must read UNDECLARED: %r"
            % bare_rows)
        problems += problem_if(
            not any(ghost in census_spans(line) and not line.startswith("- ")
                    for line in planted.splitlines()),
            "a declared reason for %s: %s, which is not a hand copy, was not "
            "named as having nothing left to explain" % (ghost_host, ghost))
        problems += main_problems("hand-copies", hand_text)
        detail = ["%s: %s%s" % (host, name,
                                "" if (Path(host).name, name) in roster
                                else "  [UNDECLARED]")
                  for host, name in entries]
    suite.record(GG, "census-hand-copies-is-the-generator-walk", problems,
                 detail=detail, text=hand_text)


STRICT_CONTROL = (
    "import json\n"
    "\n\n"
    "def _param(text):\n"
    "    return json.loads(text)\n"
    "\n\n"
    "def _render(value):\n"
    "    return json.dumps(value)\n"
    "\n\n"
    "%s %s :: _helper\n"
    "def _helper():\n"
    "    return json.loads('1')\n"
    "%s\n"
    "\n\n"
    "class McpServer:\n"
    "    def run(self, line):\n"
    "        return json.loads(line)\n"
    "\n"
    "    def _write(self, response):\n"
    "        return json.dumps(response)\n"
    "\n"
    "    def _quiet(self, response):\n"
    "        return json.dumps(response)\n"
) % (BEGIN_PREFIX, CANONICAL_NAME, END_PREFIX)


def group_strict(suite, mod):
    """H. R-0067 + R-0068: every peer frame goes through the strict blocks."""
    sources = mod.load_all_blocks()
    hosts = sorted(Path(SCRIPTS).glob(TARGET_GLOB))

    # The checker first, on a synthetic host: a checker that silently matched
    # nothing would read exactly like a clean fleet.
    control_key = ("control.py", "McpServer._quiet", "dumps")
    bad, seen = strict_violations(mod, "control.py", STRICT_CONTROL, SYNTH_SOURCES,
                                  {control_key: "control"})
    got = sorted(line.split(" ", 1)[1] for line in bad)
    want = sorted(["McpServer.run: bare json.loads", "McpServer._write: bare json.dumps",
                   "_param: bare json.loads"])
    problems = problem_if(got != want, "the checker reported %r, expected %r" % (got, want))
    problems += problem_if(seen != {control_key},
                           "the declared control row was not consumed: %r" % (seen,))
    suite.record(GH, "strict-gate-control", problems,
                 detail=["caught      : %s" % "; ".join(got),
                         "spared      : a module-level json.dumps (not the frame "
                         "tier), a json.loads inside a region, one declared row"])

    problems, detail = [], []
    for path in hosts:
        regioned = []
        for region in mod.audit(path, sources):
            if region.source == CANONICAL_NAME:
                regioned += [name for name in region.names if name in STRICT_NAMES]
        if tuple(regioned) != STRICT_NAMES:
            problems.append("%s: its %s regions carry %r, expected %r"
                            % (path.name, CANONICAL_NAME, regioned, STRICT_NAMES))
    suite.record(GH, "strict-region-in-every-host", problems,
                 detail=["%d host(s), each expected to carry %s"
                         % (len(hosts), ", ".join(STRICT_NAMES))])

    problems = []
    for path in hosts:
        text = path.read_text(encoding="utf-8")
        body = outside_regions(mod, path.name, text, sources)
        for name in ("_strict_loads", "_strict_dumps"):
            if "%s(" % name not in body:
                problems.append("%s: never calls %s outside a region" % (path.name, name))
    suite.record(GH, "strict-blocks-called", problems,
                 detail=["%d host(s): the frame read must call _strict_loads and the "
                         "frame write _strict_dumps" % len(hosts)])

    bad, seen = [], set()
    for path in hosts:
        found, used = strict_violations(mod, path.name, path.read_text(encoding="utf-8"),
                                        sources, STRICT_JSON_EXCEPTIONS)
        bad += found
        seen |= used
    suite.record(GH, "no-bare-json-on-peer-input", bad,
                 detail=bad or ["every parse, and every frame-tier emit, is strict or "
                                "declared (%d declared row(s))" % len(STRICT_JSON_EXCEPTIONS)])

    stale = ["%s %s json.%s: declared, but no such site" % key
             for key in sorted(set(STRICT_JSON_EXCEPTIONS) - seen)]
    suite.record(GH, "strict-exceptions-not-stale", stale,
                 detail=stale or ["%d declared row(s), each still excusing a live site"
                                  % len(STRICT_JSON_EXCEPTIONS)])


def group_hygiene(suite, pyc_before, digests_before):
    pyc_after = H.pycache_snapshot()
    suite.record(GD, "pycache-zero", problem_if(
        pyc_before or pyc_after,
        "expected zero .pyc in the tree, saw %d before and %d after"
        % (len(pyc_before), len(pyc_after)),
    ))

    changed = [
        path for path, digest in digests_before.items()
        if H.sha256_file(path) != digest
    ]
    suite.record(GD, "sources-untouched", problem_if(
        changed,
        "this suite modified: %s" % [os.path.basename(p) for p in changed],
    ))


# --- the HTTP front (R-0072): group E's four blocks, groups A and I ------------

# The exact bytes the generated process_request answers when every slot is taken.
HTTPFRONT_503 = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

# A synthetic host for GI3: a server and a handler class carrying the REAL
# in-class marker pairs, whose bodies are rendered from the source at run time
# (%(server)s / %(handler)s), plus exactly the hand overrides its own rows
# declare. The checkers must be silent on it; each plant below must be named.
HTTPFRONT_PIN_CLEAN = (
    "import socketserver\n"
    "import sys\n"
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
    "\n"
    "log = None\n"
    "\n\n"
    "class PinServer(ThreadingHTTPServer):\n"
    "    front_log = log\n"
    "\n"
    "    def __init__(self, address, handler, settings):\n"
    "        self.settings = settings\n"
    "        self.conn_sem = None\n"
    "        super().__init__(address, handler)\n"
    "\n"
    "    # BEGIN GENERATED: _mcp_httpfront.py :: server_bind, process_request, handle_error\n"
    "%(server)s"
    "    # END GENERATED:\n"
    "\n"
    "    def process_request_thread(self, request, client_address):\n"
    "        try:\n"
    "            super().process_request_thread(request, client_address)\n"
    "        finally:\n"
    "            self.conn_sem.release()\n"
    "\n\n"
    "class PinHandler(BaseHTTPRequestHandler):\n"
    "    timeout = 10.0\n"
    "\n"
    "    def setup(self):\n"
    "        super().setup()\n"
    "        self._header_reader = None\n"
    "\n"
    "    # BEGIN GENERATED: _mcp_httpfront.py :: parse_request, handle_expect_100, _single_header\n"
    "%(handler)s"
    "    # END GENERATED:\n"
    "\n"
    "    def send_error(self, code, message=None, explain=None):\n"
    "        self.close_connection = True\n"
)
HTTPFRONT_PIN_CLASSES = ("PinServer", "PinHandler")
HTTPFRONT_PIN_ROWS = {
    ("pin.py", "PinServer", "__init__"): "control",
    ("pin.py", "PinServer", "process_request_thread"): "control",
    ("pin.py", "PinHandler", "setup"): "control",
    ("pin.py", "PinHandler", "send_error"): "control",
}


def httpfront_raised(cid, exc):
    """The finding a refusing generator (or a missing source) becomes: never a crash."""
    return "%s: the generator raised %s: %s" % (cid, type(exc).__name__, str(exc)[:200])


def httpfront_render(mod, names):
    """*names* of the canonical source rendered at a class-body indent."""
    blocks = mod.load_blocks(mod.CANONICAL_SOURCES[HTTPFRONT_CANONICAL_NAME])
    return mod.render(HTTPFRONT_CANONICAL_NAME, list(names), blocks, indent="    ")


def httpfront_classes(tree):
    """name -> ClassDef for every class in *tree*, nested ones included."""
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def httpfront_bindings(body, name):
    """The nodes of *body* (a statement list) that bind *name* directly."""
    found = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                found.append(node)
        elif isinstance(node, ast.Assign):
            if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == name):
                found.append(node)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                found.append(node)
    return found


def httpfront_in_class(region, cls):
    """True when *region*'s BEGIN marker lies within *cls*'s lines."""
    return cls.lineno <= region.begin + 1 <= cls.end_lineno


def httpfront_inside(node, region):
    """True when *node* lies between *region*'s BEGIN and END lines."""
    return region.begin + 2 <= node.lineno and node.end_lineno <= region.end


def httpfront_binding_problems(mod, label, text, sources, members, module_names):
    """GA's checker: every httpfront name in its region, bound exactly once, inside it.

    *members* maps a class name to the member names its regions must carry;
    *module_names* are carried by column-0 regions. Raises whatever the
    generator raises -- the caller records that as a FAIL.
    """
    problems = []
    regions = [r for r in mod.audit_text(label, text, sources)
               if r.source == HTTPFRONT_CANONICAL_NAME]
    hand = set(mod.hand_copies_text(label, text, sources))
    tree = ast.parse(text, filename=label)
    classes = httpfront_classes(tree)
    wanted = []
    for cls_name, names in members.items():
        cls = classes.get(cls_name)
        if cls is None:
            problems.append("%s: no class %s" % (label, cls_name))
            continue
        for name in names:
            wanted.append(name)
            home = [r for r in regions if name in r.names and httpfront_in_class(r, cls)]
            elsewhere = [r for r in regions if name in r.names and not httpfront_in_class(r, cls)]
            if not home:
                problems.append("%s: %s.%s is in no _mcp_httpfront.py region inside the class%s"
                                % (label, cls_name, name,
                                   " (a region at line %d lists it outside)" % (elsewhere[0].begin + 1)
                                   if elsewhere else ""))
            bound = httpfront_bindings(cls.body, name)
            if len(bound) != 1:
                problems.append("%s: %s bound %d times in %s, expected exactly 1"
                                % (label, name, len(bound), cls_name))
            elif home and not httpfront_inside(bound[0], home[0]):
                problems.append("%s: %s at %d-%d lies outside its region %d-%d"
                                % (label, name, bound[0].lineno, bound[0].end_lineno,
                                   home[0].begin + 2, home[0].end))
    for name in module_names:
        wanted.append(name)
        home = [r for r in regions if name in r.names and r.indent == ""]
        if not home:
            problems.append("%s: %s is in no column-0 _mcp_httpfront.py region" % (label, name))
        bound = httpfront_bindings(tree.body, name)
        if len(bound) != 1:
            problems.append("%s: %s bound %d times in module, expected exactly 1"
                            % (label, name, len(bound)))
        elif home and not httpfront_inside(bound[0], home[0]):
            problems.append("%s: %s at %d-%d lies outside its region %d-%d"
                            % (label, name, bound[0].lineno, bound[0].end_lineno,
                               home[0].begin + 2, home[0].end))
    problems += ["%s: %s is a hand copy outside every region" % (label, name)
                 for name in wanted if name in hand]
    return problems


def httpfront_releases_in_finally(func):
    """True when *func* calls self.conn_sem.release() inside a Try's finalbody."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.finalbody:
            for sub in ast.walk(stmt):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "release"
                        and isinstance(sub.func.value, ast.Attribute)
                        and sub.func.value.attr == "conn_sem"
                        and isinstance(sub.func.value.value, ast.Name)
                        and sub.func.value.value.id == "self"):
                    return True
    return False


def httpfront_adaptation_problems(mod, label, text, sources, classes, rows):
    """GI1's checker: every stdlib override generated or declared, no row stale.

    *classes* is (server class, handler class); *rows* is keyed (label, class,
    member) like HTTPFRONT_ADAPTATIONS. The override set is computed: a
    FunctionDef in the class body whose name the stdlib base also has.
    """
    problems = []
    regions = [r for r in mod.audit_text(label, text, sources)
               if r.source == HTTPFRONT_CANONICAL_NAME]
    tree = ast.parse(text, filename=label)
    every = httpfront_classes(tree)
    bases = dict(zip(classes, (http.server.ThreadingHTTPServer,
                               http.server.BaseHTTPRequestHandler)))
    for cls_name, base in bases.items():
        cls = every.get(cls_name)
        if cls is None:
            problems.append("%s: no class %s" % (label, cls_name))
            continue
        own = [r for r in regions if httpfront_in_class(r, cls)]
        generated = {name for r in own for name in r.names}
        defs = [node for node in cls.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        overrides = sorted({node.name for node in defs if hasattr(base, node.name)})
        hand = {node.name for node in defs
                if not any(httpfront_inside(node, r) for r in own)}
        for name in overrides:
            if name not in generated and (label, cls_name, name) not in rows:
                problems.append("%s: %s.%s overrides the stdlib by hand and is neither "
                                "generated nor declared in HTTPFRONT_ADAPTATIONS"
                                % (label, cls_name, name))
        for key in sorted(rows):
            # A row must name a hand def that IS a stdlib override: a hand
            # helper the base does not have is no adaptation to declare.
            if (key[0] == label and key[1] == cls_name
                    and (key[2] not in hand or key[2] not in overrides)):
                problems.append("%s: %s.%s: declared in HTTPFRONT_ADAPTATIONS, but no such "
                                "hand override (stale)" % key)
        if cls_name == classes[0]:
            thread = [node for node in defs if node.name == "process_request_thread"]
            if not thread or not httpfront_releases_in_finally(thread[0]):
                problems.append("%s: %s.process_request_thread does not release conn_sem "
                                "in a finally" % (label, cls_name))
    for key in sorted(rows):
        if key[0] == label and key[1] not in classes:
            problems.append("%s: %s.%s: declared in HTTPFRONT_ADAPTATIONS, but no such "
                            "hand override (stale)" % key)
    for region in regions:
        if not region.indent:
            continue
        holders = [c.name for c in every.values() if httpfront_in_class(region, c)]
        if not any(name in classes for name in holders):
            problems.append("%s: a _mcp_httpfront.py region (BEGIN line %d) sits in %s, "
                            "not in a front class"
                            % (label, region.begin + 1,
                               "class %s" % holders[-1] if holders else "no class"))
    return problems


def httpfront_assigns_self(func, attr):
    """True when *func* assigns self.<attr> anywhere in its body."""
    for node in ast.walk(func):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        for target in targets:
            if (isinstance(target, ast.Attribute) and target.attr == attr
                    and isinstance(target.value, ast.Name) and target.value.id == "self"):
                return True
    return False


def httpfront_seam_problems(label, text, classes):
    """GI2's checker: the seams the generated members read are provided (presence only)."""
    problems = []
    every = httpfront_classes(ast.parse(text, filename=label))
    server, handler = (every.get(name) for name in classes)
    for name, cls in zip(classes, (server, handler)):
        if cls is None:
            problems.append("%s: no class %s" % (label, name))
    if server is not None:
        problems += problem_if(not httpfront_bindings(server.body, "front_log"),
                               "%s: %s assigns no class-level front_log" % (label, classes[0]))
        init = [n for n in server.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
        for attr in ("settings", "conn_sem"):
            if not init or not httpfront_assigns_self(init[0], attr):
                problems.append("%s: %s.__init__ never assigns self.%s" % (label, classes[0], attr))
    if handler is not None:
        problems += problem_if(not httpfront_bindings(handler.body, "timeout"),
                               "%s: %s assigns no class-level timeout" % (label, classes[1]))
        setup = [n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == "setup"]
        if not setup or not httpfront_assigns_self(setup[0], "_header_reader"):
            problems.append("%s: %s.setup never assigns self._header_reader" % (label, classes[1]))
    return problems


class _HfRecLog:
    """A logger that records (level, args) and formats nothing."""

    def __init__(self):
        self.records = []

    def __getattr__(self, level):
        if level.startswith("_"):
            raise AttributeError(level)
        return lambda *args, **kwargs: self.records.append((level, args))


class _HfSem:
    """A semaphore whose verdict is fixed; it counts what was asked of it."""

    def __init__(self, free):
        self.free = free
        self.acquired = 0
        self.released = 0
        self.blocking = []

    def acquire(self, blocking=True):
        self.blocking.append(blocking)
        if self.free:
            self.acquired += 1
        return self.free

    def release(self):
        self.released += 1


def httpfront_exec_class(code, base, socketserver_fake, where):
    """Exec `class H(Base):` + *code* in a namespace holding no host name (R1)."""
    ns = {"Base": base, "socketserver": socketserver_fake, "sys": sys, "logging": logging,
          "__name__": "httpfront_synthetic"}
    exec(compile("class H(Base):\n" + code, "<httpfront %s>" % where, "exec"), ns)  # noqa: S102
    return ns["H"]


def httpfront_drive_server(text):
    """Drive the rendered server members against fakes; nothing opens a socket."""
    problems, detail = [], []
    bind_calls = []

    class FakeTCPServer:
        @staticmethod
        def server_bind(server):
            bind_calls.append(server)
            server.server_address = ("127.0.0.1", 4242)

    fake_ss = types.SimpleNamespace(TCPServer=FakeTCPServer)

    class ServerBase:
        base_raises = False

        def __init__(self):
            self.base_calls = []
            self.shut = []

        def process_request(self, request, client_address):
            self.base_calls.append(client_address)
            if self.base_raises:
                raise RuntimeError("the handler thread did not start")

        def shutdown_request(self, request):
            self.shut.append(request)

    class FakeRequest:
        def __init__(self):
            self.sent = []

        def sendall(self, data):
            self.sent.append(data)

    try:
        cls = httpfront_exec_class(text, ServerBase, fake_ss, "server")
    except Exception as exc:  # noqa: BLE001 -- a block that does not define is the finding
        return ["server members: defining the class raised %s: %s"
                % (type(exc).__name__, str(exc)[:200])], detail

    def drive(block, fn):
        try:
            fn()
        except NameError as exc:
            problems.append("%s: NameError %s -- the block reads a name its host "
                            "does not hand it (R1)" % (block, exc))
        except Exception as exc:  # noqa: BLE001 -- any other surprise is a finding too
            problems.append("%s: raised %s: %s" % (block, type(exc).__name__, str(exc)[:200]))

    def bind():
        inst = cls()
        inst.server_bind()
        problems.extend(problem_if(bind_calls != [inst],
                                   "server_bind: TCPServer.server_bind called %d time(s), "
                                   "expected once with the instance" % len(bind_calls)))
        problems.extend(problem_if((getattr(inst, "server_name", None),
                                    getattr(inst, "server_port", None)) != ("127.0.0.1", 4242),
                                   "server_bind: server_name/server_port are %r/%r"
                                   % (getattr(inst, "server_name", None),
                                      getattr(inst, "server_port", None))))

    def refused():
        inst = cls()
        inst.front_log = _HfRecLog()
        inst.settings = types.SimpleNamespace(max_connections=7)
        inst.conn_sem = _HfSem(False)
        request = FakeRequest()
        inst.process_request(request, ("127.0.0.1", 1))
        problems.extend(problem_if(request.sent != [HTTPFRONT_503],
                                   "process_request (full): sent %r, expected one 503" % request.sent))
        problems.extend(problem_if(inst.shut != [request],
                                   "process_request (full): shutdown_request calls %r" % inst.shut))
        problems.extend(problem_if(inst.base_calls,
                                   "process_request (full): the stdlib process_request still ran"))
        problems.extend(problem_if(inst.conn_sem.blocking != [False],
                                   "process_request (full): acquire called with %r, expected "
                                   "[False]" % inst.conn_sem.blocking))
        want = [("warning", ("http connection refused: %d connections open", 7))]
        problems.extend(problem_if(inst.front_log.records != want,
                                   "process_request (full): logged %r, expected %r"
                                   % (inst.front_log.records, want)))

    def admitted():
        inst = cls()
        inst.front_log = _HfRecLog()
        inst.conn_sem = _HfSem(True)
        inst.process_request(FakeRequest(), ("127.0.0.1", 2))
        problems.extend(problem_if((inst.conn_sem.acquired, inst.conn_sem.released,
                                    inst.base_calls) != (1, 0, [("127.0.0.1", 2)]),
                                   "process_request (free): acquired %d, released %d, stdlib "
                                   "calls %r" % (inst.conn_sem.acquired, inst.conn_sem.released,
                                                 inst.base_calls)))

    def base_raises():
        inst = cls()
        inst.front_log = _HfRecLog()
        inst.conn_sem = _HfSem(True)
        inst.base_raises = True
        try:
            inst.process_request(FakeRequest(), ("127.0.0.1", 3))
        except RuntimeError:
            pass
        else:
            problems.append("process_request (stdlib raised): the exception did not propagate")
        problems.extend(problem_if(inst.conn_sem.released != 1,
                                   "process_request (stdlib raised): the slot was released %d "
                                   "time(s), expected 1" % inst.conn_sem.released))

    def failed():
        inst = cls()
        inst.front_log = _HfRecLog()
        try:
            raise ValueError("TF_SECRET")
        except ValueError:
            inst.handle_error(None, ("127.0.0.1", 4))
        want = [("warning", ("http handler failed: %s", "ValueError"))]
        problems.extend(problem_if(inst.front_log.records != want,
                                   "handle_error: logged %r, expected %r"
                                   % (inst.front_log.records, want)))
        problems.extend(problem_if("TF_SECRET" in repr(inst.front_log.records),
                                   "handle_error: the exception's text reached the log (ADR 0011)"))

    drive("server_bind", bind)
    drive("process_request", refused)
    drive("process_request", admitted)
    drive("process_request", base_raises)
    drive("handle_error", failed)
    detail.append("server      : server_bind without getfqdn; 503 + one warning when full; "
                  "slot given back when the stdlib raises; handle_error logs the type only")
    return problems, detail


def httpfront_drive_handler(text):
    """Drive the rendered handler members against fakes; nothing opens a socket."""
    problems, detail = [], []
    sentinel = object()

    class HandlerBase:
        timeout = 4.5

        def __init__(self):
            self.sent = []

        def parse_request(self):
            return sentinel

        def send_response_only(self, *args):
            self.sent.append(args)

    class FakeConn:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class FakeWfile:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

    class FakeHeaders:
        def __init__(self, values):
            self.values = values

        def get_all(self, name):
            return self.values

    try:
        cls = httpfront_exec_class(text, HandlerBase, types.SimpleNamespace(), "handler")
    except Exception as exc:  # noqa: BLE001 -- a block that does not define is the finding
        return ["handler members: defining the class raised %s: %s"
                % (type(exc).__name__, str(exc)[:200])], detail

    def drive(block, fn):
        try:
            fn()
        except NameError as exc:
            problems.append("%s: NameError %s -- the block reads a name its host "
                            "does not hand it (R1)" % (block, exc))
        except Exception as exc:  # noqa: BLE001 -- any other surprise is a finding too
            problems.append("%s: raised %s: %s" % (block, type(exc).__name__, str(exc)[:200]))

    def parse():
        inst = cls()
        inst.connection = FakeConn()
        inst._header_reader = types.SimpleNamespace(deadline=123.0)
        got = inst.parse_request()
        problems.extend(problem_if(got is not sentinel,
                                   "parse_request: returned %r, not the stdlib's answer" % (got,)))
        problems.extend(problem_if(inst._header_reader.deadline is not None,
                                   "parse_request: the header deadline was not reset"))
        problems.extend(problem_if(inst.connection.timeouts != [cls.timeout],
                                   "parse_request: settimeout calls %r, expected [%r]"
                                   % (inst.connection.timeouts, cls.timeout)))

    def expect():
        inst = cls()
        inst.wfile = FakeWfile()
        got = inst.handle_expect_100()
        problems.extend(problem_if(got is not True,
                                   "handle_expect_100: returned %r, expected True" % (got,)))
        problems.extend(problem_if(inst.wfile.written or inst.sent,
                                   "handle_expect_100: wrote %r / sent %r before any auth"
                                   % (inst.wfile.written, inst.sent)))

    def single():
        inst = cls()
        for values, want in ((None, (True, None)), (["v"], (True, "v")),
                             (["v", "w"], (False, None))):
            inst.headers = FakeHeaders(values)
            got = inst._single_header("X-Probe")
            problems.extend(problem_if(got != want,
                                       "_single_header: %r gave %r, expected %r"
                                       % (values, got, want)))

    drive("parse_request", parse)
    drive("handle_expect_100", expect)
    drive("_single_header", single)
    detail.append("handler     : parse_request ends the header phase at self.timeout; "
                  "Expect sends nothing; a repeated header is refused")
    return problems, detail


def group_httpfront_blocks(suite, mod):
    """E. What the HTTP front's blocks do (R-0072): token, deadline, headers, members."""
    try:
        src = H.load_module_from_path("mcp_httpfront_under_test", HTTPFRONT_SOURCE)
        absent = []
    except (OSError, SyntaxError) as exc:
        src = None
        absent = ["the source does not load: %s: %s" % (type(exc).__name__, str(exc)[:200])]

    # The token: the floor and the error class are the CALLER's, and a refusal
    # never quotes the value.
    problems = list(absent)
    if src is not None:
        token = getattr(src, "_http_token_value", None)

        class Refused(Exception):
            pass

        good = "".join(chr(0x21 + i) for i in range(32))
        for what, value in (("short", "tf-short-secret-value"),
                            ("space", "tf" + "x" * 20 + " " + "y" * 20),
                            ("DEL", "tf" + "x" * 40 + "\x7f"),
                            ("non-ASCII", "tf" + "x" * 40 + "é")):
            try:
                got = token(value, "--where", 32, Refused)
            except Refused as exc:
                problems += problem_if(value in str(exc),
                                       "%s: the refusal quotes the token value" % what)
                problems += problem_if("--where" not in str(exc),
                                       "%s: the refusal does not name where: %r" % (what, str(exc)))
                problems += problem_if(what == "short" and "32" not in str(exc),
                                       "short: the refusal does not name min_len: %r" % str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 -- the point: the class is the caller's
                problems.append("%s: raised %s, not the passed error class"
                                % (what, type(exc).__name__))
                continue
            problems.append("%s: accepted, returned %r" % (what, got))
        try:
            got = token(good, "--where", 32, Refused)
            problems += problem_if(got != good.encode("ascii"),
                                   "32 printable chars: returned %r" % (got,))
        except Exception as exc:  # noqa: BLE001
            problems.append("32 printable chars: refused (%s)" % type(exc).__name__)
        try:
            token("short", "--where", 32, KeyError)
            problems.append("error=KeyError: a short value was accepted")
        except KeyError:
            pass
        except Exception as exc:  # noqa: BLE001
            problems.append("error=KeyError: raised %s instead" % type(exc).__name__)
    suite.record(GE, "httpfront-token-value", problems,
                 detail=["refused     : short, a space, a DEL, a non-ASCII char -- each "
                         "through the passed class, the value never quoted"])

    # The header deadline: min(left, cap) per recv, nothing once it has passed.
    problems = list(absent)
    if src is not None:
        class FakeSock:
            def __init__(self):
                self.calls = []

            def settimeout(self, value):
                self.calls.append(("settimeout", value))

            def recv_into(self, buf):
                self.calls.append(("recv_into", len(buf)))
                return 0

        def reader_calls(cap, offset):
            sock = FakeSock()
            reader = src._HeaderDeadlineReader(sock, cap)
            if offset is not None:
                reader.deadline = time.monotonic() + offset
            try:
                reader.readinto(bytearray(8))
            except socket.timeout:
                return sock.calls, "timeout"
            return sock.calls, None

        try:
            calls, raised = reader_calls(0.5, None)
            problems += problem_if((calls, raised) != ([("recv_into", 8)], None),
                                   "deadline None: %r %r, expected one plain recv" % (calls, raised))
            calls, raised = reader_calls(0.5, 100.0)
            problems += problem_if(calls != [("settimeout", 0.5), ("recv_into", 8)],
                                   "cap 0.5, 100 s left: %r, expected settimeout(0.5)" % calls)
            calls, raised = reader_calls(30.0, 100.0)
            problems += problem_if(calls != [("settimeout", 30.0), ("recv_into", 8)],
                                   "cap 30, 100 s left: %r, expected settimeout(30.0)" % calls)
            calls, raised = reader_calls(30.0, 5.0)
            left = calls[0][1] if calls and calls[0][0] == "settimeout" else None
            problems += problem_if(left is None or not 4.0 < left <= 5.0 or len(calls) != 2,
                                   "cap 30, 5 s left: %r, expected settimeout(~5)" % calls)
            calls, raised = reader_calls(30.0, -1.0)
            problems += problem_if((calls, raised) != ([], "timeout"),
                                   "deadline passed: %r %r, expected socket.timeout and no recv"
                                   % (calls, raised))
        except Exception as exc:  # noqa: BLE001 -- a broken block fails the case
            problems.append("the reader raised %s: %s" % (type(exc).__name__, str(exc)[:200]))
    suite.record(GE, "httpfront-header-deadline", problems,
                 detail=["recv        : plain with no deadline; min(left, cap) for cap 0.5 "
                         "and 30; socket.timeout and no recv once passed"])

    problems = list(absent)
    if src is not None:
        want = (("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store"))
        got = getattr(src, "_HTTP_STDLIB_REFUSAL_HEADERS", None)
        problems += problem_if(got != want, "_HTTP_STDLIB_REFUSAL_HEADERS is %r, expected %r"
                               % (got, want))
    suite.record(GE, "httpfront-refusal-headers", problems,
                 detail=["headers     : nosniff, no-store"])

    # The members, rendered INSIDE a class from the canonical text: zero-arg
    # super() only works there (R2), and a namespace with no host name in it
    # turns a host-global read into a NameError (R1).
    problems, detail = [], []
    try:
        server_text = httpfront_render(mod, HTTPFRONT_SERVER)
        handler_text = httpfront_render(mod, HTTPFRONT_HANDLER)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- a refusing generator fails the case
        problems.append(httpfront_raised("httpfront-in-class-blocks", exc))
        server_text = handler_text = None
    if server_text is not None:
        found, lines = httpfront_drive_server(server_text)
        problems += found
        detail += lines
    if handler_text is not None:
        found, lines = httpfront_drive_handler(handler_text)
        problems += found
        detail += lines
    suite.record(GE, "httpfront-in-class-blocks", problems, detail=detail)


def group_httpfront_pins(suite, mod):
    """A + I. The HTTP front's two hosts pinned (R-0072): regions, overrides, seams."""
    texts = {host: (Path(SCRIPTS) / host).read_text(encoding="utf-8")
             for host in HTTPFRONT_HOSTS}

    # GA: every httpfront name in its region, in the right class or at column 0,
    # bound exactly once and inside that region -- the census alone cannot see a
    # hand def beside a region that LISTS the same name (R4).
    problems, detail = [], []
    try:
        sources = mod.load_all_blocks()
        for host, (server, handler) in sorted(HTTPFRONT_HOSTS.items()):
            problems += httpfront_binding_problems(
                mod, host, texts[host], sources,
                {server: HTTPFRONT_SERVER, handler: HTTPFRONT_HANDLER}, HTTPFRONT_MODULE)
        detail.append("hosts       : %s" % ", ".join(sorted(HTTPFRONT_HOSTS)))
        # The two controls, through the same checker over synthetic text.
        head = "import socketserver\nimport sys\n\n\nclass Ctl(object):\n    x = 1\n\n"
        listed = httpfront_render(mod, ("parse_request", "handle_expect_100"))
        hand = ("\n    def _single_header(self, name):\n"
                "        return True, None\n")
        ctl_i = (head + "    %s %s :: parse_request, handle_expect_100\n"
                 % (BEGIN_PREFIX, HTTPFRONT_CANONICAL_NAME)
                 + listed + "    %s\n" % END_PREFIX + hand)
        found = httpfront_binding_problems(mod, "ctl_i.py", ctl_i, sources,
                                           {"Ctl": ("_single_header",)}, ())
        problems += problem_if(not any("_single_header is a hand copy" in f for f in found),
                               "control (i): a hand _single_header beside a region that does "
                               "not list it was not reported as a hand copy: %r" % found)
        listed = httpfront_render(mod, HTTPFRONT_HANDLER)
        ctl_ii = (head + "    %s %s :: %s\n" % (BEGIN_PREFIX, HTTPFRONT_CANONICAL_NAME,
                                                ", ".join(HTTPFRONT_HANDLER))
                  + listed + "    %s\n" % END_PREFIX + hand)
        found = httpfront_binding_problems(mod, "ctl_ii.py", ctl_ii, sources,
                                           {"Ctl": HTTPFRONT_HANDLER}, ())
        problems += problem_if(not any("_single_header bound 2 times" in f for f in found),
                               "control (ii): a hand _single_header after a region that lists "
                               "it was not reported as bound twice: %r" % found)
        detail.append("controls    : a hand copy beside a region (census path) and a hand "
                      "duplicate of a listed name (count path) both reported")
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- a refusing generator fails the case
        problems.append(httpfront_raised("httpfront-in-both-hosts", exc))
    suite.record(GA, "httpfront-in-both-hosts", problems, detail=detail)

    # GI1: the override set is COMPUTED from the stdlib base and the class body;
    # the rows say why each hand override is not generated.
    problems = []
    try:
        sources = mod.load_all_blocks()
        for host, classes in sorted(HTTPFRONT_HOSTS.items()):
            problems += httpfront_adaptation_problems(mod, host, texts[host], sources,
                                                      classes, HTTPFRONT_ADAPTATIONS)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- a refusing generator fails the case
        problems.append(httpfront_raised("httpfront-adaptations-declared", exc))
    suite.record(GI, "httpfront-adaptations-declared", problems,
                 detail=["rows        : %d declared hand override(s) across %d host(s)"
                         % (len(HTTPFRONT_ADAPTATIONS), len(HTTPFRONT_HOSTS))])

    # GI2: presence of the seams the generated members read; their VALUES are
    # router J40's and proxy K5's.
    problems = []
    for host, classes in sorted(HTTPFRONT_HOSTS.items()):
        try:
            problems += httpfront_seam_problems(host, texts[host], classes)
        except SyntaxError as exc:
            problems.append("%s: cannot be parsed (%s)" % (host, exc))
    suite.record(GI, "httpfront-seams-provided", problems,
                 detail=["seams       : front_log, settings, conn_sem (server); timeout, "
                         "_header_reader (handler)"])

    # GI3: both checkers on a synthetic host first silent, then each plant named.
    problems, caught = [], []
    try:
        sources = mod.load_all_blocks()
        clean = HTTPFRONT_PIN_CLEAN % {"server": httpfront_render(mod, HTTPFRONT_SERVER),
                                       "handler": httpfront_render(mod, HTTPFRONT_HANDLER)}

        def judge(text, rows):
            return (httpfront_adaptation_problems(mod, "pin.py", text, sources,
                                                  HTTPFRONT_PIN_CLASSES, rows)
                    + httpfront_seam_problems("pin.py", text, HTTPFRONT_PIN_CLASSES))

        found = judge(clean, HTTPFRONT_PIN_ROWS)
        problems += problem_if(found, "the clean synthetic host was reported: %r" % found)
        release = ("        try:\n"
                   "            super().process_request_thread(request, client_address)\n"
                   "        finally:\n"
                   "            self.conn_sem.release()\n")
        unreleased = ("        super().process_request_thread(request, client_address)\n"
                      "        self.conn_sem.release()\n")
        send_error = "        self.close_connection = True\n"
        plants = (
            ("an undeclared log_request override", clean, send_error,
             send_error + "\n    def log_request(self, code=\"-\", size=\"-\"):\n        pass\n",
             HTTPFRONT_PIN_ROWS, "PinHandler.log_request overrides the stdlib by hand"),
            ("a stale handle_one_request row", clean, None, None,
             {**HTTPFRONT_PIN_ROWS, ("pin.py", "PinHandler", "handle_one_request"): "stale"},
             "PinHandler.handle_one_request: declared in HTTPFRONT_ADAPTATIONS, but no such "
             "hand override (stale)"),
            ("a release outside any finally", clean, release, unreleased,
             HTTPFRONT_PIN_ROWS, "process_request_thread does not release conn_sem in a finally"),
            ("no front_log", clean, "    front_log = log\n", "",
             HTTPFRONT_PIN_ROWS, "PinServer assigns no class-level front_log"),
            # A row must name a stdlib OVERRIDE: a hand helper the stdlib base
            # does not have is not an adaptation, so its row is stale too.
            ("a row naming a non-override helper", clean, send_error,
             send_error + "\n    def _precheck(self):\n        return True\n",
             {**HTTPFRONT_PIN_ROWS, ("pin.py", "PinHandler", "_precheck"): "not an override"},
             "PinHandler._precheck: declared in HTTPFRONT_ADAPTATIONS, but no such "
             "hand override (stale)"),
        )
        for what, base, old, new, rows, needle in plants:
            text = base
            if old is not None:
                if old not in base:
                    problems.append("%s: the plant did not apply" % what)
                    continue
                text = base.replace(old, new, 1)
            found = judge(text, rows)
            if any(needle in f for f in found):
                caught.append(what)
            else:
                problems.append("%s: not reported (got %r)" % (what, found))
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- a refusing generator fails the case
        problems.append(httpfront_raised("httpfront-pin-controls", exc))
    suite.record(GI, "httpfront-pin-controls", problems,
                 detail=["caught      : %s" % ("; ".join(caught) or "nothing"),
                         "spared      : the clean synthetic host"])


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="generated regions match their canonical source",
                    opts=opts, mode="grouped")

    pyc_before = H.pycache_snapshot()
    digests_before = {p: H.sha256_file(p) for p in
                      (SOURCE, CONCURRENCY_SOURCE, LOGGING_SOURCE, LSP_SOURCE,
                       PAGING_SOURCE, WEBSOCKET_SOURCE, OAUTH_SOURCE, HTTPFRONT_SOURCE,
                       GENERATOR, TARGET)}

    mod = H.load_module_from_path("amalgamate_under_test", GENERATOR)
    blocks = H.load_module_from_path("mcp_json_under_test", SOURCE)
    lsp = H.load_module_from_path("mcp_lsp_under_test", LSP_SOURCE)
    paging = H.load_module_from_path("mcp_paging_under_test", PAGING_SOURCE)
    logmod = H.load_module_from_path("mcp_logging_under_test", LOGGING_SOURCE)
    group_gate(suite, mod)
    group_contract(suite, mod)
    group_control(suite, mod)
    group_blocks(suite, blocks, lsp, paging, logmod)
    group_httpfront_blocks(suite, mod)
    group_tabs(suite, mod)
    group_census(suite, mod)
    group_strict(suite, mod)
    group_httpfront_pins(suite, mod)
    group_hygiene(suite, pyc_before, digests_before)

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
