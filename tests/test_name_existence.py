#!/usr/bin/env python3
"""Cross-checks ALL MODEL-FACING TEXT against the LIVE MCP FUNCTION INVENTORY.

This repo is the source of a Claude Code tooling fleet: `ClaudeCode/**` holds
the prompts (agent definitions, SKILL.md files, `_lib` fragments, hooks,
CLAUDE.md) and `Scripts/mcp-*.py` holds the MCP servers those prompts tell the
model to call.  Nothing used to verify that the two sides agree, so names rotted
silently: `luals_document_symbols` was prescribed for months although the real
name is `luals_document_outline`; `mcp-psql` sat in an agent's `tools:` while
the server was unregistered; `create_temp_dir` shipped with zero callers.

Every one of those is a pure set comparison -- ZERO model calls -- which is why
this is a test and not an occasional audit.

TWO CORPORA, because a prompt is a prompt wherever it is stored:
  * `ClaudeCode/**`                     -- the prompt corpus proper
  * `Scripts/mcp-*.py` MODEL-FACING TEXT -- every MCP tool `description` string
    and module docstring.  Claude Code injects these into the system prompt on
    every single request, so a dead reference inside one is the same defect as a
    dead reference in a skill, merely embedded in a Python string literal.  Six
    such live dead references were found by hand before this gate existed; none
    of them lived in `ClaudeCode/**` at all.

    Registration decides the severity, and that distinction is the whole point:
      - inside a REGISTERED server -> the text IS rendered -> FAIL
      - inside an unregistered one -> nothing ever reads it -> INFO (inert)
    `mcp-clangd`, `mcp-cuda`, `mcp-lua-lsp` and `mcp-webfetch` are retired /
    unregistered today, so their routing text is inert by definition.

Three directions, deliberately asymmetric:

  Direction 1  text -> inventory   (FAILS the suite)
      A function name / dispatcher tool / server the text PRESCRIBES must
      exist.  A prescribed name no server implements is dead instruction text.

  Direction 2  inventory -> corpus   (INFO only, never a failure)
      A server function NO `ClaudeCode/**` file mentions is an undiscoverable
      capability (the `create_temp_dir` failure mode).  Visibility, not a build
      break.  Kept anchored on the PROMPT corpus on purpose: a function only its
      own server's description mentions is still one no agent was ever told
      about, so the per-server row states how many of its orphans at least
      appear in server-side description text.

  Direction 3  agent GRANTS <-> agent PRESCRIPTIONS   (see D3 section below)
      D1 catches "a prompt prescribes a tool that does not exist".  The MIRROR
      failure is invisible to it: a tool that EXISTS, is prescribed to an agent,
      and that agent was never GRANTED it.  Found live -- `p:minion-builder` was
      told to call `inspect_call` and it is in no agent's `tools:` at all, so the
      model silently substituted a documented workaround instead of erroring.

-----------------------------------------------------------------------------
Direction 3 -- the grant surface (group I)
-----------------------------------------------------------------------------
An agent definition makes TWO typed claims that must agree:
  * frontmatter `tools:`  -- the tools the agent may call (the GRANT)
  * its body              -- the tools the agent is told to call (the PRESCRIPTION)

  3a  dead grant     (FAIL) a `tools:` entry naming an MCP tool the live
                     inventory does not have.  Inert at runtime (verified: all
                     15 agents load with a bogus entry present) but misleading,
                     and a 21-file manual sweep already cleaned these once.
  3b  missing grant  (FAIL / INFO) the body prescribes a tool that EXISTS, is
                     REGISTERED, and is absent from this agent's `tools:`.
  3c  ungranted      (INFO)  a registered dispatcher NO agent's `tools:` names
                     -- the tools-level analogue of Direction 2.  This is the
                     row that catches the live `inspect_call` defect.
  3d  inert key      (FAIL) an `mcpServers:` key in a PLUGIN agent's
                     frontmatter.  Claude Code IGNORES `hooks:`, `mcpServers:`
                     and `permissionMode:` in a plugin-shipped agent -- they
                     are dropped at load time, for security reasons -- so the
                     key confers nothing while reading as a granted
                     capability.  See the 3d section below for the doc fact,
                     the severity defence and the R6 split.

SCOPE: `ClaudeCode/agents/*.md` frontmatter, and nothing else.  NOT "every file
with a `tools:` line": `tools:` also appears ~20 times inside fenced examples in
p:writer-agent's SKILL.md, as an ARCHITECTURE.md template placeholder, and as
prose in p:mcp-inspect ("tools: interpreters, package managers, compilers").
None of those is a grant, and treating them as one would flood the group.

IN SCOPE BY SHAPE, never by allowlist: only `mcp__<server>__<tool>`,
`mcp__<server>` (whole-server grant) and the bare `<x>_call` dispatcher spelling
are MCP names.  `Read` / `Write` / `Edit` / `Bash` / `Glob` / `Grep` / `Agent` /
`Skill` / `TodoWrite` / `WebFetch` / `WebSearch` are BUILT-INS and are skipped
because they do not match those shapes -- deliberately not because they are on a
list, since a hardcoded built-in list rots the moment Claude Code ships a new
tool and would then FAIL every agent that adopts it.

3b reuses D1's EXTRACTION verbatim -- the same `Prescription` objects R1/R2/R3/R5
already produced -- so there is no second, drifting scanner.  Only
dispatcher-level evidence is used: R5's `<x>_call` mention, R1's `mcp__s__t`
token, and the dispatcher hint R2/R3 attach to a call.  Frontmatter lines are
excluded (that is R1/R6's surface).

3b SEVERITY.  A false FAIL here would fire on every prompt edit, which is this
repo's main activity, so a mention only FAILS when it is a first-person
prescription BY this agent.  Three documented suppressors turn a candidate into
INFO-with-evidence instead:
  1. NOT GRANTABLE -> not a finding at all.  A tool whose server is absent from
     ~/.claude.json cannot be granted, so naming it is a retirement note, not a
     missing grant.  This is the same registration-driven reasoning R5 uses, and
     it is what makes all 11 corpus mentions of `clangd_call` / `cuda_call` /
     `luals_call` non-findings.  Asserted explicitly (see the retirement case in
     group H), because a suppressor nobody tests is a hole.
  2. ILLUSTRATIVE context -> INFO.  `e.g.` / `for example` / `such as` / `etc.`
     on the mention line.  "Most servers expose one `*_call` dispatcher (e.g.
     `forge_call`, `gdc_call`, `lldb_call`)" is toolbelt-discovery advice, not a
     prescription.  (Live: p:minion-builder / `gdc_call`, `lldb_call`.)
  3. NEGATIVE / DELEGATION context -> INFO.  A retirement or "does not exist"
     sentence within +/-1 line, or another agent named on the line (an
     orchestrator may legitimately describe what its DELEGATE calls -- the
     agent's OWN name never counts as delegation).

REMOVED SUPPRESSOR -- `mcpServers:` DISAGREEMENT.  A fourth suppressor used to
demote a candidate to INFO when `tools:` omitted the tool but `mcpServers:` DID
declare its server, on the stated grounds that "whether `mcpServers:` alone
confers the grant is a PLATFORM-semantics question this suite cannot answer".
That question IS answered now (see 3d): in a plugin-shipped agent the key is
ignored at load time, so it can never confer anything and a disagreement with it
is never an excuse.  Its only live subject was p:minion-watson / `context7_call`
-- a REAL missing grant that the suppressor hid, leaving watson silently unable
to do the documentation lookup its own routing table prescribes.  That is the
cost of answering a platform question with a suppressor instead of a fact, and
it is why 3d fails on the key rather than reasoning about it.

NO DOUBLE-REPORTING of D1's surfaces: `mcpServers:` VALUES stay R6's (group D)
and a body mention of a dispatcher NO server exposes stays R5's (group D) --
group I drops it rather than restating it.  3a does report a dead
`mcp__s__t` grant that R1 also sees, on purpose: "this agent may call X" and
"call X" are different claims, and only the group-I row names the agent.  3d and
R6 overlap on purpose too, and are NOT the same claim: R6 judges what the key
SAYS ("mcp-nowhere is not a registered server"), 3d judges that the key EXISTS
("this is ignored at load time, whatever it says").  Both must keep speaking, so
that a key reintroduced with a bogus server name trips both rules at once.

-----------------------------------------------------------------------------
3d -- an `mcpServers:` key in a plugin agent is inert (group I)
-----------------------------------------------------------------------------
THE DOC FACT.  The official Claude Code sub-agent documentation states that
agents shipped inside a PLUGIN do not support the `hooks:`, `mcpServers:` or
`permissionMode:` frontmatter fields: they are IGNORED at load time, for
security reasons.  `ClaudeCode/` IS a plugin -- `.claude-plugin/plugin.json`,
plugin name `p`, installed via a skills-dir symlink -- so every `mcpServers:`
key in this corpus was inert.  Runtime evidence agrees: 4 of the 15 agents never
carried the key and work fine, and all 15 load with it gone.

SEVERITY: FAIL.  The defence is in `Checker.check_mcpservers_key`, which is
where a future reader will be standing when they want to argue with it.  The
short form: the key is not merely redundant, it already HID a real defect from
this very gate (the removed suppressor above), and FAIL is what stops it being
reintroduced from stale docs -- ClaudeCode/ARCHITECTURE.md's agent-frontmatter
template still shows an `mcpServers:` line.  FAIL is safe rather than flaky
because the corpus now carries ZERO such keys: the rule has no live subject, so
it cannot fire on an ordinary prompt edit, only on a regression.

THE ONE SCENARIO THAT MAKES IT WRONG is a copy of these files installed OUTSIDE
a plugin, into ~/.claude/agents/, where `mcpServers:` would be live again.  That
is not how this repo installs, and it is handled by flipping the single named
constant CORPUS_SHIPS_AS_PLUGIN -- a reviewable one-line decision instead of a
silent drift.

D3 LIMITATIONS: a `tools:` typo that breaks the shape (`purity_calll`) is
skipped, not flagged, because shape is what separates MCP names from built-ins.
A body that prescribes only a FUNCTION name (`` `search_for_pattern` ``) without
naming its dispatcher yields no 3b candidate.  A skill or CLAUDE.md prescription
is never attributed to an agent -- which is exactly why 3c exists.

-----------------------------------------------------------------------------
Getting the inventory
-----------------------------------------------------------------------------
The launch table is IMPORTED from `Scripts/_mcp_smoke_test.py` (`SERVERS`) --
single source of truth for the fleet, so a new server is picked up for free and
a deleted one (mcp-compile, gone: 16 -> 15 entries) disappears for free.  The
server-text scan additionally picks up any `Scripts/mcp-*.py` on disk the table
has not caught up with yet.

Per server, two enumeration probes, cheapest-reliable first:
  1. `tools/call` the dispatcher with a bogus function name.  13 of 15 servers
     answer `Unknown function: X. Available: a, b, c` -- one uniform, exact,
     alias-inclusive list, no output-format guessing.  (mcp-forge only answers
     once a parseable `project-forge.yaml` exists, so the probe is retried
     against a throwaway root holding a minimal `version: 1` config.)
  2. `--list` (mcp-git: an allowlist server, it never says "Available"), parsed
     as `^  <name>  <description>` indented rows.
A server that answers neither is recorded SKIP, never silently passed
(mcp-webfetch cannot even start here: no bs4 installed).

Server SOURCES are never imported, only `ast.parse`d -- so an un-importable
server is still scanned, and no import side effect can touch the repo.

TWO layers of name matter and both are checked:
  * the DISPATCHER TOOL name (`purity_call`) -- what appears in `tools:` lists
  * the DISPATCHABLE FUNCTION names + aliases -- what appears in prose

-----------------------------------------------------------------------------
Extraction rules (precision first -- a prose-flagging extractor is worse than
no test, so every rule below requires a form that unambiguously prescribes a
call; recall gaps are listed under LIMITATIONS)
-----------------------------------------------------------------------------
R1  `mcp__<server>__<tool>` tokens, anywhere (frontmatter `tools:` /
    `mcpServers:` lists and prose alike).  `<server>` must be REGISTERED in
    ~/.claude.json (user scope) and `<tool>` must be a tool that server exposes.
R2  `<x>_call(<...>)` with NO space before the paren -- a real call, not a prose
    parenthetical.  Inside it: `function=<n>` / `function: "<n>"`, else the
    leading bare identifier (`purity_call(read_file, ...)`), else a
    slash-separated list (`purity_call(replace_content/replace_lines)`).  The
    no-space rule is what keeps `purity_call (clangd-backed)` and
    `postgres_call (MANDATORY ...)` out; the identifier must not be followed by
    `-` so `purity_call(clangd/luals-backed` cannot leak either.
R3  QUOTED `function="<n>"` / `function: "<n>"` / `function:"<n>"` anywhere.
    Quotes are mandatory: bare `function: string` / `function: null` /
    `function: poluah_websocket_send_frame` are YAML/plan fields in
    task-plan/SKILL.md, not calls.  A dispatcher named on the SAME line scopes
    the check to that server; otherwise the whole inventory is accepted.
R4  Backticked namespaced identifiers -- `` `luals_document_symbols` `` -- but
    ONLY for prefixes the live inventory actually uses (computed, not
    hardcoded: today clangd_ cuda_ luals_ lldb_ gdc_ context7_).  purity, git,
    inspect, forge, wiki, tshark, jenkins, postgres and compile expose
    UNPREFIXED functions, so `` `compile_commands` `` / `` `wiki_root` `` /
    `` `git_dir` `` can never be mistaken for prescriptions.
    `` `luals_find_definition[_at]` `` expands to both spellings.
R5  `<x>_call` dispatcher mentions.  No server exposing that tool -> FAIL.
    Tool exists but its server is unregistered -> INFO (retirement notes in
    p:mcp-clangd / p:mcp-luals / p:mcp-cuda legitimately name the dead
    dispatchers, so failing on those would punish honest documentation).
R6  frontmatter `mcpServers:` list entries -> must be registered.  Since 3d
    landed the AGENT corpus carries no such key at all, so R6 has no live
    subject there.  It is deliberately KEPT rather than deleted, and kept
    exercised by the synthetic fixtures, so that a key reintroduced with a bogus
    server name is still caught on its VALUES by R6 as well as on its PRESENCE
    by 3d.
R7  near-miss INFO: a backticked snake_case identifier that is NOT in the
    inventory but is within edit distance 2 of a name that IS (same 4-char
    prefix).  Catches unprefixed typos R4 cannot see.  INFO only.
R8  bare `mcp-<name>` routing references, SERVER TEXT ONLY -- the shape of the
    real defects (`Build -> mcp-compile`, `Code navigation -> mcp-clangd`).  The
    server must be registered.  Deliberately NOT applied to `ClaudeCode/**`,
    where R1/R6 already cover server grants structurally and where the
    p:mcp-clangd / p:mcp-luals / p:mcp-cuda retirement skills must remain free
    to NAME the servers they declare dead.
R9  a server naming ITSELF (`mcp-postgres.py`'s docstring title saying
    `mcp-postgres`) is an identity statement, not routing: never a failure.  It
    is reported INFO when the self-name differs from the REGISTERED name
    (mcp-postgres.py is registered as `mcp-psql`), because prose that copies
    that self-name would be dead.

SUPPRESSIONS (false positives this suite had to defeat, and how)
  * `purity_call (clangd-backed)`, `purity_call (purity MCP, ...)`,
    `postgres_call (MANDATORY ...)`  -> R2 requires NO space before `(`.
  * `forge_call(targets=["app"])` (a deliberately-WRONG doc example) and
    `function=<placeholder>` / `function=""`  -> R2 rejects an identifier
    followed by `=`, drops the meta-keys function/params/f/p, and abandons a
    call whose `function` value is not a parseable name.
  * `function: string` / `function: null` / `function: poluah_...`  -> R3 needs
    quotes (these are YAML/plan fields in p:task-plan).
  * `` `compile_commands` ``, `` `wiki_root` ``, `` `git_dir` ``  -> R4 only
    trusts prefixes the inventory actually uses; purity/git/forge/wiki/compile
    expose UNPREFIXED names, so those prefixes are not namespaces.
  * `p:mcp-luals` (a SKILL) and `Scripts/mcp-purity.py` (a FILENAME)  -> R8
    excludes a leading `:` and a trailing `.py`.
  * a comment or a `LOG_NAME = "mcp-x"` assignment  -> server text comes from
    `ast`, so only docstrings and `"description"` values are ever read.

LIMITATIONS (documented on purpose)
  * unprefixed function names in bare prose (`` `search_for_pattern` ``) are
    verified only via R2/R3/R7 -- a bare backticked typo on a purity function
    surfaces as an R7 INFO, not a failure.
  * PARAMETER keys are out of scope.  The live `git_call ls-remote
    {"remote": ...}` bug (no such param key, so a bogus `--remote=` is forged)
    is a param-level defect; catching it needs a per-function param inventory.
  * `mcp__<server>__<tool>`: server names must not contain `__`.
  * ~/.claude.json is parsed by SCRIPT and only `mcpServers` names + launch
    argv are read (it is ~100 KB).  Unreadable -> registration checks SKIP.

In-flight churn: findings that belong to a concurrent edit (purity gaining
typeDefinition handlers) are downgraded to INFO with an explicit note.  This
suite is a DETECTOR: it never edits the corpus or a server to make itself green.

Groups:
  A  inventory probe, one case per server (+ ~/.claude.json registration)
  B  scan statistics (INFO) + hygiene: no .pyc may appear anywhere in the repo
     tree, every write must land under .claude/tmp, no child process may be
     given a working root outside it, and both shared temp dirs are watched
  C  D1, prompt corpus -- prescribed FUNCTION names exist
  D  D1, prompt corpus -- prescribed SERVERS / DISPATCHERS exist and are wired
  E  D1, model-facing text of a REGISTERED server (live instruction -> FAIL)
  F  D1, model-facing text of an unregistered server (inert -> INFO)
  G  D2 -- inventory functions no prompt-corpus file mentions (INFO)
  H  negative control -- one synthetic corpus fixture and one synthetic SERVER
     the detector MUST flag, plus precision bait it must NOT flag, plus proof
     that the same fixture read as an unregistered server yields INFO not FAIL,
     plus nine synthetic AGENTS pinning every D3 verdict (FAIL / INFO / silence)
  I  D3 -- agent `tools:` grants vs the tools the agent's own body prescribes,
     plus 3d: no plugin agent may carry an inert `mcpServers:` key

Offline, read-only, ~8s.

SANDBOX DISCIPLINE -- ALL scratch under
`.claude/tmp/test_name_existence/run-<unique>/` (gitignored, per-project,
inspectable, one subdir per run so a concurrent instance's teardown cannot
delete a live run's fixtures), NEVER the shared system temp dir, never the repo
tree, never beside a source file.  Removed on exit unless --keep.  This
is enforced structurally rather than remembered: `write_text()` is the only
write path in the module and records every target, `probe_argv()` is the only
child launcher and records every command line (rewriting the `--project-root
/tmp` the imported launch table supplies), and group B FAILS if any recorded
path or child root escapes the sandbox.  Teardown is a `finally`, so an
exception anywhere still removes the fixture tree; the group-B assertions
themselves run at the tail of a completed run.

Group B also proves the repo tree is untouched: `sys.dont_write_bytecode` is set
BEFORE the first repo import, every child gets `-B` plus
`PYTHONDONTWRITEBYTECODE=1`, server sources are parsed and never imported, and a
before/after `__pycache__` snapshot must be identical (a stale .pyc this run did
not create is reported INFO -- it cannot be blamed on this run, but it must stay
visible).  New entries in the shared temp dirs -- BOTH `tempfile.gettempdir()`
(a private /var/folders/... path on macOS) and the literal `/tmp` every process
shares -- are reported INFO only: nothing there is attributable to one run.

Usage:
  python3 tests/test_name_existence.py
  python3 tests/test_name_existence.py --brief
  python3 tests/test_name_existence.py --keep
Exit code 0 iff no case fails.
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "name_existence"
SKIP = "SKIP"

SMOKE_PATH = H.repo_path("Scripts", "_mcp_smoke_test.py")
SCRIPTS_DIR = H.repo_path("Scripts")
CORPUS_ROOT = H.repo_path("ClaudeCode")
CLAUDE_JSON = os.path.expanduser("~/.claude.json")

# ALL scratch lives under .claude/tmp/ (gitignored, per-project) -- never the
# shared system temp dir.  Each run gets its OWN mkdtemp subdirectory inside
# that base: two instances (a standalone run and a tests/run.py run) may overlap,
# and a fixed path meant one instance's teardown deleted the other's fixtures
# out from under it mid-probe.
FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_name_existence")
FIXTURE_ROOT = FIXTURE_BASE          # replaced per run by run(); see _sandbox()

PROBE_FUNCTION = "__ph_name_existence_probe__"
PROBE_TIMEOUT = 20.0

GA = "A. inventory"
GB = "B. scan + repo hygiene"
GC = "C. D1 prompt corpus: function names"
GD = "D. D1 prompt corpus: servers + dispatchers"
GE = "E. D1 server text, REGISTERED server (live instruction -> FAIL)"
GF = "F. D1 server text, unregistered server (inert -> INFO)"
GG = "G. D2 unmentioned functions"
GH = "H. negative control"
GI = "I. D3 agent grants vs agent prescriptions"

# Prescription origins.  The class decides the group and the severity ceiling.
O_CORPUS = "corpus"            # ClaudeCode/**            -> FAIL
O_LIVE = "live-server"         # registered Scripts/mcp-* -> FAIL
O_DEAD = "dead-server"         # unregistered server text -> INFO (never shown)

ORIGIN_GROUP = {O_CORPUS: None, O_LIVE: GE, O_DEAD: GF}

MAX_EVIDENCE = 6

# Concurrent edits by sibling agents: a finding whose subject matches is
# reported as INFO with the note, never as a failure.  `mcp-compile` is NOT
# listed: that deletion has LANDED (Scripts/mcp-compile.py is gone, the launch
# table is down to 15), so the references it left behind in other servers are
# residue to report, not churn to excuse.
INFLIGHT = [
    (re.compile(r"^(find_type_definition|(?:clangd|cuda|luals)_find_type_definition_at)$"),
     "in-flight: a sibling agent is adding typeDefinition handlers + aliases "
     "to Scripts/mcp-purity.py"),
]


def inflight_note(subject):
    for rx, note in INFLIGHT:
        if rx.match(subject):
            return note
    return None


# ---------------------------------------------------------------------------
# sandbox discipline: ALL scratch under .claude/tmp/, never the shared /tmp
# ---------------------------------------------------------------------------

# Every path this suite writes, and every child command line it launches, is
# recorded here so group B can ASSERT the sandbox rule instead of asserting it
# in a comment.  Any new write MUST go through write_text() or the assertion
# fails -- which is the point: the rule is enforced structurally, not by memory.
WRITES = []
CHILD_ARGV = []


def write_text(path, body):
    """The ONLY write path in this suite.  Creates parent dirs; records the path."""
    WRITES.append(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def inside(path, root):
    """True iff `path` is `root` or lives under it (no symlink games)."""
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def system_temp_dirs():
    """Shared temp dirs to watch.  `tempfile.gettempdir()` is NOT enough: on
    macOS it is a private /var/folders/... path, while the forbidden dir is the
    literal /tmp every process on the host shares."""
    dirs = []
    for candidate in (tempfile.gettempdir(), "/tmp"):
        real = os.path.realpath(candidate)
        if os.path.isdir(real) and real not in dirs:
            dirs.append(real)
    return dirs


def system_temp_snapshot():
    """dir -> its entry names, for every shared temp dir."""
    snap = {}
    for tmp_dir in system_temp_dirs():
        try:
            snap[tmp_dir] = sorted(os.listdir(tmp_dir))
        except OSError:
            snap[tmp_dir] = []
    return snap


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

class ServerInventory:
    """Enumerated surface of one MCP server, or the reason it stayed unknown."""

    def __init__(self, file, tool):
        self.file = file
        self.tool = tool
        self.functions = set()
        self.source = ""
        self.error = ""

    @property
    def ok(self):
        return bool(self.functions)


def _parse_available(text):
    """Pull the name list out of `... Available: a, b, c` (any wrapper)."""
    match = re.search(r"Available:\s*(.*)", text, re.S)
    if not match:
        return set()
    tail = match.group(1)
    for stop in ('"', "}", "\n"):
        cut = tail.find(stop)
        if cut >= 0:
            tail = tail[:cut]
    names = set()
    for chunk in tail.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        token = chunk.split()[0].strip(".;`'\"")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", token):
            names.add(token)
    return names


def _parse_list_rows(text):
    """Parse `--list` output: two-space-indented `<name>  <description>` rows."""
    names = set()
    for line in text.splitlines():
        match = re.match(r"^ {2,}([A-Za-z_][A-Za-z0-9_-]*)(?:\s|$)", line)
        if match:
            names.add(match.group(1))
    return names


def _rpc_probe(argv, tool):
    """Ask a dispatcher for a bogus function; return its reply text."""
    client = None
    try:
        client = H.JsonRpcClient(argv, tool=tool, timeout=PROBE_TIMEOUT)
        _iserr, text = client.call_tool(PROBE_FUNCTION)
        return text, ""
    except Exception as exc:                                  # noqa: BLE001
        return "", "%s: %s" % (type(exc).__name__, exc)
    finally:
        if client is not None:
            client.close()


def probe_argv(path, args, sandbox):
    """Child command line for one server, recorded and sandboxed.

    Two invariants, both asserted later by the group-B sandbox cases:
      * `-B` on top of H.child_env()'s PYTHONDONTWRITEBYTECODE=1, so a probed
        server cannot drop a .pyc into the repo tree.
      * every `--project-root` the imported launch table supplies (it uses
        `/tmp`) is REWRITTEN to the fixture sandbox, so a probed child cannot
        touch the shared system temp dir either.  The sandbox holds a minimal
        `project-forge.yaml`, which is also what makes mcp-forge enumerable.
    """
    argv = [sys.executable, "-B", path] + list(args)
    for idx, token in enumerate(argv[:-1]):
        if token == "--project-root":
            argv[idx + 1] = sandbox
    CHILD_ARGV.append(list(argv))
    return argv


def probe_server(cfg, sandbox):
    """Enumerate one server: bogus-function probe, then `--list`."""
    inv = ServerInventory(cfg["file"], cfg["tool"])
    path = os.path.join(SCRIPTS_DIR, cfg["file"])
    if not os.path.isfile(path):
        inv.error = "server file absent: Scripts/%s" % cfg["file"]
        return inv

    os.makedirs(sandbox, exist_ok=True)     # also the children's cwd
    argv = probe_argv(path, cfg["args"], sandbox)
    text, err = _rpc_probe(argv, cfg["tool"])
    names = _parse_available(text)
    if names:
        inv.functions, inv.source = names, "rpc bogus-function probe"
        return inv

    rc, out, _serr = H.run_process(argv + ["--list"], timeout=30,
                                   cwd=sandbox)
    names = _parse_available(out) or _parse_list_rows(out)
    if names:
        inv.functions, inv.source = names, "--list (rc=%d)" % rc
        return inv

    inv.error = ("no enumeration: bogus-function probe gave no 'Available:' "
                 "list and --list produced no rows"
                 + (" (rpc error: %s)" % err if err else ""))
    return inv


def build_inventory():
    """Probe every server in the imported smoke launch table."""
    smoke = H.load_module_from_path("ph_smoke_table", SMOKE_PATH)
    sandbox = os.path.join(FIXTURE_ROOT, "server-root")
    write_text(os.path.join(sandbox, "project-forge.yaml"), "version: 1\n")
    return [probe_server(cfg, sandbox) for cfg in smoke.SERVERS]


def load_registration():
    """name -> launch script basename, for user-scope ~/.claude.json servers.

    Parsed by script: only the `mcpServers` names and their argv are touched,
    never the ~100 KB of unrelated state.
    """
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:                                  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None, "no top-level 'mcpServers' mapping in %s" % CLAUDE_JSON
    out = {}
    for name, cfg in servers.items():
        argv = []
        if isinstance(cfg, dict):
            if isinstance(cfg.get("command"), str):
                argv.append(cfg["command"])
            argv += [a for a in (cfg.get("args") or []) if isinstance(a, str)]
        scripts = [os.path.basename(a) for a in argv if a.endswith(".py")]
        out[name] = scripts[0] if scripts else None
    return out, ""


# ---------------------------------------------------------------------------
# corpus scan
# ---------------------------------------------------------------------------

class Prescription:
    """One place where model-facing text tells the model to call something."""

    def __init__(self, kind, name, path, lineno, snippet, server=None,
                 frontmatter=False, origin=O_CORPUS):
        self.kind = kind
        self.name = name
        self.server = server          # server hint: registered name or tool
        self.path = path
        self.lineno = lineno
        self.snippet = snippet
        self.frontmatter = frontmatter
        self.origin = origin          # O_CORPUS / O_LIVE / O_DEAD

    @property
    def where(self):
        return "%s:%d" % (self.path, self.lineno)


RX_MCP_TOKEN = re.compile(r"mcp__(?P<server>[A-Za-z0-9][A-Za-z0-9.+-]*)__"
                          r"(?P<tool>[A-Za-z_][A-Za-z0-9_]*)")
RX_CALL_PAREN = re.compile(r"\b(?P<disp>[a-z][a-z0-9]*_call)\((?P<args>[^)\n]*)")
RX_FUNCTION_IN_ARGS = re.compile(r"function\s*[:=]\s*[\"']?([A-Za-z0-9_-]+)")
RX_FUNCTION_KEY = re.compile(r"\bfunction\s*[:=]")
RX_LEADING_IDENT = re.compile(r"^\s*([a-z_][a-z0-9_]*(?:\s*/\s*[a-z_][a-z0-9_]*)*)"
                              r"(?![A-Za-z0-9_-])")
# Dispatcher meta-keys, never function names.
CALL_META_KEYS = {"function", "params", "f", "p"}
RX_FUNCTION_KV = re.compile(r"function\s*[:=]\s*[\"'](?P<name>[A-Za-z0-9_-]+)[\"']")
RX_DISPATCHER = re.compile(r"\b([a-z][a-z0-9]*_call)\b")
RX_BACKTICK = re.compile(r"`([^`\n]{1,60})`")
RX_BARE_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
RX_OPTIONAL_TAIL = re.compile(r"^([a-z][a-z0-9_]*)\[(_[a-z0-9_]+)\]$")
# R8: a bare `mcp-<name>` routing reference.  A leading `:` is excluded so the
# `p:mcp-luals` SKILL namespace cannot be mistaken for a server, and a trailing
# `.py` is excluded so `Scripts/mcp-purity.py` stays a filename.
RX_SERVER_REF = re.compile(r"(?<![A-Za-z0-9_:.-])mcp-([a-z0-9][a-z0-9-]*)"
                           r"(?![A-Za-z0-9_-])(?!\.py)")

CORPUS_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".cache"}
CORPUS_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico",
                   ".pyc", ".drawio"}


def corpus_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in CORPUS_SKIP_DIRS)
        for filename in sorted(filenames):
            if os.path.splitext(filename)[1].lower() in CORPUS_SKIP_EXT:
                continue
            out.append(os.path.join(dirpath, filename))
    return out


def _frontmatter_end(lines):
    """Index (0-based, exclusive) of the YAML frontmatter block, 0 if none."""
    if not lines or lines[0].rstrip() != "---":
        return 0
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            return idx + 1
    return 0


def _scan_line(out, rel, lineno, line, origin, in_fm=False, mcpservers=False,
               server_refs=False):
    """Apply every extraction rule to ONE line.  Returns the new mcpservers
    block state (R6 is the only stateful rule)."""
    snippet = line.strip()[:160]

    # -- R6: frontmatter `mcpServers:` list entries ---------------------
    if in_fm:
        if re.match(r"^mcpServers\s*:", line):
            inline = line.split(":", 1)[1].strip()
            for entry in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*",
                                    inline.strip("[]")):
                out.append(Prescription("mcp_server_entry", entry, rel, lineno,
                                        snippet, frontmatter=True,
                                        origin=origin))
            return True
        if mcpservers:
            entry = re.match(r"^\s*-\s*([A-Za-z0-9][A-Za-z0-9.+-]*)\s*$", line)
            if entry:
                out.append(Prescription("mcp_server_entry", entry.group(1), rel,
                                        lineno, snippet, frontmatter=True,
                                        origin=origin))
                return True
            if line.strip() and not line.startswith((" ", "\t")):
                mcpservers = False

    # -- R1: mcp__<server>__<tool> -------------------------------------
    for match in RX_MCP_TOKEN.finditer(line):
        out.append(Prescription("mcp_token", match.group("tool"), rel, lineno,
                                snippet, server=match.group("server"),
                                frontmatter=in_fm, origin=origin))

    # -- R8: bare `mcp-<name>` routing references (server text only) ----
    if server_refs:
        own = os.path.basename(rel)
        for match in RX_SERVER_REF.finditer(line):
            token = match.group(0)
            # A file naming ITSELF (docstring title line) is an identity
            # statement, not a routing instruction: never a failure.
            kind = ("server_self_ref" if token + ".py" == own else "server_ref")
            out.append(Prescription(kind, token, rel, lineno, snippet,
                                    origin=origin))

    # -- R5: dispatcher tool mentions ----------------------------------
    for match in RX_DISPATCHER.finditer(line):
        out.append(Prescription("dispatcher", match.group(1), rel, lineno,
                                snippet, frontmatter=in_fm, origin=origin))

    # -- R2: <x>_call( ... ) -------------------------------------------
    for match in RX_CALL_PAREN.finditer(line):
        disp, args = match.group("disp"), match.group("args")
        named = RX_FUNCTION_IN_ARGS.search(args)
        if named:
            out.append(Prescription("dispatch_call", named.group(1), rel,
                                    lineno, snippet, server=disp,
                                    origin=origin))
            continue
        if RX_FUNCTION_KEY.search(args):
            # `function=<name>` / `function=""` -- a placeholder or an empty
            # value, not a prescribed name.  Nothing to check.
            continue
        lead = RX_LEADING_IDENT.match(args)
        if lead and not args[lead.end():].lstrip().startswith("="):
            # A trailing `=` means a keyword argument, e.g. the
            # deliberately-WRONG `forge_call(targets=["app"])` example.
            for part in lead.group(1).split("/"):
                part = part.strip()
                if part in CALL_META_KEYS:
                    continue
                out.append(Prescription("dispatch_call", part, rel, lineno,
                                        snippet, server=disp, origin=origin))

    # -- R3: quoted function="..." -------------------------------------
    hint = None
    dispatchers_on_line = RX_DISPATCHER.findall(line)
    if len(set(dispatchers_on_line)) == 1:
        hint = dispatchers_on_line[0]
    for match in RX_FUNCTION_KV.finditer(line):
        out.append(Prescription("function_kv", match.group("name"), rel, lineno,
                                snippet, server=hint, origin=origin))

    # -- R4/R7 candidates: backticked identifiers ----------------------
    for match in RX_BACKTICK.finditer(line):
        token = match.group(1).strip()
        for candidate in _expand_backtick(token):
            out.append(Prescription("backtick", candidate, rel, lineno, snippet,
                                    origin=origin))
    return mcpservers


def scan_corpus(root, repo_root=None):
    """Extract every prescription from every text file under `root`.

    `root` is a parameter so the negative control can point the SAME extractor
    at a synthetic fixture tree.
    """
    repo_root = repo_root or H.REPO_ROOT
    prescriptions = []
    scanned, unreadable, texts = [], [], []

    for path in corpus_files(root):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (UnicodeDecodeError, OSError) as exc:
            unreadable.append((path, "%s: %s" % (type(exc).__name__, exc)))
            continue
        rel = os.path.relpath(path, repo_root)
        scanned.append(rel)
        texts.append(text)
        lines = text.splitlines()
        fm_end = _frontmatter_end(lines)
        in_mcpservers = False
        for idx, line in enumerate(lines):
            in_mcpservers = _scan_line(prescriptions, rel, idx + 1, line,
                                       O_CORPUS, in_fm=idx < fm_end,
                                       mcpservers=in_mcpservers)

    return {
        "prescriptions": prescriptions,
        "files": scanned,
        "unreadable": unreadable,
        "text": "\n".join(texts),
    }


def server_text_lines(path):
    """(lineno, text) for every MODEL-FACING line in an MCP server source.

    Model-facing == the module docstring plus every dict value under a
    `"description"` key -- i.e. the MCP tool descriptions and their nested
    inputSchema property descriptions, which Claude Code injects into the
    system prompt on every request.  Extracted with `ast`, so ordinary code,
    comments, log messages and handler tables can never leak in, and no server
    module is ever IMPORTED (mcp-webfetch cannot even be imported here: no bs4).

    A Constant's `lineno` is the line its literal opens on, and content index 0
    starts on that same physical line, so `lineno + i` is the true source line
    of content line i -- exact `file:line` evidence.
    """
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)

    nodes = []
    first = tree.body[0] if tree.body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        nodes.append(first.value)                       # module docstring
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "description"):
                continue
            for sub in ast.walk(value):                 # concats / f-strings
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    nodes.append(sub)

    out = set()
    for node in nodes:
        for offset, line in enumerate(node.value.split("\n")):
            if line.strip():
                out.add((node.lineno + offset, line))
    return sorted(out)


def server_source_files(inventories):
    """Every `Scripts/mcp-*.py`: the imported launch table, plus any server on
    disk the table has not caught up with yet."""
    files = [inv.file for inv in inventories]
    try:
        extra = sorted(n for n in os.listdir(SCRIPTS_DIR)
                       if n.startswith("mcp-") and n.endswith(".py")
                       and n not in files)
    except OSError:
        extra = []
    return [f for f in files + extra
            if os.path.isfile(os.path.join(SCRIPTS_DIR, f))]


def scan_server_text(files, live_files, repo_root=None, scripts_dir=None):
    """Extract prescriptions from the model-facing text of MCP servers.

    `live_files` is the set of scripts a REGISTERED server actually launches;
    text inside anything else is never rendered to a model, so it is inert and
    its findings are downgraded (see ORIGIN_GROUP).  `scripts_dir` is a
    parameter so the negative control can point the SAME scanner at a synthetic
    server.
    """
    repo_root = repo_root or H.REPO_ROOT
    scripts_dir = scripts_dir or SCRIPTS_DIR
    prescriptions = []
    scanned, unreadable, texts = [], [], []
    for name in files:
        path = os.path.join(scripts_dir, name)
        try:
            numbered = server_text_lines(path)
        except (SyntaxError, OSError, UnicodeDecodeError) as exc:
            unreadable.append((name, "%s: %s" % (type(exc).__name__, exc)))
            continue
        rel = os.path.relpath(path, repo_root)
        origin = O_LIVE if name in live_files else O_DEAD
        scanned.append((rel, origin, len(numbered)))
        texts.append("\n".join(line for _lineno, line in numbered))
        for lineno, line in numbered:
            _scan_line(prescriptions, rel, lineno, line, origin,
                       server_refs=True)
    return {
        "prescriptions": prescriptions,
        "files": scanned,
        "unreadable": unreadable,
        "text": "\n".join(texts),
    }


def _expand_backtick(token):
    """Backtick body -> candidate identifiers (`a[_b]` yields `a` and `a_b`)."""
    if RX_BARE_IDENT.match(token):
        return [token]
    optional = RX_OPTIONAL_TAIL.match(token)
    if optional:
        base = optional.group(1)
        return [base, base + optional.group(2)]
    return []


# ---------------------------------------------------------------------------
# Direction 3: the agent GRANT surface
# ---------------------------------------------------------------------------

# An agent definition is `<corpus>/agents/<name>.md`.  Scoped to that directory
# on purpose -- see the D3 SCOPE paragraph in the module docstring: `tools:`
# appears in fenced SKILL.md examples, in a template and in prose, and none of
# those is a grant.  A parameter-free constant keeps the negative control able to
# point the same collector at a synthetic fixture tree.
AGENT_SUBDIR = "agents"

# The only three MCP-name SHAPES that can appear in a `tools:` list.  Anything
# else (Read, Write, Bash, Glob, TodoWrite, ...) is a built-in and out of scope.
RX_GRANT_MCP_TOOL = re.compile(r"^mcp__(?P<server>[A-Za-z0-9][A-Za-z0-9.+-]*)__"
                               r"(?P<tool>[A-Za-z_][A-Za-z0-9_]*)$")
RX_GRANT_MCP_SERVER = re.compile(r"^mcp__(?P<server>[A-Za-z0-9][A-Za-z0-9.+-]*)$")
RX_GRANT_DISPATCHER = re.compile(r"^[a-z][a-z0-9]*_call$")

# A grant that covers everything: 3b can never fire against it.
WILDCARD_GRANTS = {"*", "all", "any"}

# 3d's ONLY input, and the ONLY thing that decides its severity.
#
# The official Claude Code sub-agent documentation states that agents shipped
# inside a PLUGIN do not support `hooks:`, `mcpServers:` or `permissionMode:` --
# those keys are ignored at load time, for security reasons.  `ClaudeCode/` is a
# plugin (`.claude-plugin/plugin.json`, name `p`, installed via a skills-dir
# symlink), so an `mcpServers:` key here is inert.
#
# A named constant rather than an inline literal because this encodes an INSTALL
# SHAPE, not a truth about YAML: copy one of these files outside a plugin, into
# ~/.claude/agents/, and the key becomes live again.  That is the one scenario in
# which 3d is the wrong rule, and flipping this to False is then a one-line,
# reviewable decision instead of a silent drift.  R6 (group D) keeps judging the
# key's VALUES either way.
CORPUS_SHIPS_AS_PLUGIN = True

# 3b severity suppressors.  Each turns a missing-grant candidate into an
# INFO-with-evidence row instead of a build break; every one of them exists
# because the corpus already contains the shape it describes.
RX_ILLUSTRATIVE = re.compile(r"\be\.?g\.|\bfor example\b|\bfor instance\b"
                             r"|\bsuch as\b|\betc\b|\bexamples?:", re.I)
RX_NEGATIVE = re.compile(r"\bretir\w*|\bunregistered\b|\bdoes ?n[o']?t exist\b"
                         r"|\bnever exist\w*|\bno longer\b|\bremoved\b"
                         r"|\bdeleted\b|\bdead\b|\bgone\b|\bdeprecat\w*"
                         r"|\bobsolete\b|\babsorbed\b|\breplaced by\b"
                         r"|\brenamed\b", re.I)
RX_OTHER_AGENT = re.compile(r"\b(?:p:)?minion-[a-z0-9-]+|\bsubagent\b"
                            r"|\bdelegat\w*|\bTask tool\b|\bgeneral-purpose\b",
                            re.I)


def _frontmatter_values(lines, key, fm_end):
    """(key_present, [values]) for one frontmatter key.

    Handles all three spellings this corpus uses:
      `tools: A, B, C`          inline comma list
      `mcpServers: [A, B]`      inline bracketed list
      `mcpServers:` + `  - A`   block list
    """
    present, values = False, []
    idx = 0
    while idx < fm_end:
        match = re.match(r"^%s\s*:(.*)$" % re.escape(key), lines[idx])
        if not match:
            idx += 1
            continue
        present = True
        inline = match.group(1).strip().strip("[]").strip()
        idx += 1
        if inline:
            values += [tok for tok in re.split(r"[,\s]+", inline) if tok]
            continue
        while idx < fm_end:
            item = re.match(r"^\s*-\s*(.+?)\s*$", lines[idx])
            if not item:
                break
            values.append(item.group(1))
            idx += 1
    return present, values


class Agent:
    """One agent definition: its GRANTS (frontmatter) and its BODY."""

    def __init__(self, rel, name, lines, fm_end):
        self.rel = rel
        self.name = name
        self.lines = lines
        self.fm_end = fm_end          # 1-based line numbers <= fm_end are frontmatter
        self.tools_present, self.tool_entries = _frontmatter_values(
            lines, "tools", fm_end)
        self.servers_present, self.server_entries = _frontmatter_values(
            lines, "mcpServers", fm_end)
        self.wildcard = any(e in WILDCARD_GRANTS for e in self.tool_entries)

    @property
    def unrestricted(self):
        """No `tools:` key at all, or a wildcard -> every tool is available."""
        return (not self.tools_present) or self.wildcard

    def line(self, lineno):
        """1-based source line, or "" when out of range."""
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1]
        return ""

    def key_line(self, key):
        """1-based line number of a frontmatter key (for file:line evidence)."""
        for idx in range(self.fm_end):
            if re.match(r"^%s\s*:" % re.escape(key), self.lines[idx]):
                return idx + 1
        return 1


def collect_agents(root, repo_root=None):
    """Every agent definition under `<root>/agents/`.

    `root` is a parameter for the same reason `scan_corpus`'s is: the negative
    control points this collector at a synthetic fixture tree.
    """
    repo_root = repo_root or H.REPO_ROOT
    out = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root,
                                                             AGENT_SUBDIR)):
        dirnames[:] = sorted(d for d in dirnames if d not in CORPUS_SKIP_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            fm_end = _frontmatter_end(lines)
            if not fm_end:
                continue          # no frontmatter -> not an agent definition
            _present, names = _frontmatter_values(lines, "name", fm_end)
            name = names[0] if names else os.path.splitext(filename)[0]
            out.append(Agent(os.path.relpath(path, repo_root), name, lines,
                             fm_end))
    return out


def prescribed_tool(pre):
    """The DISPATCHER TOOL a body prescription requires, or None.

    Deliberately dispatcher-level only, and deliberately built on the SAME
    `Prescription` objects Direction 1 already extracted:
      R5 `dispatcher`   -- the name IS the tool (`git_call`)
      R1 `mcp_token`    -- the tool half of `mcp__<server>__<tool>`
      R2/R3             -- the dispatcher a call was made against
    A backticked FUNCTION name alone (R4) implies a dispatcher only by ownership
    inference, which is too weak to gate a build on, so it is skipped.
    """
    if pre.kind in ("dispatcher", "mcp_token"):
        return pre.name
    if pre.kind in ("dispatch_call", "function_kv"):
        if pre.server and RX_GRANT_DISPATCHER.match(pre.server):
            return pre.server
    return None


class Grants:
    """What one agent's frontmatter `tools:` list actually grants."""

    def __init__(self, agent):
        self.agent = agent
        self.tools = set()        # dispatcher tool names the agent may call
        # NO `mcpServers:` mirror here on purpose: that key is ignored at load
        # time (see CORPUS_SHIPS_AS_PLUGIN), so it grants nothing and must not
        # feed a severity decision.  3d reports the key itself; R6 its values.
        self.rows = []            # one rendered verdict per `tools:` entry
        self.dead = []            # (entry, why) -- 3a failures
        self.unverified = []      # (entry, why) -- 3a INFO


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

