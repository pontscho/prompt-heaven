---
name: generated-regions
type: component
status: active
title: Generated regions — how the MCP fleet shares plumbing without importing it
description: The amalgamate generator, its canonical sources, the hand-declared hosts that are not servers, the four rules that decide what may be a shared block, and the two registers of deliberate exclusion.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_brotli.py
  - Scripts/_mcp_chrome.py
  - Scripts/_mcp_codesearch.py
  - Scripts/_mcp_concurrency.py
  - Scripts/_mcp_dispatch.py
  - Scripts/_mcp_httpfront.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_logging.py
  - Scripts/_mcp_lsp.py
  - Scripts/_mcp_oauth.py
  - Scripts/_mcp_paging.py
  - Scripts/_mcp_websearch.py
  - Scripts/_mcp_websocket.py
  - Scripts/_mcp_zstd.py
  - tests/test_generated_region.py
verified:
  commit: eb1dd9e
  date: 2026-10-08
links:
  - scripts
  - tests
  - 0009-the-first-reader-is-a-cold-model
  - 0010-a-handler-failure-must-reach-iserror
  - 0019-only-gate-on-what-you-can-prove
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0023-the-websocket-client-is-a-sixth-domain
  - 0025-generate-do-not-import
  - 0026-speak-chrome-from-the-stdlib-verify-by-default
  - 0011-a-truncated-payload-carries-the-first-cookie
  - mcp-proxy
  - llm-router
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0029-the-http-front-is-a-domain
---

# Generated regions

The MCP servers in [[scripts]] share their plumbing by **generation, not
import**: a canonical function is pasted into each server between two comment
markers, and a generator re-renders it on demand. There is no runtime dependency
between servers, no shared package, and no import that could carry a helper from
one file to another.

Not every host is a server. Both search scripts, `Scripts/search_duckduckgo.py`
and `Scripts/search_github.py`, take the stdlib HTTP client and its two content
decoders they share with `Scripts/mcp-webfetch.py` and `Scripts/mcp-search.py`,
and each takes the search domain it shares with `Scripts/mcp-search.py` — web
search for the DDG script, code search for the grep.app one. The LLM router
`Scripts/llm-router.py`, an HTTP server that is not an MCP server, takes the
two logging regions, the OAuth client and the stdlib HTTP front it shares with
`Scripts/mcp-proxy.py`. They are targets because the generator **names them
by hand** `Scripts/amalgamate.py:DECLARED_HOSTS` — see "A host outside the glob"
below — not because anything about them matches the server glob.

**Scope note.** This page is about the *MCP fleet's* generated regions. The repo
contains two other marker-delimited generation mechanisms and neither is this
one: the checkpoint file's table of contents, whose one-writer design is recorded
in [[0009-the-first-reader-is-a-cold-model]], and the wiki's own **measured
regions** `Scripts/mcp-wiki.py`, which render a page block from a command named
in `docs/measurements.json` and whose WHY is
[[0019-only-gate-on-what-you-can-prove]]. This page carries two of those, and
they are where its counts come from. All three share a pattern and nothing else.

## Why generation rather than an import

Every server is a self-contained single file, and the rejection of a shared
import is argued identically in **every** canonical source
`Scripts/amalgamate.py:CANONICAL_NAMES`, in the suite
`tests/test_generated_region.py`, and in `Scripts/MCP_SKELETON.md` and
`project-forge.yaml`. Three reasons: an import would write
`Scripts/__pycache__` into *a tree every suite that snapshots bytecode asserts
stays empty*; it would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds; and it would move the helpers out of the
module attributes the footprint suite reaches for. A fourth ground sits in the
commit that made the decision, `8effd2f`, rather than in the docstrings: a user
copying one server without its sibling would get an `ImportError`. The decision,
all four grounds and why Cog was not used are recorded in
[[0025-generate-do-not-import]].

That middle clause is quoted rather than paraphrased, because its wording is
itself the decision. It names the **property** instead of tallying the suites,
and it is byte-identical at every site, so the family stays greppable and a
newly added suite cannot stale it. The tally it replaced said "four", which was
right only under an unstated reading of which check counts — a suite asserting
emptiness *absolutely*, or one asserting only a delta. Both sets are open and
move whenever a suite is added, which is why [[tests]] declines to count either.

The cost is accepted openly — duplication is the *mechanism*, and the generator
plus its gate are what keep the copies from diverging.

## The mechanism

A region is a pair of comment markers, and the closing marker carries a truncated
digest of what sits between them `Scripts/amalgamate.py`. The opening marker
names a canonical source and one or more block names; the digest is a 12-character
SHA-256 over the **emitted body alone**, markers excluded `Scripts/amalgamate.py:body_hash`.

Markers are located with `tokenize` and only `COMMENT` tokens count, so a marker
quoted inside a docstring or a string literal is inert — the generator's own
docstring contains one, and the suite uses that as a live negative control.

A region is in one of three states `Scripts/amalgamate.py:Region`:

- **ok** — the body is byte-identical to a fresh render *and* the recorded digest
  matches it. `--check` requires both; either alone is not enough.
- **hand-edited** — a digest was recorded and the body no longer hashes to it.
  This is **refused, never silently overwritten**, unless forced. The file gets
  exactly one writer.
- **stale** — everything else, including an empty body and a never-written digest.

A `BEGIN` without an `END` is a hard error rather than a skip, because a region
that quietly stops being maintained is the whole failure the mechanism exists to
prevent `Scripts/amalgamate.py`.

How much of the fleet this actually is, the generator counts on demand
`Scripts/amalgamate.py:census_fleet` — and the block below is that count rendered
into the page rather than typed into it, by the wiki's measured-region mechanism
writing between its own pair of markers:

```markdown
<!-- BEGIN MEASURED: generated-region-census -->
the command's stdout lands here, rendered on demand and never typed
<!-- END MEASURED: 3f9a1c2b7d40 -->
```

That example is a specimen, not a region: a fenced code block is inert to the
scanner, which is the rule that lets a page document the marker it also carries
`Scripts/mcp-wiki.py:_fenced_line_indices`.

<!-- BEGIN MEASURED: generated-region-census -->
- MCP servers matching `Scripts/mcp-*.py`: 17, of which 17 carry at least one generated region
- live generated regions in them: 173
- block instances those regions emit: 786
- distinct canonical blocks named on a marker: 300, out of the 359 defined by the 14 canonical sources

Regions by how many blocks one marker names: 93 name 1 block; 35 name 2 blocks; 19 name 3 blocks; 1 name 6 blocks; 8 name 7 blocks; 8 name 8 blocks; 1 name 18 blocks; 1 name 19 blocks; 2 name 21 blocks; 2 name 25 blocks; 1 name 37 blocks; 2 name 137 blocks.

The 80 region(s) that name more than one block, by the list written on the marker:

