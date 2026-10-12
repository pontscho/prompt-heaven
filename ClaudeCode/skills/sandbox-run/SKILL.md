---
name: sandbox-run
description: Run a shell command inside an OS-level sandbox via the bundled `sbx` wrapper -- macOS Seatbelt or Linux bwrap, fail-closed on any other platform or missing sandbox binary. PREFER THIS over a bare Bash invocation whenever the command's effects are not fully known in advance: a throwaway script just written to `.claude/tmp/`, a generated one-liner, anything third-party or unread, or a command that writes and you are not certain where. It costs nothing extra -- the paired `sbx-gate.py` PreToolUse(Bash) hook auto-allows a clean, in-project, network-free invocation of the ONE deployed wrapper WITHOUT a permission prompt, so a command that is not already allow-listed runs prompt-free under `sbx` and prompts without it. Defaults with no flags: writes default-deny except `<cwd>/.claude/tmp`; network off; `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude` (except its read-only `skills/` and `scripts/`) and every ancestor `.git/config` unreadable even inside a writable scope. `--write DIR` (repeatable; `--write .` opts the repo in) widens writes and still auto-allows inside the project; `--ro` denies every write except the `/dev/null` sink; `--seccomp` adds a syscall allowlist on Linux x86_64 (refused elsewhere) and still auto-allows; `--dry-run` prints the resolved plan and runs nothing. Restricted mode narrows further and still auto-allows when every value is provably narrowing: `--restrict-reads` (reads become an allowlist of `--read PATH` / `--exec PATH` grants plus the write scopes), `--clean-env` (empty environment plus `--env KEY=VALUE`), `--timeout S` (supervised; the process group is killed at the deadline, exit 124) and strict `--rlimit-{as,cpu,nproc,fsize,nofile,core} N`. Do NOT reach for it for build/test/clean (that is `forge_call`), read-only system inspection (`inspect_call`), or anything needing the network -- `--net` is never auto-allowed and always prompts, by design, and so do `--status-fd` and the restricted-only egress endpoints `--egress-port` / `--egress-socket`. The gate matches the deployed wrapper by canonical-path IDENTITY, never by filename; its registration is manual and the user's responsibility, and there is no installer.
model: sonnet
---

# Sandbox Run

Run a command inside an OS-enforced sandbox: writes are **default-deny** except a scratch scope, the network is **off**, and a fixed set of secrets is **unreadable** even when the project tree is writable. The command's exit status passes straight through as the wrapper's own (see **Exit codes** for the codes `sbx` itself adds).

This skill has two independent halves that must never be conflated (see **Trust boundary** below):

- **The helper `sbx`** -- the actual containment boundary. It resolves the request into an OS-sandbox invocation (macOS Seatbelt `sandbox-exec -p`, or Linux `bwrap`) and `os.execvp`s the target inside it. On any unsupported platform or missing sandbox binary it **fails closed** -- writes one line to stderr and exits non-zero, and NEVER runs the command unsandboxed.
- **The gate `sbx-gate.py`** -- a PreToolUse(Bash) hook that only decides *prompt vs. auto-allow*. It grants a no-prompt auto-run to a provably clean invocation of the one deployed wrapper and stays silent (normal prompt) for everything else. It does not itself contain anything.

## The helper script -- ~/.claude/skills/p/skills/sandbox-run/scripts/sbx

Stdlib-only Python (no `.py` extension -- the CLI name is intentional). Build order is pure-pieces-first / execvp-last: it resolves the writable scopes, the secret deny set, and the per-backend sandbox argv as pure data, then applies `resource.setrlimit` and `os.execvp` exactly once. Everything after the FIRST bare `--` is the target command, handed to `os.execvp` as argv LIST elements -- never through a shell -- so it can never inject into the sandbox profile.

```
~/.claude/skills/p/skills/sandbox-run/scripts/sbx [--write DIR]... [--net] [--ro] [--seccomp] [--dry-run]
    [--restrict-reads [--read PATH]... [--exec PATH]...] [--clean-env [--env KEY=VALUE]...]
    [--timeout S [--status-fd FD]] [--rlimit-{as,cpu,nproc,fsize,nofile,core} N]
    [--egress-port N | --egress-socket PATH] -- <cmd> [args...]
```

Flags may not be abbreviated: the parser runs with `allow_abbrev=False`, so `--restrict` or `--dry` is a usage error (exit 2), never a silent match to a flag the gate did not see.

Flags (parsed left of the first bare `--`):

