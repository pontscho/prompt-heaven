"""
seccomp_probe.py -- LIVE check of the program `sbx --seccomp` hands to bwrap,
WITHOUT bwrap: it installs that program into ITSELF with prctl and then proves
that ordinary CLIs still run while the denied calls fail.

    python3 -I seccomp_probe.py <path to sbx>

Linux x86_64 only. Run it as its OWN process: the filter it installs cannot be
removed, so the process that ran it is filtered until it exits (that is the
point -- and why tests/test_sbx_seccomp.py runs it as a child).

What it does, in order:
  1. loads the sbx helper from the given path and builds seccomp_program() for
     this machine;
  2. cross-checks every syscall number sbx embeds against this host's
     asm/unistd_64.h, when the header is installed (INFO when it is not);
  3. makes each DISCRIMINATING call once BEFORE the filter and records its errno
     -- a call the kernel would already refuse with EPERM proves nothing, so each
     one is chosen to fail with a DIFFERENT errno (or succeed) unfiltered;
  4. prctl(PR_SET_NO_NEW_PRIVS) + prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER);
  5. repeats them and requires the filtered answer (EPERM, or ENOSYS for clone3);
  6. forks a child that makes an x32 call and requires it to die by SIGSYS;
  7. runs python3, ls, git, sh, bash, cat, a threaded python and an AF_UNIX
     socket under the filter and requires each to exit 0.

Output: one `PASS|FAIL|INFO <name>: <detail>` line per check, then
`RESULT: <n> pass, <m> fail`. Exit 0 iff nothing failed; 2 when the host is not
Linux x86_64 (nothing is installed then).

This is NOT the bwrap end-to-end proof: bwrap installs the program itself, in
the target, after its own namespace setup. That needs a host with bwrap and
unprivileged user namespaces, and is recorded as pending in ADR 0005.

Python 3, standard library only.
"""
from __future__ import annotations

import ctypes
import errno
import importlib.machinery
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import threading

HEADERS = ("/usr/include/x86_64-linux-gnu/asm/unistd_64.h",
           "/usr/include/asm/unistd_64.h")

PR_SET_SECCOMP = 22
PR_SET_NO_NEW_PRIVS = 38
SECCOMP_MODE_FILTER = 2

# the numbers the discriminating calls use -- read from the sbx table where sbx
# names them, spelled here only for the ones sbx deliberately leaves out.
NR_PTRACE, NR_UMOUNT2, NR_KEYCTL, NR_PERSONALITY = 101, 166, 250, 135
NR_UNSHARE, NR_MOUNT, NR_BPF = 272, 165, 321
PTRACE_PEEKDATA = 2
CLONE_FS, CLONE_THREAD = 0x00000200, 0x00010000
CLONE_NEWNS, CLONE_NEWUSER = 0x00020000, 0x10000000
AF_UNIX, AF_NETLINK, SOCK_STREAM, SOCK_RAW = 1, 16, 1, 3
TIOCSTI, FIONREAD = 0x5412, 0x541B
X32_SYSCALL_BIT = 0x40000000

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
libc.prctl.restype = ctypes.c_int

COUNTS = {"PASS": 0, "FAIL": 0, "INFO": 0}


def report(verdict, name, detail):
    COUNTS[verdict] += 1
    print("%s %s: %s" % (verdict, name, detail), flush=True)


def raw(nr, *args):
    """syscall(nr, *args) -> (return value, errno). Ints go as C longs; ctypes
    objects (pointers, byref) go as they are."""
    cargs = [ctypes.c_long(a) if isinstance(a, int) else a for a in args]
    ctypes.set_errno(0)
    ret = libc.syscall(ctypes.c_long(nr), *cargs)
    return ret, (ctypes.get_errno() if ret == -1 else 0)


def ename(err):
    return errno.errorcode.get(err, str(err)) if err else "ok"


