#!/usr/bin/env python3
"""sbx-gate.py -- grant-only PreToolUse(Bash) gate for the `sbx` sandbox wrapper.

This hook auto-allows a Bash command WITHOUT a permission prompt IFF the command
is provably a single, clean, un-wrapped invocation of the ONE deployed `sbx`
wrapper -- identified by canonical-path IDENTITY (never basename), free of shell
metacharacters, with every `--write` scope inside the project and no `--net`.
Under ANY doubt it emits nothing (empty stdout) and the normal permission prompt
runs. It NEVER emits "deny": its only two outcomes are "allow" (narrow, proven)
and silence (everything else). Fail-safe == fail-to-prompt, never fail-to-allow.

Three load-bearing invariants are recorded here so no future edit reverts them
into a false-allow, a fail-closed brick, or an imported see-through parser:

(a) WHY FAIL-OPEN IS FAIL-SAFE HERE -- do NOT "fix" this into a fail-closed brick.
    The ~10-line I/O envelope is copied from mcp-first-guard.py, which fails OPEN
    ("never brick Bash": `try: main() except Exception: pass; sys.exit(0)`). For a
    DENY-guard fail-open is a genuine weakness -- an error lets a blocked command
    through. For THIS grant-only gate the polarity flips: an error -> no JSON on
    stdout -> no auto-allow -> the normal permission prompt runs -> the command is
    never auto-run and never runs unsandboxed. So inheriting the exact fail-open
    harness is not merely acceptable, it is the CORRECT safe default. An empty
    stdout ALWAYS means "prompt", so every error path is already the safe path.

(b) THE INVERTED SAFETY MODEL -- why this file COPIES, never IMPORTS, and shares NO
    parser with mcp-first-guard.py. mcp-first-guard.py steers-and-fails-open, so it
    must SEE THROUGH obfuscation (peel wrappers, descend into $( )/backticks/`bash
    -c`, fold ALL-CAPS names): a blocked command hidden one layer down must still be
    caught. This gate authorizes-and-fails-toward-the-prompt, so it must do the
    OPPOSITE -- REJECT anything it cannot trivially prove is a bare `sbx` invocation.
    Interpreting chaining/substitution here is not merely unnecessary, it is ACTIVELY
    WRONG: it is exactly the surface an authorizing gate must refuse. Therefore this
    file imports NOTHING from mcp-first-guard.py and reuses NONE of split_top /
    _tokens / primary / substitutions / _strip_heredocs. It copies ONLY the JSON I/O
    envelope and carries its own tiny, self-contained, CONSERVATIVE classifier that
    blanket-REJECTS a metacharacter blocklist. A legitimate command carrying a
    metacharacter inside a quoted argument safely falls through to a normal prompt --
    under-allow is always safe. Do NOT "improve" this gate by importing that parser.

(c) IDENTITY, NOT BASENAME (KD-6 / C1 / A01 / CWE-290) -- a basename compare is a
    FORBIDDEN false-allow. The first token is auto-allowed ONLY when its canonical
    form (expand `~`, resolve a relative/`/`-bearing form against cwd, or shutil.which
    a bare name, then os.path.realpath) equals WRAPPER_PATH EXACTLY. A program merely
    NAMED `sbx` (a planted `./sbx` in the writable `.claude/tmp`, `/tmp/x/sbx`, or a
    PATH-shadowing binary) is NOT the wrapper and grants no containment. NEVER replace
    the identity check with `os.path.basename(toks[0]) == "sbx"`.

The shared path-resolution contract (resolve_scope, project_root, and the
separator-safe containment test) is DELIBERATELY DUPLICATED here and in the helper
`sbx` -- the gate imports nothing (P8), so it cannot share the helper's copies. The
two copies MUST stay byte-identical (a one-sided edit reintroduces a gate/helper
divergence == a containment escape); task-008's cross-file parity test pins this.

Python 3, standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

# The SAFETY FLOOR (GRAFT 1 / KD-1): any of these anywhere in the raw command means
# we cannot trivially prove it is a bare `sbx` invocation, so we reject to a prompt.
# NOTE: no "~" here (L1) -- the documented invocation form starts with "~/.claude/..."
# and `~`-as-a-write-target is caught by the KD-7/KD-8 scope check, not the blocklist.
METACHARS = set(";&|$`()<>{}\n'\"\\*?")

# The ONE known-good wrapper identity (KD-6 / C1). Resolved ONCE, at import. realpath
# does not require the path to exist, so this is stable even before the skill deploys.
WRAPPER_PATH = os.path.realpath(
    os.path.expanduser("~/.claude/skills/p/skills/sandbox-run/scripts/sbx"))


def resolve_scope(dir_arg, base):
    """KD-7 shared rule -- byte-identical copy of the helper's resolve_scope (P8).

    expanduser FIRST (so "~" -> $HOME), then join against `base` (join drops `base`
    when the expanded arg is absolute), then realpath. IDENTICAL on both sides -- the
    gate imports nothing, so this is duplicated, not shared (parity test, task-008).
    """
    return os.path.realpath(os.path.join(base, os.path.expanduser(dir_arg)))


def project_root(start):
    """KD-8 shared rule -- byte-identical copy of the helper's project_root (P8).

    Nearest ancestor of `start` (inclusive) that contains a `.git` entry; if none is
    found walking up to the filesystem root, `start` itself. `start` is always `cwd`;
    `cwd` is NOT assumed to be the repo root (a Bash cwd may be any subdirectory).
    """
    cur = os.path.realpath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:            # reached the filesystem root
            return os.path.realpath(start)
        cur = parent


def _write_contained(dir_arg, cwd, root):
    """Separator-safe KD-8 containment (sec-MED-1 / A04): is the resolved `--write`
    scope inside the project? Uses os.path.commonpath (root itself allowed), NEVER a
    bare str.startswith -- a raw prefix test false-allows a `/x/repo-evil` sibling of
    `/x/repo`. Applied identically by the helper's inside_project (H1)."""
    return os.path.commonpath([resolve_scope(dir_arg, cwd), root]) == root


