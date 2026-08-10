#!/usr/bin/env python3
"""Suite for ClaudeCode/hooks/sbx-gate.py, the grant-only PreToolUse Bash gate.

This is the mirror of tests/test_mcp_first_guard.py with the polarity INVERTED,
plus two load-bearing security groups the deny-guard suite has no analogue for:
the mandatory cross-file PARITY test and the folded offline pure-builder
containment group (Step 9 of the feature plan is folded into THIS file -- there
is no separate tests/test_sbx.py, no run_sbx wrapper, no sbx forge target).

The case COUNT is deliberately absent from this docstring: it is written down
once, in the SUITES table in tests/run.py, which checks it against the run. A
second copy here would be a number nobody verifies.

Polarity (the inversion of the deny-guard's classifier):
  * empty stdout            == PROMPT      (the SAFE outcome for a grant-only gate)
  * permissionDecision=allow == AUTOALLOW  (the ONLY positive emission)
  * anything else            == a test FAILURE (BADJSON / BADSHAPE / OTHER)
The invariants are preserved verbatim from the mirror: every subprocess case
asserts rc == 0 and empty stderr (test_mcp_first_guard.py:1002-1005).

Why the AUTOALLOW cases are white-box, not end-to-end subprocess: the gate
authorizes wrapper IDENTITY -- toks[0] must canonicalize to WRAPPER_PATH, the
DEPLOYED ~/.claude/skills/p/skills/sandbox-run/scripts/sbx, which does NOT exist
in CI. So a subprocess AUTOALLOW can never fire here. The AUTOALLOW cases (and
the flag-parse PROMPT cases R11/R12/R12b/R12c, which are only reachable once the
identity check PASSES) import is_clean_sbx via the harness's gated bytecode-free
loader, monkeypatch WRAPPER_PATH to a temp fixture, and invoke that same fixture
path so identity matches. The PROMPT-by-metacharacter and fail-safe plumbing
cases stay end-to-end subprocess -- they reject regardless of WRAPPER_PATH, so
they need no fixture.

Coverage by group:
  A-L  the adversarial matrix (§5): every containment-escape class R1-R13
       (+R6b/R10b/R12b/R12c) as at least one PROMPT case
  M    R11/R12/R12b/R12c flag-parse PROMPTs, white-box (identity must pass first)
  N    the minimal AUTOALLOW set, white-box, exact-reason asserted
  O    R13 fail-safe plumbing: non-Bash, null/missing command, missing cwd,
       malformed JSON -- each PROMPT + rc 0 + empty stderr
  P    the MANDATORY cross-file PARITY test (H-A / NFR-8): the gate's copy of
       resolve_scope/project_root (loaded via load_module_from_path) and the
       helper's copy (loaded via SourceFileLoader -- the helper is extension-
       less, so spec_from_file_location returns a None loader and would crash)
       must produce BYTE-IDENTICAL output; this is the enforcement that the
       deliberately-duplicated resolution contract cannot drift into a
       containment escape
  Q    the FOLDED offline pure-builder containment group (Step 9): per backend,
       secret_paths denied read+write (incl. the sec-MED-2 ancestor-.git walk),
       the bwrap mask primitive selected by each entry's is_dir BIT rather than
       by its name, the READ-ONLY carve-out that reopens ~/.claude/skills and
       ~/.claude/scripts (order asserted by INDEX on both backends, no write ever
       granted, everything else under ~/.claude still shut), the WRITE-ONLY
       shadow deny that closes NAME DIVERGENCE (a ~/.claude symlink whose target
       leaves ~/.claude gives the same inode a second spelling that the
       spelling-keyed deny never matched -- measured WRITABLE under --write .),
       the bwrap --unshare-pid + --proc /proc that stops /proc/<pid>/root from
       walking around every mask, network unshared/denied unless --net AND --net
       actually emitting (allow network*) instead of relying on a missing deny
       under (deny default), writes confined + --ro zeroed, canonicalize
       injection reject, setrlimit-before-execvp ordering, and the M3 proof that
       importing the helper runs no exec/setrlimit/makedirs

Usage:
  python3 tests/test_sbx_gate.py
  python3 tests/test_sbx_gate.py --brief
  python3 tests/test_sbx_gate.py --keep
Exit code 0 iff every case passes.
"""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "sbx_gate"
HOOK = H.repo_path("ClaudeCode", "hooks", "sbx-gate.py")
HELPER = H.repo_path("ClaudeCode", "skills", "sandbox-run", "scripts", "sbx")
REPO = H.REPO_ROOT

PROMPT, AUTOALLOW, INFO = "PROMPT", "AUTOALLOW", "INFO"

# The gate's one AUTOALLOW reason string -- pinned exactly (the only()-style check).
REASON = "clean single sbx invocation, scope inside project, no --net"

# The wrapper prefixes the gate refuses to peel (R6). This is a MANUAL copy of the
# guard's own SKIP_WRAPPERS set (mcp-first-guard.py:134); it is NOT imported, so it
# must be kept in sync by hand if that set changes (currently identical, 9 members).
SKIP_WRAPPERS = ["sudo", "command", "env", "nice", "nohup", "time",
                 "builtin", "exec", "xargs"]

GRP_A = "A. R1 chaining (; && || & newline)"
GRP_B = "B. R2 pipe"
GRP_C = "C. R3 substitution ($(...) `...` <(...))"
GRP_D = "D. R4 heredoc"
GRP_E = "E. R5 redirection (> <)"
GRP_F = "F. R6 wrappers (SKIP_WRAPPERS)"
GRP_G = "G. R6b look-alike named sbx (C1/KD-6)"
GRP_H = "H. R7 assignment prefix"
GRP_I = "I. R8 subshell / brace group"
GRP_J = "J. R9 bash -c payload"
GRP_K = "K. R10 ALL-CAPS"
GRP_L = "L. R10b quoted metacharacter"
GRP_M = "M. R11/R12 flag-parse PROMPT (whitebox)"
GRP_N = "N. AUTOALLOW (whitebox, identity via WRAPPER_PATH)"
GRP_O = "O. R13 plumbing / fail-safe"
GRP_P = "P. PARITY (H-A: gate vs helper resolve_scope/project_root)"
GRP_Q = "Q. OFFLINE containment (folded Step 9)"


def case(group, name, cmd, expect, cwd=REPO, must=(), must_not=(), note=""):
    """A subprocess case. `cwd` is injected into the payload (the gate reads it);
    pass cwd=None to OMIT it (the missing-cwd fail-safe probe)."""
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    if cwd is not None:
        payload["cwd"] = cwd
    return {"group": group, "name": name, "expect": expect,
            "payload": json.dumps(payload), "must": list(must),
            "must_not": list(must_not), "note": note}


def raw(group, name, payload, expect, note=""):
    """A subprocess case with a hand-built (possibly malformed) payload string."""
    return {"group": group, "name": name, "expect": expect, "payload": payload,
            "must": [], "must_not": [], "note": note}


# ---------------------------------------------------------------------------
# Subprocess adversarial matrix: every row is PROMPT (empty stdout). Each case
# carries a valid cwd so the rejection is genuinely the metacharacter / identity
# rule under test, not the missing-cwd fail-safe (which group O covers).
# ---------------------------------------------------------------------------

CASES = []