class Finding:
    """One verdict about one distinct prescribed subject."""

    def __init__(self, group, kind, subject, severity, problem="", note="",
                 evidence=(), detail=()):
        self.group = group
        self.kind = kind
        self.subject = subject
        self.severity = severity     # H.FAIL / H.INFO / H.PASS
        self.problem = problem
        self.note = note
        self.evidence = list(evidence)
        # Extra rendered lines between the note and the evidence.  Direction 3
        # needs them: a grant verdict is a TABLE (one row per `tools:` entry),
        # not a sentence.
        self.detail = list(detail)


def _levenshtein(a, b, cap=3):
    if abs(len(a) - len(b)) >= cap:
        return cap
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
        if min(previous) >= cap:
            return cap
    return previous[-1]


class Checker:
    """Holds the live inventory + registration and judges prescriptions."""

    def __init__(self, inventories, registration, reg_error=""):
        self.inventories = [i for i in inventories if i.ok]
        self.all_inventories = list(inventories)
        self.registration = registration
        self.reg_error = reg_error
        self.functions = set()
        self.owners = {}
        for inv in self.inventories:
            for fn in inv.functions:
                self.functions.add(fn)
                self.owners.setdefault(fn, set()).add(inv.file)
        self.tools = {inv.tool: inv.file for inv in self.all_inventories}
        self.by_file = {inv.file: inv for inv in self.all_inventories}
        # R4 namespaces: only prefixes the LIVE inventory actually uses.
        self.namespaces = sorted({
            tool[:-len("_call")] for tool in self.tools
            if tool.endswith("_call")
            and any(fn.startswith(tool[:-len("_call")] + "_")
                    for fn in self.functions)
        })
        self.unknown_servers = {inv.file for inv in self.all_inventories
                                if not inv.ok}

    # -- helpers ---------------------------------------------------------

    def server_file_for_hint(self, hint):
        """Map a `<x>_call` dispatcher or a registered server name to a file."""
        if not hint:
            return None
        if hint in self.tools:
            return self.tools[hint]
        if self.registration and hint in self.registration:
            return self.registration[hint]
        return None

    def is_namespaced(self, name):
        return any(name.startswith(ns + "_") for ns in self.namespaces)

    def near_misses(self, name, limit=3):
        head = name[:4]
        out = [(fn, _levenshtein(name, fn)) for fn in self.functions
               if fn[:4] == head and fn != name]
        out = sorted((d, fn) for fn, d in out if d <= 2)
        return [fn for _d, fn in out[:limit]]

    # -- the judgement ---------------------------------------------------

    def evaluate(self, prescriptions):
        """Distinct prescribed subjects -> Findings (Direction 1).

        The origin class is part of the bucket key, so the SAME dead name gets
        one finding per origin: a live instruction and inert text in a retired
        server are different defects and must not be merged.
        """
        buckets = {}
        for pre in prescriptions:
            key = (pre.kind, pre.server, pre.name, pre.origin)
            buckets.setdefault(key, []).append(pre)

        findings = []
        for (kind, server, name, origin), group in sorted(
                buckets.items(), key=lambda kv: (kv[0][3], kv[0][0], kv[0][2],
                                                 kv[0][1] or "")):
            handler = getattr(self, "_check_" + kind, None)
            if handler is None:
                continue
            finding = handler(name, server, group)
            if finding is None:
                continue
            self._apply_origin(finding, origin)
            findings.append(finding)
        return self._apply_inflight(findings)

    def _apply_origin(self, finding, origin):
        """Route + cap a finding by where its text lives."""
        target = ORIGIN_GROUP.get(origin)
        if target is None:
            return
        finding.group = target
        if origin == O_DEAD and finding.severity == H.FAIL:
            finding.severity = H.INFO
            reason = ("its server is NOT registered in ~/.claude.json"
                      if self.registration is not None
                      else "the registration state is unknown (%s)"
                      % self.reg_error)
            finding.note = ("INERT -- %s, so this text is never rendered to a "
                            "model. Original problem: %s"
                            % (reason, finding.problem))
            finding.problem = ""

    def _apply_inflight(self, findings):
        for finding in findings:
            if finding.severity != H.FAIL:
                continue
            note = inflight_note(finding.subject)
            if note:
                finding.severity = H.INFO
                finding.note = note
        return findings

    def _evidence(self, group):
        return [(p.where, p.snippet) for p in group]

    # -- R1 ---------------------------------------------------------------

    def _check_mcp_token(self, tool, server, group):
        subject = "mcp__%s__%s" % (server, tool)
        evidence = self._evidence(group)
        if self.registration is None:
            return Finding(GD, "mcp_token", subject, SKIP,
                           note="registration unknown: %s" % self.reg_error,
                           evidence=evidence)
        if server not in self.registration:
            return Finding(
                GD, "mcp_token", subject, H.FAIL,
                problem="server %r is NOT registered in ~/.claude.json "
                        "(registered: %s)" % (server,
                                              ", ".join(sorted(self.registration))),
                evidence=evidence)
        script = self.registration[server]
        if script is None or script not in self.by_file:
            return Finding(
                GD, "mcp_token", subject, H.INFO,
                note="server %r is registered but its launch script (%s) is "
                     "not in the smoke launch table, so its tool list cannot "
                     "be verified" % (server, script),
                evidence=evidence)
        inv = self.by_file[script]
        if inv.tool != tool:
            return Finding(
                GD, "mcp_token", subject, H.FAIL,
                problem="server %r (%s) exposes tool %r, not %r"
                        % (server, script, inv.tool, tool),
                evidence=evidence)
        return Finding(GD, "mcp_token", subject, H.PASS,
                       note="%s -> %s exposes %s" % (server, script, tool),
                       evidence=evidence)

    # -- R6 ---------------------------------------------------------------

    def _check_mcp_server_entry(self, server, _hint, group):
        subject = "mcpServers: %s" % server
        evidence = self._evidence(group)
        if self.registration is None:
            return Finding(GD, "mcp_server_entry", subject, SKIP,
                           note="registration unknown: %s" % self.reg_error,
                           evidence=evidence)
        if server not in self.registration:
            return Finding(
                GD, "mcp_server_entry", subject, H.FAIL,
                problem="frontmatter grants server %r which is NOT registered "
                        "in ~/.claude.json" % server, evidence=evidence)
        return Finding(GD, "mcp_server_entry", subject, H.PASS,
                       note="registered -> %s" % self.registration[server],
                       evidence=evidence)

    # -- R8 ---------------------------------------------------------------

    def _check_server_ref(self, server, _hint, group):
        """A bare `mcp-<name>` routing reference inside model-facing text."""
        evidence = self._evidence(group)
        if self.registration is None:
            return Finding(GD, "server_ref", server, SKIP,
                           note="registration unknown: %s" % self.reg_error,
                           evidence=evidence)
        if server in self.registration:
            return Finding(GD, "server_ref", server, H.PASS,
                           note="registered -> %s"
                                % (self.registration[server] or "(no script)"),
                           evidence=evidence)
        on_disk = os.path.isfile(os.path.join(SCRIPTS_DIR, server + ".py"))
        return Finding(
            GD, "server_ref", server, H.FAIL,
            problem="routing text points at server %r, which is NOT registered "
                    "in ~/.claude.json (%s) -- the instruction cannot be "
                    "followed" % (server,
                                  "Scripts/%s.py exists but is unregistered"
                                  % server if on_disk
                                  else "and no Scripts/%s.py exists" % server),
            evidence=evidence)

    def _check_server_self_ref(self, server, _hint, group):
        """`mcp-x.py`'s own text naming `mcp-x`: identity, never routing."""
        evidence = self._evidence(group)
        if self.registration is None or server in self.registration:
            return None
        script = server + ".py"
        aliases = sorted(n for n, s in (self.registration or {}).items()
                         if s == script)
        if aliases:
            return Finding(
                GD, "server_self_ref", server, H.INFO,
                note="the server documents itself as %r but is REGISTERED as "
                     "%s -- prose that copies the self-name would be a dead "
                     "routing instruction" % (server, ", ".join(aliases)),
                evidence=evidence)
        return Finding(GD, "server_self_ref", server, H.INFO,
                       note="self-reference inside a server that is not "
                            "registered at all", evidence=evidence)

    # -- R5 ---------------------------------------------------------------

    def _check_dispatcher(self, tool, _hint, group):
        evidence = self._evidence(group)
        if tool not in self.tools:
            return Finding(
                GD, "dispatcher", tool, H.FAIL,
                problem="no server in the launch table exposes a %r tool "
                        "(known: %s)" % (tool, ", ".join(sorted(self.tools))),
                evidence=evidence)
        script = self.tools[tool]
        if self.registration is not None:
            registered = [n for n, s in self.registration.items() if s == script]
            if not registered:
                return Finding(
                    GD, "dispatcher", tool, H.INFO,
                    note="tool exists (Scripts/%s) but that server is NOT "
                         "registered in ~/.claude.json, so the dispatcher is "
                         "unreachable from a live session" % script,
                    evidence=evidence)
            return Finding(GD, "dispatcher", tool, H.PASS,
                           note="Scripts/%s, registered as %s"
                                % (script, ", ".join(sorted(registered))),
                           evidence=evidence)
        return Finding(GD, "dispatcher", tool, SKIP,
                       note="registration unknown: %s" % self.reg_error,
                       evidence=evidence)

    # -- R2 / R3 ----------------------------------------------------------

    def _check_function(self, kind, name, hint, group):
        evidence = self._evidence(group)
        subject = "%s%s" % (name, "" if not hint else " (via %s)" % hint)
        script = self.server_file_for_hint(hint)

        if script and script in self.unknown_servers:
            return Finding(
                GC, kind, name, SKIP,
                note="%s could not be enumerated (%s); name not verified"
                     % (script, self.by_file[script].error), evidence=evidence)

        if script and script in self.by_file:
            inv = self.by_file[script]
            if name in inv.functions:
                return Finding(GC, kind, subject, H.PASS,
                               note="Scripts/%s implements it" % script,
                               evidence=evidence)
            elsewhere = sorted(self.owners.get(name, ()))
            if elsewhere:
                return Finding(
                    GC, kind, name, H.FAIL,
                    problem="%s does NOT implement %r (implemented by: %s) -- "
                            "prescribed against the wrong dispatcher"
                            % (script, name, ", ".join(elsewhere)),
                    evidence=evidence)
            return Finding(
                GC, kind, name, H.FAIL,
                problem="%s does NOT implement %r, and no other server does "
                        "either%s" % (script, name, self._hint_suffix(name)),
                evidence=evidence)

        if name in self.functions:
            return Finding(GC, kind, subject, H.PASS,
                           note="implemented by: %s"
                                % ", ".join(sorted(self.owners[name])),
                           evidence=evidence)
        return Finding(
            GC, kind, name, H.FAIL,
            problem="no server in the fleet implements %r%s"
                    % (name, self._hint_suffix(name)), evidence=evidence)

    def _hint_suffix(self, name):
        near = self.near_misses(name)
        return " (did you mean: %s?)" % ", ".join(near) if near else ""

    def _check_dispatch_call(self, name, hint, group):
        return self._check_function("dispatch_call", name, hint, group)

    def _check_function_kv(self, name, hint, group):
        return self._check_function("function_kv", name, hint, group)

    # -- R4 / R7 ----------------------------------------------------------

    def _check_backtick(self, name, _hint, group):
        evidence = self._evidence(group)
        if name in self.functions:
            return None                     # nothing to say about a real name
        if name in self.tools:
            return None                     # dispatcher: handled by R5
        if self.is_namespaced(name):
            return Finding(
                GC, "backtick_namespaced", name, H.FAIL,
                problem="backticked %r uses the live %s_ namespace but no "
                        "server implements it%s"
                        % (name, name.split("_", 1)[0], self._hint_suffix(name)),
                evidence=evidence)
        near = self.near_misses(name)
        if near and "_" in name:
            return Finding(
                GC, "backtick_near_miss", name, H.INFO,
                note="not an MCP function name; within edit distance 2 of %s "
                     "-- check whether this was meant as a call"
                     % ", ".join(near), evidence=evidence)
        return None

    # -- Direction 2 -------------------------------------------------------

    def unmentioned(self, corpus_text):
        """server file -> sorted function names no corpus file mentions."""
        out = {}
        for inv in self.inventories:
            missing = [fn for fn in sorted(inv.functions)
                       if not re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"
                                        % re.escape(fn), corpus_text)]
            out[inv.file] = missing
        return out

    # -- Direction 3: the agent grant surface ------------------------------

    def dispatcher_of_server(self, server):
        """Registered server name -> the dispatcher tool its script exposes."""
        script = (self.registration or {}).get(server)
        inv = self.by_file.get(script) if script else None
        return inv.tool if inv is not None and inv.ok else None

    def grantable(self):
        """tool -> [registered server name] for every GRANTABLE dispatcher.

        Registration is what makes a tool grantable, and that single fact is the
        whole retirement filter: `clangd_call` / `cuda_call` / `luals_call` are
        in the launch table but no ~/.claude.json entry launches them, so an
        agent body that names one cannot be a MISSING GRANT -- there is nothing
        to grant.  Same reasoning R5 already applies to prose.
        """
        out = {}
        if self.registration is None:
            return out
        for tool, script in self.tools.items():
            inv = self.by_file.get(script)
            if inv is None or not inv.ok:
                continue
            names = sorted(n for n, s in self.registration.items() if s == script)
            if names:
                out[tool] = names
        return out

    def agent_grants(self, agent):
        """Classify every frontmatter `tools:` entry of one agent (3a input)."""
        grants = Grants(agent)
        for entry in agent.tool_entries:
            if entry in WILDCARD_GRANTS:
                grants.rows.append("grant       : %-42s WILDCARD -- grants "
                                   "every tool" % entry)
                continue

            match = RX_GRANT_MCP_TOOL.match(entry)
            if match:
                tool = match.group("tool")
                if tool in self.tools:
                    grants.tools.add(tool)
                    grants.rows.append("grant       : %-42s MCP tool, exposed "
                                       "by Scripts/%s" % (entry,
                                                          self.tools[tool]))
                else:
                    grants.dead.append(
                        (entry, "no server in the launch table exposes a %r "
                                "tool%s" % (tool, self._hint_suffix(tool))))
                continue

            match = RX_GRANT_MCP_SERVER.match(entry)
            if match:
                server = match.group("server")
                tool = self.dispatcher_of_server(server)
                if tool:
                    grants.tools.add(tool)
                    grants.rows.append("grant       : %-42s whole-server grant "
                                       "-> %s" % (entry, tool))
                elif self.registration is None:
                    grants.unverified.append(
                        (entry, "registration unknown: %s" % self.reg_error))
                elif server in self.registration:
                    grants.unverified.append(
                        (entry, "server %r is registered but its launch script "
                                "(%s) is not in the smoke launch table, so its "
                                "tool cannot be resolved"
                         % (server, self.registration[server])))
                else:
                    grants.dead.append(
                        (entry, "server %r is NOT registered in ~/.claude.json"
                         % server))
                continue

            if RX_GRANT_DISPATCHER.match(entry):
                if entry in self.tools:
                    grants.tools.add(entry)
                    grants.rows.append("grant       : %-42s bare dispatcher "
                                       "spelling -> Scripts/%s"
                                       % (entry, self.tools[entry]))
                else:
                    grants.dead.append(
                        (entry, "no server in the launch table exposes a %r "
                                "tool%s" % (entry, self._hint_suffix(entry))))
                continue

            grants.rows.append("grant       : %-42s not an MCP name shape "
                               "(built-in / unknown) -- out of scope" % entry)
        return grants

    def check_grants(self, agent, grants):
        """3a: an agent must not GRANT a tool the live inventory does not have."""
        subject = "tools: %s" % agent.name
        evidence = [("%s:%d" % (agent.rel, agent.key_line("tools")),
                     ("tools: " + ", ".join(agent.tool_entries))
                     if agent.tool_entries else "(no tools: key)")]

        if not agent.tools_present:
            return Finding(
                GI, "d3_grants", subject, H.INFO,
                note="no `tools:` key at all -- the agent inherits the full "
                     "toolbelt, so there is no grant to verify and 3b cannot "
                     "fire for it", evidence=evidence)
        if agent.wildcard:
            return Finding(
                GI, "d3_grants", subject, H.INFO,
                note="wildcard grant -- every tool is available, so there is "
                     "no dead grant to find and 3b cannot fire for it",
                detail=grants.rows, evidence=evidence)

        detail = list(grants.rows)
        detail += ["DEAD        : %-42s %s" % kv for kv in grants.dead]
        detail += ["unverified  : %-42s %s" % kv for kv in grants.unverified]
        if grants.dead:
            return Finding(
                GI, "d3_grants", subject, H.FAIL,
                problem="frontmatter grants %d MCP tool(s) that do NOT exist "
                        "in the live inventory: %s"
                        % (len(grants.dead),
                           "; ".join("%s -- %s" % kv for kv in grants.dead)),
                note="a nonexistent entry in `tools:` is inert at load time but "
                     "misleading: it reads as a granted capability that can "
                     "never be called",
                detail=detail, evidence=evidence)
        if grants.unverified:
            return Finding(
                GI, "d3_grants", subject, H.INFO,
                note="%d grant(s) could not be verified: %s"
                     % (len(grants.unverified),
                        "; ".join("%s -- %s" % kv for kv in grants.unverified)),
                detail=detail, evidence=evidence)
        return Finding(
            GI, "d3_grants", subject, H.PASS,
            note="%d entr%s, %d MCP tool(s), all present in the live inventory"
                 % (len(agent.tool_entries),
                    "y" if len(agent.tool_entries) == 1 else "ies",
                    len(grants.tools)),
            detail=detail, evidence=evidence)

    def check_mcpservers_key(self, agent):
        """3d: a plugin agent must not carry an `mcpServers:` key AT ALL.

        SEVERITY: FAIL.  The defence, because a future reader will want to argue
        with it right here:

        1. IT IS THE SAME DEFECT CLASS 3a ALREADY FAILS ON, one key over.  The
           key is not merely redundant -- it is IGNORED at load time (see
           CORPUS_SHIPS_AS_PLUGIN), so it reads as a conferred capability that
           can never be conferred.  3a fails a `tools:` entry naming a tool that
           does not exist for exactly that reason, and calls it "inert at load
           time but misleading".  A key the platform drops wholesale is more
           misleading, not less.

        2. IT HAS ALREADY HIDDEN A REAL DEFECT FROM THIS GATE.  While it existed,
           3b demoted p:minion-watson's genuine, live `context7_call` missing
           grant to INFO purely because `mcpServers:` "declared" the server, so
           watson was silently unable to do the documentation lookup its own
           routing table prescribes.  A frontmatter key that can suppress the
           gate built to catch it has earned a FAIL, not a note.

        3. FAIL IS THE ONLY SEVERITY THAT STOPS REINTRODUCTION, which is the
           whole point.  The stale documentation is real and in-tree:
           ClaudeCode/ARCHITECTURE.md's agent-frontmatter template still shows
           an `mcpServers:` line, so the next agent authored from it will carry
           the key.  Group I's INFO rows are by design a decisions-for-a-human
           list, and a green run invites skimming them -- INFO would let the key
           come straight back.

        4. FAIL IS SAFE HERE, WHICH IS EXACTLY WHAT DISQUALIFIES IT IN 3b.  3b
           needs suppressors because it fires on prose, and prose edits are this
           repo's main activity.  3d fires on a structural key, and the corpus
           now carries ZERO of them: the rule has no live subject, so it cannot
           flap on an ordinary prompt edit.  It can only fire on a REGRESSION --
           precisely the event worth breaking a build for.

        THE COUNTER-ARGUMENT, and why it does not win: a copy of these files
        installed OUTSIDE a plugin, into ~/.claude/agents/, WOULD have a live
        `mcpServers:` key, and a blanket FAIL would then be wrong.  That is not
        how this repo installs, and it is not silently assumed either: it is the
        single named constant CORPUS_SHIPS_AS_PLUGIN, which flips this row to
        INFO.  The FAIL is the alarm that forces that call to be made
        deliberately rather than by drift -- which is strictly better than an
        INFO that never forces it at all.

        A PASS row is emitted per clean agent on purpose: a rule that can only
        ever FAIL is as blind as one that can only ever PASS, and the PASS rows
        prove 3d actually ran across the whole corpus.
        """
        subject = "mcpServers: key in %s" % agent.name
        lineno = agent.key_line("mcpServers") if agent.servers_present else 1
        evidence = [("%s:%d" % (agent.rel, lineno),
                     agent.line(lineno).strip() if agent.servers_present
                     else "(no mcpServers: key -- correct)")]

        if not CORPUS_SHIPS_AS_PLUGIN:
            return Finding(
                GI, "d3_inert_key", subject, H.INFO,
                note="CORPUS_SHIPS_AS_PLUGIN is False, so this corpus is not "
                     "assumed to install as a plugin and `mcpServers:` may be "
                     "live -- its PRESENCE is not judged. R6 (group D) still "
                     "judges its VALUES.", evidence=evidence)
        if not agent.servers_present:
            return Finding(
                GI, "d3_inert_key", subject, H.PASS,
                note="no `mcpServers:` key -- nothing is silently ignored, and "
                     "the agent's whole grant surface is the `tools:` list that "
                     "3a and 3b judge", evidence=evidence)
        return Finding(
            GI, "d3_inert_key", subject, H.FAIL,
            problem="frontmatter carries an `mcpServers:` key, which a "
                    "PLUGIN-shipped agent does not support: Claude Code ignores "
                    "`hooks:`, `mcpServers:` and `permissionMode:` in a plugin "
                    "agent at load time, for security reasons. The key confers "
                    "NOTHING while reading as a granted capability. Delete it "
                    "and put the grant in `tools:` as mcp__<server>__<tool>.",
            note="not merely redundant: this key has already hidden a real "
                 "defect, because 3b used to demote a genuine missing grant to "
                 "INFO whenever `mcpServers:` declared the server",
            detail=["declares    : %s" % (", ".join(agent.server_entries) or
                                          "(key present, no entries)"),
                    "grants      : %s" % (", ".join(agent.tool_entries) or "-"),
                    "R6 (group D): still judges those VALUES independently -- a "
                    "key reintroduced with a bogus server name trips both rules"],
            evidence=evidence)

    def _context_excuse(self, agent, pre):
        """Why THIS mention cannot be attributed to the agent itself, else ""."""
        line = agent.line(pre.lineno)
        # A retirement sentence routinely spans a line break, so the negative
        # window is +/-1 line.  The other two are line-exact on purpose: a
        # neighbouring `e.g.` must not excuse a prescription of its own.
        window = " ".join(agent.line(n) for n in (pre.lineno - 1, pre.lineno,
                                                  pre.lineno + 1))
        if RX_NEGATIVE.search(window):
            return "negative / retirement context"
        if RX_ILLUSTRATIVE.search(line):
            return "illustrative example (e.g. / for example / such as / etc.)"
        others = sorted({m.group(0) for m in RX_OTHER_AGENT.finditer(line)
                         if agent.name not in m.group(0)})
        if others:
            return "delegation context (%s)" % ", ".join(others[:3])
        return ""

    def _missing_grant(self, agent, grants, tool, pres, servers):
        """3b: one (agent, ungranted-but-existing tool) verdict.

        There is NO `mcpServers:` escape hatch -- see the REMOVED SUPPRESSOR note
        in the module docstring.  A plugin agent's `mcpServers:` key is ignored at
        load time, so it cannot soften a missing grant; 3d fails on the key.
        """
        subject = "%s body -> %s" % (agent.name, tool)
        evidence = [(p.where, p.snippet) for p in pres]

        excuses, attributable = {}, []
        for pre in pres:
            excuse = self._context_excuse(agent, pre)
            if excuse:
                excuses.setdefault(excuse, []).append(pre.where)
            else:
                attributable.append(pre)

        detail = ["granted     : %s" % (", ".join(sorted(grants.tools)) or "-"),
                  "provider    : %s (Scripts/%s)"
                  % (", ".join(servers), self.tools[tool]),
                  "grant needed: mcp__%s__%s" % (servers[0], tool),
                  "mentions    : %d, attributable to this agent: %d"
                  % (len(pres), len(attributable))]
        detail += ["suppressed  : %s -- %s" % (why, ", ".join(wheres))
                   for why, wheres in sorted(excuses.items())]

        if not attributable:
            return Finding(
                GI, "d3_missing_grant", subject, H.INFO,
                note="%s is prescribed nowhere this suite can attribute to the "
                     "agent itself (%s) -- reported with evidence rather than "
                     "failed, because attribution is what separates a real "
                     "missing grant from generic prose"
                     % (tool, "; ".join(sorted(excuses))),
                detail=detail, evidence=evidence)
        return Finding(
            GI, "d3_missing_grant", subject, H.FAIL,
            problem="the agent's own body prescribes %s (registered via %s) but "
                    "its frontmatter `tools:` does not grant it -- at runtime "
                    "the tool is simply absent, so the model silently "
                    "substitutes something else. Fix by granting "
                    "mcp__%s__%s or by not prescribing it."
                    % (tool, ", ".join(servers), servers[0], tool),
            detail=detail, evidence=evidence)

    def check_body(self, agent, grants, prescriptions):
        """3b for one agent -> (receipt, findings, retired_mentions).

        `retired_mentions` are candidates dropped because the tool is not
        grantable at all; group H asserts that they never become findings.
        """
        grantable = self.grantable()
        mentions = {}
        for pre in prescriptions:
            if pre.lineno <= agent.fm_end:
                continue          # frontmatter is R1/R6's surface, not 3b's
            tool = prescribed_tool(pre)
            if tool:
                mentions.setdefault(tool, []).append(pre)

        findings, retired, covered, unknown = [], [], [], []
        for tool, pres in sorted(mentions.items()):
            if agent.unrestricted or tool in grants.tools:
                covered.append(tool)
            elif tool not in self.tools:
                unknown.append(tool)      # R5 already FAILs this in group D
            elif tool not in grantable:
                retired.append((tool, pres))
            else:
                findings.append(self._missing_grant(agent, grants, tool, pres,
                                                    grantable[tool]))

        candidates = [f.subject.split("-> ")[-1] for f in findings]
        receipt = Finding(
            GI, "d3_body_scan", "body scan: %s" % agent.name,
            H.INFO if agent.unrestricted else H.PASS,
            note="%d dispatcher(s) prescribed in the body, %d covered by "
                 "`tools:`%s" % (len(mentions), len(covered),
                                 ", UNRESTRICTED grant"
                                 if agent.unrestricted else ""),
            detail=["prescribed  : %s" % (", ".join(sorted(mentions)) or "-"),
                    "granted     : %s" % (", ".join(sorted(grants.tools)) or "-"),
                    "covered     : %s" % (", ".join(sorted(covered)) or "-"),
                    "reported    : %s" % (", ".join(sorted(candidates)) or "-"),
                    "not grantable (retirement notes, never a finding): %s"
                    % (", ".join(sorted(t for t, _p in retired)) or "-"),
                    "no such dispatcher anywhere (R5's job, group D): %s"
                    % (", ".join(sorted(unknown)) or "-")],
            evidence=[("%s:%d" % (agent.rel, agent.fm_end + 1),
                       "body starts here")])
        return receipt, findings, retired

    def check_ungranted(self, granted_by_tool, agents):
        """3c: a registered dispatcher NO agent's `tools:` names (INFO only)."""
        wildcard = sorted(a.name for a in agents if a.unrestricted)
        out = []
        for tool, servers in sorted(self.grantable().items()):
            holders = sorted(granted_by_tool.get(tool, ()))
            detail = ["server(s)   : %s" % ", ".join(servers),
                      "script      : Scripts/%s" % self.tools[tool],
                      "granted to  : %s" % (", ".join(holders) or "NO agent")]
            if holders:
                note = "granted to %d agent(s)" % len(holders)
            elif wildcard:
                detail.append("note        : %d agent(s) carry an unrestricted "
                              "grant (%s), which covers every tool"
                              % (len(wildcard), ", ".join(wildcard)))
                note = ("no explicit grant, but an unrestricted agent covers it")
            else:
                out.append(Finding(
                    GI, "d3_ungranted", "capability: %s" % tool, H.INFO,
                    note="registered and reachable, but NO agent's `tools:` "
                         "grants it -- an orphan capability. Either an agent "
                         "should be granted it or the prompts that prescribe it "
                         "are prescribing something no minion can run.",
                    detail=detail))
                continue
            out.append(Finding(GI, "d3_ungranted", "capability: %s" % tool,
                               H.PASS, note=note, detail=detail))
        return out


