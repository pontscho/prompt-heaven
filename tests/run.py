#!/usr/bin/env python3
"""Single entry point for the prompt-heaven test suites.

The registered suites are the SUITES table below, and that table is the only
place any of it is written down: the name, the one-line subject, and the
declared case count all live in the same tuple, and every written-down count is
checked against the run.

This docstring used to carry a second copy of that roster -- name, file and
description, hand-wrapped, ninety lines of it. It listed thirteen of the
seventeen suites by the time anyone read it against the table: `sbx_gate`,
`jira_cli`, `checkpoint` and `generated_region` had each been added below and
not here. A roster whose whole job is completeness is not partly stale, it is
wrong, and the copy could only ever drift in one direction. Deleting it is the
file applying to itself the rule it already states.

`purity_lsp` is the slow one (~45 s): it drives live clangd /
lua-language-server children through a real handshake.  It SKIPs cleanly when
those binaries are absent.  (It used to take ~2.5 min because each backend's
init blocked on an indexing-progress event nobody was in a position to receive,
so the 60 s deadline could only expire -- see that suite's module docstring for
the full causal chain, which has three links, not one.  The wait is now gated on
indexing announcing itself, the announcement-wait runs CONCURRENTLY with the
priming that provokes it, and an idle watchdog releases a wedged indexer.)

`smoke` is deliberately left standalone -- its path is referenced from ~15
places in the repo docs, so it is invoked here as a subprocess and only its
exit code (plus its output on failure) is consumed.  It reports SERVERS, not
cases, and is counted as such.

Usage:
  python3 tests/run.py                       # every suite
  python3 tests/run.py inspect_validate      # one or more suites by name
  python3 tests/run.py mcp_first_guard smoke
  python3 tests/run.py --show valid-json     # forwarded to inspect_validate
  python3 tests/run.py --whitebox            # forwarded to mcp_first_guard
  python3 tests/run.py --keep                # keep generated fixture dirs
  python3 tests/run.py --brief               # terse per-case lines

Exit code 0 iff every selected suite passed AND no declared case count drifted.
Works from ANY working directory: paths are derived from __file__, never from
os.getcwd().

Adding a suite takes three lines: write tests/test_<x>.py exposing
`run(opts) -> Suite`, then add one SUITES entry naming it and declaring its
case count (or None if the count is data-derived rather than a fixed table).
"""

import os
import sys

# Must precede every repo import: several suites assert that a run leaves no
# .pyc behind, and importing a repo module is exactly how one would appear.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

SMOKE = H.repo_path("Scripts", "_mcp_smoke_test.py")

# The fleet size is DERIVED from the smoke harness's own launch table, never
# typed here -- a hand-maintained copy of this number was wrong within a day of
# being written (it said 16 after the table dropped to 15).  The module is
# import-safe: SERVERS is module-level and main() is behind an __main__ guard.
sys.path.insert(0, H.repo_path("Scripts"))
import _mcp_smoke_test as _smoke_mod  # noqa: E402

SMOKE_SERVERS = len(_smoke_mod.SERVERS)


def run_python_suite(module_name, opts):
    """Import tests/<module_name>.py and run it in-process."""
    module = __import__(module_name)
    return module.run(opts).report()


def run_inspect_validate(opts):
    return run_python_suite("test_inspect_validate", opts)


def run_mcp_first_guard(opts):
    return run_python_suite("test_mcp_first_guard", opts)


def run_sbx_gate(opts):
    return run_python_suite("test_sbx_gate", opts)


def run_sbx_seccomp(opts):
    return run_python_suite("test_sbx_seccomp", opts)


def run_sbx_restrict(opts):
    return run_python_suite("test_sbx_restrict", opts)


def run_purity_lsp(opts):
    return run_python_suite("test_purity_lsp", opts)


def run_purity_file_ops(opts):
    return run_python_suite("test_purity_file_ops", opts)


def run_mcp_git_params(opts):
    return run_python_suite("test_mcp_git_params", opts)


def run_name_existence(opts):
    return run_python_suite("test_name_existence", opts)


def run_spawn_stdin(opts):
    return run_python_suite("test_spawn_stdin", opts)


def run_mcp_footprint(opts):
    return run_python_suite("test_mcp_footprint", opts)


def run_wiki_recall(opts):
    return run_python_suite("test_wiki_recall", opts)


def run_wiki_index(opts):
    return run_python_suite("test_wiki_index", opts)


def run_wiki_addendum(opts):
    return run_python_suite("test_wiki_addendum", opts)


def run_jira_cli(opts):
    return run_python_suite("test_jira_cli", opts)


def run_bitbucket_cli(opts):
    return run_python_suite("test_bitbucket_cli", opts)


def run_jenkins_job_cli(opts):
    return run_python_suite("test_jenkins_job_cli", opts)


def run_checkpoint(opts):
    return run_python_suite("test_checkpoint", opts)


def run_roadmap(opts):
    return run_python_suite("test_roadmap", opts)


def run_roadmap_board(opts):
    return run_python_suite("test_roadmap_board", opts)


def run_generated_region(opts):
    return run_python_suite("test_generated_region", opts)


def run_mcp_websocket(opts):
    return run_python_suite("test_mcp_websocket", opts)


def run_mcp_oauth(opts):
    return run_python_suite("test_mcp_oauth", opts)


def run_mcp_decoders(opts):
    return run_python_suite("test_mcp_decoders", opts)


def run_chrome_capture(opts):
    return run_python_suite("test_chrome_capture", opts)


def run_mcp_chrome(opts):
    return run_python_suite("test_mcp_chrome", opts)


def run_read_loop(opts):
    return run_python_suite("test_read_loop", opts)


def run_cancel(opts):
    return run_python_suite("test_cancel", opts)


def run_wire_log(opts):
    return run_python_suite("test_wire_log", opts)


def run_handler_crash(opts):
    return run_python_suite("test_handler_crash", opts)


def run_table_cells(opts):
    return run_python_suite("test_table_cells", opts)


def run_protocol_version(opts):
    return run_python_suite("test_protocol_version", opts)


def run_forge_dispatch(opts):
    return run_python_suite("test_forge_dispatch", opts)


def run_param_contract(opts):
    return run_python_suite("test_param_contract", opts)


def run_py_deps(opts):
    return run_python_suite("test_py_deps", opts)


def run_search_parsers(opts):
    return run_python_suite("test_search_parsers", opts)


def run_mcp_search(opts):
    return run_python_suite("test_mcp_search", opts)


def run_webfetch_roots(opts):
    return run_python_suite("test_webfetch_roots", opts)


def run_mcp_proxy(opts):
    return run_python_suite("test_mcp_proxy", opts)


def run_mcp_sandbox(opts):
    return run_python_suite("test_mcp_sandbox", opts)


def run_mcp_skill(opts):
    return run_python_suite("test_mcp_skill", opts)


def run_llm_router(opts):
    return run_python_suite("test_llm_router", opts)


def run_gpu_router(opts):
    return run_python_suite("test_gpu_router", opts)


def run_tshark_fields(opts):
    return run_python_suite("test_tshark_fields", opts)


def run_fetch(opts):
    return run_python_suite("test_fetch", opts)


def run_task_scripts(opts):
    return run_python_suite("test_task_scripts", opts)


