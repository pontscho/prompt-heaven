#!/usr/bin/env python3
"""Suite for the sbx Linux seccomp filter (R-0003): the `--seccomp` flag, the
stdlib classic-BPF program builder, and its wiring into the bwrap argv.

The case COUNT is deliberately absent from this docstring: it is written down
once, in the SUITES table in tests/run.py, which checks it against the run.

Why this is its own suite and not a group in test_sbx_gate.py: that suite gates
the PreToolUse GATE and folds in the offline containment of the two backend
builders. This one gates a PROGRAM -- bytes the kernel executes -- and the only
honest way to assert what those bytes do is to execute them, so it carries its
own classic-BPF interpreter (group A). The interpreter is written from the
kernel's seccomp_data layout and the four opcodes the builder may emit, NOT from
the builder, so a builder bug cannot hide behind a shared helper. The gate's own
`--seccomp` accept-set rows live in test_sbx_gate.py, beside the other flags.

Groups:
  A  PROGRAM -- pure, runs on any OS. The architecture check comes first and a
     foreign arch KILLs; the x32 bit KILLs; every allowlisted syscall ALLOWs and
     EVERY other number in the x86_64 table (and past its end) gets EPERM, the
     default; the dangerous names are absent; the three argument-checked entries
     (socket domain, ioctl TIOCSTI/TIOCLINUX, clone namespace flags) and clone3's
     ENOSYS; every embedded number matches an INDEPENDENT reference copy of the
     kernel header; the program only uses the opcodes the interpreter knows, every
     jump lands inside it, it ends in a RET, and two builds are byte-identical; a
     non-x86_64 machine refuses to build.
  B  ARGV -- _bwrap_argv stays a pure Scope -> argv function: flag off is the
     golden argv byte-for-byte, flag on inserts exactly `--seccomp N` before the
     terminator, the flag with no fd resolved still refuses, a non-int fd refuses,
     and the builder performs no I/O; every argv carries `--new-session` and
     `--unshare-ipc`, and a FILE secret without its mask fd refuses.
  C  CLI -- main() in-process with exec, rlimits and engine detection stubbed:
     the flag exists and is off by default, --dry-run shows it and creates no fd,
     a non-Linux platform and a non-x86_64 Linux both REFUSE (exit 3, nothing
     exec'd), a FILE secret's mask fd is inheritable and empty, and on a real
     Linux x86_64 host the exec'd argv names an inheritable fd whose content is
     the program (INFO elsewhere).
  D  LIVE -- Linux x86_64 only, INFO elsewhere: seccomp_probe.py loads the program
     into ITSELF via prctl in a child process and proves ordinary CLIs still run
     while the denied calls get EPERM.
  P  PROBE -- the header cross-check: an absent name is INFO, a differing
     number FAIL.
  H  hygiene -- no bytecode left behind.

Usage:
  python3 tests/test_sbx_seccomp.py [--brief] [--keep]
Exit code 0 iff every case passes.
"""

import io
import os
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "sbx_seccomp"
HELPER = H.repo_path("ClaudeCode", "skills", "sandbox-run", "scripts", "sbx")
PROBE = H.repo_path("ClaudeCode", "skills", "sandbox-run", "scripts",
                    "seccomp_probe.py")

GRP_A = "A. PROGRAM (interpreted, any OS)"
GRP_B = "B. ARGV (_bwrap_argv stays pure)"
GRP_C = "C. CLI (main in-process, exec stubbed)"
GRP_D = "D. LIVE (Linux x86_64 prctl probe)"
GRP_P = "P. PROBE header cross-check (pure, any OS)"
GRP_H = "H. hygiene"