def evaluate_agents(checker, agents, prescriptions):
    """Direction 3 over a whole agent corpus."""
    by_path = {}
    for pre in prescriptions:
        by_path.setdefault(pre.path, []).append(pre)

    findings, retired, granted_by_tool = [], [], {}
    for agent in sorted(agents, key=lambda a: a.rel):
        grants = checker.agent_grants(agent)
        findings.append(checker.check_grants(agent, grants))
        findings.append(checker.check_mcpservers_key(agent))
        for tool in grants.tools:
            granted_by_tool.setdefault(tool, []).append(agent.name)
        receipt, body, retired_here = checker.check_body(
            agent, grants, by_path.get(agent.rel, []))
        findings.append(receipt)
        findings += body
        retired += [(agent, tool, pres) for tool, pres in retired_here]
    findings += checker.check_ungranted(granted_by_tool, agents)
    return {"findings": findings, "retired": retired,
            "granted_by_tool": granted_by_tool, "agents": agents}


# ---------------------------------------------------------------------------
# negative control
# ---------------------------------------------------------------------------

# Every line is a deliberate probe.  `must_flag` names have to end up in the
# FAIL set; `must_not_flag` names prove the extractor does not eat prose.
FIXTURE_FILES = {
    "agents/fake-agent.md": (
        "---\n"
        "name: fake-agent\n"
        "tools: Read, mcp__mcp-purity__purity_call, "
        "mcp__mcp-nonexistent__foo_call, mcp__mcp-purity__not_a_tool\n"
        "mcpServers:\n"
        "  - mcp-purity\n"
        "  - mcp-nowhere\n"
        "---\n"
        "\n"
        "| Lua | `purity_call` (luals-backed) | `luals_document_symbols`, "
        "`luals_find_definition[_at]` |\n"
        "Use `purity_call(function=\"totally_fake_function\")` for the sweep.\n"
        "Also `bogus_call(function=\"whatever\")` is prescribed here.\n"
        "Wrong dispatcher: `git_call(function=\"outline\")` -- outline is a "
        "purity function, git does not have it.\n"
        "Real ones that MUST stay clean: `purity_call(find_definition)`, "
        "`purity_call(function: \"search_for_pattern\")`, `luals_hover`.\n"
    ),
    # -- Direction 3 fixtures.  One synthetic AGENT per verdict, because a D3
    # that is silently broken looks exactly like a clean corpus.  Every one of
    # these lives under `agents/`, which is the only place collect_agents()
    # looks.  They deliberately avoid `create_temp_dir` (group H's D2 probe
    # asserts it stays unmentioned) and avoid bare `mcp-<name>` refs (R8 is not
    # applied to the corpus).
    "agents/d3-dead-grant.md": (
        "---\n"
        "name: d3-dead-grant\n"
        "tools: Read, mcp__mcp-purity__purity_call, mcp__mcp-purity__ghost_call\n"
        "---\n"
        "\n"
        "Body prescribes only what it was granted: `purity_call`.\n"
    ),
    "agents/d3-missing-grant.md": (
        "---\n"
        "name: d3-missing-grant\n"
        "tools: Read, mcp__mcp-purity__purity_call\n"
        "---\n"
        "\n"
        "| Packet captures | `tshark_call` — MANDATORY for every pcap |\n"
    ),
    "agents/d3-ok.md": (
        "---\n"
        "name: d3-ok\n"
        "tools: Read, mcp__mcp-purity__purity_call, mcp__mcp-forge__forge_call\n"
        "---\n"
        "\n"
        "Routing: `purity_call` for symbols, `forge_call` for builds.\n"
    ),
    "agents/d3-retired.md": (
        "---\n"
        "name: d3-retired\n"
        "tools: Read, mcp__mcp-purity__purity_call\n"
        "---\n"
        "\n"
        "`luals_call` was RETIRED and its server is unregistered -- use "
        "`purity_call` instead.\n"
    ),
    "agents/d3-negative.md": (
        "---\n"
        "name: d3-negative\n"
        "tools: Read, mcp__mcp-purity__purity_call\n"
        "---\n"
        "\n"
        "`jenkins_call` does not exist in this project's toolbelt. Never "
        "call it.\n"
    ),
    "agents/d3-illustrative.md": (
        "---\n"
        "name: d3-illustrative\n"
        "tools: Read, mcp__mcp-purity__purity_call\n"
        "---\n"
        "\n"
        "Most servers expose one dispatcher (e.g. `lldb_call`, `gdc_call`) -- "
        "check which ones your session actually has.\n"
    ),
    "agents/d3-delegation.md": (
        "---\n"
        "name: d3-delegation\n"
        "tools: Read, mcp__mcp-purity__purity_call\n"
        "---\n"
        "\n"
        "Hand pcap work to `p:minion-sniffer`, which calls `tshark_call` in "
        "its own context.\n"
    ),
    # 3d PLUS the REMOVED `mcpServers:` suppressor, in one fixture.  It used to
    # prove that an `mcpServers:` declaration DEMOTED a missing grant to INFO; it
    # now proves the opposite, and both halves are pinned in D3_EXPECT: the
    # declaration rescues nothing (3b FAIL on `tshark_call`) and the key itself
    # is the defect (3d FAIL).  Both servers named here are REGISTERED on
    # purpose, so R6 stays PASS on the VALUES while 3d fails on the PRESENCE --
    # the two rules must be shown to be independent, not one rule twice.
    "agents/d3-mcpservers.md": (
        "---\n"
        "name: d3-mcpservers\n"
        "tools: Read, mcp__mcp-purity__purity_call\n"
        "mcpServers:\n"
        "  - mcp-purity\n"
        "  - mcp-tshark\n"
        "---\n"
        "\n"
        "| Packet captures | `tshark_call` — MANDATORY for every pcap |\n"
    ),
    "agents/d3-wildcard.md": (
        "---\n"
        "name: d3-wildcard\n"
        "tools: *\n"
        "---\n"
        "\n"
        "A wildcard grant, so `tshark_call` here is NOT a missing grant.\n"
    ),
    "agents/d3-no-tools-key.md": (
        "---\n"
        "name: d3-no-tools-key\n"
        "model: inherit\n"
        "---\n"
        "\n"
        "No grant list at all, so `jenkins_call` here is NOT a missing grant.\n"
    ),
    "skills/fake-skill/SKILL.md": (
        "---\ntitle: fake skill\n---\n"
        "Prose bait the extractor must ignore:\n"
        "purity_call (clangd-backed) and purity_call (purity MCP, "
        "clangd/luals-backed) and forge_call (MANDATORY when building).\n"
        "YAML-ish fields: function: string / function: null / "
        "function: poluah_websocket_send_frame\n"
        "Placeholder + kwarg forms: context7_call(function=..., "
        "params={...}) / forge_call(function=\"\") / "
        "forge_call(targets=[\"app\"])\n"
        "Ordinary backticked words: `compile_commands`, `wiki_root`, "
        "`git_dir`, `build_system`, `some_random_word`, `cat`, `README`.\n"
    ),
}