def run_smoke(opts):
    """Invoke the standalone smoke harness as a subprocess; parse its rc."""
    rc, out, err = H.run_process([sys.executable, SMOKE], timeout=300,
                                 cwd=H.REPO_ROOT)
    ok = rc == 0
    if not ok:
        print(out, end="" if out.endswith("\n") else "\n")
        if err.strip():
            print("--- smoke stderr ---")
            print(err.rstrip())
    else:
        print("  Scripts/_mcp_smoke_test.py -> rc=0, RESULT: ALL PASS")
    return H.SuiteReport("smoke", ok, SMOKE_SERVERS, unit="servers",
                         output=out, note="rc=%d" % rc)


# name -> (runner, one-line description, declared case count)
#
# The count is the SINGLE place this number is written down: the banner prints
# it, and main() asserts it against what the suite actually reported.  A count
# that lives in prose and is checked by nobody is a lie waiting to happen --
# this repo shipped six of them (a commit message claiming "119 cases" for a
# 121-case suite, an unpushed-commit total, two "10 canonical" comments, a
# "13 luals_* names" blurb, and a hardcoded fleet size of 16).
#
# None means the count is DATA-DERIVED, not a fixed case table, so pinning it
# would fail on every legitimate change to the thing being measured:
#   - smoke        reports servers, and that number is derived above
#   - name_existence generates one case per name found in the corpus, so it
#     moves whenever the corpus or the server inventory moves -- which is the
#     entire point of the suite
#   - spawn_stdin  enumerates FILES and emits one case per spawn site found, so
#     its count moves whenever a server gains or loses a subprocess call.  The
#     gate there is the INVARIANT (every site passes an explicit stdin=), and a
#     drift line reading "265 != 262" would be a strictly worse error message
#     than "new spawn site with no explicit stdin at foo.py:120".  What is typed
#     gets checked; what is derived gets derived.
#   - mcp_footprint emits several cases PER SERVER (description tax, result
#     ceiling, boilerplate), so its count is a multiple of the launch-table size
#     -- the same derived quantity SMOKE_SERVERS above refuses to hardcode.
#     Adding or retiring a server would trip a typed count for no defect.
SUITES = [
    ("inspect_validate", run_inspect_validate,
     "mcp-inspect VALIDATION family", 313),
    ("mcp_first_guard", run_mcp_first_guard,
     "mcp-first-guard PreToolUse Bash hook", 381),
    # The egress work added five rows (M EG1-EG4: --egress-port /
    # --egress-socket prompt in every spelling; N EG-ctl, the restricted
    # control they extend). Not red first: an unknown flag already prompts,
    # so these pin the verdict; sbx_restrict:S17 is the red-first table row.
    ("sbx_gate", run_sbx_gate,
     "sbx PreToolUse grant-only gate", 141),
    # TYPED: a fixed case table.  The one Linux-only row per group C and D is
    # recorded as INFO on any other host rather than omitted, so the count is
    # the same on macOS and on Linux.
    ("sbx_seccomp", run_sbx_seccomp,
     "sbx --seccomp: the x86_64 BPF allowlist run through an independent "
     "interpreter against an independent copy of the syscall table -- arch "
     "check first, x32 killed, EPERM by default, the argument-checked socket, "
     "ioctl and clone entries, clone3 ENOSYS -- the pure `--seccomp N` argv "
     "wiring, the fail-closed refusal off Linux x86_64, and the live prctl "
     "probe on Linux", 33),
    # TYPED: a fixed case table (A 15, B 8, C 4, D 4, E 10, F 11, U 9, V 8,
    # T 7, L 12, S 17, X 15, N 14) -- the Step 2 rows, the Step 3 Seatbelt and bwrap
    # goldens (F11, IMPL:025: --clean-env without --restrict-reads keeps
    # bwrap's --clearenv/--setenv), the Step 4 supervisor / strict-rlimit /
    # clean-env rows, the Step 6 live restricted-Seatbelt rows (L12 the
    # depth cap, M0:9; IMPL:029), the Step 5 gate-grammar rows and the
    # security-review round-1 rows (X: P1 cwd/builder SBPL injection, P2
    # socket reads, R1 soft rlimits, M1 status record, G3 tilde, P4 help;
    # round 3: H1 socket-burying write scopes, H2 the restricted fresh /dev,
    # R1 socket-dir carve-out/shadow targets, R2 write scopes under /run;
    # all pure or in-process). A live row that cannot run here (the other
    # OS, or a nested sandbox per the P23 control) is recorded INFO, never
    # omitted, and L7 is INFO by declaration, so the count is the same on
    # macOS and on Linux. The egress work added group N (11: the
    # --egress-port / --egress-socket grammar and platform split, the socket
    # ownership rules, both builder goldens, the forwarder, one live row per
    # platform -- N10 INFO off macOS, N11 INFO off Linux) and S17 (the gate
    # prompts for both), all written RED: 118 -> 130. Later fixes added N12
    # (foreign-writable socket ancestors), N13 (the forwarder survives a
    # thread-start failure), N14 (a user-private-group-writable ancestor is
    # accepted) and X15 (--rlimit-nproc +N counts Linux tasks): 130 -> 134.
    ("sbx_restrict", run_sbx_restrict,
     "sbx restricted mode: reads, exec filter, clean env, supervised timeout, "
     "strict rlimits, live restricted Seatbelt, gate grammar, security "
     "review round 1, egress endpoint flags", 134),
    ("purity_lsp", run_purity_lsp,
     "purity_call semantic navigation: clangd + luals absorption", 155),
    ("purity_file_ops", run_purity_file_ops,
     "purity_call file handlers: the .claude/tmp ignore exemption, the "
     "inheritance rule that keeps it narrow, the param contract, and the "
     "glob spellings that can only ever match nothing, read_file's "
     "line-count `limit` and its past-EOF note, find_file's ignore filter, "
     "a missing directory or search root reaching the caller as an error, "
     "a search root outside the project root searched rather than read as "
     "empty (and refused under --strict), "
     "an offset past the last row answered with the past-the-end note "
     "rather than an inverted range, a walk rooted in `.git` refused, and "
     "search's globs taken as a string or a list of strings, and its "
     "`regex:false` literal mode, `max_results`/`max` as head_limit, and "
     "`paths_include`/`paths_exclude` as the *_glob filters, and "
     "`only_matching` (rg -o): one row per match, paged by match rows, "
     "refused beside count/files_with_matches/context, "
     "find_file's path-style character classes, and a catastrophic "
     "search regex bounded by the call's time budget, and replace_content's "
     "regex mode bounded the same way, the file left untouched on overrun, "
     "`count`/`max_matches`/`limit` as head_limit in search, a metachar-free "
     "regex matched in-process byte-identical to the worker, a total-budget "
     "overrun named as such (backtracking blamed only for a slow file), "
     "nested repos below the search root skipped, and an out-of-root root "
     "filtered by its own repo's .gitignore, and an exclude glob pruning "
     "whole directories during the walk, `max_depth` on find_file, list_dir "
     "and search (0 = the root's own entries, omitted unchanged, junk "
     "refused), and search's `multiline` over the whole file text (one row "
     "per match at its start line, in-process and worker identical)",
     317),
    ("mcp_git_params", run_mcp_git_params,
     "mcp-git named params -> git argv, offline; a real unexposed subcommand "
     "refused with no near-miss pointer, a key no git option can be spelled "
     "from refused, a `git grep` exit 1 with no output a plain no-match "
     "answer", 317),
    ("name_existence", run_name_existence,
     "corpus + server text <-> live MCP inventory name existence", None),
    ("spawn_stdin", run_spawn_stdin,
     "explicit stdin= at every subprocess spawn site", None),
    ("mcp_footprint", run_mcp_footprint,
     "MCP fleet token footprint: description tax, result ceilings, boilerplate",
     None),
    ("wiki_recall", run_wiki_recall,
     "mcp-wiki search relevance gate: silence, measured calibration window, "
     "floored percentages, query-side stopwords, get_page section index, "
     "source_to_pages per-hit description, MEASURED state labels, "
     "file-relative line windows, the page type as a ranking signal, "
     "the frontmatter aliases synonym field, "
     "the frozen-record advisory carve-out, "
     "the dead [[slug]] wikilink gate, roadmap pages as frozen records", 171),
    ("wiki_index", run_wiki_index,
     "INDEX.md rendering and the page-type constants: the server's "
     "render_index never listing a roadmap-item and counting them in one "
     "rendered line, the orphan exemption, the server/_wikilib parity of six "
     "constants, both git() copies timed out and hardened like roadmap.py's, "
     "every other _wikilib function the server vendors compared as code and "
     "constant by value, and the freshness.py / reindex.py wrappers over the "
     "server: the freshness exit code is the server's gating number (a dead "
     "body anchor gates, git lag is advisory for every editorial status), its "
     "output is the server's answer, reindex fails on a duplicate slug through "
     "the server's exported prefix, render_index is a delegate, and no server "
     "means exit 2",
     72),
    ("wiki_addendum", run_wiki_addendum,
     "addendum.py, the one legal write to an accepted ADR: one dated "
     "addendum appended with every old byte kept, only an active `type: adr` "
     "page inside the wiki root, a staged title/body item whose body cannot "
     "forge a sibling section, and the same heading never appended twice",
     57),
    ("jira_cli", run_jira_cli,
     "Jira CLI offline: auth mode, context-path URL join, lazy deployment "
     "probe, the Cloud token pager and the DC offset pager behind one "
     "iterator, the agile envelope walked past page one on boards AND sprints "
     "with the caller's query re-sent on every page and the walk BOUNDED "
     "against a server that ignores startAt, config precedence, "
     "JIRA_READ_ONLY, --dry-run, the five "
     "error mappings, the multipart attachment body, the Markdown "
     "rendering: pipe-escaped cells, empty tables, no duplicated fields, the "
     "`.claude/jira.json` walk and its $HOME boundary, and the create payload: "
     "the one-level merge, alias resolution, NAME=VALUE at the first `=`, the "
     "system-field shaping table, and `@active` refusing every ambiguous "
     "board/sprint configuration instead of guessing -- including a sprint "
     "whose id cannot become an int, which has to refuse as a SetupError "
     "rather than escape main() as a traceback -- plus the FOURTH pager "
     "(`projects`, bounded, and a `\"total\": true` that must not truncate it "
     "because `50 >= True`), the Cloud empty page a fresh token every time "
     "never stops without, the numeric-id check on every board id that reaches "
     "a URL path from the profile, from --board and from the server's own "
     "board list, an identity that resolves to nothing being refused rather "
     "than sent, and the three ways main() owed a message and produced a "
     "traceback instead: an unclamped Retry-After, an exception with no text, "
     "and a reader that closed the pipe -- plus the createmeta fallback that "
     "used to swallow every finding on its way past, now keyed on an "
     "allowlist of statuses that mean `no such route here` and asserted as "
     "ZERO requests to the legacy endpoint for a 401, a 403, a 429 and the "
     "page bound whose own docstring says it refuses rather than grows, the "
     "SSO-proxy guard a response with NO Content-Type used to walk straight "
     "past into json.loads, a literal user name refused before Cloud can "
     "answer it with a 400 that names the field and not the reason, and one "
     "timestamp format written down once in the copy a caller can read -- "
     "plus the $HOME boundary on that walk, which the docstring, the help "
     "text and SKILL.md all claimed and the code held on neither side: one "
     "`==` between a cwd os.getcwd() had resolved and a $HOME nothing had, "
     "so it never bound from outside $HOME (where the walk climbed to `/` "
     "and read /tmp/.claude/jira.json) and never bound through a symlinked "
     "$HOME either, which is the macOS default -- both halves now gated, the "
     "symlinked one against a real symlink, each with a control beside it, "
     "and the case-folding residue realpath cannot close MEASURED rather "
     "than claimed, plus `transition --field`: the transition screen's own "
     "fields encoded by schema, refused before any write when unknown or "
     "outside allowedValues",
     243),
    # TYPED, and the count is a fixed case table plus ONE derived row: group C
    # sweeps every entry in the CLI's HANDLERS dict, but it records three cases
    # regardless of how many subcommands it finds, so adding a subcommand moves
    # this number by zero.  What DOES move it is adding a case, which is what a
    # typed count is for.  Unlike `mcp_footprint`, nothing here is a per-server
    # or per-site multiple, so there is no legitimate change to the thing being
    # measured that a typed count would punish.
    ("bitbucket_cli", run_bitbucket_cli,
     "Bitbucket CLI offline (transport injected): the context-path URL join "
     "and the two refusals at the door -- an http:// scheme that would put the "
     "bearer PAT on the wire in cleartext, and a userinfo URL whose refusal is "
     "asserted NOT to reproduce the secret -- the same-origin redirect handler "
     "that keeps the Authorization header from following a 30x off this origin, "
     "BITBUCKET_READ_ONLY at BOTH layers with the write set DERIVED by driving "
     "every handler through a verb-recording transport rather than typed twice, "
     "the four merge refusals each measured as zero requests rather than as "
     "statement order, pr-builds' verdict precedence and its exit code pinned "
     "in Markdown AND --json in one case, the per-subcommand request contract "
     "including pr-approve's three requests and paging that follows "
     "nextPageStart, reviewer flattening in both the documented and the "
     "OBSERVED shape, and byte parity with jira.py's _profile_path -- a "
     "deliberate copy, gated on identity so the eventual unification stays "
     "mechanical, and that is what it bought: the copy was taken carrying a "
     "$HOME boundary that bound on neither side, and these rows are what "
     "forced the repair through BOTH files in one change, the digest "
     "re-measured only once the two slices matched again and the helper the "
     "boundary test calls located and compared on its own because it sits "
     "outside the slice -- then the boundary DRIVEN through a real symlinked "
     "$HOME, because identical text in a file is not the same claim as "
     "identical behaviour in the loaded module", 133),
    # TYPED: a fixed case table, so a count that moves is the alarm.
    ("jenkins_job_cli", run_jenkins_job_cli,
     "Jenkins job CLI offline against a fake Jenkins on loopback: the "
     "https-only door (the loopback override is test-only) and the job path "
     "and name rules, get-config with every secret masked and no flag to show "
     "them, dry runs measured as zero writes, --apply's createItem copy then "
     "config POST with crumb and session cookie, a fresh webhook token printed "
     "once and stored nowhere else, and the failure paths -- duplicate name, "
     "missing trigger or Git source, a half-created job reported by name, a "
     "failed config POST, a cross-origin redirect, the size cap and hostile "
     "server text", 49),
    ("checkpoint", run_checkpoint,
     "checkpoint.py section reader + TOC writer: Start/End land on the block "
     "and nothing else, the numbers describe the file AFTER the region was "
     "inserted, a second write is a byte-for-byte no-op, every byte outside "
     "the region survives, `prepend` lands the block and the table it "
     "describes in ONE os.replace, a stale or duplicate-id segment is refused "
     "on content, a segment whose sessions are not each immediately followed "
     "by their own `## ACTIVATION S<NNN>` block is refused on shape, the "
     "activation block gets its own A<NNN> row that `session` reads back, "
     "`activate` prints it paste-ready and `nexts` prints MISSION + newest "
     "SESSION + its ACTIVATION -- falling back, read-only, to the old "
     "`### ACTIVATION` subsection -- an empty prompt is refused on write and "
     "on read alike, a file that is not UTF-8 is refused rather than crashed "
     "on, `migrate` moves every legacy subsection verbatim into its own block "
     "-- matched against hand-written expected files, `activate` unchanged "
     "for every session, and a second run or a --dry-run writing nothing down "
     "to the mtime -- and every refusal exits 2 with one line on stderr, "
     "leaving the file alone",
     230),
    ("roadmap", run_roadmap,
     "roadmap.py, the single writer of docs/roadmap: the on-disk format "
     "contract spelled independently of the module, the wiki type "
     "membership, and the security AST gates over its source -- stdlib only, "
     "one git spawn site with a pinned argv prefix and a read-only "
     "subcommand set, no shell, exact write-route caller sets -- the "
     "behavioural groups, the edit command held to add's validators, and "
     "hygiene that proves the sandbox guards bite even before the script "
     "exists",
     336),
    ("roadmap_board", run_roadmap_board,
     "board.py, the read-only kanban board over roadmap-export/2: anything "
     "but that schema refused with one line, the markdown board byte for "
     "byte with ADR 0016 cells decoded back and the reverse blocks edges, an "
     "html page whose embedded data cannot close its script block and which "
     "renders without v-html through one pinned Vue build, and --out held "
     "to the roadmap-board-*.html name and refused through a symlink",
     46),
    ("generated_region", run_generated_region,
     "generated regions match their canonical source, and the source named on "
     "a region's BEGIN line is the one its names resolve against; the "
     "generator's census read path -- fleet, sources, per-block hosts and the "
     "hand-copy walk with its declared reasons -- derived, sorted, writes "
     "nothing; and every host parses and emits its peer frames through the "
     "strict JSON blocks, with every other bare parse declared; and the HTTP "
     "front's two hosts pinned: every stdlib override in their front classes "
     "generated or declared with a reason; and the near-miss suggestion "
     "generated into, and called by, every server; and the unknown-params "
     "refusal unit-tested and called by every host that carries it", 142),
    ("mcp_websocket", run_mcp_websocket,
     "the stdlib WebSocket client: exact-match handshake, every frame length "
     "form, the refusals a frame header earns, fragments, ping/pong, close, "
     "the size caps and strict UTF-8, the asyncio and blocking-socket "
     "wrappers -- then mcp-gdc's generated copy driven against a loopback "
     "CDP peer", 41),
    # TYPED: A 4, B 5, C 5, D 12, E 11, F 7, G 5, H 15, I 4 = 68
    ("mcp_oauth", run_mcp_oauth,
     "the stdlib OAuth 2.0 helpers: PKCE and state, the authorize URL, the "
     "exchange, refresh and device request builders, token response and "
     "ID-token parsing with every refusal typed, the callback parse, the "
     "device flow, the loopback callback listener on a fake clock, and the "
     "ast contract over the source", 68),
    # TYPED: a fixed case table.  Every row that needs the host's libbrotlidec
    # or libzstd is recorded as INFO when the library is absent rather than
    # omitted, so the count is the same on every host.
    ("mcp_decoders", run_mcp_decoders,
     "the ctypes brotli and zstd decoders: the per-platform load order under "
     "a stubbed platform with zero find_library calls on Linux and only "
     "absolute paths handed to dlopen on darwin, the fixtures byte-exact, the "
     "output budget at its exact boundary, bombs and the window-27 frame "
     "refused, legacy zstd frames refused at the start and mid-stream, a "
     "one-line LookupError when the library is absent, lying libraries "
     "refused, a two-thread race on the first load, and the generator block "
     "contract", 150),
    # TYPED: group A reads ONE committed fixture and never iterates a
    # directory, so committing more Chrome captures moves this number by zero.
    # The one row that depends on the local OpenSSL (a stdlib client answering
    # the scripted HRR) is recorded as INFO rather than omitted when it cannot
    # run, so the count is the same everywhere. The one case that re-exports
    # every committed fixture under tests/files/chrome/ is ONE case however
    # many fixtures it reads (INFO when there are none).
    ("chrome_capture", run_chrome_capture,
     "the Chrome capture harness, the independent oracle the stdlib Chrome "
     "client is measured with: JA3/JA4 of one committed Chrome 153 "
     "ClientHello against a JA4 written from the spec and the value R-0017 "
     "recorded, HPACK against RFC 7541 C.1-C.6, serve driven over loopback "
     "(h2 streams sharing one HPACK state, h1 over TLS, plaintext h1, the "
     "scripted HelloRetryRequest, owner-only records, path labels refused, "
     "control bytes escaped in the summary), diff naming one removed "
     "signature algorithm and not a hostname-length change nor a one-sided "
     "resumed group, export refusing a foreign cookie name or value, a "
     "non-loopback Referer, credential headers and unscripted bodies, never "
     "writing through a planted .tmp symlink, while "
     "re-exporting the committed fixtures unchanged, and every non-loopback "
     "bind refused", 52),
    # TYPED: a fixed case table.  The rows that need an OpenSSL binary (the AES
    # ECB cross-check, and the two ML-KEM directions that need >= 3.5) and the
    # optional tests/files/mlkem/ row are recorded as INFO rather than omitted
    # when they cannot run, so the count is the same on every host.  So are
    # the seven openssl s_server rows (>= 3.5) and the P-256-only ssl server
    # row when set_ecdh_curve does not restrict the TLS 1.3 groups, and the br
    # and zstd session rows when libbrotlidec / libzstd is absent, and the
    # group O uv stdio row when uv is absent or cannot prepare its env.  The
    # AES-256-GCM floor row is INFO in [floor/10, floor): only a tenfold
    # shortfall FAILs, because a wall-clock rate flaps under load.
    ("mcp_chrome", run_mcp_chrome,
     "the stdlib Chrome client's crypto and TLS 1.3 layers (Gates G3, G4, G5): "
     "X25519 (RFC 7748), P-256 ECDH (RFC 5903), AES-GCM (Wycheproof), "
     "ChaCha20-Poly1305 (RFC 8439), HKDF (RFC 5869) and the RFC 8448 key "
     "schedule as pasted vectors, ML-KEM-768 cross-checked with OpenSSL both "
     "ways; the ClientHello judged by chrome_capture's parser and diff against "
     "the Chrome 154 captures; TLS 1.3 against MemoryBIO, loopback and "
     "openssl s_server peers, the RFC 8448 trace and a scripted peer (ALPS, "
     "ceilings, KeyUpdate, every refusal), and the HelloRetryRequest path; "
     "HPACK against RFC 7541 and every committed HEADERS block, the header "
     "profiles and caller-header rules; HTTP/2 against a test-side RFC 9113 "
     "responder (first flight, every flood and ceiling at N and N+1); "
     "the whole session over loopback h2 / h1-over-TLS / cleartext peers "
     "(redirects, downgrade, injected decoders, cookies and their caps, "
     "cross-origin headers and Referer); "
     "body and decode limits, on_headers, one row per SSRF address form, the "
     "public-only policy and the ordered opener against recorders, per-hop "
     "vetting and the re-vetted reconnect; the TLS 1.2 fallback verified by "
     "the test CA, *_PROXY ignored, AST rows for trust material, the "
     "environment and the late binding of _ch_open_socket; "
     "the verified default transport vs transport=\"chrome\" (labels, "
     "verified: refusals, parity, the weakest-hop chain label, the decoded "
     "cap, the call-site AST rule with a planted control); "
     "search_github's generated copy driven over loopback (the captured cors "
     "GET and referer, create_session()'s policy on its one Chrome transport, "
     "a redirect into metadata space refused, the block handling over stub "
     "sessions with no second attempt, rotation and a dropped failed session, "
     "the F32 cap, AST rows with planted controls, the NFR-5 startup delta as "
     "INFO); "
     "search_duckduckgo's generated copy the same way (the warm-up then the "
     "cors POST on one connection vs the cors-post capture, the Bing GET vs "
     "the navigate capture, the policy rows, a DDG block switching the run to "
     "Bing and a Bing 403 blocking the query, the reflected-text predicate "
     "rows with their substring controls, rotation counting queries, the F32 "
     "cap and the single parse, AST rows); "
     "both hosts' main() exiting 1 on a block with one blocked line, and "
     "under a network guard (-h / --help print usage and exit 0, an unknown "
     "option exits 2, no network entry point reached, a control that the "
     "guard stops a real query); "
     "mcp-webfetch's handle_fetch over loopback with stub bs4/markdownify "
     "(the verified default with the test-CA factory injected, the "
     "profile=chrome opt-in and its impersonate alias, 403 x3 with the hint, "
     "max_bytes, headers before size per transport, header refusals before "
     "the cache, the policy split, every transport label, cache admission "
     "and the S3 chain-honesty rows, the status reply, the security-review "
     "fix rows (cache keyed on allow_private, 0700/0600 modes, printable URL "
     "and header echoes), one uv stdio row); "
     "every refusal and the hmac.compare_digest tag gate, the generator block "
     "contract, and the AES-256-GCM decrypt floor", 846),
    # TYPED, not None, although it is one-plus-one cases per server: here a
    # server appearing WITHOUT a declared row is the defect, so a count that
    # moves when the roster moves is the alarm working rather than noise.  That
    # is the opposite of mcp_footprint's reasoning above, and deliberately so.
    ("read_loop", run_read_loop,
     "every MCP server's read loop carries ADR 0008's shape: a single-thread "
     "reader executor no handler can take, and one task per message -- with "
     "the pool/coroutine split declared per server rather than inferred", 55),
    # TYPED for read_loop's reason: a server that appears without a declared
    # reclaim class is the defect, so a moved count is the alarm working.  The
    # two per-server groups make it 2N + a fixed tail.
    ("cancel", run_cancel,
     "every MCP server honours notifications/cancelled on its read loop: a "
     "request-id registry forgotten on completion, a hook before dispatch "
     "that refuses a bool or float requestId and never replies, initialize "
     "never cancellable, a dispatch target that lets the CancelledError "
     "through, and what a cancel reclaims declared per server and MEASURED "
     "for the kill, lsp-cancel and pg-cancel classes -- the PostgreSQL "
     "CancelRequest and its drain driven against a loopback fake server", 92),
    # TYPED for read_loop's reason above, not mcp_footprint's: a server that
    # appears without a declared row is the defect, so a moved count is the
    # alarm working.  The two per-server groups make it 2N + a fixed tail.
    ("wire_log", run_wire_log,
     "every MCP server's wire logging is structure only -- protocol metadata "
     "and argument KEYS, never a payload body or value (F12/CWE-532), with "
     "the shape each site may log declared per server rather than inferred, "
     "every peer-chosen method, id, tool name and key logged through the "
     "generated _log_value (CWE-117), "
     "and MCP_SKELETON.md's own sample lifted by script and gated the same way",
     68),
    # TYPED for the same reason as the two above.  The count is 30 declared
    # sites + 19 servers + 4 roster + 11 control + 4 hygiene: the site total is
    # 19 layer-W plus 11 layer-D, so a server that gains or loses a catch-all
    # moves it, which is the alarm working.
    ("handler_crash", run_handler_crash,
     "every MCP server's tool-handler catch-all leaves a traceback at a level "
     "the default WARNING configuration emits -- at BOTH site layers, the "
     "McpServer wrap and the module-level dispatcher, each declared per server "
     "rather than inferred, with the format string required to be a literal so "
     "a payload cannot be interpolated into a log that IS written", 68),
    # TYPED for the same reason again.  The count is 6 escapers + 6 rendered
    # rows + 1 coupling + 3 unpadded + 2 documented + 2 structure + 1 jenkins
    # two-space + 1 jenkins newline + 4 roster + 12 control + 4 hygiene (the
    # sixth escaper, rendered row and unpadded row are the R-0042 board): a
    # renderer that arrives or
    # changes class moves it, which is the alarm.  The control group is the
    # large one on purpose -- six defective escapers plus a four-way
    # parser/renderer pairing, because this suite's one renderer-level defect
    # lived in two functions and in neither alone.  The unpadded pair and the
    # jenkins two-space case answer ADR 0016's two open questions (R-0011); the
    # jenkins newline case closes the finding that measurement left open.
    ("table_cells", run_table_cells,
     "a rendered table cell cannot forge a column boundary: every renderer "
     "either escapes its own delimiter and documents the scheme where the "
     "model reads it, or is whitespace-aligned and has none to escape -- "
     "with reversibility a SEPARATE clause, because an encoder that does not "
     "escape its own escape character passes a column count; a pipe renderer "
     "declared unpadded charges no row for another row's width, a jenkins "
     "two-space cell is measured not to forge against the aligned reader, "
     "and a CR/LF in a jenkins cell is escaped so it cannot end its row", 42),
    # TYPED for the same reason again.  The count is 3 clauses x 19 servers +
    # 2 fleet + 3 roster + 18 control + 3 hygiene: a server arriving without
    # being analysed IS the defect here, so a count that moves when the fleet
    # moves is the alarm working.  The control group is the large one on
    # purpose -- this gate can never be observed red against the live tree,
    # because the fleet converged before it was written, so the planted
    # defects are the only evidence that it detects anything at all.
    ("protocol_version", run_protocol_version,
     "every MCP server declares the handshake protocol version once, as the "
     "first member of class McpServer, and the initialize reply READS that "
     "member instead of restating the literal -- the shape the live smoke "
     "handshake structurally cannot see, since a server inlining the RIGHT "
     "string is indistinguishable on the wire from one reading the constant, "
     "with the fleet's agreement asserted BETWEEN the files so the suite "
     "never holds a copy of the number it polices", 83),
    ("forge_dispatch", run_forge_dispatch,
     "forge_call dispatch: `status` answers exactly what the empty call "
     "answers on all four of its paths, each with a control proving the "
     "fixture took it, and is advertised wherever the function list is", 13),
    # TYPED: 5 rules x 9 hosts + 1 judged-keys + 5 control + 2 hygiene. A
    # host that learns to refuse an unknown parameter without a row here IS
    # the defect, so a count that moves when that roster moves is the alarm
    # working.
    ("param_contract", run_param_contract,
     "the accepted-parameter tables of the nine hosts that learned to refuse "
     "an unknown key: every callable function has a row, every key a handler "
     "reads (followed through its helpers, by AST) is accepted, nothing "
     "accepted goes unread, every parameter the host's skill and tool "
     "description name is accepted, every alias lands on an accepted key, "
     "and jenkins' injected project-scope job_path is never what is judged "
     "-- each rule proven to fire on a planted defect", 53),
    ("py_deps", run_py_deps,
     "pure Python 3.9 + stdlib: every non-stdlib import allowlisted and "
     "find_spec-guarded (never except ImportError), no removed stdlib module, "
     "every file parsing as 3.9 SYNTAX, the stdlib Bing parser pinned to "
     "lxml's fields, every ctypes system library declared, no 3.10+ API "
     "in the new sources, and webfetch's PEP 723 deps bounded with its lxml "
     "builder declared", 65),
    # TYPED: a fixed case table (A 6, B 9, C 5, E 2).
    ("search_parsers", run_search_parsers,
     "the DDG lite and grep.app result parsers as single html.parser passes "
     "(R-0057, F32): the fields the regex parsers produced, pinned as "
     "literals, the href attribute rather than the first href= substring, a "
     "line number over nine digits refused, every grep.app JSON level "
     "type-checked, and five bodies shaped from the old worst cases (four "
     "regexes and the Bing tree builder's end-tag scan) one KiB under "
     "SEARCH_MAX_BYTES parsed within a wall-time bound in a child", 22),
    # TYPED: a fixed case table (A 4, B 9, C 11, D 19, E 7, F 15, G 14, H 2).
    ("mcp_search", run_mcp_search,
     "mcp-search in-process with stub sessions and no network: the dispatcher "
     "and its exact unknown-function line, params, aliases and the collision "
     "refusal, the CLI's markdown, a Bing or grep.app block making the call "
     "isError with the other queries' results kept, the DDG -> Bing switch, "
     "no results as success, two threads never inside one endpoint's session "
     "at once, a transport error dropping the session behind a fixed notice, "
     "the ceiling and its note, every cap (filters and refused Unicode "
     "included) answered before any lock, the busy endpoint, a fence the "
     "snippet cannot close, notes logged as structure only, the session "
     "kwargs of the first, rotated and post-error sessions, the render "
     "sanitizer (one-line fields, http(s)-only IDNA URLs, every invisible "
     "Unicode class dropped, both copies agreeing), a cut closing its fence "
     "with block and stop notices outside the cap, -32602 params, the "
     "grep.app schema check, pacing before the warm-up, the call deadline, "
     "a busy endpoint keeping results, _log_value at the log sites and the "
     "run() catch-all answering -32603 live", 81),
    ("webfetch_roots", run_webfetch_roots,
     "mcp-webfetch's two roots (R-0054): --cache-root names the cache "
     "directory itself, default $XDG_CACHE_HOME/web-fetch (absolute only) "
     "else ~/.cache/web-fetch and never the project root, while save_to "
     "stays contained by the project root -- a save_to into the cache dir "
     "refused before the fetch -- with the dispatcher, the status reply, the "
     "tool description and both CLI entry points (~ expanded) threading it, "
     "HOME and XDG_CACHE_HOME pinned and the real default dir asserted "
     "untouched, network stubbed; eviction least recently used -- a hit "
     "refreshes the entry's mtime and leaves its content and fetched_at "
     "alone; save_to refusing .git/, .claude/hooks/, .claude/settings*.json "
     "and the root .mcp.json case-folded and through a symlink, before any "
     "fetch (R-0062)", 38),
    # TYPED: the suite is a fixed case table (groups A-I, K, L = 57 in
    # Phase 1, plus group J's 33 HTTP cases = 90; the round-1 review added
    # A13, G6 and J34-J38 = 97; the round-2 review added J39 and J40 = 99;
    # the round-3 review added G7, J41, J42 and L4 = 103; R-0069 added J43,
    # the stdlib's pre-auth refusals = 104; R-0076 added J44, 100 Continue
    # only after auth = 105; R-0075 added J45, the ready file removed on an
    # ordered shutdown = 106; R-0067/R-0068 added G8 and J46, a NaN /
    # Infinity token or an over-long integer literal answered -32700 on both
    # fronts = 108; R-0072 added K5, the front's injected policy pinned,
    # and J47, a weak bearer token refused at start = 110; R-0089 added J48,
    # the shorter pre-auth header bound until auth = 111; R-0090 added J49,
    # the distinct-character token floor on both sources = 112; the relist
    # work added group M, M1-M11 = 123; the security round 1 -> 2 added
    # J50, --require-token-file refusing MCP_PROXY_TOKEN, run RED first =
    # 124), so a count that moves is the alarm, not noise.
    ("mcp_proxy", run_mcp_proxy,
     "mcp-proxy, the aggregating relay: config refusals, eager start and "
     "duplicate-tool refusal, id/progress remapping, cancel forwarded with "
     "the child's own id, restart with backoff/budget/disable, framing, "
     "timeout, orphan-free shutdown, static AST rules, sandbox hygiene; "
     "Streamable HTTP: a weak or low-distinct bearer token refused at "
     "start, bearer, Origin/Host, session lifecycle, every "
     "tools/call as SSE with keepalive, caps, shutdown with live traffic, "
     "total header deadline and the shorter pre-auth one, strict headers, "
     "deep nesting, malformed "
     "header lines, id-less initialize, request id shape, fixed body-less "
     "stdlib refusals, 100 Continue only after auth, ready file removed "
     "on shutdown, NaN / over-long numbers refused -32700; the opt-in "
     "per-child relist (bool-only, default off byte-identical, name set "
     "frozen, stale on failure, coalesced, min interval, cancelled on "
     "stop)", 124),
    # TYPED: a fixed case table (A 19, B 23, C 22, D 7, E 34, F 9, G 13,
    # H 26, I 17, N 22 = 192), the rows of plan Steps 7-13, so a count that moves
    # is the alarm, not noise.  F9 (validation round 1) drives layer D live
    # through McpServer.run() -- the smoke probe answers -32602 now and no
    # longer reaches the catch-all.  E (Step 13, the spec's acceptance escape
    # suite E1-E18 plus the risk rows E19-E34) is live, NOT red first (the
    # code exists): every row is INFO off macOS and under a nested Seatbelt
    # (P23) -- counted, never omitted.  D (Step 11, pure argv golden) is written RED
    # (build_sbx_argv is missing) and turns green with Step 11; I (Step 11,
    # live runs) and G (Step 12, startup plans / refresh) are written RED
    # (the engine and plan machinery are missing, checked before any runner
    # starts) and turn green with Step 12; the I and G live rows are INFO
    # off macOS and under a nested Seatbelt (P23) -- counted, never omitted.
    # The C rows are written RED (validate_run_params is
    # missing) and turn green with the Step 9 implementation.  The H rows
    # (H19 added by M0:9: the flatten remover on a rename-chained 2079-level
    # tree in a NOFILE-256 child) are written RED (the Step 10 workdir and
    # remover functions are missing); H1-H14, H17 and H19 turn green with
    # Step 10, H15, H16 and H18 (run_one + RunnerState/PlanCache) in Step 12;
    # H14 is INFO off darwin and H15/H16 INFO off macOS or under a nested
    # Seatbelt -- counted, never omitted.
    # F6 is live and recorded INFO under a nested Seatbelt (P23), never
    # omitted; B18 is the one live Mach-O row (the host's Homebrew python3)
    # and is INFO off macOS or without a keg -- counted, never omitted.  The
    # B rows are written RED (the Mach-O reader is missing) and turn green
    # with the Step 8 implementation.
    # EXPECTED FAILING from Step 7 through Step 11 on exactly the rows
    # recorded RED (F5, F6, then H15, H16, H18, D1-D4 until Step 11,
    # I1-I11, G1-G13), green from Step 12.  Security round 1 added B21-B23
    # (the closure's rpath stack, keg walk and plan deadline bounded) and
    # H20-H24 (out/ listing and remover listing bounded, darwin fallbacks
    # fd-relative only, the survivor sweep, the out/ path cap) and A11 (the
    # worst-case reply serialized through the real tool-result path stays in
    # 16 MiB: 13 chars per byte, not 12), all written RED; H22/H23 are INFO
    # off darwin -- counted, never omitted.  The per-interpreter "site" key
    # added A12 (a real bool, python3 only), D5 (site true drops -S only)
    # and I12 (live: a venv's site-packages importable only with site true;
    # INFO off macOS and under a nested Seatbelt), all written RED.  The
    # egress work added A13-A15 (the optional "network" object), D6/D7 (the
    # egress argv), group N (12, the allowlisting CONNECT proxy in-process
    # against a stub resolver/connector) and I13-I17 (live: allowed HTTPS,
    # refusals, direct sockets, DNS, no-network unchanged; I13 INFO when the
    # host itself has no network), all written RED: 154 -> 176
    # (A 15, D 7, N 12, I 17).  Security round 2 added A16-A19 (hex-letter
    # TLDs, "*." over two labels, network.nat64, extra_reads over the work
    # root) and N19-N22 (hex-letter hosts, the IPv4 denylist, configured
    # NAT64, the process-wide connection budget): 184 -> 192.
    ("mcp_sandbox", run_mcp_sandbox,
     "mcp-sandbox, the sandboxed script runner: the one JSON config file "
     "(unknown keys at the top and in limits, every numeric range at its "
     "edges, the interpreter-name enum, the config file's mode and symlink "
     "rules, sbx absolute and not world-writable, the worst-case response "
     "bound at exactly 16 MiB, as_mode absolute refused on darwin), the "
     "stdlib Mach-O reader on synthetic thin/fat images (every header bound "
     "refused as MachOError, @rpath/@loader_path/@executable_path, weak vs "
     "strong missing, cycles, system paths, the image cap, the framework "
     "launcher, the targeted keg scan capped at 512, the live python3 "
     "closure), run_script parameter validation (every relative-path rule, "
     "the NFD+casefold collision and file-vs-dir prefix keys, the file "
     "count, the running decoded total, strict base64, timeoutMs int-not-"
     "bool and clamped, entry in files, args, stdin bytes, an unknown member "
     "with did-you-mean -- each invalid_params naming the member, never the "
     "value), the "
     "sandbox_call surface (one tool, did-you-mean, unknown params, "
     "non-object params -32602, the capabilities shape, capabilities during "
     "two runs, layer D internal + log.exception in-process and through "
     "McpServer.run(), the unflagged status reply) and the workdir lifecycle (0700 hex workdir, O_EXCL|O_NOFOLLOW "
     "materialisation, out/ collection skipping symlinks, hard links and "
     "FIFOs, the free-space precheck, the startup sweep's name rule and "
     "shared deadline, the flatten remover against chmod 000/0500/0100, "
     "uchg, deny ACEs and rename-chained deep trees, the quarantine and its "
     "degraded cap), the sbx argv golden (darwin, linux + seccomp, the "
     "absolute entry, the runner's two-key environment to sbx), live runs "
     "(hello in python3/bash/node, a non-zero exit, the UTF-8 cut, invalid "
     "UTF-8, stdin, out/ files, cancel, an sbx refusal, an engine failure "
     "after spawned) and startup plans (every refused start, the AS limit, "
     "liveVerified, refresh on a re-pointed hop, a refresh failure, a "
     "refresh inside its slot, a cancel during a refresh) and the escape "
     "suite (reads, stat, writes, network, fork/CPU/output/memory bombs, "
     "out/ links and FIFOs, the exact env, hostile workdirs, busy, fds, tty, "
     "planted execs, signals, a forged status, rlimits, another process's "
     "environment, setuid, sbx killed mid-run -- each beside a positive "
     "control) and the security-round-1 bounds (rpath stack, keg walk "
     "dirs/depth/entries, the closure's plan deadline, out/ and remover "
     "listings, fd-relative darwin fallbacks, the survivor sweep, the out/ "
     "path cap, the worst-case reply serialized within 16 MiB) and the "
     "egress allowlist (the network object, the egress argv, the CONNECT "
     "proxy in-process, live allowed/refused/direct/DNS/no-network rows)",
     192),
    # TYPED: a fixed case table (A 9, B 10, C 6, D 8, E 5, F 14, G 4, H 6,
    # I 12, J 13, K 2, L 3 = 92), the rows of plan Steps 16, 17 and 18 (J1 is live:
    # INFO off macOS and under a nested Seatbelt, counted either way), so a
    # count that moves is the alarm, not noise.  K (validation round 1) is
    # layer D in-process (K1) and through McpServer.run() (K2) -- the smoke
    # probe answers -32602 now and no longer reaches the catch-all.  The
    # security round 1 -> 2 added B9, B10 (K3), I12 (M2), J11 (K4), J12 (M2)
    # and J13 (O2), each run RED first = 83.  The lazily created tier root
    # added A6-A9 and E4-E5 (a missing root or skills.json is an empty tier
    # with no error; ENOTDIR still refused), run RED first = 89.  The flat
    # params schema added L1-L3 (a schema key the function does not read is
    # dropped, a key outside the schema still refused), run RED first = 92.
    # The empty-value rule added L4-L6 (a schema key holding "" / [] / 0
    # counts as omitted; name "" is "name is required"), run RED first = 95.
    ("mcp_skill", run_mcp_skill,
     "mcp-skill, the read-only skill loader: startup flags (no roots, a "
     "group-writable root, the runner URL/token pair, the --max-description "
     "range, one tool), the exact skills.json schema with tier errors and "
     "per-entry reasons, the four-line SKILL.md frontmatter, the rendered "
     "description (preamble, [private] shadowing, sorted lines, UTF-16 "
     "units, the over-ceiling refusal naming both tier sums while tools/list "
     "still succeeds, the name enum, the stat-keyed cache), the tier-root "
     "dir rules (symlink or non-directory invalid, the root never listed), "
     "load's file hygiene (dotdot, absolute, nested, uppercase, symlink, "
     "hardlink, FIFO, symlinked subdir, over-cap, binary, a dir swapped "
     "between operations), the unframed sanitized body, the "
     "validate report shape, the sanitizer hand copy's text and "
     "behaviour parity with Scripts/_mcp_websearch.py, the runner HTTP "
     "client (loopback-only http URL, token file rules re-read per run and "
     "never logged, no redirect, the 16 MiB + 64 KiB answer cap, SSE "
     "keepalive, one "
     "retry on 404, the session DELETEd on shutdown) and run (end to end "
     "through the real sandbox, unlisted and private scripts refused, "
     "interpreter from the index, refs sent, symlinks refused, hostile output "
     "sanitized and unframed, busy passed through, an over-cap body refused "
     "before sending, the smaller serialized encoding per file) and a "
     "handler crash (layer D internal + log.exception, in-process and "
     "through McpServer.run()) and the flat params schema (a schema key the "
     "function does not read or holding its type's empty value dropped, "
     "a key outside it refused)",
     95),
    # TYPED: a fixed case table (A 50, B 32, C 16, D 17, E 15, F 31, G 23,
    # H 6 x 4 kinds + H7 + H8-H9 = 27, I 16, J 44, K 29, L 20, M 23, N 42,
    # O 18, P 4, Q 18, R 14, S 5, T 13, U 3 = 460), so a count that moves is the alarm, not noise.
    ("llm_router", run_llm_router,
     "llm-router, the Anthropic Messages router: config refusals (0600, "
     "duplicate keys), bearer/Host/Origin/misplaced token, GET /v1/models, "
     "SSRF policy and verified TLS, passthrough relay, llama.cpp quirk "
     "registry, Mistral translation and stream state machine, the codex "
     "and openai Responses kinds (request translation and stream), OAuth "
     "token store, login and config write-back, timeouts and disconnects "
     "per kind, upstream failure log, image endpoints (openai, a local "
     "stable-diffusion.cpp sd-server with an optional bearer api_key, and "
     "the experimental codex ones), "
     "streamed image answers (openai), advertised image sizes, "
     "secret leaks, static rules, "
     "hygiene", 460),
    # TYPED: a fixed case table (A 17, L 45, R 23, D 22, S 22, Q-sched 16,
    # Q-http 5, T 15, P 43, U 20, G 6, E2E 5 = 239), so a count that moves is
    # the alarm.
    ("gpu_router", run_gpu_router,
     "gpu-router, the supervisor and proxy for stable-diffusion.cpp "
     "sd-server and inferNO-style LLM apps: config refusals, one app loaded "
     "at a time with switch on demand, the bounded queue and scheduler, "
     "/v1/unload (queued and ?force=1, through any model id), crash retries "
     "and the DISABLED state, the kill ladder and process-group sweep, "
     "byte-exact relay through stub children, the per-kind routes, body caps "
     "and readiness probe, OpenAI-shaped refusals, bearer auth, "
     "structure-only logs, static rules, end-to-end through llm-router and "
     "across the two app kinds", 239),
    # TYPED: a fixed case table (A 7, E 3), so a count that moves is the alarm.
    ("tshark_fields", run_tshark_fields,
     "mcp-tshark analyze's `fields` parameter, in-process with tshark "
     "stubbed: a JSON list and a comma-separated string build the same -e "
     "argv, a non-str / non-list value is a ValueError refusal and an error "
     "reply, never an AttributeError", 10),
    # TYPED: a fixed case table (A 7, B 3, C 7, D 5, E 14, F 5, H 16, G 2), so
    # a count that moves is the alarm.
    ("fetch", run_fetch,
     "Scripts/fetch.py, offline: no import side effects, --help and usage "
     "errors, a refused URL as one `fetch.py:` line and exit 1 without a "
     "retry, the sha256 cache (a pre-seeded hit with no network, --out, "
     "--meta, binary stdout, damaged and non-2xx entries ignored), the "
     "backoff schedule and Retry-After parsing, and the pacing / retry / "
     "session-rebuild loop on a scripted session, and the offline POST path "
     "(validation, cache bypass, cors request, CLI usage errors)", 59),
    # TYPED: a fixed case table (A 12, B 6, C 9, D 1, E 2).
    ("task_scripts", run_task_scripts,
     "the task-script family's explicit --yaml PATH (task-update, "
     "task-show-details, task-implementation-plan), each run as a child "
     "beside a decoy ./requirements.yaml that must stay untouched and unread: "
     "position-independent --yaml / -y / --yaml=, an unreadable file as one "
     "stderr line and exit 1 with no fallback to the cwd, and an explicit "
     "--yaml without --doc skipping the default plan document with one "
     "warning rather than printing another feature's plan, the deployed copy "
     "byte-identical", 30),
    ("smoke", run_smoke,
     "MCP JSON-RPC plumbing invariants across the fleet", None),
]