# ---------------------------------------------------------------------------
# The INDEPENDENT reference: the x86_64 syscall table, one name=number pair per
# `#define __NR_<name> <n>` in /usr/include/x86_64-linux-gnu/asm/unistd_64.h on
# t42 (Ubuntu 24.04, linux-libc-dev 6.8.0-38.38, sha256 e34e39ab...809fe). The
# helper embeds its OWN copy of the allowlisted subset; this string is the
# oracle that copy is checked against, so a transcription slip in either shows.
# ---------------------------------------------------------------------------
_X86_64_TABLE = """
read=0 write=1 open=2 close=3 stat=4 fstat=5 lstat=6 poll=7 lseek=8 mmap=9
mprotect=10 munmap=11 brk=12 rt_sigaction=13 rt_sigprocmask=14 rt_sigreturn=15
ioctl=16 pread64=17 pwrite64=18 readv=19 writev=20 access=21 pipe=22 select=23
sched_yield=24 mremap=25 msync=26 mincore=27 madvise=28 shmget=29 shmat=30
shmctl=31 dup=32 dup2=33 pause=34 nanosleep=35 getitimer=36 alarm=37
setitimer=38 getpid=39 sendfile=40 socket=41 connect=42 accept=43 sendto=44
recvfrom=45 sendmsg=46 recvmsg=47 shutdown=48 bind=49 listen=50 getsockname=51
getpeername=52 socketpair=53 setsockopt=54 getsockopt=55 clone=56 fork=57
vfork=58 execve=59 exit=60 wait4=61 kill=62 uname=63 semget=64 semop=65
semctl=66 shmdt=67 msgget=68 msgsnd=69 msgrcv=70 msgctl=71 fcntl=72 flock=73
fsync=74 fdatasync=75 truncate=76 ftruncate=77 getdents=78 getcwd=79 chdir=80
fchdir=81 rename=82 mkdir=83 rmdir=84 creat=85 link=86 unlink=87 symlink=88
readlink=89 chmod=90 fchmod=91 chown=92 fchown=93 lchown=94 umask=95
gettimeofday=96 getrlimit=97 getrusage=98 sysinfo=99 times=100 ptrace=101
getuid=102 syslog=103 getgid=104 setuid=105 setgid=106 geteuid=107 getegid=108
setpgid=109 getppid=110 getpgrp=111 setsid=112 setreuid=113 setregid=114
getgroups=115 setgroups=116 setresuid=117 getresuid=118 setresgid=119
getresgid=120 getpgid=121 setfsuid=122 setfsgid=123 getsid=124 capget=125
capset=126 rt_sigpending=127 rt_sigtimedwait=128 rt_sigqueueinfo=129
rt_sigsuspend=130 sigaltstack=131 utime=132 mknod=133 uselib=134
personality=135 ustat=136 statfs=137 fstatfs=138 sysfs=139 getpriority=140
setpriority=141 sched_setparam=142 sched_getparam=143 sched_setscheduler=144
sched_getscheduler=145 sched_get_priority_max=146 sched_get_priority_min=147
sched_rr_get_interval=148 mlock=149 munlock=150 mlockall=151 munlockall=152
vhangup=153 modify_ldt=154 pivot_root=155 _sysctl=156 prctl=157 arch_prctl=158
adjtimex=159 setrlimit=160 chroot=161 sync=162 acct=163 settimeofday=164
mount=165 umount2=166 swapon=167 swapoff=168 reboot=169 sethostname=170
setdomainname=171 iopl=172 ioperm=173 create_module=174 init_module=175
delete_module=176 get_kernel_syms=177 query_module=178 quotactl=179
nfsservctl=180 getpmsg=181 putpmsg=182 afs_syscall=183 tuxcall=184
security=185 gettid=186 readahead=187 setxattr=188 lsetxattr=189 fsetxattr=190
getxattr=191 lgetxattr=192 fgetxattr=193 listxattr=194 llistxattr=195
flistxattr=196 removexattr=197 lremovexattr=198 fremovexattr=199 tkill=200
time=201 futex=202 sched_setaffinity=203 sched_getaffinity=204
set_thread_area=205 io_setup=206 io_destroy=207 io_getevents=208 io_submit=209
io_cancel=210 get_thread_area=211 lookup_dcookie=212 epoll_create=213
epoll_ctl_old=214 epoll_wait_old=215 remap_file_pages=216 getdents64=217
set_tid_address=218 restart_syscall=219 semtimedop=220 fadvise64=221
timer_create=222 timer_settime=223 timer_gettime=224 timer_getoverrun=225
timer_delete=226 clock_settime=227 clock_gettime=228 clock_getres=229
clock_nanosleep=230 exit_group=231 epoll_wait=232 epoll_ctl=233 tgkill=234
utimes=235 vserver=236 mbind=237 set_mempolicy=238 get_mempolicy=239
mq_open=240 mq_unlink=241 mq_timedsend=242 mq_timedreceive=243 mq_notify=244
mq_getsetattr=245 kexec_load=246 waitid=247 add_key=248 request_key=249
keyctl=250 ioprio_set=251 ioprio_get=252 inotify_init=253
inotify_add_watch=254 inotify_rm_watch=255 migrate_pages=256 openat=257
mkdirat=258 mknodat=259 fchownat=260 futimesat=261 newfstatat=262
unlinkat=263 renameat=264 linkat=265 symlinkat=266 readlinkat=267
fchmodat=268 faccessat=269 pselect6=270 ppoll=271 unshare=272
set_robust_list=273 get_robust_list=274 splice=275 tee=276
sync_file_range=277 vmsplice=278 move_pages=279 utimensat=280 epoll_pwait=281
signalfd=282 timerfd_create=283 eventfd=284 fallocate=285 timerfd_settime=286
timerfd_gettime=287 accept4=288 signalfd4=289 eventfd2=290 epoll_create1=291
dup3=292 pipe2=293 inotify_init1=294 preadv=295 pwritev=296
rt_tgsigqueueinfo=297 perf_event_open=298 recvmmsg=299 fanotify_init=300
fanotify_mark=301 prlimit64=302 name_to_handle_at=303 open_by_handle_at=304
clock_adjtime=305 syncfs=306 sendmmsg=307 setns=308 getcpu=309
process_vm_readv=310 process_vm_writev=311 kcmp=312 finit_module=313
sched_setattr=314 sched_getattr=315 renameat2=316 seccomp=317 getrandom=318
memfd_create=319 kexec_file_load=320 bpf=321 execveat=322 userfaultfd=323
membarrier=324 mlock2=325 copy_file_range=326 preadv2=327 pwritev2=328
pkey_mprotect=329 pkey_alloc=330 pkey_free=331 statx=332 io_pgetevents=333
rseq=334 pidfd_send_signal=424 io_uring_setup=425 io_uring_enter=426
io_uring_register=427 open_tree=428 move_mount=429 fsopen=430 fsconfig=431
fsmount=432 fspick=433 pidfd_open=434 clone3=435 close_range=436 openat2=437
pidfd_getfd=438 faccessat2=439 process_madvise=440 epoll_pwait2=441
mount_setattr=442 quotactl_fd=443 landlock_create_ruleset=444
landlock_add_rule=445 landlock_restrict_self=446 memfd_secret=447
process_mrelease=448 futex_waitv=449 set_mempolicy_home_node=450
cachestat=451 fchmodat2=452 map_shadow_stack=453 futex_wake=454
futex_wait=455 futex_requeue=456 statmount=457 listmount=458
lsm_get_self_attr=459 lsm_set_self_attr=460 lsm_list_modules=461
"""
REF = {n: int(v) for n, v in (p.split("=") for p in _X86_64_TABLE.split())}

