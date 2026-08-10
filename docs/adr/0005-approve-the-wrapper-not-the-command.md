---
name: 0005-approve-the-wrapper-not-the-command
type: adr
status: active
title: Approve the wrapper, not the command
description: Decision to shift the Bash trust boundary from an ever-growing per-command allowlist to one audited OS-kernel containment wrapper (sbx), auto-allowed by a grant-only, fail-to-prompt PreToolUse gate matched by canonical-path identity.
sources:
  - ClaudeCode/hooks/sbx-gate.py
  - ClaudeCode/skills/sandbox-run/scripts/sbx
  - ClaudeCode/skills/sandbox-run/scripts/selftest_probe.py
verified:
  commit: 1446acb
  date: 2026-08-08
links:
  - feature-implementation-plan
  - hooks
  - skills
  - 0007-a-path-spelled-deny-protects-the-spelling
---

# ADR 0005: Approve the wrapper, not the command

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the [[skills]] `p:sandbox-run` skill, the [[hooks]] roster,
and the [[feature-implementation-plan]] spec.

## Context

The permission surface for Bash had become a per-command allowlist that only
ever grew: roughly 140 lines of `permissions.allow` entries, each one a
whack-a-mole grant for a specific command the agent needed to run without a
prompt. Every new tool meant another line, and every line widened the trust
surface by exactly one command — with no containment behind it. An allowed
command runs with the full authority of the session: it can write anywhere,
reach the network, and read every secret the user can.

The alternative is to stop approving *commands* and approve one *containment
wrapper* instead. `sbx` (`ClaudeCode/skills/sandbox-run/scripts/sbx`) resolves a
request into an OS-kernel sandbox invocation — macOS Seatbelt (`sandbox-exec
-p` with an inline SBPL profile) or Linux `bwrap` — and `os.execvp`s the target
inside it: writes are default-deny except an explicit scratch/`--write` scope,
the network is off unless `--net`, and a fixed secret set (`~/.ssh`, `~/.aws`,
`~/.gnupg`, `~/.config/gh`, `~/.claude`, and every ancestor `.git/config`) is
denied both read and write even when the project tree is writable. On any
unsupported platform or missing engine binary it **fails closed** — one line to
stderr, non-zero exit, and the command never runs unsandboxed.

Approving the wrapper is safe *because the wrapper guarantees containment*. The
trust boundary moves from "is this specific command safe?" (an unbounded
question answered one line at a time) to "is this a genuine invocation of the
one audited wrapper?" (a bounded question answered once).

## Decision

Ship one grant-only PreToolUse(Bash) gate — `ClaudeCode/hooks/sbx-gate.py` —
that auto-allows a Bash command *without a prompt* **if and only if** the
command is provably a single, clean, un-wrapped invocation of the ONE deployed
`sbx` wrapper. The gate's `is_clean_sbx` classifier requires all of:

- **Canonical-path IDENTITY, never basename.** The first token is canonicalized
  (expand `~`, resolve a relative/`/`-bearing form against cwd, or
  `shutil.which` a bare name, then `os.path.realpath`) and must equal the
  deployed `WRAPPER_PATH` exactly. A program merely *named* `sbx` — a planted
  `./sbx`, `/tmp/x/sbx`, or a PATH-shadowing binary — is not the wrapper and
  grants no containment. A `os.path.basename(...) == "sbx"` compare is a
  FORBIDDEN false-allow (C1 / A01 / CWE-290).
- **Metacharacter-free.** Any of `; & | $ \` ( ) < > { } newline ' " \ * ?`
  anywhere in the raw command → reject to a prompt.
- **Every `--write` scope contained** inside the project root, tested with
  `os.path.commonpath` (separator-safe), never a bare `str.startswith`.
- **No `--net`.**

Under ANY doubt the gate emits nothing on stdout, and the normal permission
prompt runs. It NEVER emits `deny`; its only two outcomes are `allow` (narrow,
proven) and silence. **Fail-safe == fail-to-prompt, never fail-to-allow.**

The gate coexists with the pre-existing `mcp-first-guard.py` on the same
PreToolUse(Bash) matcher. Their polarities are disjoint — `mcp-first-guard.py`
is `deny`|empty, `sbx-gate.py` is `allow`|empty — so registration order is safe
either way. This was **confirmed empirically on a live host** (2026-08-08, both
registered guard-then-gate in one `"matcher": "Bash"` group): a clean
deployed-path invocation ran with **no permission prompt** — the gate's `allow`
was honored while the guard stayed silent — while `--net`, a `;`-chained
command, and a planted `./sbx` each fell through to a normal prompt. The
fallback remains safe regardless: a lost `allow` degrades to a prompt, never to
an unsandboxed auto-run.

## Alternatives Evaluated

### Option 1 — A bare allowlist rule `Bash(sbx *)`
- **Pros:** zero new code; reuses the existing permissions machinery.
- **Cons:** a glob on the command string is trivially chained — `sbx x ; rm -rf
  ~` matches `sbx *` and the tail runs unsandboxed. An allowlist pattern cannot
  express "a *single, clean* invocation"; that is exactly what the metacharacter
  rejection and the un-wrapped-identity check in the gate exist to prove.

