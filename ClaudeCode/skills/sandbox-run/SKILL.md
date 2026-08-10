---
name: sandbox-run
description: Run a shell command inside an OS-level sandbox via the bundled `sbx` wrapper -- macOS Seatbelt or Linux bwrap, fail-closed on any other platform or missing sandbox binary. PREFER THIS over a bare Bash invocation whenever the command's effects are not fully known in advance: a throwaway script just written to `.claude/tmp/`, a generated one-liner, anything third-party or unread, or a command that writes and you are not certain where. It costs nothing extra -- the paired `sbx-gate.py` PreToolUse(Bash) hook auto-allows a clean, in-project, network-free invocation of the ONE deployed wrapper WITHOUT a permission prompt, so a command that is not already allow-listed runs prompt-free under `sbx` and prompts without it. Defaults with no flags: writes default-deny except `<cwd>/.claude/tmp`; network off; `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude` (except its read-only `skills/` and `scripts/`) and every ancestor `.git/config` unreadable even inside a writable scope. `--write DIR` (repeatable; `--write .` opts the repo in) widens writes and still auto-allows inside the project; `--ro` denies every write; `--dry-run` prints the resolved plan and runs nothing. Do NOT reach for it for build/test/clean (that is `forge_call`), read-only system inspection (`inspect_call`), or anything needing the network -- `--net` is never auto-allowed and always prompts, by design. The gate matches the deployed wrapper by canonical-path IDENTITY, never by filename; its registration is manual and the user's responsibility, and there is no installer.
model: sonnet
---

# Sandbox Run

Run a command inside an OS-enforced sandbox: writes are **default-deny** except a scratch scope, the network is **off**, and a fixed set of secrets is **unreadable** even when the project tree is writable. The command's exit status passes straight through as the wrapper's own.

This skill has two independent halves that must never be conflated (see **Trust boundary** below):

- **The helper `sbx`** -- the actual containment boundary. It resolves the request into an OS-sandbox invocation (macOS Seatbelt `sandbox-exec -p`, or Linux `bwrap`) and `os.execvp`s the target inside it. On any unsupported platform or missing sandbox binary it **fails closed** -- writes one line to stderr and exits non-zero, and NEVER runs the command unsandboxed.
- **The gate `sbx-gate.py`** -- a PreToolUse(Bash) hook that only decides *prompt vs. auto-allow*. It grants a no-prompt auto-run to a provably clean invocation of the one deployed wrapper and stays silent (normal prompt) for everything else. It does not itself contain anything.

## The helper script -- ~/.claude/skills/p/skills/sandbox-run/scripts/sbx

Stdlib-only Python (no `.py` extension -- the CLI name is intentional). Build order is pure-pieces-first / execvp-last: it resolves the writable scopes, the secret deny set, and the per-backend sandbox argv as pure data, then applies `resource.setrlimit` and `os.execvp` exactly once. Everything after the FIRST bare `--` is the target command, handed to `os.execvp` as argv LIST elements -- never through a shell -- so it can never inject into the sandbox profile.

```
~/.claude/skills/p/skills/sandbox-run/scripts/sbx [--write DIR]... [--net] [--ro] [--dry-run] -- <cmd> [args...]
```

Flags (parsed left of the first bare `--`):