- `--write DIR` (repeatable) -- add a writable scope; `--write .` opts the repo itself in. Each scope is `realpath`-canonicalized and REJECTED (fail-closed, exit 2) if the spelling or the resolved path contains a shell/SBPL metacharacter (`"` `'` `(` `)` `\`, or any control character: every C0 character and DEL), or a `~` other than a bare `~` or a leading `~/` (bash expands `~+`, `~-` and `~user`, Python's `expanduser` does not). A scope that IS, or is an ancestor of, a socket directory (`/`, `/var`, `/run`, `/tmp`, `/var/run`, `/var/snap`, compared as spelled and as realpath'd on both OSes) is refused too (exit 2), and so is a default `<cwd>/.claude/tmp` that resolves to one (exit 3). A scope that lies UNDER `/run`, `/var/run` or `/var/snap` (e.g. `/run/user/<uid>`: D-Bus, ssh-agent, gpg-agent) is refused the same way (exit 2; a default scratch there, exit 3); a scope under `/tmp` is still accepted.
- `--net` -- allow the network (denied by default). NOTE: a `--net` invocation is never auto-allowed by the gate; it always prompts.
- `--ro` -- deny ALL writes, including the default scratch scope. The one exception, on both platforms, is the `/dev/null` sink (so `stdin=DEVNULL` and `> /dev/null` keep working).
- `--seccomp` -- **Linux x86_64 only, off by default.** Adds a seccomp syscall **allowlist** that bwrap installs in the target (`bwrap --seccomp FD`): a call not on the list gets `EPERM`; a foreign architecture or an x32 call kills the process. On macOS, or on a Linux machine that is not x86_64, `sbx --seccomp` **refuses to run** (exit 3) rather than run without the filter. Auto-allowed by the gate like `--ro`, because it only narrows. See **Platforms** for what it blocks.
- `--dry-run` -- print the resolved plan and run NOTHING (no child is exec'd, no scratch dir is created; the helper exits 0). NOTE: unlike `--net`, a `--dry-run` invocation IS auto-allowed by the gate -- running nothing grants strictly less than the plain `sbx -- <cmd>` form the gate already allows, so it needs no extra trust. It is also the only auto-allowed form that completes inside Claude Code's own command sandbox (a real run needs a nested `sandbox-exec`, which that sandbox refuses -- see the nesting caveat under **Registration**), which makes it the live diagnostic for checking that the gate is registered and firing.

Restricted-mode flags. A run that carries none of them keeps the legacy behaviour unchanged; any one of them routes the request through the restricted path (`_restricted_scope`). Under `--dry-run` the extra plan fields are printed, with the environment as its KEYS only.

- `--restrict-reads` -- replaces read-everything with an allowlist. On Seatbelt: a readdir of `/` only, a LITERAL metadata allow on each ancestor of a granted path (never a global metadata rule), the `--read` grants, the write scopes, and a FILTERED `process-exec` (the absolute argv0, each `--exec` entry, and `/usr/bin/env` under `--clean-env`). On bwrap: an empty root with only the system base (`/usr`, `/lib*`, `/bin`, `/sbin` and a few `/etc` entries the loader, TLS and the clock need) plus the `--read` grants. The `~/.claude` carve-outs are NOT emitted (they are read grants). Refused with `--net` (exit 2). The `cwd` must lie inside a write scope or a `--read` grant (exit 3 otherwise). On Seatbelt a request with nothing to filter exec on (a bare command name, no `--exec`, an inherited environment) is refused (exit 3) rather than given an unfiltered exec allow.
- `--read PATH` (repeatable, at most 256) -- an absolute readable grant. Requires `--restrict-reads` (exit 2). The spelling is kept, never `realpath`'d: a directory is granted as a subtree, a file or a symlink as a literal, and a missing path is a usage error (exit 2). No symlink chain is walked into the rules; pass every hop.
- `--exec PATH` (repeatable, at most 32) -- an absolute exec-able file or directory. Requires `--restrict-reads` (exit 2), must exist (exit 2). Seatbelt matches the exec filter against the REALPATH, so pass each real path the target will exec; `sbx` never resolves one.
- A `--read` or `--exec` that is a socket, or that resolves -- at any hop of its symlink chain, spelled or realpath'd, or finally -- under `/run`, `/tmp`, `/var/snap` or `/var/run`, is refused (exit 2), on both platforms: a read-only bind of a socket still lets the target `connect()` to it (the host docker socket). On macOS the check matches the spelling, since system paths resolve to `/private/...`, so `--read /tmp/x` is refused there while `--read /private/tmp/x` is not unless it is a socket.
- `--clean-env` -- start the target with an EMPTY environment plus the `--env` pairs; the sandbox engine itself also gets an empty environment. The command after `--` must then be an absolute path (exit 2), and on macOS it may not contain `=` (it is run through an `/usr/bin/env -i K=V ... <cmd>` trampoline; Linux uses bwrap's `--clearenv --setenv`).
- `--env KEY=VALUE` (repeatable, at most 64) -- one variable for `--clean-env` (exit 2 without it). The key matches `[A-Za-z_][A-Za-z0-9_]{0,127}`; the `LD_` and `DYLD_` prefixes, a duplicate key, a NUL in the value and a value over 32768 characters are refused (exit 2). `sbx` never prints a value -- not in errors, not in `--dry-run` -- but the values travel in the sandbox engine's argv (the `/usr/bin/env -i K=V` trampoline on macOS, `--setenv K V` on Linux), so they are visible in the process list: pass no secret through `--env`.
- `--timeout S` -- supervised mode: `sbx` forks, the target runs as its own session and process-group leader, and at the wall-clock deadline the whole group is killed (exit 124). A `SIGTERM`/`SIGINT`/`SIGHUP` to `sbx` kills the group too (exit 128+N). `S` is decimal with at most three fraction digits, `0 < S <= 86400`. Without `--timeout`, `sbx` `execvp`s the engine as before.
- `--status-fd FD` -- requires `--timeout` (exit 2); `FD >= 3` and already open (exit 2). The supervisor writes one compact JSON line per event, at most 512 bytes each: `{"sbx":1,"event":"spawned","pid":...,"pgid":...}`, then a terminal `{"sbx":1,"event":"exit","outcome":"exited"|"signaled"|"timeout"|"refused","code":...,"signal":...,"durationMs":...}` (a `refused` record carries a `reason` and has no `spawned` before it). NOTE: never auto-allowed by the gate -- it writes to an inherited fd the gate cannot see.
- `--egress-port N` -- **macOS (Seatbelt) only.** One TCP endpoint through the restricted network deny: the profile keeps `(deny network*)` and adds, after it, `(allow network-outbound (remote tcp "localhost:N"))`. "localhost" reaches both `127.0.0.1:N` and `[::1]:N`, so the caller must hold BOTH. Port is plain decimal 1..65535 (exit 2). On Linux it is refused (exit 3): bwrap's private network namespace cannot reach a host port -- use `--egress-socket`. NOTE: never auto-allowed by the gate -- it opens a network path.
- `--egress-socket PATH` -- **Linux (bwrap) only.** `--unshare-net` stays; the caller's unix socket `PATH` is read-only bound at `/.sbx-egress.sock`, and the target runs behind an in-sandbox stdlib forwarder that listens on `127.0.0.1:3128` and relays bytes to that socket (at most 32 connections; the CONNECT is the caller's proxy's business). On macOS it is refused (exit 3; use `--egress-port`). `PATH` must be absolute and metacharacter-free (exit 2), and (exit 3 otherwise) its own realpath (no symlink hop), not under `/run`, `/var/run`, `/tmp` or `/var/snap` (spelled or realpath'd), a socket owned by the euid, in a 0700 directory owned by the euid; every ancestor above that directory owned by root or the euid and not writable by others -- world-writable only if root-owned and sticky, group-writable only if the group is the euid's user-private group. The forwarder runs under `sbx`'s own interpreter, which must already be visible inside the restricted root (exit 3, "grant it with `--read`"). NOTE: never auto-allowed by the gate -- it opens a network path.
- Both egress flags: mutually exclusive, require `--restrict-reads`, and refused with `--net` (each exit 2).
- `--rlimit-{as,cpu,nproc,fsize,nofile,core} N` -- a STRICT limit applied with soft == hard, on top of the default backstop (see **Resource limits**). `N` is plain decimal inside its range (AS >= 64 MiB, CPU 1..86400, NPROC 1..1000000, NOFILE 3..1048576, FSIZE and CORE 0..2^62); `--rlimit-nproc` also takes `+N` (1..4096), meaning N above the uid's CURRENT count in the kernel's own RLIMIT_NPROC unit: processes on macOS, TASKS (threads included) on Linux. A value above the finite hard limit, a value above the current finite SOFT limit (a strict limit only narrows; applying it would raise the soft limit -- a non-zero `--rlimit-core` over an inherited soft 0 would turn core dumps on), or a `setrlimit` the kernel rejects, is refused -- exit 3, nothing runs -- never clamped or skipped.

Defaults with no flags: the sole writable scope is `<cwd>/.claude/tmp` (scratch, created on demand); the network is denied; and the secret deny set -- `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude`, plus the `.git/config` of EVERY ancestor `.git` from `cwd` up to the filesystem root -- is denied both read and write, even inside a writable scope. The deny set is fixed: there is no flag to add a path to it, so a secret that must stay out of a legacy run's reach belongs under one of those directories (the sandboxed script runner keeps its token file under `~/.claude` for that reason).

**The working directory is checked first.** The `cwd` feeds the profile without passing any flag check -- its ancestors, the default scratch scope, every ancestor `.git/config` -- so a `cwd`, or its realpath, that contains `"` `'` `(` `)` `\` or any control character is refused before anything is resolved: one stderr line, exit 3, in every mode and under `--dry-run` too. Every path the Seatbelt builder writes into the profile passes the same refusal.

Without `--net`, pathname `AF_UNIX` sockets are unreachable too (docker, D-Bus, gpg-agent, ssh-agent, tmux, lxd): Seatbelt's network deny already refuses a unix-socket `connect()`, and on Linux `/run` (hence `/var/run`), `/tmp` and `/var/snap` are replaced by empty read-only tmpfs mounts, with a `--write` scope bound back on top. A `cwd` under `/tmp` is bound back read-only; a `cwd` under `/run` or `/var/snap` is refused (exit 3), because re-binding it would reopen those daemons. Declared limit: on Linux a socket anywhere else -- inside the project (including a project under `/tmp`) or under `$HOME` -- stays connectable.

**The one carve-out: `~/.claude/skills` and `~/.claude/scripts` are READABLE** (never writable), emitted AFTER the deny so it wins -- Seatbelt `(allow file-read* (subpath ...))` by last-match-wins, bwrap `--ro-bind` by later-mount-wins. Without it the project's own tooling is unrunnable under the sandbox, and the reason is not the obvious one. Seatbelt matches the **terminal node canonically** -- a script whose resolved path lands in the repo is already covered by the permissive read-allow -- but it also checks **each symlink at its own un-followed path**. `~/.claude/skills/p` is a symlink into the repo, so the readlink of THAT path falls under the `~/.claude` deny and resolution dies at the link, never reaching the target. Measured both ways: `Operation not permitted` on `~/.claude/skills/p/skills/**/scripts/*.py` before the carve-out, and the identical command runs after it. Plain ancestor traversal is NOT gated -- if it were, the carve-out could not work at all, since its own parent `~/.claude` stays denied. The carve-out is deliberately **fail-closed**: it names two subdirectories to open rather than listing secrets to hide, so everything else under `~/.claude` -- `projects/` (the full session-transcript history), `.credentials.json`, `settings*.json`, `hooks/` -- stays denied, including files that land there in the future. The write-deny on `~/.claude` is untouched and load-bearing: a sandboxed command must never be able to rewrite its own gate.

**The shadow write-denies.** A deny set spelled `~/.claude` protects that NAME, not the inodes behind it. On a plugin-developer host `~/.claude/hooks`, `~/.claude/scripts`, `~/.claude/skills/p` and `~/.claude/CLAUDE.md` are symlinks INTO the project, so `--write .` reached the gate's own source under its repo spelling: a sandboxed command could rewrite the very hook that auto-allowed it, and the next Bash call would run the rewritten gate. Measured live, then closed. `sbx` now walks the direct children of `~/.claude` and of each carve-out, and emits a **write-only** deny for every symlink target that leaves `~/.claude`. They are the LAST file rules in the profile, so no later allow can undo them. Intended consequence: under `--write .` the plugin's own executable configuration (`ClaudeCode/`, `Scripts/`) is **read-only**. Edit those with normal tools -- a sandboxed command has no business writing the code that contains it. A carve-out or shadow target that is, contains or lies under a socket directory (`/`, `/tmp`, `/run`, `/var/run`, `/var/snap`; under `/tmp` is accepted) refuses the whole run (exit 3, both OSes and modes, `--dry-run` too): on Linux it would be bound back over the socket tmpfs, and dropping it would lose its deny.

## Exit codes

| Code | Meaning |
|---|---|
| the target's own | the target exited (passed through unchanged) |
| 2 | usage error -- one `sbx: error:` stderr line plus the usage line; nothing ran |
| 3 | fail-closed refusal -- unsupported platform or missing engine, a working directory carrying a metacharacter, a builder refusal, `--seccomp` off Linux x86_64, a strict rlimit refused, the uid process count failed, the exec failed; nothing ran |
| 124 | `--timeout` deadline reached; the process group was killed |
| 128+N | `--timeout` mode: the target was killed by signal N, or `sbx` received signal N and killed the group |

## Usage

Invoke the bundled helper by its **absolute deployed path** (there is no `$ARGUMENTS` substitution in this repo -- a skill references its bundled script by that path):

```
~/.claude/skills/p/skills/sandbox-run/scripts/sbx -- ./run-tests.sh                 # scratch-only, no net
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --write . -- make build           # repo writable, no net
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --net -- curl https://example.com  # network allowed (will PROMPT)
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --ro -- python3 analyze.py         # no writes at all
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --seccomp -- ./tool               # + syscall allowlist (Linux x86_64)
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --dry-run --write . -- make        # preview the plan, run nothing
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --restrict-reads --write . --read /abs/input --exec /abs/realpath/of/tool --clean-env --env LANG=C --timeout 60 -- /abs/tool arg   # placeholders: allowlisted reads, clean env, 60 s
```

In restricted mode the caller supplies every grant the target needs: each path it reads as `--read`, each real path it execs as `--exec`, and a `cwd` inside a `--write` scope or a `--read` grant. Preview the resolved grants with `--dry-run` first.

**Keep the scaffolding INSIDE the target script, never around the call.** A timing harness like `start=$(date +%s); sbx -- python3 convert.py; echo $((end-start))` hard-prompts before the gate even looks at the wrapper: `;` is R1, `$(...)` is R3, and the `start=` prefix is R7. The sandbox still contains the run, but the auto-allow is gone -- you pay the wrapper's cost and get none of its benefit. Move the clock, the exit-code echo and any `&&` chaining into the target script, and the identical run costs zero prompts. The same applies to `cd X && sbx ...` (use the script's own `os.chdir`) and to output redirection (write the file from inside the script, into the scratch scope).