### Option 2 — A Bash helper instead of a Python wrapper
- **Pros:** no interpreter dependency.
- **Cons:** a shell wrapper reintroduces quoting/word-splitting/injection bugs at
  the one layer that must be injection-proof. The Python helper passes the target
  as `os.execvp` argv **list elements**, never through a shell, so the target can
  never inject into the sandbox profile.

### Option 3 — A separate `/sandbox` slash-command
- **Pros:** explicit, discoverable.
- **Cons:** reintroduces the friction the wrapper removes — it cannot auto-allow,
  so every contained run still prompts. The whole point is to make the *safe*
  path the *frictionless* one.

### Option 4 — Docker / a VM / nsjail / a bespoke auto-runner
- **Pros:** stronger isolation in the abstract.
- **Cons:** scope creep and a far larger attack surface (a daemon, images, a
  network bridge, privilege to manage them) for a per-command sandbox. Seatbelt
  and bwrap are already present on the target OSes and need no privileged
  service. Adding a third OS later touches exactly one builder function plus one
  `BACKENDS` entry.

### Chosen — Approve the wrapper; a grant-only, fail-to-prompt, identity-checked gate
The wrapper is the single audited containment boundary; the gate only removes a
keypress for a provably clean invocation of it. Two ends, two responsibilities,
neither trusting the other.

## Consequences

- **Why fail-OPEN is fail-SAFE here.** The ~10-line JSON I/O envelope is copied
  from `mcp-first-guard.py`, which fails open ("never brick Bash"). For a
  *deny*-guard fail-open is a genuine weakness — an error lets a blocked command
  through. For THIS *grant-only* gate the polarity flips: an error → no JSON on
  stdout → no auto-allow → the normal prompt runs → the command is never
  auto-run and never runs unsandboxed. Inheriting the exact fail-open harness is
  therefore the CORRECT safe default, not a liability. Do not "fix" it into a
  fail-closed brick.

- **The inverted safety model — COPY, never IMPORT.** `mcp-first-guard.py`
  steers-and-fails-open, so it must SEE THROUGH obfuscation (peel wrappers,
  descend into `$( )`/backticks/`bash -c`, fold ALL-CAPS names): a blocked
  command hidden one layer down must still be caught. This gate
  authorizes-and-fails-toward-the-prompt, so it must do the OPPOSITE — REJECT
  anything it cannot trivially prove is a bare `sbx` invocation. Interpreting
  chaining/substitution here is not merely unnecessary, it is the exact surface
  an authorizing gate must refuse. So `sbx-gate.py` imports NOTHING from
  `mcp-first-guard.py` and shares NONE of its parser; it copies only the JSON
  envelope and carries its own tiny CONSERVATIVE classifier that blanket-rejects
  a metacharacter blocklist. Under-allow is always safe. Do not "improve" the
  gate by importing that parser.

- **Identity, not filename.** The gate authorizes only when the first token
  canonicalizes to the exact deployed `WRAPPER_PATH`. This is the load-bearing
  anti-CWE-290 property and must never be relaxed to a basename compare.

- **Shared path-resolution contract is deliberately duplicated.** `resolve_scope`
  and `project_root` are byte-identical in the gate and the helper because the
  gate imports nothing; a cross-file parity test pins them. A one-sided edit
  reintroduces a gate/helper divergence == a containment escape.

- **Residual-risk / verification record (stronger than the plan predicted).**
  - macOS: the Seatbelt secret boundary depends on SBPL **last-match-wins**
    ordering — the secret denies are emitted AFTER the permissive
    `(allow file-read* (subpath "/"))` so they win. That ordering was UNVERIFIED
    in-repo; it is now **live-verified on the host** — `selftest_probe.py` probe
    (d) confirmed `~/.ssh/id_rsa` is unreadable under `--write .`. The macOS
    secret boundary is therefore **probe-verified, not CI-verified**.
  - Linux: the `bwrap` path is now **live-verified on a real host (bubblewrap
    0.6.1)**, which caught TWO Linux-only defects that offline assertions and the
    macOS test missed, both fixed: (i) `--tmpfs` over the `.git/config` regular
    FILE aborts bwrap with ENOTDIR — masked instead with `--ro-bind /dev/null
    <file>` for regular-file secrets; (ii) masking a NONEXISTENT secret dir under
    the read-only root aborts bwrap at setup — so `secret_paths` now
    existence-filters (a nonexistent secret has nothing to protect; macOS
    deny-of-nonexistent is a no-op, so the filter is cross-platform-safe).
  - Nuance: on Linux a secret DIRECTORY is masked via an empty `--tmpfs` (files
    vanish → ENOENT) rather than EPERM, so probe (d) reports **inconclusive** on
    Linux even though containment holds. Verify manually with
    `sbx --write . -- ls -a ~/.ssh`.

- **Accepted assumption (INFO), not a defense the gate provides.** A realpath→exec
  TOCTOU on the wrapper token — a symlink that resolves to `WRAPPER_PATH` at
  classify-time but is swapped before exec — is OUT of the stated adversary model
  (the adversary is a command STRING, not a concurrent local process racing the
  filesystem). It is recorded here as an accepted assumption, not a mitigation.

- **seccomp (Linux) is off by default.** A dormant `if scope.seccomp:` seam inside
  `_bwrap_argv` marks where a future BPF filter would plug in; no flag sets it, and
  if it were ever set with no filter wired it refuses (raises) rather than running
  unfiltered. Until then, bwrap's namespace + no-net + read-only bind is the
  enforced Linux boundary.