- `--write DIR` (repeatable) -- add a writable scope; `--write .` opts the repo itself in. Each scope is `realpath`-canonicalized and REJECTED (fail-closed) if it contains a shell/SBPL metacharacter (`"` `'` `(` `)` `\` newline).
- `--net` -- allow the network (denied by default). NOTE: a `--net` invocation is never auto-allowed by the gate; it always prompts.
- `--ro` -- deny ALL writes, including the default scratch scope.
- `--dry-run` -- print the resolved plan and run NOTHING (no child is exec'd, no scratch dir is created; the helper exits 0). NOTE: unlike `--net`, a `--dry-run` invocation IS auto-allowed by the gate -- running nothing grants strictly less than the plain `sbx -- <cmd>` form the gate already allows, so it needs no extra trust. It is also the only auto-allowed form that completes inside Claude Code's own command sandbox (a real run needs a nested `sandbox-exec`, which that sandbox refuses -- see the nesting caveat under **Registration**), which makes it the live diagnostic for checking that the gate is registered and firing.

Defaults with no flags: the sole writable scope is `<cwd>/.claude/tmp` (scratch, created on demand); the network is denied; and the secret deny set -- `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude`, plus the `.git/config` of EVERY ancestor `.git` from `cwd` up to the filesystem root -- is denied both read and write, even inside a writable scope.

**The one carve-out: `~/.claude/skills` and `~/.claude/scripts` are READABLE** (never writable), emitted AFTER the deny so it wins -- Seatbelt `(allow file-read* (subpath ...))` by last-match-wins, bwrap `--ro-bind` by later-mount-wins. Without it the project's own tooling is unrunnable under the sandbox, and the reason is not the obvious one. Seatbelt matches the **terminal node canonically** -- a script whose resolved path lands in the repo is already covered by the permissive read-allow -- but it also checks **each symlink at its own un-followed path**. `~/.claude/skills/p` is a symlink into the repo, so the readlink of THAT path falls under the `~/.claude` deny and resolution dies at the link, never reaching the target. Measured both ways: `Operation not permitted` on `~/.claude/skills/p/skills/**/scripts/*.py` before the carve-out, and the identical command runs after it. Plain ancestor traversal is NOT gated -- if it were, the carve-out could not work at all, since its own parent `~/.claude` stays denied. The carve-out is deliberately **fail-closed**: it names two subdirectories to open rather than listing secrets to hide, so everything else under `~/.claude` -- `projects/` (the full session-transcript history), `.credentials.json`, `settings*.json`, `hooks/` -- stays denied, including files that land there in the future. The write-deny on `~/.claude` is untouched and load-bearing: a sandboxed command must never be able to rewrite its own gate.

**The shadow write-denies.** A deny set spelled `~/.claude` protects that NAME, not the inodes behind it. On a plugin-developer host `~/.claude/hooks`, `~/.claude/scripts`, `~/.claude/skills/p` and `~/.claude/CLAUDE.md` are symlinks INTO the project, so `--write .` reached the gate's own source under its repo spelling: a sandboxed command could rewrite the very hook that auto-allowed it, and the next Bash call would run the rewritten gate. Measured live, then closed. `sbx` now walks the direct children of `~/.claude` and of each carve-out, and emits a **write-only** deny for every symlink target that leaves `~/.claude`. They are the LAST file rules in the profile, so no later allow can undo them. Intended consequence: under `--write .` the plugin's own executable configuration (`ClaudeCode/`, `Scripts/`) is **read-only**. Edit those with normal tools -- a sandboxed command has no business writing the code that contains it.

## Usage

Invoke the bundled helper by its **absolute deployed path** (there is no `$ARGUMENTS` substitution in this repo -- a skill references its bundled script by that path):

```
~/.claude/skills/p/skills/sandbox-run/scripts/sbx -- ./run-tests.sh                 # scratch-only, no net
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --write . -- make build           # repo writable, no net
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --net -- curl https://example.com  # network allowed (will PROMPT)
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --ro -- python3 analyze.py         # no writes at all
~/.claude/skills/p/skills/sandbox-run/scripts/sbx --dry-run --write . -- make        # preview the plan, run nothing
```

**Keep the scaffolding INSIDE the target script, never around the call.** A timing harness like `start=$(date +%s); sbx -- python3 convert.py; echo $((end-start))` hard-prompts before the gate even looks at the wrapper: `;` is R1, `$(...)` is R3, and the `start=` prefix is R7. The sandbox still contains the run, but the auto-allow is gone -- you pay the wrapper's cost and get none of its benefit. Move the clock, the exit-code echo and any `&&` chaining into the target script, and the identical run costs zero prompts. The same applies to `cd X && sbx ...` (use the script's own `os.chdir`) and to output redirection (write the file from inside the script, into the scratch scope).

## Workflow

### Step 1 -- Parse arguments

- Collect every `--write DIR` (repeatable), and detect `--net`, `--ro`, `--dry-run` in the invocation. Everything after the FIRST bare `--` is the target command, taken verbatim.
- Do NOT expand, rewrite, or shell-interpret the target command -- it is passed through to `sbx` as argv elements, which hands them straight to `os.execvp`. Quoting/globbing/substitution is neither performed nor honored here.
- A missing `--`, or nothing after it, is a usage error: `sbx` reports it on stderr and exits non-zero. There is no default command.

### Step 2 -- Invoke the wrapper

Run the helper by its absolute deployed path with the parsed flags: `~/.claude/skills/p/skills/sandbox-run/scripts/sbx <flags> -- <cmd> [args...]`. Because `os.execvp` replaces the process image (same PID) and the sandboxed command's exit status is the wrapper's own, there is no fork/waitpid wrapper to interpret.

## Invocation contract

The wrapper is invoked -- and auto-allowed by `sbx-gate.py` **without a permission prompt** -- ONLY by its absolute deployed path `~/.claude/skills/p/skills/sandbox-run/scripts/sbx`, and the gate matches it by canonical-path **IDENTITY** (KD-6), NEVER by filename. The gate canonicalizes the first token -- expand `~`, resolve a relative/`/`-bearing form against `cwd`, or `shutil.which` a bare name, then `os.path.realpath` -- and grants the auto-allow ONLY when that canonical form equals `WRAPPER_PATH` exactly. A program merely NAMED `sbx` elsewhere -- a planted `./sbx` in a writable dir, `/tmp/x/sbx`, or a PATH-shadowing binary -- is NOT the deployed wrapper and gets **no** auto-allow (it falls through to a normal prompt). A basename compare (`os.path.basename(...) == "sbx"`) is a forbidden false-allow.

Beyond identity, the auto-allow fires ONLY when the command is a single, clean, un-wrapped invocation: metacharacter-free (any of `; & | $ \` ( ) < > { } newline ' " \ * ?` anywhere means prompt), every `--write` scope separator-safe-contained inside the project root, and no `--net`. Under ANY doubt the gate emits nothing (empty stdout) and the normal permission prompt runs -- it never emits `deny`.

The gate's **recognized flag set** left of the bare `--` -- the accept-set that this section MUST stay in sync with -- is exactly:

| Token | Gate verdict |
|---|---|
| `--net` | **refused** -- always prompts (R11) |
| `--ro` | accepted (argument-less; imposes no scope) |
| `--write DIR` / `--write=DIR` | accepted ONLY if `DIR` resolves separator-safe-contained inside the project root; otherwise prompt |
| `--dry-run` | accepted (argument-less; runs NOTHING, so it grants strictly less than a plain `sbx -- <cmd>`) |
| **anything else** | **hard prompt** -- never skipped |

The gate matches argparse's grammar and is never MORE permissive than it: `--dry-run` is recognized ONLY as the exact bare token, because the helper's `store_true` rejects `--dry-run=1`, so that equals-form is an unrecognized token and hard-prompts. Likewise ANY other token before the `--` -- an unknown flag, an unhandled equals-form, a stray argument -- is a hard prompt, never silently skipped. This section and the gate's docstring / `is_clean_sbx` accept-set are the two ends that the H2 fix keeps in sync: the gate's accept-set and this documented invocation form MUST intersect, so edit them together or not at all.

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

**seccomp (Linux) is OFF by default.** There is a dormant `if scope.seccomp:` seam inside `_bwrap_argv` (locked decision 8) marking where a future BPF syscall filter would plug in without a new code path. No `sbx` flag ever sets it, so the branch is never taken; if it ever were set with no filter wired, it refuses (raises) rather than silently running without the promised filtering. Until then, bwrap's namespace + no-net + read-only bind is the enforced Linux boundary.

## Resource limits

Before `os.execvp`, `sbx` applies `resource.setrlimit` on `RLIMIT_AS`, `RLIMIT_CPU`, and `RLIMIT_NPROC` to this process (limits survive exec, so the target inherits them). These are a **robustness backstop** against a runaway (fork/mem bomb), NOT the containment boundary -- the OS sandbox is.

**RLIMIT_NPROC per-UID caveat.** `RLIMIT_NPROC` counts **all** of the invoking user's processes, not just this sandbox's descendants. A small absolute cap (say 64) would spuriously fail an innocent command the instant the user's existing process count already exceeds it, so `sbx` never imposes a small absolute number: it keeps the working per-UID soft cap and only imposes a generous **headroom** when there is no ceiling at all, leaving room for the engine to fork its target. This is a robustness caveat, not a containment hole.

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
