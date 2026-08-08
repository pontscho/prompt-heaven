#!/usr/bin/env python3
"""
selftest_probe.py -- LIVE self-test fixture for the sbx sandbox wrapper.

Run me INSIDE the sandbox to prove containment actually holds on THIS host:

    sbx --write . -- python3 ~/.claude/skills/p/skills/sandbox-run/scripts/selftest_probe.py

(or, from a checkout, wrap the repo copy directly:
    python3 <repo>/ClaudeCode/skills/sandbox-run/scripts/sbx --write . -- \\
        python3 <repo>/ClaudeCode/skills/sandbox-run/scripts/selftest_probe.py)

I attempt four actions and print a one-line machine-readable verdict, then exit
0 only when containment held on every one:

  (a) write <cwd>/.claude/tmp/probe.out        -> must SUCCEED   (scratch is writable)
  (b) write ~/sbx_probe_escape                 -> must be BLOCKED (out-of-project write)
  (c) connect a TCP socket to a public IP:port -> must be BLOCKED (no --net)
  (d) read a secret under ~/.ssh (id_rsa, ...) -> must be BLOCKED (M2/A05/CWE-696)

Probe (d) is the LOAD-BEARING one: it is the ONLY proof that the macOS SBPL
last-match-wins secret-deny (sbx._seatbelt_argv) actually takes effect. Reads
are otherwise permissive (allow file-read* (subpath "/")), so a secret readable
under --write . is a REAL containment defect in the SBPL rule ORDER, not a test
artifact -- fix the ordering in sbx, do NOT paper over it. A missing or
out-of-scope-only probe could stay green while ~/.ssh was still readable
in-sandbox, which is exactly the failure this fixture exists to catch.

Expected verdict under a working sandbox:  a=ok b=blocked c=blocked d=blocked

Exit codes (so a caller / the skill self-test can gate on the result):
  0  every probe matched its expected outcome (full pass)
  1  a containment/functional VIOLATION -- (a) blocked (scratch broken), or any
     of (b)/(c)/(d)/(e) succeeded (an escape). Investigate the sbx profile.
  2  probe (d) was INCONCLUSIVE (nothing under ~/.ssh to read) and nothing else
     failed -- rerun on a host whose ~/.ssh is populated to PROVE the boundary
     (an empty ~/.ssh must never be reported as a false d=ok).

Additional in-sandbox check (reported on its OWN line, gated like a breach):
  (e) open <repo>/.git/config for WRITE        -> must be BLOCKED (R17/R22: a
      secret INSIDE an allowed --write scope is still write-denied). Non-
      destructive: O_WRONLY open only, ZERO bytes written, fd closed at once.

The faked-missing-sandbox check (R20) runs OUTSIDE the sandbox -- it wraps sbx
itself with the engine binary stripped from PATH, so sbx must fail closed
(non-zero exit, target NEVER run). It cannot run in-sandbox, so it is a
DOCUMENTED command rather than an in-sandbox probe:

    PATH=/var/empty /usr/bin/python3 <abs sbx> --write . -- \\
        /usr/bin/python3 <abs selftest_probe.py>
    # expect: non-zero exit, NO "a=... b=... c=... d=..." line on stdout, and a
    #         "refusing to run unsandboxed (fail-closed)" reason on stderr.

*** NOT IN PORTABLE CI (one-line rationale, mirrored in SKILL.md) ***
This probe needs a real sandbox-exec/bwrap engine and a real ~/.ssh, is
platform-specific (macOS Seatbelt / Linux bwrap), and is destructive-adjacent,
so it is an on-demand skill self-test deliberately kept OUT of `forge test all`.

Python 3, standard library only.
"""
from __future__ import annotations

import errno
import os
import socket
import sys

# errnos that mean "the OS sandbox denied this operation" (as opposed to the
# path simply not existing). PermissionError carries one of these.
_BLOCKED_ERRNOS = frozenset(
    e for e in (getattr(errno, "EPERM", None), getattr(errno, "EACCES", None))
    if e is not None
)