## Workflow

### Step 1 -- Parse arguments

- Collect every `--write DIR` (repeatable), and detect `--net`, `--ro`, `--seccomp`, `--dry-run` in the invocation, plus any restricted-mode flag (`--restrict-reads`, `--read`, `--exec`, `--clean-env`, `--env`, `--timeout`, `--status-fd`, `--rlimit-*`, `--egress-port`, `--egress-socket`). Everything after the FIRST bare `--` is the target command, taken verbatim.
- Do NOT expand, rewrite, or shell-interpret the target command -- it is passed through to `sbx` as argv elements, which hands them straight to `os.execvp`. Quoting/globbing/substitution is neither performed nor honored here.
- A missing `--`, or nothing after it, is a usage error: `sbx` reports it on stderr and exits non-zero. There is no default command.

### Step 2 -- Invoke the wrapper

Run the helper by its absolute deployed path with the parsed flags: `~/.claude/skills/p/skills/sandbox-run/scripts/sbx <flags> -- <cmd> [args...]`. Because `os.execvp` replaces the process image (same PID) and the sandboxed command's exit status is the wrapper's own, there is no fork/waitpid wrapper to interpret. The one exception is `--timeout`: `sbx` then stays as a supervisor, and 124 / 128+N are its own codes (see **Exit codes**).