# R1 -- shell chaining. The blocklist carries ; & | newline.
CASES += [
    case(GRP_A, "semicolon chain", "sbx -- echo hi ; rm -rf ~", PROMPT),
    case(GRP_A, "&& chain", "sbx -- echo hi && rm -rf ~", PROMPT),
    case(GRP_A, "|| chain", "sbx -- echo hi || rm -rf ~", PROMPT),
    case(GRP_A, "bare & chain", "sbx -- echo hi & rm -rf ~", PROMPT),
    case(GRP_A, "newline chain", "sbx -- echo hi\nrm -rf ~", PROMPT),
]

# R2 -- pipe.
CASES += [
    case(GRP_B, "pipe into sh", "sbx -- cat f | sh", PROMPT),
]

# R3 -- command / process substitution.
CASES += [
    case(GRP_C, "$( ) substitution", "sbx -- echo $(id)", PROMPT),
    case(GRP_C, "backtick substitution", "sbx -- echo `id`", PROMPT),
    case(GRP_C, "process substitution <( )", "sbx -- diff <(id) f", PROMPT),
]

# R4 -- heredoc hiding a body (both < and newline are blocklisted).
CASES += [
    case(GRP_D, "heredoc body", "sbx -- cat <<EOF\nrm -rf ~\nEOF", PROMPT),
]

# R5 -- redirection. NOTE: `~` is NOT a metacharacter (L1); these reject via >/<.
CASES += [
    case(GRP_E, "output redirect >", "sbx -- echo x > ~/.bashrc", PROMPT,
         note="~ is NOT a metachar (L1); rejected by `>`"),
    case(GRP_E, "input redirect <", "sbx -- cat < ~/.ssh/id_rsa", PROMPT,
         note="rejected by `<`, not by `~`"),
]

# R6 -- one wrapper per SKIP_WRAPPERS member. No metachar: rejected at IDENTITY
# (toks[0] is the wrapper name, which never canonicalizes to WRAPPER_PATH).
for _w in SKIP_WRAPPERS:
    CASES.append(case(GRP_F, "%s sbx ..." % _w, "%s sbx -- echo hi" % _w, PROMPT,
                      note="no peeling: toks[0]=%r != WRAPPER_PATH" % _w))

# R6b -- planted look-alike NAMED sbx: a path form resolves != WRAPPER_PATH
# (identity, never basename). The PATH-shadowed bare form is white-box (group G).
CASES += [
    case(GRP_G, "./sbx (planted relative)", "./sbx -- echo hi", PROMPT,
         note="resolve_scope(./sbx, cwd) = cwd/sbx != WRAPPER_PATH (C1)"),
    case(GRP_G, "/tmp/x/sbx (planted absolute)", "/tmp/x/sbx -- echo hi", PROMPT,
         note="realpath(/tmp/x/sbx) != WRAPPER_PATH (C1)"),
]

# R7 -- leading assignment prefix. toks[0] is the assignment, not the wrapper.
CASES += [
    case(GRP_H, "PATH=/evil sbx ...", "PATH=/evil sbx -- echo hi", PROMPT,
         note="toks[0]=PATH=/evil -> resolves nowhere near WRAPPER_PATH"),
    case(GRP_H, "SBX_X=1 sbx ...", "SBX_X=1 sbx -- echo hi", PROMPT),
]

# R8 -- subshell / brace group (parens, braces, ; all blocklisted).
CASES += [
    case(GRP_I, "subshell (sbx ...)", "(sbx -- echo hi)", PROMPT),
    case(GRP_I, "brace group { sbx ...; }", "{ sbx -- echo hi; }", PROMPT),
]

# R9 -- bash -c payload (quote chars blocklisted; toks[0] is bash anyway).
CASES += [
    case(GRP_J, "bash -c 'sbx ...'", "bash -c 'sbx -- echo hi'", PROMPT),
]

# R10 -- ALL-CAPS. The chained form is unconditionally PROMPT via `;` (a bare
# SBX would be identity-decided and is environment-dependent, so it is not
# asserted as a fixed outcome here).
CASES += [
    case(GRP_K, "SBX ... ; id", "SBX -- echo hi ; id", PROMPT,
         note="rejected by `;` regardless of how SBX resolves"),
]

# R10b -- a metacharacter inside a quoted argument. PROMPT is the SAFE outcome,
# NOT a bug (GRAFT 1 dropped the AUTOALLOW-echo-';' requirement).
CASES += [
    case(GRP_L, 'sbx -- echo ";" (quoted metachar)', 'sbx -- echo ";"', PROMPT,
         note="intentionally-safe under-allow: `;` and `\"` both reject"),
]

# R13 -- fail-safe plumbing.
CASES += [
    raw(GRP_O, "non-Bash tool",
        json.dumps({"tool_name": "Read",
                    "tool_input": {"command": "sbx -- echo hi"}, "cwd": REPO}),
        PROMPT, note="tool_name != Bash -> self-gate returns (silence)"),
    raw(GRP_O, "command null",
        json.dumps({"tool_name": "Bash",
                    "tool_input": {"command": None}, "cwd": REPO}), PROMPT),
    raw(GRP_O, "tool_input missing",
        json.dumps({"tool_name": "Bash", "cwd": REPO}), PROMPT),
    raw(GRP_O, "command key missing",
        json.dumps({"tool_name": "Bash", "tool_input": {}, "cwd": REPO}), PROMPT),
    raw(GRP_O, "cwd missing -> cannot resolve scope",
        json.dumps({"tool_name": "Bash",
                    "tool_input": {"command": "sbx -- echo hi"}}), PROMPT,
        note="cwd is None -> bare return before is_clean_sbx (fail-safe)"),
    raw(GRP_O, "malformed JSON (fail-open == prompt)", "{not json at all", PROMPT,
        note="json.loads raises -> try/except: pass -> exit 0, empty stdout"),
    case(GRP_O, "empty command", "", PROMPT),
    case(GRP_O, "whitespace-only command", "   \t  ", PROMPT),
    case(GRP_O, "comment line", "# sbx -- echo hi", PROMPT,
         note="s[0]=='#' -> reject before the metachar scan"),
]


# ---------------------------------------------------------------------------
# execution helpers
# ---------------------------------------------------------------------------

def run_hook(payload):
    return H.run_process([sys.executable, HOOK], stdin_text=payload, timeout=20)


def classify(stdout):
    """INVERTED classifier: empty stdout is PROMPT (safe); allow is AUTOALLOW."""
    s = stdout.strip()
    if not s:
        return PROMPT, ""
    try:
        obj = json.loads(s)
    except Exception:
        return "BADJSON", s
    if not isinstance(obj, dict):
        return "BADJSON", s
    hso = obj.get("hookSpecificOutput") or {}
    if hso.get("permissionDecision") == "allow":
        if hso.get("hookEventName") != "PreToolUse":
            return "BADSHAPE", s
        if not isinstance(hso.get("permissionDecisionReason"), str):
            return "BADSHAPE", s
        return AUTOALLOW, hso.get("permissionDecisionReason") or ""
    return "OTHER", s


def _load_gate():
    """Import the GATE copy: it has a .py extension, so the harness's
    spec_from_file_location loader (load_module_from_path) works (H3)."""
    return H.load_module_from_path("sbx_gate_wb", HOOK)


