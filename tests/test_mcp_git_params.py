#!/usr/bin/env python3
"""Offline suite for the mcp-git params -> git argv conversion (166 cases, A-I).

Drives `handle_git_call` from Scripts/mcp-git.py IN-PROCESS with the module's
`subprocess` reference swapped for a stub, so:

  * no git process is ever spawned -> no network (ls-remote cases are safe),
    no repo mutation, no dependence on the checkout's history;
  * the stub records the exact argv the server built, and the reply still
    contains the human-readable `git ...` line the server echoes back, so both
    the CONVERSION and the DISPLAY can be asserted on.

Coverage by group:
  A  every `range` alias resolves to the SAME positional argv (+ list forms,
     + the alias set itself is pinned)
  B  leading-dash / empty rejection for the revision AND repository slots,
     per element of a list value; plus the values that must stay ACCEPTED
     (`^master`, `:/fix typo`, `HEAD@{2 days ago}`, a URL)
  C  the `--key=value` fall-through, pinned case by case.  The traps are
     recorded as INFO: they are deliberate behaviour, not a feature.
  D  ORDER: flags first, then the repository, then revisions/paths -- and the
     order does NOT follow the caller's dict order for the repository
  E  the new `remote` / `repository` / `repo` key on ls-remote and fetch
  F  _quote_arg display fidelity, end to end: the exact rendering of the echoed
     line, plus what a REAL shell makes of it (`printf "%s\\0" <line>` under
     every shell in SHELLS -- bash and zsh by name, because those are what a
     human pastes into).  The words the shell yields must be byte-identical to
     the argv git actually received.
  G  regression: the two live failures this suite was written for
  H  offline contract + no-regression plumbing (validators still fire, no
     spawn on any rejection, no __pycache__ written)
  I  _quote_arg as a PROPERTY, stdlib only, no shell: for a nasty corpus,
     shlex.split(_quote_arg(s)) == [s] -- in plain POSIX mode and in the
     stricter punctuation_chars mode -- plus the other direction of the goal,
     that ordinary git arguments come back UNQUOTED.  Group I also measures how
     much each oracle is actually worth by running both round trips against the
     PRE-FIX rendering: an oracle that cannot tell the old bug from the fix
     protects nothing, and that limit belongs in the suite rather than in prose.

Three layers, deliberately, because each sees something the others cannot:
  exact rendering (F)  pins the policy; catches every class
  shlex round trip (I) permanent, portable, no shell needed; models quoting and
                       word splitting only -- and provably misses globs, brace
                       expansion, `!`, leading `~`/`=`, `^`
  real shells (F)      the only oracle that models EXPANSION, which is exactly
                       what the leading-character carve-out is about

Usage:
  python3 tests/test_mcp_git_params.py
  python3 tests/test_mcp_git_params.py --brief
Exit code 0 iff every non-informational case passes.
"""

import os
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_git_params"
SERVER = H.repo_path("Scripts", "mcp-git.py")

# The documented alias list, in the order the tool description spells it out.
RANGE_ALIASES = ["range", "revision_range", "rev_range", "rev", "revs",
                 "revision", "revisions", "ref", "commit", "commits",
                 "object", "tree_ish", "treeish"]
REPO_ALIASES = ["remote", "repository", "repo"]

REV_REJECT = "must be a revision or range"
REPO_REJECT = "must be a remote name or URL"

# Shells the echoed command line is replayed under, most human-relevant first.
# bash and zsh are named explicitly: a person copy-pastes into their login
# shell, and the leading-`=` claim in _quote_arg's docstring is specifically a
# zsh property, so measuring /bin/sh alone would not answer it.
SHELLS = [p for p in ("/bin/bash", "/bin/zsh", "/bin/sh") if os.path.exists(p)]


def _pre_fix_quote_arg(a):
    """The rendering Scripts/mcp-git.py used BEFORE Fix 2.

    Kept only as a yardstick for group I: an oracle that cannot distinguish this
    from the current implementation is not protecting anything, and that is a
    fact worth machine-checking instead of asserting in a comment.
    """
    if not a or any(c in a for c in " \t\"'\\$`"):
        return "'" + a.replace("'", "'\\''") + "'"
    return a


def posix_split(rendered):
    """shlex.split: quoting + whitespace splitting, POSIX mode."""
    return shlex.split(rendered)


def operator_split(rendered):
    """shlex with punctuation_chars=True: also splits on ; | & < > ( ).

    shlex.split() does not expose punctuation_chars, so the lexer is built by
    hand.  This is the stricter of the two stdlib oracles and the only one that
    notices an unquoted shell operator.
    """
    lex = shlex.shlex(rendered, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


# ---------------------------------------------------------------------------
# offline plumbing: the server never gets to spawn git
# ---------------------------------------------------------------------------

class StubSubprocess:
    """Stands in for the `subprocess` module inside the module under test.

    Only the three attributes mcp-git touches are provided, so an unexpected use
    of any other subprocess API shows up as an AttributeError instead of
    silently reaching the real one.
    """

    DEVNULL = subprocess.DEVNULL
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.calls = []
        self.returncode = 0
        self.stdout = "STUB-STDOUT\n"
        self.stderr = ""

    def run(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), self.returncode,
                                           self.stdout, self.stderr)