## Invocation contract

The wrapper is invoked -- and auto-allowed by `sbx-gate.py` **without a permission prompt** -- ONLY by its absolute deployed path `~/.claude/skills/p/skills/sandbox-run/scripts/sbx`, and the gate matches it by canonical-path **IDENTITY** (KD-6), NEVER by filename. The gate canonicalizes the first token -- expand `~`, resolve a relative/`/`-bearing form against `cwd`, or `shutil.which` a bare name, then `os.path.realpath` -- and grants the auto-allow ONLY when that canonical form equals `WRAPPER_PATH` exactly. A program merely NAMED `sbx` elsewhere -- a planted `./sbx` in a writable dir, `/tmp/x/sbx`, or a PATH-shadowing binary -- is NOT the deployed wrapper and gets **no** auto-allow (it falls through to a normal prompt). A basename compare (`os.path.basename(...) == "sbx"`) is a forbidden false-allow.

Beyond identity, the auto-allow fires ONLY when the command is a single, clean, un-wrapped invocation: printable ASCII plus tab only (any other character -- a form feed, a carriage return, a non-breaking space, any non-ASCII character -- means prompt), metacharacter-free (any of `; & | $ \` ( ) < > { } [ ] newline ' " \ * ?` anywhere means prompt), split into words on space and tab only, exactly as bash splits them, every word -- flags, values and the target command alike -- made only of `A-Z a-z 0-9 . _ / = + : , @ % ~ -` (so `#`, `!` and `^` prompt too), every `~` either a bare `~` or a leading `~/` (bash expands `~+`, `~-` and `~user`; the gate does not, so it refuses them), every `--write` scope separator-safe-contained inside the project root, every other flag one that only narrows with a value the gate can prove narrowing, and no `--net`, `--status-fd`, `--egress-port` or `--egress-socket`. Under ANY doubt the gate emits nothing (empty stdout) and the normal permission prompt runs -- it never emits `deny`. The alphabet, word and tilde rules were added on 2026-10-11 (ADR 0031, security review round 1): a command that auto-allowed before can prompt now, never the other way round.