| Blocks named on one marker | Regions |
|---|---|
| `_LOG_VALUE_WIDTH`, `_LOG_KEYS_SHOWN`, `_log_value` | 17 |
| `_result`, `_error` | 16 |
| `JSON_INT_LITERAL_LIMIT`, `_json_no_constant`, `_json_finite_float`, `_json_bounded_int`, `_strict_loads`, `_strict_dumps`, `_json_error_window` | 8 |
| `JSON_INT_LITERAL_LIMIT`, `_json_no_constant`, `_json_finite_float`, `_json_bounded_int`, `_strict_loads`, `_strict_dumps`, `_json_error_window`, `_ensure_dict` | 8 |
| `DEFAULT_MAX_ANSWER_CHARS`, `_max_answer_chars` | 6 |
| `_abs_uri`, `_abs_path` | 4 |
| `_request`, `_notify` | 4 |
| `uri_to_path`, `path_to_uri` | 4 |
| `BR_SONAMES_LINUX`, `BR_SONAMES_MACOS`, `BR_DIRS_LINUX`, `BR_DIRS_MACOS`, `BR_FILES_LINUX`, `BR_FILES_MACOS`, `BR_CHUNK_BYTES`, `_BR_RESULT_ERROR`, `_BR_RESULT_SUCCESS`, `_BR_RESULT_NEEDS_MORE_INPUT`, `_BR_RESULT_NEEDS_MORE_OUTPUT`, `_BR_SYMBOLS`, `_BR_STATE`, `_br_platform`, `_br_exists`, `_br_cdll`, `_br_find_library`, `_br_attempts`, `_br_configure`, `_br_load`, `_brotli_decompress` | 2 |
| `ChromeClientError`, `ChromeTls12Error`, `ChromeBodyTooLarge`, `_chrome_profile`, `CHROME_PROFILE`, `CH_MAX_RECORD_BYTES`, `CH_MAX_HANDSHAKE_BYTES`, `CH_MAX_FRAME_BYTES`, `CH_MAX_HEADER_LIST_BYTES`, `CH_MAX_HEADER_BLOCK_BYTES`, `CH_HPACK_TABLE_BYTES`, `CH_MAX_HPACK_INT`, `CH_MAX_HPACK_INT_CONTINUATIONS`, `CH_MAX_H2_CONTROL_FRAMES`, `CH_MAX_H2_EMPTY_FRAMES`, `CH_MAX_H2_CONTINUATION_FRAMES`, `CH_MAX_H1_HEAD_BYTES`, `CH_MAX_H1_HEADERS`, `CH_MAX_H1_CHUNK_LINE_BYTES`, `CH_MAX_BODY_BYTES`, `CH_DEFAULT_DECODE_CAP`, `CH_MAX_CONTENT_CODINGS`, `CH_MAX_REDIRECTS`, `CH_MAX_COOKIES_PER_DOMAIN`, `CH_MAX_COOKIES`, `CH_MAX_COOKIE_BYTES`, `_CH_X25519_P`, `_CH_X25519_A24`, `_ch_x25519_cswap`, `_ch_x25519`, `_ch_x25519_keypair`, `_CH_P256_P`, `_CH_P256_B`, `_CH_P256_N`, `_CH_P256_GX`, `_CH_P256_GY`, `_ch_p256_double`, `_ch_p256_add`, `_ch_p256_mul`, `_ch_p256_keypair`, `_ch_p256_shared`, `_ChMlKem768`, `_ch_aes_tables`, `_CH_AES_TABLES`, `_ChAesGcm`, `_ChChaCha20Poly1305`, `_ch_hkdf_extract`, `_ch_hkdf_expand`, `_ch_hkdf_expand_label`, `_ch_derive_secret`, `CH_MAX_PLAINTEXT_BYTES`, `_ChReader`, `_ChRecordReader`, `_ChHandshakeReader`, `_ChRecordCipher`, `_CH_KEY_SHARE_BYTES`, `_ch_vec`, `_ch_draw`, `_ch_idna_encode`, `_ch_sni_name`, `_ch_grease`, `_ch_permutation`, `_ch_key_share_entry`, `_ch_hello_extensions`, `_ch_hello_wire`, `_ch_client_hello`, `_CH_ALERT_NAMES`, `_ch_alert_name`, `_CH_HRR_RANDOM`, `_CH_TLS13_SUITES`, `_CH_SERVER_SHARE_BYTES`, `_CH_EE_FORBIDDEN`, `_ch_parse_server_hello`, `_ch_key_share_new`, `_ch_key_share_secret`, `_ch_traffic_cipher`, `CH_MAX_KEY_UPDATES`, `_ChTls`, `_ChTlsStream`, `_ch_huffman_table`, `_CH_HUFFMAN`, `_ch_huffman_decode_table`, `_CH_HUFFMAN_DECODE`, `_ch_hpack_static`, `_CH_HPACK_STATIC`, `_ch_hpack_int`, `_ch_huffman_encode`, `_ch_huffman_size`, `_ch_hpack_string`, `_ch_cookie_crumbs`, `_ChHpackEncoder`, `_ch_hpack_decode_int`, `_ch_huffman_decode`, `_ch_hpack_decode_string`, `_ChHpackDecoder`, `_CH_BAD_PORTS`, `_ch_split_url`, `_CH_TCHAR_SYMBOLS`, `_CH_FINGERPRINT_NAMES`, `_CH_FINGERPRINT_PREFIXES`, `_CH_FRAMING_NAMES`, `_CH_FRAMING_PREFIXES`, `_ch_header_pairs`, `_ch_check_caller_headers`, `_ch_origin_of`, `_ch_origin_text`, `_CH_PUBLIC_SUFFIXES`, `_ch_is_ip_host`, `_ch_is_public_suffix`, `_ch_site_of`, `_ch_sec_fetch_site`, `_ch_referer_for`, `_ch_profile_headers`, `_CH_H2_ERROR_NAMES`, `_ch_h2_frame`, `_ChH2Connection`, `_ChH1Connection`, `_ChHeaders`, `_ch_leading_digits`, `_ch_cookie_date`, `_ChCookieJar`, `_ChResponse`, `_ch_zlib_decode`, `_ch_decode_body`, `_CH_TRANSLATION_PREFIXES`, `_ch_embedded_ipv4`, `_ch_address_refused`, `_ch_public_only_policy`, `_ch_open_socket`, `_CH_REDIRECT_CODES`, `_CH_SITE_RANK`, `_ch_transport_error`, `_ch_unvetted_policy`, `_ChDeadlineSocket`, `_ChFallbackConnection`, `_ChSession`, `_ch_session_new` | 2 |
| `ZSTD_SONAMES_LINUX`, `ZSTD_SONAMES_MACOS`, `ZSTD_DIRS_LINUX`, `ZSTD_DIRS_MACOS`, `ZSTD_FILES_LINUX`, `ZSTD_FILES_MACOS`, `ZSTD_WINDOW_LOG_MAX`, `ZSTD_MAX_CHUNK_BYTES`, `_ZSTD_D_WINDOW_LOG_MAX`, `_ZSTD_FRAME_MAGIC`, `_ZSTD_SKIPPABLE_TAIL`, `_ZSTD_SYMBOLS`, `_ZSTD_STATE`, `_ZstdInBuffer`, `_ZstdOutBuffer`, `_zstd_platform`, `_zstd_exists`, `_zstd_cdll`, `_zstd_find_library`, `_zstd_attempts`, `_zstd_configure`, `_zstd_load`, `_zstd_error`, `_zstd_check_magic`, `_zstd_decompress` | 2 |
| `JSON_INT_LITERAL_LIMIT`, `_json_no_constant`, `_json_finite_float`, `_json_bounded_int`, `_strict_loads`, `_strict_dumps` | 1 |
| `WebSocketError`, `WS_MAX_HANDSHAKE_BYTES`, `WS_MAX_FRAME_BYTES`, `WS_MAX_MESSAGE_BYTES`, `_ws_parse_url`, `_ws_handshake_request`, `_ws_handshake_split`, `_ws_handshake_verify`, `_ws_mask`, `_ws_encode_frame`, `_ws_parse_frame`, `_ws_assemble`, `_ws_control_reply`, `_WsConnection`, `_ws_step`, `_ws_connect`, `_ws_recv`, `_ws_send` | 1 |
| `_CODE_LINE_SEPARATORS`, `_code_clean`, `_code_line`, `_code_field`, `_ext_to_lang`, `EXT_TO_LANG`, `detect_language`, `_SnippetParser`, `extract_code_from_snippet`, `build_github_url`, `_grep_hits`, `_grep_str`, `parse_grep_results`, `warmup_code_session`, `_body_is_json`, `_grep_app_blocked`, `search_github`, `_code_fence`, `format_code_results` | 1 |
| `_http_write_ready_file`, `_http_remove_ready_file` | 1 |
| `_normalize`, `_WEB_LINE_SEPARATORS`, `_web_clean`, `_web_line`, `_WEB_URL_SAFE`, `_web_url`, `decode_duckduckgo_url`, `_raw_href`, `_has_class`, `_LiteParser`, `parse_lite_results`, `_decode_bing_url`, `_VOID_TAGS`, `_H`, `_LISTING`, `_FONTSTYLE`, `_start_close`, `_START_CLOSE`, `_end_priority`, `_END_PRIORITY`, `_END_PRIORITY_DEFAULT`, `_Node`, `_TREE_MAX_DEPTH`, `_TREE_SCAN_BUDGET`, `_TreeBuilder`, `_child_elements`, `_descendant_text`, `_iter_elements`, `parse_bing_results`, `warmup_session`, `_DDG_CHALLENGE_MARKERS`, `_ddg_blocked`, `search_ddg`, `_bing_blocked`, `search_bing`, `run_web`, `format_web_results` | 1 |
| `parse_request`, `handle_expect_100`, `_single_header` | 1 |
| `server_bind`, `process_request`, `handle_error` | 1 |

Generated into every one of the 17 servers: `JSON_INT_LITERAL_LIMIT`, `_LOG_KEYS_SHOWN`, `_LOG_VALUE_WIDTH`, `_configure_logging`, `_did_you_mean`, `_json_bounded_int`, `_json_finite_float`, `_json_no_constant`, `_log_value`, `_strict_dumps`, `_strict_loads`.
Generated into every server but `Scripts/mcp-proxy.py`: `_json_error_window`.
Generated into every server but `Scripts/mcp-webfetch.py`: `_error`, `_result`.
<!-- END MEASURED: bed31ae21a08 -->

A single region may name several blocks, and that is the whole of the gap between
the region count and the block-instance count.

Those three counts — live regions, block instances, distinct canonical blocks —
are rendered rather than incremented, and the reason is the state hand-typing had
left them in: the first two had read `70` and `84` since before the logging
source existed, and the third read `11` against a table that summed to thirteen
further down this same page.

## Fourteen canonical sources, and why fourteen

The registry is a hand-written tuple, not a glob `Scripts/amalgamate.py:CANONICAL_NAMES`,
because a glob would let an unrelated file become a generation source by merely
existing — and the marker naming it would look exactly as legitimate as the ones
that belong. `Scripts/_mcp_smoke_test.py` is the standing example: an `_mcp_*.py`
file that is deliberately not a source.

`Scripts/_mcp_concurrency.py` is the other side of that coin — the case where the
deliberate edit was actually made, and the only source so far added for a constant
rather than for code. It holds one line, `MAX_INFLIGHT_REQUESTS = 8`, which every
live server that carried it had written out by hand until `8d56b6d` lifted it:
the fleet's widest-shared constant, whose hosts today are the block's row in the
`canonical-block-hosts` table below. It got a
domain of its own because none of the four existing ones could hold it without
becoming the shelf each of them is written not to be — JSON-RPC envelopes,
logging configuration, LSP framing and output paging are four questions, and "how
many handlers run at once" is a fifth. Filing it under the nearest of them would
have made the marker's source field decorative for every block in that file,
which is the one property the multi-source design exists to protect.

What that source deliberately does **not** take is the concurrency *decision*.
[[0008-a-serialized-read-loop-looks-like-a-dead-server]] records that the decision
was audited per server rather than copied, and the shared block is the number
those audits agreed on, not the agreeing. A server needing a different one keeps
its own copy and says why, exactly as the output ceiling works — at which point
the hand-copy census names it rather than hiding it.

Each source is **a domain, not a shelf**, and the JSON source has been narrowed
twice — framing left for the LSP source, row accounting for the paging source —
with both departures asserted as departures in the suite. The rule itself, the
naming complaint that measurement redirected at the container, and the test that
decides when a sixth source is warranted are
[[0014-a-canonical-source-is-a-domain]].

| Source | Domain |
|---|---|
| `Scripts/_mcp_brotli.py` | decoding one compression format, brotli, through the system shared library over `ctypes` |
| `Scripts/_mcp_codesearch.py` | how a code query becomes code results from grep.app — the request, the JSON answer and its HTML snippet, the block signal, and the rendered markdown |
| `Scripts/_mcp_chrome.py` | how a client makes the bytes a server receives indistinguishable from what Chrome sends — the ClientHello, the h2 preface and HEADERS blocks, the HTTP/1.1 request head — and how it survives what a hostile server sends back |
| `Scripts/_mcp_concurrency.py` | how many tool calls a server runs at once |
| `Scripts/_mcp_dispatch.py` | how a tool's dispatcher answers a name it does not know -- the near-miss pointer (`Did you mean 'X'?`) appended to an unknown-function, unknown-parameter or (on the proxy) unknown-tool refusal |
| `Scripts/_mcp_httpfront.py` | how a stdlib `http.server` listener takes a connection and gets it through the header phase safely — connection admission, the total header deadline, request-line hardening — plus the ready file a supervisor waits on and the bearer token value it compares against |
| `Scripts/_mcp_json.py` | JSON-RPC envelopes, wire-value coercion, strict JSON parsing and emitting for a peer's frames, JSON error reporting |
| `Scripts/_mcp_logging.py` | how a server CONFIGURES logging — level, sink, file mode — and how a peer-chosen structural value is written into a log line |
| `Scripts/_mcp_lsp.py` | how the LSP wire is spoken — `Content-Length` framing for a message, the `file://` DocumentUri for a path, the client-side hops that put a message on that wire, and the two spellings of a resolved path |
| `Scripts/_mcp_oauth.py` | how a native public client obtains, keeps and renews an OAuth 2.0 / OIDC grant — PKCE, the authorize redirect, the code, refresh and device grants, the token-endpoint answers and the ID-token claims, and the loopback redirect it listens on |
| `Scripts/_mcp_paging.py` | how much of a result a caller gets, and how it is told where the rest is |
| `Scripts/_mcp_websearch.py` | how a query becomes web results — the request each endpoint (DDG lite, Bing) is sent, the page it answers with and how it is parsed, the block signal, and the run loop that moves a batch from DDG to Bing |
| `Scripts/_mcp_websocket.py` | how a client speaks the WebSocket wire — the upgrade, the frames, message assembly, the control frames it must answer, and the ceilings on what a peer can make it allocate |
| `Scripts/_mcp_zstd.py` | decoding one compression format, zstd, through the system shared library over `ctypes` |

