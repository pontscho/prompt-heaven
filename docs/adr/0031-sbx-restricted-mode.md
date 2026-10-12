---
name: 0031-sbx-restricted-mode
type: adr
status: active
title: sbx restricted mode -- an allowlist profile, a filtered exec, a clean env, a supervised timeout and strict rlimits
description: Decision to give sbx an opt-in restricted mode for running untrusted scripts -- reads granted by spelling instead of read-anywhere, process-exec filtered to measured realpaths, a target environment set exactly through a /usr/bin/env -i trampoline with an empty engine env, a supervised fork with a JSON status fd and kill-before-reap, strict soft-equals-hard rlimits, an ACL-write deny and a literal 100-level depth cap -- auto-allowed by the gate only where it narrows; amends ADR 0005. With the M0 measurements that decided each fork, the finding that the 8 GiB RLIMIT_AS default is inert on macOS, the flatten remover and quarantine that clean a hostile workdir, the alternatives rejected, the gates and what their first runs measured, the security review round 1 fixes (a cwd floor and one SBPL refusal point, reads that resolve to a socket refused, strict limits that never raise the soft limit, a gate that splits like bash and allowlists every word, fd-relative darwin cleanup and a survivor sweep), and the declared limits -- among them that a restricted Seatbelt run can read the argv and environment of any same-uid process through the unfiltered sysctl-read.
sources:
  - ClaudeCode/skills/sandbox-run/scripts/sbx:_seatbelt_restricted_head
  - ClaudeCode/skills/sandbox-run/scripts/sbx:_supervise
  - ClaudeCode/skills/sandbox-run/scripts/sbx:_strict_rlimits
  - ClaudeCode/hooks/sbx-gate.py
  - Scripts/mcp-sandbox.py:remove_tree
  - tests/test_sbx_restrict.py
  - tests/test_mcp_sandbox.py
verified:
  commit: 7b603d2
  date: 2026-10-11
links:
  - 0005-approve-the-wrapper-not-the-command
  - 0007-a-path-spelled-deny-protects-the-spelling
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
  - 0024-pure-python-39-and-the-stdlib
  - spec-sandbox-run
  - skills
  - hooks
  - tests
  - mcp-skill
---

# ADR 0031: sbx restricted mode

Status: accepted 2026-10-11 as the design the code in the working tree
implements, on top of `4d3640c`, and not yet committed when this page was
written. That is why the frontmatter says `draft` and carries `targets:`
instead of `sources:` and `verified:`: a `verified.commit` cannot vouch for code
no commit holds yet. Once the code lands, the page is promoted to `active` with
that commit, and from then on it is append-only. It amends
[[0005-approve-the-wrapper-not-the-command]]; that ADR is accepted and gets its
pointer as a dated addendum, never an edit. Every number below is a measurement
or a count at the moment of the decision. The M0 measurements were taken on
macOS 14.2.1 (Darwin 23.2.0, x86_64), APFS, Python 3.14.0. The runner that is
the first caller of this mode, and the skill server in front of it, are ADR
0032's subject (a runner behind a proxy), not this page's.

## Context: a wrapper built for trusted commands

ADR 0005 made `sbx` the one audited containment wrapper for Bash commands the
agent itself chose. Its profile fits that caller. It reads anywhere
(`(allow file-read* (subpath "/"))` on Seatbelt, `--ro-bind / /` on bwrap) and
carves a fixed secret set back out. It execs anything. It hands the target the
caller's whole environment. It has no wall clock, and its rlimits are best
effort: a limit the kernel refuses is skipped
`ClaudeCode/skills/sandbox-run/scripts/sbx:_apply_rlimits`.

A sandboxed script runner has a different caller: a script written by a skill
author, not chosen by the agent. Against that caller each of those defaults is
an escape. A read-anywhere profile reads the user's repositories and store
root. An unfiltered exec runs a binary the script wrote. An inherited
environment carries tokens. Without a wall clock a loop runs forever, and a
refused limit that is silently skipped is no limit. The question was whether
to build a second wrapper or to give `sbx` a mode that removes each of those
defaults, and which rules each removal needs on a real host.

## Decision

`sbx` gains a restricted mode, opt-in flag by flag. Without the new flags the
legacy output is byte-identical (`sbx_restrict:D4` Seatbelt, `sbx_restrict:F9`
bwrap).

1. **Restricted reads (`--restrict-reads`, `--read PATH`).** The read-anywhere
   baseline is replaced by an allowlist
   `ClaudeCode/skills/sandbox-run/scripts/sbx:_seatbelt_restricted_head`:
   `file-read-data` on the literal `/` only, `file-read-metadata` as one
   LITERAL per proper ancestor of every allowed path
   `ClaudeCode/skills/sandbox-run/scripts/sbx:metadata_ancestors` (never a
   global metadata rule, `sbx_restrict:E4`), each `--read` spelling, reads on
   the write scopes, and its own `/dev/null` read+write literal. A `--read` is
   kept AS SPELLED: absolute, `normpath`, the injection floor, must exist, and
   never `realpath`'d `ClaudeCode/skills/sandbox-run/scripts/sbx:canonicalize_read`.
   A symlink becomes a literal, a directory a subpath, a file a literal
   `ClaudeCode/skills/sandbox-run/scripts/sbx:resolve_reads`, so each hop of a
   chain is one rule the caller passes. The `~/.claude` carve-outs are not
   emitted: a carve-out is a read grant. On Linux the base becomes `/usr`,
   `/lib*`, `/bin`, `/sbin` and a few `/etc` files
   `ClaudeCode/skills/sandbox-run/scripts/sbx:linux_base_reads`, and the
   `shadow_write_denies` re-binds are dropped for every target outside every
   write scope, because on an empty root a `--ro-bind` is a read grant of the
   repo trees (`sbx_restrict:F10`). Restricted mode refuses `--net`.
2. **A filtered exec (`--exec PATH`).** Restricted mode never emits the
   unfiltered `(allow process-exec)`. It emits one filter of literals (files)
   and subpaths (directories): the argv0 spelling, `/usr/bin/env` when the env
   is clean, and every `--exec` entry. A request with nothing to filter on is
   refused with exit 3 rather than given an unfiltered allow. Seatbelt matches
   the REALPATH of what is exec'd (M0 3g), so the caller passes realpaths and
   `sbx` never resolves one.