# (subject, why, needs_registration).  `needs_registration` entries can only be
# judged when ~/.claude.json was readable; otherwise they degrade to SKIP rather
# than failing the suite for an environmental reason.
MUST_FLAG = [
    ("luals_document_symbols", "R4 namespaced backtick, the historical offender",
     False),
    ("totally_fake_function", "R2 dispatch_call with function=", False),
    ("whatever", "R2 function name behind an unknown dispatcher", False),
    ("outline", "R3 real name prescribed against the WRONG dispatcher", False),
    ("bogus_call", "R5 dispatcher no server exposes", False),
    ("mcp__mcp-nonexistent__foo_call", "R1 unregistered server", True),
    ("mcp__mcp-purity__not_a_tool", "R1 wrong tool on a real server", True),
    ("mcpServers: mcp-nowhere", "R6 unregistered frontmatter server", True),
]

MUST_NOT_FLAG = [
    "purity", "clangd", "luals", "compile_commands", "wiki_root", "git_dir",
    "build_system", "some_random_word", "string", "null",
    "poluah_websocket_send_frame", "find_definition", "search_for_pattern",
    "luals_hover", "mcp__mcp-purity__purity_call", "mcpServers: mcp-purity",
    "cat", "README", "function", "params", "targets",
]

# Direction 3 control: (subject, EXACT expected severity, why).
#
# `None` means "no finding at all" and is a real assertion, not a gap: a
# suppressor that silently swallows a genuine defect and a suppressor that
# correctly drops a non-defect both look like "no FAIL", so the INFO rows and
# the silent drops are pinned just as hard as the failures.
D3_EXPECT = [
    ("tools: d3-dead-grant", H.FAIL,
     "3a: grants mcp__mcp-purity__ghost_call, a tool no server exposes"),
    ("tools: fake-agent", H.FAIL,
     "3a: the older fixture's grant list is dead too (foo_call, not_a_tool)"),
    ("tools: d3-ok", H.PASS, "3a: every grant exists in the live inventory"),
    ("tools: d3-wildcard", H.INFO, "3a: `tools: *` -- nothing to verify"),
    ("tools: d3-no-tools-key", H.INFO, "3a: no `tools:` key -- unrestricted"),
    ("d3-missing-grant body -> tshark_call", H.FAIL,
     "3b: the body prescribes a real, REGISTERED, ungranted dispatcher"),
    ("fake-agent body -> git_call", H.FAIL,
     "3b: the older fixture's body calls git_call(function=\"outline\") while "
     "its tools: grants purity_call only -- pinned so an edit to that fixture "
     "cannot silently drop a FAIL"),
    ("d3-ok body -> purity_call", None,
     "3b: correctly granted -> no finding at all"),
    ("d3-retired body -> luals_call", None,
     "3b: the tool's server is unregistered, so there is nothing to grant -- a "
     "retirement note is never a missing grant"),
    ("d3-negative body -> jenkins_call", H.INFO,
     "3b: a 'does not exist' sentence -> INFO, never a FAIL"),
    ("d3-illustrative body -> lldb_call", H.INFO,
     "3b: named inside an illustrative `e.g.` list -> INFO"),
    ("d3-illustrative body -> gdc_call", H.INFO,
     "3b: named inside an illustrative `e.g.` list -> INFO"),
    ("d3-delegation body -> tshark_call", H.INFO,
     "3b: the tool belongs to the DELEGATE, not to this agent -> INFO"),
    ("d3-mcpservers body -> tshark_call", H.FAIL,
     "3b: `mcpServers:` declares the server while `tools:` does not -- and that "
     "no longer rescues anything. The key is ignored at load time, so this is a "
     "plain missing grant. Pinned as FAIL because it is the exact row the "
     "REMOVED suppressor used to demote to INFO, and the exact shape of the live "
     "p:minion-watson / context7_call defect it hid"),
    # -- 3d.  A FAIL-only rule is as blind as a PASS-only one, so the key's
    # presence AND its absence are both pinned, on two different fixtures.
    ("mcpServers: key in d3-mcpservers", H.FAIL,
     "3d: a plugin agent carrying an `mcpServers:` key -- inert at load time, so "
     "the key is the defect regardless of what it names (both its servers are "
     "REGISTERED, so R6 has nothing to say and only 3d can fail this)"),
    ("mcpServers: key in fake-agent", H.FAIL,
     "3d: the older fixture carries the key too, with one bogus server -- pinned "
     "so it is visible that 3d (PRESENCE) and R6 (VALUES) both fire on it, "
     "rather than one masking the other"),
    ("mcpServers: key in d3-ok", H.PASS,
     "3d: no `mcpServers:` key -- proves the rule discriminates instead of "
     "failing every agent it sees"),
    ("d3-wildcard body -> tshark_call", None,
     "3b: a wildcard grant covers every tool"),
    ("d3-no-tools-key body -> jenkins_call", None,
     "3b: no `tools:` key, so the agent is unrestricted"),
]