`Scripts/_mcp_websocket.py` is the sixth, and the first source whose domain is a
**protocol client** rather than a piece of server plumbing. It passed ADR 0014's
test on the only question that test asks: none of the five could hold it without
becoming a shelf — the LSP source speaks `Content-Length` framing over stdio,
the JSON source speaks envelopes, and a WebSocket frame is neither. It is also
the first source whose blocks mostly call **one another**, so no block of it
can stand in a region alone: every host spells one marker, the sans-IO core
dependency first and then the wrapper it uses — the asyncio one in `mcp-gdc`,
its only host since `8dde3a6`. The blocking-socket wrapper the DDG search script
used for its `cdp` backend stays in the source with no host. The decision, the hardening that
came with the lift, and what stays out of scope are
[[0023-the-websocket-client-is-a-sixth-domain]]; the behaviour is gated by its
own suite, `tests/test_mcp_websocket.py`, rather than by group E here.

Three arrived together, when the two third-party browser-impersonation
packages left the tree (R-0044): `Scripts/_mcp_chrome.py`, a stdlib HTTP client
whose Chrome path reproduces the pinned Chrome on the wire (153 then, 154 since
R-0051) and whose default transport
is the verified stdlib one (the search hosts have asked for the Chrome path
explicitly since `8dde3a6`), and the two decoders it is **handed** rather than
imports, `Scripts/_mcp_brotli.py` and `Scripts/_mcp_zstd.py`. Each passed ADR
0014's test on its own: a decoder is a collaborator the client receives as
`decoders=`, not a part of the wire it speaks, and the two formats are two
libraries and two questions, duplicated on purpose rather than shelved together
`Scripts/_mcp_brotli.py`. What is new is a rule about **how** they are taken.
Their blocks call one another as one state machine, so a host takes each of the
three **whole or not at all** `Scripts/amalgamate.py:WHOLE_SOURCES` — one marker
naming every block in source order — and the generator does not enforce that:
`whole-source-regions` does `tests/test_generated_region.py:group_gate`. The
decision, the cert gap the Chrome path carries and the gates with their measured
results are recorded in [[0026-speak-chrome-from-the-stdlib-verify-by-default]]; the suites that prove the behaviour are
`tests/test_mcp_chrome.py` and `tests/test_mcp_decoders.py`, again rather than
group E here.

Two more arrived together with `Scripts/mcp-search.py` (`8dde3a6`), when the
search code left the two CLIs: `Scripts/_mcp_websearch.py`, how a query becomes
web results (DDG lite first, Bing after a DDG block), and
`Scripts/_mcp_codesearch.py`, how a query becomes code results from grep.app. They
are two sources and not one because they are two questions under ADR 0014's test:
a different endpoint, a different page, a different block signal and a different
result shape. Neither fits the Chrome source either, which answers how to speak
HTTP like Chrome and nothing about what a search engine's page means. Both are
`WHOLE_SOURCES` `Scripts/amalgamate.py:WHOLE_SOURCES`, for the Chrome client's
reason: their blocks call one another, so each host carries one marker naming every
block in source order. The web source is generated into
`Scripts/search_duckduckgo.py` and `Scripts/mcp-search.py`, the code source into
`Scripts/search_github.py` and `Scripts/mcp-search.py`; the
`canonical-block-hosts` table below is the measured form of those two lists. The
session is **injected**, never created in either source: creating one needs
`_ch_session_new`, `_DECODERS` and `_ch_public_only_policy`, which belong to the
host and to another source, so each host keeps its own session factory and
per-endpoint hook `Scripts/_mcp_websearch.py:run_web`.

**The output sanitizer is one copy per domain, on purpose.** Both sources render
third-party text into a reply a model reads, and both need the same rule: drop every
invisible or display-steering code point, keep one-line fields on one line, and
render a URL only as a safe http(s) link. A third, shared source for that rule was
not made, and neither source calls the other's. A block may use only builtins, its
host's imports and the names of its OWN source co-listed on its marker
("A name must resolve against the source that was named", below, and
[[0014-a-canonical-source-is-a-domain]]), so a search block calling a sanitizer
from another source would be refused by the free-name gate. So each domain carries
its own copy under its own names: `_web_clean` / `_web_line` / `_web_url`
`Scripts/_mcp_websearch.py:_web_clean` and `_code_clean` / `_code_line` /
`_code_field` `Scripts/_mcp_codesearch.py:_code_clean`. The copies cannot drift
silently: `sanitizer-copies-agree` drives both over one corpus covering every
Unicode class they judge and fails on any disagreement
`tests/test_mcp_search.py:group_f`. The names differ because the sources must
stay disjoint (`sources-disjoint`); the agreement is a behavioural gate, not a
naming one.

