---
name: mcp-sandbox
type: component
status: active
title: mcp-sandbox — the sandboxed script runner
description: The stdio MCP server that runs a caller's inline script under one configured interpreter through sbx restricted mode behind one sandbox_call tool -- one strict JSON config file and no policy flags, the run lifecycle from a non-blocking slot through a self-refreshing interpreter plan, an fd-relative workdir, the supervised sbx spawn and its status protocol, to the flatten remover and the quarantine; the optional network allowlist and the per-run CONNECT proxy that is a run's only way out; the four refusal codes, the KD-17 timeout chain and the startup bound, its launch behind mcp-proxy --http, how it is tested and what it declares rather than gates.
sources:
  - Scripts/mcp-sandbox.py
  - tests/test_mcp_sandbox.py
  - tests/files/mcp_sandbox/config.json.tmpl
verified:
  commit: 7b603d2
  date: 2026-10-11
links:
  - mcp-skill
  - mcp-proxy
  - generated-regions
  - scripts
  - skills
  - tests
  - 0005-approve-the-wrapper-not-the-command
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0015-ambiguity-is-the-defect
  - 0027-the-proxy-relays-it-never-composes
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0033-egress-through-an-allowlisting-proxy
  - spec-sbx-restricted-runner
---

# mcp-sandbox

`Scripts/mcp-sandbox.py` is a stdio MCP server that runs a short script a caller
passes inline: it materialises the files into a fresh work directory, runs one
configured interpreter — `python3`, `bash` or `node` — on the entry file through
`sbx --restrict-reads` (Seatbelt on macOS, bwrap on Linux), and answers the exit
status, the capped stdout and stderr and the files the script left under `out/`.
It has one tool, `sandbox_call`, whose functions are `run_script` and
`capabilities` `Scripts/mcp-sandbox.py:SANDBOX_CALL_TOOL`
`Scripts/mcp-sandbox.py:HANDLERS`. Its consumer is [[mcp-skill]], through a
dedicated [[mcp-proxy]] instance over HTTP, so it is not registered with Claude
Code — the smoke table carries it as an unregistered server whose config is
generated per launch `Scripts/_mcp_smoke_test.py:_sandbox_smoke_config_text`.

Everything below is the *shape*. The decision records are still being written:
what sbx restricted mode closes and why — the read set, the exec filter, the
clean environment, the supervised timeout, the strict rlimits, the cleanup
design and the measurements behind each — is
[ADR 0031](../adr/0031-sbx-restricted-mode.md), which amends
[[0005-approve-the-wrapper-not-the-command]]; why the runner sits behind its
own proxy, the timeout chain and the same-uid trust model are
[ADR 0032](../adr/0032-a-runner-behind-a-proxy.md). Why a run may reach the
network only through an allowlisting proxy the runner owns is
[[0033-egress-through-an-allowlisting-proxy]]. Where the server sits in the
fleet is [[scripts]]; the sbx flags it drives are the sandbox-run skill's
([[skills]]).

## The config file

**One JSON file, no policy flags.** The command line is `--config PATH`
(required) plus the fleet's `--debug` and `--log-file`; every setting lives in
the file, so no setting has two spellings ([[0015-ambiguity-is-the-defect]])
`Scripts/mcp-sandbox.py:main`. A config refusal is one stderr line
`mcp-sandbox: config refused: …` and exit 2, and its message names the **key**,
never the value `Scripts/mcp-sandbox.py:ConfigError`.

**The file itself is vetted on the descriptor it opened.** It is opened with
`O_NOFOLLOW` (a symlink is refused) and must be a regular file owned by the euid
and neither group- nor world-writable `Scripts/mcp-sandbox.py:_check_owned_file`;
it is read up to `Scripts/mcp-sandbox.py:_CONFIG_MAX_BYTES` and must be UTF-8
`Scripts/mcp-sandbox.py:_read_config_text`. It is parsed by the generated
`_strict_loads`, then once more by the one bare parse in the file, whose only
job is to refuse a repeated key `Scripts/mcp-sandbox.py:_refuse_duplicate_keys_parse`
(declared in the strict-JSON exception table on [[generated-regions]]).

**Every key is required but one, unknown keys are refused at every level.** The
top level is `version` (1), `sbx`, `work_root`, `seccomp`, `log_content`,
`interpreters` and `limits`, plus the one optional key `network`
`Scripts/mcp-sandbox.py:_CONFIG_OPTIONAL_KEYS` `Scripts/mcp-sandbox.py:load_config`
(the example below leaves it out, which is a run with no network):

```json
{
  "version": 1,
  "sbx": "/Users/<you>/.claude/skills/p/skills/sandbox-run/scripts/sbx",
  "work_root": null,
  "seccomp": "auto",
  "log_content": false,
  "interpreters": {
    "python3": {"path": "/usr/local/bin/python3"},
    "bash":    {"path": "/usr/local/bin/bash"},
    "node":    {"path": "/usr/local/bin/node", "extra_reads": ["/usr/local/etc/openssl@3/openssl.cnf"]}
  },
  "limits": {
    "timeout_ms": 30000, "max_timeout_ms": 120000,
    "as_budget_bytes": 1073741824, "as_mode": "baseline",
    "nproc_headroom": 64, "fsize_bytes": 16777216, "nofile": 256,
    "stdout_bytes": 65536, "stderr_bytes": 65536,
    "max_files": 64, "max_files_bytes": 4194304,
    "max_out_files": 16, "max_out_bytes": 1048576,
    "max_stdin_bytes": 1048576, "max_args": 64, "max_arg_chars": 4096,
    "max_concurrent": 2, "min_free_bytes": 1073741824
  }
}
```

- `sbx` is an absolute path to a file that passes the same ownership rule, root
  being an accepted owner for it too.
- `work_root: null` selects a per-user default (on macOS
  `<realpath of the temp dir>/mcp-sandbox-<euid>`, elsewhere
  `~/.cache/mcp-sandbox/work`) `Scripts/mcp-sandbox.py:_default_work_root`.
  Every test, smoke and footprint config names an explicit one under
  `.claude/tmp/`.