# We cannot os.listdir(~/.ssh) in-sandbox (the read-deny covers the directory
# itself), so we probe well-known secret filenames in order. Under a WORKING
# sandbox the very first attempt returns EPERM; the rest of the list only
# matters if the sandbox is broken and leaks real errnos, in which case trying
# several names raises the chance of hitting an existing file to prove/disprove.
_SSH_CANDIDATES = (
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "known_hosts", "authorized_keys", "config",
)


def _nearest_git_dir(start):
    """Nearest ancestor of `start` (inclusive) whose `<dir>/.git` exists, else
    None. Mirrors sbx.project_root's walk but returns None when no `.git` is
    found so probe (e) can report INCONCLUSIVE instead of guessing a target.
    (`.git` the directory is readable in-sandbox -- only `.git/config` is denied
    -- so os.path.exists on it works.)"""
    cur = os.path.realpath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:            # reached the filesystem root
            return None
        cur = parent


def probe_scratch_write():
    """(a) write <cwd>/.claude/tmp/probe.out -- expected 'ok' (scratch writable).

    Under `--write .` the whole project (== cwd) is writable, so the scratch
    subtree is too; we create it (never a shell mkdir) and write a marker.
    """
    target = os.path.join(os.getcwd(), ".claude", "tmp", "probe.out")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as fh:
            fh.write("sbx selftest_probe (a): scratch write ok\n")
    except OSError as exc:
        return "blocked", "scratch write FAILED (a should succeed): %s" % (exc,)
    return "ok", "wrote %s" % (target,)


def probe_escape_write():
    """(b) write ~/sbx_probe_escape -- expected 'blocked' (out-of-project write).

    A successful write is a containment ESCAPE; clean it up best-effort so a
    breach on one run does not litter $HOME for the next.
    """
    target = os.path.expanduser("~/sbx_probe_escape")
    try:
        with open(target, "w") as fh:
            fh.write("sbx selftest_probe (b): this file should never exist\n")
    except OSError as exc:
        return "blocked", "denied: %s" % (exc,)
    try:
        os.remove(target)
    except OSError:
        pass
    return "ok", "WROTE outside project (%s) -- containment ESCAPE" % (target,)