# Kernel constants, spelled here independently of the helper (uapi seccomp.h /
# audit.h / errno-base.h / asm-generic/ioctls.h / sched.h / socket.h).
AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_I386 = 0x40000003
AUDIT_ARCH_AARCH64 = 0xC00000B7
X32_SYSCALL_BIT = 0x40000000
RET_KILL_PROCESS = 0x80000000
RET_ALLOW = 0x7FFF0000
RET_ERRNO = 0x00050000
EPERM = 1
ENOSYS = 38
DENY = RET_ERRNO | EPERM

# Must never be allowed: each is a kernel attack surface or a way out of the
# namespace/mount/credential model bwrap sets up.
DANGEROUS = (
    "ptrace", "mount", "umount2", "pivot_root", "chroot", "unshare", "setns",
    "keyctl", "add_key", "request_key", "bpf", "perf_event_open", "kexec_load",
    "kexec_file_load", "init_module", "finit_module", "delete_module",
    "open_by_handle_at", "name_to_handle_at", "userfaultfd", "iopl", "ioperm",
    "reboot", "swapon", "swapoff", "personality", "process_vm_readv",
    "process_vm_writev", "kcmp", "pidfd_getfd", "io_uring_setup",
    "io_uring_enter", "io_uring_register", "fsopen", "fsconfig", "fsmount",
    "fspick", "open_tree", "move_mount", "mount_setattr", "modify_ldt",
    "syslog", "acct", "settimeofday", "clock_settime", "adjtimex",
    "clock_adjtime", "sethostname", "setdomainname", "quotactl", "quotactl_fd",
    "lookup_dcookie", "vhangup", "uselib", "_sysctl", "create_module",
    "fanotify_init", "setuid", "setgid", "setgroups", "setreuid", "setregid",
    "setresuid", "setresgid", "vmsplice", "memfd_secret", "move_pages",
    "migrate_pages", "mbind", "set_mempolicy", "process_madvise",
    "shmget", "semget", "msgget", "mq_open", "statmount", "listmount",
)

# Must be allowed or an ordinary CLI under bwrap cannot start, fork, exec, map
# memory, read a directory or wait for a child.
REQUIRED = (
    "read", "write", "close", "openat", "newfstatat", "statx", "getdents64",
    "lseek", "mmap", "munmap", "mprotect", "brk", "execve", "execveat", "fork",
    "vfork", "wait4", "waitid", "exit", "exit_group", "futex", "rseq",
    "set_robust_list", "set_tid_address", "rt_sigaction", "rt_sigprocmask",
    "rt_sigreturn", "pipe2", "dup3", "poll", "ppoll", "epoll_create1",
    "epoll_ctl", "epoll_wait", "epoll_pwait", "getrandom", "prlimit64",
    "sched_getaffinity", "sched_yield", "arch_prctl", "prctl", "fcntl",
    "getcwd", "chdir", "readlink", "faccessat2", "close_range", "kill",
    "tgkill", "getpid", "gettid", "uname", "clock_gettime", "nanosleep",
    "clock_nanosleep",
)

# The argument-checked entries, spelled from the kernel headers.
AF_UNIX, AF_INET, AF_INET6 = 1, 2, 10
AF_NETLINK, AF_PACKET, AF_ALG, AF_VSOCK = 16, 17, 38, 40
TIOCSTI, TIOCLINUX, TCGETS, FIONREAD = 0x5412, 0x541C, 0x5401, 0x541B
CLONE_NS_FLAGS = {
    "CLONE_NEWNS": 0x00020000, "CLONE_NEWCGROUP": 0x02000000,
    "CLONE_NEWUTS": 0x04000000, "CLONE_NEWIPC": 0x08000000,
    "CLONE_NEWUSER": 0x10000000, "CLONE_NEWPID": 0x20000000,
    "CLONE_NEWNET": 0x40000000,
}
SIGCHLD = 17
PTHREAD_CLONE_FLAGS = 0x003D0F00   # glibc create_thread's flag word
SPECIAL = ("socket", "ioctl", "clone", "clone3")

# ---------------------------------------------------------------------------
# A classic-BPF interpreter for exactly the opcodes a seccomp allowlist needs.
# Written from struct seccomp_data { int nr; __u32 arch; __u64 ip; __u64
# args[6]; } and linux/filter.h, never from the builder.
# ---------------------------------------------------------------------------
LD_W_ABS = 0x20     # BPF_LD | BPF_W | BPF_ABS
JEQ_K = 0x15        # BPF_JMP | BPF_JEQ | BPF_K
JSET_K = 0x45       # BPF_JMP | BPF_JSET | BPF_K
RET_K = 0x06        # BPF_RET | BPF_K
KNOWN_OPS = {LD_W_ABS, JEQ_K, JSET_K, RET_K}
SECCOMP_DATA_SIZE = 64
BPF_MAXINSNS = 4096


def decode(prog):
    return [struct.unpack_from("<HBBI", prog, i) for i in range(0, len(prog), 8)]