# A synthetic MCP server whose tool description carries dead routing text.  It
# reproduces the exact shape of the six real dead references a sibling agent
# found -- including `Scripts/mcp-lldb.py:768`'s `Code navigation -> mcp-clangd`
# (already repaired upstream, so only a fixture can keep proving the detector
# sees it).  Scanned as a REGISTERED server, so the verdicts must be FAIL.
FIXTURE_SERVER_NAME = "mcp-fakeserver.py"
FIXTURE_SERVER = '''#!/usr/bin/env python3
"""mcp-fakeserver — synthetic server for tests. Build -> mcp-nonexistent."""

# A comment naming mcp-commentonly must NEVER be extracted (not model-facing).
LOG_NAME = "mcp-alsobogus"

LISTED_TOOLS = [
    {
        "name": "fakeserver_call",
        "description": (
            "Call any fake function by name.\\n\\n"
            "When NOT to use:\\n"
            "  - Ad-hoc shell -> Bash. Code navigation -> mcp-clangd.\\n\\n"
            "IMPORTANT: load the p:mcp-luals skill for the API reference.\\n"
            "Lua outline: `luals_document_symbols`. See Scripts/mcp-purity.py."
        ),
        "inputSchema": {
            "properties": {
                "function": {
                    "description": "Function name (e.g. purity_call"
                                   "(function=\\"totally_fake_function\\"))",
                },
            },
        },
    },
]
'''