def extract_cmdline(text):
    """Pull the `git ...` line out of the Markdown reply.

    Delimiter search rather than a regex: an argument may legitimately contain a
    backtick or a newline, and `rfind` on the fixed ``` (exit ``` suffix is
    unambiguous where a greedy/lazy regex would not be.
    """
    start = text.find("`git ")
    end = text.rfind("` (exit ")
    if start < 0 or end < start:
        return ""
    return text[start + 1:end]


class Driver:
    def __init__(self):
        self.mod = H.load_module_from_path("mcp_git_under_test", SERVER)
        self.stub = StubSubprocess()
        self.mod.subprocess = self.stub

    def call(self, function, params, rc=0, stdout="STUB-STDOUT\n", stderr=""):
        self.stub.returncode = rc
        self.stub.stdout = stdout
        self.stub.stderr = stderr
        before = len(self.stub.calls)
        result = self.mod.handle_git_call(
            {"function": function, "params": params}, H.REPO_ROOT)
        spawned = self.stub.calls[before:]
        text = result.get("__raw_text__") or result.get("error") or ""
        return {
            "error": "error" in result,
            "text": text,
            "argv": spawned[-1] if spawned else None,
            "nspawn": len(spawned),
            "cmdline": extract_cmdline(text),
        }


def shell_words(shell, cmdline, cwd):
    """Ask a real shell what words the echoed command line expands to.

    Returns (words, stderr) or (None, reason).  The shell runs in a throwaway
    directory precisely because a quoting bug is what is being hunted: an
    unquoted `>` or `*` in the echoed line would otherwise act on the repo.
    Expansion is the half `shlex` cannot model, so this is not redundant with
    group I -- it is the only oracle that can see a glob or a tilde.
    """
    try:
        proc = subprocess.run([shell, "-c", 'printf "%s\\0" ' + cmdline],
                              capture_output=True, text=True, timeout=15,
                              cwd=cwd, env=H.child_env())
    except Exception as exc:                                # pragma: no cover
        return None, "%s: %s" % (type(exc).__name__, exc)
    words = proc.stdout.split("\0")
    if words and words[-1] == "":
        words.pop()
    return words, (proc.stderr or "").strip()


# ---------------------------------------------------------------------------
# assertion helpers
# ---------------------------------------------------------------------------

def check(suite, drv, group, cid, function, params, argv=None, cmdline=None,
          error=None, must=(), must_not=(), spawns=1, status=None, note="",
          rc=None, stdout="STUB-STDOUT\n"):
    """One conversion case.  `error` set => the call must be REFUSED, unspawned.

    The success path no longer echoes the command line — the caller already knows
    what it sent — so a case that asserts on the ECHO must exercise the path that
    still carries it: a non-zero exit. The stub returns stdout regardless, so
    `is_error` stays False and no other assertion in the case shifts. Pass `rc`
    explicitly to override.
    """
    if rc is None:
        rc = 1 if cmdline is not None else 0
    rep = drv.call(function, params, rc=rc, stdout=stdout)
    problems = []
    detail = ["function    : %r" % function, "params      : %r" % (params,)]
    if note:
        detail.append("note        : %s" % note)

    if error is not None:
        detail.append("verdict     : %s"
                      % ("REFUSED before spawn" if rep["error"] and not rep["nspawn"]
                         else "accepted"))
        detail.append("reply       : %s" % " ".join(rep["text"].split())[:200])
        if not rep["error"]:
            problems.append("expected refusal, got argv %r" % (rep["argv"],))
        elif error not in rep["text"]:
            problems.append("error text missing %r" % error)
        if rep["nspawn"]:
            problems.append("git SPAWNED despite refusal: %r" % (rep["argv"],))
    else:
        detail.append("argv        : %r" % (rep["argv"],))
        detail.append("cmdline     : %s" % rep["cmdline"])
        if rep["error"]:
            problems.append("unexpected error: %s"
                            % " ".join(rep["text"].split())[:200])
        if rep["nspawn"] != spawns:
            problems.append("expected %d spawn(s), got %d" % (spawns, rep["nspawn"]))
        if argv is not None and rep["argv"] != argv:
            problems.append("argv %r != expected %r" % (rep["argv"], argv))
        if cmdline is not None and rep["cmdline"] != cmdline:
            problems.append("cmdline %r != expected %r" % (rep["cmdline"], cmdline))

    for s in must:
        if s not in rep["text"]:
            problems.append("MISSING %r" % s)
    for s in must_not:
        if s in rep["text"]:
            problems.append("UNEXPECTED %r" % s)

    if status is None:
        status = H.FAIL if problems else H.PASS
    brief = "%s | %s%s" % (status, cid, " | " + "; ".join(problems) if problems else "")
    suite.record(group, cid, problems, status=status, detail=detail, brief=brief,
                 text=rep["text"])
    return rep


def identity(suite, group, cid, argvs, label):
    """Assert a set of alias spellings all produced the same argv."""
    distinct = sorted({tuple(a) for a in argvs if a is not None})
    problems = []
    if len(distinct) != 1:
        problems.append("%d distinct argvs: %r" % (len(distinct), distinct))
    if len(argvs) != len([a for a in argvs if a is not None]):
        problems.append("some spelling produced no argv at all")
    suite.record(group, cid, problems,
                 detail=["%s (%d spellings)" % (label, len(argvs)),
                         "argv        : %r" % (list(distinct[0]) if distinct else None)],
                 brief="%s | %s" % (H.FAIL if problems else H.PASS, cid))