def seccomp_data(nr, arch=AUDIT_ARCH_X86_64, args=()):
    args = tuple(args) + (0,) * (6 - len(args))
    return struct.pack("<iIQ6Q", nr, arch, 0, *[a & (2 ** 64 - 1) for a in args])


def emulate(prog, nr, arch=AUDIT_ARCH_X86_64, args=()):
    """Run the filter on one synthetic syscall; return the 32-bit action."""
    data = seccomp_data(nr, arch, args)
    insns = decode(prog)
    pc, acc = 0, 0
    for _ in range(len(insns) + 1):          # no backward jumps exist in cBPF
        code, jt, jf, k = insns[pc]
        if code == LD_W_ABS:
            acc = struct.unpack_from("<I", data, k)[0]
            pc += 1
        elif code == JEQ_K:
            pc += 1 + (jt if acc == k else jf)
        elif code == JSET_K:
            pc += 1 + (jt if acc & k else jf)
        elif code == RET_K:
            return k
        else:
            raise ValueError("unknown opcode 0x%x at %d" % (code, pc))
    raise ValueError("program did not terminate")


def _load_helper(name, path=HELPER):
    """The helper is extension-less: an explicit SourceFileLoader, bytecode-free
    (the same loader test_sbx_gate.py uses, for the same reason). Also loads the
    probe, by path, for group P."""
    import importlib.machinery
    import importlib.util
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader = importlib.machinery.SourceFileLoader(name, path)
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = prev


def _rec(suite, group, name, problems, extra=(), status=None):
    problems = list(problems)
    if status is None:
        status = H.FAIL if problems else H.PASS
    brief = "%s | %s%s" % (status, name,
                           (" | " + "; ".join(problems)) if problems else "")
    suite.record(group, name, problems, status=status, detail=list(extra),
                 brief=brief, text="; ".join(problems))


def _guard(suite, group, name, fn):
    """Record `fn()`'s problem list; an exception is a FAIL, never a crash --
    the RED run has to report every case, not stop at the first AttributeError."""
    try:
        problems, extra = fn()
    except Exception as exc:              # noqa: BLE001 -- reported, not hidden
        problems, extra = ["raised %s: %s" % (type(exc).__name__, exc)], ()
    _rec(suite, group, name, problems, extra)


def _info(suite, group, name, why):
    _rec(suite, group, name, [], extra=["skipped     : " + why], status=H.INFO)


def _live_host():
    """Linux on x86_64 is the only host the program is built for."""
    return sys.platform.startswith("linux") and os.uname().machine == "x86_64"


# ---------------------------------------------------------------------------
# A: the program
# ---------------------------------------------------------------------------

