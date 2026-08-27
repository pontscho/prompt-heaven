#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""MCP-Git: Read-only git operations MCP server.

Single-tool dispatcher pattern (like mcp-purity): one tool (git_call) routes
to whitelisted git subcommands. Mutating commands are NOT exposed — those must
go through the user's normal Bash tool with manual approval. Exception: the
full `git stash` subcommand surface is allowed (list/show/push/pop/apply/
drop/clear/branch/create/store).

Whitelist strategy:
  - Pure read-only subcommands: any args allowed.
  - Dual-use subcommands: arg-level filter rejects mutating flags.
  - Network-read subcommands (fetch --dry-run, ls-remote, apply --check): allowed.
  - Anything not in the whitelist: rejected.

Output is always Markdown (no JSON/YAML).

Usage:
  python3 mcp-git.py --project-root <path>
                     [--strict]
                     [--debug]
                     [--log-file <path>]   # implies --debug

  --project-root  Required. Git working tree the dispatcher runs `git` in.
  --strict        Reject `cwd` parameters that resolve outside --project-root.
                  Useful when the dispatcher is shared between repos.

Call `git_call` with no `function` to print the full allowlist (also available
via `python3 mcp-git.py --list`).
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("mcp-git")


# ---------------------------------------------------------------------------
# Sandbox utility (same as mcp-purity)
# ---------------------------------------------------------------------------

def safe_path(project_root: str, relative_path: str, strict: bool = False) -> str:
    if os.path.isabs(relative_path) and not strict:
        return os.path.realpath(relative_path)
    resolved = os.path.realpath(os.path.join(project_root, relative_path))
    if not resolved.startswith(project_root + os.sep) and resolved != project_root:
        raise ValueError(f"Path escapes project root: {relative_path}")
    return resolved


# ---------------------------------------------------------------------------
# Whitelist & validators
# ---------------------------------------------------------------------------

# Pure read-only — any args allowed
SAFE_SUBCOMMANDS = {
    "status", "log", "diff", "show", "blame", "annotate",
    "reflog", "shortlog", "whatchanged", "describe",
    "rev-parse", "rev-list", "merge-base", "name-rev",
    "show-ref", "show-branch", "cat-file", "count-objects",
    "ls-files", "ls-tree", "for-each-ref", "grep", "check-ignore",
    "ls-remote", "version", "help",
}


def _reject_flags(args: List[str], forbidden: set, subcmd: str) -> None:
    for a in args:
        base = a.split("=", 1)[0]
        if base in forbidden:
            raise ValueError(f"Forbidden git {subcmd} flag (would mutate repo): {a}")


def validate_branch(args: List[str]) -> None:
    forbidden = {
        "-d", "-D", "--delete",
        "-m", "-M", "--move",
        "-c", "-C", "--copy",
        "-u", "--set-upstream", "--set-upstream-to", "--unset-upstream",
        "--edit-description",
    }
    _reject_flags(args, forbidden, "branch")
    list_flags = {
        "-l", "--list", "-a", "--all", "-r", "--remotes",
        "--show-current", "--contains", "--no-contains",
        "--merged", "--no-merged", "--points-at",
    }
    has_positional = any(not a.startswith("-") for a in args)
    has_list_flag = any(a in list_flags for a in args)
    if has_positional and not has_list_flag:
        raise ValueError(
            "git branch with a positional arg would create a branch. "
            "Add -l/--list/--contains/--merged/--points-at to use it as a filter."
        )


def validate_tag(args: List[str]) -> None:
    forbidden = {
        "-d", "-D", "--delete",
        "-a", "--annotate",
        "-s", "--sign", "--no-sign",
        "-m", "-F", "--file",
        "-f", "--force",
        "--cleanup",
    }
    _reject_flags(args, forbidden, "tag")
    list_flags = {
        "-l", "--list", "-n",
        "--contains", "--no-contains",
        "--merged", "--no-merged", "--points-at",
    }
    has_positional = any(not a.startswith("-") and not a.startswith("-n") for a in args)
    has_list_flag = any(a == "-l" or a == "--list" or a.startswith("-n")
                        or a in list_flags for a in args)
    if has_positional and not has_list_flag:
        raise ValueError(
            "git tag with a positional arg would create a tag. "
            "Add -l/--list/--contains/--merged/--points-at to use it as a filter."
        )


def validate_remote(args: List[str]) -> None:
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return
    allowed = {"show", "get-url"}
    if positional[0] not in allowed:
        raise ValueError(
            f"git remote subcommand '{positional[0]}' not allowed. "
            f"Allowed: {', '.join(sorted(allowed))} (or no subcommand to list)."
        )


def validate_stash(args: List[str]) -> None:
    allowed = {
        "list", "show",
        "push", "save",
        "pop", "apply",
        "drop", "clear",
        "branch",
        "create", "store",
    }
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return
    if positional[0] not in allowed:
        raise ValueError(
            f"git stash subcommand '{positional[0]}' not recognized. "
            f"Allowed: {', '.join(sorted(allowed))}."
        )


def validate_config(args: List[str]) -> None:
    forbidden = {
        "--add", "--unset", "--unset-all", "--replace-all",
        "--rename-section", "--remove-section",
        "--edit", "-e", "--unset-regexp",
    }
    _reject_flags(args, forbidden, "config")
    has_read_flag = any(
        a == "--list" or a == "-l" or a.startswith("--get")
        for a in args
    )
    positional = [a for a in args if not a.startswith("-")]
    if has_read_flag:
        return
    if len(positional) >= 2:
        raise ValueError(
            "git config <name> <value> sets a value (mutates). "
            "Use --get/--get-all/--get-regexp/--list to read."
        )


def validate_hash_object(args: List[str]) -> None:
    # -w is the ONLY mutating mode: it writes the blob into .git/objects.
    _reject_flags(args, {"-w", "--write"}, "hash-object")
    # Short options bundle: `-wt blob` is parsed by git as `-w -t blob` and DOES
    # write the object, so exact-match rejection of "-w" is not enough. The only
    # short options here are -t/-w, and no object type contains a 'w', so any
    # single-dash cluster carrying a 'w' is either -w or an invalid -t value.
    for a in args:
        if a == "--":
            break                            # everything after -- is a path
        if a.startswith("-") and not a.startswith("--") and "w" in a[1:]:
            raise ValueError(
                f"Forbidden git hash-object flag (would mutate repo): {a} "
                "(bundled short options are expanded by git: -wt == -w -t)"
            )
        # Prefix match, not equality: git accepts unambiguous long-option
        # abbreviations, so `--stdin-pa` still reaches --stdin-paths. Shorter
        # forms (--stdi) are ambiguous and git rejects them itself.
        if a.startswith("--stdin"):
            raise ValueError(
                "git hash-object --stdin/--stdin-paths is not available here: this "
                "server's stdin is the MCP protocol stream, so reading it would "
                "corrupt the session. Pass file path(s) instead."
            )
    if not any(not a.startswith("-") for a in args):
        raise ValueError(
            "git hash-object needs at least one file path "
            "(e.g. params={\"path\":\"src/main.c\"})."
        )