The gate's **recognized flag set** left of the bare `--` -- the accept-set that this section MUST stay in sync with -- is exactly:

| Token | Gate verdict |
|---|---|
| `--net` | **refused** -- always prompts (R11) |
| `--ro` | accepted (argument-less; imposes no scope) |
| `--seccomp` | accepted (argument-less; only NARROWS -- adds a syscall filter, and the helper refuses to run where it cannot apply one) |
| `--write DIR` / `--write=DIR` | accepted ONLY if `DIR` resolves separator-safe-contained inside the project root AND is neither a socket directory (`/run`, `/tmp`, `/var/run`, `/var/snap`, as spelled or realpath'd) nor an ancestor of one, nor under `/run`, `/var/run` or `/var/snap` -- from a `.git`-less cwd of `/`, `/var` or `/run/user/<uid>` the project root IS the cwd, and `sbx` refuses such a scope (exit 2) anyway; otherwise prompt |
| `--dry-run` | accepted (argument-less; runs NOTHING, so it grants strictly less than a plain `sbx -- <cmd>`) |
| `--restrict-reads` / `--clean-env` | accepted (argument-less; strictly fewer reads / an emptier environment) |
| `--status-fd FD` | **refused** -- always prompts: it writes to an inherited fd the gate cannot see |
| `--egress-port N` | **refused** -- always prompts: opens a network path |
| `--egress-socket PATH` | **refused** -- always prompts: opens a network path |
| `--read PATH` / `--exec PATH` | accepted ONLY if the value is absolute (starts with `/`; no `~`, no relative path) |
| `--env KEY=VALUE` | accepted ONLY if there is an `=` and `KEY` matches `[A-Za-z_][A-Za-z0-9_]*` without an `LD_` / `DYLD_` prefix |
| `--timeout S` | accepted ONLY if `S` is ASCII decimal (`[0-9]+(.[0-9]+)?`) -- a supervised kill only shortens a run |
| `--rlimit-as N` / `--rlimit-cpu N` / `--rlimit-nproc N` | accepted ONLY if `N` is ASCII decimal no looser than `sbx`'s own defaults: AS <= 8 GiB, CPU <= 900, NPROC an ABSOLUTE `N` <= 512 -- the `+N` headroom form always prompts, because it counts over the uid's current processes (tasks on Linux) and can land above 512 |
| `--rlimit-core N` | accepted ONLY if `N` is exactly `0` -- a non-zero core limit turns kernel core dumps on, written outside every write scope |
| `--rlimit-fsize N` / `--rlimit-nofile N` | accepted ONLY if `N` is ASCII decimal (no default exists, so any value adds a limit) |
| **anything else** | **hard prompt** -- never skipped |

Every value flag is accepted in both the space form (`--flag V`) and the equals form (`--flag=V`, split on the first `=`); a dangling value flag prompts. The digit grammars are literal ASCII ranges, never `str.isdigit()` (which accepts non-ASCII digits). The gate only proves the run is no wider than a bare `sbx -- <cmd>`; `sbx` re-validates every value itself (ranges, caps, existence). A NEW `sbx` flag prompts until it is placed in one of the gate's three tables (`_BOOL_FLAGS`, `_VALUE_FLAGS`, `_PROMPT_FLAGS`, which together must equal the parser's flag set).

