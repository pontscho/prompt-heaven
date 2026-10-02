---
name: 0024-pure-python-39-and-the-stdlib
type: adr
status: active
title: Pure Python 3.9 and the standard library; a third-party module is an allowlisted, find_spec-guarded exception
description: Decision that every Python file the repo ships runs on a bare Python 3.9 interpreter with the stdlib alone, that the four third-party modules still imported are an allowlist each entry of which has no stdlib replacement, that absence is detected with importlib.util.find_spec before the import and never with except ImportError, that mcp-webfetch's uv/PEP 723 launch is a declared temporary exception, and that lxml and websocket-client leave the tree -- with the survey that measured the tracebacks, the equivalence evidence for the stdlib Bing parser, the seven alternatives rejected, and the gate's declared blind spots.
links:
  - scripts
  - tests
  - spec-ddg
  - requirements-yaml
  - 0004-never-pin-a-browser-impersonation-version
  - 0014-a-canonical-source-is-a-domain
  - 0023-the-websocket-client-is-a-sixth-domain
---

# ADR 0024: Pure Python 3.9 and the standard library

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the dependency section of [[scripts]] and the docstring of
`tests/test_py_deps.py`.

This page carries no `sources:`/`verified:` frontmatter, which SCHEMA §3 makes
optional for an `adr`, for the reason [[0004-never-pin-a-browser-impersonation-version]]
records: a rationale does not expire when the code it governs is edited.

## Context — the dependency set was undeclared, and the failure was a traceback

A read-only survey (2026-09-29) walked every tracked `*.py` with `ast` and kept
each import whose top-level name was neither stdlib nor a repo module. The tree
was nearly clean — every MCP server but one, every hook, every test and every
skill script already ran on the stdlib alone — and the few exceptions were
handled four different ways:

| file | module | handling before this decision |
|---|---|---|
| `Scripts/mcp-webfetch.py` | bs4, markdownify, primp, curl_cffi (lxml via bs4) | PEP 723 block, launched with `uv run --script` |
| `Scripts/search_duckduckgo.py` | lxml (top level), primp / curl_cffi (lazy), websocket (lazy, cdp only) | nothing: no declaration, no guard |
| `Scripts/search_github.py` | primp / curl_cffi (lazy) | nothing |
| `Scripts/task-validator.py` | yaml | PEP 723 block **and** `try: import yaml / except ImportError` → message, exit 2 |
| `ClaudeCode/skills/verify/scripts/validate.py`, `Scripts/mcp-inspect.py` | yaml, tomli | `except ImportError` → degraded verdict |

The survey reproduced the failures instead of inferring them. With the user site
hidden, `search_duckduckgo.py` died at line 36 with `No module named 'lxml'`, and
`search_github.py` got through argument parsing and died **mid-run** inside
`create_session`, because its import was lazy. Both exit 1 — indistinguishable
from any other failure — and the traceback lands in the Bash tool result a model
reads. One agent prompt even tells a minion to "install missing dep, retry" on
`ModuleNotFoundError`, so the traceback was not merely ugly, it was an
instruction to go and mutate the host.

The trigger for the user is ordinary: the search scripts are called by path
(`~/.claude/scripts` is a symlink to `Scripts/`), the shebang takes whichever
`python3` is first on `PATH`, and the third-party packages live in one minor
version's user site. A Python upgrade is a new, empty user site.

## Decision

1. **Every Python file this repo ships runs on a bare Python 3.9 interpreter
   with the standard library.** 3.9 is the floor because it is what the fleet
   already targets (`requires-python = ">=3.9"` in every PEP 723 block the
   servers carry, 3.9 syntax throughout) and the oldest interpreter still on the
   hosts this runs on.
2. **A third-party module is allowed only when the stdlib has no way to do the
   job, and only from an allowlist.** Each entry is there because its
   replacement does not exist, not because it is convenient:
   - **`primp`, `curl_cffi`** — browser TLS/HTTP2 impersonation. The `ssl`
     module cannot shape a ClientHello (cipher order, extensions, GREASE, ALPS),
     and a Python-shaped ClientHello is exactly what DuckDuckGo and Bing block
     ([[spec-ddg]]). The platform split between them is
     [[0004-never-pin-a-browser-impersonation-version]]'s and is unchanged.
   - **`yaml` (PyYAML)** — there is no YAML parser in the stdlib, and a
     validator must judge well-formedness, which a regex cannot
     ([[requirements-yaml]]).
   - **`tomli`** — the 3.9/3.10 backport of `tomllib`. Fallback only: absent, a
     TOML check degrades to SKIP. `tomllib` itself is stdlib from 3.11, absent on
     the 3.9 floor, so it takes the same guard as a third-party module.