def validate_fetch(args: List[str]) -> None:
    if "--dry-run" not in args:
        raise ValueError("git fetch is only allowed with --dry-run.")


def validate_apply(args: List[str]) -> None:
    if "--check" not in args:
        raise ValueError("git apply is only allowed with --check.")


FILTERED_SUBCOMMANDS: Dict[str, Callable[[List[str]], None]] = {
    "branch": validate_branch,
    "tag":    validate_tag,
    "remote": validate_remote,
    "stash":  validate_stash,
    "config": validate_config,
    "fetch":  validate_fetch,
    "apply":  validate_apply,
    "hash-object": validate_hash_object,
}

SUBCOMMAND_DESCRIPTIONS = {
    "status":         "Working tree status",
    "log":            "Commit history",
    "diff":           "Show changes (working tree, staged, between commits)",
    "show":           "Show a commit, tag, or object",
    "blame":          "Annotate each line with last modifying commit",
    "annotate":       "Alias for blame",
    "reflog":         "Show ref update log",
    "shortlog":       "Summarize git log output",
    "whatchanged":    "Show logs with diff each commit introduces",
    "describe":       "Describe a commit using nearest tag",
    "rev-parse":      "Parse and print revisions",
    "rev-list":       "List commits in reverse order",
    "merge-base":     "Find common ancestor of commits",
    "name-rev":       "Find symbolic name for revision",
    "show-ref":       "List refs",
    "show-branch":    "Show branches and their commits",
    "cat-file":       "Show object content / type / size",
    "count-objects":  "Count and disk usage of objects",
    "ls-files":       "List tracked files",
    "ls-tree":        "List tree object contents",
    "for-each-ref":   "Iterate refs with formatting",
    "grep":           "Search tracked files",
    "check-ignore":   "Which ignore rule excludes a path (-v/-n/--no-index; no match: exit 1, -n prints it)",
    "ls-remote":      "List references on remote (network)",
    "version":        "Print git version",
    "help":           "Show git help",
    "branch":         "List branches (mutating flags blocked)",
    "tag":            "List tags (mutating flags blocked)",
    "remote":         "List remotes / show / get-url",
    "stash":          "Full stash support: list/show/push/save/pop/apply/drop/clear/branch/create/store",
    "config":         "Read config (--list / --get*)",
    "fetch":          "Network read with --dry-run only",
    "apply":          "Patch validity check with --check only",
    "hash-object":    "Compute the git object ID of a file (-w and --stdin blocked)",
}


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _md_fence(content: str, lang: str = "") -> str:
    """Wrap *content* in a fence wide enough to survive its own backticks.

    The `strip("\\n")` is not cosmetic, it removes a DIVERGENCE: the unfenced
    branch in handle_git_call already emits `stdout.strip("\\n")`, and
    `_needs_fence` already decides on `text.strip("\\n")` -- so the payload's
    trailing newline was being stripped everywhere EXCEPT here, where it landed
    a blank line before the closing fence on every fenced reply. One payload
    rendering two ways depending on which branch caught it is the same shape of
    defect as the `_cap_text` pager bug: two individually reasonable layers that
    jointly revoke a property.

    Only NEWLINES go. Trailing SPACES stay, per line: for `diff`, `blame` and
    `grep` a line's trailing whitespace is the payload rather than padding --
    `git diff --check` exists to hunt exactly that -- so stripping it would edit
    the evidence. That is also why the space-squeeze is per-subcommand and lives
    in _squeeze, not here.
    """
    content = content.strip("\n")
    max_run = 0
    for run in re.findall(r"`+", content):
        if len(run) > max_run:
            max_run = len(run)
    fence = "`" * max(3, max_run + 1)
    return f"{fence}{lang}\n{content}\n{fence}"


def _needs_fence(text: str) -> bool:
    """True when *text* cannot sit in Markdown prose unharmed.

    `_md_fence` already decides how WIDE a fence must be; this decides whether
    one is needed at all. It errs toward keeping it, because a fence is the
    cheapest part of the envelope (7-8 chars) and the failure it prevents is a
    corrupted reply, not a wasted token. It may only be dropped for a payload
    that survives verbatim: ONE line, no backtick, no block-level lead
    character, no edge whitespace to lose.

    Multi-line ALWAYS needs one: Markdown folds a single newline into a space,
    so an unfenced two-line answer silently arrives as one line. That is the
    same class of quiet corruption as an unbalanced fence, just quieter.
    """
    body = text.strip("\n")
    if not body:
        return False
    if "\n" in body:
        return True                      # Markdown would fold the line breaks
    if body != body.strip():
        return True                      # edge whitespace is part of the answer
    if "`" in body:
        return True
    # Block-level lead characters: heading, quote, list, table row, setext rule.
    # `git status -b` opens with `## master...`, which without a fence renders
    # as an H2 heading — this branch is why the fence stays on that reply.
    if body[0] in "#>|-*+=":
        return True
    if body[0].isdigit() and body[1:2] in (".", ")"):
        return True                      # ordered-list marker
    return False


# Characters that survive an unquoted shell word unchanged, in any position.
_SHELL_SAFE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-_./:,=+@%~"
)
# ... except these three, which are inert mid-word but expand when they START a
# word. This is an EXPANSION argument, so no amount of shlex round-tripping can
# check it; tests/test_mcp_git_params.py group F replays every echoed line under
# /bin/bash, /bin/zsh and /bin/sh by name and asserts the words each one yields:
#   ~   all three expand a leading `~/x` to $HOME/x, and all three leave
#       `HEAD~7..HEAD` alone -> the carve-out is by POSITION, not by character.
#   =   zsh specifically: a bare `=x` dies with "zsh:1: x not found", because
#       equals-expansion looks the word up as a command. bash and sh do not.
#   #   all three swallow the rest of the line as a comment.
_SHELL_UNSAFE_LEADING = frozenset("~=#")