def load_sbx(path):
    loader = importlib.machinery.SourceFileLoader("sbx_probe_target", path)
    spec = importlib.util.spec_from_loader("sbx_probe_target", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sbx_probe_target"] = module
    loader.exec_module(module)
    return module


def header_crosscheck(sbx):
    for path in HEADERS:
        if os.path.exists(path):
            break
    else:
        report("INFO", "header", "no asm/unistd_64.h installed; not cross-checked")
        return
    table = {}
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 3 and parts[0] == "#define" and \
                    parts[1].startswith("__NR_") and parts[2].isdigit():
                table[parts[1][5:]] = int(parts[2])
    bad = ["%s=%d (header %r)" % (n, v, table.get(n))
           for n, v in sorted(sbx._SECCOMP_X86_64.items()) if table.get(n) != v]
    report("FAIL" if bad else "PASS", "header",
           "%s: %s" % (path, "; ".join(bad)) if bad else
           "%d embedded numbers match %s" % (len(sbx._SECCOMP_X86_64), path))


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def install(program):
    zero = ctypes.c_ulong(0)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, ctypes.c_ulong(1), zero, zero, zero):
        return "PR_SET_NO_NEW_PRIVS: %s" % ename(ctypes.get_errno())
    buf = ctypes.create_string_buffer(program, len(program))
    prog = SockFprog(len(program) // 8, ctypes.cast(buf, ctypes.c_void_p))
    if libc.prctl(PR_SET_SECCOMP, ctypes.c_ulong(SECCOMP_MODE_FILTER),
                  ctypes.byref(prog), zero, zero):
        return "PR_SET_SECCOMP: %s" % ename(ctypes.get_errno())
    return None


def discriminators(sbx):
    """(name, thunk, wanted errno after the filter). Each thunk returns
    (ret, errno); every one is safe to call unfiltered."""
    nr = sbx._SECCOMP_X86_64
    rfd, wfd = os.pipe()
    ch = ctypes.c_char(b"x")
    out = []

    def socket_netlink():
        ret, err = raw(nr["socket"], AF_NETLINK, SOCK_RAW, 0)
        if ret >= 0:
            os.close(ret)
        return ret, err

    out.append(("ptrace(PEEKDATA, pid 0)",
                lambda: raw(NR_PTRACE, PTRACE_PEEKDATA, 0, 0, 0), errno.EPERM))
    out.append(("unshare(invalid flag 0x1)",
                lambda: raw(NR_UNSHARE, 0x1), errno.EPERM))
    out.append(("umount2('/', invalid flag)",
                lambda: raw(NR_UMOUNT2, ctypes.c_char_p(b"/"), 0x80000000),
                errno.EPERM))
    out.append(("keyctl(invalid op 9999)",
                lambda: raw(NR_KEYCTL, 9999, 0, 0, 0, 0), errno.EPERM))
    out.append(("personality(query 0xffffffff)",
                lambda: raw(NR_PERSONALITY, 0xFFFFFFFF), errno.EPERM))
    out.append(("socket(AF_NETLINK)", socket_netlink, errno.EPERM))
    out.append(("ioctl(pipe, TIOCSTI)",
                lambda: raw(nr["ioctl"], rfd, TIOCSTI, ctypes.byref(ch)),
                errno.EPERM))
    # CLONE_NEWNS|CLONE_FS is the first combination copy_process() rejects
    # (EINVAL), before any privilege check -- so unfiltered it creates nothing and
    # answers something other than EPERM. (CLONE_NEWUSER is NOT usable here: an
    # Ubuntu kernel with unprivileged_userns_clone=0 answers it EPERM unfiltered,
    # measured on t42, so it lives in after_only.)
    out.append(("clone(CLONE_NEWNS|CLONE_FS)",
                lambda: raw(nr["clone"], CLONE_NEWNS | CLONE_FS, 0, 0, 0, 0),
                errno.EPERM))
    out.append(("clone3(NULL, 0)",
                lambda: raw(nr["clone3"], 0, 0), errno.ENOSYS))
    return out, (rfd, wfd)


def after_only(sbx):
    """Calls that are only made once the filter is in: unfiltered, some of them
    would CHANGE this process (a new user namespace) or are refused by the
    kernel with EPERM anyway, so they carry no before/after contrast -- they are
    here to show the filtered answer, not to prove the filter is the cause."""
    nr = sbx._SECCOMP_X86_64
    return [
        ("unshare(CLONE_NEWUSER)", lambda: raw(NR_UNSHARE, CLONE_NEWUSER)),
        ("clone(CLONE_NEWUSER|CLONE_THREAD)",
         lambda: raw(nr["clone"], CLONE_NEWUSER | CLONE_THREAD, 0, 0, 0, 0)),
        ("mount(NULL, '/', NULL, 0, NULL)",
         lambda: raw(NR_MOUNT, 0, ctypes.c_char_p(b"/"), 0, 0, 0)),
        ("bpf(invalid cmd 9999)", lambda: raw(NR_BPF, 9999, 0, 0)),
    ]


def x32_child():
    pid = os.fork()
    if pid == 0:
        raw(X32_SYSCALL_BIT | 39)                 # x32 getpid
        os._exit(0)                              # reached only if NOT killed
    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGSYS:
        report("PASS", "x32 getpid in a child", "killed by SIGSYS")
    else:
        report("FAIL", "x32 getpid in a child", "status %r, want SIGSYS" % status)


def commands():
    py = sys.executable
    runs = [
        ("python3 -c pass", [py, "-c", "pass"]),
        ("ls /", ["/bin/ls", "/"]),
        ("git --version", ["git", "--version"]),
        ("sh -c true", ["sh", "-c", "true"]),
        ("bash -c 'echo ok'", ["bash", "-c", "echo ok"]),
        ("cat /etc/os-release", ["cat", "/etc/os-release"]),
        ("python3 threads + subprocess",
         [py, "-c", "import subprocess, threading; t = threading.Thread("
                    "target=lambda: None); t.start(); t.join(); "
                    "subprocess.run(['true'], check=True)"]),
        ("python3 AF_UNIX socketpair",
         [py, "-c", "import socket; a, b = socket.socketpair(); a.sendall(b'x'); "
                    "assert b.recv(1) == b'x'"]),
    ]
    for name, argv in runs:
        if shutil.which(argv[0]) is None:
            report("INFO", name, "%s not installed; skipped" % argv[0])
            continue
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            report("FAIL", name, "could not run: %s" % exc)
            continue
        first = (proc.stdout.strip().splitlines() or [""])[0][:60]
        report("PASS" if proc.returncode == 0 else "FAIL", name,
               "rc %d %s" % (proc.returncode,
                            repr(first) if proc.returncode == 0 else
                            proc.stderr.strip()[-200:]))


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: seccomp_probe.py <path to sbx>\n")
        return 2
    if not sys.platform.startswith("linux") or os.uname().machine != "x86_64":
        print("SKIP: needs Linux x86_64, this is %s/%s"
              % (sys.platform, os.uname().machine))
        return 2
    sbx = load_sbx(argv[1])
    program = sbx.seccomp_program(os.uname().machine)
    report("INFO", "program", "%d instructions, %d allowlisted syscalls"
           % (len(program) // 8, len(sbx._SECCOMP_ALLOW)))
    header_crosscheck(sbx)

    checks, fds = discriminators(sbx)
    before = {name: fn() for name, fn, _want in checks}

    failed = install(program)
    if failed:
        report("FAIL", "install", failed)
        print("RESULT: %d pass, %d fail" % (COUNTS["PASS"], COUNTS["FAIL"]))
        return 1
    report("PASS", "install", "no_new_privs + SECCOMP_MODE_FILTER loaded")

    for name, fn, want in checks:
        ret, err = fn()
        b_ret, b_err = before[name]
        ok = ret == -1 and err == want and b_err != want
        report("PASS" if ok else "FAIL", name,
               "before %s, after %s (want %s)"
               % (ename(b_err) if b_ret == -1 else "ret %d" % b_ret,
                  ename(err) if ret == -1 else "ret %d" % ret, ename(want)))
    for name, fn in after_only(sbx):
        ret, err = fn()
        report("PASS" if (ret == -1 and err == errno.EPERM) else "FAIL", name,
               "after %s (want EPERM; no before/after contrast)"
               % (ename(err) if ret == -1 else "ret %d" % ret))
    for fd in fds:
        os.close(fd)

    x32_child()
    pid = os.fork()
    if pid == 0:
        os._exit(7)
    _, status = os.waitpid(pid, 0)
    report("PASS" if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 7
           else "FAIL", "os.fork", "child status %r" % status)
    box = []
    t = threading.Thread(target=lambda: box.append(1))
    t.start()
    t.join()
    report("PASS" if box == [1] else "FAIL", "threading.Thread", "ran %r" % box)
    commands()

    print("RESULT: %d pass, %d fail" % (COUNTS["PASS"], COUNTS["FAIL"]))
    return 1 if COUNTS["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