def _load_helper(name):
    """Import the HELPER copy via an EXPLICIT SourceFileLoader (H3): the helper
    is extension-less, so spec_from_file_location returns a None loader and
    load_module_from_path would crash. Bytecode-free (mirror _harness:397-398)."""
    import importlib.machinery
    import importlib.util
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader = importlib.machinery.SourceFileLoader(name, HELPER)
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        # Register BEFORE exec: the helper's @dataclass resolves annotations via
        # sys.modules[cls.__module__] (Python 3.12+), which is None if unregistered.
        sys.modules[name] = module
        loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = prev


def _clean(gate, cmd, cwd, wrapper_path):
    """Call is_clean_sbx with WRAPPER_PATH monkeypatched to a test fixture, then
    restore it. Keeps the identity surface out of the shipped gate (no prod env
    override)."""
    prev = gate.WRAPPER_PATH
    gate.WRAPPER_PATH = wrapper_path
    try:
        return gate.is_clean_sbx(cmd, cwd)
    finally:
        gate.WRAPPER_PATH = prev


def _rec(suite, group, name, problems, note="", extra=()):
    detail = list(extra)
    if note:
        detail.append("note        : " + note)
    status = H.FAIL if problems else H.PASS
    brief = "%s | %s%s" % (status, name,
                           (" | " + "; ".join(problems)) if problems else "")
    suite.record(group, name, problems, status=status, detail=detail,
                 brief=brief, text="; ".join(problems))


# ---------------------------------------------------------------------------
# White-box groups M / N + the R6b PATH-shadow case (identity must pass first,
# so these call is_clean_sbx directly with WRAPPER_PATH pointed at a fixture).
# ---------------------------------------------------------------------------