def _quote_arg(a: str) -> str:
    """Render one argv element for the human-readable command line we echo back.

    DISPLAY FIDELITY ONLY — this is NOT a security boundary. git is spawned via
    subprocess.run() with a list argv and shell=False (see handle_git_call), so
    no shell ever parses these strings and nothing here can affect execution:
    every element reaches git verbatim whatever it contains. What it does affect
    is whether the `git ...` line in our Markdown reply still means the same
    thing when a human copy-pastes it into a shell. The old check quoted only
    " \\t\\"'\\\\$`", which left `;` `|` `&` `<` `>` `(` `)` `*` `?` `[` `]` `{`
    `}` `!` `#` and a leading `~` bare — so an echoed line could redirect,
    background, glob, brace-expand or comment out part of itself and describe
    something the server never ran.

    Whitelist rather than blacklist, so a metacharacter we forgot is quoted
    (harmless) instead of leaked (misleading).

    Why not shlex.quote(): its safe set is `%+,-./0-9:=@A-Z_a-z` — no `~` — so
    it renders the most ordinary git revisions as `'HEAD~7..HEAD'` and
    `'HEAD~1'`, and every history line comes back in quotes. Readability of the
    common case is the whole point of echoing the command, so the safe set here
    keeps `~` and pays for it with a leading-character carve-out
    (_SHELL_UNSAFE_LEADING): `HEAD~7..HEAD` stays bare, `~/x` gets quoted.
    That carve-out also fixes one place where shlex.quote is outright wrong for
    an interactive reader: it leaves `=x` unquoted, and zsh's equals-expansion
    turns that into "zsh:1: x not found". The set is otherwise a subset of
    shlex's plus `~`, so anything shlex would quote is quoted here too.

    Ordinary git arguments — `--oneline`, `-20`, `master..HEAD`, `HEAD~7..HEAD`,
    `--max-count=10`, `--pretty=format:%h`, `src/dir/file.c` — stay unquoted.
    `^` is quoted although sh/bash/zsh in their default configuration all leave
    it alone (measured), because `setopt extendedglob` — common in real zsh
    setups — makes `^master` a negation pattern; over-quoting costs two
    characters, under-quoting costs the reader's trust.

    What is machine-checked, and what is not
    (tests/test_mcp_git_params.py, groups F and I):
      * group I asserts the property `shlex.split(_quote_arg(s)) == [s]` over a
        hostile corpus, in plain POSIX mode and in the stricter
        punctuation_chars mode, plus the converse — that the ordinary arguments
        above come back UNQUOTED. shlex models QUOTING and WORD SPLITTING, and
        that is all. The suite replays both round trips against the PRE-FIX
        rendering to price them: of the 17 corpus values this fix re-renders,
        plain shlex.split notices 1 (the newline) and the punctuation_chars lexer
        notices 9 (`;` `|` `&` `<` `>` `(` `)` `#` and the newline). Both are
        blind to the remaining 8 — globbing, brace expansion, `!`, and every
        leading-character expansion above, i.e. blind to the reason those
        characters are listed at all.
      * group F therefore also replays each echoed line through real bash, zsh
        and sh and compares the words they produce against the argv git got.
        That is the only oracle that can see an expansion, and it is what backs
        the `~` / `=` / `#` carve-out and the `^` decision.
    """
    if not a:
        return "''"
    if a[0] in _SHELL_UNSAFE_LEADING or any(c not in _SHELL_SAFE_CHARS for c in a):
        return "'" + a.replace("'", "'\\''") + "'"
    return a


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

DEFAULT_MAX_CHARS = 100_000

DEFAULT_TIMEOUT_SEC = 60

# Upper bound on the caller-supplied `timeout`, in the spirit of mcp-jenkins'
# MAX_RUN_AND_WAIT_SEC. An UNCLAMPED caller-supplied deadline is the same defect
# class as the inline handler this loop used to have (see McpServer.run): both
# let one request decide how long the server stays unavailable, and neither is
# bounded by anything the SERVER controls. It merely used to be unreachable in
# practice because a local git read finishes in milliseconds — "practically
# fine" was a property of current usage, not of the code, and a model writing
# {"timeout": 86400} bought a day-long worker slot (and, for a mutating stash, a
# day-long hold on _MUTATING_GIT_LOCK below). 300s is far past any honest local
# read on this whitelist: the slowest of them — `log -p` over a full history,
# `grep` across a large tree, `count-objects -v` on a cold cache — are seconds,
# not minutes.
MAX_TIMEOUT_SEC = 300

# One lock for every git invocation that MUTATES this working tree. The module
# itself holds no state (see the audit note in McpServer.run), so handlers are
# free to run concurrently — but the REPOSITORY is shared state that no amount
# of stateless Python makes safe: two `git stash push` calls racing on one
# worktree interleave their index writes, and the loser is a caller's
# uncommitted work. So the mutating door is serialized while the read-only ones
# stay concurrent, which is the entire point of the two-executor loop below.
#
# ONE lock for the process, not one per worktree. `cwd` can name a sub-repo (or,
# without --strict, an absolute path in another repo entirely), so two stashes in
# two unrelated repositories serialize against each other needlessly. That is the
# conservative direction of wrong: the cost is one waiting stash, the alternative
# is keying a lock table on a resolved worktree path — module-level mutable state,
# in the server whose read loop was just fixed for not having any.
#
# Only `stash` can get through here: every other whitelisted subcommand is
# read-only by construction (hash-object rejects -w, fetch demands --dry-run,
# apply demands --check, config/branch/tag/remote reject their mutating flags).
# The lock is held across subprocess.run, so MAX_TIMEOUT_SEC above is what keeps
# it from being held forever.
#
# Residual, stated rather than hidden: a read-only git command may still take
# .git/index.lock briefly to refresh the index, so it can collide with a
# concurrent stash and git will print its own "Unable to create index.lock"
# error. That is git's ordinary multi-client behaviour — the same thing a human
# typing `git status` in a terminal during a stash gets — and it is a retryable
# error message, not corruption. Two concurrent stashes are corruption, which is
# why THAT is the pair this lock separates.
_MUTATING_GIT_LOCK = threading.Lock()

# The two stash subcommands that only READ. Everything else in validate_stash's
# allowlist — including the empty positional list, which git reads as an
# implicit `push` — mutates.
_READ_ONLY_STASH = {"list", "show"}


def _mutates_repo(function: str, args: List[str]) -> bool:
    """True when this argv would change the worktree, the index or a ref.

    The positional list is extracted exactly as validate_stash extracts it, so
    the two cannot disagree about what a given argv means: a `--` or any flag is
    skipped, so `stash push -- src/x.c` is seen as mutating (positional[0] is
    the path, not "show") and `stash show -- src/x.c` is not.
    """
    if function != "stash":
        return False
    positional = [a for a in args if not a.startswith("-")]
    return not positional or positional[0] not in _READ_ONLY_STASH


def _run_timeout(params: dict) -> float:
    """The clamped subprocess deadline for one git invocation.

    Coercion, not just clamping, because the clamp needs a NUMBER to compare
    and the wire hands over whatever the caller typed. Three shapes reach here
    that `min()` cannot handle and that used to reach subprocess.run intact:
      * a string: `subprocess.run(timeout="30")` is a TypeError inside
        Popen.wait, surfacing as an opaque "Internal server error: TypeError"
        instead of a git answer. float() repairs the NUMERIC ones — `"30"`
        means 30 to every reader, and refusing it would be pedantry — while a
        non-numeric string takes the fallback.
      * NaN: json.loads accepts the bare token `NaN` by default, and every
        comparison against NaN is False — so the deadline never expires and the
        clamp this function exists for is silently skipped.
      * 0 / negative (which is also what a `timeout: false` coerces to): a
        deadline the command cannot possibly meet, so the call could only ever
        answer "timed out".
    The unusable ones fall back to the documented default rather than raising:
    the caller asked a git question, and a bad deadline is not a reason to
    refuse it.
    """
    try:
        requested = float(params.get("timeout", DEFAULT_TIMEOUT_SEC))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    # `requested != requested` is the NaN test; it must come first, because NaN
    # would also slip through the `<= 0` comparison below.
    if requested != requested or requested <= 0:
        return DEFAULT_TIMEOUT_SEC
    return min(float(MAX_TIMEOUT_SEC), requested)