- `seccomp` is `auto`, `on` or `off`; `auto` means on only on Linux x86_64
  `Scripts/mcp-sandbox.py:_seccomp_on`. `log_content` is a real JSON bool.
- `interpreters` names at least one of `python3`, `bash` and `node`, each
  exactly `{path, extra_reads?, site?}` with absolute paths and at most
  `Scripts/mcp-sandbox.py:_EXTRA_READS_MAX` extra reads
  `Scripts/mcp-sandbox.py:_cfg_interpreters`. node's `openssl.cnf` read in the
  example was measured necessary (without it node exits 1 on an OpenSSL
  configuration error); widen it to that directory if a host needs more, never
  further.
- `site` is a real JSON bool, default false, accepted only on `python3`
  (`Scripts/mcp-sandbox.py:_SITE_INTERPRETERS`; on bash or node the key is
  refused whatever its value). python3 normally runs `-I -S -B`; `site: true`
  drops only `-S` (`Scripts/mcp-sandbox.py:_SITE_ARGV_FLAGS`), so a venv
  interpreter's `site-packages` reaches `sys.path` and its `.pth` files run;
  `-I` still ignores `PYTHON*` variables and the user site. The intended use is a
  venv python as `path` with the venv directory in `extra_reads`:
  `{"path": "<venv>/bin/python", "extra_reads": ["<venv>"], "site": true}`.
  Measured live by `mcp_sandbox:I12` (a venv behind a Homebrew framework
  symlink imports with `site: true`, gets `ImportError` without it).
- An `extra_reads` entry that equals or is an ancestor of `work_root`, spelled
  or realpath'd, is refused: it would show a run its siblings' workdirs and, on
  Linux, their egress sockets
  `Scripts/mcp-sandbox.py:_refuse_reads_over_work_root` (`mcp_sandbox:A19`).
- `network` is optional, and absent means a run has no network at all, exactly
  as before. Present, it is `{allow, ports?, nat64?}`
  `Scripts/mcp-sandbox.py:_cfg_network`: `allow` is a non-empty list of exact
  hosts or `*.` patterns that match strict subdomains only (never the apex),
  lower-case DNS labels with an IP literal in any `inet_aton` spelling and a
  trailing dot refused `Scripts/mcp-sandbox.py:_hostname_problem`, and a `*.`
  over a single label (`*.com`) refused; `ports` defaults to `[443]`; `nat64`
  lists the site's own NAT64 prefixes, which are refused as egress targets like
  the well-known ones `Scripts/mcp-sandbox.py:_cfg_nat64`. The bounds are the
  code's `Scripts/mcp-sandbox.py:_NETWORK_ALLOW_MAX`
  `Scripts/mcp-sandbox.py:_NETWORK_PORTS_MAX`
  `Scripts/mcp-sandbox.py:_NAT64_PREFIX_LENGTHS`; duplicates are refused, and a
  refusal names `network.allow[i]`, `network.ports[i]` or `network.nat64[i]`,
  never the value. The allowlist is the runner's, not a skill's. How a run uses
  it is "Network egress" below.
- Every integer limit is range-checked without coercion — a bool, a float or a
  string is refused — against `Scripts/mcp-sandbox.py:_LIMIT_RANGES`; the
  ranges are the code's, this page does not copy them. `timeout_ms` runs from
  100 to the config's own `max_timeout_ms`, which is capped at 120000;
  `max_concurrent` is capped at the fleet's `MAX_INFLIGHT_REQUESTS`
  `Scripts/mcp-sandbox.py:_cfg_limits`.
- `as_mode` is `baseline` or `absolute`, and `absolute` is refused on macOS,
  where a process's VSZ is dominated by the shared cache, so the address-space
  limit is always a measured baseline plus the budget there.

Two derived bounds are refused at load `Scripts/mcp-sandbox.py:_cfg_limits`.
The worst-case reply must fit in `Scripts/mcp-sandbox.py:_RESPONSE_BOUND`
(16 MiB). A capped byte travels twice, as at most 6 characters in
`structuredContent` (`\u0001`) and at most 7 in the text copy, which is that
JSON dumped again inside a JSON string, so it is priced at
`Scripts/mcp-sandbox.py:_REPLY_BYTE_WORST` (13) characters; an `out/` file's
path is priced at its cap `Scripts/mcp-sandbox.py:_OUT_PATH_MAX_BYTES` (512
bytes, and `collect_out` skips a longer one) at the same rate, plus its keys;
and the envelope is 8 KiB per copy:

```
13 x (stdout_bytes + stderr_bytes + max_out_bytes)
  + max_out_files x (13 x 512 + 256)
  + 2 x 8192          <=  16 MiB
```

Until security review round 1 the formula priced a byte at 2 x 6 and left the
path out; `mcp_sandbox:A11` now serializes the worst case through the real
tool-result path and checks it stays inside the bound. The second derived
bound: `max_timeout_ms` plus the fixed run terms
`Scripts/mcp-sandbox.py:_RUN_FIXED_TERMS_S` must fit under the run deadline
(unreachable with the 120000 cap; it pins the arithmetic in code).

A limit that passes its range can still be refused at run time. `sbx` applies
the address-space, CPU, process, file-size and open-file limits strictly and,
since security review round 1, refuses a value above the runner's own finite
SOFT limit rather than raise it
`ClaudeCode/skills/sandbox-run/scripts/sbx:_strict_rlimits`. A `nofile` or
`fsize_bytes` above the soft limit the runner inherited, or an
`nproc_headroom` that puts the uid's process count above it, therefore fails
the plan's self-test, which runs the production argv, and refuses the start;
met later, it refuses the run `sandbox_unavailable`. Before the change such a
value was silently raised.

## Startup

Startup fails closed before `initialize` is answered; each refusal is one stderr
line `mcp-sandbox: refusing to start: …` and exit 1
`Scripts/mcp-sandbox.py:_refuse_start`. In order `Scripts/mcp-sandbox.py:main`:

1. **The engine** — `/usr/bin/sandbox-exec`, or `bwrap` on Linux — must exist;
   the runner never runs a script unsandboxed `Scripts/mcp-sandbox.py:_engine_path`.
2. **The work root** is created 0700 if absent, must not be a symlink, must be
   its own realpath, owned by the euid with no group or other bits, and is
   opened `O_DIRECTORY|O_NOFOLLOW` and re-checked on the descriptor. Then
   **one runner per work root**: an exclusive non-blocking `flock` on
   `<work_root>/.lock`, held CLOEXEC for the process lifetime — a second runner
   refuses to start with "in use by another runner (lock held)". Then
   `quarantine/` and `egress/` (the parent of every Linux run's egress socket
   directory) are created and vetted the same way
   `Scripts/mcp-sandbox.py:prepare_work_root`
   `Scripts/mcp-sandbox.py:_open_egress_root`.
3. **The sweep.** Every name is validated before anything is removed: the work
   root may hold only 32-hex-digit workdirs, `.lock`, `quarantine` and
   `egress`, and `quarantine/` and `egress/` only 32-hex-digit entries —
   anything else, a planted file or symlink at `egress` included, refuses the
   start (`mcp_sandbox:H26`). Stale `egress/` entries, a SIGKILLed runner's
   socket directories, are removed first, and one that resists is logged, never
   quarantined (`mcp_sandbox:H25`). Leftovers are removed, and the quarantine retried, against **one** shared
   deadline `Scripts/mcp-sandbox.py:_STARTUP_SWEEP_DEADLINE_S`; whatever resists
   or is not reached stays quarantined and counts
   `Scripts/mcp-sandbox.py:sweep_work_root`. A leftover whose move into
   `quarantine/` failed is picked up by the runner's own quarantine so it still
   counts `Scripts/mcp-sandbox.py:RunnerState`. A count above the cap starts the
   runner degraded; it never exits.
4. **Every interpreter is planned** against one shared deadline
   `Scripts/mcp-sandbox.py:_STARTUP_PLANS_DEADLINE_S`, each plan additionally
   capped by the refresh deadline; any failure — a missing or non-regular
   interpreter, a Mach-O refusal, a failed self-test, an exhausted deadline —
   refuses the start `Scripts/mcp-sandbox.py:PlanCache`.

## Interpreter plans

**A plan is what one interpreter needs to run under restricted reads**
`Scripts/mcp-sandbox.py:InterpreterPlan`. The read set is every symlink hop of
the configured spelling — ancestors included — plus, on macOS, the dylib closure
walked by a stdlib Mach-O reader, the Homebrew kegs it lands in, the
`extra_reads`, and `/private/var/select/sh`; on Linux it is the `extra_reads`
plus the realpath's directory when that lies outside the system trees. The exec
set is the interpreter's **realpath**, `/bin` and `/usr/bin`, and for a
framework python the `Python.app` image it re-executes; a symlink hop is a read,
never an exec `Scripts/mcp-sandbox.py:plan_interpreter`. The closure walk
resolves `@rpath`, `@loader_path` and `@executable_path`, skips system images and
missing weak images, refuses a missing strong one, and scans each keg only in
targeted image directories under a file cap
`Scripts/mcp-sandbox.py:dylib_closure` `Scripts/mcp-sandbox.py:_keg_images`
`Scripts/mcp-sandbox.py:_KEG_SCAN_MAX_FILES`; every header bound is a
`MachOError` naming the rule, never the bytes `Scripts/mcp-sandbox.py:parse_macho`.
Every other term of the walk is bounded too, each a `MachOError` refusal,
never a truncation (security review round 1, C1): one image's deduped rpath
search stack holds at most `Scripts/mcp-sandbox.py:_RPATH_STACK_MAX` (256)
entries; the `node_modules` walk visits at most
`Scripts/mcp-sandbox.py:_KEG_SCAN_MAX_DIRS` (4096) directories and goes at most
`Scripts/mcp-sandbox.py:_KEG_SCAN_MAX_DEPTH` (32) levels down; and one keg
directory yields at most `Scripts/mcp-sandbox.py:_KEG_DIR_MAX_ENTRIES` (4096)
entries, counted while `scandir` yields them, so only a bounded list is ever
sorted. The walk checks the request's cancel and the plan deadline per image
and per directory `Scripts/mcp-sandbox.py:_plan_check`, so the plan term bounds
it like every other planning step. The constants' comment records the host's
measurement behind them: the largest Homebrew `lib/node_modules` (node@10) is
850 directories, 8 levels deep, at most 369 entries in one directory.

**A refresh proves the plan by running it.** In one planning workdir it measures
the VSZ of the interpreter and of the sbx launcher under the production argv
(the address-space limit is the larger plus the budget plus a fixed headroom)
`Scripts/mcp-sandbox.py:measure_vsz`, takes the interpreter's version from a
sandboxed `--version` run, and runs a self-test that must print `ok` under the
full production argv `Scripts/mcp-sandbox.py:self_test`. Every spawn of it goes
through the one adopting sbx spawn, and only the `/bin/ps` VSZ samples through
the second helper `Scripts/mcp-sandbox.py:_spawn_sbx`
`Scripts/mcp-sandbox.py:_run_child`.

**Plans refresh themselves.** The plan carries a signature — path, inode and
mtime of every hop, the realpath, every closure image, keg root and extra read
`Scripts/mcp-sandbox.py:_chain_signature` — and every run re-walks it. On a
change the run refreshes the plan under a per-interpreter lock, so a
`brew upgrade` needs no restart `Scripts/mcp-sandbox.py:PlanCache`. A failed
refresh makes the interpreter `refresh-failed` (runs refuse
`sandbox_unavailable`, `capabilities` shows the reason) and the next run retries,
except that the same failing signature is not retried within
`Scripts/mcp-sandbox.py:_REFRESH_SUPPRESS_S`. A refresh ended by a **cancel** is
not a failure: the prior state stays and no suppression starts. A run spends at
most **one** refresh term on its plan, the wait on another run's refresh and any
refresh of its own together; a term partly spent waiting refuses only that run,
with no state change.