def pinned_set(suite, group, cid, actual, expected, label):
    problems = []
    if set(actual) != set(expected):
        problems.append("%s drifted: extra=%r missing=%r"
                        % (label, sorted(set(actual) - set(expected)),
                           sorted(set(expected) - set(actual))))
    suite.record(group, cid, problems,
                 detail=["%s = %r" % (label, sorted(actual))],
                 brief="%s | %s" % (H.FAIL if problems else H.PASS, cid))


def quoting(suite, drv, cid, arg, rendered, shell_cwd, note=""):
    """One _quote_arg case: exact rendering PLUS a real-shell round trip.

    rc=1 because the echoed command line — the thing under test here — is now
    only emitted on the failure path. `_quote_arg`'s rendering is identical
    either way; this just picks the reply that still shows it.
    """
    rep = drv.call("log", {"args": [arg]}, rc=1)
    want_line = "git log " + rendered
    want_argv = ["git", "log", arg]
    problems = []
    detail = ["arg         : %r" % arg,
              "rendered    : %s" % rep["cmdline"],
              "expected    : %s" % want_line]
    if note:
        detail.append("note        : %s" % note)
    if rep["error"]:
        problems.append("unexpected error: %s" % " ".join(rep["text"].split())[:160])
    if rep["argv"] != want_argv:
        problems.append("argv %r != %r (git did NOT get the value verbatim)"
                        % (rep["argv"], want_argv))
    if rep["cmdline"] != want_line:
        problems.append("rendered %r != expected %r" % (rep["cmdline"], want_line))

    if not SHELLS:
        detail.append("shell oracle: SKIPPED (no shell found)")
    for shell in SHELLS:
        words, err = shell_words(shell, rep["cmdline"], shell_cwd)
        if words is None:
            detail.append("%-10s : SKIPPED (%s)" % (os.path.basename(shell), err))
            continue
        detail.append("%-10s : %r%s" % (os.path.basename(shell), words,
                                        "  stderr=%r" % err[:60] if err else ""))
        if words != want_argv:
            problems.append("%s re-expands the echoed line to %r, not %r"
                            % (shell, words, want_argv))
        if err:
            problems.append("%s complained: %s" % (shell, err[:160]))

    status = H.FAIL if problems else H.PASS
    suite.record("F", cid, problems, status=status, detail=detail,
                 brief="%s | %s | %r -> %s" % (status, cid, arg, rep["cmdline"]),
                 text=rep["text"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-git named params -> git argv (offline)",
                    opts=opts, mode="grouped")
    work = H.TempWorkspace("ph-mcp-git-params-", keep=opts.keep)
    pyc_before = H.pycache_snapshot()

    try:
        drv = Driver()
        sh_cwd = work.subdir("shell")

        # ============ A: every range alias hits the same slot ============
        argvs = []
        for key in RANGE_ALIASES:
            rep = check(suite, drv, "A", "alias-" + key, "log",
                        {key: "HEAD~7..HEAD", "stat": True},
                        argv=["git", "log", "--stat", "HEAD~7..HEAD"],
                        cmdline="git log --stat HEAD~7..HEAD")
            argvs.append(rep["argv"])
        identity(suite, "A", "all-range-aliases-identical", argvs,
                 "every documented range alias -> the same positional")
        pinned_set(suite, "A", "revision-key-set", drv.mod._REVISION_KEYS,
                   RANGE_ALIASES, "_REVISION_KEYS")
        check(suite, drv, "A", "range-list-single", "log",
              {"range": ["HEAD~7..HEAD"], "stat": True},
              argv=["git", "log", "--stat", "HEAD~7..HEAD"])
        check(suite, drv, "A", "range-list-two-endpoints", "log",
              {"range": ["^master", "HEAD"]},
              argv=["git", "log", "^master", "HEAD"],
              cmdline="git log '^master' HEAD")
        check(suite, drv, "A", "range-non-string-coerced", "log", {"range": 1234},
              argv=["git", "log", "1234"],
              note="non-string positionals are str()-ed, not rejected")

        # ============ B: leading-dash / empty refusal ============
        for cid, params in [
            ("rev-upload-pack", {"range": "--upload-pack=/tmp/x"}),
            ("rev-short-S", {"range": "-S"}),
            ("rev-bundled-short", {"range": "-wt"}),
            ("rev-looks-like-flag", {"range": "--stat"}),
            ("rev-key-ref", {"ref": "--upload-pack=/tmp/x"}),
            ("rev-key-object", {"object": "-x"}),
            ("rev-key-commit", {"commit": "--foo"}),
            ("rev-list-2nd-element", {"range": ["HEAD", "-S"]}),
            ("rev-leading-whitespace", {"range": "   --upload-pack=/tmp/x"}),
        ]:
            check(suite, drv, "B", cid, "log", params, error=REV_REJECT)
        for cid, params in [("rev-empty", {"range": ""}),
                            ("rev-whitespace-only", {"range": "   "})]:
            check(suite, drv, "B", cid, "log", params, error="must not be empty")

        for cid, params in [
            ("repo-upload-pack", {"remote": "--upload-pack=/tmp/x"}),
            ("repo-key-repository", {"repository": "-x"}),
            ("repo-key-repo", {"repo": "--foo"}),
            ("repo-list-2nd-element", {"remote": ["origin", "--upload-pack=/tmp/x"]}),
        ]:
            check(suite, drv, "B", cid, "ls-remote", params, error=REPO_REJECT)
        check(suite, drv, "B", "repo-empty", "ls-remote", {"remote": ""},
              error="must not be empty")

        # values that MUST stay accepted -- the guard is a leading dash, nothing more
        check(suite, drv, "B", "ok-caret-exclude", "log", {"range": "^master"},
              argv=["git", "log", "^master"], cmdline="git log '^master'")
        check(suite, drv, "B", "ok-search-syntax", "log", {"range": ":/fix typo"},
              argv=["git", "log", ":/fix typo"], cmdline="git log ':/fix typo'")
        check(suite, drv, "B", "ok-reflog-date", "log",
              {"range": "HEAD@{2 days ago}"},
              argv=["git", "log", "HEAD@{2 days ago}"],
              cmdline="git log 'HEAD@{2 days ago}'")
        check(suite, drv, "B", "ok-repo-url", "ls-remote",
              {"remote": "https://example.invalid/x.git"},
              argv=["git", "ls-remote", "https://example.invalid/x.git"])

        # ====== C: the --key=value fall-through, pinned (traps = INFO) ======
        for cid, fn, params, want, note in [
            ("trap-raw_exit", "log", {"raw_exit": True},
             ["git", "log", "--raw-exit"],
             "the historical ambush: a stray meta-ish key becomes a flag"),
            ("trap-remote_name", "ls-remote", {"remote_name": "github"},
             ["git", "ls-remote", "--remote-name=github"],
             "near-miss of the new `remote` key"),
            ("trap-reference", "log", {"reference": "HEAD"},
             ["git", "log", "--reference=HEAD"],
             "`reference` is NOT a revision alias"),
            ("trap-revison-typo", "log", {"revison": "HEAD"},
             ["git", "log", "--revison=HEAD"], "one typo, one bogus flag"),
            ("trap-rangee-typo", "log", {"rangee": "A..B"},
             ["git", "log", "--rangee=A..B"], "one typo, one bogus flag"),
            ("trap-branch", "log", {"branch": "master"},
             ["git", "log", "--branch=master"],
             "no `branch` positional key: git log has no --branch"),
            ("trap-float-truncation", "log", {"max_count": 3.0},
             ["git", "log", "--max-count=3"],
             "a whole float is emitted as an int"),
        ]:
            check(suite, drv, "C", cid, fn, params, argv=want, status=H.INFO,
                  note=note)

        for cid, fn, params, want in [
            ("flag-bool-true", "log", {"stat": True}, ["git", "log", "--stat"]),
            ("flag-bool-false-omitted", "log", {"stat": False}, ["git", "log"]),
            ("flag-int", "log", {"max_count": 10},
             ["git", "log", "--max-count=10"]),
            ("flag-underscore-to-dash", "log", {"no_merges": True},
             ["git", "log", "--no-merges"]),
            ("flag-string-single-argv-element", "log", {"pretty": "%h %s"},
             ["git", "log", "--pretty=%h %s"]),
            ("flag-list-repeats", "log", {"grep": ["a", "b"]},
             ["git", "log", "--grep=a", "--grep=b"]),
            ("flag-refs-not-a-revision-key", "ls-remote", {"refs": True},
             ["git", "ls-remote", "--refs"]),
            ("flag-remotes-not-a-repo-key", "log", {"remotes": True},
             ["git", "log", "--remotes"]),
        ]:
            check(suite, drv, "C", cid, fn, params, argv=want)
        check(suite, drv, "C", "meta-keys-not-forwarded", "log",
              {"cwd": ".", "timeout": 30, "max_answer_chars": 500, "stat": True},
              argv=["git", "log", "--stat"])

        # ============ D: flag / positional ORDER ============
        check(suite, drv, "D", "flags-before-positional", "log",
              {"range": "A..B", "stat": True, "max_count": 3},
              argv=["git", "log", "--stat", "--max-count=3", "A..B"],
              note="range written FIRST, still emitted last")
        check(suite, drv, "D", "flags-before-positional-reversed", "log",
              {"stat": True, "range": "A..B"},
              argv=["git", "log", "--stat", "A..B"])
        check(suite, drv, "D", "lsremote-repo-then-refs", "ls-remote",
              {"remote": "origin", "heads": True, "ref": "refs/heads/master"},
              argv=["git", "ls-remote", "--heads", "origin", "refs/heads/master"],
              cmdline="git ls-remote --heads origin refs/heads/master")
        check(suite, drv, "D", "lsremote-repo-first-despite-dict-order", "ls-remote",
              {"ref": "refs/heads/master", "remote": "origin"},
              argv=["git", "ls-remote", "origin", "refs/heads/master"],
              note="ref written first; the repository still precedes it")
        check(suite, drv, "D", "lsremote-flag-then-repo-alias", "ls-remote",
              {"tags": True, "repo": "origin"},
              argv=["git", "ls-remote", "--tags", "origin"])
        check(suite, drv, "D", "rev-then-path", "log",
              {"range": "A..B", "path": "src/x.c"},
              argv=["git", "log", "A..B", "src/x.c"])
        check(suite, drv, "D", "path-then-rev-follows-param-order", "log",
              {"path": "src/x.c", "range": "A..B"},
              argv=["git", "log", "src/x.c", "A..B"], status=H.INFO,
              note="revisions and paths keep the caller's order and get NO `--` "
                   "separator; ambiguous names can be misread by git")
        check(suite, drv, "D", "two-revision-keys-keep-order", "merge-base",
              {"revision": "A", "ref": "B"}, argv=["git", "merge-base", "A", "B"])
        check(suite, drv, "D", "semantic-args-precede-params-args", "log",
              {"range": "A..B", "args": ["--oneline"]},
              argv=["git", "log", "A..B", "--oneline"], status=H.INFO,
              note="named params are prepended to params.args, so a positional "
                   "can end up before a raw flag (git tolerates it)")

        # ============ E: the new repository slot ============
        repo_argvs = []
        for key in REPO_ALIASES:
            rep = check(suite, drv, "E", "lsremote-" + key, "ls-remote",
                        {key: "github", "heads": True},
                        argv=["git", "ls-remote", "--heads", "github"],
                        cmdline="git ls-remote --heads github",
                        must_not=["--%s=github" % key.replace("_", "-")])
            repo_argvs.append(rep["argv"])
        identity(suite, "E", "all-repo-aliases-identical", repo_argvs,
                 "every repository alias -> the same positional")
        pinned_set(suite, "E", "repo-key-set", drv.mod._REPO_KEYS, REPO_ALIASES,
                   "_REPO_KEYS")
        check(suite, drv, "E", "lsremote-repo-only", "ls-remote",
              {"remote": "github"}, argv=["git", "ls-remote", "github"],
              cmdline="git ls-remote github")
        check(suite, drv, "E", "lsremote-repo-plus-ref-list", "ls-remote",
              {"remote": "github", "ref": ["refs/heads/master"]},
              argv=["git", "ls-remote", "github", "refs/heads/master"])
        check(suite, drv, "E", "lsremote-legacy-ref-smuggling-still-works",
              "ls-remote", {"ref": ["github", "refs/heads/master"]},
              argv=["git", "ls-remote", "github", "refs/heads/master"],
              note="the only spelling that worked before Fix 1; must not regress")
        check(suite, drv, "E", "fetch-dry-run-with-remote", "fetch",
              {"remote": "origin", "dry_run": True},
              argv=["git", "fetch", "--dry-run", "origin"],
              cmdline="git fetch --dry-run origin",
              note="fetch takes <repository> positionally too, same slot order")
        check(suite, drv, "E", "fetch-without-dry-run-still-refused", "fetch",
              {"remote": "origin"}, error="only allowed with --dry-run")
        check(suite, drv, "E", "repo-key-on-log-is-just-a-revision", "log",
              {"remote": "origin"}, argv=["git", "log", "origin"], status=H.INFO,
              note="global key scope: no per-subcommand schema, so git reads it "
                   "as a revision and reports its own error")

        # ============ F: _quote_arg display fidelity ============
        NEEDS_QUOTING = [
            ("meta-semicolon", "a;b", "'a;b'"),
            ("meta-pipe", "a|b", "'a|b'"),
            ("meta-ampersand", "a&b", "'a&b'"),
            ("meta-redirect-in", "a<b", "'a<b'"),
            ("meta-redirect-out", "a>b", "'a>b'"),
            ("meta-parens", "(a)", "'(a)'"),
            ("glob-star", "refs/heads/*", "'refs/heads/*'"),
            ("glob-question", "a?b", "'a?b'"),
            ("glob-class", "[ab]", "'[ab]'"),
            ("brace-expansion", "a{b,c}", "'a{b,c}'"),
            ("bang", "a!b", "'a!b'"),
            ("leading-hash", "#comment", "'#comment'"),
            ("leading-tilde", "~/x", "'~/x'"),
            ("leading-equals", "=x", "'=x'"),
            ("leading-caret", "^master", "'^master'"),
            ("newline", "a\nb", "'a\nb'"),
            ("tab", "a\tb", "'a\tb'"),
            ("empty-string", "", "''"),
            ("space", "a b", "'a b'"),
            ("dollar", "$HOME", "'$HOME'"),
            ("backtick", "`id`", "'`id`'"),
            ("single-quote", "it's", "'it'\\''s'"),
            ("double-quote", 'a"b', "'a\"b'"),
            ("backslash", "a\\b", "'a\\b'"),
        ]
        for cid, arg, rendered in NEEDS_QUOTING:
            quoting(suite, drv, cid, arg, rendered, sh_cwd)

        STAYS_BARE = [
            "--oneline", "-20", "master..HEAD", "HEAD~7..HEAD", "--max-count=10",
            "src/dir/file.c", "--pretty=format:%h", "user@host,x+y",
            "refs/heads/master", "50%", "origin", "v1.2.3_rc-4",
        ]
        for arg in STAYS_BARE:
            quoting(suite, drv, "bare-" + arg, arg, arg, sh_cwd,
                    note="common case: must stay readable")
        rep = drv.call("log", {"args": ["--grep", "a b; ls", "master..HEAD"]},
                       rc=1)
        want = "git log --grep 'a b; ls' master..HEAD"
        want_words = ["git", "log", "--grep", "a b; ls", "master..HEAD"]
        problems = []
        detail = ["cmdline     : %s" % rep["cmdline"]]
        if rep["cmdline"] != want:
            problems.append("cmdline %r != %r" % (rep["cmdline"], want))
        for shell in SHELLS:
            words, err = shell_words(shell, rep["cmdline"], sh_cwd)
            detail.append("%-10s : %r" % (os.path.basename(shell), words))
            if words is not None and words != want_words:
                problems.append("%s expands to %r" % (shell, words))
        suite.record("F", "multi-arg-line", problems, detail=detail,
                     brief="%s | multi-arg-line"
                           % (H.FAIL if problems else H.PASS))
        suite.note("      shells exercised: %s"
                   % (", ".join(SHELLS) if SHELLS else "NONE"))

        # ============ G: the two live failures ============
        check(suite, drv, "G", "live-lsremote-remote-github", "ls-remote",
              {"remote": "github", "heads": True},
              argv=["git", "ls-remote", "--heads", "github"],
              cmdline="git ls-remote --heads github",
              must_not=["--remote=github", "unknown option"],
              note="was: git ls-remote --remote=github --heads -> exit 129 "
                   "\"error: unknown option `remote=github'\"")
        check(suite, drv, "G", "live-log-range-master-head", "log",
              {"range": "master..HEAD", "stat": True},
              argv=["git", "log", "--stat", "master..HEAD"],
              cmdline="git log --stat master..HEAD",
              must_not=["--range", "unrecognized argument"],
              note="was: fatal: unrecognized argument: --range=master..HEAD")
        check(suite, drv, "G", "live-lsremote-ref-upload-pack", "ls-remote",
              {"ref": "--upload-pack=/tmp/x"}, error=REV_REJECT,
              note="closed by f9000a5 via the revision slot")
        check(suite, drv, "G", "live-lsremote-remote-upload-pack", "ls-remote",
              {"remote": "--upload-pack=/tmp/x"}, error=REPO_REJECT,
              note="the same hole in the NEW repository slot, closed with it")
        rep = drv.call("log", {"args": ["a;b"]}, rc=1)   # echo lives on the failure path
        problems = []
        if rep["cmdline"] != "git log 'a;b'":
            problems.append("cmdline %r not quoted" % rep["cmdline"])
        if "git log a;b" in rep["text"]:
            problems.append("echoed line still contains bare `git log a;b`")
        suite.record("G", "live-echoed-metachar-quoted", problems,
                     detail=["cmdline     : %s" % rep["cmdline"],
                             "note        : was echoed bare, so copy-pasting the "
                             "line ran a second command"],
                     brief="%s | live-echoed-metachar-quoted"
                           % (H.FAIL if problems else H.PASS))

        # ============ H: offline contract + plumbing ============
        check(suite, drv, "H", "no-function-lists-allowlist", "", {}, spawns=0,
              must=["Allowed subcommands", "ls-remote", "hash-object"])
        check(suite, drv, "H", "unknown-function-refused", "push",
              {"remote": "origin"}, error="not on the read-only whitelist")
        check(suite, drv, "H", "branch-positional-refused", "branch",
              {"args": ["newbranch"]}, error="would create a branch")
        check(suite, drv, "H", "tag-positional-refused", "tag",
              {"args": ["v9.9"]}, error="would create a tag")
        check(suite, drv, "H", "hash-object-bundled-w-refused", "hash-object",
              {"args": ["-wt", "blob", "x"]}, error="bundled short options")
        check(suite, drv, "H", "config-set-refused", "config",
              {"args": ["user.name", "x"]}, error="mutates")
        check(suite, drv, "H", "apply-without-check-refused", "apply",
              {"path": "x.patch"}, error="only allowed with --check")
        check(suite, drv, "H", "params-as-json-string", "log",
              '{"range":"A..B","stat":true}',
              argv=["git", "log", "--stat", "A..B"])
        check(suite, drv, "H", "args-as-string-shlex-split", "log",
              {"args": "--oneline -5"}, argv=["git", "log", "--oneline", "-5"])
        check(suite, drv, "H", "args-wrong-type-refused", "log", {"args": 5},
              error="must be a list")

        # ====== I: _quote_arg as a property, stdlib only, no shell ======
        # (label, value, must_stay_unquoted)
        QUOTE_CORPUS = [
            ("empty", "", False),
            ("plain-range", "master..HEAD", True),
            ("tilde-range", "HEAD~7..HEAD", True),
            ("long-opt-value", "--max-count=10", True),
            ("path", "src/dir/file.c", True),
            ("pretty-format", "--pretty=format:%h", True),
            ("short-opt", "-20", True),
            ("percent", "50%", True),
            ("at-comma-plus", "user@host,x+y", True),
            ("space", "a b", False),
            ("tab", "a\tb", False),
            ("single-quote", "it's", False),
            ("double-quote", 'a"b', False),
            ("backslash", "a\\b", False),
            ("dollar-var", "$VAR", False),
            ("backtick", "`id`", False),
            ("semicolon", "a;b", False),
            ("pipe", "a|b", False),
            ("ampersand", "a&b", False),
            ("redirect-in", "a<b", False),
            ("redirect-out", "a>b", False),
            ("parens", "(a)", False),
            ("glob-star", "refs/heads/*", False),
            ("glob-question", "a?b", False),
            ("glob-class", "[ab]", False),
            ("bang", "a!b", False),
            ("leading-hash", "#comment", False),
            ("mid-hash", "a#b", False),
            ("leading-tilde", "~/x", False),
            ("leading-equals", "=x", False),
            ("leading-caret", "^master", False),
            ("brace-expansion", "a{b,c}", False),
            ("newline", "a\nb", False),
            ("utf8", "árvíztűrő tükörfúrógép", False),
            ("already-quoted", "'already quoted'", False),
        ]
        quote_arg = drv.mod._quote_arg
        for label, value, bare in QUOTE_CORPUS:
            rendered = quote_arg(value)
            problems = []
            for name, splitter in (("shlex.split", posix_split),
                                   ("punctuation_chars", operator_split)):
                try:
                    got = splitter(rendered)
                except Exception as exc:
                    problems.append("%s raised %s: %s"
                                    % (name, type(exc).__name__, exc))
                    continue
                if got != [value]:
                    problems.append("%s re-parses %r to %r, not [%r]"
                                    % (name, rendered, got, value))
            if bare and rendered != value:
                problems.append("readability lost: %r was quoted to %r"
                                % (value, rendered))
            if not bare and rendered == value:
                problems.append("%r was left unquoted" % value)
            suite.record("I", "roundtrip-" + label, problems,
                         detail=["value       : %r" % value,
                                 "rendered    : %r" % rendered,
                                 "expected    : %s"
                                 % ("bare" if bare else "quoted")],
                         brief="%s | roundtrip-%s | %r -> %r"
                               % (H.FAIL if problems else H.PASS, label, value,
                                  rendered))

        bare = [lab for lab, val, _b in QUOTE_CORPUS if quote_arg(val) == val]
        want_bare = [lab for lab, _v, b in QUOTE_CORPUS if b]
        suite.record("I", "readability-not-collapsed",
                     [] if bare == want_bare
                     else ["bare set drifted: %r != %r" % (bare, want_bare)],
                     detail=["%d of %d corpus values stay unquoted"
                             % (len(bare), len(QUOTE_CORPUS)),
                             "bare        : %r" % bare])

        # How much is each oracle actually worth?  Replay both round trips
        # against the PRE-FIX rendering and record which corpus entries each one
        # would have caught.  Anything NOT in these lists is a class the stdlib
        # oracles are blind to, and is covered only by group F.
        def caught_by(splitter):
            out = []
            for label, value, _b in QUOTE_CORPUS:
                try:
                    if splitter(_pre_fix_quote_arg(value)) != [value]:
                        out.append(label)
                except Exception:
                    out.append(label + "(raised)")
            return out

        plain_caught = caught_by(posix_split)
        op_caught = caught_by(operator_split)
        # Only the entries Fix 2 actually CHANGED can be caught by anything; the
        # rest were already rendered identically before the fix, so counting them
        # as blind spots would flatter or slander the oracle at random.
        changed = [lab for lab, val, _b in QUOTE_CORPUS
                   if _pre_fix_quote_arg(val) != quote_arg(val)]
        MUST_CATCH = {"semicolon", "pipe", "ampersand", "redirect-in",
                      "redirect-out", "parens", "leading-hash", "newline"}
        missed = sorted(MUST_CATCH - set(op_caught))
        suite.record("I", "oracle-strength-punctuation_chars",
                     [] if not missed
                     else ["punctuation_chars mode no longer detects the pre-fix "
                           "rendering of: %r" % missed],
                     detail=["Fix 2 changed the rendering of %d/%d corpus values"
                             % (len(changed), len(QUOTE_CORPUS)),
                             "caught      : %r" % op_caught,
                             "BLIND to    : %r  <- group F only"
                             % sorted(set(changed) - set(op_caught))])
        suite.record("I", "oracle-strength-plain-shlex", [], status=H.INFO,
                     detail=["plain shlex.split catches %d of the %d changed "
                             "renderings -- on its own it would have MISSED the "
                             "bug Fix 2 is about"
                             % (len(plain_caught), len(changed)),
                             "caught      : %r" % plain_caught,
                             "BLIND to    : %r"
                             % sorted(set(changed) - set(plain_caught)),
                             "this is why group F replays the line under real "
                             "shells as well"])

        # ====== J: git status defaults to a machine format ======
        # Plain `git status` spends ~190 chars on advice git_call can never act
        # on -- it is read-only, so `git add` / `git restore` are unreachable
        # through it. The default is prepended ONLY when the caller expressed no
        # format preference; anyone who states one keeps it byte for byte.
        check(suite, drv, "J", "bare-status-gets-porcelain", "status", {},
              argv=["git", "status", "--porcelain=v1", "-b"],
              note="the point of the change: no advice lines, branch delta kept")
        for cid, given in [
            ("short-s", ["-s"]),
            ("short-cluster-sb", ["-sb"]),
            ("short-long-spelling", ["--short"]),
            ("porcelain-bare", ["--porcelain"]),
            ("porcelain-v2", ["--porcelain=v2"]),
            ("long-explicit", ["--long"]),
            ("nul-separated", ["-z"]),
            ("nul-in-cluster", ["-zb"]),
        ]:
            check(suite, drv, "J", "caller-format-kept-" + cid, "status",
                  {"args": given}, argv=["git", "status"] + given,
                  note="caller chose a format -> argv untouched")
        check(suite, drv, "J", "branch-flag-not-duplicated", "status",
              {"args": ["-b"]},
              argv=["git", "status", "--porcelain=v1", "-b"],
              note="-b already there -> add porcelain only, no doubled flag")
        check(suite, drv, "J", "branch-long-not-duplicated", "status",
              {"args": ["--branch"]},
              argv=["git", "status", "--porcelain=v1", "--branch"])
        check(suite, drv, "J", "untracked-param-survives", "status",
              {"untracked": "all"},
              argv=["git", "status", "--porcelain=v1", "-b", "--untracked=all"],
              note="a real call site from session 19 -- must still reach git")
        check(suite, drv, "J", "u-cluster-is-not-a-format", "status",
              {"args": ["-uall"]},
              argv=["git", "status", "--porcelain=v1", "-b", "-uall"],
              note="-uall carries no s/z letter -> not a format choice")
        check(suite, drv, "J", "pathspec-after-dashdash-not-a-flag", "status",
              {"args": ["--", "-s"]},
              argv=["git", "status", "--porcelain=v1", "-b", "--", "-s"],
              note="a file literally named -s must not read as --short")
        for fn in ("log", "diff", "show"):
            check(suite, drv, "J", "no-leak-into-" + fn, fn, {},
                  argv=["git", fn], note="the default is status-only")

        # ====== K: the envelope states only what the caller cannot know ======
        # It knows which function it called and with what params -- that IS the
        # tool call -- so on the success path a heading, a command echo and
        # `(exit 0)` are pure cost. What it cannot know: flags the server added,
        # a non-zero exit, and (with no stdout at all) whether the thing worked.
        check(suite, drv, "K", "success-has-no-heading-echo-or-exit", "log",
              {"args": ["--oneline"]}, must=["STUB-STDOUT"],
              must_not=["## git", "(exit 0)", "`git log"],
              note="the caller sent these params; reading them back buys nothing")
        check(suite, drv, "K", "no-stdout-success-says-exit-0", "merge-base",
              {"args": ["--is-ancestor", "A", "B"]}, stdout="",
              must=["exit 0"], must_not=["_(no output)_", "## git"],
              note="--is-ancestor: the exit code IS the answer, not a placeholder")
        check(suite, drv, "K", "failure-keeps-full-echo", "log",
              {"args": ["--oneline"]}, rc=2,
              must=["`git log --oneline` (exit 2)"],
              note="an exit code is only diagnosable next to the argv that made it")

        rep = drv.call("merge-base", {"args": ["--is-ancestor", "A", "B"]},
                       rc=1, stdout="")
        problems = []
        if not rep["error"]:
            problems.append("rc!=0 with no stdout should return an error reply")
        for want in ("(exit 1)", "`git merge-base"):
            if want not in rep["text"]:
                problems.append("error reply is missing %r" % want)
        suite.record("K", "error-reply-keeps-exit-and-argv", problems,
                     detail=["reply       : %r" % rep["text"]],
                     brief="%s | error-reply-keeps-exit-and-argv"
                           % (H.FAIL if problems else H.PASS))

        # --- a fence only where Markdown would damage the payload ---
        for cid, out, fenced, why in [
            ("sha-single-line", "28eca15a1def7e1978387b15ad0470c2914c9c0a\n",
             False, "one markdown-neutral line: nothing to protect"),
            ("branch-name", "master\n", False, "same"),
            ("multi-line", "a\nb\n", True,
             "Markdown folds a lone newline into a space"),
            ("porcelain-branch-header", "## master...github/master\n", True,
             "`##` would render as an H2 heading -- the status -b reply"),
            ("backtick", "fix `foo` in bar\n", True, "a backtick breaks out"),
            ("leading-space", "  indented\n", True,
             "edge whitespace is part of the answer"),
            ("list-dash", "- item\n", True, "block-level lead character"),
            ("table-pipe-midline", "a|b\n", False,
             "a lone mid-line pipe is NOT a table: GFM needs a separator row"),
            ("table-pipe-leading", "|a|b|\n", True,
             "leads with a pipe -> reads as a table row"),
        ]:
            rep = drv.call("log", {"args": ["--oneline"]}, stdout=out)
            has = "```" in rep["text"]
            problems = ([] if has == fenced
                        else ["fenced=%s, expected %s" % (has, fenced)])
            suite.record("K", "fence-" + cid, problems,
                         detail=["stdout      : %r" % out,
                                 "fenced      : %s" % has,
                                 "why         : %s" % why,
                                 "reply       : %r" % rep["text"][:120]],
                         brief="%s | fence-%s | fenced=%s"
                               % (H.FAIL if problems else H.PASS, cid, has))

        check(suite, drv, "K", "injected-flags-disclosed", "status", {},
              must=["_+ --porcelain=v1 -b_"],
              note="a bare status coming back in porcelain must say why")
        check(suite, drv, "K", "no-disclosure-when-caller-chose", "status",
              {"args": ["-s"]}, must_not=["_+ "],
              note="nothing was injected -> nothing to disclose")

        stub_ok = drv.mod.subprocess is drv.stub
        non_git = [c for c in drv.stub.calls if not c or c[0] != "git"]
        suite.record("H", "offline-contract",
                     ([] if stub_ok else ["the module's subprocess reference was "
                                          "replaced -- a real git may have run"])
                     + ([] if not non_git else ["non-git argv captured: %r" % non_git]),
                     detail=["stubbed spawns: %d" % len(drv.stub.calls),
                             "every argv[0] == 'git', nothing executed"])
    finally:
        work.cleanup()

    pyc_after = H.pycache_snapshot()
    new_pyc = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record("H", "no-pycache-written",
                 [] if not (new_pyc or touched)
                 else ["new=%r touched=%r" % (new_pyc, touched)],
                 detail=["pyc files before=%d after=%d"
                         % (len(pyc_before), len(pyc_after))])

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