The gate matches argparse's grammar and is never MORE permissive than it: the argument-less flags (`--ro`, `--seccomp`, `--dry-run`, `--restrict-reads`, `--clean-env`) are recognized ONLY as the exact bare token, because the helper's `store_true` rejects `--dry-run=1` / `--seccomp=1`, so those equals-forms are unrecognized tokens and hard-prompt. Likewise ANY other token before the `--` -- an unknown flag, an unhandled equals-form, a stray argument -- is a hard prompt, never silently skipped. This section and the gate's docstring / `is_clean_sbx` accept-set are the two ends that the H2 fix keeps in sync: the gate's accept-set and this documented invocation form MUST intersect, so edit them together or not at all.

## Trust boundary

**The gate and the helper are independent, and neither trusts the other (KD-5).** The gate's metacharacter rejection is convenience-side -- it decides *auto-allow vs. prompt* and nothing more. The helper's `realpath` + injection rejection + fail-closed backend dispatch is safety-side -- it is the actual containment boundary enforced by the OS kernel.

The gate is therefore **bypassable on purpose**: a user can always run `sbx` from a normal prompt, or run any other command, and the gate merely declines to auto-allow it. That is fine, because the *containment* comes from the helper's OS sandbox, not from the gate. The gate deciding "prompt" for a legitimate command costs an extra keypress; it never lets an uncontained command run as though it were contained. A future engineer MUST NOT "simplify" this by collapsing the two into one -- e.g. by having the gate itself sandbox, or by trusting the gate's classification to skip a helper-side check. Two ends, two responsibilities: keep them separate.

## Registration

There is **no installer and no self-check** (locked decision c). The plugin ships **no `hooks` block**; wiring the gate into `~/.claude/settings.json` is the **USER's responsibility**. Until it is registered, `sbx` still contains -- an unregistered gate just means a clean invocation prompts like any other Bash command (a safe degradation, never an escape).

Add the hook to `~/.claude/settings.json` using the **PascalCase** `PreToolUse` key and matcher `"Bash"`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "/abs/path/to/ClaudeCode/hooks/mcp-first-guard.py" },
          { "type": "command", "command": "/abs/path/to/ClaudeCode/hooks/sbx-gate.py" }
        ]
      }
    ]
  }
}
```

Replace `/abs/path/to/` with the real path to your `ClaudeCode/hooks/` directory. The nesting is three levels -- event (`PreToolUse`) -> matcher group (`{"matcher": "Bash", "hooks": [...]}`) -> one or more handlers (`{"type": "command", "command": ...}`); both hooks live in the SAME `"matcher": "Bash"` group's `hooks` array. The gate runs as a command, so make it executable once -- `chmod +x /abs/path/to/ClaudeCode/hooks/sbx-gate.py` (it carries a `#!/usr/bin/env python3` shebang) -- or set `"command": "python3"` with `"args": ["/abs/path/to/ClaudeCode/hooks/sbx-gate.py"]`.

**WARNING -- camelCase trap.** `ClaudeCode/README.md:356-379` shows an example using camelCase `"postToolUse"`. That is **WRONG** for real Claude Code, whose hook keys are **PascalCase** (`PreToolUse`, `PostToolUse`). Copy the shape from the snippet above, not from that README example.