## run_script: the run lifecycle

**Validation is pure and refuses before anything runs.** The members are exactly
`files`, `entry`, `interpreter`, `args`, `stdin` and `timeoutMs`
`Scripts/mcp-sandbox.py:_RUN_PARAMS`; each file is `{path, content}` with an
optional `encoding` (`utf8` or strict `base64`) and `executable`, the decoded
total is checked file by file before the next is decoded, and `timeoutMs` is an
integer (never a bool) clamped to `max_timeout_ms`
`Scripts/mcp-sandbox.py:validate_run_params`. A path is relative, without `..`,
`.`, empty components, NUL, backslash or control characters, no component
starting with `-`, bounded in length and depth, and its first component is not
`out`, `tmp` or `.sbx*`; two paths colliding under NFD plus casefold, or one
being the other's directory prefix, are refused
`Scripts/mcp-sandbox.py:_valid_relpath`. The interpreter must be configured and
admitted by the plan cache. Every refusal is `invalid_params` naming the member.

**Then, in this order** `Scripts/mcp-sandbox.py:run_one`
`Scripts/mcp-sandbox.py:_run_in_slot`:

1. **A slot first**, non-blocking: with `max_concurrent` runs in flight the
   answer is `busy` with `retryAfterMs` immediately, before any plan work — so a
   refresh's spawns count against the cap `Scripts/mcp-sandbox.py:RunSlots`.
2. **The run deadline** `Scripts/mcp-sandbox.py:_RUN_DEADLINE_S` is armed.
3. **Degraded and disk prechecks.** A degraded runner first retries its
   quarantine for `Scripts/mcp-sandbox.py:_DEGRADED_RETRY_S` and refuses
   `sandbox_unavailable` if it is still over the cap; free space below
   `min_free_bytes` refuses too `Scripts/mcp-sandbox.py:_check_free_space`.
4. **The plan**, under the run's one plan deadline.
5. **The workdir**: a fresh 0700 32-hex-digit directory with `tmp/` and `out/`
   `Scripts/mcp-sandbox.py:create_workdir`, the files created fd-relative with
   `O_EXCL|O_NOFOLLOW` `Scripts/mcp-sandbox.py:materialize`.
6. **The egress proxy**, only when `network` is configured: the run's own
   proxy is started before sbx, and if it cannot start the run is refused
   `sandbox_unavailable` ("the egress proxy could not be started", or the
   too-long socket path message) `Scripts/mcp-sandbox.py:_spawn_and_read`.
   See "Network egress" below.