3. **A clean env at two layers (`--clean-env`, `--env K=V`).** The target's
   environment is exactly the given set: keys `^[A-Za-z_][A-Za-z0-9_]{0,127}$`,
   no `LD_*` or `DYLD_*`, no duplicates. The unsandboxed engine always runs with
   an EMPTY environment on both platforms
   `ClaudeCode/skills/sandbox-run/scripts/sbx:_prepare_exec`. On darwin the
   target argv becomes `/usr/bin/env -i K1=V1 ... <argv0> ...` (keys sorted), so
   the pairs are applied inside the profile; argv0 must be absolute and must
   not contain `=`, which BSD `env` would read as an assignment
   (`sbx_restrict:T4`, `sbx_restrict:T6`). On Linux the pairs travel as bwrap
   `--clearenv --setenv K V`.
4. **A supervised timeout and one status protocol (`--timeout`,
   `--status-fd`).** With `--timeout`, `sbx` forks; the child `setsid()`s,
   applies its limits and execs the engine; the parent kills the whole process
   group on expiry and exits 124 with one stderr marker line
   `ClaudeCode/skills/sandbox-run/scripts/sbx:_supervise`. `--status-fd` carries
   JSON lines of at most 512 bytes: one `spawned` event with the target's `pid`
   and `pgid`, then exactly one terminal `exit` event whose `outcome` is
   `exited`, `signaled`, `timeout` or `refused`
   `ClaudeCode/skills/sandbox-run/scripts/sbx:_write_status`. A caller above
   `sbx` can therefore kill the group even if `sbx` itself dies, and never has
   to read meaning into an exit code the target can also produce. The
   SIGTERM/SIGINT/SIGHUP handlers are installed BEFORE `fork()` and the child
   restores `SIG_DFL` before exec, so a signal between the fork and the
   `spawned` write is never lost (`sbx_restrict:U7`).
5. **Kill before reap.** The supervisor kills the group while the leader is
   still an unreaped zombie, so its pgid cannot be recycled between reap and
   kill `ClaudeCode/skills/sandbox-run/scripts/sbx:_wait_exit_unreaped`. Linux
   polls `waitid(WEXITED | WNOWAIT | WNOHANG)`. darwin registers a kqueue
   `KQ_FILTER_PROC` / `KQ_NOTE_EXIT` event right after the fork, and the path is
   selected by `sys.platform == "darwin"`, never by the absence of `os.waitid`,
   which Python >= 3.13 has on darwin (M0 item 1). Then `killpg(SIGKILL)` with
   `ESRCH` and `EPERM` ignored (macOS answers `EPERM` for a zombie-only group),
   then `waitpid` `ClaudeCode/skills/sandbox-run/scripts/sbx:_sweep`
   (`sbx_restrict:U6`).
6. **Strict requested rlimits (`--rlimit-as`, `-cpu`, `-nproc N|+N`, `-fsize`,
   `-nofile`, `-core`).** A requested limit is set soft == hard, a value above
   the current hard is refused rather than clamped, and a limit the kernel
   refuses is exit 3 with nothing run
   `ClaudeCode/skills/sandbox-run/scripts/sbx:_strict_rlimits`
   (`sbx_restrict:V3`). A strict limit only ever narrows: a value above the
   current finite SOFT limit is refused too, exit 3, because
   `setrlimit(value, value)` would raise it (security review round 1, R1,
   below; `sbx_restrict:X6`). `+N` is the real uid's current process count plus N,
   counted immediately before the fork
   `ClaudeCode/skills/sandbox-run/scripts/sbx:uid_process_count`. Unrequested
   limits keep the legacy best-effort path.
7. **Two write-side rules for the write scope.** One
   `(deny file-write-acl ...)` over the write scopes, and NO `file-write-flags`
   deny (M0 3l, below; `sbx_restrict:E10`). And a depth cap: no
   `file-write-create` below 100 directory levels under any write scope, with
   the group `[^/]+/` written out literally 100 times
   `ClaudeCode/skills/sandbox-run/scripts/sbx:_depth_cap_regex`
   (`sbx_restrict:E1`, `sbx_restrict:L12`). Both are Seatbelt only and sit
   after every allow, because Seatbelt is last-match-wins.
8. **The parser refuses abbreviations.** `_build_parser` passes
   `allow_abbrev=False` `ClaudeCode/skills/sandbox-run/scripts/sbx:_build_parser`:
   argparse would otherwise resolve `--rlimit-a` or `--restrict` to a full flag
   the gate never saw. Legacy abbreviations such as `--dry` are now exit 2 too
   (`sbx_restrict:A15`). `--dry-run` prints every new plan field, env KEYS
   only.

### The gate auto-allows only what narrows

The gate's flag loop is driven by three tables, `_BOOL_FLAGS`, `_VALUE_FLAGS`
and `_PROMPT_FLAGS` `ClaudeCode/hooks/sbx-gate.py:_PROMPT_FLAGS`, and a parity
row holds their union equal to the parser's option set, legacy flags included
(`sbx_restrict:S14`). A new flag is auto-allowed only when it narrows relative
to today's defaults. `--read` and `--exec` must be absolute and free of shell
metacharacters (`sbx_restrict:S15`); `--env` must have a valid key that is not
`LD_*`/`DYLD_*`; any `--timeout` narrows, because `sbx` had no wall clock.
`--rlimit-as`, `--rlimit-cpu` and `--rlimit-nproc` prompt above `sbx`'s current
defaults of 8 GiB, 900 s and 512 `ClaudeCode/hooks/sbx-gate.py:_RLIMIT_AS_MAX`
(`sbx_restrict:S11`): a higher value raises a limit and does not narrow.
`--net` still always prompts (`sbx_restrict:S13`). `--status-fd` ALWAYS prompts
(`sbx_restrict:S16`): it makes the unsandboxed `sbx` parent write JSON into
whatever fd the Bash tool's process inherited, a write target the gate cannot
see. The one caller that uses it, the runner, spawns `sbx` directly and never
goes through the gate.

**Security review round 1 (2026-10-11) tightened the gate in five places**
`ClaudeCode/hooks/sbx-gate.py:is_clean_sbx`, each a case where the gate and
bash read one command differently. Every change only turns an auto-allow into
a prompt, so the visible effect is more prompts, never fewer:

- **G1, the alphabet.** The raw command must be printable ASCII (0x20-0x7E)
  plus tab, checked before anything is stripped
  `ClaudeCode/hooks/sbx-gate.py:_COMMAND_RE`, and it is split on space and tab
  only `ClaudeCode/hooks/sbx-gate.py:_WORD_SEP_RE`. `str.split()` also splits on
  form feed, vertical tab, carriage return, `\x1c`-`\x1f`, U+0085, U+00A0 and
  U+2000 onwards, which bash does not, so `--env X=1<FF>-- --net` showed the
  gate a phantom bare `--` before `--net`, while `sbx` received `X=1<FF>--` as
  one word and then opened the network.