SUITE_NAMES = [name for name, _runner, _desc, _count in SUITES]


def usage():
    print(__doc__)
    print("Valid suite names: %s" % ", ".join(SUITE_NAMES))


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        usage()
        return 0

    unknown = [n for n in opts.names if n not in SUITE_NAMES]
    if unknown:
        print("error: unknown suite name(s): %s" % ", ".join(unknown),
              file=sys.stderr)
        print("valid names: %s" % ", ".join(SUITE_NAMES), file=sys.stderr)
        return 2

    selected = [s for s in SUITES if not opts.names or s[0] in opts.names]

    reports = []
    drift = []
    for name, runner, desc, declared in selected:
        label = desc if declared is None else "%s (%d cases)" % (desc, declared)
        print("\n" + H.BANNER)
        print("SUITE %s -- %s" % (name, label))
        print(H.BANNER)
        report = runner(opts)
        reports.append(report)
        if declared is not None and report.count != declared:
            drift.append((name, declared, report.count))

    total_cases = sum(r.count for r in reports if r.unit == "cases")
    total_pass = sum(r.passed for r in reports if r.unit == "cases")
    total_fail = sum(r.failed for r in reports if r.unit == "cases")
    total_info = sum(r.info for r in reports if r.unit == "cases")
    total_servers = sum(r.count for r in reports if r.unit == "servers")
    ok = all(r.ok for r in reports) and not drift

    print("\n" + H.BANNER)
    print("AGGREGATE")
    print(H.BANNER)
    for report in reports:
        print("  " + report.line())
    bits = ["%d suite%s" % (len(reports), "" if len(reports) == 1 else "s")]
    if total_cases:
        bits.append("%d cases" % total_cases)
    if total_servers:
        bits.append("%d servers" % total_servers)
    print("  %s -- %s" % (", ".join(bits), "ALL PASS" if ok else "FAILURES"))
    if total_cases:
        print("  cases: %d pass, %d fail, %d info" % (total_pass, total_fail,
                                                      total_info))
    if drift:
        print("\nCASE COUNT DRIFT -- a declared count no longer matches the run:")
        for name, declared, actual in drift:
            print("  %-18s declared %d, ran %d" % (name, declared, actual))
        print("  Fix the number in the SUITES table in this file (it is the")
        print("  only place it is written down), then re-run.  This is a hard")
        print("  failure on purpose: a stale count gets quoted as fact.")
    if not ok:
        failing = [r.name for r in reports if not r.ok]
        if failing:
            print("\nFAILING SUITES: %s" % ", ".join(failing))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