SERVER_MUST_FLAG = [
    ("mcp-nonexistent", "R8 dead server named in the MODULE DOCSTRING", True),
    ("mcp-clangd", "R8 retired-but-present server in a tool description", True),
    ("luals_document_symbols", "R4 dead function name in a tool description",
     False),
    ("totally_fake_function",
     "R2 dead function name in an inputSchema description", False),
]

SERVER_MUST_NOT_FLAG = [
    "mcp-luals",        # `p:mcp-luals` is a SKILL reference, not a server
    "mcp-purity",       # `Scripts/mcp-purity.py` is a filename
    "mcp-alsobogus",    # a module-level assignment is not model-facing
    "mcp-commentonly",  # a comment is not model-facing
    "mcp-fakeserver",   # a docstring naming its OWN file is identity, not routing
]


def write_fixture(root):
    for rel, body in FIXTURE_FILES.items():
        write_text(os.path.join(root, rel), body)
    return root


def write_server_fixture(root):
    return write_text(os.path.join(root, FIXTURE_SERVER_NAME), FIXTURE_SERVER)


# ---------------------------------------------------------------------------
# suite
# ---------------------------------------------------------------------------

def _record_finding(suite, finding, group=None):
    detail = []
    if finding.note:
        detail.append("note        : %s" % finding.note)
    detail += list(finding.detail)
    for where, snippet in finding.evidence[:MAX_EVIDENCE]:
        detail.append("evidence    : %s | %s" % (where, snippet))
    extra = len(finding.evidence) - MAX_EVIDENCE
    if extra > 0:
        detail.append("evidence    : ... and %d more occurrence(s)" % extra)
    cid = "%-18s %s" % (finding.kind, finding.subject)
    problems = [finding.problem] if finding.severity == H.FAIL else []
    status = finding.severity
    brief = "%s | %s | %s" % (status, cid, finding.problem or finding.note)
    suite.record(group or finding.group, cid, problems, status=status,
                 detail=detail, brief=brief)