**Two PreToolUse(Bash) hooks coexist -- CONFIRMED WORKING.** `mcp-first-guard.py` only ever emits `deny`|empty; `sbx-gate.py` only ever emits `allow`|empty. Their polarities are disjoint, and the coexistence was **verified empirically on a live host** (2026-08-08, both registered in that order in the same `"matcher": "Bash"` group): a clean deployed-path invocation (`sbx -- echo hi`) ran with **no permission prompt** -- the gate's `allow` was honored while `mcp-first-guard.py` stayed silent -- and `--net`, a chained `;` command, and a planted look-alike `./sbx` each fell through to a normal prompt. Registration order was guard-then-gate; the fallback remains safe in any case, since a lost `allow` degrades to a normal prompt, never to an unsandboxed auto-run.

**Claude Code's OWN command sandbox conflicts with `sbx` (nesting).** If the harness sandbox (`/sandbox`) is enabled, a `sbx` run fails with `sandbox-exec: sandbox_apply: Operation not permitted` (exit 71) -- two sandboxes cannot nest. This is not an `sbx` fault and not a containment failure; the command simply never starts. Since `sbx` IS the containment boundary (OS-kernel enforced, live-verified on macOS and Linux), the harness sandbox is redundant for wrapped commands: turn it off with `/sandbox` to let auto-allowed `sbx` invocations actually run. Anything still needing the harness sandbox can keep using it -- just not through `sbx`. The one exception is `--dry-run`: it exec's no child, so it completes normally even inside the harness sandbox -- which makes `sbx --dry-run -- <cmd>` the check for whether the gate is registered and auto-allowing, without needing `/sandbox` off.

## Platforms

`sbx` selects a backend from a registry keyed on a `sys.platform` prefix, with a **fail-closed default**. It never falls back to running the command unsandboxed.

| Platform | Backend | Engine binary | Status |
|---|---|---|---|
| macOS (`darwin`) | `_seatbelt_argv` -- inline SBPL profile | `sandbox-exec -p` | FULL |
| Linux (`linux`) | `_bwrap_argv` -- bubblewrap argv | `bwrap` | FULL |
| any other platform | none (registry `.get` -> `None`) | -- | **FAIL-CLOSED**: one-line stderr, exit non-zero, command never runs |
| supported platform, engine binary absent from PATH | -- | missing | **FAIL-CLOSED**: one-line stderr, exit non-zero, command never runs |

Adding a third OS later touches exactly one builder function plus one `BACKENDS` entry -- nothing else.

**seccomp (Linux x86_64) is OFF by default; `--seccomp` turns it on.** Without the flag, the Linux boundary is bwrap's read-only bind of `/`, a fresh read-only `/dev`, private PID and IPC namespaces, no network, and a new session (`--new-session`: no controlling terminal, so `/dev/tty` is `ENXIO` and `TIOCSTI` cannot reach the launching shell); the argv is unchanged by the flag. With it, `sbx` builds a classic-BPF program in pure Python (no libseccomp), writes it to an inheritable memfd, and `_bwrap_argv` -- still a pure function, inside its existing `if scope.seccomp:` branch (locked decision 8) -- adds `--seccomp <fd>`. The program is an **allowlist**:

- the architecture is checked first -- anything but x86_64 (e.g. an i386 `int 0x80` call) and any x32 call **kills** the process;
- the ordinary syscalls a CLI needs (file and directory I/O, memory, fork/exec/wait, threads and futexes, signals, timers, polling, sockets) are allowed;
- `socket()` is allowed for `AF_UNIX`, `AF_INET` and `AF_INET6` only; `ioctl()` is allowed except `TIOCSTI`/`TIOCLINUX` (terminal input injection); `clone()` is allowed except with any `CLONE_NEW*` namespace flag; `clone3()` answers `ENOSYS`, so libc falls back to the checked `clone()`;
- everything else -- `ptrace`, `mount`, `unshare`, `setns`, `bpf`, `keyctl`, `perf_event_open`, `io_uring_*`, module loading, `kexec`, `userfaultfd`, `personality`, credential changes, and any syscall added to the kernel later -- gets **`EPERM`**, a clean error rather than a kill.

x86_64 is the only syscall table: on any other machine, and on macOS, `sbx --seccomp` refuses to run (exit 3). The in-process filter is **live-verified** on an Ubuntu 24.04 x86_64 host (`seccomp_probe.py`, below), and the **bwrap end-to-end path** on Ubuntu 22.04 / bubblewrap 0.6.1 (2026-10-02, ADR 0005): under `--seccomp` the target shows `Seccomp: 2` and the denied calls already answer `EPERM` before the probe installs its own copy.

## Resource limits

Before `os.execvp`, `sbx` applies `resource.setrlimit` on `RLIMIT_AS`, `RLIMIT_CPU`, and `RLIMIT_NPROC` to this process (limits survive exec, so the target inherits them). These are a **robustness backstop** against a runaway (fork/mem bomb), NOT the containment boundary -- the OS sandbox is.