def _run_whitebox(suite, gate, ws):
    wrapdir = ws.subdir("wrap")
    wrapper = os.path.join(wrapdir, "sbx")
    with open(wrapper, "w") as fh:
        fh.write("#!/bin/sh\n")
    wrapper_real = os.path.realpath(wrapper)

    repo = ws.subdir("proj")
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    repo_real = os.path.realpath(repo)
    sibling = "../%s-evil" % os.path.basename(repo_real)

    # -- N: the minimal AUTOALLOW set, exact reason asserted --------------
    autoallow = [
        ("<WRAPPER> -- echo hi (default scratch)",
         "%s -- echo hi" % wrapper, repo_real),
        ("<WRAPPER> --write . -- touch f",
         "%s --write . -- touch f" % wrapper, repo_real),
        ("<WRAPPER> --ro -- true",
         "%s --ro -- true" % wrapper, repo_real),
        # --dry-run runs NOTHING (prints the plan, exits 0, no child, no scratch
        # dir), so it grants strictly less than the plain `<WRAPPER> -- <cmd>` form
        # already allowed above -- and it is the one auto-allow path that completes
        # inside Claude Code's own command sandbox (a real run needs a nested
        # sandbox-exec, which the harness sandbox refuses).
        ("<WRAPPER> --dry-run -- echo hi",
         "%s --dry-run -- echo hi" % wrapper, repo_real),
        ("<WRAPPER> --dry-run --write . -- touch f (combines with a scope)",
         "%s --dry-run --write . -- touch f" % wrapper, repo_real),
    ]
    for name, cmd, cwd in autoallow:
        ok, reason = _clean(gate, cmd, cwd, wrapper_real)
        problems = []
        if not ok:
            problems.append("expected AUTOALLOW, got PROMPT (reason=%r)" % reason)
        elif reason != REASON:
            problems.append("reason %r != %r" % (reason, REASON))
        _rec(suite, GRP_N, name, problems,
             extra=["cmd         : %r" % cmd,
                    "verdict     : ok=%s reason=%r" % (ok, reason)])

    # -- M: identity passes, but a flag rule rejects (empty-reason PROMPT) --
    flag_prompt = [
        ("R11 --net -- curl http://x", "%s --net -- curl http://x" % wrapper,
         repo_real),
        ("R12 --write /etc (outside project)",
         "%s --write /etc -- touch x" % wrapper, repo_real),
        ("R12/H1 --write ~ (-> $HOME, outside)",
         "%s --write ~ -- touch f" % wrapper, repo_real),
        ("R12b --write=/etc (equals-form, M-B)",
         "%s --write=/etc -- touch x" % wrapper, repo_real),
        ("R12b --frobnicate (unknown flag, M-B)",
         "%s --frobnicate -- x" % wrapper, repo_real),
        ("R12c --write ../<root>-evil (sibling, sec-MED-1)",
         "%s --write %s -- touch f" % (wrapper, sibling), repo_real),
        # --dry-run is recognized ONLY as the exact bare token. The equals-form is
        # NOT: argparse's store_true ERRORS on `--dry-run=1`, so recognizing it here
        # would make the gate MORE permissive than the helper it authorizes -- the
        # M-B false-allow class. This case must FAIL if anyone ever loosens the parse
        # to a `tok.startswith("--dry-run")` check.
        ("R12b --dry-run=1 (equals-form of a store_true, M-B)",
         "%s --dry-run=1 -- echo hi" % wrapper, repo_real),
        # --dry-run must NOT rescue a --net invocation: --net is refused first (R11).
        ("R11 --dry-run --net (dry-run does not rescue --net)",
         "%s --dry-run --net -- curl x" % wrapper, repo_real),
    ]
    for name, cmd, cwd in flag_prompt:
        ok, reason = _clean(gate, cmd, cwd, wrapper_real)
        problems = []
        if ok:
            problems.append("expected PROMPT, got AUTOALLOW (reason=%r)" % reason)
        if reason != "":
            problems.append("PROMPT must carry empty reason, got %r" % reason)
        _rec(suite, GRP_M, name, problems, extra=["cmd         : %r" % cmd])

    # -- R6b: a bare `sbx` shadowed on PATH resolves != WRAPPER_PATH -------
    shadowdir = ws.subdir("shadow")
    shadow = os.path.join(shadowdir, "sbx")
    with open(shadow, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(shadow, 0o755)
    prev_path = os.environ.get("PATH", "")
    os.environ["PATH"] = shadowdir + os.pathsep + prev_path
    try:
        ok, reason = _clean(gate, "sbx -- echo hi", repo_real, wrapper_real)
    finally:
        os.environ["PATH"] = prev_path
    problems = []
    if ok:
        problems.append("PATH-shadowed `sbx` false-allowed (basename != identity)")
    _rec(suite, GRP_G, "bare `sbx` shadowed on PATH -> resolves != WRAPPER_PATH",
         problems,
         note="shutil.which finds the planted sbx; realpath != WRAPPER_PATH (C1)")


# ---------------------------------------------------------------------------
# P: the MANDATORY cross-file parity test. The gate's and helper's duplicated
# resolve_scope/project_root MUST agree byte-for-byte, and the separator-safe
# containment verdict must agree -- else the gate/helper resolution has diverged
# (a containment escape). This test is the enforcement; do NOT weaken it.
# ---------------------------------------------------------------------------

def _run_parity(suite, gate, helper, ws):
    repo = ws.subdir("parity_repo")
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    repo_real = os.path.realpath(repo)
    nogit_real = os.path.realpath(ws.subdir("parity_nogit"))

    linkcwd = ws.subdir("linkcwd")
    target = ws.subdir("linktarget")
    os.symlink(target, os.path.join(linkcwd, "lnk"))

    for dir_arg in ("~", ".", "/etc", "../sibling"):
        g = gate.resolve_scope(dir_arg, REPO)
        h = helper.resolve_scope(dir_arg, REPO)
        problems = [] if g == h else ["gate=%r != helper=%r" % (g, h)]
        _rec(suite, GRP_P, "resolve_scope(%r) byte-identical" % dir_arg, problems,
             extra=["value       : %r" % g])

    g = gate.resolve_scope("lnk", linkcwd)
    h = helper.resolve_scope("lnk", linkcwd)
    tgt = os.path.realpath(target)
    problems = []
    if g != h:
        problems.append("gate=%r != helper=%r" % (g, h))
    if g != tgt:
        problems.append("symlink not realpath'd to target: %r != %r" % (g, tgt))
    _rec(suite, GRP_P, "resolve_scope(symlink) identical + realpath'd", problems,
         extra=["value       : %r" % g])

    for label, cwd in (("(.git tree)", repo_real), ("(no .git)", nogit_real)):
        g = gate.project_root(cwd)
        h = helper.project_root(cwd)
        problems = [] if g == h else ["gate=%r != helper=%r" % (g, h)]
        _rec(suite, GRP_P, "project_root %s byte-identical" % label, problems,
             extra=["value       : %r" % g])

    root = helper.project_root(repo_real)
    sibling = "../%s-evil" % os.path.basename(repo_real)
    for dir_arg, want in ((".", True), (sibling, False)):
        gv = gate._write_contained(dir_arg, repo_real, root)
        hv = helper.inside_project(helper.resolve_scope(dir_arg, repo_real), root)
        problems = []
        if gv != hv:
            problems.append("gate verdict %s != helper verdict %s" % (gv, hv))
        if gv != want:
            problems.append("verdict %s != expected %s" % (gv, want))
        _rec(suite, GRP_P, "containment(%r) agrees == %s (sep-safe)"
             % (dir_arg, want), problems)


# ---------------------------------------------------------------------------
# Q: the folded offline pure-builder containment group (Step 9). Pure string
# work over constructed Scope objects -- runs on any OS.
# ---------------------------------------------------------------------------

def _run_offline(suite, helper, ws):
    import io
    import inspect
    import resource as _res

    # --- controlled HOME fixture (existence-filter regression guard) ----------
    # secret_paths is EXISTENCE-FILTERED: it returns only home-anchored secrets and
    # .git/config entries that actually EXIST at call time. A LIVE Linux run proved
    # why -- bwrap masks each secret with a mountpoint it must mkdir under the
    # read-only `--ro-bind / /` root, and an ABSENT secret (e.g. ~/.aws) makes it
    # die "Can't mkdir <p>: Read-only file system" BEFORE exec, sbx dead-on-arrival.
    # To pin that class DETERMINISTICALLY (and NOT depend on the CI host's real
    # $HOME, whose nondeterministic contents are exactly what let the bug slip past
    # this suite), monkeypatch HOME to a fixture holding only SOME of the five
    # secrets: ~/.ssh and ~/.claude present (dirs), ~/.aws/.gnupg/.config/gh absent.
    home = ws.subdir("home")
    os.makedirs(os.path.join(home, ".ssh"), exist_ok=True)
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    present = (os.path.join(home, ".ssh"), os.path.join(home, ".claude"))
    absent = (os.path.join(home, ".aws"), os.path.join(home, ".gnupg"),
              os.path.join(home, ".config", "gh"))

    # --- read-only carve-out fixture, under the SAME controlled HOME ----------
    # ~/.claude is a SECRET (credentials, transcripts, and the settings/hooks that
    # gate this very tool), so the deny set hides all of it -- which also hid this
    # repo's own tooling living under it (~/.claude/scripts, ~/.claude/skills/**).
    # carveout_paths() opens exactly those two subtrees for READ. The fixture holds
    # both carve-outs AND two siblings that must stay shut, so the negative case is
    # a real sibling of the positive one rather than a hypothetical.
    claude = os.path.join(home, ".claude")
    carve_skills = os.path.join(claude, "skills")
    carve_scripts = os.path.join(claude, "scripts")
    os.makedirs(carve_skills, exist_ok=True)
    os.makedirs(carve_scripts, exist_ok=True)
    os.makedirs(os.path.join(claude, "projects"), exist_ok=True)
    open(os.path.join(claude, "settings.json"), "w").close()
    not_carved = (os.path.join(claude, "projects"),
                  os.path.join(claude, "settings.json"))

    # a SECOND home holding only ONE of the two carve-outs: the existence filter
    # must drop the absent one (same bwrap "Can't mkdir <p>: Read-only file system"
    # class the secret filter already guards -- a carve-out is a mountpoint too).
    home_nc = ws.subdir("home_nocarve")
    os.makedirs(os.path.join(home_nc, ".claude", "skills"), exist_ok=True)

    # --- NAME-DIVERGENCE fixture: the symlink farm under the controlled HOME ------
    # Measured on the dev host: ~/.claude/hooks is a SYMLINK to <repo>/ClaudeCode/
    # hooks and ~/.claude/skills/p is a SYMLINK to <repo>/ClaudeCode. Seatbelt subpath
    # rules and bwrap mounts key on the path SPELLING, not the inode, so the ~/.claude
    # deny never covered the repo spelling of the SAME directory -- and under
    # `--write .` that spelling was WRITABLE. .claude/tmp/write-probe.py wrote a marker
    # into <repo>/ClaudeCode/hooks/ and opened the live sbx-gate.py for append while
    # the ~/.claude/hooks/ spelling was refused: a sandboxed command could rewrite its
    # own PreToolUse gate. The fixture reproduces all three shapes at once -- a
    # divergent link directly under ~/.claude, a divergent link under a CARVE-OUT (the
    # deliberately-opened subtree, hence exactly where a link out is dangerous), and a
    # link that stays INSIDE ~/.claude and must therefore be left alone.
    plugin = ws.subdir("plugin")
    plugin_hooks = os.path.join(plugin, "hooks")
    os.makedirs(plugin_hooks, exist_ok=True)
    os.symlink(plugin_hooks, os.path.join(claude, "hooks"))      # direct child
    os.symlink(plugin, os.path.join(carve_skills, "p"))          # carve-out child
    inside_link = os.path.join(claude, "inside-link")
    os.symlink(os.path.join(claude, "projects"), inside_link)    # stays inside
    shadow_want = (os.path.realpath(plugin), os.path.realpath(plugin_hooks))
    shadow_never = os.path.realpath(os.path.join(claude, "projects"))

    plain = ws.subdir("plain")

    # nested-.git tree WITH real config FILES -- the filter now requires the config
    # FILE (not merely the .git dir) to exist for the entry to be masked (sec-MED-2).
    nroot = ws.subdir("nest")
    os.makedirs(os.path.join(nroot, ".git"), exist_ok=True)
    nsub = os.path.join(nroot, "sub")
    os.makedirs(os.path.join(nsub, ".git"), exist_ok=True)
    open(os.path.join(nroot, ".git", "config"), "w").close()
    open(os.path.join(nsub, ".git", "config"), "w").close()
    cfg_sub = os.path.join(os.path.realpath(nsub), ".git", "config")
    cfg_root = os.path.join(os.path.realpath(nroot), ".git", "config")

    # a .git DIR with NO config FILE -> its .git/config must be filtered OUT.
    nocfg = ws.subdir("nocfg")
    os.makedirs(os.path.join(nocfg, ".git"), exist_ok=True)
    nocfg_config = os.path.join(os.path.realpath(nocfg), ".git", "config")

    prev_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        secrets = helper.secret_paths(plain)
        secrets_n = helper.secret_paths(nsub)
        secrets_nocfg = helper.secret_paths(nocfg)
        carveouts = helper.carveout_paths()
        shadow = helper.shadow_write_denies()
        os.environ["HOME"] = home_nc
        carveouts_nc = helper.carveout_paths()
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home

    # secret_paths returns (path, is_dir) PAIRS; membership assertions below work on
    # the path half, while the bwrap primitive assertions consume the bit itself.
    secret_names = [p for p, _d in secrets]
    secret_names_n = [p for p, _d in secrets_n]
    secret_names_nocfg = [p for p, _d in secrets_nocfg]

    writes = helper.compute_writes([], False, plain)
    scratch = helper.scratch_scope(plain)
    scope = helper.Scope(writes=writes, net=False, ro=False,
                         argv=["echo", "hi"], secret_paths=secrets,
                         carveouts=carveouts, shadow_write_denies=shadow)
    scope_net = helper.Scope(writes=writes, net=True, ro=False,
                             argv=["echo", "hi"], secret_paths=secrets,
                             carveouts=carveouts, shadow_write_denies=shadow)
    scope_ro = helper.Scope(writes=helper.compute_writes([], True, plain),
                            net=False, ro=True, argv=["echo", "hi"],
                            secret_paths=secrets, carveouts=carveouts,
                            shadow_write_denies=shadow)

    profile = helper._seatbelt_argv(scope)[2].split("\n")
    seat_net = helper._seatbelt_argv(scope_net)[2].split("\n")
    seat_ro = helper._seatbelt_argv(scope_ro)[2].split("\n")
    bw = helper._bwrap_argv(scope)
    bw_net = helper._bwrap_argv(scope_net)
    bw_ro = helper._bwrap_argv(scope_ro)

    read_allow = '(allow file-read* (subpath "/"))'
    ra_idx = profile.index(read_allow)

    # (a) Seatbelt: every secret denied read+write, AFTER the permissive read-allow.
    problems = []
    for s in secret_names:
        dr = '(deny file-read* (subpath "%s"))' % s
        dw = '(deny file-write* (subpath "%s"))' % s
        if dr not in profile:
            problems.append("missing %s" % dr)
        elif profile.index(dr) <= ra_idx:
            problems.append("read-deny for %s not after read-allow" % s)
        if dw not in profile:
            problems.append("missing %s" % dw)
        elif profile.index(dw) <= ra_idx:
            problems.append("write-deny for %s not after read-allow" % s)
    _rec(suite, GRP_Q, "(a) Seatbelt: secrets denied read+write, after read-allow",
         problems)

    # (a) existence filter (Linux dead-on-arrival REGRESSION GUARD): the EXISTING
    # home secrets are masked; the ABSENT ones (~/.aws, ~/.gnupg, ~/.config/gh) are
    # NOT in the set. This case FAILS on the pre-fix code, which returned all five
    # unconditionally -- and an absent ~/.aws made bwrap die "Can't mkdir
    # <home>/.aws: Read-only file system" before exec, killing every Linux run.
    problems = []
    for p in present:
        if p not in secret_names:
            problems.append("existing home secret %s missing from set" % p)
    for p in absent:
        if p in secret_names:
            problems.append("ABSENT secret %s wrongly masked "
                            "(filter broken -> Linux dead-on-arrival)" % p)
    _rec(suite, GRP_Q,
         "(a) existence filter: present home secrets masked, absent NOT (regression)",
         problems)

    # (a) sec-MED-2: a nested-.git tree lands BOTH .git/config paths in the set.
    problems = []
    if cfg_sub not in secret_names_n:
        problems.append("missing sub .git/config: %s" % cfg_sub)
    if cfg_root not in secret_names_n:
        problems.append("missing ancestor .git/config: %s (sec-MED-2)" % cfg_root)
    _rec(suite, GRP_Q, "(a) nested-.git: BOTH .git/config in secret set (sec-MED-2)",
         problems)

    # (a) existence filter: a .git DIR with no config FILE contributes nothing --
    # masking a nonexistent .git/config would trip the same bwrap mkdir-under-a-
    # read-only-root failure the home filter guards against. FAILS on the pre-fix
    # code, which appended <d>/.git/config whenever <d>/.git existed, config or not.
    problems = []
    if nocfg_config in secret_names_nocfg:
        problems.append(".git-without-config %s wrongly in secret set" % nocfg_config)
    _rec(suite, GRP_Q,
         "(a) existence filter: .git dir without config file NOT in secret set",
         problems)

    # (a) bwrap: each secret masked by the CORRECT primitive PER ENTRY, AFTER the
    # binds. The primitive is selected by the entry's own `is_dir` BIT (resolved in
    # secret_paths, which already stats the path) -- a DIRECTORY secret gets --tmpfs
    # <p>; a regular-FILE secret (the .git/config entries) gets --ro-bind /dev/null
    # <p>, NOT --tmpfs, which on a file makes bwrap die() with ENOTDIR before exec.
    # Asserting the primitive PER ENTRY is what makes this test FAIL on the old
    # --tmpfs-for-files code and PASS on the fix; driving the expectation off the BIT
    # (not off a path suffix) is what keeps it honest now that the builder does too.
    last_bind = max(i for i, t in enumerate(bw) if t == "--bind")
    problems = []
    for s, is_dir in secrets:
        if is_dir:                               # DIRECTORY -> tmpfs mountpoint
            found = [i for i in range(len(bw) - 1)
                     if bw[i] == "--tmpfs" and bw[i + 1] == s]
            if not found:
                problems.append("no --tmpfs mask for dir secret %s" % s)
            elif found[0] <= last_bind:
                problems.append("mask for %s not after binds" % s)
        else:                                    # regular FILE -> ro-bind /dev/null
            found = [i for i in range(len(bw) - 2)
                     if bw[i] == "--ro-bind" and bw[i + 1] == "/dev/null"
                     and bw[i + 2] == s]
            if not found:
                problems.append("no --ro-bind /dev/null mask for file secret %s" % s)
            elif found[0] <= last_bind:
                problems.append("mask for %s not after binds" % s)
            if any(bw[i] == "--tmpfs" and bw[i + 1] == s
                   for i in range(len(bw) - 1)):
                problems.append("file secret %s wrongly masked with --tmpfs" % s)
    _rec(suite, GRP_Q,
         "(a) bwrap: file secrets ro-bind /dev/null, dir secrets --tmpfs, after binds",
         problems)

    # (a) the primitive follows the BIT, not the NAME. The old builder recognized a
    # file by `path.endswith(<sep>.git<sep>config)`; that heuristic is GONE, and this
    # case is what stops anyone reinstating it "as a fallback". Two synthetic entries
    # invert the name/structure correlation the real fixtures happen to have: a
    # file-shaped secret NOT named .git/config must still get --ro-bind /dev/null,
    # and a directory-shaped one that IS named .git/config must still get --tmpfs.
    # The builder is pure, so neither path needs to exist on disk.
    odd_file = os.path.join(plain, "creds.txt")
    odd_dir = os.path.join(plain, "weird", ".git", "config")
    bw_odd = helper._bwrap_argv(helper.Scope(
        writes=[], net=False, ro=True, argv=["true"],
        secret_paths=((odd_file, False), (odd_dir, True)), carveouts=()))
    problems = []
    if not any(bw_odd[i] == "--ro-bind" and bw_odd[i + 1] == "/dev/null"
               and bw_odd[i + 2] == odd_file for i in range(len(bw_odd) - 2)):
        problems.append("is_dir=False secret %s not masked with --ro-bind /dev/null "
                        "(name heuristic reinstated?)" % odd_file)
    if not any(bw_odd[i] == "--tmpfs" and bw_odd[i + 1] == odd_dir
               for i in range(len(bw_odd) - 1)):
        problems.append("is_dir=True secret %s not masked with --tmpfs -- the "
                        "endswith('.git/config') heuristic is back" % odd_dir)
    _rec(suite, GRP_Q,
         "(a) bwrap: mask primitive follows the is_dir BIT, not the path name",
         problems)

    # (a) bwrap nested-.git: BOTH configs masked.
    scope_n = helper.Scope(writes=helper.compute_writes([], False, nsub),
                           net=False, ro=False, argv=["true"],
                           secret_paths=secrets_n, carveouts=carveouts)
    bw_n = helper._bwrap_argv(scope_n)
    problems = []
    for cfg in (cfg_sub, cfg_root):
        if not any(bw_n[i] == "--ro-bind" and bw_n[i + 1] == "/dev/null"
                   and bw_n[i + 2] == cfg
                   for i in range(len(bw_n) - 2)):
            problems.append("no --ro-bind /dev/null mask for %s" % cfg)
        if any(bw_n[i] == "--tmpfs" and bw_n[i + 1] == cfg
               for i in range(len(bw_n) - 1)):
            problems.append("%s wrongly masked with --tmpfs (ENOTDIR pre-exec)" % cfg)
    _rec(suite, GRP_Q,
         "(a) bwrap nested-.git: BOTH .git/config masked with ro-bind /dev/null",
         problems)

    # --- (a2) the read-only carve-out: ~/.claude stays a WRITE-denied secret, but
    # the two named tooling subtrees under it become READABLE. Every case below
    # asserts ORDER by INDEX, not mere presence: on both backends a carve-out
    # emitted before the mask it carves out of is a DEAD rule that would leave the
    # measured "Operation not permitted" bug in place while the test went green.

    # (a2) Seatbelt: the carve-out read-allow exists AND lands after the ~/.claude
    # read-deny (last-match-wins).
    claude_deny = '(deny file-read* (subpath "%s"))' % claude
    problems = []
    for want in (carve_skills, carve_scripts):
        if want not in carveouts:
            problems.append("carveout_paths() omitted %s" % want)
    if claude_deny not in profile:
        problems.append("no ~/.claude read-deny to carve out of: %s" % claude_deny)
    else:
        deny_idx = profile.index(claude_deny)
        for c in carveouts:
            allow = '(allow file-read* (subpath "%s"))' % c
            if allow not in profile:
                problems.append("missing carve-out read-allow for %s" % c)
            elif profile.index(allow) <= deny_idx:
                problems.append("carve-out %s emitted at %d, BEFORE the ~/.claude "
                                "read-deny at %d -- last-match-wins makes it a dead "
                                "rule" % (c, profile.index(allow), deny_idx))
    _rec(suite, GRP_Q,
         "(a2) Seatbelt: carve-out read-allow AFTER the ~/.claude read-deny (index)",
         problems)

    # (a2) Seatbelt: READ only. A carve-out must never emit a file-write* allow --
    # ~/.claude holds settings.json and hooks/, so a writable carve-out would let a
    # sandboxed command rewrite its own gate. This is the invariant, not a detail.
    problems = []
    for c in carveouts:
        bad = '(allow file-write* (subpath "%s"))' % c
        if bad in profile:
            problems.append("carve-out %s got a WRITE allow (~/.claude must stay "
                            "write-denied)" % c)
    if '(allow file-write* (subpath "%s"))' % claude in profile:
        problems.append("~/.claude itself got a write-allow")
    _rec(suite, GRP_Q,
         "(a2) Seatbelt: NO file-write* allow for any carve-out (write stays denied)",
         problems)

    # (a2) bwrap: --ro-bind <c> <c> layered AFTER the --tmpfs that masks ~/.claude,
    # because the LATER mount wins. --ro-bind is readable-but-not-writable, which is
    # exactly the wanted semantics -- no writable --bind is emitted for a carve-out.
    tmpfs_claude = [i for i in range(len(bw) - 1)
                    if bw[i] == "--tmpfs" and bw[i + 1] == claude]
    problems = []
    if not tmpfs_claude:
        problems.append("no --tmpfs mask for ~/.claude to carve out of")
    else:
        mask_idx = tmpfs_claude[0]
        for c in carveouts:
            found = [i for i in range(len(bw) - 2)
                     if bw[i] == "--ro-bind" and bw[i + 1] == c and bw[i + 2] == c]
            if not found:
                problems.append("no --ro-bind %s %s carve-out" % (c, c))
            elif found[0] <= mask_idx:
                problems.append("carve-out %s bound at %d, BEFORE the ~/.claude "
                                "--tmpfs at %d -- the later mount wins, so the tmpfs "
                                "would bury it" % (c, found[0], mask_idx))
            if any(bw[i] == "--bind" and bw[i + 1] == c for i in range(len(bw) - 1)):
                problems.append("carve-out %s bound READ-WRITE (--bind)" % c)
    _rec(suite, GRP_Q,
         "(a2) bwrap: carve-out --ro-bind AFTER the ~/.claude --tmpfs (index)",
         problems)

    # (a2) NEGATIVE -- the carve-out is fail-closed BY ENUMERATION: only the two
    # named subtrees open. ~/.claude/projects (session transcripts) and
    # ~/.claude/settings.json (the gate config) exist in the fixture and must get NO
    # carve-out entry, NO Seatbelt allow line of any kind, and NO bwrap bind. This is
    # what would fail if anyone widened the carve-out to ~/.claude wholesale.
    problems = []
    for p in not_carved:
        if p in carveouts:
            problems.append("%s wrongly in the carve-out set" % p)
        for line in ('(allow file-read* (subpath "%s"))' % p,
                     '(allow file-write* (subpath "%s"))' % p):
            if line in profile:
                problems.append("Seatbelt allow line for %s: %s" % (p, line))
        if any(bw[i] in ("--bind", "--ro-bind") and bw[i + 1] == p
               for i in range(len(bw) - 1)):
            problems.append("bwrap bind for %s" % p)
    _rec(suite, GRP_Q,
         "(a2) negative: ~/.claude/projects + settings.json get NO carve-out",
         problems)

    # (a2) existence filter: an ABSENT carve-out is dropped. A carve-out is a bwrap
    # MOUNTPOINT under the read-only `/` root, so binding one that does not exist
    # dies "Can't mkdir <p>: Read-only file system" BEFORE exec -- the exact class
    # that already killed every Linux run once via the secret set. Second HOME
    # fixture: skills/ present, scripts/ absent.
    problems = []
    if os.path.join(home_nc, ".claude", "skills") not in carveouts_nc:
        problems.append("existing carve-out dropped from the set")
    if os.path.join(home_nc, ".claude", "scripts") in carveouts_nc:
        problems.append("ABSENT carve-out wrongly bound (bwrap would die at setup "
                        "before exec -- Linux dead-on-arrival)")
    _rec(suite, GRP_Q,
         "(a2) existence filter: absent carve-out NOT in the set (bwrap mkdir guard)",
         problems)

    # (a2) --dry-run must SHOW the carve-out set. The plan is the only way a user
    # sees what the sandbox will do without running it; a read-relaxation invisible
    # in the preview is a security-relevant omission, not cosmetics.
    plan = helper._format_plan(scope)
    problems = []
    if "carveouts" not in plan:
        problems.append("plan has no carveouts line")
    for c in carveouts:
        if c not in plan:
            problems.append("plan omits carve-out %s" % c)
    if "secret_paths" not in plan:
        problems.append("plan lost its secret_paths line")
    _rec(suite, GRP_Q, "(a2) --dry-run plan lists the carve-out set", problems,
         extra=["plan        : %r" % plan])

    # --- (a3) NAME DIVERGENCE: the write-only shadow deny. The carve-out above is
    # what makes this reachable -- it opens ~/.claude/skills for READ, and the
    # deployed plugin tree under it is a symlink INTO a repo that `--write .` makes
    # writable under its other spelling. Every case here asserts ORDER by INDEX for
    # the same reason the carve-out cases do: a deny emitted before the allow it
    # revokes is a dead rule, and the live escape would stay open while the suite
    # went green.

    # (a3) Seatbelt: a file-write* deny for every shadow target, emitted AFTER the
    # LAST file-write* allow. This is the case that FAILS on the pre-fix builder,
    # which emitted no shadow rule at all.
    write_allow_idx = [i for i, l in enumerate(profile)
                       if l.startswith("(allow file-write*")]
    problems = []
    if not write_allow_idx:
        problems.append("fixture emitted no file-write* allow to order against")
    for want in shadow_want:
        if want not in shadow:
            problems.append("shadow_write_denies() omitted the divergent target %s"
                            % want)
    for t in shadow:
        deny = '(deny file-write* (subpath "%s"))' % t
        if deny not in profile:
            problems.append("missing shadow write-deny: %s" % deny)
        elif write_allow_idx and profile.index(deny) <= max(write_allow_idx):
            problems.append("shadow write-deny for %s at %d, NOT after the last "
                            "file-write* allow at %d -- last-match-wins makes it a "
                            "dead rule and the repo spelling stays writable"
                            % (t, profile.index(deny), max(write_allow_idx)))
    _rec(suite, GRP_Q,
         "(a3) Seatbelt: shadow write-deny AFTER every file-write* allow (index)",
         problems, extra=["shadow      : %r" % (shadow,)])

    # (a3) Seatbelt: WRITE only. A read-deny on a shadow target would re-close the
    # plugin tree the carve-out just opened (~/.claude/skills/p IS the divergent
    # link on the real host) and would blind the sandbox to the repo it runs in.
    # Denying READ here would trade one bug for a worse one, so it must never appear.
    problems = []
    for t in shadow:
        bad = '(deny file-read* (subpath "%s"))' % t
        if bad in profile:
            problems.append("shadow target %s got a READ deny -- the carve-out and "
                            "the repo itself would go dark" % t)
    _rec(suite, GRP_Q,
         "(a3) Seatbelt: NO file-read* deny for any shadow target (read stays open)",
         problems)

    # (a3) bwrap: the shadow target is re-bound onto ITSELF read-only, AFTER the
    # writable --bind whose spelling it revokes (the later mount wins). --ro-bind, not
    # --tmpfs: the target must stay READABLE, only lose write.
    problems = []
    for t in shadow:
        found = [i for i in range(len(bw) - 2)
                 if bw[i] == "--ro-bind" and bw[i + 1] == t and bw[i + 2] == t]
        if not found:
            problems.append("no --ro-bind %s %s shadow re-mount" % (t, t))
        elif found[0] <= last_bind:
            problems.append("shadow ro-bind for %s at %d, BEFORE the writable --bind "
                            "at %d -- the later mount wins, so the writable spelling "
                            "would stand" % (t, found[0], last_bind))
        if any(bw[i] == "--tmpfs" and bw[i + 1] == t for i in range(len(bw) - 1)):
            problems.append("shadow target %s masked with --tmpfs -- that hides it "
                            "from READ too" % t)
    _rec(suite, GRP_Q,
         "(a3) bwrap: shadow --ro-bind <t> <t> AFTER the writable --bind (index)",
         problems)

    # (a3) NEGATIVE: a symlink whose realpath STAYS under ~/.claude is no divergence
    # -- the ~/.claude deny already covers it under a spelling it matches -- so it
    # must not enter the shadow set. The fixture's inside-link points at
    # ~/.claude/projects; adding that target would emit a redundant rule naming a
    # session-transcript directory, and would mean the containment test degenerated
    # into "every symlink".
    problems = []
    if shadow_never in shadow:
        problems.append("%s is INSIDE ~/.claude yet entered the shadow set -- the "
                        "containment test is not filtering" % shadow_never)
    if '(deny file-write* (subpath "%s"))' % shadow_never in profile:
        problems.append("Seatbelt emitted a shadow rule for the inside target %s"
                        % shadow_never)
    if os.path.realpath(inside_link) in shadow:
        problems.append("the inside-link itself resolved into the shadow set")
    _rec(suite, GRP_Q,
         "(a3) negative: a symlink staying INSIDE ~/.claude is NOT a shadow target",
         problems)

    # (a3) --dry-run must SHOW the shadow set, for the same reason it shows the
    # carve-outs: the plan is the only way to see what the sandbox will do without
    # running it, and this set is what makes parts of the repo read-only under
    # `--write .` -- a surprise the preview must not hide.
    plan = helper._format_plan(scope)
    problems = []
    if "shadow_write_denies" not in plan:
        problems.append("plan has no shadow_write_denies line")
    for t in shadow:
        if t not in plan:
            problems.append("plan omits shadow target %s" % t)
    _rec(suite, GRP_Q, "(a3) --dry-run plan lists the shadow write-deny set",
         problems, extra=["plan        : %r" % plan])

    # (a4) bwrap: --unshare-pid + --proc /proc. `--ro-bind / /` imports the HOST
    # /proc recursively, and /proc/<pid>/root/<path> resolves in the HOST mount
    # namespace -- so every --tmpfs mask above can be walked around through it, and
    # /proc/<pid>/environ leaks other processes' environments. A fresh procfs closes
    # that, and the kernel only grants one to a process owning its PID namespace,
    # which is why the two ship together. --proc is a MOUNT, so it must come after
    # every bind and mask or the host /proc would be re-imported over it.
    problems = []
    if "--unshare-pid" not in bw:
        problems.append("bwrap missing --unshare-pid (a fresh /proc needs an owned "
                        "PID namespace)")
    proc_at = [i for i in range(len(bw) - 1)
               if bw[i] == "--proc" and bw[i + 1] == "/proc"]
    if not proc_at:
        problems.append("bwrap missing --proc /proc -- host /proc stays visible and "
                        "/proc/<pid>/root bypasses every mask")
    else:
        mounts = [i for i, t in enumerate(bw)
                  if t in ("--bind", "--ro-bind", "--tmpfs")]
        if mounts and proc_at[0] < max(mounts):
            problems.append("--proc /proc at %d precedes the last bind/mask at %d -- "
                            "a later mount could re-import host /proc"
                            % (proc_at[0], max(mounts)))
    _rec(suite, GRP_Q,
         "(a4) bwrap: --unshare-pid + --proc /proc, mounted after every bind/mask",
         problems)

    # (b) network unshared/denied unless --net.
    problems = []
    if "(deny network*)" not in profile:
        problems.append("Seatbelt default missing (deny network*)")
    if "(deny network*)" in seat_net:
        problems.append("Seatbelt --net still denies network")
    _rec(suite, GRP_Q, "(b) Seatbelt: (deny network*) default, omitted under --net",
         problems)

    # (b) --net must GRANT, not merely stop denying. The profile opens with
    # (deny default), so omitting (deny network*) left --net INERT on macOS: the
    # default-deny still stood and the network stayed closed while the flag and the
    # docs claimed otherwise (measured). Only (allow network*) actually opens it.
    # Linux was unaffected (--net drops --unshare-net, which really does share the
    # host net), so bwrap must be UNCHANGED by this fix -- asserted by requiring that
    # the two bwrap argvs differ in exactly the --unshare-net token and nothing else.
    problems = []
    if "(allow network*)" not in seat_net:
        problems.append("--net emits no (allow network*) -- (deny default) at the "
                        "top keeps the network shut, so the flag is INERT")
    if "(allow network*)" in profile:
        problems.append("default profile grants network* without --net (fail-open)")
    if [t for t in bw if t != "--unshare-net"] != bw_net:
        problems.append("bwrap argv changed by more than the --unshare-net token: "
                        "%r vs %r" % (bw, bw_net))
    _rec(suite, GRP_Q,
         "(b) Seatbelt: --net emits (allow network*), not just a missing deny; "
         "bwrap unchanged", problems)

    problems = []
    if "--unshare-net" not in bw:
        problems.append("bwrap default missing --unshare-net")
    if "--unshare-net" in bw_net:
        problems.append("bwrap --net still unshares net")
    _rec(suite, GRP_Q, "(b) bwrap: --unshare-net default, omitted under --net",
         problems)

    # (c) writes confined; --ro yields ZERO writable scopes on both backends.
    wa = '(allow file-write* (subpath "%s"))' % scratch
    problems = []
    if wa not in profile:
        problems.append("Seatbelt default missing write-allow for scratch")
    if any(l.startswith("(allow file-write*") for l in seat_ro):
        problems.append("Seatbelt --ro still allows a write scope (R21)")
    _rec(suite, GRP_Q, "(c) Seatbelt: scratch writable; --ro = zero write-allow (R21)",
         problems)

    problems = []
    if not any(bw[i] == "--bind" and bw[i + 1] == scratch
               for i in range(len(bw) - 1)):
        problems.append("bwrap default missing --bind for scratch")
    if "--bind" in bw_ro:
        problems.append("bwrap --ro still has a writable --bind (R21)")
    _rec(suite, GRP_Q, "(c) bwrap: scratch bound; --ro = zero writable bind (R21)",
         problems)

    # (d) canonicalize REJECTS injected metacharacters fail-closed (R14). The
    # gate rejects the same class upstream via METACHARS; here we prove the
    # helper's own fail-closed floor. stderr is captured so the reason lines do
    # not pollute the suite output.
    def _rejects(bad):
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            helper.canonicalize(bad, plain)
            return "canonicalize(%r) did not reject" % bad
        except SystemExit as exc:
            if exc.code in (0, None):
                return "canonicalize(%r) exited 0 (must be non-zero)" % bad
            return None
        finally:
            sys.stderr = old

    problems = [p for p in (_rejects("/tmp/a(b"), _rejects("/tmp/a)b")) if p]
    _rec(suite, GRP_Q, "(d) canonicalize rejects parens fail-closed (R14)", problems)

    problems = [p for p in (_rejects('/tmp/a"b'), _rejects("/tmp/a'b")) if p]
    _rec(suite, GRP_Q, "(d) canonicalize rejects quotes fail-closed (R14)", problems)

    # (d) `..` is NOT an inject char: canonicalize RESOLVES it, and the escape is
    # caught by the separator-safe containment test, not by canonicalize.
    ddrepo = ws.subdir("dd_repo")
    os.makedirs(os.path.join(ddrepo, ".git"), exist_ok=True)
    ddrepo_real = os.path.realpath(ddrepo)
    problems = []
    old = sys.stderr
    sys.stderr = io.StringIO()
    try:
        resolved = helper.canonicalize("../evil", ddrepo_real)
        root = helper.project_root(ddrepo_real)
        if helper.inside_project(resolved, root):
            problems.append("`..` escape %r wrongly classified inside project"
                            % resolved)
    except SystemExit:
        problems.append("canonicalize rejected a bare `..` (should resolve)")
    finally:
        sys.stderr = old
    _rec(suite, GRP_Q,
         "(d) `..` resolved, then REJECTED by separator-safe containment", problems)

    # setrlimit BEFORE execvp (KD-4) -- structural source-order check. Compare
    # the CALL sites: "os.execvp(" (with the paren) matches only the real call,
    # never the load-bearing "# setrlimit BEFORE os.execvp" comment above it.
    src = inspect.getsource(helper.main)
    problems = []
    if "_apply_rlimits()" not in src or "os.execvp(" not in src:
        problems.append("main() missing the _apply_rlimits()/os.execvp() calls")
    elif src.index("_apply_rlimits()") >= src.index("os.execvp("):
        problems.append("setrlimit is NOT before execvp (KD-4 violated)")
    _rec(suite, GRP_Q, "setrlimit-before-execvp ordering (KD-4)", problems)

    # M3: importing the helper runs NO exec / setrlimit / makedirs.
    calls = []
    o_exec, o_srl, o_mkd = os.execvp, _res.setrlimit, os.makedirs
    os.execvp = lambda *a, **k: calls.append("execvp")
    _res.setrlimit = lambda *a, **k: calls.append("setrlimit")
    os.makedirs = lambda *a, **k: calls.append("makedirs")
    try:
        _load_helper("sbx_helper_m3")
    finally:
        os.execvp, _res.setrlimit, os.makedirs = o_exec, o_srl, o_mkd
    problems = [] if not calls else ["import triggered: %s" % ", ".join(calls)]
    _rec(suite, GRP_Q, "M3: importing helper runs NO exec/setrlimit/makedirs",
         problems)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="sbx-gate PreToolUse grant-only Bash gate",
                    opts=opts, mode="grouped")

    for c in CASES:
        rc, out, err = run_hook(c["payload"])
        actual, reason = classify(out)
        problems = []
        if rc != 0:
            problems.append("exit code %d (must be 0)" % rc)
        if err.strip():
            problems.append("stderr not empty: %s" % err.strip()[:200])
        if actual != c["expect"]:
            problems.append("expected %s, got %s" % (c["expect"], actual))
        detail = ["payload     : %r" % c["payload"],
                  "expected=%s actual=%s exit=%d" % (c["expect"], actual, rc)]
        if c["note"]:
            detail.append("note        : %s" % c["note"])
        brief = "%s | %s | exp=%s | act=%s%s" % (
            H.FAIL if problems else H.PASS, c["name"], c["expect"], actual,
            (" | " + "; ".join(problems)) if problems else "")
        suite.record(c["group"], c["name"], problems, detail=detail, brief=brief,
                     text="payload=%r actual=%s reason=%r"
                          % (c["payload"], actual, reason[:400]))

    gate = _load_gate()
    helper = _load_helper("sbx_helper_wb")
    with H.TempWorkspace(prefix="sbx_gate.", keep=opts.keep) as ws:
        _run_whitebox(suite, gate, ws)
        _run_parity(suite, gate, helper, ws)
        _run_offline(suite, helper, ws)

    suite.render()
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
