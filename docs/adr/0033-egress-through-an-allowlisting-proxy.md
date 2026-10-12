---
name: 0033-egress-through-an-allowlisting-proxy
type: adr
status: active
title: Egress through an allowlisting proxy -- a per-run CONNECT proxy in the runner, one sanctioned endpoint in the sandbox
description: Decision to give the mcp-sandbox runner network egress without giving the sandbox the network -- an optional global network allowlist in the runner config, a stdlib CONNECT-only proxy inside the runner that vets every name and every resolved address, one proxy per run, and a single sandbox-side hole per platform (a Seatbelt localhost:N allow on macOS, a read-only bound unix socket and an in-sandbox forwarder on Linux), with both new sbx flags always prompting at the gate; with the egress/ work_root entry and the sun_path alias, the Linux RLIMIT_NPROC task-count fix found on the way (amends ADR 0031), the alternatives rejected, the security review of 2026-10-12 and what each finding changed, the live measurements on macOS and Ubuntu 22.04, the user-private-group refinement of the ancestor rule, and the declared limits -- among them that TLS is not inspected and that the macOS endpoint cannot tell which local uid connects.
links:
  - 0031-sbx-restricted-mode
  - 0032-a-runner-behind-a-proxy
  - 0005-approve-the-wrapper-not-the-command
  - 0024-pure-python-39-and-the-stdlib
  - 0015-ambiguity-is-the-defect
  - mcp-sandbox
  - mcp-skill
  - skills
  - hooks
  - tests
---

# ADR 0033: Egress through an allowlisting proxy

Status: accepted 2026-10-12 as the design the code in the working tree
implements, on top of `5498e8f`, and **not yet committed when this page was
written**; the commit that lands it is recorded by a dated addendum below, never
by an edit. The decisions are Zoltán's unless a line says otherwise. It amends
[[0031-sbx-restricted-mode]] (two restricted-only sbx flags, the gate, and the
Linux `--rlimit-nproc +N` unit) and [[0032-a-runner-behind-a-proxy]] (the runner's
`network` config, the per-run proxy, the `egress/` work-root entry); both are
accepted and get their pointers as dated addenda. Every number below is a
measurement or a count at the moment of the decision.

## Context: a runner with no network, and two engines that cannot filter by name

ADR 0031's restricted mode has no network at all: Seatbelt ends the profile with
`(deny network*)` and bwrap runs the target in `--unshare-net`. That was the
right default and stays the default. But the skill scripts the runner exists for
([[0032-a-runner-behind-a-proxy]]) include ones whose whole job is an HTTPS call
to a known service, and "no network" made them unrunnable.

Neither engine can express "these domains and nothing else". Seatbelt's network
filter takes a host of `*` or `localhost` only (the SBPL compiler refuses
`127.0.0.1`: "host must be * or localhost"), and bwrap's choice is the host's
network namespace or an empty one. A domain allowlist therefore cannot live in
the sandbox. It has to live outside, in something the sandbox is forced to go
through, and the sandbox may reach only that.

## Decision

### D1 -- an optional, global allowlist in the runner config