def _run_program(suite, helper):
    state = {}

    def build():
        prog = helper.seccomp_program("x86_64")
        state["prog"] = prog
        problems = []
        if not isinstance(prog, bytes):
            problems.append("not bytes: %r" % type(prog))
        elif len(prog) % 8:
            problems.append("length %d is not a whole number of sock_filter"
                            % len(prog))
        elif not 0 < len(prog) // 8 <= BPF_MAXINSNS:
            problems.append("%d instructions outside 1..%d"
                            % (len(prog) // 8, BPF_MAXINSNS))
        return problems, ["instructions: %d" % (len(prog) // 8)]
    _guard(suite, GRP_A, "builds bytes: whole sock_filters, <= BPF_MAXINSNS",
           build)
    prog = state.get("prog", b"")

    def structure():
        insns = decode(prog)
        problems = []
        if insns[:1] != [(LD_W_ABS, 0, 0, 4)]:
            problems.append("instruction 0 is %r, not a load of arch (offset 4)"
                            % (insns[:1],))
        if len(insns) > 1 and (insns[1][0], insns[1][3]) != (JEQ_K,
                                                             AUDIT_ARCH_X86_64):
            problems.append("instruction 1 is %r, not JEQ AUDIT_ARCH_X86_64"
                            % (insns[1],))
        for pc, (code, jt, jf, k) in enumerate(insns):
            if code not in KNOWN_OPS:
                problems.append("opcode 0x%x at %d" % (code, pc))
            if code == LD_W_ABS and (k % 4 or k >= SECCOMP_DATA_SIZE):
                problems.append("load offset %d at %d outside seccomp_data"
                                % (k, pc))
            if code in (JEQ_K, JSET_K) and max(pc + 1 + jt, pc + 1 + jf) >= len(
                    insns):
                problems.append("jump at %d leaves the program" % pc)
        if insns and insns[-1][0] != RET_K:
            problems.append("last instruction is not a RET")
        return problems, []
    _guard(suite, GRP_A, "arch check FIRST; known opcodes; jumps in bounds; "
           "ends in RET", structure)

    def foreign_arch():
        problems = []
        for label, arch in (("i386", AUDIT_ARCH_I386),
                            ("aarch64", AUDIT_ARCH_AARCH64), ("zero", 0)):
            for nr in (REF["read"], REF["execve"], REF["ptrace"]):
                got = emulate(prog, nr, arch=arch)
                if got != RET_KILL_PROCESS:
                    problems.append("%s nr %d -> 0x%x" % (label, nr, got))
        return problems, []
    _guard(suite, GRP_A, "foreign arch (i386, aarch64, 0) -> KILL_PROCESS",
           foreign_arch)

    def x32():
        problems = []
        for name in ("read", "write", "execve", "ptrace"):
            got = emulate(prog, REF[name] | X32_SYSCALL_BIT)
            if got != RET_KILL_PROCESS:
                problems.append("x32 %s -> 0x%x" % (name, got))
        got = emulate(prog, -1)
        if got != RET_KILL_PROCESS:
            problems.append("nr -1 (x32 bit set) -> 0x%x" % got)
        return problems, []
    _guard(suite, GRP_A, "x32 bit (nr & 0x40000000) -> KILL_PROCESS", x32)

    def table_matches():
        table = helper._SECCOMP_X86_64
        problems = []
        for name, nr in sorted(table.items()):
            if REF.get(name) != nr:
                problems.append("%s=%d, reference says %r" % (name, nr,
                                                              REF.get(name)))
        if len(set(table.values())) != len(table):
            problems.append("duplicate numbers in the embedded table")
        return problems, ["entries     : %d" % len(table)]
    _guard(suite, GRP_A, "every embedded number == the reference header copy",
           table_matches)

    def allowed():
        allow = helper._SECCOMP_ALLOW
        problems = []
        for name in allow:
            got = emulate(prog, REF[name])
            if got != RET_ALLOW:
                problems.append("%s -> 0x%x" % (name, got))
        return problems, ["allowlisted : %d" % len(allow)]
    _guard(suite, GRP_A, "every allowlisted syscall -> ALLOW", allowed)

    def required():
        allow = set(helper._SECCOMP_ALLOW)
        missing = [n for n in REQUIRED if n not in allow]
        return (["not allowlisted: %s" % ", ".join(missing)] if missing else []), []
    _guard(suite, GRP_A, "the core an ordinary CLI needs is allowlisted",
           required)

    def default_deny():
        allow = set(helper._SECCOMP_ALLOW) | set(SPECIAL)
        problems = []
        denied = 0
        for name, nr in sorted(REF.items(), key=lambda kv: kv[1]):
            if name in allow:
                continue
            got = emulate(prog, nr)
            denied += 1
            if got != DENY:
                problems.append("%s(%d) -> 0x%x" % (name, nr, got))
        known = set(REF.values())
        for nr in range(0, 1024):
            if nr not in known and emulate(prog, nr) != DENY:
                problems.append("unassigned nr %d not EPERM" % nr)
        return problems, ["denied names: %d" % denied]
    _guard(suite, GRP_A, "every other number, assigned or not -> ERRNO(EPERM)",
           default_deny)

    def dangerous():
        allow = set(helper._SECCOMP_ALLOW)
        problems = []
        for name in DANGEROUS:
            if name in allow:
                problems.append("%s is allowlisted" % name)
            got = emulate(prog, REF[name])
            if got != DENY:
                problems.append("%s -> 0x%x" % (name, got))
        return problems, ["checked     : %d names" % len(DANGEROUS)]
    _guard(suite, GRP_A, "the dangerous names are absent and get EPERM",
           dangerous)

    def socket_domain():
        problems = []
        for dom in (AF_UNIX, AF_INET, AF_INET6):
            got = emulate(prog, REF["socket"], args=(dom, 1, 0))
            if got != RET_ALLOW:
                problems.append("socket(%d) -> 0x%x" % (dom, got))
        for dom in (AF_NETLINK, AF_PACKET, AF_ALG, AF_VSOCK, 0, 3):
            got = emulate(prog, REF["socket"], args=(dom, 1, 0))
            if got != DENY:
                problems.append("socket(%d) -> 0x%x" % (dom, got))
        return problems, []
    _guard(suite, GRP_A, "socket: AF_UNIX/INET/INET6 ALLOW, every other "
           "domain EPERM", socket_domain)

    def ioctl_cmd():
        problems = []
        for cmd in (TIOCSTI, TIOCLINUX, TIOCSTI | (1 << 32)):
            got = emulate(prog, REF["ioctl"], args=(0, cmd, 0))
            if got != DENY:
                problems.append("ioctl(0x%x) -> 0x%x" % (cmd, got))
        for cmd in (TCGETS, FIONREAD, 0):
            got = emulate(prog, REF["ioctl"], args=(0, cmd, 0))
            if got != RET_ALLOW:
                problems.append("ioctl(0x%x) -> 0x%x" % (cmd, got))
        return problems, []
    _guard(suite, GRP_A, "ioctl: TIOCSTI/TIOCLINUX EPERM (low 32 bits), the "
           "rest ALLOW", ioctl_cmd)

    def clone_flags():
        problems = []
        for flags in (SIGCHLD, PTHREAD_CLONE_FLAGS, 0x01000000 | 0x00200000
                      | SIGCHLD):
            got = emulate(prog, REF["clone"], args=(flags,))
            if got != RET_ALLOW:
                problems.append("clone(0x%x) -> 0x%x" % (flags, got))
        for name, bit in sorted(CLONE_NS_FLAGS.items()):
            got = emulate(prog, REF["clone"], args=(bit | SIGCHLD,))
            if got != DENY:
                problems.append("clone(%s) -> 0x%x" % (name, got))
        return problems, []
    _guard(suite, GRP_A, "clone: fork/thread flags ALLOW, any CLONE_NEW* EPERM",
           clone_flags)

    def clone3():
        got = emulate(prog, REF["clone3"])
        want = RET_ERRNO | ENOSYS
        return ([] if got == want else
                ["clone3 -> 0x%x, want ERRNO(ENOSYS) 0x%x" % (got, want)]), []
    _guard(suite, GRP_A, "clone3 -> ERRNO(ENOSYS) so libc falls back to the "
           "checked clone", clone3)

    def deterministic():
        again = helper.seccomp_program("x86_64")
        problems = [] if again == prog else ["two builds differ"]
        reenc = b"".join(struct.pack("<HBBI", *i) for i in decode(prog))
        if reenc != prog:
            problems.append("does not round-trip through <HBBI")
        return problems, []
    _guard(suite, GRP_A, "two builds are byte-identical, little-endian <HBBI",
           deterministic)

    def refuses_foreign():
        problems = []
        for machine in ("aarch64", "i686", "armv7l", "x86_64 ", "", "amd64"):
            try:
                helper.seccomp_program(machine)
            except ValueError:
                continue
            problems.append("built a program for machine %r" % machine)
        return problems, []
    _guard(suite, GRP_A, "a non-x86_64 machine refuses to build (ValueError)",
           refuses_foreign)


# ---------------------------------------------------------------------------
# B: _bwrap_argv
# ---------------------------------------------------------------------------

# A fresh /dev first (the host one is nodev, every node EACCES); the FILE secret
# masked with an empty `--ro-bind-data` file (`--ro-bind /dev/null` is nodev too).
# The socket directories (R-0061) are emptied before every bind and made read-only
# after the last one.
GOLDEN = [
    "bwrap", "--ro-bind", "/", "/",
    "--dev", "/dev", "--remount-ro", "/dev",
    "--tmpfs", "/run", "--tmpfs", "/tmp", "--tmpfs", "/var/snap",
    "--bind", "/w", "/w",
    "--tmpfs", "/h/.ssh",
    "--ro-bind", "/h/.claude/skills", "/h/.claude/skills",
    "--ro-bind", "/r/ClaudeCode", "/r/ClaudeCode",
    "--ro-bind-data", "5", "/r/.git/config",
    "--remount-ro", "/run", "--remount-ro", "/tmp", "--remount-ro", "/var/snap",
    "--unshare-pid", "--unshare-ipc", "--proc", "/proc",
    "--unshare-net",
    "--new-session",
    "--die-with-parent",
    "--", "true", "x",
]


def _scope(helper, **kw):
    base = dict(writes=["/w"], net=False, ro=False, argv=["true", "x"],
                secret_paths=(("/h/.ssh", True), ("/r/.git/config", False)),
                carveouts=("/h/.claude/skills",),
                shadow_write_denies=("/r/ClaudeCode",),
                file_mask_fds=(5,))
    base.update(kw)
    return helper.Scope(**base)


def _run_argv(suite, helper):
    def off():
        got = helper._bwrap_argv(_scope(helper))
        return ([] if got == GOLDEN else ["argv %r" % (got,)]), []
    _guard(suite, GRP_B, "flag off: argv is the golden argv byte-for-byte", off)

    def off_with_fd():
        got = helper._bwrap_argv(_scope(helper, seccomp_fd=7))
        return ([] if got == GOLDEN else ["an fd with the flag off leaked "
                                          "into argv: %r" % (got,)]), []
    _guard(suite, GRP_B, "flag off: a stray fd is NOT emitted", off_with_fd)

    def on():
        got = helper._bwrap_argv(_scope(helper, seccomp=True, seccomp_fd=7))
        want = GOLDEN[:-3] + ["--seccomp", "7"] + GOLDEN[-3:]
        problems = [] if got == want else ["argv %r" % (got,)]
        if got.count("--seccomp") != 1:
            problems.append("--seccomp emitted %d times" % got.count("--seccomp"))
        return problems, []
    _guard(suite, GRP_B, "flag on: exactly `--seccomp N` before the terminator",
           on)

    def no_fd():
        try:
            got = helper._bwrap_argv(_scope(helper, seccomp=True))
        except Exception:                 # noqa: BLE001 -- any refusal is right
            return [], []
        return ["returned argv with no program fd: %r" % (got,)], []
    _guard(suite, GRP_B, "flag on with NO fd resolved still refuses "
           "(never runs unfiltered)", no_fd)

    def bad_fd():
        problems = []
        for fd in ("7", "7 --bind / /", -1, None, True, 3.0):
            try:
                got = helper._bwrap_argv(_scope(helper, seccomp=True,
                                                seccomp_fd=fd))
            except Exception:             # noqa: BLE001
                continue
            problems.append("fd %r accepted: %r" % (fd, got))
        return problems, []
    _guard(suite, GRP_B, "a non-int or negative fd refuses (no argv injection)",
           bad_fd)

    def session_and_ipc():
        problems = []
        for kw in ({}, dict(net=True), dict(ro=True, writes=[])):
            got = helper._bwrap_argv(_scope(helper, **kw))
            for flag in ("--new-session", "--unshare-ipc"):
                if got[:got.index("--")].count(flag) != 1:
                    problems.append("%r: %s not emitted once" % (kw, flag))
        return problems, []
    _guard(suite, GRP_B, "every argv carries `--new-session` and `--unshare-ipc`",
           session_and_ipc)

    def mask_fd_missing():
        problems = []
        for fds in (None, (), (5, 6)):
            try:
                got = helper._bwrap_argv(_scope(helper, file_mask_fds=fds))
            except ValueError:
                continue
            problems.append("mask fds %r accepted: %r" % (fds, got))
        return problems, []
    _guard(suite, GRP_B, "a FILE secret without exactly one mask fd refuses",
           mask_fd_missing)

    def pure():
        import builtins
        saved = {}
        targets = [(builtins, "open"), (os, "open"), (os, "write"),
                   (os, "pipe"), (os, "stat"), (os, "lstat"),
                   (os.path, "exists"), (os.path, "isdir")]
        if hasattr(os, "memfd_create"):
            targets.append((os, "memfd_create"))
        touched = []

        def trap(label):
            def _t(*a, **k):
                touched.append(label)
                raise AssertionError("I/O from a pure builder: " + label)
            return _t
        for mod, attr in targets:
            saved[(mod, attr)] = getattr(mod, attr)
            setattr(mod, attr, trap("%s.%s" % (mod.__name__, attr)))
        try:
            a = helper._bwrap_argv(_scope(helper, seccomp=True, seccomp_fd=9))
            b = helper._bwrap_argv(_scope(helper, seccomp=True, seccomp_fd=9))
        finally:
            for (mod, attr), fn in saved.items():
                setattr(mod, attr, fn)
        problems = ["touched %s" % ", ".join(touched)] if touched else []
        if a != b:
            problems.append("two calls differ")
        return problems, []
    _guard(suite, GRP_B, "_bwrap_argv with the flag on performs NO I/O", pure)


# ---------------------------------------------------------------------------
# C: the CLI, in-process
# ---------------------------------------------------------------------------

class _Stubbed:
    """main() with every side effect it could reach replaced by a recorder:
    exec, rlimits, engine detection and (optionally) the platform/machine."""

    def __init__(self, helper, platform=None, machine=None):
        self.helper = helper
        self.platform = platform
        self.machine = machine
        self.execs = []
        self.memfds = []

    def __enter__(self):
        h = self.helper
        self._saved = (os.execvp, h._apply_rlimits, h._detect_engine,
                       h._host_machine, sys.platform, sys.stdout, sys.stderr,
                       getattr(os, "memfd_create", None))

        def _exec(path, argv):
            self.execs.append(list(argv))
            raise OSError("exec stubbed by the suite")
        os.execvp = _exec
        h._apply_rlimits = lambda: None
        h._detect_engine = lambda: (h._bwrap_argv, "/usr/bin/bwrap")
        if self.machine is not None:
            h._host_machine = lambda: self.machine
        if self.platform is not None:
            sys.platform = self.platform
        real_memfd = self._saved[-1]
        if real_memfd is not None:
            def _memfd(*a, **k):
                fd = real_memfd(*a, **k)
                self.memfds.append(fd)
                return fd
            os.memfd_create = _memfd
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        return self

    def __exit__(self, *exc):
        h = self.helper
        self.out, self.err = sys.stdout.getvalue(), sys.stderr.getvalue()
        (os.execvp, h._apply_rlimits, h._detect_engine, h._host_machine,
         sys.platform, sys.stdout, sys.stderr, real_memfd) = self._saved
        if real_memfd is not None:
            os.memfd_create = real_memfd
        return False

    def main(self, argv):
        try:
            return self.helper.main(argv)
        except SystemExit as exc:
            return exc.code


def _run_cli(suite, helper):
    def flag():
        parser = helper._build_parser()
        problems = []
        if parser.parse_args([]).seccomp is not False:
            problems.append("--seccomp is not off by default")
        if parser.parse_args(["--seccomp"]).seccomp is not True:
            problems.append("--seccomp does not set the flag")
        return problems, []
    _guard(suite, GRP_C, "--seccomp exists and is OFF by default", flag)

    def dry_run():
        with _Stubbed(helper) as st:
            rc = st.main(["--dry-run", "--ro", "--seccomp", "--", "true"])
        problems = []
        if rc != 0:
            problems.append("rc %r" % rc)
        if st.execs:
            problems.append("exec'd %r" % st.execs)
        if st.memfds:
            problems.append("created a program fd on a dry run")
        if "seccomp" not in st.out or "True" not in st.out.split("seccomp", 1)[1]:
            problems.append("plan does not show seccomp True: %r" % st.out)
        return problems, []
    _guard(suite, GRP_C, "--dry-run --seccomp shows it, creates no fd, runs "
           "nothing", dry_run)

    def not_linux():
        with _Stubbed(helper, platform="darwin", machine="x86_64") as st:
            rc = st.main(["--ro", "--seccomp", "--", "true"])
        problems = []
        if rc != 3:
            problems.append("rc %r, want 3" % rc)
        if st.execs:
            problems.append("exec'd %r" % st.execs)
        if "seccomp" not in st.err:
            problems.append("stderr does not name seccomp: %r" % st.err)
        return problems, ["stderr      : %s" % st.err.strip()]
    _guard(suite, GRP_C, "non-Linux platform + --seccomp REFUSES (exit 3, "
           "nothing exec'd)", not_linux)

    def foreign_machine():
        with _Stubbed(helper, platform="linux", machine="aarch64") as st:
            rc = st.main(["--ro", "--seccomp", "--", "true"])
        problems = []
        if rc != 3:
            problems.append("rc %r, want 3" % rc)
        if st.execs:
            problems.append("exec'd %r" % st.execs)
        if "aarch64" not in st.err:
            problems.append("stderr does not name the machine: %r" % st.err)
        return problems, ["stderr      : %s" % st.err.strip()]
    _guard(suite, GRP_C, "Linux on a non-x86_64 machine + --seccomp REFUSES "
           "(exit 3)", foreign_machine)

    def off_unchanged():
        with _Stubbed(helper, platform="linux", machine="x86_64") as st:
            st.main(["--ro", "--", "true"])
        problems = []
        if len(st.execs) != 1:
            problems.append("exec count %d" % len(st.execs))
        elif "--seccomp" in st.execs[0]:
            problems.append("--seccomp emitted with the flag off")
        if st.memfds:
            problems.append("created a program fd with the flag off")
        return problems, []
    _guard(suite, GRP_C, "flag off: no fd created, no --seccomp in the exec'd "
           "argv", off_unchanged)

    def file_masks_wired():
        saved = helper.secret_paths        # stubbed: deterministic, writes nothing
        helper.secret_paths = lambda _cwd: (("/x/.git/config", False),
                                            ("/x/.ssh", True))
        try:
            with _Stubbed(helper, platform="linux", machine="x86_64") as st:
                st.main(["--ro", "--", "true"])
        finally:
            helper.secret_paths = saved
        argv = st.execs[0]
        fd = int(argv[argv.index("/x/.git/config") - 1])
        try:
            ok = os.get_inheritable(fd) and os.read(fd, 1) == b""
        finally:
            os.close(fd)
        return ([] if ok else ["mask fd %d not inheritable+empty" % fd]), []
    _guard(suite, GRP_C, "file masks: the exec'd argv names an inheritable, "
           "EMPTY fd for the FILE secret", file_masks_wired)

    if not (_live_host() and hasattr(os, "memfd_create")):
        _info(suite, GRP_C, "Linux x86_64: exec'd argv names an inheritable fd "
              "holding the program", "needs Linux x86_64 with os.memfd_create; "
              "this host is %s/%s" % (sys.platform, os.uname().machine))
        return

    def wired():
        with _Stubbed(helper) as st:
            st.main(["--ro", "--seccomp", "--", "true"])
        problems = []
        try:
            if len(st.execs) != 1:
                return ["exec count %d" % len(st.execs)], []
            argv = st.execs[0]
            i = argv.index("--seccomp")
            fd = int(argv[i + 1])
            if fd not in st.memfds:
                problems.append("fd %d is not the memfd sbx created" % fd)
            if not os.get_inheritable(fd):
                problems.append("fd %d is not inheritable" % fd)
            if os.lseek(fd, 0, os.SEEK_CUR) != 0:
                problems.append("fd offset is not 0 (bwrap would read nothing)")
            body = os.pread(fd, 1 << 20, 0)
            if body != helper.seccomp_program("x86_64"):
                problems.append("fd content != seccomp_program('x86_64')")
        finally:
            for fd in st.memfds:
                try:
                    os.close(fd)
                except OSError:
                    pass
        return problems, []
    _guard(suite, GRP_C, "Linux x86_64: exec'd argv names an inheritable fd "
           "holding the program", wired)


# ---------------------------------------------------------------------------
# D: live
# ---------------------------------------------------------------------------

def _run_live(suite):
    if not _live_host():
        _info(suite, GRP_D, "prctl probe: CLIs run, denied calls EPERM",
              "needs Linux x86_64; this host is %s/%s"
              % (sys.platform, os.uname().machine))
        return

    def probe():
        rc, out, err = H.run_process([sys.executable, "-I", PROBE, HELPER],
                                     timeout=120)
        problems = [] if rc == 0 else ["probe rc %d" % rc]
        lines = [ln for ln in out.splitlines() if ln.strip()]
        problems += [ln for ln in lines if ln.startswith("FAIL")]
        if err.strip():
            problems.append("stderr: %s" % err.strip()[-300:])
        return problems, lines
    _guard(suite, GRP_D, "prctl probe: CLIs run, denied calls EPERM", probe)


# ---------------------------------------------------------------------------
# P: the probe's header cross-check, as a pure function
# ---------------------------------------------------------------------------

def _run_probe_header(suite):
    """An older header lacks the newest names (Ubuntu 22.04: futex_waitv 449 ..):
    absence is INFO; only a DIFFERENT number is FAIL."""
    def verdicts():
        probe = _load_helper("sbx_seccomp_probe_suite", PROBE)
        got = probe.header_verdicts({"read": 0, "write": 1, "futex_waitv": 449},
                                    {"read": 0, "write": 2}, "/h.h")
        rows = ["%s %s: %s" % row for row in got]
        fail = [r for r in rows if r.startswith("FAIL")]
        info = [r for r in rows if r.startswith("INFO")]
        problems = []
        if len(fail) != 1 or "write=1" not in fail[0] or "futex" in fail[0]:
            problems.append("want one FAIL naming only write")
        if len(info) != 1 or "futex_waitv=449" not in info[0]:
            problems.append("want one INFO naming futex_waitv")
        return problems, rows
    _guard(suite, GRP_P, "a name absent from the header is INFO; a different "
           "number is FAIL", verdicts)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="sbx --seccomp: the Linux BPF allowlist",
                    opts=opts, mode="grouped")
    before = H.pycache_snapshot()
    helper = _load_helper("sbx_helper_seccomp")
    _run_program(suite, helper)
    _run_argv(suite, helper)
    _run_cli(suite, helper)
    _run_live(suite)
    _run_probe_header(suite)
    after = H.pycache_snapshot()
    new = sorted(set(after) - set(before))
    _rec(suite, GRP_H, "no __pycache__ left behind",
         ["new bytecode: %s" % ", ".join(new[:5])] if new else [])
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