def probe_network():
    """(c) outbound TCP connect to a literal public IP -- expected 'blocked'.

    A literal IP:port (1.1.1.1:443) needs no DNS. A successful connect means the
    network was reachable -- an escape (no --net was passed). Any OSError
    (EPERM from the sandbox, or a timeout/refusal) counts as blocked; the errno
    is carried in the reason so a real EPERM is distinguishable in the log.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("1.1.1.1", 443))
    except OSError as exc:
        return "blocked", "denied: %s" % (exc,)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return "ok", "CONNECTED to 1.1.1.1:443 -- network NOT contained"


def probe_secret_read():
    """(d) read a secret under ~/.ssh -- expected 'blocked'. LOAD-BEARING (M2).

    Returns:
      'blocked'      an open attempt raised EPERM/EACCES -> the SBPL secret-deny
                     fired (the desired proof).
      'ok'           an open SUCCEEDED and a byte was readable -> the secret is
                     readable in-sandbox -> a REAL macOS secret-boundary breach.
      'inconclusive' every candidate returned ENOENT (nothing under ~/.ssh to
                     read) -- NOT a false 'ok': we simply could not prove the
                     boundary on this host.
    """
    ssh_dir = os.path.expanduser("~/.ssh")
    saw_nonperm_miss = False
    for name in _SSH_CANDIDATES:
        path = os.path.join(ssh_dir, name)
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as exc:
            if exc.errno in _BLOCKED_ERRNOS:
                return "blocked", "read denied on %s: %s" % (path, exc)
            # ENOENT (or any other non-permission miss): this candidate could
            # not prove the boundary -- try the next well-known name.
            saw_nonperm_miss = True
            continue
        # The open SUCCEEDED -> confirm we can actually read a byte -> BREACH.
        try:
            os.read(fd, 1)
        finally:
            os.close(fd)
        return "ok", ("READ secret %s -- SBPL secret-deny FAILED; the macOS "
                      "last-match-wins ordering did NOT take effect (M2)" % (path,))
    if saw_nonperm_miss:
        return "inconclusive", ("no readable candidate under ~/.ssh (all missing); "
                                "rerun on a host with ~/.ssh populated to prove (d)")
    return "inconclusive", "~/.ssh absent"


def probe_secret_write():
    """(e) open <repo>/.git/config for WRITE -- expected 'blocked' (R17/R22).

    A secret INSIDE an allowed --write scope must still be write-denied. This is
    NON-DESTRUCTIVE: O_WRONLY opens for write intent but we write nothing and
    close the fd immediately, so if the deny (wrongly) failed we still leave the
    file byte-for-byte intact.
    """
    root = _nearest_git_dir(os.getcwd())
    if root is None:
        return "inconclusive", "no ancestor .git found from cwd"
    path = os.path.join(root, ".git", "config")
    try:
        fd = os.open(path, os.O_WRONLY)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return "inconclusive", "%s does not exist" % (path,)
        return "blocked", "write denied on %s: %s" % (path, exc)
    os.close(fd)
    return "ok", ("OPENED %s for WRITE -- secret-write-deny FAILED inside an "
                  "allowed --write scope (R17/R22)" % (path,))


def main():
    # The mandatory a..d contract, in order. Each entry: (key, expected, fn).
    probes = [
        ("a", "ok", probe_scratch_write),
        ("b", "blocked", probe_escape_write),
        ("c", "blocked", probe_network),
        ("d", "blocked", probe_secret_read),
    ]
    results = []
    for key, expected, fn in probes:
        try:
            outcome, detail = fn()
        except Exception as exc:            # a probe must never crash the report
            outcome, detail = "error", "probe raised: %r" % (exc,)
        results.append((key, expected, outcome, detail))

    # (e) secret-WRITE (R17/R22): reported on its OWN line, off the a..d contract
    # line the self-test greps for, but gated like a breach below.
    try:
        e_outcome, e_detail = probe_secret_write()
    except Exception as exc:
        e_outcome, e_detail = "error", "probe raised: %r" % (exc,)

    # Machine-readable contract line FIRST: "a=ok b=blocked c=blocked d=blocked".
    print(" ".join("%s=%s" % (k, out) for k, _exp, out, _d in results))
    print("e_secret_write=%s" % (e_outcome,))

    # Human-readable detail.
    def _mark(expected, outcome):
        if outcome == expected:
            return "PASS"
        if outcome == "inconclusive":
            return "INFO"
        return "FAIL"

    for key, expected, outcome, detail in results:
        print("  %s [%s] expected=%s got=%s -- %s"
              % (key, _mark(expected, outcome), expected, outcome, detail))
    print("  e [%s] expected=blocked got=%s -- %s"
          % (_mark("blocked", e_outcome), e_outcome, e_detail))

    # Exit-code decision. A violation is any probe whose outcome is neither the
    # expected one NOR 'inconclusive' (an unprovable but non-breaching state).
    violated = any(
        outcome != expected and outcome != "inconclusive"
        for _k, expected, outcome, _d in results
    )
    if e_outcome not in ("blocked", "inconclusive"):
        violated = True
    inconclusive = (
        any(outcome == "inconclusive" for _k, _e, outcome, _d in results)
        or e_outcome == "inconclusive"
    )

    if violated:
        print("VERDICT: FAIL -- containment breach or functional failure (see above)")
        return 1
    if inconclusive:
        print("VERDICT: INCONCLUSIVE -- rerun with ~/.ssh populated to prove probe (d)")
        return 2
    print("VERDICT: PASS -- the sandbox contained every probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