def _assert_control(suite, label, must_flag, must_not_flag, flagged,
                    all_subjects, registration_known=True):
    """Assert the detector fires on planted defects and stays quiet on bait."""
    for subject, why, needs_reg in must_flag:
        if needs_reg and not registration_known:
            suite.record(GH, "%s must flag: %s" % (label, subject), [],
                         status=SKIP,
                         detail=["rule        : %s" % why,
                                 "reason      : this probe needs the "
                                 "~/.claude.json registration, which could not "
                                 "be read -- degraded to SKIP rather than "
                                 "failing for an environmental reason"],
                         brief="%s | %s must flag %s (registration unknown)"
                               % (SKIP, label, subject))
            continue
        problems = []
        if subject not in flagged:
            other = all_subjects.get(subject)
            problems.append(
                "detector did NOT flag %r (%s); verdict was %s"
                % (subject, why, other.severity if other else "no finding"))
        suite.record(GH, "%s must flag: %s" % (label, subject), problems,
                     detail=["rule        : %s" % why,
                             "verdict     : %s"
                             % (flagged[subject].problem if subject in flagged
                                else "NOT FLAGGED")],
                     brief="%s | %s must flag %s"
                           % (H.FAIL if problems else H.PASS, label, subject))

    for subject in must_not_flag:
        problems = []
        if subject in flagged:
            problems.append("false positive: detector flagged %r -- %s"
                            % (subject, flagged[subject].problem))
        suite.record(GH, "%s must not flag: %s" % (label, subject), problems,
                     detail=["verdict     : %s"
                             % (flagged[subject].problem if subject in flagged
                                else "not flagged (correct)")],
                     brief="%s | %s must not flag %s"
                           % (H.FAIL if problems else H.PASS, label, subject))


def _assert_d3_control(suite, findings, expectations, registration_known=True):
    """Pin the EXACT Direction 3 verdict of every synthetic agent.

    Deliberately stricter than the flag/no-flag control above: D3's whole risk
    is a suppressor that is too eager, and an over-eager suppressor produces the
    same "no FAIL" as a correct one.  So INFO is asserted to be INFO and silence
    is asserted to be silence.
    """
    by_subject = {f.subject: f for f in findings}
    for subject, expected, why in expectations:
        label = "d3 control: %s" % subject
        if not registration_known:
            suite.record(GH, label, [], status=SKIP,
                         detail=["rule        : %s" % why,
                                 "reason      : D3 decides grantability from "
                                 "~/.claude.json, which could not be read -- "
                                 "degraded to SKIP rather than failing for an "
                                 "environmental reason"],
                         brief="%s | %s (registration unknown)" % (SKIP, label))
            continue
        actual = by_subject.get(subject)
        got = actual.severity if actual else None
        problems = []
        if got != expected:
            problems.append("expected %s, got %s"
                            % (expected or "NO finding", got or "NO finding"))
        detail = ["rule        : %s" % why,
                  "expected    : %s" % (expected or "no finding at all"),
                  "actual      : %s" % (got or "no finding at all")]
        if actual:
            detail.append("verdict     : %s" % (actual.problem or actual.note))
        suite.record(GH, label, problems, detail=detail,
                     brief="%s | %s -> %s"
                           % (H.FAIL if problems else H.PASS, label,
                              got or "no finding"))


def run(opts=None):
    global FIXTURE_ROOT
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="corpus <-> MCP inventory name existence",
                    opts=opts, mode="grouped")

    # A per-run subdirectory INSIDE the project sandbox: concurrency-safe, and
    # still never outside .claude/tmp/.
    os.makedirs(FIXTURE_BASE, exist_ok=True)
    FIXTURE_ROOT = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    del WRITES[:]
    del CHILD_ARGV[:]
    try:
        return _run(suite, opts)
    finally:
        if opts.keep:
            print("\n[--keep] fixtures retained at: %s" % FIXTURE_ROOT)
        else:
            shutil.rmtree(FIXTURE_ROOT, ignore_errors=True)
            try:                      # tidy the base away when nobody else is in it
                os.rmdir(FIXTURE_BASE)
            except OSError:
                pass


IMPORTED_MODULES = ("_mcp_smoke_test", "test_name_existence", "_harness")


def _record_hygiene(suite, before):
    """This suite must never leave a .pyc inside the repo tree.

    Two separate questions, because a count-based check is blind to a file that
    already existed:
      * did THIS run create or touch any __pycache__ entry?   -> FAIL
      * is there a STALE .pyc for a module this suite imports? -> INFO (it
        cannot be attributed to this run, but it must be visible)
    """
    after = H.pycache_snapshot()
    created = sorted(set(after) - set(before))
    touched = sorted(p for p in set(after) & set(before)
                     if after[p] != before[p])
    problems = []
    if created:
        problems.append("this run CREATED %d __pycache__ file(s): %s"
                        % (len(created), ", ".join(
                            os.path.relpath(p, H.REPO_ROOT) for p in created)))
    if touched:
        problems.append("this run TOUCHED %d __pycache__ file(s): %s"
                        % (len(touched), ", ".join(
                            os.path.relpath(p, H.REPO_ROOT) for p in touched)))
    suite.record(GB, "no __pycache__ created by this run", problems,
                 detail=["before      : %d file(s) under the repo tree" % len(before),
                         "after       : %d file(s)" % len(after),
                         "guards      : sys.dont_write_bytecode set before the "
                         "first repo import; children get -B plus "
                         "PYTHONDONTWRITEBYTECODE=1"],
                 brief="%s | pycache %d -> %d"
                       % (H.FAIL if problems else H.PASS, len(before), len(after)))

    stale = sorted(p for p in before
                   if any(mod in os.path.basename(p) for mod in IMPORTED_MODULES))
    suite.record(GB, "stale __pycache__ for imported modules", [],
                 status=H.INFO if stale else H.PASS,
                 detail=(["%s (NOT created by this run)"
                          % os.path.relpath(p, H.REPO_ROOT) for p in stale]
                         or ["none -- %s" % ", ".join(IMPORTED_MODULES)]),
                 brief="%s | %d stale pyc for imported modules"
                       % (H.INFO if stale else H.PASS, len(stale)))


def _record_sandbox(suite, tmp_before):
    """Every byte this suite writes must land under .claude/tmp/.

    Asserted structurally, not by inspection: `write_text` is the only write
    path and it records every target, `probe_argv` is the only child launcher
    and it records every command line.  A future write that bypasses them shows
    up here as a failure.
    """
    sandbox_root = H.repo_path(".claude", "tmp")
    problems = []
    if not inside(FIXTURE_ROOT, sandbox_root):
        problems.append("FIXTURE_ROOT %s is not under %s"
                        % (FIXTURE_ROOT, sandbox_root))
    if FIXTURE_ROOT == FIXTURE_BASE:
        problems.append("FIXTURE_ROOT was never given a per-run subdirectory, "
                        "so a concurrent instance's teardown can delete these "
                        "fixtures mid-run")
    stray = [p for p in WRITES if not inside(p, FIXTURE_ROOT)]
    if stray:
        problems.append("%d write(s) outside the fixture sandbox: %s"
                        % (len(stray), ", ".join(stray)))
    # The per-run directory name is masked so the report stays byte-identical
    # across runs (the whole point of a per-run directory is that it varies).
    suite.record(GB, "every write lands under .claude/tmp", problems,
                 detail=["sandbox     : %s/run-<unique>"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "writes      : %d, all inside the sandbox: %s"
                         % (len(WRITES), not stray),
                         "paths       : %s"
                         % ", ".join(sorted(os.path.relpath(p, FIXTURE_ROOT)
                                            for p in WRITES))],
                 brief="%s | %d writes, %d stray"
                       % (H.FAIL if problems else H.PASS, len(WRITES),
                          len(stray)))

    roots = []
    for argv in CHILD_ARGV:
        for idx, token in enumerate(argv[:-1]):
            if token == "--project-root":
                roots.append(argv[idx + 1])
    outside = sorted({r for r in roots if not inside(r, FIXTURE_ROOT)})
    problems = []
    if outside:
        problems.append("%d child process(es) were given a --project-root "
                        "outside the sandbox: %s"
                        % (len(outside), ", ".join(outside)))
    suite.record(GB, "no child process works outside the sandbox", problems,
                 detail=["children    : %d launched, %d given a --project-root"
                         % (len(CHILD_ARGV), len(roots)),
                         "note        : the imported launch table says "
                         "`--project-root /tmp`; probe_argv rewrites every one "
                         "to the sandbox"],
                 brief="%s | %d children, %d outside"
                       % (H.FAIL if problems else H.PASS, len(CHILD_ARGV),
                          len(outside)))

    # Visibility only: a shared temp dir belongs to every process on the host,
    # so a new entry there cannot be attributed to this run.  BOTH dirs are
    # watched: on macOS `tempfile.gettempdir()` is a private
    # /var/folders/... path, so watching it alone would miss literal /tmp --
    # which is exactly where scratch is forbidden to go.
    appeared, details = [], []
    for tmp_dir in system_temp_dirs():
        try:
            now = set(os.listdir(tmp_dir))
        except OSError:
            continue
        new = sorted(now - set(tmp_before.get(tmp_dir, ())))
        appeared += ["%s/%s" % (tmp_dir, name) for name in new]
        details.append("%-28s %d new entr%s" % (tmp_dir, len(new),
                                                "y" if len(new) == 1 else "ies"))
    suite.record(GB, "shared temp dirs untouched by this run", [],
                 status=H.INFO if appeared else H.PASS,
                 detail=details + ["  %s" % path for path in appeared[:20]]
                        + (["note        : shared dirs -- new entries are NOT "
                            "necessarily ours"] if appeared else []),
                 brief="%s | %d new entries across %d shared temp dir(s)"
                       % (H.INFO if appeared else H.PASS, len(appeared),
                          len(system_temp_dirs())))