# Keys in params that are handled specially (not forwarded as git CLI flags)
_META_KEYS = {"args", "cwd", "timeout", "max_answer_chars"}

# Keys naming a revision or revision RANGE. These are positional args in git
# (`git log A..B`), so they need an entry here — otherwise the generic conversion
# below turns a caller-invented name like `range` into a bogus `--range=A..B`
# flag and git dies with "unrecognized argument". All of these address the same
# slot; `range` is the canonical spelling, the rest are aliases so callers do not
# have to guess.
#
# `refs` earns its entry twice over: git's own grammar names that slot
# (`git ls-remote [<options>] [<repository> [<refs>...]]`), and the tool
# description quotes that very line — so it is the spelling a caller reaches for,
# while the alias list never offered it. It fell through to `--refs=master`,
# which git refuses with "option `refs' takes no value" (exit 129, measured).
# It is also the one key here whose BOOLEAN spelling names a working git flag:
# `ls-remote --refs` hides peeled tags. (`path` reaches a real option too —
# `cat-file --path` — but that one takes a value, so no boolean spelling of it can
# quietly succeed.) That is why a boolean value is NOT a positional here — see the
# isinstance(value, bool) carve-out in _semantic_params_to_args.
_REVISION_KEYS = {
    "range", "revision_range", "rev_range", "rev", "revs",
    "revision", "revisions", "ref", "refs", "commit", "commits",
    "object", "tree_ish", "treeish",
}

# Keys naming file paths / pathspecs (also positional)
_PATH_KEYS = {"pathspec", "paths", "path"}

# Keys naming a repository: a remote name, a remote alias, or a URL. git takes
# this as a POSITIONAL argument as well, and it comes BEFORE any refs:
#     git ls-remote [<options>] [<repository> [<refs>...]]
#     git fetch     [<options>] [<repository> [<refspec>...]]
# so these are emitted ahead of the revision/path positionals no matter which
# order the caller wrote the params in. Without an entry here the fall-through
# turned {"remote": "github"} into `--remote=github` and git died with
# "error: unknown option `remote=github'" (exit 129); the only spelling that
# worked was smuggling the remote through `ref` — which lands positionally by
# accident, is semantically wrong (a remote is not a revision) and is
# undiscoverable. Claiming these three names costs nothing: no subcommand on
# this whitelist has a real `--remote` / `--repository` / `--repo` flag. git's
# real `--remotes` (log, branch) is a DIFFERENT key and still becomes a flag.
_REPO_KEYS = {"remote", "repository", "repo"}

# Keys that map to positional arguments (appended after flags, not as --key=val)
_POSITIONAL_KEYS = _REVISION_KEYS | _PATH_KEYS | _REPO_KEYS


def _camel_to_snake(key: str) -> str:
    """Normalize a camelCase param key to its snake_case spelling.

    Applied to EVERY key at the top of the conversion loop, BEFORE the meta
    check and before any membership test, so a caller who writes `maxCount`,
    `noMerges`, `revRange` or `maxAnswerChars` reaches exactly the same slot as
    the snake_case spelling every set here is written in. Placing it ahead of the
    `_META_KEYS` test is deliberate: it makes `maxAnswerChars` recognized as meta
    and camelCase positional aliases (`revRange` -> `rev_range`, `treeIsh` ->
    `tree_ish`) match the snake_case membership sets at the positional test.

    The sets stay snake_case: the normalization happens on the way IN, not on the
    sets. An already-snake or all-lowercase key is returned unchanged (identity),
    so no existing `--key=value` fall-through shifts -- `remote_name`, `reference`
    and the `revison` typo still become `--remote-name`, `--reference` and
    `--revison`.
    """
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key).lower()