def is_clean_sbx(cmd, cwd):
    """Return (auto_allow, reason). auto_allow is True ONLY when `cmd` is provably a
    single, clean, un-wrapped invocation of the ONE deployed `sbx` wrapper (KD-6
    identity, never basename), metacharacter-free, every `--write` scope inside the
    project, and no `--net`. Under ANY doubt -> (False, "") -> the caller stays silent
    -> the normal permission prompt runs. This classifier shares NOTHING with
    mcp-first-guard.py: it REJECTS metacharacters, it does not see through them (b)."""
    if cwd is None:
        return (False, "")
    s = cmd.strip()
    if not s or s[0] == "#":                      # empty or a comment line -> reject
        return (False, "")
    if any(ch in METACHARS for ch in s):          # ANY metacharacter anywhere -> reject
        return (False, "")

    toks = s.split()                              # plain whitespace split; no quote logic

    # Wrapper IDENTITY, NOT basename (KD-6 / C1 / CWE-290): canonicalize toks[0] and
    # require an EXACT match to WRAPPER_PATH. A "/"- or "~"-bearing form resolves via
    # the SHARED resolve_scope rule (KD-7); a bare name via shutil.which + realpath.
    first = toks[0]
    if "/" in first or first.startswith("~"):
        cand = resolve_scope(first, cwd)
    else:
        w = shutil.which(first)
        cand = os.path.realpath(w) if w else None
    if cand != WRAPPER_PATH:                      # never os.path.basename(...) == "sbx"
        return (False, "")

    # Parse sbx's OWN flags up to the first bare "--", matching argparse's grammar
    # EXACTLY (M-B): --net, --ro, --dry-run, `--write DIR` AND the equals-form
    # `--write=DIR` (split on the FIRST "="). Refuse on --net (R11). Every --write
    # must be separator-safe-contained (KD-8). ANY unrecognized token before "--" --
    # an unknown flag OR an unhandled equals-form -- is a HARD PROMPT (bare return),
    # NEVER skipped: a `== "--write"`-only scan would silently pass `--write=/etc`
    # while the helper's argparse opens /etc, a false-allow (R12b).
    root = project_root(cwd)
    i = 1
    n = len(toks)
    while i < n:
        tok = toks[i]
        if tok == "--":                           # end of sbx flags -> target argv follows
            break
        if tok == "--net":                        # R11: --net never auto-allows
            return (False, "")
        if tok == "--ro":
            i += 1
            continue
        if tok == "--dry-run":                    # argument-less boolean, like --ro
            # SAFE TO AUTO-ALLOW: --dry-run makes the helper print the resolved plan
            # and exit 0 WITHOUT execing any child and WITHOUT creating the scratch
            # dir -- it runs NOTHING, so it imposes no scope and needs no containment
            # check. Combined with the KD-6 identity check above (toks[0] IS the one
            # deployed wrapper, whose --dry-run semantics we control), it grants
            # strictly LESS capability than the already-allowed `sbx -- <cmd>` form.
            # ONLY the exact bare token: `--dry-run=1` stays UNRECOGNIZED and falls to
            # the R12b hard prompt below, because argparse's store_true REJECTS the
            # equals-form -- the gate must never be MORE permissive than argparse (M-B).
            i += 1
            continue
        if tok == "--write":                      # space-form: DIR is the next token
            if i + 1 >= n:                        # dangling --write with no value -> doubt
                return (False, "")
            if not _write_contained(toks[i + 1], cwd, root):
                return (False, "")
            i += 2
            continue
        if tok.startswith("--write="):            # equals-form, argparse-style (M-B)
            if not _write_contained(tok.split("=", 1)[1], cwd, root):
                return (False, "")
            i += 1
            continue
        # unknown flag / unhandled equals-form / stray token -> HARD PROMPT (R12b)
        return (False, "")

    return (True, "clean single sbx invocation, scope inside project, no --net")


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return                                    # defense-in-depth self-gate
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        return
    cwd = data.get("cwd")
    if cwd is None:                               # no cwd -> cannot resolve scope -> prompt
        return
    ok, reason = is_clean_sbx(cmd, cwd)
    if not ok:
        return                                    # silence == prompt (the safe default)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",        # the ONLY positive emission; never "deny"
            "permissionDecisionReason": reason,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail OPEN -- for a GRANT-only gate this is fail-SAFE (see docstring (a)):
              # error -> no JSON -> no auto-allow -> normal prompt. Never brick Bash.
    sys.exit(0)