`Scripts/_mcp_oauth.py` is the twelfth. It arrived with the
router's `codex` and `openai` backends, which hold an OAuth grant, refresh it
while they serve, and log in through the router's `login` subcommand. Its one
host is `Scripts/llm-router.py`. Neither the Chrome source nor the WebSocket one
could hold it without becoming a shelf: one answers how a client looks like
Chrome on the wire, the other a frame protocol, and neither says what a grant
is or when it is dead. The source holds the **protocol only**. The provider
rows (endpoints, client ids, scopes) are one vendor's data and stay in the host,
written with the source's row type `Scripts/llm-router.py:_rt_oauth_providers`.
The transport and the clock are **injected**: every request builder returns
`(url, headers, body)` for the host to send, and no block reads the time, so the
router's own framing and address policy guard the token endpoint
`Scripts/_mcp_oauth.py`. It is not a `WHOLE_SOURCES` entry. It follows the
websocket shape instead: a sans-IO core, then a two-block loopback wrapper
(`_oauth_listen`, `_oauth_sync_accept_callback`), all on one marker with the
core first. The suite mirrors the core's list by hand as `OAUTH_CORE`, as it does
`WEBSOCKET_CORE`, so a block added to the source and not to the mirror fails by
name. The security review's F2 and F9 fixes were the first such additions:
`OAUTH_MIN_REFRESH_INTERVAL_S` (a floor on clock-driven refreshes),
`OAUTH_INT_LITERAL_LIMIT` and `_oauth_bounded_int` (a bound on a JSON integer
literal's length) joined the source, `OAUTH_CORE` and the router's marker
together. The behaviour is gated by its own suite, `tests/test_mcp_oauth.py`,
rather than by group E here. The domain decision, the plan's ordinal for it and
why it is not whole are recorded in
[[0014-a-canonical-source-is-a-domain]] (addendum); the injected transport and
clock as the way a protocol source stays host-neutral are recorded in
[[0025-generate-do-not-import]] (addendum).

`Scripts/_mcp_httpfront.py` is the thirteenth, and the newest (R-0072). It was
lifted out of two hosts, `Scripts/mcp-proxy.py` and `Scripts/llm-router.py`,
whose stdlib HTTP fronts had been a declared, adapted copy of each other with no
gate holding them equal. It holds only the mechanism both hosts carried as
byte-identical twins: `_HeaderDeadlineReader`, the refusal-header pair, the
token value check, the ready-file pair, and six **methods** — `server_bind`,
`process_request` and `handle_error` for the server class, `parse_request`,
`handle_expect_100` and `_single_header` for the handler class
`Scripts/_mcp_httpfront.py`. The methods are top-level `def`s in the source and
land at an in-class marker's column, as the LSP source's client methods do; what
is new is that they override stdlib members and call `super()`, which works only
once rendered, so group E drives them rendered inside a synthetic class. None
of the existing sources could hold it without becoming a shelf: the Chrome
source is the client side of the wire, the OAuth source one protocol's loopback
redirect, and the logging source decides how a line is written, not when a
server stops reading headers. Policy never travels in a block: the reader's cap,
the token floor and its error class, the ready file's error class and logger are
arguments; `parse_request` reads the handler's own `self.timeout`; the server
blocks log through a class attribute `front_log` bound to the host's logger.
What differs between the hosts — `setup`, `handle_one_request`,
`process_request_thread`, `send_error`, `log_message` and the constructors —
stays hand-written and is declared row by row in
`tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS`, which group I reads.
The decision, the adaptation table, the policy seams and the declared limits
are [[0029-the-http-front-is-a-domain]].

`Scripts/_mcp_dispatch.py` is the fourteenth. It holds one block,
`_did_you_mean`, generated into every server (the fleet census above lists it
among the blocks that reach all of them): a refusal of a name a caller
sent ends with the closest real name, by `difflib.get_close_matches` at
`mcp-forge`'s cutoff of 0.6, and with nothing when no name is that close
`Scripts/_mcp_dispatch.py:_did_you_mean`. The JSON source was the nearest home
and the wrong question: it owns how a frame is enveloped and parsed and how a
wire VALUE is coerced, and a suggestion is about the caller's NAMES and reads
no JSON. None of the other sources is about names at all, so under
[[0014-a-canonical-source-is-a-domain]]'s rule a second domain in any of them
would make it a shelf. The block takes the host's tables as arguments, so it
has no free name but `difflib`; that is what lets it be generated where the
alias resolvers it sits beside cannot ([[0015-ambiguity-is-the-defect]],
Option 7). A suggestion never resolves: `context_chars` is pointed at
`context_lines` and still refused. The hosts that refuse an unknown parameter
take the pointer there too, and they are exactly the rows of
`Scripts/_mcp_smoke_test.py:NEAR_MISS_PARAM`, whose coverage row derives the
set from each host's source; every other host ignores an unknown key, so there
is no refusal to append it to. The behaviour is unit-tested in group E and the
call sites are driven live by `Scripts/_mcp_smoke_test.py:near_miss_checks`.
The decision, the alternatives rejected and the declared limits are
[[0030-point-at-the-near-miss-never-resolve-it]].

That column is the half no command can print: a domain is a decision about what a
source is *for*. What each source actually **defines** is measured
`Scripts/amalgamate.py:census_sources`, and the two tables are deliberately not
merged into one. The rendered one is the authority on which sources exist, so a
source appearing there with no row above it is a domain nobody has argued yet —
which is the question [[0014-a-canonical-source-is-a-domain]] makes a person
answer, and the one thing this page must not let a generator answer for them.

<!-- BEGIN MEASURED: canonical-source-blocks -->
| Canonical source | Blocks | Block names |
|---|---|---|
| `Scripts/_mcp_brotli.py` | 21 | `BR_CHUNK_BYTES`, `BR_DIRS_LINUX`, `BR_DIRS_MACOS`, `BR_FILES_LINUX`, `BR_FILES_MACOS`, `BR_SONAMES_LINUX`, `BR_SONAMES_MACOS`, `_BR_RESULT_ERROR`, `_BR_RESULT_NEEDS_MORE_INPUT`, `_BR_RESULT_NEEDS_MORE_OUTPUT`, `_BR_RESULT_SUCCESS`, `_BR_STATE`, `_BR_SYMBOLS`, `_br_attempts`, `_br_cdll`, `_br_configure`, `_br_exists`, `_br_find_library`, `_br_load`, `_br_platform`, `_brotli_decompress` |
| `Scripts/_mcp_chrome.py` | 137 | `CHROME_PROFILE`, `CH_DEFAULT_DECODE_CAP`, `CH_HPACK_TABLE_BYTES`, `CH_MAX_BODY_BYTES`, `CH_MAX_CONTENT_CODINGS`, `CH_MAX_COOKIES`, `CH_MAX_COOKIES_PER_DOMAIN`, `CH_MAX_COOKIE_BYTES`, `CH_MAX_FRAME_BYTES`, `CH_MAX_H1_CHUNK_LINE_BYTES`, `CH_MAX_H1_HEADERS`, `CH_MAX_H1_HEAD_BYTES`, `CH_MAX_H2_CONTINUATION_FRAMES`, `CH_MAX_H2_CONTROL_FRAMES`, `CH_MAX_H2_EMPTY_FRAMES`, `CH_MAX_HANDSHAKE_BYTES`, `CH_MAX_HEADER_BLOCK_BYTES`, `CH_MAX_HEADER_LIST_BYTES`, `CH_MAX_HPACK_INT`, `CH_MAX_HPACK_INT_CONTINUATIONS`, `CH_MAX_KEY_UPDATES`, `CH_MAX_PLAINTEXT_BYTES`, `CH_MAX_RECORD_BYTES`, `CH_MAX_REDIRECTS`, `ChromeBodyTooLarge`, `ChromeClientError`, `ChromeTls12Error`, `_CH_AES_TABLES`, `_CH_ALERT_NAMES`, `_CH_BAD_PORTS`, `_CH_EE_FORBIDDEN`, `_CH_FINGERPRINT_NAMES`, `_CH_FINGERPRINT_PREFIXES`, `_CH_FRAMING_NAMES`, `_CH_FRAMING_PREFIXES`, `_CH_H2_ERROR_NAMES`, `_CH_HPACK_STATIC`, `_CH_HRR_RANDOM`, `_CH_HUFFMAN`, `_CH_HUFFMAN_DECODE`, `_CH_KEY_SHARE_BYTES`, `_CH_P256_B`, `_CH_P256_GX`, `_CH_P256_GY`, `_CH_P256_N`, `_CH_P256_P`, `_CH_PUBLIC_SUFFIXES`, `_CH_REDIRECT_CODES`, `_CH_SERVER_SHARE_BYTES`, `_CH_SITE_RANK`, `_CH_TCHAR_SYMBOLS`, `_CH_TLS13_SUITES`, `_CH_TRANSLATION_PREFIXES`, `_CH_X25519_A24`, `_CH_X25519_P`, `_ChAesGcm`, `_ChChaCha20Poly1305`, `_ChCookieJar`, `_ChDeadlineSocket`, `_ChFallbackConnection`, `_ChH1Connection`, `_ChH2Connection`, `_ChHandshakeReader`, `_ChHeaders`, `_ChHpackDecoder`, `_ChHpackEncoder`, `_ChMlKem768`, `_ChReader`, `_ChRecordCipher`, `_ChRecordReader`, `_ChResponse`, `_ChSession`, `_ChTls`, `_ChTlsStream`, `_ch_address_refused`, `_ch_aes_tables`, `_ch_alert_name`, `_ch_check_caller_headers`, `_ch_client_hello`, `_ch_cookie_crumbs`, `_ch_cookie_date`, `_ch_decode_body`, `_ch_derive_secret`, `_ch_draw`, `_ch_embedded_ipv4`, `_ch_grease`, `_ch_h2_frame`, `_ch_header_pairs`, `_ch_hello_extensions`, `_ch_hello_wire`, `_ch_hkdf_expand`, `_ch_hkdf_expand_label`, `_ch_hkdf_extract`, `_ch_hpack_decode_int`, `_ch_hpack_decode_string`, `_ch_hpack_int`, `_ch_hpack_static`, `_ch_hpack_string`, `_ch_huffman_decode`, `_ch_huffman_decode_table`, `_ch_huffman_encode`, `_ch_huffman_size`, `_ch_huffman_table`, `_ch_idna_encode`, `_ch_is_ip_host`, `_ch_is_public_suffix`, `_ch_key_share_entry`, `_ch_key_share_new`, `_ch_key_share_secret`, `_ch_leading_digits`, `_ch_open_socket`, `_ch_origin_of`, `_ch_origin_text`, `_ch_p256_add`, `_ch_p256_double`, `_ch_p256_keypair`, `_ch_p256_mul`, `_ch_p256_shared`, `_ch_parse_server_hello`, `_ch_permutation`, `_ch_profile_headers`, `_ch_public_only_policy`, `_ch_referer_for`, `_ch_sec_fetch_site`, `_ch_session_new`, `_ch_site_of`, `_ch_sni_name`, `_ch_split_url`, `_ch_traffic_cipher`, `_ch_transport_error`, `_ch_unvetted_policy`, `_ch_vec`, `_ch_x25519`, `_ch_x25519_cswap`, `_ch_x25519_keypair`, `_ch_zlib_decode`, `_chrome_profile` |
| `Scripts/_mcp_codesearch.py` | 19 | `EXT_TO_LANG`, `_CODE_LINE_SEPARATORS`, `_SnippetParser`, `_body_is_json`, `_code_clean`, `_code_fence`, `_code_field`, `_code_line`, `_ext_to_lang`, `_grep_app_blocked`, `_grep_hits`, `_grep_str`, `build_github_url`, `detect_language`, `extract_code_from_snippet`, `format_code_results`, `parse_grep_results`, `search_github`, `warmup_code_session` |
| `Scripts/_mcp_concurrency.py` | 1 | `MAX_INFLIGHT_REQUESTS` |
| `Scripts/_mcp_dispatch.py` | 1 | `_did_you_mean` |
| `Scripts/_mcp_httpfront.py` | 11 | `_HTTP_STDLIB_REFUSAL_HEADERS`, `_HeaderDeadlineReader`, `_http_remove_ready_file`, `_http_token_value`, `_http_write_ready_file`, `_single_header`, `handle_error`, `handle_expect_100`, `parse_request`, `process_request`, `server_bind` |
| `Scripts/_mcp_json.py` | 12 | `JSON_INT_LITERAL_LIMIT`, `_bool_param`, `_ensure_dict`, `_error`, `_int_param`, `_json_bounded_int`, `_json_error_window`, `_json_finite_float`, `_json_no_constant`, `_result`, `_strict_dumps`, `_strict_loads` |
| `Scripts/_mcp_logging.py` | 4 | `_LOG_KEYS_SHOWN`, `_LOG_VALUE_WIDTH`, `_configure_logging`, `_log_value` |
| `Scripts/_mcp_lsp.py` | 7 | `_abs_path`, `_abs_uri`, `_notify`, `_request`, `encode_lsp_message`, `path_to_uri`, `uri_to_path` |
| `Scripts/_mcp_oauth.py` | 55 | `OAUTH_B64URL_ALPHABET`, `OAUTH_BODY_LIMIT`, `OAUTH_CALLBACK_BAD_LIMIT`, `OAUTH_CALLBACK_CONN_TIMEOUT_S`, `OAUTH_CALLBACK_HEAD_LIMIT`, `OAUTH_CODE_ALPHABET`, `OAUTH_ERROR_CODES`, `OAUTH_ERROR_KINDS`, `OAUTH_ID_ALPHABET`, `OAUTH_INT_LITERAL_LIMIT`, `OAUTH_JWT_LIMIT`, `OAUTH_MIN_REFRESH_INTERVAL_S`, `OAUTH_REFRESH_SKEW_S`, `OAUTH_RELOGIN_CODES`, `OAUTH_TOKEN_LIMIT`, `OAUTH_TRANSIENT_CODES`, `OAuthError`, `OAuthProvider`, `_oauth_account_claims`, `_oauth_authorize_url`, `_oauth_b64url`, `_oauth_b64url_decode`, `_oauth_bounded_int`, `_oauth_callback_page`, `_oauth_callback_verdict`, `_oauth_check_id_token`, `_oauth_device_interval`, `_oauth_device_poll_request`, `_oauth_device_start_request`, `_oauth_error_code`, `_oauth_error_refusal`, `_oauth_exchange_request`, `_oauth_form_headers`, `_oauth_id_ok`, `_oauth_json_headers`, `_oauth_json_object`, `_oauth_jwt_claims`, `_oauth_listen`, `_oauth_new_state`, `_oauth_no_constant`, `_oauth_no_duplicate_keys`, `_oauth_pairs_ok`, `_oauth_parse_callback`, `_oauth_parse_device_poll`, `_oauth_parse_device_start`, `_oauth_parse_token_response`, `_oauth_pkce_pair`, `_oauth_refresh_request`, `_oauth_scope_has`, `_oauth_state_matches`, `_oauth_status_refusal`, `_oauth_sync_accept_callback`, `_oauth_token_due`, `_oauth_token_ok`, `_oauth_uuid4_urn` |
| `Scripts/_mcp_paging.py` | 7 | `DEFAULT_MAX_ANSWER_CHARS`, `DEFAULT_MAX_CHARS`, `PAGE_LINE_RESERVE`, `_FENCE_LINE_RE`, `_max_answer_chars`, `_offset`, `_rows_note` |
| `Scripts/_mcp_websearch.py` | 37 | `_DDG_CHALLENGE_MARKERS`, `_END_PRIORITY`, `_END_PRIORITY_DEFAULT`, `_FONTSTYLE`, `_H`, `_LISTING`, `_LiteParser`, `_Node`, `_START_CLOSE`, `_TREE_MAX_DEPTH`, `_TREE_SCAN_BUDGET`, `_TreeBuilder`, `_VOID_TAGS`, `_WEB_LINE_SEPARATORS`, `_WEB_URL_SAFE`, `_bing_blocked`, `_child_elements`, `_ddg_blocked`, `_decode_bing_url`, `_descendant_text`, `_end_priority`, `_has_class`, `_iter_elements`, `_normalize`, `_raw_href`, `_start_close`, `_web_clean`, `_web_line`, `_web_url`, `decode_duckduckgo_url`, `format_web_results`, `parse_bing_results`, `parse_lite_results`, `run_web`, `search_bing`, `search_ddg`, `warmup_session` |
| `Scripts/_mcp_websocket.py` | 22 | `WS_MAX_FRAME_BYTES`, `WS_MAX_HANDSHAKE_BYTES`, `WS_MAX_MESSAGE_BYTES`, `WebSocketError`, `_WsConnection`, `_ws_assemble`, `_ws_connect`, `_ws_control_reply`, `_ws_encode_frame`, `_ws_handshake_request`, `_ws_handshake_split`, `_ws_handshake_verify`, `_ws_mask`, `_ws_parse_frame`, `_ws_parse_url`, `_ws_recv`, `_ws_send`, `_ws_step`, `_ws_sync_close`, `_ws_sync_connect`, `_ws_sync_recv`, `_ws_sync_send` |
| `Scripts/_mcp_zstd.py` | 25 | `ZSTD_DIRS_LINUX`, `ZSTD_DIRS_MACOS`, `ZSTD_FILES_LINUX`, `ZSTD_FILES_MACOS`, `ZSTD_MAX_CHUNK_BYTES`, `ZSTD_SONAMES_LINUX`, `ZSTD_SONAMES_MACOS`, `ZSTD_WINDOW_LOG_MAX`, `_ZSTD_D_WINDOW_LOG_MAX`, `_ZSTD_FRAME_MAGIC`, `_ZSTD_SKIPPABLE_TAIL`, `_ZSTD_STATE`, `_ZSTD_SYMBOLS`, `_ZstdInBuffer`, `_ZstdOutBuffer`, `_zstd_attempts`, `_zstd_cdll`, `_zstd_check_magic`, `_zstd_configure`, `_zstd_decompress`, `_zstd_error`, `_zstd_exists`, `_zstd_find_library`, `_zstd_load`, `_zstd_platform` |

14 canonical sources define 359 blocks between them, and no name is defined by two of them.
<!-- END MEASURED: 7fbff8b05e7b -->

Its closing line is the disjointness the suite gates as a check rather than a
count. The paging row read `5` until the edit that added the logging row: the
number was left behind when `_max_answer_chars` was lifted, and a stale figure
sitting beside a new row is worse than one sitting alone. The moves since
are genuine arrivals rather than corrections — `_ensure_dict` in the JSON row,
`DEFAULT_MAX_CHARS` in the paging one, the DocumentUri pair then the client
half in the LSP one, then the six strict-JSON blocks in the JSON row (`d4a241b`,
R-0067/R-0068) and `_log_value` with its two bounds in the logging row
(`72ed9b4`, R-0070).

**The strict-JSON blocks are new code, not a lift.** `JSON_INT_LITERAL_LIMIT`
(4300 characters, sign included, checked before `int()` sees the literal), the
three `json.loads` hooks `_json_no_constant`, `_json_finite_float` and
`_json_bounded_int`, then `_strict_loads` and `_strict_dumps`
`Scripts/_mcp_json.py:_strict_loads`. The stdlib reads and writes `NaN`,
`Infinity` and `-Infinity` (and reads `1e999` as an infinity), which no strict
JSON peer can parse (R-0067), and the int-digit limit that answers
CVE-2020-10735 exists only from Python 3.9.14, so on the macOS system Python
3.9.6 a long run of digits parses at quadratic cost (R-0068). `_strict_loads`
raises every refusal as a `json.JSONDecodeError` at the refused literal's
position, because that is the one exception every host's frame loop and
`_ensure_dict` already catch: a refused number gets the same `-32700` an
unparseable line gets, and `exc.pos` still feeds `_json_error_window`.
`_strict_dumps` is `json.dumps` with `allow_nan=False` and nothing else, so every
finite frame is byte-identical. The six call one another, so they travel as one
run on one marker, dependency first; and because `_ensure_dict` now parses
through `_strict_loads`, a host that takes `_ensure_dict` spells all eight on one
marker — the six, then `_json_error_window, _ensure_dict`. `Scripts/mcp-proxy.py`,
which carries no `_json_error_window`, took the six as a new region of their own,
and routes its child reader and its HTTP front through them as well as its
stdio loop ([[mcp-proxy]]). All 17 servers carry
the six and route their frame parse and frame emit through them — and, where
they have them, a stringified `arguments` and the `_resolve_aliases` params. Their bound and refusal style mirror
`_rt_loads` in the router and `_oauth_json_object` in the OAuth source, which
were written first and stay their own.

**`_log_value` is the logging source's second block, and it is a renderer, not a
site.** The wire log logs structure only, but structure — a method, an id, a
tool name, the argument keys — is still text the peer chose, and one holding a
line break writes a forged line into the log (CWE-117; security review
2026-10-05, F19). `mcp-proxy`, `mcp-search` and the router each carried a
hand-written copy with the same behaviour; the other servers logged the fields
raw. It belongs to the logging domain because it decides how a value is written
into a log line, and it reads no `log` and calls no logger, so the reason the
wire sites stay out (below) does not touch it `Scripts/_mcp_logging.py:_log_value`.
It reads its two bounds, `_LOG_VALUE_WIDTH` and `_LOG_KEYS_SHOWN`, so every host
spells `_LOG_VALUE_WIDTH, _LOG_KEYS_SHOWN, _log_value` on one marker — the one
marker in the fleet that puts two constants side by side, which the renderer
accepts with two blank lines between them `Scripts/amalgamate.py:render`. It is
generated into all 17 servers and the router, and the three hand copies are
gone.

Which files each of those blocks actually reaches is the other half of the
picture, and the generator counts that too `Scripts/amalgamate.py:census_hosts`:
one row per canonical block, whether or not any host takes it. It counts the
hand-declared non-server hosts as well, so it is the table to read for a single
block's reach, while the fleet census above stays about the servers. A whole
source is the one exception to one-row-per-block: when every block of a
`WHOLE_SOURCES` source reaches the same hosts, the census collapses it into ONE
row reading `all <N> blocks (whole source)` `Scripts/amalgamate.py:census_hosts`,
so a whole source shown here per block is a host that took it partially — a
visible violation of the whole-or-nothing rule, not a presentation choice.

<!-- BEGIN MEASURED: canonical-block-hosts -->
| Canonical block | Source | Hosts | Generated into |
|---|---|---|---|
| `all 21 blocks (whole source)` | `Scripts/_mcp_brotli.py` | 4 | `Scripts/mcp-search.py`, `Scripts/mcp-webfetch.py`, `Scripts/search_duckduckgo.py`, `Scripts/search_github.py` |
| `all 137 blocks (whole source)` | `Scripts/_mcp_chrome.py` | 4 | `Scripts/mcp-search.py`, `Scripts/mcp-webfetch.py`, `Scripts/search_duckduckgo.py`, `Scripts/search_github.py` |
| `all 19 blocks (whole source)` | `Scripts/_mcp_codesearch.py` | 2 | `Scripts/mcp-search.py`, `Scripts/search_github.py` |
| `MAX_INFLIGHT_REQUESTS` | `Scripts/_mcp_concurrency.py` | 10 | `Scripts/mcp-context7.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_did_you_mean` | `Scripts/_mcp_dispatch.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_HTTP_STDLIB_REFUSAL_HEADERS` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `_HeaderDeadlineReader` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `_http_remove_ready_file` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `_http_token_value` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `_http_write_ready_file` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `_single_header` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `handle_error` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `handle_expect_100` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `parse_request` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `process_request` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `server_bind` | `Scripts/_mcp_httpfront.py` | 2 | `Scripts/llm-router.py`, `Scripts/mcp-proxy.py` |
| `JSON_INT_LITERAL_LIMIT` | `Scripts/_mcp_json.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_bool_param` | `Scripts/_mcp_json.py` | 11 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-wiki.py` |
| `_ensure_dict` | `Scripts/_mcp_json.py` | 8 | `Scripts/mcp-context7.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-search.py`, `Scripts/mcp-webfetch.py` |
| `_error` | `Scripts/_mcp_json.py` | 16 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-wiki.py` |
| `_int_param` | `Scripts/_mcp_json.py` | 5 | `Scripts/mcp-jenkins.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py` |
| `_json_bounded_int` | `Scripts/_mcp_json.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_json_error_window` | `Scripts/_mcp_json.py` | 16 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_json_finite_float` | `Scripts/_mcp_json.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_json_no_constant` | `Scripts/_mcp_json.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_result` | `Scripts/_mcp_json.py` | 16 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-wiki.py` |
| `_strict_dumps` | `Scripts/_mcp_json.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_strict_loads` | `Scripts/_mcp_json.py` | 17 | `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_LOG_KEYS_SHOWN` | `Scripts/_mcp_logging.py` | 18 | `Scripts/llm-router.py`, `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_LOG_VALUE_WIDTH` | `Scripts/_mcp_logging.py` | 18 | `Scripts/llm-router.py`, `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_configure_logging` | `Scripts/_mcp_logging.py` | 18 | `Scripts/llm-router.py`, `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_log_value` | `Scripts/_mcp_logging.py` | 18 | `Scripts/llm-router.py`, `Scripts/mcp-clangd.py`, `Scripts/mcp-context7.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-proxy.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-tshark.py`, `Scripts/mcp-webfetch.py`, `Scripts/mcp-wiki.py` |
| `_abs_path` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `_abs_uri` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `_notify` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `_request` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `encode_lsp_message` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `path_to_uri` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `uri_to_path` | `Scripts/_mcp_lsp.py` | 4 | `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py`, `Scripts/mcp-lua-lsp.py`, `Scripts/mcp-purity.py` |
| `OAUTH_B64URL_ALPHABET` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_BODY_LIMIT` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_CALLBACK_BAD_LIMIT` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_CALLBACK_CONN_TIMEOUT_S` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_CALLBACK_HEAD_LIMIT` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_CODE_ALPHABET` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_ERROR_CODES` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_ERROR_KINDS` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_ID_ALPHABET` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_INT_LITERAL_LIMIT` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_JWT_LIMIT` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_MIN_REFRESH_INTERVAL_S` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_REFRESH_SKEW_S` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_RELOGIN_CODES` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_TOKEN_LIMIT` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAUTH_TRANSIENT_CODES` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAuthError` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `OAuthProvider` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_account_claims` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_authorize_url` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_b64url` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_b64url_decode` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_bounded_int` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_callback_page` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_callback_verdict` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_check_id_token` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_device_interval` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_device_poll_request` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_device_start_request` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_error_code` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_error_refusal` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_exchange_request` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_form_headers` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_id_ok` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_json_headers` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_json_object` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_jwt_claims` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_listen` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_new_state` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_no_constant` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_no_duplicate_keys` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_pairs_ok` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_parse_callback` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_parse_device_poll` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_parse_device_start` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_parse_token_response` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_pkce_pair` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_refresh_request` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_scope_has` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_state_matches` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_status_refusal` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_sync_accept_callback` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_token_due` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_token_ok` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `_oauth_uuid4_urn` | `Scripts/_mcp_oauth.py` | 1 | `Scripts/llm-router.py` |
| `DEFAULT_MAX_ANSWER_CHARS` | `Scripts/_mcp_paging.py` | 7 | `Scripts/mcp-context7.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py`, `Scripts/mcp-webfetch.py` |
| `DEFAULT_MAX_CHARS` | `Scripts/_mcp_paging.py` | 5 | `Scripts/mcp-forge.py`, `Scripts/mcp-gdc.py`, `Scripts/mcp-git.py`, `Scripts/mcp-inspect.py`, `Scripts/mcp-wiki.py` |
| `PAGE_LINE_RESERVE` | `Scripts/_mcp_paging.py` | 5 | `Scripts/mcp-context7.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-webfetch.py` |
| `_FENCE_LINE_RE` | `Scripts/_mcp_paging.py` | 5 | `Scripts/mcp-context7.py`, `Scripts/mcp-forge.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-tshark.py` |
| `_max_answer_chars` | `Scripts/_mcp_paging.py` | 6 | `Scripts/mcp-context7.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-search.py` |
| `_offset` | `Scripts/_mcp_paging.py` | 5 | `Scripts/mcp-context7.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py`, `Scripts/mcp-tshark.py` |
| `_rows_note` | `Scripts/_mcp_paging.py` | 5 | `Scripts/mcp-context7.py`, `Scripts/mcp-jenkins.py`, `Scripts/mcp-lldb.py`, `Scripts/mcp-postgres.py`, `Scripts/mcp-purity.py` |
| `all 37 blocks (whole source)` | `Scripts/_mcp_websearch.py` | 2 | `Scripts/mcp-search.py`, `Scripts/search_duckduckgo.py` |
| `WS_MAX_FRAME_BYTES` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `WS_MAX_HANDSHAKE_BYTES` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `WS_MAX_MESSAGE_BYTES` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `WebSocketError` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_WsConnection` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_assemble` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_connect` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_control_reply` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_encode_frame` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_handshake_request` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_handshake_split` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_handshake_verify` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_mask` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_parse_frame` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_parse_url` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_recv` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_send` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_step` | `Scripts/_mcp_websocket.py` | 1 | `Scripts/mcp-gdc.py` |
| `_ws_sync_close` | `Scripts/_mcp_websocket.py` | 0 | no host |
| `_ws_sync_connect` | `Scripts/_mcp_websocket.py` | 0 | no host |
| `_ws_sync_recv` | `Scripts/_mcp_websocket.py` | 0 | no host |
| `_ws_sync_send` | `Scripts/_mcp_websocket.py` | 0 | no host |
| `all 25 blocks (whole source)` | `Scripts/_mcp_zstd.py` | 4 | `Scripts/mcp-search.py`, `Scripts/mcp-webfetch.py`, `Scripts/search_duckduckgo.py`, `Scripts/search_github.py` |

20 hosts scanned; 359 canonical blocks, of which 355 are generated into at least one host; generated into no host: `_ws_sync_close`, `_ws_sync_connect`, `_ws_sync_recv`, `_ws_sync_send`.
<!-- END MEASURED: f1f2e2c800e9 -->

`DEFAULT_MAX_CHARS` is worth naming here because of the state its first hosts
were found in when `0f05101` lifted it. `mcp-git.py`, `mcp-inspect.py` and
`mcp-wiki.py` each wrote `100_000` — ADR 0013's verbatim-artefact class — and each additionally asserted
*in prose* that its number and spelling matched the other two. That is agreement
claimed on disk in triplicate, and it is the costly form: correcting one of the
three leaves two comments lying about it. The constant is generated now and the
cross-references are gone; what stayed above each marker is the only
host-specific half, why that server is in the class at all. None of the hosts
the `canonical-block-hosts` table lists for `DEFAULT_MAX_CHARS` takes the
`DEFAULT_MAX_ANSWER_CHARS, _max_answer_chars` pair, and that is not an
oversight — the pair renders the reader together with its own `24000`
`Scripts/_mcp_paging.py:DEFAULT_MAX_ANSWER_CHARS`, so taking the marker would
take the value.

The logging source is the only one whose domain is defined by what it
EXCLUDES. Configuring logging, and rendering one peer-chosen value for a log
line (`_log_value`, above), are not the same question as what gets
logged: the wire log is a security invariant decided in
[[0011-a-truncated-payload-carries-the-first-cookie]] and gated by
`tests/test_wire_log.py`, and that ADR already rejected lifting `_write` into a
region on a ground that still holds — `host_provides` offers only the host's
module-level imports, `log` is a module-level *assignment*, so any block reading
it is refused by name. `_configure_logging` clears that bar by never reading
`log`: it is a module-level `def` whose free names are `logging`, `os` and `sys`.
The logger object itself stays hand-written because its NAME is the one thing
that differs from copy to copy, and the `--debug` / `--log-file`
declarations stay hand-written because `mcp-webfetch`'s `-v` / `--verbose`
aliases are a feature, not drift.

The source name on a marker **selects the block map**: a name is resolved against
that source and no other, and an unknown source is refused by name with the known
ones listed `Scripts/amalgamate.py:audit_text`.

## What may be a block — four constraints

**A function, a class, or a single-target constant.** `load_blocks_text` walks a
source's `tree.body` and takes `FunctionDef`, `AsyncFunctionDef`, `ClassDef` —
and, since the constants were lifted, an `ast.Assign` whose one target is a bare
`ast.Name` `Scripts/amalgamate.py:load_blocks_text`. Every other assignment shape
is turned down, each on its own grounds `Scripts/amalgamate.py:assign_name`:
`A = B = 1` and `A, B = f()` bind several names from one indivisible slice, so
the region would define a name its marker never mentions; `x.attr = 1` and
`x[0] = 1` bind no name to key the map on. **`X += 1` is the load-bearing
refusal**, and it is structural rather than stylistic — its target carries a
`Store` context, so `free_names` *binds* `X` and reports no requirement at all,
while the statement cannot run unless the host already defines `X`. A block whose
precondition is invisible to the contract check is the exact failure that check
exists to catch, so the shape is refused rather than handed to a check that
cannot see it. `X: int = 1` is out on demand rather than on principle: no queued
block is one, and admitting the form would also admit a bare `X: int`, which
binds nothing at run time and fails at the host's first read. Both arms — what is
taken and what is turned down — are pinned by `single-name-assign-only`
`tests/test_generated_region.py:group_control`.

An unextractable shape is **skipped, not raised on**, and that follows from the
loader having two callers. It is not only the canonical-source reader: the
hand-copy census points it at every server in the fleet
`tests/test_generated_region.py:group_gate`, so raising would turn an ordinary
module-level statement in any server into a traceback naming the wrong subject
entirely. Nothing is lost to the skip, because a name only enters the system by
being written on a marker, and an unknown one is already refused by name with the
source it was asked for `Scripts/amalgamate.py:render`.

A method inside a class still cannot be a block — the walk is over `tree.body`
and nothing else — which is why every block that lands inside a class body is
written at module top level in its canonical source: `_result` and `_error` in
the JSON source, and the four client-side methods in the LSP source, whose `self`
is an ordinary first parameter the generator has nothing to check about
`Scripts/_mcp_lsp.py`.

**The host must already import every free name.** `free_names` over-reports
deliberately, and it catches two holes that were once live: a block calling
another canonical block, and a block whose *annotation* needs an import — an
annotation is evaluated at def time, so a host missing the name dies at startup
while the block looks fine where it is written. The host half,
`host_provides`, returns **module-level import aliases only** — not defs, not
assignments. A free name the host *defines* is therefore still a refusal, and the
region is rejected by name at END-marker time `Scripts/amalgamate.py:host_provides`.

The remedy is not to import the name but to **co-list it on the same marker**:
`free_names` runs over the whole rendered region, so a block defined beside its
caller is *bound* rather than free `Scripts/amalgamate.py:audit_text`. That is
why `_max_answer_chars` travels with its constant, `_log_value` with its two
bounds, and `_ensure_dict` with `_json_error_window` — and, since R-0067/R-0068,
behind the six strict-JSON blocks it parses through — dependency first in every
case. The `_ensure_dict` pairing is the
instructive one, because it moved a cost into the test suite rather than the
generator: in `mcp-forge.py`, `mcp-git.py` and `mcp-inspect.py`, `_ensure_dict`
was the *only* caller of the window, so the moment it went inside the region
those three had no window call site outside one — and the liveness census, which
deliberately ignores calls inside a region, had to start asking whether the
**pair** is reached instead of whether one name is
`tests/test_generated_region.py:group_gate`. Co-listing is cheap at the marker
and is not free further out.

The remedy has a hard boundary, and it follows from where the blocks land rather
than from any check: co-listing works for a **module-level** region and cannot
work for an indented one. Every block in a region is emitted at the marker's own
column `Scripts/amalgamate.py:render`, so a dependency co-listed into a class
body arrives as a class member — where the block's own unqualified global lookup
would never find it. An indented region's free names must therefore be satisfied
by the host's imports alone, and a block whose dependency is a module-level `def`
has no remedy at that column at all.

Admitting constants needed nothing added here, and that is a property of the walk
rather than luck: `ast.walk` descends the whole tree and every binder is matched
by node type, so a module-level statement is analysed on the same terms as a
`def` body `Scripts/amalgamate.py:free_names`. `_FENCE_LINE_RE` is the first
*constant* to spend the budget — it reads `re`, and carries that import
requirement to every host its row in the `canonical-block-hosts` table names. It
is not the first block to carry one at all: `_result`'s `msg_id: Any` has
demanded `typing.Any` of every host that table names for it since long before, which is the annotation hole named above. What is new is the route,
not the requirement — a plain read in an executable statement rather than an
annotation. The refusal used to fire only incidentally, because every live region
passes it; `free-name-refusal` now drives it directly, on a constant, against one
host that imports `re` and one that does not
`tests/test_generated_region.py:group_control`.

**Tab safety is decided per block, mechanically.** `block_is_tab_safe` requires
both that no line join happens while a bracket is open and that every leading
whitespace run is a whole multiple of four; anything unprovable is unsafe
`Scripts/amalgamate.py:block_is_tab_safe`. The host's style is read from its own
indent tokens, not from the marker's column. This **narrowed an earlier per-file
rule** that refused tab hosts outright and cost one server three hand copies that
were byte-identical modulo the indent character.

Neither the unsafe set nor the tab hosts are tallied here, because the suite
measures both on every run: `tab-safety-real-blocks` asserts the unsafe set is
exactly `_rows_note` over the real canonical text, and `host-indent-from-tokens`
that the tab-indented servers are exactly `mcp-forge.py` and `mcp-webfetch.py`
`tests/test_generated_region.py:group_tabs`, and — in the same case — that the
two search scripts among the declared hosts `Scripts/amalgamate.py:DECLARED_HOSTS`
are tab-indented, and `llm-router.py` is a space host. A block added to a source moves that set or
fails there. The websocket source was written for the rule from the start: its tab
host was the DDG search script, so every one of its blocks fits one call per
physical line, the request built by appending rather than as one bracketed list,
and it stays that way now that `mcp-gdc` is its only host. The Chrome client and
its decoders were written under the same constraint for the same reason — every
host they had was tab-indented until the space-indented `Scripts/mcp-search.py`
joined them — so their long byte tables are built by `+=` inside a function
rather than as one bracketed literal `Scripts/_mcp_chrome.py`. The two search
sources are the rule's newest customers: each has a tab-indented CLI host, so
every multi-line table in them is a builder function plus one single-target
assignment, `_start_close` / `_START_CLOSE` and `_ext_to_lang` / `EXT_TO_LANG`
`Scripts/_mcp_websearch.py:_start_close` `Scripts/_mcp_codesearch.py:_ext_to_lang`,
because the generator silently drops a module-level subscript assignment.
The refusal therefore bites in one place: `mcp-webfetch` is
the only tab host that carries `_rows_note` at all, and it keeps its own —
excluded twice over, tab-unsafe *and* body-diverged, so clearing the tab hazard
alone would not make it adoptable. Lifting the constants did not widen that
surface even though it grew the block count, because a constant has no leading
whitespace on any line and the tab conversion is a no-op for it.

The rule's larger effect is not the refusals it issues but the **code it shapes
before one is ever issued**. Both blocks extracted most recently had to be
rewritten on the way in: `_configure_logging` and `_ensure_dict` each built a
call across several physical lines in every hand copy, which is an implicit line
join, and each has a tab-indented host — so the verbatim text would have been
refused by name for `mcp-forge.py` and `mcp-webfetch.py` and quietly accepted
everywhere else. Both now fit one call per physical line, and both say so in
their canonical source, because a **reshape** and a **lift** make different
promises: a lift's first generated diff is marker lines only, and reading a
reshape as a botched lift is the misreading the declaration exists to prevent
`Scripts/_mcp_json.py`.

**A name must resolve against the source that was named.** Cross-source
resolution is refused, and the suite gates both halves — the refusal *and* a
positive case proving the right body was selected, because "refused" alone could
be satisfied by a merged namespace that got lucky.

## A host outside the glob

The generator's targets were `Scripts/mcp-*.py` and nothing else until the
websocket source arrived with a consumer that is not a server.
`Scripts/amalgamate.py:DECLARED_HOSTS` is the widening, and it is a hand-written
tuple of filenames for the reason `CANONICAL_NAMES` is: a wider glob would
make every script in `Scripts/` a target by merely existing. A declared host
takes the default run and `--check` exactly as a server does, and
`tests/test_generated_region.py` mirrors the tuple, asserts the two spellings
agree in `target-glob`, and walks the declared hosts in `fleet-ok` beside the
servers.

`Scripts/search_github.py` is the second declared host, and it came with the
Chrome client rather than the WebSocket one. Since `8dde3a6` the two declared
hosts are symmetric: each takes the three whole Chrome sources — the client and
both decoders — plus its own search domain, whole, and nothing else.
`Scripts/search_duckduckgo.py` takes `Scripts/_mcp_websearch.py`;
`Scripts/search_github.py` takes `Scripts/_mcp_codesearch.py`. Neither takes the
WebSocket client any more: the DDG script's `cdp` backend, the one consumer of the
blocking-socket wrapper, was removed. Both pass the same `--check`, are walked by
the same `fleet-ok`, and are counted by the `canonical-block-hosts` table above
rather than by the fleet census.

`Scripts/llm-router.py` is the third declared host, and the first that shares no
domain with the other two: a stdlib HTTP server run by path, not an MCP server,
and space-indented. It takes three sources: two regions from the logging source —
`_configure_logging`, and `_LOG_VALUE_WIDTH, _LOG_KEYS_SHOWN, _log_value` on one
marker, which replaced the router's own hand copy in `72ed9b4` — the OAuth
client from `Scripts/_mcp_oauth.py` on one marker, the sans-IO core then its
loopback wrapper, and the HTTP front from `Scripts/_mcp_httpfront.py` in six
regions, two of them inside its server and handler classes, exactly as
`Scripts/mcp-proxy.py` takes it. It takes none of the strict-JSON blocks: its request bodies
are parsed by its own `_rt_loads` `Scripts/llm-router.py:_rt_loads`. It did not have to be declared again
for the second source, because the host list names files, not sources. The `_mcp_chrome.py`
address classifier it needs for its upstream connections is kept as declared
hand copies rather than generated — the router takes a few of that source's
blocks, and the Chrome client is a source a host takes whole or not at all — so
the hand-copy census reports those copies with their declared reason instead of
as `[UNDECLARED]`.

They are **not** in the `--census fleet` count, deliberately: that census is
about the server fleet and says so in its first line, so its measured block above
counts the servers only. A block generated only into a declared host, or into no
host at all, therefore shows up there as defined-but-not-named-on-a-server-marker,
which is true. The four `_ws_sync_*` blocks are the standing case of the second
kind: they stay in `Scripts/_mcp_websocket.py` with no host, and the
`canonical-block-hosts` table reports them that way rather than failing on them
([[0023-the-websocket-client-is-a-sixth-domain]], addendum).

Why a declared host rather than an import is the servers' argument, with one
reason sharper: agents run the search script by path, without `-B`, so an
imported sibling would write `Scripts/__pycache__` on every search — into the
tree the bytecode-snapshotting suites assert stays empty — and a copy of the one
file taken on its own would stop at an `ImportError` —
[[0023-the-websocket-client-is-a-sixth-domain]].

## Two registers of deliberate exclusion

The rule is not "duplication is bad". Two distinct kinds of copy are left in
place on stated grounds, and they are counted differently.

**Hand copies of things that ARE blocks** are censused by the generator
`Scripts/amalgamate.py:hand_copies`: it intersects the names every host binds at
module level or as a direct class member with the canonical block names,
subtracts what regions cover, and the suite reports the remainder as INFO rather
than FAIL — "this host keeps its own" is a legitimate answer, but an
*undeclared* copy cannot appear without landing on that line. Each surviving copy
carries the measured reason declared for it in
`Scripts/amalgamate.py:HAND_COPY_REASONS`, and the census below renders them
together, marking any copy with no reason UNDECLARED and naming any reason whose
copy has gone:

<!-- BEGIN MEASURED: hand-copy-census -->
- `Scripts/llm-router.py`: `_CH_TRANSLATION_PREFIXES` -- declared: _mcp_chrome.py is a WHOLE source (G-c); the router needs its address classifier and none of the client
- `Scripts/llm-router.py`: `_ch_address_refused` -- declared: _mcp_chrome.py is a WHOLE source (G-c); the router needs its address classifier and none of the client
- `Scripts/llm-router.py`: `_ch_embedded_ipv4` -- declared: _mcp_chrome.py is a WHOLE source (G-c); the router needs its address classifier and none of the client
- `Scripts/mcp-git.py`: `_max_answer_chars` -- declared: excluded twice over: it defaults to its own DEFAULT_MAX_CHARS rather than to the value the canonical block renders its reader with, and its body carries a camelCase fallback loop the canonical has no trace of
- `Scripts/mcp-inspect.py`: `_int_param` -- declared: takes a parameter NAME and raises, where the canonical takes a default and falls back to it
- `Scripts/mcp-tshark.py`: `_bool_param` -- declared: keeps the older (params, key, default) signature
- `Scripts/mcp-webfetch.py`: `_bool_param` -- declared: an ALLOW-list, so an unrecognised string reads False here and True canonically
- `Scripts/mcp-webfetch.py`: `_error` -- declared: nothing measurable: byte-identical to the canonical once re-indented for a tab host, kept by hand only because every other server takes it co-listed with _result on one marker and this host cannot take _result
- `Scripts/mcp-webfetch.py`: `_result` -- declared: annotates result as dict where the canonical says Any -- a body difference no re-indenting removes
- `Scripts/mcp-webfetch.py`: `_rows_note` -- declared: excluded twice over: it is not tab-safe (its else aligns under an open paren) and its body diverged -- (start, shown, total) against the canonical (start, shown, total, exact), with no lower-bound branch

10 hand-written copies of a canonical block name, bound at module level or as a direct class member outside every generated region, in 5 of the 20 hosts scanned; 10 carry a declared reason and 0 do not.
Declared reasons with no hand copy left to explain: none.
<!-- END MEASURED: 32ab7de33187 -->

**Things that are not blocks at all** are documented in `Scripts/MCP_SKELETON.md`
rather than censused, because the census cannot see them. `_tool_error` *cannot*
be a block — its free names include the host's own server class, and host
provision is imports-only. `_ErrorText` *could* be one — byte-identical copies,
empty free names — and deliberately is not, because blessing a second mechanism
as generated infrastructure would buy drift protection for a divergence; see
[[0010-a-handler-failure-must-reach-iserror]]. `_send` is the third entry and
fails on a third rule: it is byte-identical in every LSP server and squarely
inside the LSP source's domain — it is `encode_lsp_message`'s only caller — but
that is exactly what refuses it, since the name it needs is a module-level `def`
in every host rather than an import, and the co-listing remedy above cannot reach
an indented region `Scripts/MCP_SKELETON.md`.

`_resolve_aliases` is the fourth entry and the widest of them: a hand copy in
every host that defines it, which makes it the fleet's widest hand-copied
non-generated function. It fails on the same rule `_tool_error` does — its free
names are each host's own alias tables, `PARAM_ALIASES` plus
`PARAM_ALIASES_BY_FUNC` in `mcp-postgres.py`, `mcp-purity.py` and
`mcp-wiki.py`, and those are module-level *assignments*, while `host_provides`
offers only the host's module-level imports
`Scripts/amalgamate.py:host_provides`. `Scripts/mcp-forge.py` sidesteps the
tables entirely by taking one as an argument
`Scripts/mcp-forge.py:_resolve_aliases`, and that is the tell: these are not
copies that drifted from one original but **shapes that never agreed**.
Measured on 2026-09-29 at `771e142`, by reading every definition: **eight
distinct bodies over the ten files**, only `Scripts/mcp-clangd.py`,
`Scripts/mcp-cuda.py` and `Scripts/mcp-tshark.py` byte-identical, and two of the ten tab-indented — the
same `mcp-forge.py` / `mcp-webfetch.py` pair `host-indent-from-tokens` pins
above. The rule every host now implements — a collision is an error, not a
precedence question — is frozen in [[0015-ambiguity-is-the-defect]], and it is
gated behaviourally rather than structurally, by
`Scripts/_mcp_smoke_test.py:alias_collision_checks` driving every host over
live JSON-RPC.

Which files count as hosts, though, is decided **textually**: that gate pairs its
per-server probe row against a search of the server's source for the resolver's *definition line*,
and asserts the two agree. A text search cannot tell a definition from a
quotation of one, so a server that defines no resolver can be made to look like a
host merely by writing that line into a docstring — measured, on the one server
that had reason to: `Scripts/mcp-git.py` reaches the same collision rule through
no resolver at all, its aliasing being structural rather than table-driven, and
explaining that in prose tripped the gate's own consistency row until the name
was spelled around rather than out `Scripts/mcp-git.py:_passthrough_args`. So the
host list is a census of a *spelling*, not of a mechanism, and the page-level
claim it supports is the narrower one: a set of files writes this function,
while the contract it implements is met by more — see [[scripts]].

That count is why the entry earns its space. ADR 0015 and section 7c of
`Scripts/MCP_SKELETON.md` both record **nine** bodies with only clangd and cuda
identical, and both were right when they were written: `Scripts/mcp-tshark.py`
held the fleet's last first-wins resolver, and the very commit that made
collisions an error is what converged it onto the clangd/cuda body. The ADR is
frozen at its decision and keeps its number; this page re-measured it at the
commit named above, which is a dated measurement rather than a rendered one — no
command counts resolver bodies yet.

## The gate

`tests/test_generated_region.py` gates the mechanism in lettered groups: A the
live tree against the canonical sources, B the marker and hash contract, C the
negative controls, D hygiene, E what each shared block actually *does*, F tab
safety, G the `--census` output this page renders — re-derived, proven
sorted, and unable to write `tests/test_generated_region.py:group_census` — H
strict JSON on the wire `tests/test_generated_region.py:group_strict`, and I the
HTTP front's pins `tests/test_generated_region.py:group_httpfront_pins`: every
stdlib member the two front hosts' classes override is generated or declared in
`HTTPFRONT_ADAPTATIONS`, a row naming no hand override of a stdlib member is
stale, each hand `process_request_thread` releases its connection slot in a
`finally`, and the seams the generated members read are present, with planted
controls for each rule. The hand-copy census is informational, and every other
case is a gated failure.

Group H runs its checker on a synthetic host first, because a checker that
matched nothing would read exactly like a clean fleet; then it requires every
server to carry the six strict-JSON blocks and to call `_strict_loads` and
`_strict_dumps` outside a region, and it walks every bare `json.load` /
`json.loads` call in a server with `ast`, plus every bare `json.dump` /
`json.dumps` on the frame tier — the methods of `McpServer`, and the whole of
`mcp-proxy.py`, whose every byte is a relayed frame. Each bare call it tolerates
is a row in `tests/test_generated_region.py:STRICT_JSON_EXCEPTIONS`, keyed by
host, enclosing function and json attribute, with the reason it is not a peer's
MCP frame, and a row whose site has gone fails `strict-exceptions-not-stale`.
R-0092 emptied the table of peer bytes: the thirteen peer sites R-0067 and
R-0068 had left declared out of scope — an LSP child's `Content-Length` body in
the four LSP servers, the upstream HTTP bodies `mcp-context7` (two sites),
`mcp-jenkins` and `mcp-gdc` (two sites) read, gdc's CDP websocket messages, and
the user project's `compile_commands.json` that cuda and purity read (three
sites; a cloned repository controls it byte for byte) — now parse through
`_strict_loads`, and each one is DRIVEN in-process with a finite, a `NaN` and
an over-long-integer body, the run lifting 3.11+'s own digit limit so the lax
code cannot pass on the interpreter's refusal
`tests/test_generated_region.py:STRICT_SITES`
`tests/test_generated_region.py:group_strict_sites`. Six rows remain, each with
its reason. Five are not peer input at all — a checked-in or self-written file
(wiki's `docs/measurements.json`, tshark's saved config, webfetch's cache), the
proxy operator's config, purity's private regex worker. The sixth is
`mcp-inspect`'s `_v_json`, whose job is to report the stdlib parser's own
verdict, so it stays lax on purpose (`NaN` is still OK there) but bounded: an
integer literal over `JSON_INT_LITERAL_LIMIT` characters is refused before
`int()` sees it and reported as a FAIL row at the literal's line and column
`Scripts/mcp-inspect.py:_v_json`. The proxy's own ready file needs no row any
more: since R-0072 its read and write are the generated
`_http_write_ready_file` / `_http_remove_ready_file`, and a bare json call
inside a generated region is spared. Group E pins what the new blocks do once, on the canonical modules:
the strict pair's refusals, the position each refusal reports and the
4300-character bound, and `_log_value`'s escaping, bound and cut marker. Which
wire-log fields must go through `_log_value` is gated by `tests/test_wire_log.py`,
not here ([[0011-a-truncated-payload-carries-the-first-cookie]]).

The case count is deliberately **not** repeated here. It is declared once, in the
suite table, and asserted on every run against what the suite actually recorded
`tests/run.py:SUITES`; a count that lives in prose and is checked by nobody is
precisely the defect that table exists to prevent.

Group E is not optional — the suite argues that a drift gate on its own would
only ever prove that every copy agrees on the same bug `Scripts/_mcp_paging.py`.
It loads each canonical source as a module and exercises every block that *has*
behaviour, which is why the value constants sit outside it by design, and
`_FENCE_LINE_RE` outside it by omission — see Known gaps.

The format contract is spelled out **independently** of the generator rather than
imported from it: a test that imported the marker constants would sail through a
rename that orphaned every region already written into a server.

## Known gaps

- **`_FENCE_LINE_RE` has no behavioural case.** Group E exercises every block
  that *has* behaviour, and this one does: `re.M` is the whole of it. Drop the
  flag and the pattern still compiles and still reads right, while `findall`
  quietly returns at most one hit — the fence count comes out even and a reply
  cut mid-fence reaches the reader with the block still open
  `Scripts/_mcp_paging.py`. Group A pins the constant's *text* in every host its
  row in the `canonical-block-hosts` table names; nothing yet pins what it does.
  The paging source's other constants are deliberately not in this position:
  `DEFAULT_MAX_ANSWER_CHARS`, `DEFAULT_MAX_CHARS` and `PAGE_LINE_RESERVE` have
  no behaviour to exercise, and a case asserting `== 24000` against a literal
  typed into the suite would be the same number written twice.
- **A name that is present but unextractable is reported as absent.** The loader
  skips a shape it cannot take, so a marker naming `X` where the source writes
  `X += 1` is refused with "defines no top-level `X`"
  `Scripts/amalgamate.py:render`. That is true of the block map and misleading
  about the cause; the refusal cannot yet tell the two apart.
- **`--check` proves a region's body, not what the host does with it.** It
  proves every region body matches its canonical source; it does not prove a
  host leaves the generated names alone outside the region. An attribute
  assignment or a `setattr` that rebinds a generated name later in the file is
  invisible to the census and to `--check` alike `Scripts/amalgamate.py`. The
  committer is trusted here, as for every other line of the host (security
  review 20261001-082224, F43).
- **A deep-nesting `RecursionError` is the host's to catch.** The security
  triage of `d4a241b` left two residuals. The first, peer bytes parsed laxly
  outside the MCP frame tier, was closed by R-0092 (above); what stays lax is
  declared in `STRICT_JSON_EXCEPTIONS`, and `_v_json` is lax but bounded. The
  second stands: `_strict_loads` deliberately does not convert a
  `RecursionError`: a host that answers deep nesting catches it itself, and 16
  stdio hosts still do not catch it at the frame parse, on the ground that
  their stdin parent is trusted.