3. **Absence is detected with `importlib.util.find_spec("<name>")` BEFORE the
   import, never with `except ImportError`.** `find_spec` locates a top-level
   package without importing it, so an absent package gets the clean path while
   an installed-but-**broken** one (a C extension built for another ABI, a
   half-written wheel) still fails at the real import with its own traceback.
   `except ImportError` cannot tell those apart: it reports a broken install as
   "not installed", and the fix it then suggests — install it — is already done.
   What absence produces depends on the dependency's role:
   - **required** (the impersonation backend, PyYAML in `task-validator.py`):
     exactly one stderr line naming the package and a `pip install` command for
     the interpreter actually running (`sys.executable`, because the realistic
     cause is the *wrong* `python3`), and **exit 2**;
   - **optional** (PyYAML and tomli in the two validators): the declared
     degraded verdict, unchanged.
   The check runs **at startup, for the backend this platform will use** — not
   at the lazy import in `create_session`, which is what let the survey's
   traceback happen after the run had started. The module names are string
   literals so the gate can see them.
4. **`Scripts/mcp-webfetch.py` is a declared, temporary exception.** It keeps
   bs4 + markdownify and its `uv run --script` launch, because its HTML→Markdown
   conversion has no stdlib replacement in the tree yet, and `uv` already turns
   the missing-package case into a resolved one. The exception exempts four
   module **names** in one file, not the file, so a fifth dependency added there
   still fails the gate. No new PEP 723 block and no new `uv` launch anywhere
   else: `task-validator.py`'s block was removed by this decision.
5. **lxml and websocket-client leave the tree.**
   - `lxml` served one function, `parse_bing_results`, as four XPath
     expressions. It is now a stdlib `html.parser` tree builder that evaluates
     the same four expressions by hand. Equivalence was **measured**, not
     argued, against the lxml parser on a committed fixture
     (`tests/files/html/tf_bing_serp.html`) and on mutations of it — see
     *Evidence* below.
   - `websocket-client` served the opt-in `cdp` backend and is replaced by a
     stdlib client in the same change set, under
     [[0023-the-websocket-client-is-a-sixth-domain]].
6. **A gate keeps the rule true** — `tests/test_py_deps.py`, below.

## Evidence — the lxml replacement

The first version of the stdlib parser matched lxml on the fixture exactly and
still disagreed on **222 of 500** fuzzed variants (1-6 random end tags dropped).
A single dropped end tag isolated the cause: only `</div>` diverged, 17 of 26.
libxml2 — what lxml parses with — ignores an end tag that would have to close an
open element of higher *end priority* (`div` 150 against the default 100), so
with a stray `<div>` open inside a result, `</li>` does not end it and the next
result nests inside. Transcribing that rule left 10 of 1000 fuzzed variants
diverging; every one was libxml2's start-tag auto-close table (a `<li>` closes an
open `<h2>`), which was then transcribed as a table rather than approximated.
Final measurement, lxml 6.1.1 on Python 3.14.0: the fixture equal (9 results);
93 single-dropped-end-tag variants, 88 single-dropped-start-tag variants and
1000 seeded multi-drop variants — **0 differences**. The new parser gives the
same fixture result on Python 3.9.21, where lxml is not installed. The expected
fields are committed as `tests/files/html/tf_bing_serp.expected.json`, a
recorded measurement of lxml, not something to regenerate from the current code.

## The gate, and what it cannot see

`tests/test_py_deps.py` reads every `.py` under `Scripts/`, `ClaudeCode/` and
`tests/`, plus the extensionless `sbx`, with `ast`. An import whose top-level
name is neither 3.9 stdlib nor a repo module must be on the allowlist (or a
declared per-name exception), must be preceded by a literal `find_spec` call,
and must not sit inside a `try` that catches `ImportError`. No file may import a
3.9 stdlib module removed since (PEP 594 and earlier). Every file must parse
with `ast.parse(src, feature_version=(3, 9))`.

The 3.9 stdlib name set is **embedded**, not read from `sys.stdlib_module_names`:
that attribute is 3.10+, and it describes the running interpreter, not the
floor — on 3.14 it contains `tomllib` and lacks `imp`. On 3.10+ the suite
cross-checks the embedded table against it, so a typo fails rather than silently
widening "stdlib".

Declared blind spots, each stated in the suite's docstring:

- **Syntax only.** `feature_version=(3, 9)` refuses a `match` statement but
  cannot see 3.10+ stdlib *API* use, and CPython documents it as best-effort:
  the suite measures a PEP 701 f-string that 3.14 accepts under it and 3.9
  refuses, and prints that as INFO. Only a 3.9 interpreter is a complete check;
  the suite itself was run under 3.9.21 once and gave the same verdicts.