7. **The spawn.** sbx is started with a fresh status pipe, `close_fds`,
   `start_new_session` and an environment of exactly `PATH` and `LANG`; the
   script's own environment is exactly `PATH`, `HOME`, `TMPDIR`, `LANG` and `OUT`
   through `--clean-env`; the workdir is the one write scope, the plan's reads and
   execs follow, then the timeout, the status fd and the rlimits (address space,
   CPU, a process headroom over the uid's count, file size, open files, no core),
   with egress the endpoint flag and the four proxy variables, and after `--`
   the interpreter and the absolute entry
   `Scripts/mcp-sandbox.py:build_sbx_argv`.
8. **The read.** stdout and stderr are kept up to their caps and **read and
   discarded** after, so the script's exit status stays its own and the server's
   memory stays flat `Scripts/mcp-sandbox.py:_read_capped`. Every status line is
   parsed strictly; the first `spawned` event's pgid is adopted, the first
   terminal `exit` event is kept, anything else is ignored, and an unterminated
   line over `Scripts/mcp-sandbox.py:_STATUS_BUFFER_MAX` is answered `internal`
   `Scripts/mcp-sandbox.py:_StatusCapture`. If sbx overruns the caller's timeout
   by `Scripts/mcp-sandbox.py:_HARD_DEADLINE_SLACK_S` the runner sends it SIGTERM,
   then SIGKILL after `Scripts/mcp-sandbox.py:_KILL_GRACE_S`
   `Scripts/mcp-sandbox.py:_wait_sbx`. After sbx is reaped the pipes get one
   `Scripts/mcp-sandbox.py:_DRAIN_S` and are closed, never waited on for an EOF a
   surviving orphan could withhold; if sbx died without a terminal event, the
   runner kills the adopted target group itself
   `Scripts/mcp-sandbox.py:_kill_target_group`.
9. **The result.** No `spawned` event means the script never ran: the answer is
   `sandbox_unavailable` with the fixed message `sbx refused the run (exit N)`.
   sbx's own stderr line can name host paths (it quotes a refused `--read`), so
   since security review round 1 (O2) that line goes to the runner's own log
   only, never to the caller. After `spawned`
   every outcome is a normal result — `exitCode`, `signal` (a name such as
   `SIGKILL`), `timedOut`, `durationMs`, `stdout`, `stderr`, their truncation
   flags, `outFiles` and `outFilesTruncated`, and, only when `network` is
   configured, `egress: {tunnels, refused}` `Scripts/mcp-sandbox.py:_spawn_and_read`.
   `out/` is collected fd-relative: symlinks, hard links and FIFOs are skipped,
   a replaced `out/` yields nothing, the walk is bounded in depth and entries,
   a path longer than `Scripts/mcp-sandbox.py:_OUT_PATH_MAX_BYTES` is skipped,
   each directory is listed only up to the entry bound while `scandir` yields
   (only that bounded list is sorted, Q1), every skip sets
   `outFilesTruncated`, and a file is sent as `utf8` when it is strict UTF-8,
   else `base64` `Scripts/mcp-sandbox.py:collect_out`.
10. **Cleanup** in a `finally`: the egress proxy, if any, is closed once sbx is
   reaped, bounded by the earlier of one second and the run deadline minus the
   cleanup term, so no tunnel outlives the run; the run's survivors are swept, then the workdir
   is removed within `Scripts/mcp-sandbox.py:_CLEANUP_DEADLINE_S`, or
   quarantined; the result is returned either way, and the opportunistic
   quarantine retry shares the same deadline
   `Scripts/mcp-sandbox.py:_cleanup_workdir`. Then the slot is released.

Logging is one line per run with metadata only — interpreter, exit, signal,
duration, sizes — and content only when `log_content` is true; the wire log is
structure-only ([[0011-a-truncated-payload-carries-the-first-cookie]]).

## Network egress

**The sandbox never gets the network; the runner owns the one way out.**
Neither Seatbelt nor bwrap can filter by domain, so with `network` configured
each run gets its own host-side, CONNECT-only proxy that enforces the allowlist,
and the sandbox may reach only that proxy
([[0033-egress-through-an-allowlisting-proxy]]).

**One proxy per run** `Scripts/mcp-sandbox.py:_RunEgress`. On macOS it listens
on `127.0.0.1:N` **and** `[::1]:N` (`IPV6_V6ONLY`), because the Seatbelt rule
sbx emits for `--egress-port N` reaches both loopbacks and no other uid may
squat the v6 side; it retries up to
`Scripts/mcp-sandbox.py:_EGRESS_BIND_ATTEMPTS` ports and fails closed, and binds
v4 only on a host with no `::1`. On Linux it listens on a unix socket
`<work_root>/egress/<32-hex>/s` in a 0700 per-run directory, passed as
`--egress-socket`, and the script reaches it through sbx's in-sandbox forwarder
on `127.0.0.1:3128` `Scripts/mcp-sandbox.py:_EGRESS_LINUX_PORT`. When `<dir>/s`
is over the `AF_UNIX` path limit, Linux binds through `/proc/self/fd/<dirfd>/s`
after a device-and-inode match; elsewhere the run is refused "the egress socket
path exceeds the AF_UNIX limit; shorten work_root"
`Scripts/mcp-sandbox.py:EgressProxy`. The script's environment gains
`HTTPS_PROXY`, `HTTP_PROXY`, `https_proxy` and `http_proxy` pointing at the
proxy, with `NO_PROXY` and `no_proxy` empty `Scripts/mcp-sandbox.py:build_sbx_argv`.

**What the proxy admits.** CONNECT only, anything else 405; a head bounded in
bytes and header lines (431) under one total deadline; a strict request line,
host and port grammar (400) `Scripts/mcp-sandbox.py:parse_connect_head`. The host must pass the config's name rules, the allowlist and the port list, refused 403
before any lookup `Scripts/mcp-sandbox.py:egress_host_refusal`
`Scripts/mcp-sandbox.py:egress_host_allowed`. Then **one** lookup of the
absolute name (`host.`, so no resolver search list applies)
`Scripts/mcp-sandbox.py:_default_resolver`; **every** answer must be public or
the request is 403 `Scripts/mcp-sandbox.py:egress_address_public` — an explicit
IPv4 special-purpose denylist checked before `is_global`
`Scripts/mcp-sandbox.py:_EGRESS_V4_DENY`, the v4-mapped, 6to4, Teredo and
site-local IPv6 forms, the well-known NAT64 prefixes
`Scripts/mcp-sandbox.py:_NAT64_NETS` and the configured `network.nat64`; and
the connect goes to the vetted address itself, never a second resolution
`Scripts/mcp-sandbox.py:_default_connector`.

**Bounds** are the code's: tunnels per run (503 beyond)
`Scripts/mcp-sandbox.py:_EGRESS_MAX_TUNNELS`, connections per run
`Scripts/mcp-sandbox.py:_EGRESS_MAX_CONNECTIONS`, connections across every run's
proxy in the process `Scripts/mcp-sandbox.py:_EGRESS_GLOBAL_CONNECTIONS`, and the
head, connect, idle and send timeouts beside them. A half-close is relayed: EOF
on one side shuts down the other side's write half, and the tunnel ends when
both directions are done, on idle, or on stop. `close()` interrupts a connect
in progress; a connect that completes after it began is closed and not counted.
The log carries host, port, status, the vetted address and byte counts, never a
payload byte.

**The caller sees it.** `capabilities` carries `network: {enabled, allow,
ports}`, and a run result carries `egress: {tunnels, refused}` when `network` is
configured `Scripts/mcp-sandbox.py:capabilities`.

## Cleanup: the flatten remover and the quarantine

**`shutil.rmtree` is never used.** The remover is iterative and fd-relative,
with an explicit stack and no recursion `Scripts/mcp-sandbox.py:remove_tree`.
Restricted Seatbelt refuses a create more than 100 levels below a write scope
`ClaudeCode/skills/sandbox-run/scripts/sbx:_DEPTH_CAP_LEVELS`, but a script can
still build a deeper tree by renaming one deep chain under another, and on macOS
every mutating operation costs more the deeper it is. So the remover
**flattens**: it walks a segment at most `Scripts/mcp-sandbox.py:_FLATTEN_DEPTH`
levels deep, lifts every subdirectory it finds at that depth to a fresh
top-level `.sbx-flat-…` name with one fd-relative rename, repeats until every
segment is that shallow, then removes each segment bottom-up. The open frames of
a walk are its descriptor window, so it stays bounded under a low
`RLIMIT_NOFILE`. Lifted segments persist, so a retry resumes where the last one
stopped.

Per directory it opens `O_NOFOLLOW` (a symlink is unlinked, never followed),
`chmod`s 0700 through the parent fd on an open refusal, `fchmod`s an opened
directory before listing it only when its owner bits are short, and on macOS
clears an immutable flag and removes an extended ACL before giving up on an
entry. **Every macOS fallback is fd-relative**: the entry is one name under its
verified parent fd, reached with `setattrlistat(2)` and `FSOPT_NOFOLLOW`
`Scripts/mcp-sandbox.py:_clear_flags_at` `Scripts/mcp-sandbox.py:_remove_acl_at`,
and the parent through its own fd with `fchflags` and an empty `acl_set_fd_np`
`Scripts/mcp-sandbox.py:_strip_acl_fd`. Security review round 1 removed the
spelled-path fallbacks (`lchflags`, `chmodx_np`, `acl_set_file` under an inode
re-check): a broad deny ACE refuses every `open(2)` of the entry, so only a
call that needs no open of it works, and a spelled path is what a live
survivor could redirect. What these calls cannot clear stays, and the workdir
is quarantined; there is no path-based last resort. The deadline is checked
before **every** mutating operation, the total is bounded by
`Scripts/mcp-sandbox.py:_RM_MAX_OPS`, and a directory is listed in batches of
`Scripts/mcp-sandbox.py:_RM_LIST_BATCH` entries with a deadline check before
each, holding at most `Scripts/mcp-sandbox.py:_RM_PENDING_MAX` of them at once
(a directory holding more is listed again once those are gone).

**Survivors are swept before every removal**
`Scripts/mcp-sandbox.py:_sweep_survivors`. A process that left the target's
group survives the group kill on macOS and could still be writing into the
workdir. So before a run's workdir or a refresh's planning workdir is removed,
and before every quarantine retry (over `quarantine/` and every name stuck in
place), the run's session is killed unless a live leader now holds
that pid, and on macOS every process of the euid still in that session, or
whose cwd lies inside the tree about to be removed, gets `SIGKILL`, in at most
`Scripts/mcp-sandbox.py:_SWEEP_ROUNDS` rounds inside the same cleanup deadline.
On Linux the pid namespace dies with sbx, so only the session kill runs.

**What still resists is quarantined, and the runner keeps serving.** The workdir
is renamed into `quarantine/`, or, if that rename fails, remembered in place
`Scripts/mcp-sandbox.py:Quarantine`. Only a count above
`Scripts/mcp-sandbox.py:_QUARANTINE_MAX` makes the runner degraded
(`"quarantine full"`: runs refuse `sandbox_unavailable`, `capabilities` still
answers). The quarantine is retried at startup, before each run while degraded,
and after a run's own cleanup at most every
`Scripts/mcp-sandbox.py:_QUARANTINE_RETRY_EVERY_S`, each retry under one shared
deadline.

## The dispatcher and the error contract

A refusal is a tool result with `isError` and `{"error": {"code", "message"}}`,
never a JSON-RPC error `Scripts/mcp-sandbox.py:_tool_refusal`
([[0010-a-handler-failure-must-reach-iserror]]). The codes:

```json
{"error": {"code": "invalid_params", "message": "Unknown function 'run_scrip'. Did you mean 'run_script'? Available: capabilities, run_script."}}
{"error": {"code": "busy", "message": "2 runs in flight (max_concurrent=2)", "retryAfterMs": 1000}}
{"error": {"code": "sandbox_unavailable", "message": "interpreter node is not ready (...)"}}
{"error": {"code": "internal", "message": "<ExceptionType>"}}
```

- `invalid_params` — a malformed call; an unknown function or parameter, with a
  did-you-mean pointer that never resolves the call
  ([[0030-point-at-the-near-miss-never-resolve-it]]).
- `busy` — every run slot is taken; retry after `retryAfterMs`.
- `sandbox_unavailable` — the runner cannot run this script now: not started,
  degraded, low disk, an interpreter not ready or a refresh out of time, the
  run's egress proxy unable to start, sbx refusing before `spawned` (always the fixed `sbx refused the run (exit N)`,
  never sbx's own line).
- `internal` — a handler bug or a status-protocol overflow; the message is the
  exception **type** only, because a message could carry the caller's payload,
  and the exception is logged `Scripts/mcp-sandbox.py:handle_sandbox_call`.

**A failing script is not an error**: a non-zero exit, a signal or a timeout is
a normal result with `isError` false. A call with no `function` (absent, empty
or blank) gets an unflagged status reply — the function list, each interpreter's
plan state, whether the runner is degraded and how many runs are in flight,
never a path or the read set `Scripts/mcp-sandbox.py:_status_text`; a `function`
that is not a string is `invalid_params`. The tool's `params` may arrive as a
JSON-encoded object string, held to the same member rules, as everywhere in the
fleet; a non-object JSON-RPC `params` on `tools/call` stays a protocol-level
`-32602`.

**`capabilities`** needs no slot, so it answers while every slot is taken
`Scripts/mcp-sandbox.py:capabilities`: per interpreter its spelling, `site`,
realpath, version, `state` and `reason`, `asLimitBytes`, `effectiveBudgetBytes` and the
read and exec sets; the limits; the backend (platform, engine, seccomp,
`liveVerified` — true on macOS, false on Linux
`Scripts/mcp-sandbox.py:LIVE_VERIFIED` — and the sbx path); `concurrency`
(`max`, `inFlight`); `quarantine` (`count`, `max`); `network` (`enabled`,
`allow`, `ports`; disabled and empty without a `network` key); and `degraded`.

## Concurrency and cancel

Handlers are synchronous and run in a worker pool of `max_concurrent + 2` beside
a dedicated stdin reader, so with every run slot taken two workers stay free for
`capabilities`, the status reply and `busy` `Scripts/mcp-sandbox.py:McpServer`
([[0008-a-serialized-read-loop-looks-like-a-dead-server]]; its row is
`tests/test_read_loop.py:FLEET`). A cancel **kills**: each request gets its own
`_Reclaim`, every sbx spawn and `/bin/ps` sample is adopted into it, and a
cancel sends each adopted sbx group SIGTERM, then SIGKILL after the same one
grace `Scripts/mcp-sandbox.py:_Reclaim`. sbx traps the signal and kills the
target group; the run thread sweeps the adopted target pgid only if no terminal
event arrived, removes the workdir in its `finally`, and its result is
discarded. The cancel class is pinned by `tests/test_cancel.py:FLEET`.

## The timeout chain

Every term of a run is enforced, and the sum stays strictly inside the next
hop's timeout (the arithmetic is written once, above the constants it adds up)
`Scripts/mcp-sandbox.py:_RUN_DEADLINE_S`:

| Term | Constant | Seconds |
|---|---|---|
| degraded pre-run quarantine retry | `_DEGRADED_RETRY_S` | 2 |
| one plan term (wait + own refresh) | `_REFRESH_DEADLINE_S` | 30 |
| the caller's timeout, at most | `max_timeout_ms` | 120 |
| sbx overrun before the runner kills | `_HARD_DEADLINE_SLACK_S` | 10 |
| SIGTERM to SIGKILL | `_KILL_GRACE_S` | 2 |
| pipe drain | `_DRAIN_S` | 0.5 |
| cleanup and opportunistic retry | `_CLEANUP_DEADLINE_S` | 10 |
| **run bound** | | **174.5 ≤ 175** |

175 < the runner proxy's `call_timeout` 190 < mcp-skill's `--runner-timeout-s`
200 < the skill proxy's `call_timeout` 220. Startup is bounded the same way:
sweep 20 s + plans 75 s = 95 s, under the runner proxy's `startup_timeout` 120 —
a runner slower than that would be restarted until the proxy's restart budget
disables it for good. The plan records a measured startup of about 0.9 s for the
one-interpreter smoke config and about 2.7 s for all three interpreters on the
development host.

## Launch behind mcp-proxy

The runner speaks stdio only; HTTP is the proxy's job
([[0027-the-proxy-relays-it-never-composes]]). The planned deployment — example
only, the paths are illustrative — is one dedicated proxy instance with the
runner as its single child:

```json
{
  "root": "/Users/<you>/mcp/sandbox-root",
  "scripts": "/Users/<you>/mcp/Scripts",
  "children": [
    {"name": "sandbox", "command": "python3",
     "args": ["-I", "-B", "{scripts}/mcp-sandbox.py", "--config", "/Users/<you>/mcp/sandbox.json"],
     "call_timeout": 190, "startup_timeout": 120}
  ]
}
```

launched as `python3 mcp-proxy.py --config sandbox-proxy.json --http --port 3010
--token-file ~/.claude/mcp-sandbox/runner.token --require-token-file
--max-body-bytes 8388608 --session-idle 900`. `--require-token-file` makes the
proxy refuse to start while `MCP_PROXY_TOKEN` is set, so the runner token can
only come from the file: the environment is readable from inside a run (E28,
below) ([[mcp-proxy]]). **Keep the token file under `~/.claude`**, owner-only
(0600): `sbx`'s deny set is fixed and covers `~/.claude` outside its two
read-only carve-outs, while a token file anywhere else is readable by any
legacy `sbx` run the gate auto-allows, `sbx -- cat <file>` included (ADR 0032,
T3). The path above is an example; nothing checks the location. The
shortened `--session-idle` bounds the sessions of skill servers that die
without their shutdown `DELETE`; `--max-body-bytes` is what mcp-skill's
`--runner-max-body` must match. Lowering `startup_timeout` much below 100 s
reopens the crash loop the startup bound exists to prevent. The skill side of
the deployment is on [[mcp-skill]].

**The deployed shape is a sibling child** (2026-10-12, ADR 0032 addendum of
that day). Every household member gets an own proxy instance, so nothing is
shared that a dedicated runner proxy would serialize. The runner is one more
child of the member's own proxy (no wrapper: it spawns `sbx` itself and
Seatbelt does not nest, `call_timeout` 190, `startup_timeout` 120), and
mcp-skill reaches it by calling back into that same proxy:
`--runner-url http://127.0.0.1:<proxy port>/mcp`, `--runner-token-file` the
proxy's own token file, `--runner-max-body` equal to that proxy's
`--max-body-bytes` (its default is 4 MiB). Two runners must never share a
`work_root`: the second refuses to start on the work-root lock, and a child that
fails startup takes the whole proxy down with it. With `work_root: null` that
means one runner per uid.