def _check_not_option(value: str, key: str, what: str, example: str) -> None:
    """Reject a positional value that git's option parser would read as a flag.

    Positional values reach git as separate argv elements (subprocess list form,
    no shell), so the only smuggling vector is git's own parser — and that treats
    an argument as an option only when it starts with '-'. Refusing a leading dash
    therefore closes the hole completely, including bundled short options
    (`-wt` == `-w -t`, the hash-object lesson) and `--long=value` forms that an
    exact-match blocklist would miss. Nothing else needs restricting: legitimate
    revisions contain spaces and colons (`HEAD@{2 days ago}`, `:/fix typo`) and
    may start with '^' (`^master`), and a repository may be a URL — none of which
    git can read as an option. A `--` separator cannot do this job for the slots
    guarded here: git reads everything after `--` as a path, so a separator placed
    in front of a revision or a repository would destroy it — which is why these
    values are screened for a leading dash instead. Paths are the opposite case:
    they WANT the separator and are given one, after everything else, by
    _append_path_positionals.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"params.{key} must not be empty.")
    if stripped.startswith("-"):
        raise ValueError(
            f"params.{key} must be {what} (e.g. {example!r}), "
            f"not an option: {value!r}. Values starting with '-' are refused "
            "because git would parse them as flags (and short options bundle: "
            "-wt == -w -t). Pass flags as named params or via params.args."
        )


def _check_revision(value: str, key: str) -> None:
    _check_not_option(value, key, "a revision or range", "master..HEAD")


def _check_repository(value: str, key: str) -> None:
    """Same guard for the repository slot: without it `{"remote":
    "--upload-pack=<cmd>"}` would reach git ls-remote as an option."""
    _check_not_option(value, key, "a remote name or URL", "origin")


def _semantic_params_to_args(params: dict) -> Tuple[List[str], List[str]]:
    """Convert semantic parameter names to CLI args.

    Models sometimes pass named parameters (e.g. max_count=5, pretty="%h %s")
    instead of raw args lists.  This converts them to CLI flags so both
    calling styles work.

    Returns (args, paths) — file paths come back SEPARATELY, they are not part of
    the first list. Order within it is: flags, then the repository, then
    revisions — git spells it `git ls-remote [<options>] [<repository>
    [<refs>...]]`, so the repository cannot simply follow the caller's param
    order. Revision and repository values are checked for a leading dash. Every
    other key becomes a `--key[=value]` flag, so an unknown key reaches git
    verbatim.

    WHY PATHS LEAVE BY A SEPARATE DOOR. They belong after a `--`, and that
    separator cannot be placed from here: it has to come after the caller's own
    `params.args`, or every flag written there turns into a pathspec. That
    concatenation happens in handle_git_call, so the caller of this function owns
    the placement — see _append_path_positionals, which also explains why the
    paths must reach the VALIDATORS, and why guessing that answer got it backwards.

    Keeping them in one list was a defect on its own, independently of the
    missing separator: revisions and paths were appended in the caller's dict
    order, so `{"path": ..., "range": ...}` emitted the path FIRST and git read
    the range as another path (`fatal: <range>: no such path in the working
    tree`, exit 128, measured) while `{"range": ..., "path": ...}` — the same two
    values, the other key order — worked. Correctness must not depend on JSON key
    order, which nothing in the protocol preserves for a caller anyway.

    A positional key carrying a BOOLEAN SCALAR is emitted as a flag instead. That
    is not a special case for one key but the only reading that can be right: a
    caller writing `true` cannot mean a revision by that name, while `refs=true`
    IS a working git flag. A boolean inside a LIST is deliberately left alone and
    becomes the positional "True" — a list means "several values for this slot",
    and dropping an element there would answer a nonsensical request with silence
    instead of with git's own complaint.

    There is no per-subcommand param schema: the whole key set is accepted for
    every function, and a key is simply meaningless where git takes no such
    argument (`log {"remote": "origin"}` hands git a revision named "origin").
    Narrowing a key to one subcommand would mean inventing that schema layer, and
    these names produced a guaranteed-bogus flag before they were claimed, so
    global scope costs no working behaviour. It is documented in GIT_CALL_TOOL
    instead.

    Raises ValueError on a rejected revision / repository value.
    """
    flags: List[str] = []
    repos: List[str] = []
    revisions: List[str] = []
    paths: List[str] = []

    for key, value in params.items():
        # camelCase -> snake_case, before the meta check and before any set
        # membership test, so `maxAnswerChars` is caught as meta and camelCase
        # positional aliases match the snake_case sets. See _camel_to_snake.
        key = _camel_to_snake(key)
        if key in _META_KEYS:
            continue
        # A positional key carrying a BOOLEAN SCALAR falls through to the flag
        # branch on purpose: `{"refs": true}` is git's own `ls-remote --refs`, and
        # without this it would emit the positional "True" and git would hunt for a
        # ref by that name. A bool inside a LIST is NOT filtered — see the
        # docstring for why silence would be the worse answer there.
        #
        # The bool test below must stay AHEAD of the int test: bool subclasses int
        # in Python, so swapping them renders `{"refs": true}` as `--refs=True`,
        # which is exactly the exit-129 shape this key was fixed for.
        if key in _POSITIONAL_KEYS and not isinstance(value, bool):
            if key in _REPO_KEYS:
                target = repos
            elif key in _PATH_KEYS:
                target = paths
            else:
                target = revisions
            values = value if isinstance(value, list) else [value]
            for v in values:
                v = str(v)
                if key in _REVISION_KEYS:
                    _check_revision(v, key)
                elif key in _REPO_KEYS:
                    _check_repository(v, key)
                target.append(v)
            continue

        cli_key = key.replace("_", "-")

        if isinstance(value, bool):
            if value:
                flags.append(f"--{cli_key}")
        elif isinstance(value, int):
            flags.append(f"--{cli_key}={value}")
        elif isinstance(value, float):
            flags.append(f"--{cli_key}={int(value) if value == int(value) else value}")
        elif isinstance(value, str):
            flags.append(f"--{cli_key}={value}")
        elif isinstance(value, list):
            for v in value:
                flags.append(f"--{cli_key}={v}")

    return flags + repos + revisions, paths


# Subcommands that must not receive a `--` even when a path was named. `git
# rev-parse` prints every `--` it is handed as an OUTPUT LINE (measured), so the
# separator would arrive in the caller's answer as data.
_DASHDASH_ECHOED = {"rev-parse"}


def _append_path_positionals(function: str, args: List[str],
                             paths: List[str]) -> List[str]:
    """Append file paths to *args*, behind a `--` where that is safe.

    `--` is what tells git "everything after this is a path", and without it a
    path that is not resolvable as a revision AND not present in the working tree
    is fatal: `git log <deleted-file>` dies with "ambiguous argument ... unknown
    revision or path not in the working tree" (exit 128, measured), and git's own
    error text prescribes the separator. With it the same call answers exit 0 and
    an empty history — which is the honest answer to "what happened to this file".
    The history of a DELETED path is the main reason `git log -- <path>` exists,
    so the slot was unusable for its primary purpose.

    Three carve-outs, each measured rather than assumed:

      * only when a path was actually named. A bare `--` with no path is exit 129
        on blame/annotate (`usage: git blame ...`).
      * only when the caller did not already write `--` in params.args. A SECOND
        separator is exit 128 on blame, annotate and hash-object ("fatal: could
        not open '--' for reading"). If the caller opened the pathspec section
        themselves, the paths simply join it.
      * never for _DASHDASH_ECHOED (see above).

    There is deliberately NO per-subcommand pathspec whitelist, in keeping with
    this module's rule that a key is merely meaningless where git takes no such
    argument. It was measured for the whole allowlist: where there is no pathspec
    slot, the separator changes nothing — describe/merge-base/show-branch/ls-remote
    fail with exit 128 identically with and without it, count-objects and cat-file
    reject the path at 129 either way, and name-rev/for-each-ref/show-ref misread
    it silently in both spellings. So the gate would defend no working behaviour
    while inventing the schema layer this server does without.

    WHERE THIS IS CALLED FROM IS A SECURITY BOUNDARY, and the answer is the
    opposite of the plausible one. The paths must join the argv BEFORE the
    validators run, because the FILTERED_SUBCOMMANDS guards judge POSITIONALS:
    validate_branch, validate_tag and validate_config each decide by what the
    positional list holds. Appended after them, a path is a positional they never
    saw — and git strips the `--` happily. Measured on the first attempt at this
    change: `branch {"path": "newbranch"}` became `git branch -- newbranch` and
    CREATED THE BRANCH (exit 0), `tag {"path": "v9.9"}` created the tag, and
    `config {"paths": ["user.name", "evil"]}` WROTE THE CONFIG — three mutations
    through a read-only server, while the same values in params.args stayed
    correctly refused.

    What is safe about this position — also measured, not reasoned — is that the
    separator lands at the END of the argv, behind the caller's own flags. Every
    flag scan here stops at a `--` (validate_hash_object, _status_format_chosen,
    _status_branch_flag_present), but a left-to-right scan has already passed the
    caller's flags by the time it reaches the tail, so nothing is truncated:
    `hash-object {"path": "x", "args": ["-wt", "blob"]}` stays refused by the
    bundled-option check this repo paid for once, and `status {"paths": "x",
    "args": ["-s"]}` still has its `-s` recognised as a chosen format. The hazard
    is the separator's position WITHIN argv, never the call site's order. Moving
    it in front of params.args is what breaks both, and a mutant does exactly that
    to keep the distinction honest.
    """
    if not paths:
        return args
    if function in _DASHDASH_ECHOED or "--" in args:
        return args + paths
    return args + ["--"] + paths


_STATUS_FORMAT_FLAGS = ("--short", "--long", "--porcelain", "--no-short")


def _status_format_chosen(args: List[str]) -> bool:
    """True when the caller already picked a `git status` output format.

    Short-option clusters count: `-sb` chose short just as `-s` did. That is the
    bug class this repo already paid for once on `hash-object -wt`, where a
    bundled letter slipped past a check that only compared whole arguments.
    """
    for a in args:
        if a == "--":            # everything after this is a pathspec, not a flag
            break
        if a.split("=", 1)[0] in _STATUS_FORMAT_FLAGS:
            return True
        if len(a) > 1 and a[0] == "-" and not a.startswith("--"):
            if "s" in a[1:] or "z" in a[1:]:
                return True
    return False


def _status_branch_flag_present(args: List[str]) -> bool:
    """True when the caller already asked for the branch header (-b / --branch)."""
    for a in args:
        if a == "--":
            break
        if a == "--branch":
            return True
        if len(a) > 1 and a[0] == "-" and not a.startswith("--") and "b" in a[1:]:
            return True
    return False


def _status_default_format(args: List[str]) -> List[str]:
    """Prepend a machine format to `git status` when the caller chose none.

    Plain `git status` spends ~190 characters on advice this tool physically
    cannot act on — `(use "git add ...")`, `(use "git restore ...")`, `no changes
    added to commit` — because git_call is read-only and not one of those commands
    can be run through it. For the model reading the reply that is pure cost: the
    same answer (branch, upstream delta, per-file state) fits in three lines.

    `--porcelain=v1` rather than `-s`, because git contractually keeps porcelain
    stable across versions while the short format is explicitly not guaranteed.
    `-b` restores the branch/upstream line the long format gave for free, in
    `## master...github/master [ahead 5]` form — the delta is what callers here
    actually read before a push.

    A caller who states a format keeps it, untouched.
    """
    if _status_format_chosen(args):
        return args
    prefix = ["--porcelain=v1"]
    if not _status_branch_flag_present(args):
        prefix.append("-b")
    return prefix + args


def _ensure_dict(value: Any, name: str = "params") -> dict:
    """Coerce *value* to a dict.

    Accepts None (→ {}), dict (passthrough), or JSON-encoded object string.
    Raises ValueError on a non-JSON string, JSON that is not an object,
    or any other type.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'{name}' was a string but not valid JSON: {exc}. "
                f"Pass '{name}' as an object, not a JSON-encoded string."
            )
    if not isinstance(value, dict):
        raise ValueError(
            f"'{name}' must be an object (dict) or a JSON-encoded object string; "
            f"got {type(value).__name__}."
        )
    return value