- **G2, the word allowlist.** Every word, flag or value, before or after the
  `--`, must match `[A-Za-z0-9._/=+:,@%~-]+`
  `ClaudeCode/hooks/sbx-gate.py:_WORD_RE`, so `[`, `]`, `#`, `!`, `^` and any
  character not yet thought of prompt. `[` and `]` also joined `METACHARS`: the
  gate took `--write [x]` as a literal, contained `<cwd>/[x]` while bash
  expanded it to whatever `x` matched, for example a planted symlink to `/`.
- **G3, the tilde rule.** A `~` is accepted only as a bare `~` or a leading
  `~/` `ClaudeCode/hooks/sbx-gate.py:_tilde_ok`. `os.path.expanduser` leaves
  `~+`, `~-` and `~user` literal while bash expands them, so `--write ~+/..`
  resolved to the project for the gate and to its parent for `sbx`. `sbx`
  applies the same rule to a `--write` scope, exit 2
  `ClaudeCode/skills/sandbox-run/scripts/sbx:canonicalize` (`sbx_restrict:X8`).
- **R1, `--rlimit-core`.** Only `0` auto-allows. A non-zero core limit turns
  kernel core dumps on, and they are written outside every write scope.
- **R2, `--rlimit-nproc`.** Only an absolute `N <= 512` auto-allows. The `+N`
  form is headroom over the uid's CURRENT process count, so it can land above
  the absolute 512 default; it prompts in both spellings.

The rows are in `sbx_gate`'s whitebox group `tests/test_sbx_gate.py:_run_whitebox`:
each attack form beside a control that pins the narrow form still auto-allowed
(a tab-separated command, a target `~/x`, `--rlimit-core 0`, `--rlimit-nproc 512`).

## The measurements that decided the forks (M0)

Each design fork was measured before code was written. No STOP rule fired: not
the kqueue one (item 1), not the setuid one (3k), not the ACL one (3l).