def _run(suite, opts):
    pycache_before = H.pycache_snapshot()
    tmp_before = system_temp_snapshot()

    # -- A. inventory ----------------------------------------------------
    inventories = build_inventory()
    for inv in inventories:
        detail = ["source      : %s" % (inv.source or "-"),
                  "tool        : %s" % inv.tool,
                  "functions   : %d" % len(inv.functions)]
        if inv.functions:
            names = ", ".join(sorted(inv.functions))
            detail.append("names       : %s" % names[:400]
                          + (" ..." if len(names) > 400 else ""))
        status = H.PASS if inv.ok else SKIP
        suite.record(GA, "enumerate %s" % inv.file, [], status=status,
                     detail=detail + ([] if inv.ok else
                                      ["reason      : %s" % inv.error]),
                     brief="%s | %s | %d functions"
                           % (status, inv.file, len(inv.functions)))

    registration, reg_error = load_registration()
    reg_detail = ["path        : %s" % CLAUDE_JSON]
    if registration is None:
        reg_detail.append("reason      : %s" % reg_error)
    else:
        reg_detail.append("registered  : %s" % ", ".join(sorted(registration)))
    suite.record(GA, "~/.claude.json registration", [],
                 status=H.PASS if registration is not None else SKIP,
                 detail=reg_detail,
                 brief="%s | registration | %s"
                       % (H.PASS if registration is not None else SKIP,
                          "%d servers" % len(registration or {})))

    checker = Checker(inventories, registration, reg_error)

    # -- B. corpus scan ---------------------------------------------------
    corpus = scan_corpus(CORPUS_ROOT)
    kinds = {}
    for pre in corpus["prescriptions"]:
        kinds[pre.kind] = kinds.get(pre.kind, 0) + 1
    suite.record(GB, "scan ClaudeCode/**", [], status=H.INFO,
                 detail=["files       : %d" % len(corpus["files"]),
                         "extractions : %d" % len(corpus["prescriptions"]),
                         "by rule     : %s"
                         % ", ".join("%s=%d" % kv for kv in sorted(kinds.items())),
                         "namespaces  : %s (derived from the live inventory)"
                         % ", ".join(checker.namespaces),
                         "inventory   : %d distinct function names across %d "
                         "enumerated servers"
                         % (len(checker.functions), len(checker.inventories))],
                 brief="INFO | corpus scan | %d files, %d extractions"
                       % (len(corpus["files"]), len(corpus["prescriptions"])))
    if corpus["unreadable"]:
        suite.record(GB, "unreadable corpus files",
                     ["%d file(s) could not be decoded" % len(corpus["unreadable"])],
                     detail=["%s -- %s" % (p, e) for p, e in corpus["unreadable"]],
                     brief="FAIL | unreadable corpus files")

    if not corpus["files"]:
        suite.record(GB, "corpus root is populated",
                     ["no readable file under %s -- Direction 1 would pass "
                      "vacuously" % CORPUS_ROOT],
                     brief="FAIL | empty corpus root")

    # -- B. server model-facing text scan --------------------------------
    live_files = set()
    if registration is not None:
        live_files = {s for s in registration.values() if s}
    server_files = server_source_files(inventories)
    servers = scan_server_text(server_files, live_files)
    kinds = {}
    for pre in servers["prescriptions"]:
        kinds[pre.kind] = kinds.get(pre.kind, 0) + 1
    live = [rel for rel, origin, _n in servers["files"] if origin == O_LIVE]
    dead = [rel for rel, origin, _n in servers["files"] if origin == O_DEAD]
    suite.record(GB, "scan Scripts/mcp-*.py model-facing text", [],
                 status=H.INFO,
                 detail=["servers     : %d (%d registered, %d not)"
                         % (len(servers["files"]), len(live), len(dead)),
                         "registered  : %s" % ", ".join(sorted(live)),
                         "unregistered: %s" % ", ".join(sorted(dead)),
                         "text lines  : %d"
                         % sum(n for _r, _o, n in servers["files"]),
                         "extractions : %d" % len(servers["prescriptions"]),
                         "by rule     : %s"
                         % ", ".join("%s=%d" % kv for kv in sorted(kinds.items()))],
                 brief="INFO | server text | %d servers, %d extractions"
                       % (len(servers["files"]), len(servers["prescriptions"])))
    if servers["unreadable"]:
        suite.record(GB, "unparseable server sources",
                     ["%d server(s) could not be parsed"
                      % len(servers["unreadable"])],
                     detail=["%s -- %s" % (p, e) for p, e in servers["unreadable"]],
                     brief="FAIL | unparseable server sources")

    # -- C / D / E / F. Direction 1 --------------------------------------
    for finding in checker.evaluate(corpus["prescriptions"]):
        _record_finding(suite, finding)
    for finding in checker.evaluate(servers["prescriptions"]):
        _record_finding(suite, finding)

    # -- G. Direction 2 ---------------------------------------------------
    # Anchored on the PROMPT corpus, as the original create_temp_dir case was:
    # a function only its own server's description mentions is still something
    # no agent or skill was ever told about.
    unmentioned = checker.unmentioned(corpus["text"])
    in_server_text = checker.unmentioned(servers["text"])
    for inv in checker.inventories:
        missing = unmentioned.get(inv.file, [])
        only_server_text = [fn for fn in missing
                            if fn not in set(in_server_text.get(inv.file, []))]
        detail = ["functions   : %d" % len(inv.functions),
                  "unmentioned : %d" % len(missing),
                  "of those, mentioned in server description text: %d"
                  % len(only_server_text)]
        detail += ["  %s%s" % (fn, "  (in server text)"
                               if fn in only_server_text else "")
                   for fn in missing]
        suite.record(GG, "unmentioned in %s" % inv.file, [], status=H.INFO,
                     detail=detail,
                     brief="INFO | %s | %d/%d unmentioned"
                           % (inv.file, len(missing), len(inv.functions)))

    # -- I. Direction 3: agent grants vs agent prescriptions --------------
    agents = collect_agents(CORPUS_ROOT)
    problems = []
    if not agents:
        problems.append("no agent definition found under %s/ -- Direction 3 "
                        "would pass vacuously"
                        % os.path.join(os.path.relpath(CORPUS_ROOT, H.REPO_ROOT),
                                       AGENT_SUBDIR))
    suite.record(GB, "scan ClaudeCode/agents/**", problems,
                 status=None if problems else H.INFO,
                 detail=["agents      : %d" % len(agents),
                         "names       : %s"
                         % ", ".join(a.name for a in agents),
                         "with tools: : %d"
                         % sum(1 for a in agents if a.tools_present),
                         "unrestricted: %d"
                         % sum(1 for a in agents if a.unrestricted),
                         "grantable   : %d dispatcher(s) a live session can put "
                         "in a tools: list" % len(checker.grantable())],
                 brief="INFO | agent scan | %d agents" % len(agents))

    d3 = evaluate_agents(checker, agents, corpus["prescriptions"])
    for finding in d3["findings"]:
        _record_finding(suite, finding)

    # The retirement suppressor, ASSERTED rather than assumed.  Anchored on the
    # corpus-wide mention count so the case cannot pass merely because nobody
    # mentions a retired dispatcher anywhere.
    retired_tools = sorted(set(checker.tools) - set(checker.grantable()))
    hits = [p for p in corpus["prescriptions"]
            if p.kind == "dispatcher" and p.name in retired_tools]
    per_tool = {}
    for pre in hits:
        per_tool.setdefault(pre.name, []).append(pre.where)
    leaked = sorted(f.subject for f in d3["findings"]
                    if any(f.subject.endswith("-> " + t) for t in retired_tools))
    problems = []
    if registration is not None and not hits:
        problems.append("no corpus mention of a retired dispatcher was found at "
                        "all, so this control is vacuous -- either the "
                        "retirement notes are gone or the extractor stopped "
                        "seeing them")
    if leaked:
        problems.append("Direction 3 produced finding(s) for a dispatcher that "
                        "cannot be granted at all: %s" % ", ".join(leaked))
    suite.record(GI, "%-18s %s" % ("d3_retirement",
                                   "retired dispatchers are never missing grants"),
                 problems, status=SKIP if registration is None else None,
                 detail=["not grantable: %s" % (", ".join(retired_tools) or "-"),
                         "corpus mentions: %d" % len(hits)]
                        + ["  %-14s %d mention(s): %s"
                           % (tool, len(wheres), ", ".join(wheres[:4])
                              + (" ..." if len(wheres) > 4 else ""))
                           for tool, wheres in sorted(per_tool.items())]
                        + ["agent-body mentions: %d" % len(d3["retired"])]
                        + ["  %s -> %s (%s)" % (a.name, t, p[0].where)
                           for a, t, p in d3["retired"]]
                        + (["reason      : registration unknown, so "
                            "grantability cannot be decided"]
                           if registration is None else []),
                 brief="%s | retirement suppressor | %d corpus mentions, %d "
                       "leaked" % (SKIP if registration is None else
                                   (H.FAIL if problems else H.PASS),
                                   len(hits), len(leaked)))

    # 3c must DISCRIMINATE: an all-PASS or all-INFO capability table would be
    # indistinguishable from a broken one.
    cap_rows = [f for f in d3["findings"] if f.kind == "d3_ungranted"]
    granted_rows = [f for f in cap_rows if f.severity == H.PASS]
    orphan_rows = [f for f in cap_rows if f.severity == H.INFO]
    problems = []
    if registration is not None:
        if len(cap_rows) != len(checker.grantable()):
            problems.append("3c produced %d row(s) for %d grantable dispatcher(s)"
                            % (len(cap_rows), len(checker.grantable())))
        if cap_rows and not granted_rows:
            problems.append("3c classified NO dispatcher as granted, so the "
                            "granted/ungranted split is not discriminating")
    suite.record(GI, "%-18s %s" % ("d3_ungranted",
                                   "3c discriminates granted from orphaned"),
                 problems, status=SKIP if registration is None else None,
                 detail=["capabilities: %d" % len(cap_rows),
                         "granted     : %s"
                         % (", ".join(f.subject.split(": ")[-1]
                                      for f in granted_rows) or "-"),
                         "orphaned    : %s"
                         % (", ".join(f.subject.split(": ")[-1]
                                      for f in orphan_rows) or "-")],
                 brief="%s | 3c | %d granted, %d orphaned"
                       % (SKIP if registration is None else
                          (H.FAIL if problems else H.PASS),
                          len(granted_rows), len(orphan_rows)))

    # -- H. negative control ---------------------------------------------
    fixture_root = write_fixture(os.path.join(FIXTURE_ROOT, "corpus"))
    fake = scan_corpus(fixture_root, repo_root=FIXTURE_ROOT)
    fake_findings = checker.evaluate(fake["prescriptions"])
    flagged = {f.subject: f for f in fake_findings if f.severity == H.FAIL}
    all_subjects = {f.subject: f for f in fake_findings}

    suite.record(GH, "fixture scanned", [], status=H.INFO,
                 detail=["root        : %s" % os.path.relpath(fixture_root,
                                                              H.REPO_ROOT),
                         "files       : %d" % len(fake["files"]),
                         "extractions : %d" % len(fake["prescriptions"]),
                         "flagged     : %s" % ", ".join(sorted(flagged)) or "-"],
                 brief="INFO | fixture | %d flagged" % len(flagged))

    reg_known = registration is not None
    _assert_control(suite, "corpus", MUST_FLAG, MUST_NOT_FLAG, flagged,
                    all_subjects, registration_known=reg_known)

    # The same control for the SERVER-TEXT direction: a synthetic server whose
    # docstring + tool description carry dead routing text, scanned as if it
    # were registered so the verdicts must be FAIL.
    server_dir = os.path.join(FIXTURE_ROOT, "servers")
    write_server_fixture(server_dir)
    fake_srv = scan_server_text([FIXTURE_SERVER_NAME], {FIXTURE_SERVER_NAME},
                                repo_root=FIXTURE_ROOT, scripts_dir=server_dir)
    fake_srv_findings = checker.evaluate(fake_srv["prescriptions"])
    srv_flagged = {f.subject: f for f in fake_srv_findings
                   if f.severity == H.FAIL}
    srv_all = {f.subject: f for f in fake_srv_findings}
    suite.record(GH, "server fixture scanned", [], status=H.INFO,
                 detail=["file        : %s" % FIXTURE_SERVER_NAME,
                         "text lines  : %d"
                         % sum(n for _r, _o, n in fake_srv["files"]),
                         "extractions : %d" % len(fake_srv["prescriptions"]),
                         "flagged     : %s" % (", ".join(sorted(srv_flagged))
                                               or "-")],
                 brief="INFO | server fixture | %d flagged" % len(srv_flagged))
    _assert_control(suite, "server", SERVER_MUST_FLAG, SERVER_MUST_NOT_FLAG,
                    srv_flagged, srv_all, registration_known=reg_known)

    # The registered/unregistered split is the whole point of the server
    # direction: the SAME fixture scanned as an unregistered server must
    # produce INFO, not FAIL.
    inert = scan_server_text([FIXTURE_SERVER_NAME], set(),
                             repo_root=FIXTURE_ROOT, scripts_dir=server_dir)
    inert_findings = checker.evaluate(inert["prescriptions"])
    inert_fail = [f.subject for f in inert_findings if f.severity == H.FAIL]
    inert_info = {f.subject for f in inert_findings if f.severity == H.INFO}
    problems = []
    if inert_fail:
        problems.append("unregistered server text produced FAIL verdicts: %s"
                        % ", ".join(sorted(inert_fail)))
    for subject, _why, needs_reg in SERVER_MUST_FLAG:
        if needs_reg and not reg_known:
            continue
        if subject not in inert_info:
            problems.append("%r was not even reported as INFO for an "
                            "unregistered server" % subject)
    suite.record(GH, "unregistered server text is INFO, not FAIL", problems,
                 status=None if reg_known else SKIP,
                 detail=["groups      : %s"
                         % ", ".join(sorted({f.group for f in inert_findings})),
                         "info        : %s" % ", ".join(sorted(inert_info))]
                        + ([] if reg_known else
                           ["reason      : registration unknown, so the "
                            "registered/unregistered split cannot be judged"]),
                 brief="%s | inert server text"
                       % (SKIP if not reg_known else
                          (H.FAIL if problems else H.PASS)))

    # The same negative control for DIRECTION 3: synthetic agents whose grant
    # lists and bodies pin every verdict the group can reach -- one per
    # suppressor, so a suppressor that starts swallowing real defects fails here
    # instead of quietly turning group I green.
    fake_agents = collect_agents(fixture_root, repo_root=FIXTURE_ROOT)
    fake_d3 = evaluate_agents(checker, fake_agents, fake["prescriptions"])
    d3_flagged = sorted(f.subject for f in fake_d3["findings"]
                        if f.severity == H.FAIL)
    problems = []
    if not fake_agents:
        problems.append("no synthetic agent was collected from the fixture, so "
                        "the Direction 3 control below cannot fail")
    suite.record(GH, "d3 fixture scanned", problems,
                 status=None if problems else H.INFO,
                 detail=["agents      : %d (%s)"
                         % (len(fake_agents),
                            ", ".join(a.name for a in fake_agents)),
                         "findings    : %d" % len(fake_d3["findings"]),
                         "flagged     : %s" % (", ".join(d3_flagged) or "-")],
                 brief="INFO | d3 fixture | %d agents, %d flagged"
                       % (len(fake_agents), len(d3_flagged)))
    _assert_d3_control(suite, fake_d3["findings"], D3_EXPECT,
                       registration_known=reg_known)

    # Direction 2 must also work against an alternate root: the fixture
    # mentions `find_definition` but not, say, `create_temp_dir`.
    fake_unmentioned = checker.unmentioned(fake["text"])
    purity_missing = set(fake_unmentioned.get("mcp-purity.py", []))
    problems = []
    if "create_temp_dir" not in purity_missing:
        problems.append("D2 did not report create_temp_dir as unmentioned in "
                        "the fixture corpus")
    if "find_definition" in purity_missing:
        problems.append("D2 wrongly reported find_definition as unmentioned "
                        "although the fixture prescribes it")
    suite.record(GH, "D2 works on an alternate root", problems,
                 detail=["unmentioned : %d of mcp-purity.py's functions"
                         % len(purity_missing)],
                 brief="%s | D2 alternate root"
                       % (H.FAIL if problems else H.PASS))

    # -- B. repo + sandbox hygiene ----------------------------------------
    _record_hygiene(suite, pycache_before)
    _record_sandbox(suite, tmp_before)

    suite.print_summary()
    nskip = sum(1 for r in suite.results if r.status == SKIP)
    if nskip:
        print("\nNOTE: %d [SKIP] case(s) above are counted in the 'pass' column"
              " -- they are NOT failures and NOT verifications." % nskip)
    print("NOTE: group G is Direction 2 (INFO only, by design). Group F is "
          "server text that no model ever sees (unregistered servers), so it "
          "is INFO by design too. INFO rows in C/D/E are in-flight churn, "
          "near-misses or unverifiable-by-design; each carries its reason.")
    print("NOTE: group I is Direction 3. Its `d3_ungranted` INFO rows are "
          "orphan CAPABILITIES (3c, the tools-level analogue of group G), and "
          "its `d3_missing_grant` INFO rows are missing grants this suite "
          "refuses to fail on -- each names the suppressor that spared it "
          "(illustrative / negative / delegation) and carries file:line "
          "evidence. Both are decisions for a human, not noise. Its "
          "`d3_inert_key` rows are 3d: an `mcpServers:` key in a plugin agent "
          "is IGNORED at load time, so it FAILs; PASS means the key is absent.")
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