**RLIMIT_NPROC per-UID caveat.** `RLIMIT_NPROC` counts **all** of the invoking user's processes, not just this sandbox's descendants. A small absolute cap (say 64) would spuriously fail an innocent command the instant the user's existing process count already exceeds it, so `sbx` never imposes a small absolute number: it keeps the working per-UID soft cap and only imposes a generous **headroom** when there is no ceiling at all, leaving room for the engine to fork its target. This is a robustness caveat, not a containment hole.

**Strict limits (`--rlimit-*`).** The default backstop above is best-effort -- it clamps to the hard limit and skips a limit the platform rejects. An explicit `--rlimit-*` is the opposite: a contract. It is applied AFTER the backstop with soft == hard (the target can raise neither), in the supervised child under `--timeout` or right before the exec otherwise, and any value it cannot apply exactly is a refusal (exit 3, nothing runs). `--rlimit-nproc +N` counts the uid's current processes first (`/proc` on Linux, `/bin/ps` on macOS); a failed count is also exit 3.

## Self-test

The bundled fixture `selftest_probe.py` is a clearly-named probe (NOT `foo.py`) that exercises the live boundary. Run it under the sandbox with the project writable, giving the probe's path **inside the repo** (see the deployed-path trap below):

```
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --write . -- python3 ClaudeCode/skills/sandbox-run/scripts/selftest_probe.py
```

**Deployed-path trap -- do NOT invoke the probe via `~/.claude/...`.** `~/.claude` is itself in the secret deny set, so a target running *inside* the sandbox cannot read anything under it: `sbx --write . -- python3 ~/.claude/skills/p/skills/sandbox-run/scripts/selftest_probe.py` fails with `can't open file ...: [Errno 1] Operation not permitted` before the probe ever starts. This is the sandbox working as designed, not a bug. Note the asymmetry: the WRAPPER may live under `~/.claude` (it reads itself and builds the profile *before* applying the sandbox, then `execvp`s), but its TARGET may not -- the target is already contained. The same applies to any script you wrap: if it lives under a denied path (`~/.claude`, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`), invoke it from a copy outside those paths.

It attempts four actions and reports each outcome; a healthy sandbox yields **ok / blocked / blocked / blocked**:

| Probe | Action | Expected |
|---|---|---|
| a | write to `<cwd>/.claude/tmp/probe.out` (scratch) | **ok** (succeeds) |
| b | write to a path OUTSIDE the project (e.g. `~/sbx_probe_escape`) | **blocked** |
| c | open a network socket / `urllib` fetch | **blocked** |
| d | **READ a real secret path (e.g. a file under `~/.ssh`) while `--write .` makes the project writable** | **blocked** |

Probe **(d) is mandatory**. It is the ONLY proof that the secret-deny actually takes effect: on macOS the SBPL secret-deny relies on **last-match-wins ordering**, which is UNVERIFIED in this repo (there is no prior SBPL to measure it against). A probe that only checked out-of-scope *writes* could stay green while `~/.ssh` remained readable in-sandbox -- so probe (d) reads a secret under `--write .` and asserts the READ is BLOCKED. Consequently **the macOS secret boundary is probe-verified, NOT CI-verified.**

This self-test is **on-demand and platform-specific** (it needs a real `sandbox-exec` / `bwrap`) and is **explicitly NOT part of portable CI** (`forge test all`). Wiring a platform-specific live probe into portable CI would either break on the other OS or, worse, stay green while the secret boundary silently regressed. The offline pure-builder suite pins the deterministic argv/profile contract in CI; this probe is the live complement, run by hand on the host OS.

**Nested-sandbox caveat.** Running the probe requires an **un-nested** context: a nested `sandbox-exec` invocation is refused, so the self-test cannot be driven from inside an already-sandboxed shell. Run it from a normal (un-sandboxed) terminal.

### The seccomp probe -- seccomp_probe.py

`seccomp_probe.py` checks the `--seccomp` program **without bwrap** (Linux x86_64 only): it builds the program from the given `sbx`, cross-checks every embedded syscall number against the host's `asm/unistd_64.h` (a name an older header lacks is INFO, a different number is FAIL), installs the program into itself with `prctl(PR_SET_NO_NEW_PRIVS)` + `prctl(PR_SET_SECCOMP)`, then shows that denied calls get `EPERM` (each chosen to fail DIFFERENTLY without the filter, so the filter is proven to be the cause), that an x32 call is killed by `SIGSYS`, and that `python3`, `ls`, `git`, `sh`, `bash`, `cat`, threads and subprocesses still work. Run it as its own process -- the filter cannot be removed:

```
python3 -I ClaudeCode/skills/sandbox-run/scripts/seccomp_probe.py ClaudeCode/skills/sandbox-run/scripts/sbx
```

The offline suite (`forge test sbx_seccomp`) runs the same program through its own BPF interpreter on any OS, and runs this probe as a child when the host is Linux x86_64.