def handle_git_call(arguments: dict, project_root: str, strict: bool = False) -> dict:
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    raw_params = arguments.get("params") or arguments.get("p") or {}
    try:
        params = _ensure_dict(raw_params)
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        lines = ["## mcp-git", "", f"Project root: `{project_root}`", "", "Allowed subcommands:", ""]
        for name in sorted(set(list(SAFE_SUBCOMMANDS) + list(FILTERED_SUBCOMMANDS.keys()))):
            desc = SUBCOMMAND_DESCRIPTIONS.get(name, "")
            lines.append(f"- `{name}` — {desc}")
        return {"__raw_text__": "\n".join(lines)}

    if function in SAFE_SUBCOMMANDS:
        validator: Optional[Callable[[List[str]], None]] = None
    elif function in FILTERED_SUBCOMMANDS:
        validator = FILTERED_SUBCOMMANDS[function]
    else:
        return {"error": (
            f"git subcommand '{function}' is not on the read-only whitelist. "
            "Use the Bash tool for mutating operations."
        )}

    try:
        semantic_args, semantic_paths = _semantic_params_to_args(params)
    except ValueError as exc:
        return {"error": str(exc)}

    args = params.get("args", [])
    if args is None:
        args = []
    if isinstance(args, str):
        import shlex
        args = shlex.split(args)
    if not isinstance(args, list):
        return {"error": "params.args must be a list of strings"}
    args = [str(a) for a in args]
    args = semantic_args + args
    # Paths join HERE, before the validator — it has to judge the argv git will
    # actually get. See _append_path_positionals.
    args = _append_path_positionals(function, args, semantic_paths)

    if validator is not None:
        try:
            validator(args)
        except ValueError as exc:
            return {"error": str(exc)}

    cwd_param = params.get("cwd", ".")
    try:
        cwd = safe_path(project_root, cwd_param, strict)
    except ValueError as exc:
        return {"error": str(exc)}
    if not os.path.isdir(cwd):
        return {"error": f"cwd is not a directory: {cwd_param}"}

    # After the validator: it must judge the caller's argv, not ours.
    # `injected` is what the SERVER added — the reply has to disclose it, or a
    # bare `status` coming back in porcelain is unexplainable to the caller.
    injected: List[str] = []
    if function == "status":
        extended = _status_default_format(args)
        injected = extended[:len(extended) - len(args)]
        args = extended

    cmd = ["git", function] + args
    # Handlers run concurrently (McpServer.run), so a mutating argv takes the
    # repository lock for the duration of the spawn and a read-only one does not.
    # nullcontext keeps the two paths one statement rather than an acquire /
    # release pair straddling the except clauses below.
    guard = (_MUTATING_GIT_LOCK if _mutates_repo(function, args)
             else contextlib.nullcontext())
    try:
        with guard:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                stdin=subprocess.DEVNULL,   # git must never consume the MCP stream
                timeout=_run_timeout(params),
            )
    except FileNotFoundError:
        return {"error": "git executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"git {function} timed out"}
    except OSError as exc:
        return {"error": f"git {function} failed: {exc}"}

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    max_chars = params.get("max_answer_chars", DEFAULT_MAX_CHARS)
    truncated_stdout = False
    if max_chars > 0 and len(stdout) > max_chars:
        stdout = stdout[:max_chars]
        truncated_stdout = True

    cmd_str = "git " + function + ("" if not args else " " + " ".join(_quote_arg(a) for a in args))

    # The envelope states only what the caller could NOT know. It knows which
    # function it called and with what params — that is the tool call itself —
    # so a heading and a full command echo are pure cost on the success path.
    # What it cannot know: a non-default cwd, flags the server added, a non-zero
    # exit, and (when there is no stdout at all) whether the command succeeded.
    parts: List[str] = []
    if cwd != project_root:
        parts.append(f"_cwd: `{os.path.relpath(cwd, project_root)}`_")
    if result.returncode != 0:
        # The failure path is where verbosity pays: an exit code is only
        # diagnosable next to the argv that produced it. Full echo stays here.
        parts.append(f"`{cmd_str}` (exit {result.returncode})")
    elif injected:
        parts.append("_+ " + " ".join(_quote_arg(a) for a in injected) + "_")

    if stdout:
        parts.append(_md_fence(stdout) if _needs_fence(stdout)
                     else stdout.strip("\n"))
    elif not stderr and result.returncode == 0:
        # No stdout and success: the exit code IS the answer. This is the whole
        # point of `merge-base --is-ancestor`, `diff --quiet` and `apply
        # --check` — a placeholder saying "no output" throws that bit away and
        # charges 13 characters for it.
        parts.append("exit 0")
    if truncated_stdout:
        parts.append(f"_stdout truncated at {max_chars} chars_")
    if stderr.strip():
        parts.append(_md_fence(stderr.strip()))

    md = "\n".join(parts)
    is_error = result.returncode != 0 and not stdout
    if is_error:
        return {"error": md}
    return {"__raw_text__": md}


# ---------------------------------------------------------------------------
# MCP Server (same shape as mcp-purity)
# ---------------------------------------------------------------------------

GIT_CALL_TOOL = {
    "name": "git_call",
    "description": (
        "MANDATORY for ALL read-only git operations AND for the full `git stash` "
        "workflow. NEVER invoke these through Bash(\"git ...\") — `git_call` exists "
        "specifically to replace those calls. Using Bash for an operation that "
        "this tool already supports is a VIOLATION: it wastes a permission prompt, "
        "skips the Markdown wrapping, and bypasses the safety filters that block "
        "destructive flags on dual-use subcommands (branch, tag, remote, config).\n\n"
        "ALWAYS-VIA-git_call list — NEVER run these through Bash:\n"
        "  Bash(\"git status ...\")     -> function=\"status\"\n"
        "  Bash(\"git log ...\")        -> function=\"log\"\n"
        "  Bash(\"git diff ...\")       -> function=\"diff\"\n"
        "  Bash(\"git show ...\")       -> function=\"show\"\n"
        "  Bash(\"git blame ...\")      -> function=\"blame\"\n"
        "  Bash(\"git reflog ...\")     -> function=\"reflog\"\n"
        "  Bash(\"git rev-parse ...\")  -> function=\"rev-parse\"\n"
        "  Bash(\"git rev-list ...\")   -> function=\"rev-list\"\n"
        "  Bash(\"git ls-files ...\")   -> function=\"ls-files\"\n"
        "  Bash(\"git ls-tree ...\")    -> function=\"ls-tree\"\n"
        "  Bash(\"git ls-remote ...\")  -> function=\"ls-remote\"\n"
        "  Bash(\"git grep ...\")       -> function=\"grep\"\n"
        "  Bash(\"git describe ...\")   -> function=\"describe\"\n"
        "  Bash(\"git merge-base ...\") -> function=\"merge-base\"\n"
        "  Bash(\"git check-ignore ...\")                     -> function=\"check-ignore\"\n"
        "  Bash(\"git branch -l/-a/--contains/--merged ...\") -> function=\"branch\"\n"
        "  Bash(\"git tag -l/--contains/--merged ...\")       -> function=\"tag\"\n"
        "  Bash(\"git remote / git remote show / get-url\")  -> function=\"remote\"\n"
        "  Bash(\"git stash ...\") (ANY subcommand)           -> function=\"stash\"\n"
        "  Bash(\"git config --list/--get ...\")              -> function=\"config\"\n"
        "  Bash(\"git fetch --dry-run\")                      -> function=\"fetch\"\n"
        "  Bash(\"git apply --check ...\")                    -> function=\"apply\"\n"
        "  Bash(\"git hash-object <file>\")                   -> function=\"hash-object\"\n\n"
        "Use Bash ONLY for the mutating ops this tool does NOT expose (commit, "
        "add, push, reset, checkout, merge, rebase, branch -d/-m, tag -a/-d, "
        "remote add/set-url, config <name> <value>, fetch without --dry-run, "
        "apply without --check). Everything else MUST go through git_call.\n\n"
        "COMMIT COMMAND CHAIN — single Bash call, NEVER split add + commit:\n"
        "  Stage the explicit file list and create the commit in ONE chained\n"
        "  Bash call. Feed the message to `git commit -F -` through a quoted\n"
        "  HEREDOC on stdin — NOT `-m \"$(cat <<'EOF'...)\"`, whose `cat` some\n"
        "  sandboxes forbid — so multi-line messages, backticks, quotes, and\n"
        "  `$` survive shell expansion untouched.\n\n"
        "  Pattern (copy verbatim, substitute <files> and <message>):\n"
        "    git add <file1> <file2> <...> && git commit -F - <<'EOF'\n"
        "    <subject line>\n"
        "    \n"
        "    <body paragraph, optional>\n"
        "    EOF\n\n"
        "  Rules:\n"
        "    - ONE Bash call, chained with `&&` — never two separate calls.\n"
        "    - Stage files explicitly by name; do NOT use `git add -A` / `.`\n"
        "      (sweeps in unrelated changes, secrets, build artifacts).\n"
        "    - HEREDOC delimiter MUST be quoted: `<<'EOF'` (single quotes) to\n"
        "      disable variable / backtick / command substitution inside the\n"
        "      message. Unquoted `<<EOF` will expand `$var` and `` `cmd` ``.\n"
        "    - `git commit -F -` reads the message from STDIN (the heredoc\n"
        "      above), NOT from disk — that is the required channel. Do NOT\n"
        "      instead route it through a real file: no `-F <file>` /\n"
        "      `--file=<file>`, and no `Write`/`create_text_file` of a scratch\n"
        "      commit-message file (e.g. `.claude/tmp/commit-msg.txt`) first.\n"
        "      The message must never touch disk — a file detour is slower,\n"
        "      leaks a stray artifact, and is FORBIDDEN.\n"
        "    - Do NOT use `git commit -am` (re-stages tracked files only and\n"
        "      misses new files) or `--amend` (rewrites history — only when\n"
        "      the user explicitly asks).\n"
        "    - Do NOT pass `--no-verify`, `--no-gpg-sign`, or any hook bypass\n"
        "      unless the user explicitly requests it.\n\n"
        "Params: args (CLI args list), cwd (sub-repo, default project root), "
        "max_answer_chars (default 100000), timeout (default 60s, capped at 300s). "
        "Markdown output.\n\n"
        "NAMED PARAMS (alternative to args). The SAME key set applies to EVERY\n"
        "function — there is no per-subcommand schema — so a key is simply\n"
        "meaningless where git takes no such argument (log with `remote` hands git\n"
        "a revision named 'origin'). Use the key that matches the slot git wants:\n"
        "  - booleans -> flags: stat=true gives --stat\n"
        "  - strings/numbers -> --key=value: max_count=10 gives --max-count=10\n"
        "  - `_` becomes `-` in the flag name\n"
        "  - POSITIONAL slots each need a dedicated key. Emitted after the flags,\n"
        "    repository first, then revisions, then paths behind a `--`:\n"
        "      revision or revision RANGE: `range` (canonical). Aliases:\n"
        "        revision_range, rev_range, rev, revs, revision, revisions, ref,\n"
        "        refs, commit, commits, object, tree_ish, treeish.\n"
        "      repository / remote name or URL: `remote` (canonical). Aliases:\n"
        "        repository, repo. git puts it BEFORE the refs\n"
        "        (git ls-remote [<options>] [<repository> [<refs>...]]).\n"
        "      file paths: `path` / `paths` / `pathspec`. These are emitted LAST,\n"
        "        after a `--`, so a path that no longer exists in the working tree\n"
        "        still works (log of a DELETED file answers empty instead of\n"
        "        'fatal: ambiguous argument'). Use these keys rather than writing\n"
        "        the path into args, where git may read it as a revision.\n"
        "    Revision and repository values starting with '-' are refused (no\n"
        "    flag smuggling). Lists are allowed and checked per element.\n"
        "    A positional key given a BOOLEAN becomes a FLAG instead: refs=true\n"
        "    is git's own ls-remote --refs (hide peeled tags), not a ref named\n"
        "    'true'.\n"
        "  - ANY OTHER key is forwarded verbatim as `--key[=value]`, so an\n"
        "    invented or misspelled param name reaches git as an unknown flag.\n\n"
        "Examples:\n"
        "  function=\"log\", params={\"args\":[\"--oneline\",\"-20\"]}\n"
        "  function=\"log\", params={\"range\":\"master..HEAD\",\"stat\":true}\n"
        "    -> git log --stat master..HEAD   (diffstat-annotated range log)\n"
        "  function=\"ls-remote\", params={\"remote\":\"origin\",\"heads\":true}\n"
        "    -> git ls-remote --heads origin\n"
        "  function=\"log\", params={\"range\":\"master..HEAD\",\"paths\":\"src/x.c\"}\n"
        "    -> git log master..HEAD -- src/x.c   (one file's history in a range)\n"
        "Call without 'function' for full allowlist."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {"type": "string", "description": "Git subcommand."},
            "params":   {"type": "object", "description": "See main description."},
        },
    },
}