| item | measured | what it decided |
|---|---|---|
| 1 | kqueue `NOTE_EXIT` arrives in ~0.30 s on python3 3.14.0 and 3.9.21 with the child still unreaped; `killpg` kills the setsid grandchild; `killpg` on a zombie-only group answers `EPERM`; `hasattr(os, "waitid")` is True on 3.14, False on 3.9 | kill-before-reap on darwin through kqueue, selected by platform (Decision 5) |
| 2 | idle VSZ ~32.5 GiB for python3, node, both bashes and an sbx-sized launcher; effective AS budget ~1.27 GiB per interpreter (python3 1.279, node 1.250, bash 1.267 / 1.268 GiB) | the AS limit is measured VSZ plus budget; the `sh ulimit` trampoline stays deferred |
| 3a | python running `/bin/sh -c 'echo ok'` works | `/bin` in the exec filter |
| 3b | a planted binary and a planted `#!/bin/sh` script in the workdir are both EPERM at exec | the exec filter checks the script's own path |
| 3c | `(target self)`: `kill $!` EPERM; `(target same-sandbox)`: works; a bare-name PATH lookup fails without metadata on the PATH dirs (127) | `(allow signal (target same-sandbox))`; exec-dir metadata, below |
| 3d | a setsid grandchild survives `killpg` and keeps the output pipes open | T11 stays declared; the runner drains with a bound |
| 3e | `chflags uchg` is permitted in the write scope; the owner clears it outside | why `file-write-flags` is not denied |
| 3f | only fds 0-2 reach the target; `/dev/fd` is unlistable | |
| 3g | the exec filter matches the REALPATH; a spelling literal alone gives `sandbox-exec` exit 71 | exec entries are realpaths |
| 3h | `ENV_ADDITIONS`: python3 none (no `__PYVENV_LAUNCHER__`), node none, bash `PWD SHLVL _` as a child sees them; `/usr/bin/env -i` and dyld add nothing; node also needs `/usr/local/etc/openssl@3/openssl.cnf`, `/dev/null` rw and `(allow sysctl-read)` | the trampoline (Decision 3); the env rows assert set equality. "python3 none" is a restricted-profile result: Seatbelt denies the locale read, so PEP 538 coercion sets nothing. The live T1/T2/T7 rows run the legacy profile, where the locale file is readable and python3 exports `LC_CTYPE` itself after exec (re-measured 2026-10-11). So the test keys `ENV_ADDITIONS` per profile, and set equality holds per profile |
| 3i | python3 needs `.../Versions/3.14/Resources/Python.app/Contents/MacOS/Python` in the exec filter, else exit 1 with `posix_spawn: ... Undefined error: 0`, not EPERM text | the framework launcher literal; no row keys on EPERM text there |
| 3j | `/bin/sh` runs without a read of `/private/var/select/sh` but prints `Error opening /private/var/select/sh: Operation not permitted` every time | every darwin plan reads that one literal |
| 3k | `crontab`, `at`, `sudo` (setuid-root) are refused by Seatbelt itself, kernel operation `forbidden-exec-sugid`: sh exit 126 inside the profile, 71 as argv0; it holds under the filter, under an unfiltered exec and under `(allow default)`; no setuid binary ever ran | the `/bin` and `/usr/bin` exec subpaths stay |
| 3l | `file-write-acl`, `-flags`, `-xattr`, `-mode` are valid operation names; `file-write-acl` stops `chmod +a` and `acl_set_file`; `file-write-flags` stops `chflags uchg` but breaks `shutil.copy2` / `copytree` (copystat's `chflags`); `file-write-mode` breaks chmod | deny `file-write-acl` ONLY |
| 4 | bottom-up removal of deep chains is depth-priced (mkdir+rmdir pair 59 us at depth 100, 9.8 ms at 1000, 27.7 ms at 3000; `fstatat` ~3.7 us constant); a 10 000-level chain took 445.66 s; always-`fchmod` used the whole 10 s on 2560 levels; one fd per frame hit EMFILE at depth 252 under soft NOFILE 256 | the flatten remover, conditional `fchmod`, the bounded fd window |
| 4b | flatten removal of rename-chained trees: 2079 levels in 0.294 s / 0.278 s, 4059 levels in 0.556 s / 0.550 s, at most 50 fds, nothing left | the remover meets the 10 s cleanup deadline |
| 5 | keg totals python3 4162 files, node 2602, openssl@3 7609; the largest targeted image dir is python3's `lib-dynload`, 76 Mach-O files | `_KEG_SCAN_MAX_FILES = 512` (76 x 4, rounded up to a power of two) `Scripts/mcp-sandbox.py:_KEG_SCAN_MAX_FILES` |
| 6 | raw planning spawns: bash median 78.6 ms, python3 172.2 ms, node 214.9 ms; three interpreters 465.6 ms median, 592.6 ms worst; symlinked ANCESTOR dirs must be read literals (else exit 71) | no per-entry smoke `read_timeout` |
| depth | the bounded quantifiers `{n}`, `{n,}`, `{m,n}` compile and silently never match; the literal 100-group refuses level 101 (absolute, `chdir`- and `*at`-relative, past PATH_MAX); a rename DESTINATION is checked as `file-write-create`, the depth of the moved subtree is not | the literal depth cap; the rename-chaining limit below |

Two rules came out of 3c and item 6 that the table only names. **Exec-dir
metadata is gated on PATH.** A bare command name is found by `stat`ing
`<dir>/<name>` along `$PATH`, and an exec allow is not a metadata allow, so
`sbx` emits metadata subpaths for the `--exec` directories only when the target
env can carry a `PATH`: inherited, or `PATH` among the `--env` keys. No PATH,
no lookup, no oracle. **Symlinked parent directories are read literals.**
`/usr/local/opt/node` in `/usr/local/bin/node -> /usr/local/opt/node/bin/node`
is read, never exec'd; without it `sandbox-exec` exits 71.

## The 8 GiB RLIMIT_AS default is inert on macOS

XNU refuses `setrlimit(RLIMIT_AS)` below the calling process's CURRENT VSZ, and
the caller is `sbx`, a python process at ~32.5 GiB VSZ. The legacy default
`_RLIMIT_AS_BYTES = 8 GiB` `ClaudeCode/skills/sandbox-run/scripts/sbx:_RLIMIT_AS_BYTES`
is therefore always refused there, and `_lower_to` swallows the refusal ("skip,
don't brick") `ClaudeCode/skills/sandbox-run/scripts/sbx:_apply_rlimits`. On
macOS a legacy `sbx` run has never had an address-space limit. It is recorded,
not changed: the legacy path stays best effort by ADR 0005's design. The strict
path cannot fall into the same hole: a requested `--rlimit-as` below the VSZ is
exit 3, nothing runs (`sbx_restrict:V3`). The runner therefore asks for
`max(VSZ(interpreter), VSZ(sbx launcher)) + budget + 256 MiB`, measured under the
production launch path and re-measured on every plan refresh. That is why the
effective budget is ~1.27 GiB and not the 1 GiB configured.

## Cleaning a hostile workdir

The write scope is a fresh 0700 workdir that must be gone after every outcome,
and the script owns everything in it. `shutil.rmtree` is not used: it recurses,
and it cannot enter a `chmod 000` directory. The remover is iterative and
fd-relative, every open `O_NOFOLLOW`, and it FLATTENS
`Scripts/mcp-sandbox.py:remove_tree`. It walks a segment at most
`_FLATTEN_DEPTH = 50` levels deep `Scripts/mcp-sandbox.py:_FLATTEN_DEPTH`; the
open frames of that walk are the fd window, so at most 50 directory fds are open
at once. Every subdirectory met at relative depth 50 is lifted with one
fd-relative rename to a fresh top-level `.sbx-flat-%07d` name. When every
segment is at most 50 deep, each is removed bottom-up. Every mutation stays
shallow, which is what item 4 priced.

Per directory: `chmod 0700` of the entry through its parent fd, never
following a symlink, when the open is refused
`Scripts/mcp-sandbox.py:_Remover`; `fchmod(fd, 0o700)` before listing ONLY when
`(st_mode & 0o700) != 0o700`, since `fchmod` is a depth-priced mutation and the
`fstat` is not; and on darwin, when an operation is still refused, a flag clear
and then an ACL removal before giving up on the entry. **Every darwin fallback
is fd-relative** (security review round 1, W1/W2): the entry is reached as ONE
name under its verified parent fd through `setattrlistat(2)` with
`FSOPT_NOFOLLOW`, flags set to 0 `Scripts/mcp-sandbox.py:_clear_flags_at` and
the extended ACL removed as a `kauth_filesec` with no entries
`Scripts/mcp-sandbox.py:_remove_acl_at`, and the parent through its own fd with
`fchflags(fd, 0)` and an empty `acl_set_fd_np`
`Scripts/mcp-sandbox.py:_strip_acl_fd`. No call takes a spelled path any more:
the earlier `lchflags`, `chmodx_np` and `acl_set_file` fallbacks by spelled
path, with their prefix and inode re-checks, are gone. The reason was measured:
the item-3l broad deny ACE refuses every `open(2)` of the entry, `O_EVTONLY`
included, so an fd on the entry itself was never available, while
`setattrlistat` on the parent fd needs no open of the entry; on this host it
cleared `uchg` and removed that ACE on a file and on a directory, mode 000 or
not, and on a symlink it changed the link, not its target. What these calls
cannot clear stays, and the tree is quarantined; there is no path-based last
resort (`mcp_sandbox:H22`). `acl_set_link_np` is still not used: one broad deny
ACE that includes `readattr` or `readsecurity` defeated it (M0 3l). The deadline
is checked before EVERY mutating operation, and `_RM_MAX_OPS = 1_000_000` bounds
the total `Scripts/mcp-sandbox.py:_RM_MAX_OPS`. A directory is listed in batches
of `_RM_LIST_BATCH = 1024` entries with a deadline check before each batch, and
at most `_RM_PENDING_MAX = 65536` of its entries are held at once; a directory
holding more is listed again once those are gone
`Scripts/mcp-sandbox.py:_RM_PENDING_MAX` (Q1, `mcp_sandbox:H21`).

**Survivors are swept before every removal** (security review round 1, W6)
`Scripts/mcp-sandbox.py:_sweep_survivors`. `sbx` is reaped and the target group
killed before cleanup starts, but on darwin a process that `setsid()`s or
`setpgid()`s out of that group survives the kill (T11) and could still be
writing into the workdir while the remover works on it. So before the workdir
is removed, and before every quarantine retry, the run's session is killed
when its leader is no longer alive under that pid, and on darwin every euid
process still in that session, or whose cwd is inside the tree about to be
removed, is `SIGKILL`ed: at most `_SWEEP_ROUNDS = 5` rounds over at most
`_SWEEP_PIDS_MAX = 16384` pids each, all inside the caller's cleanup deadline
`Scripts/mcp-sandbox.py:_cleanup_workdir` (`mcp_sandbox:H23`). On Linux only the
session kill runs: bwrap's `--unshare-pid --die-with-parent` already tears the
pid namespace down with `sbx`. Its two limits are declared below.

A workdir that still resists is quarantined: moved into
`<work_root>/quarantine/`, and the runner keeps serving
`Scripts/mcp-sandbox.py:Quarantine`. The runner goes `degraded` only when more
than `_QUARANTINE_MAX = 16` entries are quarantined
`Scripts/mcp-sandbox.py:_QUARANTINE_MAX`. The ACL deny of Decision 7 means a
script cannot set those ACEs in the first place, so the strip is defence in
depth.

Every retry is bounded by one shared deadline, never one per entry
`Scripts/mcp-sandbox.py:sweep_work_root`. The run bound is 2 s degraded retry +
30 s plan term + 120 s maximum script timeout + 10 s hard-deadline slack + 2 s
kill grace + 0.5 s drain + 10 s cleanup = 174.5 s, under the runner's 175 s.
Startup is a 20 s sweep plus 75 s of interpreter planning, 95 s under the runner
proxy's `startup_timeout` of 120 s. There is one kill grace,
`_KILL_GRACE_S = 2.0` `Scripts/mcp-sandbox.py:_KILL_GRACE_S`, for both the
cancel escalation and the hard deadline. A cancel during a plan refresh never
marks it failed and starts no 5 s suppression (`mcp_sandbox:G13`). Measured:
`H19` removes a ~2079-level rename-chained tree inside the 10 s deadline in a
child under soft NOFILE 256. Startup to the `initialize` reply took a median
0.938 s for `/bin/bash` alone and 2.699 s for python3, bash and node.

## Security review round 1 (2026-10-11): what changed in sbx

A gated security review of the working tree (findings recorded as R1, P1, P2,
P4 and M1) changed five things in `sbx` itself. The gate's half is in "The gate
auto-allows only what narrows" above, the remover's half in "Cleaning a hostile
workdir". Each row below is in `sbx_restrict` group X, written red first.

- **P1, a cwd floor and one SBPL refusal point (CWE-74).** The working
  directory reached the profile without any flag check: its ancestors are the
  restricted metadata literals, `<cwd>/.claude/tmp` is the default write
  subpath in both modes, and every ancestor's `.git/config` is a deny. A
  directory named `a"))(allow file-write* (regex #"^"))(allow file-read-metadata (literal "b`
  was a write-anywhere rule. `main()` now refuses a cwd, or a cwd whose
  realpath, contains `"`, `'`, `(`, `)`, `\` or any C0 control character or
  DEL, with one stderr line and exit 3, in both modes and under `--dry-run`
  too, before any scope is computed
  `ClaudeCode/skills/sandbox-run/scripts/sbx:floor_cwd` (`sbx_restrict:X1`,
  `sbx_restrict:X2`). The same character class, which used to name newline as
  its only control character, now holds every C0 control and DEL
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_INJECT_CHARS`. And every path
  the Seatbelt builder interpolates into an SBPL string passes ONE function,
  which refuses rather than escapes, so a derived path ($HOME, a symlink target,
  an ancestor) cannot open a rule either
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_sbpl_path` (`sbx_restrict:X3`).
- **P2, a `--read` or `--exec` that resolves to a socket (CWE-41).** The bwrap
  builder checked a read against the socket directories by its spelling only,
  so `--read /var/run/docker.sock` (a symlink to `/run`, which was not listed)
  was ro-bound past the `/run` tmpfs, and a read-only bind does not stop
  `connect()`. Every hop of the spelling's symlink chain is now checked as
  spelled and as realpath'd, then the final realpath, against
  `SOCKET_REFUSED_DIRS`, which is `SOCKET_DIRS` plus `/var/run`, and a target
  that IS a socket (`S_ISSOCK`) is refused wherever it lives; a chain of more
  than 40 hops is refused too. A refusal is exit 2
  `ClaudeCode/skills/sandbox-run/scripts/sbx:socket_read_refusal`
  (`sbx_restrict:X4`, `sbx_restrict:X5`). The check is the same code on both
  OSes. On darwin, where `(deny network*)` already refuses a unix-socket
  connect, it refuses only what is spelled under `/run`, `/tmp`, `/var/snap` or
  `/var/run`, or is a socket: a darwin `--read /tmp/...` is now refused, while
  the same file spelled `/private/tmp/...` is not.
  `SOCKET_REFUSED_DIRS` is not mounted over again: the bwrap mount list stays the
  legacy argv. A round-2 residual: a read that is an ANCESTOR of a socket
  directory (`--read /`, `--read /var`) was ro-bound after the socket tmpfs and
  buried it, so the restricted bwrap argv now emits the socket tmpfs after
  every base and read bind (the legacy argv is unchanged)
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_bwrap_argv` (`sbx_restrict:X10`).
  Round 3, H1 (both modes, pre-existing): a write scope that IS or is an
  ANCESTOR of a socket directory (`--write /`, `--write /var`, or `--write .`
  from a `.git`-less cwd of `/`, which the gate auto-allowed as "inside the
  project") was bound after the tmpfs and buried it, so it is now refused,
  spelling and realpath against each `SOCKET_REFUSED_DIRS` entry and its
  realpath, exit 2 for `--write` and exit 3 for a scratch default that
  resolves to one; a scope UNDER a socket directory (`--write /tmp/x`) is not,
  and the gate prompts for the same case
  `ClaudeCode/skills/sandbox-run/scripts/sbx:socket_write_refusal`
  `ClaudeCode/hooks/sbx-gate.py:_buries_sockets` (`sbx_restrict:X11`, four
  `sbx_gate` group M rows). Round 3, H2 (restricted bwrap only): `--dev /dev`
  came before the reads, so `--read /` or `--read /dev` ro-bound the host
  `/dev` (`/dev/shm`, `/dev/mqueue`) over the fresh one; the restricted argv
  now emits it after the last read bind, next to the socket tmpfs, and the
  legacy argv is unchanged
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_bwrap_argv` (`sbx_restrict:X12`).
  Round 3, R1 (legacy bwrap, refused on both OSes and in both modes): a
  `~/.claude` carve-out (expanduser'd only, so bwrap follows its link) or a
  shadow re-bind target that is, is an ancestor of, or lies under a socket
  directory was ro-bound after the tmpfs and re-exposed the host sockets, so
  `main()` now refuses the whole run, exit 3 and `--dry-run` included, rather
  than drop the entry and lose its deny; "under" means under `/run`,
  `/var/run` or `/var/snap`, while under `/tmp` is accepted like a cwd there
  `ClaudeCode/skills/sandbox-run/scripts/sbx:socket_bind_refusal`
  (`sbx_restrict:X13`). Round 3, R2: a write scope UNDER `/run`, `/var/run` or
  `/var/snap` (`/run/user/<uid>`: D-Bus, ssh-agent, gpg-agent) was accepted
  while a cwd there was refused; it is now refused, exit 2 for `--write` and
  exit 3 for a scratch default there, and under `/tmp` stays accepted, with
  the gate prompting for the same case
  `ClaudeCode/skills/sandbox-run/scripts/sbx:socket_under_refusal`
  `ClaudeCode/hooks/sbx-gate.py:_under_sockets` (`sbx_restrict:X14`, three
  `sbx_gate` group M rows).
- **R1, a strict limit never raises the soft limit (CWE-269).**
  `_strict_rlimits` refuses a value above the current finite soft limit, exit 3
  (Decision 6) `ClaudeCode/skills/sandbox-run/scripts/sbx:_strict_rlimits`
  (`sbx_restrict:X6`). For `mcp-sandbox` this is a behaviour change: a
  configured `nofile` or `fsize`, or the `nproc_headroom` over the uid's
  current count, that lands above the runner's inherited finite soft limit
  used to be raised silently and is now refused. The plan's self-test runs the
  production argv, so such a config fails the plan and refuses the start; a
  run that meets it later is `sandbox_unavailable`.
- **P4, `--env` values are visible in argv (CWE-214).** The help used to say
  values are never printed. They are not printed by `sbx`, but they travel in
  the engine's argv (the darwin `/usr/bin/env -i K=V` trampoline, bwrap's
  `--setenv K V`) and so in the process list. The help now says so and tells
  the caller to pass no secret there
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_build_parser`
  (`sbx_restrict:X9`). Passing values through an inherited fd was the
  alternative; it was not taken.
- **M1, a status record is never byte-sliced.** A refused record's `reason` is
  halved until the ENCODED line fits 512 bytes, because one character can
  json-escape to six bytes; a record that does not fit even without its reason
  is not written at all. The old hard slice could drop the closing `}\n`, and
  the runner then lost the terminal record
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_write_status` (`sbx_restrict:X7`).

**W4, measured, no change.** The review asked whether a restricted run can
`link(2)` a file it does not own into its workdir, since the profile has no
file-link rule and a hard link would put an outside inode under the remover.
Measured on 2026-10-11 on macOS 14.2.1: Seatbelt refuses a hard link from a
user-owned file outside the workdir into it with `EPERM`, under the restricted
profile, the legacy profile and the default one. No rule was added and no row
pins it; it is a measurement of this host, not a gate.

## Alternatives rejected

1. **A second wrapper beside `sbx`.** *Rejected:* every primitive (filtered
   exec, clean env, wall clock, strict limits) is useful to any `sbx` caller
   and is covered by the one gate ADR 0005 audits. A second wrapper would need
   a second gate.
2. **`--read` realpath'd inside `sbx`, or `sbx` walking symlink chains.**
   *Rejected:* Seatbelt needs the symlink file itself readable as well as its
   target, and a realpath'd read loses the hop. Keeping the spelling keeps
   `sbx` dumb; the caller supplies each hop.
3. **Coarse Homebrew reads (`--read /usr/local`).** *Rejected:* `/usr/local`
   holds every keg, `etc/` and `var/`, databases and logs included. The
   measured precise set works.
4. **An sbx-side env key allow-list passed through the engine's env.**
   *Rejected:* it still hands caller-controlled values to the unsandboxed
   `sandbox-exec`. The trampoline gives the engine `{}`, and no blocker to it
   was found (3h).
5. **`--status-file PATH` instead of `--status-fd`.** *Rejected:* the
   unsandboxed parent would write a caller-chosen path, a write outside every
   scope. An inherited fd grants nothing.
6. **A `/bin/sh -c 'ulimit -v ...; exec "$@"'` trampoline for RLIMIT_AS on
   macOS.** *Rejected for now:* it puts a shell into the exec chain and needs
   `/bin/sh` in every filter. It was the fallback if the bash budget had come
   out above 8 GiB; it came out at ~1.27 GiB (item 2).
7. **Deny `file-write-flags` with `file-write-acl`.** *Rejected:* it breaks
   `shutil.copy2` and `shutil.copytree`, and the owner clears `uchg` anyway
   (3e, 3l), since security review round 1 fd-relative through
   `setattrlistat` and `fchflags` rather than `lchflags(0)` on a path. `file-write-mode` is never denied, since
   `chmod +x` must work.
8. **A `{100}` quantifier for the depth cap.** *Rejected:* measured to compile
   and silently never match. Even `(d/){2}` let `mkdir` reach 12 levels.
9. **Deny directory unlink in the write scope to close rename-chaining.**
   *Rejected:* `(deny file-write-unlink (require-all (vnode-type DIRECTORY)
   ...))` does stop the rename, but it also refuses every `rmdir` in the
   workdir, and SBPL cannot tell the two apart. The bypass is bounded at the
   remover instead.
10. **Narrow the exec subpaths to the self-test set.** *Rejected:* it would
    break ordinary scripts that call `/bin/sh`, `/usr/bin/env` and coreutils,
    for no containment gain (limit S-L1 below).

## The gates and their first runs

Two suites carry the gate, each typed in `tests/run.py`. `sbx_restrict` has 104
cases: A 15, B 8, C 4, D 4, E 10, F 11, U 9, V 8, T 7, L 12, S 16. The plan said
103; `F11` was added by IMPL:025. `mcp_sandbox` had 141, as planned; validation round 1 added `F9` (layer D
driven live through `McpServer.run()`), so it is 142. `sbx_gate`
stayed 105 and `sbx_seccomp` 33. Security review round 1 then added, each row
run red first: `sbx_restrict` group X, `X1`-`X9`, so 113; `mcp_sandbox` `A11`
(the worst-case reply serialized through the real tool-result path), `B21`-`B23`
(the closure's bounds and plan deadline) and `H20`-`H24` (the bounded listings,
the fd-relative darwin fallbacks, the survivor sweep, the `out/` path cap), so
151; and `sbx_gate` 23 whitebox rows for G1-G3, R1 and R2 with their controls,
so 128. `tests/run.py:SUITES` holds the numbers. Every live Seatbelt row first runs a control
invocation; under a nested Seatbelt every row of that group is INFO, counted
and never omitted.

Every group whose code did not exist yet was written and run red before its
implementation. A row cannot pass because argparse does not know a flag yet:
the red run fails on a control, never on an `ImportError`
`tests/test_sbx_restrict.py`. The one exception is `mcp_sandbox` group E, the
live escape suite, which was written after the code it probes and is not red
first; each of its rows pairs the attempt with a positive control instead. Which
rows were recorded red, and where each turned green, is in the ledger comments
of `tests/run.py`. Per-group red counts were not written down as numbers. What
the first runs measured was:

- **IMPL:025.** On Linux, `--clean-env --env K=V` without `--restrict-reads`
  exec'd bwrap with `{}` and no `--setenv`, so the pairs were dropped. `F11`
  was written red first; `_bwrap_argv` now emits `--clearenv` and the sorted
  `--setenv` pairs whenever the env is clean
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_bwrap_argv`.
- **IMPL:029.** The first live run of group L failed two rows, both on the
  plan's own assertions and not on `sbx`: `L5` and `L11`. Both are now declared
  limits (below), and no `sbx` code changed.
- **IMPL:068 / IMPL:069.** `E1`-`E18` passed at their first live run with no
  code change. The fork bomb stopped at `EAGAIN` after 70 forks, and the
  10 GiB flood took 2.8 s with a runner RSS delta of 0.1 MiB. `E19`-`E34`
  passed except `E28`, which stayed red until the decision recorded below made
  it INFO.

## Declared limits

Accepted and declared, not gated beyond what is named.

- **A bind source can be swapped between sbx's check and bwrap's mount (round
  3, R3).** `sbx` realpaths a write scope, a carve-out and a shadow target and
  checks the result against the socket directories, then bwrap resolves the
  same spelling again when it mounts it. In that window a local principal who
  controls a directory on the path (for an accepted scope, one under `/tmp`)
  can replace it with a symlink to a socket directory or an ancestor of one.
  This needs a hostile local user and a scope the caller placed under such a
  directory; it is not gated, and it is not closed (an fd-based bind was not
  tried).
- **A restricted Seatbelt run can read the argv AND environment of any
  same-uid process (E28; Zoltán's decision, 2026-10-11).** Inside the profile a
  script reads another process's arguments and environment through `sysctl`
  `KERN_PROCARGS2`; the probe read the runner's and saw `MCP_PROXY_TOKEN`. The
  cause is the unfiltered `(allow sysctl-read)` in
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_seatbelt_restricted_head`. It
  stays, because node aborts in V8 without it (M0:6) and a blanket
  `(deny sysctl-read)` also aborts python's `import ctypes` with SIGABRT.
  Name-filtered (`sysctl-name "kern.procargs"`, `"kern.procargs2"`) and
  prefix-filtered (`sysctl-name-prefix "kern.procargs"`) denies placed after
  the allow had no effect. A Chromium-style allowlist of sysctl names was NOT
  tried. Consequence: any secret in the environment of a same-uid process is
  readable from inside a run. The clean env of Decision 3 hides the caller's
  environment from the target, not the environment of every other process.
  This supersedes the plan's T35, which described the rule as a narrower
  hostname and hardware-model leak. By decision there is no further
  measurement, and `sbx` is unchanged. `mcp_sandbox:E28` is INFO: it still runs
  the probe against its positive control and still prints whether it saw the
  leak.
- **Seatbelt is an existence oracle (L5).** It checks the terminal vnode only.
  An existing path outside every grant answers `EPERM`, with no size and no
  times; a missing one answers `ENOENT`, because there is no vnode to deny. No
  path rule can close that. `sbx_restrict:L5` gates the half that holds (an
  existing path gives `EPERM`, no metadata) and records the missing-name answer
  as a detail.
- **`chmod +a 'everyone ...'` fails before the kernel (L11).** Under the
  restricted profile opendirectoryd is unreachable (there is no mach-lookup),
  so ACL names such as `everyone` do not resolve to a UUID and the command
  fails rc 1. The `file-write-acl` deny itself is proven by a ctypes
  `acl_set_file` answering `EPERM`. For `chmod +a`, `sbx_restrict:L11` gates
  the outcome: no extended ACL lands, checked by the owner against a positive
  control.
- **The depth cap does not bound rename-chaining (R-13).** Only a rename's
  destination entry is checked, so a 99-deep carrier renamed under a 99-deep
  parent, repeated, built 2079 levels in 237 ms sandboxed. It is mitigated, not
  closed: the flatten remover removes such a tree linearly (item 4b), and what
  it cannot remove inside the deadline is quarantined and finished on later
  retries. Building is far cheaper than removing, so a deliberately hostile run
  can outpace one cleanup; the bound for that case, and the decision not to add
  a background reaper, are ADR 0032's. Untested: a cut through a `uchg` or
  ACL'd directory, trees with non-default modes under flatten, and anything
  deeper than 4059 levels.
- **A setsid orphan survives the supervised kill (T11, `mcp_sandbox:E10`).** On
  macOS a process that `setsid()`s leaves the killed group and keeps running
  inside Seatbelt, irrevocably, until its RLIMIT_CPU or its own exit. It also
  keeps the inherited output pipes open, which is why the runner drains for at
  most 500 ms after `sbx` exits instead of reading to EOF. `E10` passes on
  "no hang" and records the survival; the test kills the orphan itself. On
  Linux the PID-namespace teardown kills it. Since security review round 1 the
  runner's cleanup sweeps such survivors before it removes the workdir (see
  the next limit); between the kill and the cleanup the orphan still runs.
- **The exec-to-`spawned` window (T11a).** If `sbx` is killed after the target
  exec'd but before the runner read the `spawned` line, the runner has no pgid
  to sweep. On Linux `--die-with-parent` still tears the namespace down; on
  macOS the target runs until its RLIMIT_CPU (timeout + 1 s of CPU) or its own
  exit. `mcp_sandbox:E33` covers only the post-`spawned` case.
- **A concurrent run is a separate sandbox (E22; measured, not a bug).** A
  sibling run's target pid answers `EPERM` under `(target same-sandbox)`, as the
  runner and `sbx`'s parent do. Two `sandbox-exec` invocations of the same
  profile text are separate sandboxes. This closes the plan's T10 open
  question; the script's own background job stays killable.
- **The survivor sweep finds a survivor by session and by cwd only (W6).** The
  remover no longer makes any spelled-path call (security review round 1), so
  the race this limit used to declare, an orphan redirecting one spelled-path
  `lchflags`, ACL strip or `chmod`, is gone. What the sweep before removal
  cannot do is declared instead
  `Scripts/mcp-sandbox.py:_sweep_survivors`: a survivor that left the run's
  session AND moved its cwd out of the tree, but still holds an fd into it, is
  not found, because there is no fd walk; and a pid recycled between the check
  and the kill is a race the kernel gives no handle to close. Such a survivor
  can keep writing into a tree the remover is working on; what then resists is
  quarantined.
- **An engine-stage failure after `spawned` is a normal result (KD-16).** Once
  the engine is exec'd, `sbx` runs no code before the target, so "sandbox-exec
  rejected the profile" cannot be told from a target that printed the same text
  and exited 1. Nothing ran unconfined, and a run uses the profile shape its
  startup self-test proved (`mcp_sandbox:I11`).
- **Every binary under `/bin` and `/usr/bin` is exec-able (S-L1).** It runs
  under the same profile: restricted reads, `(deny network*)`, no mach-lookup,
  one write scope. It gains nothing the script lacks.
- **Setuid binaries are refused, by Seatbelt (R2:S-L2).** Measured, not
  assumed (3k), and pinned live by `mcp_sandbox:E34`. On Linux bwrap's
  `NO_NEW_PRIVS` is relied on as bubblewrap's documented behaviour, not
  live-tested.
- **Linux has no exec filter (R2:L8).** bwrap has no `process-exec` rule, so a
  script can exec a binary it wrote into its workdir. That binary runs in the
  same namespaces, under the same seccomp filter and rlimits. `mcp_sandbox:E21`
  is a macOS proof, INFO on Linux.
- **The runner's `killpg` after `sbx` died can in principle hit a recycled pgid
  (R2:L4).** The orphaned leader is reaped by init or launchd, not by the
  runner, so a full pid wrap between that reap and the runner's `killpg` could
  hand the pgid to someone else. The runner signals right after `Popen.wait()`
  returns; it cannot close the window without being the leader's parent.
- **Quarantine surfaces only past 16 entries.** Up to 16 resisting workdirs
  are held silently, apart from the count `capabilities` reports.
- **Linux has no depth cap.** bwrap has no SBPL; the same flatten remover runs
  there, and Linux mutation cost by depth was not measured.
- **`/` is listable (T36, `sbx_restrict:L7`, INFO).** dyld needs the
  `file-read-data` literal on `/`.
- **The address-space limit is relative.** It is VSZ + budget + slack, so the
  effective budget is ~1.27 GiB for every interpreter (R-3, measured, not
  triggered).
- **A fork bomb can starve the user's other processes.** RLIMIT_NPROC is per
  real uid, so `+64` is headroom over every process the user runs. A bomb is
  bounded by the wall clock and the group kill, and a dedicated sandbox uid is a
  roadmap item.
- **The Linux branch is built and verified offline only.** Golden argv rows,
  Linux-only live rows INFO on macOS, `liveVerified: false` until it runs on the
  target.

## Implementation notes the code carries

- **The Homebrew python `lib-dynload` lives under the framework (IMPL:048).**
  On this host it is only at
  `<keg>/Frameworks/Python.framework/Versions/*/lib/python*/lib-dynload`; the
  keg has no `<keg>/lib/python*/lib-dynload`. The keg scan therefore visits a
  fifth targeted location, under the same per-directory cap
  `Scripts/mcp-sandbox.py:_keg_images`. Without it `_ssl` is never reached and
  `openssl@3` never enters the closure (`mcp_sandbox:B18`).
- **The spelled-path ACL strip and its inode re-check are gone (security
  review round 1).** A `readattr` deny ACE refuses even the owner's `fstatat`
  with `EACCES` (measured on the `H14` broad ACE), so the first implementation
  re-checked a `d_ino` taken through the parent fd before each spelled-path
  call. Both the spelled-path calls and that re-check were removed: the
  fd-relative `setattrlistat` on the parent fd needs neither an open nor a stat
  of the entry (see "Cleaning a hostile workdir").
- **`plan_interpreter` reads every non-system closure image by realpath
  (IMPL:062)** `Scripts/mcp-sandbox.py:plan_interpreter`. Inside a keg it is
  redundant; for an image reached through a symlink outside a keg it is the
  only read literal.

## Addendum (2026-10-11): the code landed in 7b603d2; the page is active and append-only

The Status paragraph above was written while the code sat uncommitted on top of `4d3640c` and says the frontmatter is `draft` with `targets:`. That is no longer true. The implementation, the four security-review rounds' fixes and their tests were committed as `7b603d2` (2026-10-11), and the page was promoted to `status: active` with `sources:` and `verified.commit: 7b603d2`. From that commit on this ADR is append-only: a later change to restricted mode lands as a dated addendum here, never as an edit of the body. The shipped plan is kept as a record in [[spec-sbx-restricted-runner]].

## Addendum (2026-10-12): two restricted-only egress endpoints, and --rlimit-nproc +N counts tasks on Linux

Decided by Zoltán with ADR 0033 ([[0033-egress-through-an-allowlisting-proxy]]), which carries the reasoning, the security review and the measurements.

### Egress endpoints

`sbx` gains two restricted-only flags; each requires `--restrict-reads`, they are mutually exclusive and neither combines with `--net` `ClaudeCode/skills/sandbox-run/scripts/sbx:validate_egress`. `--egress-port N` (macOS) keeps `(deny network*)` and adds after it `(allow network-outbound (remote tcp "localhost:N"))` `ClaudeCode/skills/sandbox-run/scripts/sbx:_seatbelt_egress_rule`; the caller must hold both 127.0.0.1:N and [::1]:N. `--egress-socket PATH` (Linux) keeps `--unshare-net`, read-only binds the caller's unix socket at `/.sbx-egress.sock` and runs the target behind an in-sandbox stdlib forwarder on 127.0.0.1:3128 `ClaudeCode/skills/sandbox-run/scripts/sbx:EGRESS_FORWARDER`, whose interpreter must already be visible in the restricted root `ClaudeCode/skills/sandbox-run/scripts/sbx:egress_python`. The socket must be its own realpath, under no socket directory, a socket owned by the euid in a 0700 euid-owned directory, and every ancestor up to `/` owned by root or the euid and writable by nobody else: world-writable only when root-owned and sticky, group-writable only when the group is the euid's user-private group (added after Ubuntu 22.04's umask 0002 refused every run) `ClaudeCode/skills/sandbox-run/scripts/sbx:egress_socket_refusal` `ClaudeCode/skills/sandbox-run/scripts/sbx:_private_group`. The gate always prompts for both flags `ClaudeCode/hooks/sbx-gate.py:_PROMPT_FLAGS`. Rows `sbx_restrict` group N (N1-N14) and S17, `sbx_gate` EG-ctl and EG1-EG5.

### The nproc base counts tasks on Linux

A defect in what `7b603d2` shipped: `--rlimit-nproc +N` resolved against the uid's process count, but Linux charges RLIMIT_NPROC per task (thread). On an Ubuntu host with 127 processes and 1054 threads the limit came out at 191, bwrap's clone failed EAGAIN, and the runner could not start on Linux at all. The base is now the sum of `Threads:` over `/proc/<pid>/status` for the real uid `ClaudeCode/skills/sandbox-run/scripts/sbx:uid_task_count`; macOS still counts processes. Row `sbx_restrict:X15`, red first. `sbx_restrict` went from 118 to 134 cases with this and the egress rows.