## How it is tested

The `mcp_sandbox` suite `tests/test_mcp_sandbox.py` renders every config from
`tests/files/mcp_sandbox/config.json.tmpl` with `json.dumps`-quoted values into
`.claude/tmp/test_mcp_sandbox/`, and runs ten groups: the config file
`tests/test_mcp_sandbox.py:_run_a`, the Mach-O reader on synthetic images built
from header bytes plus one live python3 closure
`tests/test_mcp_sandbox.py:_run_macho`, `run_script` validation
`tests/test_mcp_sandbox.py:_run_c`, the sbx argv golden per platform
`tests/test_mcp_sandbox.py:_run_d`, the MCP surface
`tests/test_mcp_sandbox.py:_run_f`, the workdir lifecycle, the flatten remover —
`chmod 000`, `0500` and `0100` directories, `uchg`, deny ACEs, a deep
rename-chained tree removed in a child under a soft `RLIMIT_NOFILE` of 256 — and
the quarantine with an injected `remove_tree` `tests/test_mcp_sandbox.py:_run_h`,
live runs in all three interpreters including a cancel and an engine failure
after `spawned` through a pass-through fixture sbx
`tests/test_mcp_sandbox.py:_run_i`, startup, plans and refresh — every refused
start, a refresh on a re-pointed hop, a refresh holding its run's slot, a cancel
during a refresh — `tests/test_mcp_sandbox.py:_run_g`, and the acceptance escape
suite `tests/test_mcp_sandbox.py:_run_e`: E1 to E18 are the spec's acceptance
rows, E19 to E34 the risk rows, each an escape attempt from inside a run beside
a positive control, so a broken probe cannot pass as "denied", and each ending
with an empty work root. Security review round 1 added rows to three groups,
each run red first: `A11` serializes the worst-case reply through the real
tool-result path, `B21`-`B23` pin the closure's rpath-stack cap, its keg-walk
caps and its plan deadline and cancel, and `H20`-`H24` the bounded `out/` and
remover listings, the fd-relative macOS fallbacks, the survivor sweep and the
`out/` path cap (H22 and H23 INFO off macOS, counted).