# How many tool calls may be in flight at once. The stdin reader owns a thread of
# its own, outside this pool, so saturating it delays queued CALLS and can never
# stop the server from READING — which is the entire point of the split in run().
MAX_INFLIGHT_REQUESTS = 8


class McpServer:
    def __init__(self, project_root: str, strict: bool = False):
        self.project_root = os.path.realpath(project_root)
        self.strict = strict

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, project_root=%s", self.project_root)
        # TWO executors, and one task per request, on purpose. This loop used to
        # call the handler INLINE — `self._handle_message(msg)`, no await, no
        # executor — on the very thread that next had to await sys.stdin.readline.
        # A running git command therefore froze the whole server for its duration:
        # every other request sat unread in the pipe, timed out client-side (~60s)
        # and was then answered in a burst against ids the client had already
        # abandoned. From the caller's chair that is a dead server, and a restart
        # was the only lever. A local git read is milliseconds, so this was the
        # mild end of the fleet — but `timeout` is caller-supplied (see
        # MAX_TIMEOUT_SEC, which now bounds it), so the freeze had no ceiling
        # the server controlled.
        #
        # ONE POOL WOULD NOT DO. If the readline shared the handler pool, eight
        # slow calls would occupy every worker and the readline would sit in the
        # pool's QUEUE — reintroducing exactly the deafness above, just with more
        # steps. The reader's single dedicated thread is what makes reading
        # unconditional.
        #
        # Concurrent handlers are safe here, and this was audited rather than
        # assumed: the module declares no `global` and holds no mutable state —
        # every collection at module level (SAFE_SUBCOMMANDS, FILTERED_SUBCOMMANDS,
        # SUBCOMMAND_DESCRIPTIONS, the _*_KEYS sets, _SHELL_SAFE_CHARS,
        # GIT_CALL_TOOL) is built once at import and only ever read; there is no
        # cache, no memoised repo root (safe_path recomputes per call) and no
        # resolved-repo handle; every list a handler appends to is a function
        # local; and self.project_root / self.strict are written in __init__
        # before this loop starts and never again. What IS shared is the
        # repository on disk, which Python statelessness cannot protect — so the
        # mutating stash argvs serialize on _MUTATING_GIT_LOCK at the spawn site
        # while the read-only commands run concurrently.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="git-stdin")
        workers = ThreadPoolExecutor(max_workers=MAX_INFLIGHT_REQUESTS,
                                     thread_name_prefix="git-call")
        inflight: set = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    log.warning("stdin read failed, shutting down: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    # Answering is not optional: a bare `continue` here left the
                    # caller's request id unanswered until it timed out.
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    # `5` is valid JSON. It used to reach msg.get() and take the
                    # process down with an AttributeError that escaped run() —
                    # and an MCP client does not respawn a dead stdio server.
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue

                log.debug("← %s", json.dumps(msg)[:200])
                task = loop.create_task(self._serve(loop, workers, msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
        finally:
            for task in inflight:
                task.cancel()
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)
            log.info("MCP server shutting down")

    async def _serve(self, loop, workers: ThreadPoolExecutor, msg: dict) -> None:
        """One request, from dispatch to written reply. Runs as its own task."""
        try:
            response = await loop.run_in_executor(workers, self._handle_message, msg)
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            self._write(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message.

        Called only from the event-loop thread: handlers run in the worker pool,
        but `_serve` resumes on the loop after its await, so concurrent replies
        cannot interleave and this needs no lock.
        """
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {exc}"))
        log.debug("→ %s", out[:200])
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            # Unguarded, this escaped run() and killed the process.
            log.warning("stdout write failed: %s", exc)

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mcp-git", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [GIT_CALL_TOOL]})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name != "git_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        if not isinstance(arguments, dict):
            return self._result(msg_id, {
                "content": [{"type": "text", "text":
                    f"'arguments' must be an object; got {type(arguments).__name__}."}],
                "isError": True,
            })
        try:
            result = handle_git_call(arguments, self.project_root, self.strict)
        except Exception as exc:
            log.exception("Unhandled exception in handle_git_call")
            result = {"error": f"Internal server error: {type(exc).__name__}: {exc}"}
        is_error = "error" in result
        text = result.get("__raw_text__") or result.get("error", "")
        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    if "--list" in sys.argv:
        print("mcp-git — allowed read-only subcommands:\n")
        for name in sorted(SAFE_SUBCOMMANDS):
            desc = SUBCOMMAND_DESCRIPTIONS.get(name, "")
            print(f"  {name:18s} {desc}")
        print("\nFiltered (arg-level checks):")
        for name in sorted(FILTERED_SUBCOMMANDS.keys()):
            desc = SUBCOMMAND_DESCRIPTIONS.get(name, "")
            print(f"  {name:18s} {desc}")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="MCP-Git: Read-only git operations MCP server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--strict", action="store_true", help="Reject paths outside project root")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()

    level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
    log_handlers: list = []
    if args.log_file:
        log_handlers.append(logging.FileHandler(args.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    if not os.path.isdir(args.project_root):
        print(f"Error: project root is not a directory: {args.project_root}", file=sys.stderr)
        sys.exit(1)

    server = McpServer(args.project_root, strict=args.strict)
    asyncio.run(server.run())
    # stdin is closed, so the client is gone. Handler threads live in the
    # server's own executors rather than the loop's default one, so asyncio does
    # not join them — but concurrent.futures registers an atexit hook that
    # would, and one handler mid-command would hold this process open for the
    # rest of its (now clamped) timeout budget. Every reply is flushed as it is
    # written and logging flushes per record, so there is nothing left to drain.
    os._exit(0)


if __name__ == "__main__":
    main()