- **The guard check is lexical** — a `find_spec` of the same literal on an
  earlier line — not control flow.
- **A computed dynamic import is listed, not judged.** The pre-decision tomli
  fallback, `for modname in ("tomllib", "tomli"): __import__(modname)`, was
  exactly that shape: this gate would have listed it rather than failed it.
  Both copies now spell the two imports out.
- **A third-party name equal to a repo module stem reads as local.**

## Alternatives Evaluated

1. **PEP 723 blocks plus `#!/usr/bin/env -S uv run --script` shebangs on every
   third-party script** (the survey's recommendation). *Rejected by the user:*
   it trades a missing package for a missing `uv` ("env: uv: No such file" is
   no better a failure), needs the network on first run, and any caller that
   runs the file through `sys.executable` — every test — silently bypasses it.
2. **Keep `try/except ImportError`**, the one clean-message precedent.
   *Rejected:* it misreports a broken install as an absent one, and the policy
   point is an honest failure in both cases.
3. **Guard lxml instead of removing it.** *Rejected:* the allowlist admits a
   module only where there is no stdlib way, and `html.parser` is one — the
   measurement above is the proof that it was not a downgrade.
4. **Vendor the dependencies.** *Rejected:* `curl_cffi` and `primp` are binary
   wheels; vendoring them is platform matrices in git.
5. **One shared guard helper, as a generated region.** *Rejected:* the hosts are
   three CLI scripts and two validators, not MCP servers, and a four-line
   `find_spec` check is not a domain by [[0014-a-canonical-source-is-a-domain]]'s
   bar. A sibling import is not available either: the fleet imports no sibling.
6. **Derive the stdlib set from `sys.stdlib_module_names`.** *Rejected:* 3.10+,
   and it answers for the running interpreter rather than for the floor.
7. **A "doctor" function in `mcp-inspect` reporting which packages are
   installed.** *Deferred, not rejected:* useful, but it answers "what is
   installed", not "what is allowed", and the rule does not need it.

## Consequences

- A missing dependency is now one line and exit 2 in `search_duckduckgo.py`,
  `search_github.py` and `task-validator.py`, measured under the 3.9
  interpreter, which has none of them installed.
- The cdp backend of `search_duckduckgo.py` does not run the impersonation
  check: it opens no impersonating session.
- `tests/test_py_deps.py` joins the fleet; its count is declared once in
  `tests/run.py`.
- `mcp-webfetch.py` remains the one file that needs `uv`, and it is listed in the
  suite with its reason, so ending the exception is a deletion the gate will
  demand (a declared exception that exempts nothing fails).

## Addendum (2026-10-01): a ctypes system library is a dependency category; the allowlist shrinks (ADR 0026)

[[0026-speak-chrome-from-the-stdlib-verify-by-default]] replaced primp and curl_cffi with a stdlib Chrome client, so the policy tables of `tests/test_py_deps.py` shrink. `ALLOWLIST` goes from four modules to two, `yaml` and `tomli`; the `Scripts/mcp-webfetch.py` entry of `EXCEPTIONS` goes from four names to two, `bs4` and `markdownify`. Decision 2's primp/curl_cffi bullet no longer has a subject. Decision 4 stands: webfetch keeps its `uv run --script` launch as a declared, temporary exception, now for the HTML-to-Markdown conversion alone, since the fetch itself is the stdlib client.

The same change brought a dependency the import gate cannot see. `ctypes` is stdlib, so a shared library loaded through it never appears as an import. The client's content decoders load `libbrotlidec` (`Scripts/_mcp_brotli.py`) and `libzstd` (`Scripts/_mcp_zstd.py`): the stdlib has no brotli decoder on any version, and no zstd decoder before `compression.zstd` in 3.14, far above the 3.9 floor. A ctypes-loaded system library is therefore a new category, with its own register: `SYSTEM_LIBS`, the ctypes counterpart of `ALLOWLIST`, maps each library stem to the reason the stdlib cannot do the job. Group F of the suite reads, with `ast`, every literal library name (the first argument of `ctypes.CDLL`, `PyDLL`, `cdll.LoadLibrary` and `ctypes.util.find_library`, and every `lib<stem>.(so|dylib)` element of a top-level tuple whose name contains SONAMES or FILES) and fails any stem not declared there; a declared stem that no scanned file names is a stale licence and fails too. `CDLL(None)` names no library and is listed, not judged. Like an optional module, a system library may be absent on a host: the decoder then raises a one-line `LookupError` instead of a traceback. Declared blind spots: a load with a computed name is judged only through the literal it is computed from when that sits in a SONAMES/FILES tuple, and a `find_library` stem held in any other literal is not read.

The syntax-only blind spot above is narrowed for the new code. The four new sources, `NEW_39_SCOPE` (`Scripts/_mcp_chrome.py`, `Scripts/_mcp_brotli.py`, `Scripts/_mcp_zstd.py`, `Scripts/chrome_capture.py`), were written against the 3.9 floor, where an API check is cheap and exact, so group F also refuses 3.10+ stdlib API use in them: `int.bit_count`; `zip`/`map` with `strict=`; `int.to_bytes`/`int.from_bytes` without both the length/bytes and byteorder arguments (both optional only from 3.11); `bisect.*` with `key=`; `itertools.pairwise`; `create_connection(all_errors=)`; `hashlib.file_digest`; `ssl.VERIFY_X509_PARTIAL_CHAIN`; and `X | Y` in an annotation. It is a list of known APIs, not a model of the stdlib, and it does not parse a string annotation; a real 3.9 interpreter remains the complete check. The rest of the tree keeps the syntax-only check of the gate described above.

## Addendum (2026-10-01): the webfetch PEP 723 block after ADR 0026, and its unpinned dependencies

The Context table's `Scripts/mcp-webfetch.py` row still names primp and curl_cffi. It is the pre-decision survey and stays as it was measured; it is not the live dependency set. Since [[0026-speak-chrome-from-the-stdlib-verify-by-default]] the file's PEP 723 block declares three distributions, `beautifulsoup4`, `markdownify` and `lxml`. The gate's per-name exception covers the two that are imported, `bs4` and `markdownify` (the addendum above); `lxml` is never imported by name, it is the tree builder bs4 is asked for by the string `"lxml"`, so the import gate cannot see it and Decision 5's "lxml leaves the tree" holds for `search_duckduckgo.py` only.

Security review 20261001-082224 (finding F47, LOW) recorded that none of the three carries a version bound, so every `uv run --script` cold start resolves whatever the index serves that day. The surface is smaller than before the Chrome client (no impersonation package is fetched any more), and the finding is pre-existing. It is deferred, not kept: pinning or bounding the three, or finishing lxml's removal with the stdlib `html.parser` tree builder, is roadmap item R-0058.

## Addendum (2026-10-02): R-0058 closed by bounding; lxml stays in webfetch as the tree builder

R-0058 (security review finding F47) is closed by `6216391`. Each of the three requirements in the PEP 723 block of `Scripts/mcp-webfetch.py` now has a lower bound at the version in use and an upper bound below the next major: `beautifulsoup4>=4.15,<5`, `markdownify>=1.2,<2` and `lxml>=6.1,<7`. The versions were read from the uv cache environments. A `uv run --script` cold start can no longer resolve a new major.

**lxml stays in webfetch, by user decision (2026-10-02).** R-0058 also offered the other road: finish lxml's removal by switching bs4 to the stdlib `html.parser` builder. The user first approved that road, and a measured comparison reversed it. Of 63 outputs over 21 HTML cases, 53 were identical. The differences came from `html.parser`, which has no implied end tags: an unclosed `<li>` or `<td>` nests the next one inside it, and the markdown loses list items and table cells. Main-content selection was identical in every case. Alternative 3's "`html.parser` is one" (a stdlib way) was measured for `parse_bing_results`, a single function made equal to lxml by transcribing libxml2's close rules. No such transcription exists for webfetch's general HTML-to-Markdown conversion, and the measurement decided it.

This supersedes Decision 5's "lxml leaves the tree" for `Scripts/mcp-webfetch.py` only. The rule still holds for `Scripts/search_duckduckgo.py`. In webfetch, lxml is now a deliberate dependency and not a removal waiting to happen. The 2026-10-01 addendum's phrase "finishing lxml's removal ... is roadmap item R-0058" no longer describes an open option.

**The find_spec / allowlist rule is unaffected.** `Scripts/mcp-webfetch.py:_soup` asks bs4 for lxml by the builder string `"lxml"` and never imports it by name. The import gate therefore cannot see lxml and needs no entry for it. The webfetch entry of `EXCEPTIONS` stays `bs4` and `markdownify`, and Decision 4 (webfetch's `uv run --script` launch as a declared exception) is unchanged. The gap the import gate cannot see is now covered by group G of `tests/test_py_deps.py` (`group_webfetch_deps`). It checks that every requirement in webfetch's PEP 723 block has a `>=` lower bound and a `<` upper bound. It also checks that every `BeautifulSoup()` call names the literal `"lxml"` builder and that lxml is declared in the block, because an undeclared builder would only show up as `FeatureNotFound` at the first conversion. Planted controls sit in the same group.
