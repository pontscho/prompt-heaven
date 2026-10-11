#!/usr/bin/env python3
"""sbx-gate.py -- grant-only PreToolUse(Bash) gate for the `sbx` sandbox wrapper.

This hook auto-allows a Bash command WITHOUT a permission prompt IFF the command
is provably a single, clean, un-wrapped invocation of the ONE deployed `sbx`
wrapper -- identified by canonical-path IDENTITY (never basename), printable
ASCII made of allowlisted words, free of shell metacharacters, every flag in the gate's narrowing tables, every `--write` scope
inside the project, and no `--net` / `--status-fd`. Under ANY doubt it emits
nothing (empty stdout) and the normal permission prompt runs. It NEVER emits
"deny": its only two outcomes are "allow" (narrow, proven) and silence
(everything else). Fail-safe == fail-to-prompt, never fail-to-allow.

Four load-bearing invariants are recorded here so no future edit reverts them
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

(d) AUTO-ALLOW ONLY WHAT NARROWS (ADR 0005, restricted-mode addendum) -- the gate
    may NEVER auto-allow a command that can escape containment. sbx's flags are
    split into three tables that together equal its argparse parser, with no
    overlap (sbx_restrict:S14): _PROMPT_FLAGS (--net, --status-fd) ALWAYS
    prompt, whatever accompanies them; _BOOL_FLAGS (--ro, --seccomp, --dry-run,
    --restrict-reads, --clean-env) only narrow and pass as exact bare tokens;
    _VALUE_FLAGS pass only when _value_narrows proves the value under a STRICT
    grammar -- --write contained in the project, --read/--exec absolute, --env
    keys matching _ENV_KEY_RE and never LD_/DYLD_, --timeout and every
    --rlimit-* ASCII-decimal (never str.isdigit(), which accepts non-ASCII
    digits), --rlimit-as/-cpu/-nproc no looser than sbx's own defaults
    (8 GiB, 900 s, 512; --rlimit-nproc ABSOLUTE only, never the +N headroom
    form), and --rlimit-core exactly 0 (a non-zero core limit enables core
    dumps outside the write scopes). Before any of this the raw command must be
    printable ASCII plus TAB, is split on space/tab only (bash's separators,
    never str.split()), and every word must match _WORD_RE and _tilde_ok
    (security review 20261011 G1-G3). A NEW sbx flag is a prompt until it is placed in a
    table; never add one to _BOOL_FLAGS or _VALUE_FLAGS unless every accepted
    value narrows the run.

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
import re
import shutil
import sys

# The SAFETY FLOOR (GRAFT 1 / KD-1): any of these anywhere in the raw command means
# we cannot trivially prove it is a bare `sbx` invocation, so we reject to a prompt.
# "[" and "]" are glob characters (security review 20261011 G2): `--write [x]` was
# a literal, contained <cwd>/[x] to the gate while bash handed sbx whatever `x`
# matched. NOTE: no "~" here (L1) -- the documented invocation form starts with
# "~/.claude/..."; a tilde is held to _tilde_ok instead (G3).
METACHARS = set(";&|$`()<>{}[]\n'\"\\*?")

# The command alphabet (G1): printable ASCII plus TAB, nothing else. str.split()
# also splits on \f \v \r \x1c-\x1f \x85 \xa0 U+2000.. which bash does NOT, so a
# form feed forged a phantom bare "--" the gate stopped at while sbx received the
# whole word and then a --net it never saw. Outside this alphabet -> prompt.
_COMMAND_RE = re.compile(r"[\t\x20-\x7e]*")

# bash's word separators -- exactly space and tab once the alphabet holds (G1).
_WORD_SEP_RE = re.compile(r"[ \t]+")

# The per-word allowlist (G2): every word, flag or value, must be made of these
# and nothing else. Stricter than METACHARS by construction -- "!", "#", "^" and
# any glob or expansion character not yet thought of all prompt.
_WORD_RE = re.compile(r"[A-Za-z0-9._/=+:,@%~-]+")


def _tilde_ok(word):
    """G3: is every "~" in `word` one bash and os.path.expanduser read alike?

    Only a bare "~" or a leading "~/" qualifies. expanduser leaves "~+", "~-" and
    "~user" literal while bash expands them ($PWD, $OLDPWD, a home), so the gate
    resolved <cwd>/~+/.. == <cwd> (contained) while sbx opened the project's
    parent. A "~" anywhere else in the word is refused rather than reasoned about.
    """
    if "~" not in word:
        return True
    return word == "~" or (word.startswith("~/") and "~" not in word[1:])

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


# sbx SOCKET_REFUSED_DIRS, spelled here independently (the gate imports nothing):
# the pathname-socket directories bwrap hides behind an empty tmpfs.
_SOCKET_DIRS = ("/run", "/tmp", "/var/snap", "/var/run")


def _buries_sockets(dir_arg, cwd):
    """Round-3 H1 mirror: does the resolved `--write` scope EQUAL or CONTAIN a
    socket directory (as spelled or realpath'd)? Such a scope is bound after the
    socket tmpfs and buries it; with cwd `/` or `/var` and no .git the project
    root IS the cwd, so `--write .` passed _write_contained. sbx refuses it
    (exit 2); the gate prompts rather than auto-allow a run sbx will refuse."""
    scope = resolve_scope(dir_arg, cwd)
    for d in _SOCKET_DIRS:
        for sock in (d, os.path.realpath(d)):
            if os.path.commonpath([sock, scope]) == scope:
                return True
    return False


# sbx SOCKET_UNDER_REFUSED_DIRS, spelled here independently: the socket
# directories a --write scope may not lie UNDER either (not /tmp: sbx accepts it).
_SOCKET_UNDER_DIRS = ("/run", "/var/run", "/var/snap")


def _under_sockets(dir_arg, cwd):
    """Round-3 R2 mirror: does the resolved `--write` scope lie UNDER /run,
    /var/run or /var/snap (as spelled or realpath'd)? /run/user/<uid> holds
    D-Bus, ssh-agent and gpg-agent; with no .git the project root IS such a
    cwd, so `--write .` passed _write_contained. sbx refuses it (exit 2); the
    gate prompts rather than auto-allow a run sbx will refuse."""
    scope = resolve_scope(dir_arg, cwd)
    for d in _SOCKET_UNDER_DIRS:
        for sock in (d, os.path.realpath(d)):
            if scope != sock and os.path.commonpath([sock, scope]) == sock:
                return True
    return False


# THE FLAG TABLES (invariant (d)). Every option string of sbx's argparse parser
# lives in EXACTLY ONE of these three tables (sbx_restrict:S14 pins the union to
# the parser and forbids overlap). A token in none of them before "--" prompts.
#
# _BOOL_FLAGS -- argument-less store_true flags that only NARROW a run the gate
# would already allow. Exact bare tokens ONLY: argparse's store_true refuses
# `--ro=1`, so the equals-form falls to the hard prompt.
#   --ro            denies every write, including the scratch scope.
#   --seccomp       adds a syscall allowlist; the helper refuses to run at all
#                   wherever it cannot honour it (anything but Linux x86_64).
#   --dry-run       prints the resolved plan and exits WITHOUT execing any child
#                   and WITHOUT creating the scratch dir -- it runs NOTHING.
#   --restrict-reads  replaces read-everything with an allowlist and filters
#                   process-exec (ADR 0031) -- strictly fewer reads.
#   --clean-env     starts the target with an empty environment plus --env.
_BOOL_FLAGS = frozenset({
    "--ro", "--seccomp", "--dry-run", "--restrict-reads", "--clean-env",
})

# _VALUE_FLAGS -- flags taking one value, in the space form (`--flag V`) AND the
# equals form (`--flag=V`, split on the FIRST "="). A dangling flag prompts. The
# value must pass _value_narrows, whose grammar is STRICT (ASCII digits only,
# never str.isdigit(), which accepts U+0665) and whose bounds stop at sbx's
# defaults: a value that would LOOSEN a default (more address space, more CPU,
# more processes) prompts.
_VALUE_FLAGS = frozenset({
    "--write", "--read", "--exec", "--env", "--timeout",
    "--rlimit-as", "--rlimit-cpu", "--rlimit-nproc",
    "--rlimit-fsize", "--rlimit-nofile", "--rlimit-core",
})

# _PROMPT_FLAGS -- flags that ALWAYS prompt, in any form, alongside anything.
#   --net        opens the network (R11): the one flag that widens containment.
#   --status-fd  writes to an inherited fd the gate cannot see (R2:S-L1).
_PROMPT_FLAGS = frozenset({"--net", "--status-fd"})

# The --env key grammar (sbx's parse_env refuses the same keys; the gate refuses
# them FIRST, so a loader-injection pair never reaches an auto-allow).
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_REFUSED_PREFIXES = ("LD_", "DYLD_")

# sbx's own defaults (sbx _RLIMIT_AS_BYTES / _RLIMIT_CPU_SECONDS /
# _RLIMIT_NPROC_HEADROOM): an explicit value above one of them widens the run.
_RLIMIT_AS_MAX = 8 * 1024 ** 3
_RLIMIT_CPU_MAX = 900
_RLIMIT_NPROC_MAX = 512

# ASCII-only numeric grammars. [0-9] is a literal range, so U+0665 never matches.
_UINT_RE = re.compile(r"[0-9]+")
_TIMEOUT_RE = re.compile(r"[0-9]+(\.[0-9]+)?")
_UINT_MAX_DIGITS = 20                    # sbx's own _DECIMAL_RE bound; never int() more


def _bounded_uint(value, high):
    """True when `value` is plain ASCII decimal no greater than `high`."""
    if len(value) > _UINT_MAX_DIGITS or not _UINT_RE.fullmatch(value):
        return False
    return int(value) <= high


def _value_narrows(flag, value, cwd, root):
    """Is `value` for the _VALUE_FLAGS member `flag` provably narrowing? Any doubt
    -> False -> the caller prompts. sbx re-validates every value itself; the gate
    only has to prove the run is no wider than the bare `sbx -- cmd` form."""
    if flag == "--write":                 # legacy rule (KD-8, sec-MED-1) + H1 + R2
        return (_write_contained(value, cwd, root)
                and not _buries_sockets(value, cwd)
                and not _under_sockets(value, cwd))
    if flag in ("--read", "--exec"):      # absolute only (C-L3); no "~", no relative
        return value.startswith("/") and not any(ch in METACHARS for ch in value)
    if flag == "--env":
        key, sep, _ = value.partition("=")
        return (bool(sep) and bool(_ENV_KEY_RE.fullmatch(key))
                and not key.startswith(_ENV_REFUSED_PREFIXES))
    if flag == "--timeout":               # a supervised kill only shortens a run
        return bool(_TIMEOUT_RE.fullmatch(value))
    if flag == "--rlimit-as":
        return _bounded_uint(value, _RLIMIT_AS_MAX)
    if flag == "--rlimit-cpu":
        return _bounded_uint(value, _RLIMIT_CPU_MAX)
    if flag == "--rlimit-nproc":
        # ABSOLUTE N <= 512 only (R2): `+N` is headroom over the CURRENT process
        # count, so it can land above sbx's absolute 512 default -> prompt.
        return _bounded_uint(value, _RLIMIT_NPROC_MAX)
    if flag == "--rlimit-core":
        # 0 only (R1): a non-zero core limit ENABLES kernel core dumps, written
        # outside every write scope -- it widens the run, it never narrows it.
        return _bounded_uint(value, 0)
    if flag in ("--rlimit-fsize", "--rlimit-nofile"):
        # no sbx default exists, so ANY accepted value adds a limit (narrows)
        return bool(_UINT_RE.fullmatch(value))
    return False                          # a table member without a rule -> prompt


def is_clean_sbx(cmd, cwd):
    """Return (auto_allow, reason). auto_allow is True ONLY when `cmd` is provably a
    single, clean, un-wrapped invocation of the ONE deployed `sbx` wrapper (KD-6
    identity, never basename), metacharacter-free, every flag a narrowing table
    member with a strict value (d), every `--write` scope inside the project, and
    no _PROMPT_FLAGS member (--net, --status-fd). Under ANY doubt -> (False, "") -> the caller stays silent
    -> the normal permission prompt runs. This classifier shares NOTHING with
    mcp-first-guard.py: it REJECTS metacharacters, it does not see through them (b)."""
    if cwd is None:
        return (False, "")
    if not _COMMAND_RE.fullmatch(cmd):            # G1: printable ASCII + TAB, checked
        return (False, "")                        # on the RAW command, before strip()
    s = cmd.strip(" \t")
    if not s or s[0] == "#":                      # empty or a comment line -> reject
        return (False, "")
    if any(ch in METACHARS for ch in s):          # ANY metacharacter anywhere -> reject
        return (False, "")

    toks = _WORD_SEP_RE.split(s)                  # bash's separators only; no quote logic
    for tok in toks:                              # G2 allowlist + G3 tilde rule, per word
        if not _WORD_RE.fullmatch(tok) or not _tilde_ok(tok):
            return (False, "")

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

    # Parse sbx's OWN flags up to the first bare "--", driven by the three flag
    # tables and matching argparse's grammar EXACTLY (M-B): _PROMPT_FLAGS (--net,
    # --status-fd) refuse outright (R11); _BOOL_FLAGS pass as exact bare tokens;
    # _VALUE_FLAGS take `--flag V` AND `--flag=V` (split on the FIRST "=") and the
    # value must pass _value_narrows (--write stays separator-safe-contained, KD-8).
    # ANY unrecognized token before "--" -- an unknown flag OR an unhandled
    # equals-form -- is a HARD PROMPT (bare return), NEVER skipped: a
    # `== "--write"`-only scan would silently pass `--write=/etc` while the
    # helper's argparse opens /etc, a false-allow (R12b).
    root = project_root(cwd)
    i = 1
    n = len(toks)
    while i < n:
        tok = toks[i]
        if tok == "--":                           # end of sbx flags -> target argv follows
            break
        if tok in _PROMPT_FLAGS:                  # R11 / R2:S-L1: never auto-allows
            return (False, "")
        if tok in _BOOL_FLAGS:                    # exact bare token ONLY (M-B)
            i += 1
            continue
        if tok in _VALUE_FLAGS:                   # space-form: the value is the next token
            if i + 1 >= n:                        # dangling flag with no value -> doubt
                return (False, "")
            if not _value_narrows(tok, toks[i + 1], cwd, root):
                return (False, "")
            i += 2
            continue
        flag, sep, value = tok.partition("=")     # equals-form, split on the FIRST "="
        if sep and flag in _VALUE_FLAGS:
            if not _value_narrows(flag, value, cwd, root):
                return (False, "")
            i += 1
            continue
        # unknown flag / a boolean's or prompt flag's equals-form / stray token ->
        # HARD PROMPT (R12b). argparse's store_true REJECTS `--ro=1`, so the gate must
        # never accept it either (never MORE permissive than argparse, M-B).
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