The egress work (2026-10-12, [[0033-egress-through-an-allowlisting-proxy]])
added a tenth group and rows to four others, every one run red first: `A13`-`A19`
the `network` key, its pattern grammar, ports, NAT64 prefixes and the
`extra_reads`-over-`work_root` refusal; `D6` and `D7` the egress argv golden per
platform; `H25` and `H26` the `egress/` sweep and a planted entry; group N, the
proxy in-process against a stub resolver and connector
`tests/test_mcp_sandbox.py:_run_n` — head parsing, host forms, the allowlist,
address vetting, the tunnel, the deadline and caps, `close()`, the metadata-only
log, both endpoints, the sun_path alias, the dual loopback (N15), the absolute
lookup (N16), a stop during connect (N17), the half-close (N18), the IPv4
denylist, configured NAT64 and the process-wide budget (N19-N22); and `I13`-`I17`
live `tests/test_mcp_sandbox.py:_run_i_egress` — an allowed HTTPS fetch, a host
off the list refused, direct sockets and DNS denied inside, and no `network` key
unchanged (I13 INFO when the host itself cannot reach the test host).

Live rows are INFO off macOS and under a nested Seatbelt — detected by one
control sbx run whose stderr carries `sandbox_apply` — counted either way
`tests/test_mcp_sandbox.py:_nested_seatbelt`. The case count is declared once,
in `tests/run.py:SUITES`, and checked against the run; the forge target is
`mcp_sandbox`.