The runner config gains one optional top-level key, `network`
`Scripts/mcp-sandbox.py:_cfg_network`; absent means no network and the run is
byte-for-byte what it was (`mcp_sandbox:I17`). It is
`{allow: [host | "*.suffix"], ports: [int], nat64: [IPv6 prefix]}`, `ports`
defaulting to `[443]` and `nat64` to none. The allowlist is global for the whole
runner, not per skill (Zoltán). Validation is strict and every refusal names the
key -- `network.allow[i]`, `network.ports[i]`, `network.nat64[i]` -- and never
echoes the value ([[0015-ambiguity-is-the-defect]]'s house rule for config). A
host is lower-case DNS labels; an IP literal in any `inet_aton` spelling and a
trailing dot are refused `Scripts/mcp-sandbox.py:_hostname_problem`. `*.` matches
strict subdomains only, never the apex, and a `*.` over a single label (`*.com`)
is refused. Bounds at the time: 64 patterns, 8 ports, 8 NAT64 prefixes of an RFC
6052 length (32, 40, 48, 56, 64 or 96) `Scripts/mcp-sandbox.py:_cfg_nat64`.

### D2 -- a CONNECT-only proxy inside the runner, stdlib threads

`Scripts/mcp-sandbox.py:EgressProxy` is a host-side HTTP proxy that serves
exactly one method, `CONNECT`, and refuses everything else with 405
`Scripts/mcp-sandbox.py:parse_connect_head`. The head is bounded (8192 bytes, 64
header lines, else 431) and must arrive within a 10 s **total** deadline; the
request line, host and port grammar are strict (400). Then, in this order:

1. The host passes the same name rules as the config, plus the allowlist and the
   port list `Scripts/mcp-sandbox.py:egress_host_refusal`
   `Scripts/mcp-sandbox.py:egress_host_allowed` -- 403 before any lookup.
2. **One** lookup, of the absolute name `host + "."`, so no resolver search list
   can turn an allowlisted name into a different FQDN
   `Scripts/mcp-sandbox.py:_default_resolver`.
3. **Every** answer must be public, or the request is refused 403 -- not just
   the one it would connect to `Scripts/mcp-sandbox.py:egress_address_public`.
   Public is an explicit IPv4 IANA special-purpose denylist checked before
   `is_global` `Scripts/mcp-sandbox.py:_EGRESS_V4_DENY` (so the verdict does not
   depend on the interpreter's `ipaddress` tables), then for IPv6 the
   v4-mapped, 6to4, Teredo and site-local forms, the two well-known NAT64
   prefixes `Scripts/mcp-sandbox.py:_NAT64_NETS` and the operator's
   `network.nat64`.
4. The connect goes to the **vetted sockaddr itself**
   `Scripts/mcp-sandbox.py:_default_connector`: no second resolution, so no
   DNS-rebinding window between the check and the use.

Caps and timeouts at the time: 8 tunnels per run (503 beyond)
`Scripts/mcp-sandbox.py:_EGRESS_MAX_TUNNELS`, 32 connections per run
`Scripts/mcp-sandbox.py:_EGRESS_MAX_CONNECTIONS`, 64 connections process-wide
across every run's proxy `Scripts/mcp-sandbox.py:_EGRESS_GLOBAL_CONNECTIONS`;
connect 10 s and interruptible by `close()`, idle 120 s, send 30 s. A half-close
is relayed: EOF on one side becomes `shutdown(SHUT_WR)` on the other, and the
tunnel ends when both directions are done, on idle, or on stop. The log carries
host, port, status, vetted IP and byte counts -- never a payload byte.

### D3 -- one proxy per run

`Scripts/mcp-sandbox.py:_RunEgress` owns one run's proxy. It starts before sbx is
spawned and closes in the run's `finally`, with a deadline of
`min(now + 1 s, run deadline - cleanup term)` `Scripts/mcp-sandbox.py:_spawn_and_read`,
so the egress term fits inside the KD-17 chain of ADR 0032 unchanged. A connect
that completes after `close()` has begun is closed and not counted; the run's
summary is final when `close()` returns.

### D4 -- one sanctioned hole per platform

Both endpoints are **restricted-only** sbx flags: each requires `--restrict-reads`,
they are mutually exclusive and neither combines with `--net`
`ClaudeCode/skills/sandbox-run/scripts/sbx:validate_egress`
`ClaudeCode/skills/sandbox-run/scripts/sbx:parse_egress_port`.

- **macOS, `--egress-port N`.** The profile keeps `(deny network*)` and adds,
  after it, `(allow network-outbound (remote tcp "localhost:N"))`
  `ClaudeCode/skills/sandbox-run/scripts/sbx:_seatbelt_egress_rule`. Measured
  (M0, macOS 14.2.1): that rule reaches 127.0.0.1:N and [::1]:N and nothing else
  -- another loopback port, an outside address, UDP, the mDNSResponder socket
  and DNS stay denied. Because the rule covers both loopbacks, the runner binds
  127.0.0.1:N **and** [::1]:N (`IPV6_V6ONLY`), retrying up to
  `Scripts/mcp-sandbox.py:_EGRESS_BIND_ATTEMPTS` ports and failing closed, so no
  other uid can squat the v6 side; a host with no `::1` gets v4 only.
- **Linux, `--egress-socket PATH`.** `--unshare-net` stays. The runner's unix
  socket is read-only bound at
  `ClaudeCode/skills/sandbox-run/scripts/sbx:EGRESS_SOCKET_DEST`, and the target
  runs behind an in-sandbox stdlib forwarder on 127.0.0.1:3128
  `ClaudeCode/skills/sandbox-run/scripts/sbx:EGRESS_FORWARDER`
  `ClaudeCode/skills/sandbox-run/scripts/sbx:EGRESS_FORWARD_PORT` (the runner
  pins the same number, `Scripts/mcp-sandbox.py:_EGRESS_LINUX_PORT`). The
  forwarder splices bytes only, caps itself at 32 connections (the runner's
  per-run cap), and drops -- never dies on -- a connection it cannot start a
  thread for. Its interpreter is sbx's own and must already be visible inside
  the restricted root, else exit 3 "grant it with --read"
  `ClaudeCode/skills/sandbox-run/scripts/sbx:egress_python`; no read is added
  for it.
- **The socket path is vetted** `ClaudeCode/skills/sandbox-run/scripts/sbx:egress_socket_refusal`:
  its own realpath, under no socket directory (`SOCKET_REFUSED_DIRS`, spelled
  and realpath'd), a socket, owned by the euid, in a 0700 euid-owned directory,
  and every ancestor up to `/` owned by root or the euid and writable by nobody
  else (see "The user-private-group refinement" below).
- **The environment** the runner sets for the script is `HTTPS_PROXY`,
  `HTTP_PROXY`, `https_proxy`, `http_proxy` = `http://127.0.0.1:<port>` (the
  proxy's port on macOS, 3128 on Linux), with `NO_PROXY` and `no_proxy` empty
  `Scripts/mcp-sandbox.py:build_sbx_argv`.

### D5 -- the gate always prompts for both flags

`--egress-port` and `--egress-socket` join `--net` and `--status-fd` in
`ClaudeCode/hooks/sbx-gate.py:_PROMPT_FLAGS`: a direct sbx call that opens an
endpoint is never auto-allowed. The `=` forms and abbreviations fall to the
gate's unknown-token prompt, and sbx's parser has `allow_abbrev=False`
(`sbx_gate:EG1`-`EG5` beside the `EG-ctl` control; `sbx_restrict:S17`). This is
ADR 0005's rule applied once more: the gate auto-allows only what narrows.

### D6 -- the caller can see it

`capabilities` carries `network: {enabled, allow, ports}`
`Scripts/mcp-sandbox.py:capabilities`, and a run result carries
`egress: {tunnels, refused}` only when `network` is configured.

## Where the Linux socket lives: `egress/` and the sun_path alias

Each Linux run's socket is `<work_root>/egress/<32-hex>/s` in a 0700 per-run
directory. `egress/` is a runner-owned work-root entry exactly like
`quarantine/`: created at startup, opened `O_NOFOLLOW` and vetted on the
descriptor `Scripts/mcp-sandbox.py:_open_egress_root`, stale entries swept at
startup, and a planted file or symlink at the name refuses the start
`Scripts/mcp-sandbox.py:sweep_work_root` (`mcp_sandbox:H25`, `H26`). It is never
inside a run workdir, so the script cannot touch it.

The first placement was `/tmp`. sbx refused it: a socket under its
`SOCKET_DIRS` (`/run`, `/tmp`, `/var/snap`) is exactly what restricted mode
hides. The work root is the runner's own 0700 tree and was the natural home.

A deep work root can push `<dir>/s` past the `AF_UNIX` path limit (107 bytes on
Linux, 103 on darwin). On Linux the runner then binds through
`/proc/self/fd/<dirfd>/s` after an lstat/fstat device-and-inode match, so the
socket inode still lands at the vetted path; elsewhere it refuses with "the
egress socket path exceeds the AF_UNIX limit; shorten work_root"
`Scripts/mcp-sandbox.py:EgressProxy` (`mcp_sandbox:N14`).

An interpreter `extra_reads` entry that equals or is an ancestor of `work_root`,
spelled or realpath'd, is now refused at load: on Linux it would expose sibling
runs' workdirs and egress sockets to a run
`Scripts/mcp-sandbox.py:_refuse_reads_over_work_root` (`mcp_sandbox:A19`).

## Found on the way: Linux RLIMIT_NPROC counts tasks, not processes

This one is a defect in what shipped with ADR 0031 (`7b603d2`), found when the
egress rows were first run on a Linux host. sbx resolved `--rlimit-nproc +N`
against the uid's **process** count (`/proc/[0-9]*`), but Linux charges
`RLIMIT_NPROC` per **task** (thread). On the Ubuntu host measured (para) the uid
had 127 processes and 1054 threads, so the resolved limit was 191 -- far below
the current count -- and bwrap's own `clone()` failed `EAGAIN`. The runner could
not start at all on Linux.

The base is now `ClaudeCode/skills/sandbox-run/scripts/sbx:uid_task_count`, which
sums the `Threads:` line of `/proc/<pid>/status`, selecting the uid by the real
uid of the same read `ClaudeCode/skills/sandbox-run/scripts/sbx:uid_process_count`.
macOS is unchanged: it counts processes there, which is what its kernel charges.
Row `sbx_restrict:X15`, red first. This amends ADR 0031's strict-rlimit section
and gets its own line in that ADR's addendum.

## Alternatives rejected

1. **Transparent per-domain filtering in the sandbox.** Impossible without root:
   neither Seatbelt nor bwrap filters by domain, and redirecting all traffic into
   a filter needs packet rules a user cannot install.
2. **pasta or slirp4netns** as a user-mode network for the bwrap namespace.
   Not installed on the target hosts, and would still need a filter in front.
3. **A Python `sitecustomize` layer** that routes every socket through the
   proxy transparently. Deferred by Zoltán, not rejected on merit: it would
   reach clients that ignore `HTTPS_PROXY`, at the cost of interpreter-specific
   machinery inside the sandbox.
4. **One dedicated, shared egress proxy** for every runner. Rejected: every
   household member runs an own proxy instance and an own runner (ADR 0032's
   addendum of 2026-10-12), so there is nothing to share; a per-run proxy also
   ties every tunnel's lifetime to its run.
5. **The socket directory under `/tmp`.** Refused by sbx's `SOCKET_DIRS` rule,
   by design -- see above.

## Security review 2026-10-12 (20261012-110000)

A code-mode review of the uncommitted diff of `sbx`, `sbx-gate.py` and
`mcp-sandbox.py` in three lanes -- ssrf-net, parseval and access-control --
raised 22 candidates, deduplicated to 14 (F1-F14). The verdict was **APPROVE**:
11 verified (8 LOW, 3 INFO), 3 suppressed. Per Zoltán's standing rule every LOW
and INFO was fixed except F2, which is declared. Each fix carries a row written
red first.

- **F1, the v6 loopback was free.** The Seatbelt rule reaches [::1]:N but the
  proxy bound only 127.0.0.1:N, so any local process could bind [::1]:N and
  receive the script's traffic outside the allowlist. Fixed by binding both
  (`mcp_sandbox:N15`).
- **F2, the macOS TCP endpoint is unauthenticated.** Declared, not fixed: see
  the limits.
- **F3, resolver search lists.** A bare name could be expanded by the resolver
  to a different FQDN. Fixed by resolving the absolute name `host + "."`
  (`mcp_sandbox:N16`).
- **F4, a wildcard over a TLD.** `*.com` was accepted. Now refused; a `*.`
  pattern needs at least two labels (`mcp_sandbox:A17`; `A16` and `N19` keep
  hex-letter TLDs such as `.cafe` accepted).
- **F5, interpreter-dependent `is_global`.** Python 3.9's tables misjudge some
  IPv4 special-purpose ranges, and operator NAT64 prefixes embed IPv4. Fixed by
  the explicit IANA denylist and the `network.nat64` key (`mcp_sandbox:N20`,
  `N21`, `A18`).
- **F6, a connect could complete after close.** Now interruptible by `close()`,
  and a late connect is closed and uncounted (`mcp_sandbox:N17`). The blocking
  `getaddrinfo` half is declared (below).
- **F8, the socket's ancestors were unchecked.** Only the parent's mode was
  vetted, while bwrap re-resolves the bind source at mount time. Fixed by the
  every-ancestor rule (`sbx_restrict:N12`).
- **F9, `extra_reads` over `work_root`.** Fixed by the load-time refusal
  (`mcp_sandbox:A19`).
- **F12, no cross-run bound on proxy threads.** Fixed by the process-wide budget
  of 64 connections (`mcp_sandbox:N22`).
- **F13, the first EOF ended the whole tunnel.** Fixed by relaying the
  half-close (`mcp_sandbox:N18`). Consequence: a client that closes fully holds
  its tunnel slot until upstream ends or idles out, as the sbx forwarder does.
- **F14, a failed `Thread.start` killed the forwarder.** It now drops that one
  connection and keeps serving (`sbx_restrict:N13`).
- **Suppressed: F7** (the per-run egress directory is handled by path, so only a
  same-uid writer of `work_root` could race it -- ADR 0032's same-uid trust),
  **F10** (a same-uid process can inflate the nproc base at count time, before
  the fork, as it could before the fix), and **F11** (the gate's `=`-form and
  abbreviation question, which the lane itself traced to a prompt; `sbx_gate:EG2`
  and `EG5` pin it).

## Live measurements

- **macOS** (Darwin 23.2.0): `sbx_restrict:N10` live Seatbelt -- the allowed
  port echoes; DNS, mDNS, another port and outside addresses are denied.
  `mcp_sandbox:N15` holds both loopbacks; `I14` and `I15` ran live through the
  proxy. `I13` was INFO on this host: `example.com:443` was not reachable from it.
- **Linux, para** (Ubuntu 22.04.4, kernel 5.15, Python 3.10.12, bwrap 0.6.1,
  umask 0002 under user-private groups): `sbx_restrict` 134 cases, 102 pass,
  0 fail, 32 INFO, with `N11` (the live forwarder) passing; `mcp_sandbox` 192
  cases, 126 pass, 0 fail, 66 INFO, with `I13`-`I16` passing --
  `https://example.com` answered 200 through the tunnel, a host off the list got
  403, and a direct connect to 1.1.1.1:443 got `ENETUNREACH`.
- **The whole fleet**, before the refinement below: 42 suites, 6328 cases,
  6066 pass, 0 fail, 262 INFO.
- **Counts** at the time (`tests/run.py:SUITES`): `mcp_sandbox` 154 to 192,
  `sbx_restrict` 118 to 134, `sbx_gate` 135 to 141.

## The user-private-group refinement

The first ancestor rule refused any group-writable ancestor. On para it refused
every run: under `umask 0002` and user-private groups, every directory a user
creates is 0775, so the user's home tree is group-writable throughout. A group
that holds nobody but the user grants nobody else anything, so the rule now
accepts a group write bit when the group is the euid's user-private group -- no
other member in `grp`, and no other passwd entry with that primary gid; an
unknown gid fails closed `ClaudeCode/skills/sandbox-run/scripts/sbx:_private_group`
(`sbx_restrict:N14`). World-writable stays refused unless the directory is
root-owned and sticky. On macOS `staff` is a shared group, so a group-writable
ancestor stays refused there -- and darwin uses `--egress-port`, not the socket.

## Declared limits

- **TLS is not inspected.** Anything the script sends to an allowed host goes;
  the allowlist bounds where data can go, not what.
- **Only proxy-aware clients use it.** `urllib`, `requests` and `curl` honour
  `HTTPS_PROXY`; a raw socket and anything that is not HTTP fail closed. Node's
  `fetch` and the environment proxy were not verified.
- **No transparent per-domain filtering without root** (accepted by Zoltán; the
  `sitecustomize` layer is deferred, alternative 3).
- **F2: the macOS endpoint cannot see the peer uid.** `LOCAL_PEERCRED` exists
  only for `AF_UNIX` -- measured on Darwin 23.2, `getsockopt(0, 1)` on a loopback
  TCP socket is `IP_OPTIONS` and returns 0 bytes. Another local uid can connect to
  the proxy during a run and use the allowlisted tunnels. It cannot intercept the
  script's own connection (F1).
- **A configured `work_root` under `/tmp` or `/run` cannot host the egress
  socket** (sbx's `SOCKET_DIRS` refusal).
- **A proxy thread blocked in `getaddrinfo` cannot be interrupted.** It ends on
  the OS resolver's timeout, but after `close()` it no longer counts or relays.
- **Linux stays `liveVerified: false`** in `capabilities`
  `Scripts/mcp-sandbox.py:LIVE_VERIFIED`, although para ran the live rows; the
  flag was not changed by this work.