The server also sits in every fleet gate that discovers `Scripts/mcp-*.py`: the
read-loop (pool), cancel (KILL), wire-log, handler-crash and protocol-version
tables, the 3.10+ API scope of the dependency gate
`tests/test_py_deps.py:NEW_39_SCOPE`, and a smoke row with near-miss and
documented-call probes `Scripts/_mcp_smoke_test.py:NEAR_MISS_FUNCTION`.

## Declared limits

These are recorded, not closed; the reasoning and measurements are in
[ADR 0031](../adr/0031-sbx-restricted-mode.md) and
[ADR 0032](../adr/0032-a-runner-behind-a-proxy.md).

- **Same-uid argv and environment are readable (E28).** Inside a restricted
  Seatbelt run a script can read the argv **and environment** of any same-uid
  process — the runner's included — through `sysctl` `KERN_PROCARGS2`; the live
  row saw `MCP_PROXY_TOKEN` that way. The cause is the unfiltered
  `(allow sysctl-read)` in `ClaudeCode/skills/sandbox-run/scripts/sbx:_seatbelt_restricted_head`,
  which node and python need; name- and prefix-filtered denies of
  `kern.procargs2` had no effect, and a blanket deny aborts node and python's
  `import ctypes`. The clean environment hides nothing of *other* processes.
  The row stays INFO with its positive control
  `tests/test_mcp_sandbox.py:_run_e_risk`; ADR 0031. This is why the runner
  proxy is launched with `--require-token-file`.
- **Hostile depth.** The depth cap does not bound what rename-chaining builds: a
  deliberately hostile script can build on the order of a million levels in a
  120 s run (an extrapolation, unmeasured past 4059 levels). Removal is linear,
  so such a tree spills its cleanup into the quarantine over several retries,
  and the quarantine cap surfaces repeated abuse as `degraded`. There is no
  background reaper. A cut through a `uchg` or ACL'd directory is untested.
  ADR 0032.
- **A `setsid` orphan survives on macOS until cleanup.** The group kill cannot
  reach a process that left the group; it stays inside the same profile and
  rlimits, and the run does not wait for it. The survivor sweep before removal
  kills it if it is still in the run's session or its cwd is inside the
  workdir. It does not find a survivor that left the session AND moved its cwd
  out but still holds an fd into the tree (there is no fd walk), and a pid
  recycled between the check and the kill is a race it cannot close. Likewise
  an sbx killed between the target's exec and the `spawned` line leaves the
  runner no session to sweep, and the target runs on until its CPU limit or its
  own exit unless its cwd is still in the workdir.
- **The runner token file's location is a deployment rule.** Nothing checks
  that it lives under `~/.claude`; elsewhere a legacy `sbx` run can read it
  (ADR 0032, T3).
- **An engine failure after `spawned` is reported as a normal result**; nothing
  ran unconfined, and the profile shape is the one the self-test proved.
- **Every binary under `/bin` and `/usr/bin` is exec-able** inside the profile,
  still with no network beyond the run's egress proxy and one write scope; a setuid exec is refused by
  Seatbelt itself. **Linux has no exec filter**: a script may exec a binary it
  wrote into its workdir.
- **Linux is not live-verified** (`liveVerified: false`); its rows are golden
  argv and INFO.
- **`capabilities` exposes the read set** — absolute paths of kegs, framework
  images and extra reads — to its caller, who is the runner token's holder.
- **Same-uid trust.** Anyone who can read the runner proxy's token file can call
  `run_script` directly; the token is the boundary.
- **Egress does not inspect TLS.** A script can send anything to an allowed
  host; the allowlist bounds where, not what. Only proxy-aware clients
  (`urllib`, `requests`, `curl`) use the proxy; a raw socket or a non-HTTP
  protocol fails closed, and node's `fetch` with the environment proxy is
  unverified. [[0033-egress-through-an-allowlisting-proxy]].
- **The macOS egress endpoint cannot see the peer uid** (`LOCAL_PEERCRED` is
  `AF_UNIX`-only), so another local uid can use a run's allowlisted tunnels
  while it lasts; it cannot intercept the script's own connection.
- **A `work_root` under `/tmp` or `/run` cannot host a Linux egress socket**
  (sbx refuses a socket under its socket directories), and a proxy thread
  blocked in `getaddrinfo` runs on until the OS resolver gives up, no longer
  counting or relaying after `close()`.
