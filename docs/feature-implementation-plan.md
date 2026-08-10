---
name: feature-implementation-plan
type: spec
status: active
title: Sandboxed CLI Runner — sandbox-run skill + sbx helper + PreToolUse gate
description: Approve-the-wrapper-not-the-command trust-boundary shift — a stdlib-only sbx sandbox helper (Seatbelt/bwrap) plus a grant-only PreToolUse gate that auto-allows only a clean, contained, single sbx invocation and prompts on everything else.
sources:
  - ClaudeCode/skills/sandbox-run/SKILL.md
  - ClaudeCode/skills/sandbox-run/scripts/sbx
  - ClaudeCode/skills/sandbox-run/scripts/selftest_probe.py
  - ClaudeCode/hooks/sbx-gate.py
  - tests/test_sbx_gate.py
  - tests/run.py
  - project-forge.yaml
  - ClaudeCode/hooks/mcp-first-guard.py
  - ClaudeCode/skills/checkpoint/SKILL.md
  - tests/test_mcp_first_guard.py
verified:
  commit: c6af014
  date: 2026-08-10
links:
  - hooks
  - skills
---

# Feature Implementation Plan: Sandboxed CLI Runner (`sandbox-run` skill + `sbx` helper + PreToolUse gate)

> Synthesized canonical plan. The lead perspective is **risk-first / security** —
> this feature *is* a security boundary, and its whole value proposition is that
> approving ONE wrapper (`sbx`) is safe because the wrapper GUARANTEES containment
> (default-deny writes + no network). Two grafts from the maintainability and
> mvp-first perspectives are folded in and marked where they override the base:
> the gate uses a **self-contained, conservative, metacharacter-REJECTING**
> classifier (it does NOT reuse the see-through parser), and the helper is built
> **pure-pieces-first, execvp-last** so no unsafe intermediate state can ever run
> a child.
>
> Every file/line reference below was verified against HEAD via `purity_call` /
> `Read` before this plan was written. Stale anchors carried in from the drafts
> were corrected (see the Verification note at the end).

---

## 0. Threat Model (lead section)

There are **two independent trust surfaces**, and a break in *either* defeats the feature:

- **Surface A — the GATE (`ClaudeCode/hooks/sbx-gate.py`).** Decides *prompt vs. auto-allow* for a Bash command. It grants NO capability by itself, but a false-allow lets a command that is **not purely an `sbx` invocation** run **without a prompt**. That is a containment escape by definition, because the un-prompted command never went through the sandbox. The gate's failure mode of concern is **false-allow**; its safe direction is *always* "emit nothing → normal permission prompt."
- **Surface B — the HELPER (`ClaudeCode/skills/sandbox-run/scripts/sbx`).** Even an honestly-approved `sbx` command must be contained by the OS sandbox. A helper bug (SBPL/argv injection, fail-open when the sandbox binary is missing, a secret readable in-scope, network reachable by default) means an approved wrapper does NOT contain — the trust the whole feature asks the user to extend is betrayed.

### Assets to protect
1. **Filesystem outside the write-scope** (default `<cwd>/.claude/tmp`, or `--write .` for the repo) — no writes may land elsewhere.
2. **Secrets** (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude`, plus the `.git/config` of **every ancestor `.git`** from `cwd` up to the filesystem root — sec-MED-2) — denied **both read and write**, even when inside an otherwise-allowed scope.
3. **The network** — unreachable unless the user explicitly passes `--net`, and `--net` **must not auto-allow** (it always prompts).
4. **Host stability** — no fork/mem bomb may take down the machine (resource limits).

### Adversary capabilities
The adversary is a **command string proposed for the Bash tool** (whether by a confused model, a prompt-injected instruction, or a malicious paste). It controls the full command text and the `sbx` argv. It does **not** control the gate/helper source or the user's `settings.json`.

### The load-bearing invariant
> `sbx-gate.py` auto-allows **iff** the command is provably a **single, clean, un-wrapped invocation of the ONE deployed `sbx` wrapper — verified by canonical-path IDENTITY, never by basename (KD-6 / C1)** — **with no shell metacharacters** AND its declared scope stays inside the project AND `--net` is absent. Under **any** doubt — a metacharacter anywhere, a parse ambiguity, an unknown field, a missing `cwd`, or a first token that does not canonicalize to the exact deployed wrapper path — it emits **empty stdout** and the normal prompt runs. Fail-safe == fail-to-prompt, never fail-to-allow.
>
> **Identity, not filename (C1 / A01 / CWE-290).** Any program merely *named* `sbx` (a planted `./sbx` in the writable `.claude/tmp`, `/tmp/x/sbx`, or a `PATH`-shadowing binary) is NOT the wrapper and grants no containment. The gate therefore canonicalizes `toks[0]` — expand `~`, resolve a relative/`/`-bearing form against `cwd`, or `shutil.which` a bare name, then `os.path.realpath` — and auto-allows ONLY when the result is byte-for-byte equal to the ONE deployed wrapper path `WRAPPER_PATH` (KD-6). A basename match is a **false-allow** and is forbidden.

### Why fail-OPEN in the cloned hook is SAFE here (must be written into `sbx-gate.py`'s module docstring)
The source hook `mcp-first-guard.py` fails **open** ("never brick Bash": `try: main() except Exception: pass; sys.exit(0)`, verified `ClaudeCode/hooks/mcp-first-guard.py:594-599`). For a *deny*-guard, fail-open is a real weakness (an error lets a blocked command through). For our **grant-only** gate the polarity flips: an error → no JSON on stdout → **no auto-allow** → the normal permission prompt runs → the command is never auto-run and never runs unsandboxed. So inheriting the exact fail-open harness is not just acceptable, it is the **correct** safe default. **This reasoning must be written verbatim into `sbx-gate.py`'s module docstring** so no future edit "fixes" it into a fail-closed brick.

### The inverted safety model — why the gate REJECTS metacharacters instead of SEEING THROUGH them (GRAFT 1, also into the docstring)
`mcp-first-guard.py` **steers and fails open**, so it must **see through** obfuscation: it peels wrappers, descends into `$( )` / backticks / `bash -c`, and folds ALL-CAPS names, because a *blocked* command hidden one layer down must still be caught. `sbx-gate.py` **authorizes and fails toward the prompt**, so it must do the OPPOSITE: it must **reject** anything it cannot trivially prove is a bare `sbx` invocation. Seeing through quotes/obfuscation is not just unnecessary here, it is *actively wrong* — teaching the gate to interpret chaining/substitution is exactly the surface an authorizing gate must refuse. Therefore the gate contains a tiny, self-contained, **conservative** classifier that blanket-rejects a metacharacter blocklist; it **imports nothing** from `mcp-first-guard.py` and reuses **none** of `split_top`/`_tokens`/`primary`/`substitutions`/`_strip_heredocs`. A legitimate command with a metacharacter inside a quoted argument SAFELY falls through to a normal prompt — under-allow is always safe. This too goes in the docstring so nobody "improves" the gate into importing the see-through parser.

---

## 1. Requirements Summary

### Functional Requirements
- **[FR-1]** Ship a skill at `ClaudeCode/skills/sandbox-run/SKILL.md` plus a bundled helper `ClaudeCode/skills/sandbox-run/scripts/sbx` (Python 3, stdlib-only).
- **[FR-2]** `sbx` interface (FINAL): `sbx [--write DIR]... [--net] [--ro] -- <cmd> [args...]`. The bare `--` separates `sbx` flags from the target argv; everything after `--` is passed to `os.execvp` as **argv list elements**, never through a shell.
- **[FR-3]** Default write-scope is **STRICT** `<cwd>/.claude/tmp` (relative to cwd, created if missing via `os.makedirs(..., exist_ok=True)` — never a shell `mkdir`). `--write DIR` (repeatable) adds a scope; `--write .` opts into the repo. `--ro` denies **all** writes including the scratch default.
- **[FR-4]** Reads are permissive **MINUS secrets** (see [FR-6]). Network is denied unless `--net`.
- **[FR-5]** Ship a PreToolUse gate hook `ClaudeCode/hooks/sbx-gate.py` that auto-allows only a clean single `sbx` invocation (Threat Model invariant) and stays silent otherwise. It copies ONLY the ~10-line JSON envelope + `main()` I/O skeleton from `mcp-first-guard.py`; it imports nothing and shares no parser (GRAFT 1 / KD-1).
- **[FR-6]** Secrets (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude`, plus the `.git/config` of **every ancestor `.git`** from `cwd` up to the filesystem root — sec-MED-2) are denied **read AND write** even when inside an allowed scope. They are produced ONCE, by a single **pure, import-side-effect-free function `secret_paths(cwd) -> tuple[str, ...]`** in `sbx` — a FUNCTION, not a module-level constant, because the `.git/config` entries are cwd-derived and a module-level tuple evaluated at import has no `cwd` (M-A); it is still single-source and still runs no I/O at import (KD-2, M3).
- **[FR-7]** Auto-allow policy in the gate: writes confined to the current project (scratch or `--write .` / a `--write` resolving inside the project) → **AUTO-ALLOW**; `--net` OR any `--write` resolving **outside** the project OR any metacharacter → **PROMPT** (empty stdout).
- **[FR-8]** Ship `tests/test_sbx_gate.py` (mandatory, CI) mirroring `tests/test_mcp_first_guard.py`, registered in `tests/run.py`'s `SUITES` table, and reachable via a new forge `sbx_gate` test target.
- **[FR-9]** Fix the stale `docs/subsystems/hooks.md` roster (it omits `mcp-first-guard.py` today) and add `sbx-gate.py`; add a `p:sandbox-run` row to `docs/subsystems/skills.md`; regenerate/audit `docs/INDEX.md`; add an ADR. **Do NOT edit `docs/subsystems/scripts.md`** (GRAFT 3).

### Non-Functional Requirements
- **[NFR-1] Fail-closed helper / fail-safe gate.** Unknown platform or missing sandbox binary → helper exits non-zero, **never** runs the command unsandboxed. Gate under any doubt → empty stdout (prompt).
- **[NFR-2] Platform parity.** macOS via `sandbox-exec -p <inline SBPL>`; Linux FULL via `bwrap`. Any other platform, or a missing binary → fail-closed. The backend is chosen from a `BACKENDS` registry keyed on a `sys.platform` prefix, with a fail-closed default (KD-3).
- **[NFR-3] Zero-dependency.** Python 3 stdlib only (house style, verified `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:1-16`, `ClaudeCode/skills/wiki/scripts/reindex.py:1-27`).
- **[NFR-4] Injection-proof profile construction.** No `--write` value may inject into the SBPL profile string or the bwrap argv. `realpath` every scope; reject paths containing quotes/parens/backslash/newline before they reach the profile/argv.
- **[NFR-5] Bounded blast radius.** `resource.setrlimit` on `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NPROC` in-process, **before** `execvp` (limits survive exec — KD-4).
- **[NFR-6] Exit-code passthrough.** The sandboxed command's exit status is the helper's exit status (natural via `os.execvp` replacing the process image).
- **[NFR-7] Test invariants preserved.** Gate always exits 0; AUTOALLOW == parsed `permissionDecision:"allow"`; PROMPT == literally no stdout; stderr empty (mirrors `tests/test_mcp_first_guard.py:1002-1005`).
- **[NFR-8] Drift resistance.** The security contract lives in as few places as possible and each strand is pinned by a test so a future edit cannot silently loosen it: the secret deny set once, as the pure `secret_paths(cwd)` function in the helper (M-A); the metacharacter blocklist once in the gate; the resource limits once in the helper; **and — this is the H-A addition — the shared path-resolution contract (`resolve_scope`, `project_root`, AND the separator-safe containment test of sec-MED-1) which, because the gate imports nothing and is deployed in a different tree from the helper (P8), is DELIBERATELY DUPLICATED as two physical copies rather than one function, and is therefore pinned by the mandatory cross-file PARITY TEST of Step 8 that asserts the two copies produce byte-identical output.** The resolution rule is now an explicitly drift-pinned contract, not merely SECRET_PATHS/metachars/rlimits — a one-sided edit to either copy would silently reintroduce the H1 gate/helper divergence, i.e. a containment escape, which the parity test is the enforcement against.
- **[NFR-9] Localized platform extension.** Adding a third OS/backend later touches exactly one builder function + one `BACKENDS` entry, nothing else. seccomp stays an OFF-by-default `if` branch inside `_bwrap_argv` (locked decision 8) — not a new code path, and NOT a class hierarchy (`ARCHITECTURE.md` forbids speculative abstraction).

### Success Criteria
- **[SC-1]** Every adversarial gate case in the Risk Mitigation matrix (§5) that represents a containment escape resolves to **PROMPT (empty stdout)**, proven by `tests/test_sbx_gate.py`.
- **[SC-2]** The live self-test passes in ZERO iterations: scratch write OK, out-of-scope write BLOCKED, network BLOCKED, secret-READ BLOCKED under `--write .` (probe d, M2) — plus a faked-missing-sandbox run that fail-closes.
- **[SC-3]** A secret file (e.g. `~/.ssh/id_rsa`) is unreadable inside the sandbox even with `--write .`.
- **[SC-4]** `forge test all` passes with the new `sbx_gate` suite registered and its declared case count matching the run; no existing suite regresses (e.g. `mcp_first_guard`, declared 335, `tests/run.py:205-206`). `reindex.py --check` passes after the doc updates.

### Assumptions
- The PreToolUse payload carries a **`cwd`** field the gate reads to resolve `--write` scopes and the project root. <!-- UNVERIFIED: the existing hook never reads `cwd` (it consumes only `tool_name` and `tool_input.command`, mcp-first-guard.py:561-564); `cwd` is a documented Claude Code PreToolUse payload field but is exercised nowhere in this repo. The gate MUST fail-safe (emit nothing → prompt) if `cwd` is absent or unparseable, so this assumption can never cause a false-allow. -->
- `permissionDecision:"allow"` is a valid PreToolUse decision (same `hookSpecificOutput` envelope the deny path uses at `mcp-first-guard.py:585-591`). <!-- GAP: the repo's own hook only ever emits "deny"|empty; "allow" is used by Claude Code but has no in-repo precedent. The Step-8 gate suite pins the exact envelope so a schema mismatch fails loudly rather than silently. -->
- macOS ships a functional `sandbox-exec` (Apple marks it deprecated but it is present and enforcing on current macOS). If absent/broken → the fail-closed path applies.
- The user registers the hook themselves in `~/.claude/settings.json` (locked decision c). The plugin ships **no** `hooks` block (verified: `ClaudeCode/.claude-plugin/plugin.json` has no `hooks` key; 10 lines, keys `$schema,name,displayName,version,description,author,repository,license`); there is no installer and no self-check.

### Out of Scope (with risk notes — do NOT widen)
- **No Docker/VM/nsjail/auto-runner, no `bypassPermissions` mode.** *Risk note:* containers would give stronger isolation, but the decision is Seatbelt/bwrap; adding one now is scope creep that also enlarges the attack surface we must audit.
- **No auto-registration / install helper / hook self-check** (locked decision c). *Risk note:* an un-registered hook means commands simply prompt as normal — a safe degradation, not an escape. The residual risk is a *usability* gap (no protection until the user wires it up), not a *security* hole; documenting it is the mitigation.
- **seccomp syscall filtering on Linux** — an OFF-by-default `if` branch inside `_bwrap_argv`, documented, not enabled (locked decision 8). *Risk note:* without seccomp a contained process can still attempt any syscall; bwrap's namespace + no-net + read-only bind is the enforced boundary. A future seccomp layer would harden syscall surface but is explicitly deferred.
- **Gate-side re-implementation of the full sandbox policy.** The gate only classifies prompt-vs-allow; it does NOT itself sandbox. *Risk note:* keeping the gate thin avoids two divergent policy engines; the helper is the single enforcement point.
- **Signature/allowlist of *which* programs may run under `sbx`.** Any program may run; containment (not program identity) is the boundary. *Risk note:* deliberate — an allowlist would recreate the ever-growing per-command list this feature replaces.
- **Windows / any non-macOS-non-Linux platform, configurable secret lists, policy files, telemetry, profile caching, an `sbx --help` epic.** *Risk note:* handled by fail-closed ([NFR-1]); polish, not on the path to the two guarantees. Any of these arriving later must not weaken a security invariant.
- **`macOS sandbox-exec` → Endpoint Security migration.** Documented as a known risk only.

---

## 2. Architecture Analysis

### Affected Subsystems
- **Hooks** (`ClaudeCode/hooks/`) — new `sbx-gate.py`. Same PreToolUse(Bash) contract as `mcp-first-guard.py`, **opposite polarity** (grant, not deny) and **opposite parser stance** (reject metacharacters, not see through them). Sibling to `attention-reminder.py` and `mcp-first-guard.py`; each hook is a **self-contained, stdlib-only single file** — there is NO shared hook library today (verified: `attention-reminder.py:1-29` imports stdlib only and shares nothing with `mcp-first-guard.py`). Key reference file: `ClaudeCode/hooks/mcp-first-guard.py` (source of the I/O envelope only).
- **Skills** (`ClaudeCode/skills/`) — new `sandbox-run/` extended skill (`SKILL.md` + `scripts/sbx` + `scripts/selftest_probe.py`), the exact shape `docs/subsystems/skills.md:30-34` calls an "Extended skill" (precedent: `static-linking/`, `checkpoint/`). Deployed to `~/.claude/skills/p/skills/sandbox-run/` (the `p:` comes from `ClaudeCode/.claude-plugin/plugin.json:3` `name: p`, convention `ClaudeCode/ARCHITECTURE.md:23,37`).
- **Tests** (`tests/`) — new `tests/test_sbx_gate.py` mirroring `tests/test_mcp_first_guard.py`; one new `SUITES` row + a `run_sbx_gate` wrapper in `tests/run.py:202-226`. Optionally (flagged) `tests/test_sbx.py` (offline pure-builder assertions).
- **Build system** (`project-forge.yaml`) — a new `sbx_gate` **test** target mirroring the existing `mcp_first_guard` target (`project-forge.yaml:51-57`). Current inventory verified: 1 build target `syntax`, 10 test targets, 1 clean target `pycache`.
- **Docs wiki** (`docs/`) — `docs/subsystems/hooks.md` roster de-staled + both hooks added; `docs/subsystems/skills.md` gains a `p:sandbox-run` row; `docs/INDEX.md` regenerated/audited; an ADR at `docs/adr/0005-approve-the-wrapper-not-the-command.md` records the trust-boundary rationale. `docs/subsystems/scripts.md` is **intentionally untouched** (GRAFT 3).

### Integration Points
- **Claude Code → gate:** PreToolUse fires on tool `Bash`; Claude sends JSON on stdin containing `tool_name`, `tool_input.command`, and (assumed) `cwd`. The gate parses it exactly as `mcp-first-guard.py:561-566` does, self-gates on `tool_name == "Bash"` (defense in depth, `:562-563`), and replies on stdout.
- **Gate → Claude Code:** stdout JSON `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":<str>}}` to auto-allow; **empty stdout** to let the normal prompt run (envelope shape verified `mcp-first-guard.py:585-591` for the deny form; ALLOW-by-silence at `:569-570`). One-shot request/response per Bash call; the process always exits 0.
- **User → helper:** `sbx ... -- cmd args` on the CLI (or emitted by the skill). The helper builds an OS sandbox invocation and `os.execvp`s into it.
- **Helper → OS:** `os.execvp("sandbox-exec", ["sandbox-exec","-p",<profile>, *argv])` (macOS) or `os.execvp("bwrap", ["bwrap", *<bwrap flags>, *argv])` (Linux). The target `cmd`/`args` travel as **argv list elements**, never concatenated into a shell string, so they cannot inject into the profile.

### Constraints
- **PreToolUse contract is rigid:** always `sys.exit(0)`; AUTOALLOW means the `allow` JSON on stdout, PROMPT means *no* stdout; the JSON shape is fixed. Breaking any of these breaks the hook silently (`mcp-first-guard.py:585-599`).
- **Two PreToolUse(Bash) hooks will coexist** (`mcp-first-guard.py` + `sbx-gate.py`). Polarities are disjoint — `mcp-first-guard.py` only ever emits `deny`|empty; `sbx-gate.py` only ever emits `allow`|empty — so ordering in `settings.json` is *asserted* safe either way. **This safety assertion rests on Claude Code's multi-hook aggregation semantics, which are NOT verified in-repo — confirm empirically at implementation time (see the §5 multi-hook caveat).** The SKILL.md registration section must say so, so a user does not fear a conflict.
- **Grant-only polarity:** the gate's ONLY positive action is `"allow"`. It must never `"deny"` — denying would brick legitimate non-`sbx` Bash. Its two outcomes are `allow` (narrow, proven) and silence (everything else).
- **Case-count discipline:** `tests/run.py`'s `SUITES` row declares an exact case count that the runner cross-checks (`tests/run.py:96-98,202-226,260-261`; drift is a hard failure at `:284-290`); the new suite must declare its count (or `None` if data-derived).
- **Bytecode-free tree:** Python must stay stdlib-only and no `.pyc` may leak; test loaders set `sys.dont_write_bytecode` (`tests/run.py:106`, `tests/test_mcp_first_guard.py:65-66`), the forge `clean pycache` target is the backstop, and forge runs under `PYTHONDONTWRITEBYTECODE=1` (`project-forge.yaml:19-20`).
- **SBPL ordering is last-match-wins:** secret-deny rules must be emitted **after** the permissive read-allow, or they will not take effect. <!-- UNVERIFIED: SBPL rule-evaluation order (last matching rule wins) cannot be confirmed from this repo (no existing SBPL); the live self-test secret-READ probe (Step 10, probe d) [SC-2]/[SC-3] is the verification gate for this — probe-verified, NOT CI-verified (M2). -->

---

## 3. Captured Information

### Existing Patterns the implementation MUST follow

**P1 — PreToolUse I/O envelope + always-exit-0 / fail-open harness — the ONLY thing the gate copies** (`ClaudeCode/hooks/mcp-first-guard.py:560-599`):
```python
def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return                       # defense-in-depth self-gate (:562-563)
    cmd = (data.get("tool_input") or {}).get("command") or ""   # :564
    if not cmd.strip():
        return                       # :565-566
    # ... decide ...
if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                          # fail open — never brick Bash (:594-599)
    sys.exit(0)
```
The gate keeps this skeleton; its ONLY positive emission is an `allow` JSON, and every non-clean path is a bare `return` (silence == prompt).

**P2 — the ALLOW / deny JSON shape** (`mcp-first-guard.py:585-591`): emit `hookSpecificOutput` with `hookEventName:"PreToolUse"`. For the gate, `permissionDecision:"allow"` with a short human `permissionDecisionReason` (e.g. `"clean single sbx invocation, scope inside project, no --net"`).

**P3 — the gate's self-contained CONSERVATIVE classifier (GRAFT 1 / KD-1 — this OVERRIDES the see-through-parser approach).**
The gate does **NOT** import `mcp-first-guard.py` and does **NOT** reuse `split_top` (`:198-226`), `_tokens` (`:229-254`), `primary` (`:288-333`), `substitutions` (`:487-532`), or `_strip_heredocs`/`HEREDOC_RE` (`:431-451`/`:428`). Those functions exist to *see through* obfuscation for a deny-guard; an authorizing gate must instead **refuse** it. The classifier is roughly:
```python
# ~50 lines, self-contained. NO import from mcp-first-guard.py.
import os, shutil
METACHARS = set(";&|$`()<>{}\n'\"\\*?")   # the SAFETY FLOOR (note: no "~" here — see L1)
# The ONE known-good wrapper identity (KD-6 / C1). Resolved ONCE, at import.
WRAPPER_PATH = os.path.realpath(
    os.path.expanduser("~/.claude/skills/p/skills/sandbox-run/scripts/sbx"))
def is_clean_sbx(cmd: str, cwd: str | None) -> tuple[bool, str]:
    """Return (auto_allow, reason). auto_allow=True ONLY when provably safe."""
    if cwd is None:
        return (False, "")
    s = cmd.strip()
    if not s or s[0] == "#":                  # comment line -> reject
        return (False, "")                    # NOTE: leading "~" is NOT rejected here
                                              #   (H2) — the documented invocation form
                                              #   starts with "~/.claude/..."; "~"-as-a-
                                              #   write-target is caught below by KD-7.
    if any(ch in METACHARS for ch in s):      # ANY metacharacter anywhere -> reject
        return (False, "")
    toks = s.split()                          # plain whitespace split; no quote logic
    # IDENTITY, not basename (KD-6 / C1 / CWE-290): canonicalize toks[0] and compare
    # to the ONE deployed wrapper path. A "/"- or "~"-bearing form is resolved via the
    # SHARED resolve_scope() rule (KD-7); a bare name via shutil.which(PATH) + realpath.
    if "/" in toks[0] or toks[0].startswith("~"):
        cand = resolve_scope(toks[0], cwd)                    # KD-7 shared rule
    else:
        w = shutil.which(toks[0]); cand = os.path.realpath(w) if w else None
    if cand != WRAPPER_PATH:                  # exact identity match REQUIRED
        return (False, "")
    # parse sbx's own flags up to '--', MATCHING argparse's grammar (M-B): recognize
    # --net, --ro, --write DIR AND the equals-form --write=DIR (split on first '=', like
    # argparse). Refuse on --net. Every --write must be separator-safe-contained (KD-8):
    #   commonpath([resolve_scope(DIR, cwd), project_root(cwd)]) == project_root(cwd)
    # (the SAME KD-7/KD-8 rules the helper uses -- H1). ANY unrecognized token before
    # '--' -- an unknown flag OR an unhandled equals-form -- is a HARD PROMPT (bare
    # return), NEVER skipped: a `== "--write"`-only scan would silently pass --write=/etc
    # while the helper's argparse opens /etc (M-B false-allow).
    ...
    return (True, "clean single sbx invocation, scope inside project, no --net")
```
Blanket metacharacter rejection is the safety floor: `sbx foo ; rm -rf ~`, `sbx $(id)`, `sbx x | sh`, redirections, subshells, wrappers hiding behind `env`/`sudo`/`bash -c`, and leading `VAR=val` assignments are all rejected because each contains a blocklisted character. **Wrapper IDENTITY (KD-6), not filename, gates the first token**: a program merely *named* `sbx` (planted `./sbx`, `/tmp/x/sbx`, a `PATH`-shadowing binary, or an ALL-CAPS `SBX`) canonicalizes to something `!= WRAPPER_PATH` and is rejected. **A legitimate command carrying a metacharacter inside a quoted argument (e.g. `sbx -- echo ";"`) also prompts — that is the safe outcome, not a bug** (GRAFT 1 explicitly drops the base draft's "must still AUTOALLOW `echo ';'`" requirement). Under-allow is always safe.

> **Invocation contract (H2 — stated ONCE, mirrored in SKILL.md Step 11).** The gate's accept-set and the SKILL.md-documented invocation form MUST intersect. The single coherent contract: **the wrapper is invoked, and auto-allowed, by its absolute deployed path** `~/.claude/skills/p/skills/sandbox-run/scripts/sbx`. The gate's KD-6 identity canonicalization expands `~`, resolves relative/`PATH` forms, and `realpath`s uniformly, so the documented `~`-leading path resolves to `WRAPPER_PATH` and is accepted — while `~`-as-a-write-target stays rejected by the KD-7/KD-8 scope-containment check. There is NO blanket leading-`~` reject (that older rule made the documented form unreachable and is removed).

**P3-drift — why COPY, not IMPORT / not a shared `_lib` (KD-1, must be a top-of-file comment in `sbx-gate.py`).**
- *Copy the envelope (chosen).* Risk: if Claude Code changes the `hookSpecificOutput` schema, both hooks change. Mitigation: the schema is ~10 lines and both `test_sbx_gate.py` and `test_mcp_first_guard.py` pin it — a schema change fails both suites loudly. The parsers do NOT drift because they are *supposed to differ*; there is no shared logic to keep in sync.
- *Shared `ClaudeCode/hooks/_lib` (rejected).* It would (a) break the "each hook is one self-contained file you point `settings.json` at" deploy story — a user copying one hook path without the sibling lib gets an `ImportError`, i.e. a bricked Bash tool; (b) create a false coupling inviting a future "improve the parser once, benefit both" change that is *dangerous* precisely because the two gates need opposite behavior; (c) be the speculative abstraction `ARCHITECTURE.md` warns against, for ~10 trivial lines. The skill-local `_wikilib` precedent (`reindex.py:25-26`) is NOT applicable: it shares *within one skill* and both sharers want the *same* behavior — neither condition holds here.

**P4 — Python house style for the helper** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:1-16`, `ClaudeCode/skills/wiki/scripts/reindex.py:1-27`): `#!/usr/bin/env python3`, module docstring with a `Usage:` line, `argparse`, stdlib-only, non-zero exit on error.

**P5 — bundled-script deployed path + SKILL.md body shape** (`ClaudeCode/skills/checkpoint/SKILL.md:52-64,66-95`): a skill references its bundled script by its **absolute deployed path** `~/.claude/skills/p/skills/<name>/scripts/<script>`. The SKILL.md uses a `## Usage` fenced block and a `### Step 1 — Parse arguments` prose section — there is **no** `$ARGUMENTS` substitution in this repo.

**P6 — skill frontmatter** (`ClaudeCode/ARCHITECTURE.md:28-37`, live example `checkpoint/SKILL.md:1-5`): required keys `name` (MUST equal the directory name, never with the `p:` prefix — `ARCHITECTURE.md:37`) and `description`; `model` optional.

**P7 — the ONLY blessed sharing pattern is skill-local, and does not apply here** (`reindex.py:25-26`, sharing `_wikilib.py` between two scripts inside one skill). Documented so a future engineer does not cite it to justify a cross-hook library (see P3-drift).

**P8 — no shared hook library today** (`attention-reminder.py:1-29` imports only stdlib and shares nothing with `mcp-first-guard.py`). This is a load-bearing house pattern, not an accident: it is what makes a hook path in `settings.json` self-sufficient.

**P9 — test harness contract (mirror target).**
- HOOK path via `H.repo_path` (`tests/test_mcp_first_guard.py:71`).
- Payload/case builder (`:76-81`):
```python
def case(group, name, cmd, expect, must=(), must_not=(), note=""):
    return {"group": group, "name": name, "expect": expect,
            "payload": json.dumps({"tool_name":"Bash","tool_input":{"command":cmd}}),
            "must": list(must), "must_not": list(must_not), "note": note}
```
  For the gate suite, extend `case()` to optionally inject a `cwd` field into the payload (needed to test scope resolution); a `raw(...)` builder for malformed payloads exists at `:84-86`.
- Classifier (`:897-914`): the source returns ALLOW on empty stdout and DENY on parsed `permissionDecision=="deny"`. The gate suite **inverts** this: empty stdout == **PROMPT** (the *safe* outcome), parsed `permissionDecision=="allow"` == **AUTOALLOW**; a malformed/other shape is a test failure (`BADJSON`/`BADSHAPE`).
- Invariants (`:1002-1005`): every case asserts `rc == 0` and `stderr` empty. Preserve both.
- Suite driver (`:992+`): expose `run(opts=None) -> H.Suite`, iterate `CASES`, run the hook as a subprocess per case (`run_hook`, `:893-894`). The `only(*names)` exact-reason assertion (`:92-99`) adapts so an AUTOALLOW case can assert its reason string. Whitebox import is gated + bytecode-free (`:65-66`).

**P10 — manual settings.json registration** (`ClaudeCode/README.md:356-379`): NOTE the trap — the README example uses **camelCase** `"postToolUse"` (`:359`), but the real Claude Code keys are **PascalCase**. The SKILL.md registration block MUST use PascalCase `PreToolUse`, matcher `"Bash"`:
```json
{ "hooks": { "PreToolUse": [
  { "matcher": "Bash", "command": "/abs/path/to/ClaudeCode/hooks/sbx-gate.py" }
] } }
```

### Shared resolution rules (a SHARED CONTRACT — deliberately DUPLICATED across the gate/helper boundary, applied IDENTICALLY by both)

These rules exist in exactly one *conceptual* place each, but NOT in one *physical*
place, and the plan states this plainly (H-A): the gate (`sbx-gate.py`) imports
NOTHING and is deployed in a different tree from the helper — that is the P8
self-contained-hook rule, a hook path in `settings.json` must be self-sufficient — so
`resolve_scope`, `project_root`, and the separator-safe containment test are **two
physically duplicated copies** (one in the gate, one in the helper) that MUST produce
byte-for-byte identical output. They are a SHARED CONTRACT, **not a single shared
function**. A one-sided edit to either copy silently reintroduces the gate/helper
divergence, which is a containment escape (C1, H1, M1, H-A); the mandatory cross-file
PARITY TEST of Step 8 is the enforcement that this duplication cannot drift, and NFR-8
now lists the resolution rule among the drift-pinned contracts. State each rule once
here; every step below references them by number.

**KD-6 — wrapper identity (C1 / A01 / CWE-290).** `WRAPPER_PATH = os.path.realpath(os.path.expanduser("~/.claude/skills/p/skills/sandbox-run/scripts/sbx"))`, resolved once at gate import. The gate authorizes the first token ONLY when its canonicalized form equals `WRAPPER_PATH` exactly — never by `os.path.basename(...) == "sbx"`. Canonicalization of `toks[0]`: if it contains `/` or starts with `~`, apply KD-7 `resolve_scope(toks[0], cwd)`; otherwise `shutil.which(toks[0])` then `os.path.realpath`. No match / unresolvable → reject (empty stdout).

**KD-7 — one path-resolution rule for `--write` and for the wrapper token (H1 / A04 / CWE-706).** ONE logical rule, physically DUPLICATED as an identical function in BOTH the gate (classify) and the helper (`canonicalize`) — the gate cannot import the helper (P8) — applied with the SAME `base` (always `cwd`) on both sides:
```python
def resolve_scope(dir_arg: str, base: str) -> str:
    # expanduser FIRST (so "~" -> $HOME), then join against base (join drops base
    # when the expanded arg is absolute), then realpath. IDENTICAL on both sides.
    return os.path.realpath(os.path.join(base, os.path.expanduser(dir_arg)))
```
This closes the H1 divergence: previously the helper did `realpath(expanduser(dir))` while the gate did `realpath(join(cwd, dir))` with NO `expanduser`, so `sbx --write ~` looked like `<cwd>/~` (inside project → false AUTO-ALLOW) to the gate but opened all of `$HOME` in the helper. With KD-7 both sides map `~` → `realpath($HOME)`, which the gate's KD-8 containment check then classifies as outside the project → PROMPT. The helper additionally REJECTS (fail-closed) any raw-or-resolved path containing `"`, `'`, `(`, `)`, `\`, or newline before it reaches the profile/argv (R14); the gate rejects those upstream via `METACHARS`.

**KD-8 — one project-root definition + a SEPARATOR-SAFE containment test (M1 / sec-MED-1 / sec-MED-2).** `project_root(start)` walks up from `start` (which is always `cwd`) to the nearest ancestor directory containing a `.git` entry, and falls back to `start` itself if none is found. It is physically duplicated in gate and helper (see the KD-7 duplication note) and must stay byte-identical (parity test, Step 8).

*Containment (sec-MED-1 / A04).* "Is this `--write` inside the project?" MUST use a **separator-safe** test, NOT a bare `str.startswith` — a raw prefix test false-allows a sibling: with `project_root == /x/repo`, `--write ../repo-evil` resolves to `/x/repo-evil` and `"/x/repo-evil".startswith("/x/repo")` is `True`, a false AUTO-ALLOW of an out-of-project write. The rule is therefore `os.path.commonpath([resolve_scope(DIR, cwd), project_root(cwd)]) == project_root(cwd)` (equivalently `path == root` OR `path.startswith(root + os.sep)`, root itself allowed), which respects the separator boundary. This SAME separator-safe test is applied by BOTH the gate (scope-containment) and the helper (`inside_project`); it is part of the shared, duplicated resolution contract and is covered by the Step 8 parity test.

*The `.git/config` secret is NO LONGER anchored to only `project_root(cwd)` (sec-MED-2 / secret-read leak).* A decoy `cwd/.git` — plantable by a prior auto-allowed `sbx --write . -- ...` run — would relocate the nearest-ancestor `project_root` to the decoy, so a single `<project_root>/.git/config` deny would point at the decoy and the REAL repo-root `.git/config` (a higher ancestor, which can hold git remote credentials) would drop out of the deny set and become readable in-sandbox. The fix lives in `secret_paths(cwd)` (§3 Type Definitions): it denies the `.git/config` of **every `.git` directory encountered walking up from `cwd` to the filesystem root**, so a planted nested `.git` cannot un-protect an ancestor. `cwd` alone is NOT the project root — a Bash tool `cwd` may be any subdirectory of the repo.

### Type Definitions / Key Constants (to be introduced, single-source)
- `secret_paths(cwd) -> tuple[str, ...]` — **a single pure FUNCTION in `sbx`, the ONE definition** of the deny set (M-A). It is a FUNCTION, not a module-level constant, because part of the set is cwd-derived and a module-level tuple evaluated at import has no `cwd` (resolving the M-A contradiction between "import side-effect-free / one module-level constant" and "cwd-derived secrets"); it is nonetheless import-side-effect-free (M3 — it touches the filesystem only when CALLED at runtime, and only to read `.git` existence, never to mutate) and single-source. It returns: the home-anchored entries `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude` (expand `~` via `os.path.expanduser`, independent of `cwd`); PLUS, for `.git/config` protection, `os.path.join(d, ".git", "config")` for **every ancestor `d` of `cwd` (inclusive) whose `<d>/.git` exists**, walking from `cwd` up to the filesystem root (sec-MED-2) — NOT merely the nearest-ancestor `project_root(cwd)` one — so a planted nested `.git` cannot un-protect the real repo-root `.git/config`. Each backend *translates* the returned tuple to its own deny syntax; the policy is single-source, the expression is per-backend (KD-2).
- `BACKENDS: dict[str, Callable[[Scope], list[str]]]` — the platform registry `{"darwin": _seatbelt_argv, "linux": _bwrap_argv}`, keyed on a `sys.platform` prefix, `.get(...)` with a **fail-closed default** that exits non-zero (KD-3). No class hierarchy; two functions + a dict.
- `Scope` (tiny dataclass / namedtuple) — carries `writes: list[str]` (realpath-canonicalized, injection-checked), `net: bool`, `ro: bool`, `argv: list[str]` (target command after `--`), and the resolved `secret_paths`. Keeps each builder a **pure `Scope -> argv`** function so Step 9's offline suite can assert them.
- Gate decision: `permissionDecision ∈ {"allow"}` (never `"deny"`), or empty stdout.

### Build System
- `project-forge.yaml` (root). Verified inventory: build target `syntax` (`:22-28`), 10 `test` targets (incl. `mcp_first_guard` at `:51-57`), clean target `pycache` (`:118-124`). Forge runs under `PYTHONDONTWRITEBYTECODE:"1"` (`:19-20`).
- The `syntax` target compiles every `*.py` under `Scripts`, `tests`, `ClaudeCode/hooks` in memory (`:26`). **Correction to the drafts:** it therefore compile-checks `sbx-gate.py`, `tests/test_sbx_gate.py`, and `tests/test_sbx.py` automatically, but it does **NOT** cover the `sbx` helper — the helper lives under `ClaudeCode/skills/` (not swept) AND has no `.py` extension (not matched by the `*.py` glob). The helper is exercised only by the offline/live suites, not by `syntax`.
- Suite registration (`tests/run.py:96-98`): "Adding a suite takes three lines" — write `run(opts) -> Suite`, add a `run_<name>` wrapper (mirror `run_mcp_first_guard` at `:133-134`), add one `SUITES` tuple with a declared count that is cross-checked (`:260-261`; drift is a hard failure `:284-290`).

---

## 4. Alternative Approaches

### Selected: PreToolUse **grant-only** gate + stdlib `sbx` helper over Seatbelt/bwrap
**Rationale:** shifts the trust boundary from an ever-growing per-command allowlist to a single audited wrapper that *enforces* containment in the OS kernel. The gate provides the "auto-run without a prompt when contained" ergonomics; the helper provides the actual safety, so the gate can be bypassable (locked decision c) without weakening the guarantee. Reuses the repo's proven PreToolUse envelope, subprocess-driven test model, and skill-bundled-script conventions — maximal familiarity for the next engineer.
**Trade-offs:** two PreToolUse(Bash) hooks coexist (disjoint polarities); registration is manual; the helper must be correct per-OS; macOS `sandbox-exec` is deprecated (still functional); no seccomp by default on Linux. All mitigated (documented registration, per-backend tests + fail-closed default, seccomp deferred).

### Rejected: bare allowlist rule (`permissions.allow: ["Bash(sbx *)"]`)
**Reason:** trivially bypassable — `sbx foo ; rm -rf ~` matches the prefix yet chains an unsandboxed `rm`, and a static rule cannot inspect `--net`/`--write` scope. The gate's whole reason to exist is to reject exactly this (locked decision c).

### Rejected: a Bash helper instead of Python
**Reason:** building an SBPL profile / bwrap argv in shell invites quoting and injection bugs (word-splitting, `$IFS`, glob expansion) — exactly the bug class `mcp-first-guard.py:1-48` catalogues. Python with `os.execvp` (argv list, no shell), explicit `realpath`, and character-validation is far easier to make injection-proof (NFR-4), and the pure argv builders are testable offline.

### Rejected: a separate `/sandbox` slash-command replacing normal permissions
**Reason:** complementary, not a substitute. The value here is *auto-run without a prompt when contained*; a manual slash command reintroduces friction and does not integrate with PreToolUse auto-allow (FR-5 would be unmet).

### Rejected: nsjail (Linux)
**Reason:** `bwrap` is lighter, already common, needs no setuid helper, and covers our needs (`--ro-bind / /`, `--bind <scope>`, `--unshare-net`, `--die-with-parent`). nsjail's extra features (cgroups, seccomp presets) are the "optional extra" we explicitly deferred (locked decision 8), not a baseline requirement.

---

## 5. Implementation Strategy

### Overview
Build the helper **pure-pieces-first, execvp-last** (GRAFT 5): argument model + `--dry-run`, then fail-closed platform/binary detection, then the pure injection/secret/realpath guard layer, then the two pure profile/argv builders, and only then the single `setrlimit`+`execvp` wiring — so at the exact moment `sbx` first runs a child, every containment guarantee and every guard is already implemented and tested. The gate is a small, self-contained, **metacharacter-REJECTING** classifier that copies only the ~10-line I/O envelope from `mcp-first-guard.py` and imports nothing. Pin the gate's contract with a mirrored subprocess suite; pin the helper's deterministic containment surface with an offline pure-builder suite; keep the live sandbox self-test (scratch/write/net + mandatory secret-READ probe d) an on-demand skill self-test. Finish by fixing the stale hooks doc, adding the skill row, auditing the index, and recording the ADR.

### Key Design Decisions
- **[KD-1] The gate shares NOTHING with `mcp-first-guard.py` beyond the ~10-line JSON envelope — by deliberate copy, not import.** It REJECTS metacharacters rather than seeing through them (inverted safety model). See §0 and P3/P3-drift. This is the single most important deviation from the source hook and from the base draft.
- **[KD-2] The security contract has exactly ONE source per concern.** The secret deny set is one pure FUNCTION `secret_paths(cwd)` consumed by both backends (a function, not a module-level constant, because the `.git/config` entries are cwd-derived — M-A); the metacharacter blocklist lives once in the gate; the resource limits live once in the helper; and the shared path-resolution rules (`resolve_scope`, `project_root`, the separator-safe containment test) are ONE logical contract that — because the gate imports nothing (P8) — is physically DUPLICATED in gate and helper and pinned against drift by the Step 8 parity test (H-A). Each backend *translates* the shared contract to its own syntax (Seatbelt `(deny file* ...)` vs bwrap `--tmpfs`/bind-over). Policy single-source, expression per-backend (NFR-4/NFR-8).
- **[KD-3] Platform extension seam = registry of pure builder functions.** `BACKENDS = {"darwin": _seatbelt_argv, "linux": _bwrap_argv}` selected by a `sys.platform` prefix, `.get(...)` → fail-closed default that exits non-zero (NFR-1). Each builder is pure `Scope -> argv`. Adding a platform later = one function + one dict entry. seccomp is an OFF-by-default `if scope.seccomp:` branch *inside* `_bwrap_argv` (locked decision 8), not a new path. NO class hierarchy (ARCHITECTURE.md forbids speculative abstraction).
- **[KD-4] `setrlimit` runs in-process BEFORE `os.execvp`.** `os.execvp` replaces the process image; rlimits set with `resource.setrlimit` are inherited across exec. The helper sets RLIMIT_AS/CPU/NPROC, *then* execs. This ordering is a subtle correctness point — pin it in a code comment and in the helper test.
- **[KD-5] Gate and helper are independent; neither trusts the other.** The gate's metacharacter rejection is convenience-side (decide auto-allow); the helper's `realpath` + injection rejection + fail-closed dispatch is safety-side. The SKILL.md must state this so a future engineer does not "simplify" by collapsing them.
- **[KD-6] The gate authorizes wrapper IDENTITY, never basename (C1 / A01 / CWE-290).** Defined in §3 "Shared resolution rules": auto-allow requires `toks[0]` to canonicalize (expand `~`, resolve relative/`PATH`, `realpath`) to the exact deployed `WRAPPER_PATH`. A basename compare is a false-allow and is forbidden. This also fixes the H2 self-contradiction: the documented `~`-leading absolute-path invocation now resolves to `WRAPPER_PATH` and is the ONE accepted form.
- **[KD-7] One `--write`/wrapper path-resolution rule, applied identically by gate and helper (H1 / A04 / CWE-706).** Defined in §3: `resolve_scope(dir, base) = realpath(join(base, expanduser(dir)))` with `base == cwd` on both sides. Eliminates the `expanduser` divergence that let `sbx --write ~` false-allow.
- **[KD-8] One project-root definition, computed identically by gate and helper (M1).** Defined in §3: `project_root(cwd)` = nearest `.git`-bearing ancestor of `cwd`, else `cwd`. Used by the gate's scope-containment check AND by the helper's `inside_project` containment classification. It does NOT anchor the `.git/config` secret deny — after sec-MED-2 that lives in `secret_paths(cwd)`'s ancestor-walk (every `.git` from `cwd` up to the filesystem root), not in `project_root`. `cwd` is NOT assumed to be the repo root.
- **Silence is the safe default everywhere in the gate.** No `deny` is ever emitted. Any parse error, malformed payload, missing `cwd`, metacharacter, or unrecognised flag → bare `return`.
- **argv, never a shell.** `os.execvp` with a list means target `cmd`/`args` cannot inject into the profile. Only `--write` scope strings are embedded, and those are `realpath`'d and character-validated.
- **Inline SBPL via `sandbox-exec -p`** (no temp `.sb` file) — fewer artifacts, no file-based injection vector.

### Risk Mitigation — bypass → defense → proving test
Every row's "proving test" is a case in `tests/test_sbx_gate.py` (gate rows) or the offline `tests/test_sbx.py` / the live self-test (helper rows). After the GRAFT-1 parser change, **R1–R10 mostly collapse into one defense — "the raw command contains a blocklisted metacharacter (or fails the bare-`sbx`-first-token test) → reject"** — but each row is kept as an **explicit, separate test case** so the collapse is proven per class, not assumed.

| # | Bypass / attack | Surface | Defense | Proving test |
|---|---|---|---|---|
| R1 | Shell chaining `sbx x ; rm -rf ~` / `&&` / `\|\|` / `&` / newline | Gate | metacharacter blocklist contains `; & \| \n` → reject | `test_sbx_gate`: `"sbx -- echo hi ; rm -rf ~"` → PROMPT; each of `; && \|\| & \n` variants → PROMPT |
| R2 | Pipe `sbx x \| tee /etc/passwd` | Gate | `\|` in blocklist → reject | `"sbx -- cat f \| sh"` → PROMPT |
| R3 | Command substitution `sbx $(rm -rf ~)` / backticks / `<()` `>()` | Gate | `$ ( ) \` < >` in blocklist → reject | `"sbx -- echo $(id)"`, `` "sbx -- echo `id`" ``, `"sbx -- diff <(id) f"` → all PROMPT |
| R4 | Heredoc `sbx -- cat <<EOF ... EOF` hiding a body | Gate | `< \n` in blocklist → reject | `"sbx -- cat <<EOF\nrm -rf ~\nEOF"` → PROMPT |
| R5 | Redirection `sbx -- echo x > ~/.bashrc` / `< secret` | Gate | `>` and `<` are in `METACHARS` → reject (redirection). **`~` is NOT in `METACHARS` (L1 fix)** — `~`-as-a-write-target is handled by KD-7 shared resolution + KD-8 containment (`~`→$HOME, outside project → PROMPT), not by the blocklist | `"sbx -- echo x > ~/.bashrc"`, `"sbx -- cat < ~/.ssh/id_rsa"` → PROMPT (both via `>`/`<`) |
| R6 | Wrapper `env X=1 sbx ...`, `sudo sbx`, `xargs sbx`, `command/nice/nohup/time/exec/builtin sbx` | Gate | first whitespace-split token must canonicalize (KD-6) to `WRAPPER_PATH`; a wrapper makes `toks[0]` `env`/`sudo`/… which resolves elsewhere → reject (no peeling) | one PROMPT case per name in `mcp-first-guard.py:134` SKIP_WRAPPERS |
| R6b | **Planted look-alike NAMED `sbx`** (`./sbx` in writable `.claude/tmp`, `/tmp/x/sbx`, a `PATH`-shadowing `sbx`) — C1 / A01 / CWE-290 | Gate | **IDENTITY, not basename (KD-6):** `toks[0]` is canonicalized (expand `~`, resolve rel/`PATH`, `realpath`) and must equal `WRAPPER_PATH` exactly; any same-named non-helper resolves elsewhere → reject | `test_sbx_gate`: `"./sbx -- echo hi"`, `"/tmp/x/sbx -- echo hi"`, and a bare `sbx` shadowed earlier on `PATH` → each PROMPT |
| R7 | Leading assignment `SBX_X=1 sbx ...` / `PATH=/evil sbx` | Gate | first token is `PATH=/evil` / `SBX_X=1`, which canonicalizes nowhere near `WRAPPER_PATH` (KD-6) → reject | `"PATH=/evil sbx -- echo hi"` → PROMPT |
| R8 | Subshell/brace hiding `(sbx x; evil)` / `{ sbx x; }` | Gate | `( ) { } ;` in blocklist → reject | `"(sbx -- echo hi)"`, `"{ sbx -- echo hi; }"` → PROMPT |
| R9 | `bash -c 'sbx ...'` payload | Gate | quote chars `' "` in blocklist → reject; also first token is `bash` | `"bash -c 'sbx -- echo hi'"` → PROMPT |
| R10 | ALL-CAPS `SBX ... ; evil` on case-insensitive FS | Gate | R1 `METACHARS` rejects the chained form (`;`). A bare `SBX -- x` is decided by KD-6 identity: on a case-sensitive FS `shutil.which("SBX")` misses → reject; on a case-insensitive FS it may resolve to `WRAPPER_PATH` — in which case it IS the real contained wrapper, so AUTOALLOW is also SAFE (containment, not spelling, is the boundary) | `"SBX -- echo hi ; id"` → PROMPT (via `;`); `"SBX -- echo hi"` → PROMPT on case-sensitive host (both outcomes safe) |
| R10b | False-positive quoted metachar `sbx -- echo ";"` | Gate | contains `; "` → reject → PROMPT. **This is the safe outcome, NOT a bug** (GRAFT 1: dropped the AUTOALLOW-echo-';' requirement) | `"sbx -- echo \";\""` → PROMPT (documented as intentionally-safe under-allow) |
| R11 | `--net` sneaks an auto-allow | Gate | if `--net` in sbx argv → PROMPT (locked decision) | `"sbx --net -- curl http://x"` → PROMPT |
| R12 | `--write` outside project auto-allows (incl. `--write ~` — H1 / A04 / CWE-706) | Gate | resolve each `--write` via the SHARED KD-7 rule (`realpath(join(cwd, expanduser(DIR)))`, byte-for-byte the helper's rule), then apply the KD-8 separator-safe containment (see R12c); if any resolves outside `project_root(cwd)` → PROMPT | `"sbx --write /etc -- touch /etc/x"` (cwd=/repo) → PROMPT; `"sbx --write ~ -- touch f"` → PROMPT (`~`→$HOME, outside project — the H1 case); `"sbx --write . -- touch f"` → AUTOALLOW |
| R12b | Equals-form / unknown flag SKIPPED: `sbx --write=/etc -- x`, `sbx --frobnicate -- x` (M-B / false-allow) | Gate | the flag loop parses `--write=DIR` like argparse (split on first `=`) AND treats ANY unrecognized token before `--` as a HARD PROMPT (bare return) — never a `== "--write"`-only scan that skips the equals-form and lets the helper's argparse open `/etc` | `test_sbx_gate`: `"sbx --write=/etc -- touch /etc/x"` → PROMPT; `"sbx --frobnicate -- x"` → PROMPT |
| R12c | Sibling-prefix false-allow: `sbx --write ../<root-basename>-evil` — with root `/x/repo`, `/x/repo-evil`.startswith(`/x/repo`) is True (sec-MED-1 / A04) | Gate | SEPARATOR-SAFE containment `os.path.commonpath([resolve_scope(DIR,cwd), project_root(cwd)]) == project_root(cwd)` (root itself allowed), NOT a bare `str.startswith`; applied identically in the helper's `inside_project` | `test_sbx_gate`: `"sbx --write ../<root-basename>-evil -- touch f"` (cwd inside `/x/repo`) → PROMPT |
| R13 | Missing/oddly-shaped payload or `cwd` | Gate | fail-safe: `tool_name!="Bash"`, empty cmd, `cwd is None`, JSON error → bare return (silence) | non-Bash tool, null command, missing cwd, malformed JSON → PROMPT + rc 0 + no stderr |
| R14 | SBPL / argv injection via `--write '/a") (allow default);("'` | Helper | KD-7 `resolve_scope` every scope; REJECT any raw-or-resolved path containing `" ' ( ) \` newline → fail-closed exit non-zero | `test_sbx`: crafted `--write` with quotes/parens → builder/guard rejects; helper exits non-zero, nothing runs |
| R15 | Symlink trick: scope symlink whose target is outside | Helper | `realpath` resolves symlinks at build time; the kernel enforces on the resolved final path | live self-test: symlink in scratch → target outside → write denied |
| R16 | Secret exfil via read (`sbx -- cat ~/.ssh/id_rsa`) | Helper | reads permissive-MINUS-secrets: explicit `deny file-read*` per `secret_paths(cwd)` entry, emitted AFTER the allow (last-match-wins) | [SC-3] live self-test + `test_sbx`: reading each secret path denied even with `--write .` |
| R17 | Secret write in-scope (`--write ~` then write `~/.aws/...`) | Helper | secret subpaths get an explicit `deny file-write*` regardless of scope | `test_sbx` (a) asserts each `secret_paths(cwd)` entry denied; live self-test confirms |
| R18 | Network via default | Helper | Seatbelt `(deny network*)`; bwrap `--unshare-net` — off unless `--net` | `test_sbx` (b) + [SC-2] live net probe blocked by default |
| R19 | Fork bomb / mem bomb | Helper | `resource.setrlimit(RLIMIT_NPROC/RLIMIT_AS/RLIMIT_CPU)` before execvp (KD-4) | live self-test: fork bomb capped by NPROC; large alloc capped by AS |
| R20 | Sandbox binary missing → fail-open run | Helper | `shutil.which` the required binary; if absent (or unknown platform) → exit non-zero, NEVER execvp the raw command | [SC-2] faked-missing-sandbox (PATH stripped) → helper exits non-zero, command never runs |
| R21 | `--ro` still allows scratch writes | Helper | `--ro` emits no write-allow at all (Seatbelt: no `file-write*` allow; bwrap: drop every `--bind`) | `test_sbx` (c): `--ro` yields zero writable scopes; live: `--ro -- touch ./.claude/tmp/x` denied |
| R22 | `.git/config` write (hook/alias RCE) | Helper | every ancestor `.git/config` is a `secret_paths(cwd)` write-deny even under `--write .` | `test_sbx`: `.git/config` in deny set; live: `--write . -- ...` writing `.git/config` denied |
| R22b | `.git/config` un-protected via a PLANTED nested `.git`: a decoy `cwd/.git` (plantable by a prior auto-allowed `--write .` run) relocates `project_root` to the decoy, so a single-anchor deny drops the REAL repo-root `.git/config` (git remote credentials) out of the deny set → readable in-sandbox (sec-MED-2 / secret-read leak) | Helper | `secret_paths(cwd)` denies read+write of the `.git/config` of EVERY `.git` walking up from `cwd` to the filesystem root, not only the nearest-ancestor `project_root` one — a decoy cannot un-protect an ancestor | `test_sbx`: temp tree `root/.git` + `root/sub/.git`, `cwd=root/sub` → BOTH `root/.git/config` and `root/sub/.git/config` in the deny set (read+write) |

*RLIMIT_NPROC caveat (capture):* `RLIMIT_NPROC` is **per-UID** (it counts ALL of the user's processes, not just this sandbox's descendants), so a low absolute cap can spuriously fail an innocent command. Tune it to a modest headroom above the current process count rather than a small absolute number (and leave headroom for the sandbox engine to fork its target), and document this. This is a *robustness* caveat, not a containment hole.

*Multi-hook precedence caveat (INFO):* two PreToolUse(Bash) hooks (`mcp-first-guard.py` + `sbx-gate.py`) run for the same event. Their polarities are disjoint (deny|empty vs allow|empty), so the plan asserts ordering is safe either way (§2 Constraints) — but Claude Code's **multi-hook aggregation semantics** (does one hook's `allow` override another's silence/deny? does registration order matter?) are **not verified in-repo**. Confirm empirically at implementation time (register both, exercise a clean `sbx` payload and a blocked non-`sbx` payload, observe the resolved decision). Even if aggregation misbehaves the fallback is safe: a lost `allow` degrades to a normal prompt, never to an unsandboxed auto-run.

*macOS secret-boundary caveat (M2 / A05 / CWE-696):* secret-READ denial on macOS rests on SBPL last-match-wins ordering, which is UNVERIFIED in-repo (§2). The ONLY proof is the live self-test probe (Step 10), deliberately kept OUT of portable CI — so CI can stay green while `~/.ssh` is still readable in-sandbox. Mitigation: Step 10 MUST explicitly assert secret-READ denial (read a secret path under `--write .` and assert it is BLOCKED), not merely out-of-scope write; and the ADR (Step 12) records that the macOS secret boundary is **probe-verified, not CI-verified**, with that residual risk stated.

---

## 6. Step-by-Step Plan

> Helper ordering is **security-first / pure-pieces-first** (GRAFT 5): every pure, unit-testable piece (Steps 1–5) lands before the single `execvp` site (Step 6). The gate (Step 7) is parallelizable. Test surfaces (Steps 8–10) and docs (Steps 11–12) follow, with a final regression (Step 13).

### Step 1: `sbx` helper — skeleton: argparse, manual `--` split, `Scope` object, `--dry-run`
**Files:** `ClaudeCode/skills/sandbox-run/scripts/sbx` (create)
**Dependencies:** none
**Description:** Python 3 stdlib-only executable (`#!/usr/bin/env python3`, `Usage:` docstring per P4). Parse `sbx [--write DIR]... [--net] [--ro] -- <cmd> [args...]`: **slice argv at the first bare `--` yourself** (feed the left side to `argparse`; keep the right side verbatim as the target argv — do **NOT** rely on `argparse.REMAINDER`, it is brittle). `--write` is `action="append"` (repeatable). Build the `Scope` object (§3 Type Definitions). Add a `--dry-run` mode that prints the fully-resolved plan and exits 0 **running NOTHING**, so the entire arg surface is testable with zero risk. If there is no `--` or no target argv → exit non-zero with a usage error.
**Pattern to follow:** P4 (`checkpoint.py:1-16`, `reindex.py:1-27`).
**Verification:** `sbx --write . -- echo hi` in `--dry-run` prints a plan with `writes` including the repo root and `argv == ["echo","hi"]`; `sbx -- ` (no target) exits non-zero.

### Step 2: `sbx` helper — fail-closed platform + binary detection (`BACKENDS` registry)
**Files:** `ClaudeCode/skills/sandbox-run/scripts/sbx` (modify)
**Dependencies:** Step 1
**Description:** Implement `BACKENDS = {"darwin": _seatbelt_argv, "linux": _bwrap_argv}` keyed on a `sys.platform` prefix; `builder = BACKENDS.get(_platform_key(sys.platform))`. If `builder is None` (unknown platform) OR the resolved sandbox binary is not found via `shutil.which("sandbox-exec")` / `shutil.which("bwrap")` → write a one-line reason to stderr and `sys.exit(<non-zero>)` **without ever constructing or running a target** (NFR-1, R20). There is no unsandboxed fallback, ever. Record the resolved engine on the `Scope`.
**Pattern to follow:** KD-3; `shutil.which` presence-gating as in `mcp-first-guard.py:158`.
**Verification:** with `PATH` doctored so the engine binary is not found, `sbx -- echo hi` exits non-zero, prints nothing to stdout, runs no target (the locked "faked-missing → fail-closed" case); an unknown platform key exits non-zero.

### Step 3: `sbx` helper — pure injection / secret / realpath guard layer
**Files:** `ClaudeCode/skills/sandbox-run/scripts/sbx` (modify)
**Dependencies:** Step 1
**Description:** Pure, unit-testable helpers (NO exec, NO filesystem side effects — the scratch `makedirs` is deferred to the runtime dispatch behind `if __name__ == "__main__":`, see M3/Step 6):
- `project_root(cwd) -> str` — the single KD-8 rule: walk up from `cwd` to the nearest ancestor containing a `.git` entry; fall back to `cwd` if none. On the helper side it exists to (a) feed the write-scope containment classification via `inside_project` and (b) be an importable parity-target for the Step 8 cross-file parity test — it does NOT feed `secret_paths` (sec-MED-2's ancestor-walk owns the `.git/config` deny); the gate computes the byte-identical rule. Never assume `cwd` itself is the repo root.
- `resolve_scope(dir, base) -> str` — the single KD-7 rule: `os.path.realpath(os.path.join(base, os.path.expanduser(dir)))`, with `base == cwd`. This is the EXACT rule the gate applies to `--write` (H1); the helper and gate MUST NOT diverge (no `expanduser`-only vs `join`-only split).
- `secret_paths(cwd) -> tuple[str, ...]` — the single pure FUNCTION (KD-2, M-A — NOT a module-level constant, because the `.git/config` entries are cwd-derived and a module-level tuple has no `cwd` at import): returns the home-anchored `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.claude` (expanded via `os.path.expanduser`, `cwd`-independent) PLUS a `.git/config` deny for **EVERY `.git` directory found walking up from `cwd` to the filesystem root** (sec-MED-2) — i.e. `os.path.join(d, ".git", "config")` for each ancestor `d` of `cwd` (inclusive) whose `<d>/.git` exists — NOT only the nearest-ancestor `project_root(cwd)` one, so a decoy `cwd/.git` planted by a prior `--write .` run cannot relocate the anchor and un-protect the real repo-root `.git/config`. Import-side-effect-free (M3): it reads the filesystem (to test `.git` existence) only when CALLED at runtime, never at import, and never mutates.
- `canonicalize(dir) -> str`: **thin wrapper over the shared `resolve_scope(dir, cwd)` (KD-7)**, then **REJECT** (fail-closed, exit non-zero) any raw or resolved path containing `"`, `'`, `(`, `)`, `\`, or a newline (R14). It performs NO `makedirs`.
- Writable-set computation (pure): with no `--write`, the sole writable scope is `<cwd>/.claude/tmp`; each `--write DIR` adds `canonicalize(DIR)`. `--ro` overrides: the writable set is empty (R21). The scratch dir is only *created* (`os.makedirs(..., exist_ok=True)`, never a shell `mkdir`) at runtime dispatch behind `__main__` (M3) — the pure layer merely names it.
- `inside_project(path, root) -> bool`: the **separator-safe** containment test (KD-8 / sec-MED-1) — `os.path.commonpath([path, root]) == root` (root itself allowed), NOT a bare `str.startswith` (which false-allows a `/x/repo-evil` sibling of `/x/repo`). This is the SAME test the gate's scope-containment check applies; it is part of the shared, DUPLICATED resolution contract (H-A) and is covered by the Step 8 parity test.
**Pattern to follow:** keep these referentially transparent AND import-side-effect-free (M3) so Step 9's offline suite can call them directly (KD-2, KD-7, KD-8).
**Verification:** `canonicalize("/tmp/a(b")` exits non-zero (paren rejected); with no flags the writable set is exactly `[<cwd>/.claude/tmp]`; `--ro` yields an empty writable set; `--write /etc` and `--write ~` are both classified outside-project (the `~` case matches the gate — H1); `inside_project("/x/repo-evil", "/x/repo")` is `False` (separator-safe — sec-MED-1); `secret_paths(cwd)` for a temp tree `root/.git` + `root/sub/.git` with `cwd=root/sub` includes BOTH `.git/config` paths (sec-MED-2); importing the module runs no `makedirs`.

### Step 4: `sbx` helper — pure macOS Seatbelt (SBPL) builder `_seatbelt_argv`
**Files:** `ClaudeCode/skills/sandbox-run/scripts/sbx` (modify)
**Dependencies:** Steps 2, 3
**Description:** `_seatbelt_argv(scope) -> list[str]` returns `["sandbox-exec","-p",<profile>, *scope.argv]`, where `<profile>` is an inline SBPL string built in last-match-wins order (see the SBPL UNVERIFIED constraint in §2): `(version 1)` / `(deny default)` / allow process-exec/fork and the minimal ops a normal program needs / `(allow file-read* (subpath "/"))` permissive reads / for each writable scope `(allow file-write* (subpath "<canonical>"))` unless `scope.ro` (then none) / **LAST, so they win:** `(deny file-read* (subpath "<secret>"))` and `(deny file-write* (subpath "<secret>"))` for every `secret_paths(cwd)` entry (R16, R17, R22, R22b) / `(deny network*)` unless `scope.net` (R18). Because Step 3 already rejected quotes/parens/backslash/newline, every embedded path is a safe SBPL string literal.
**Pattern to follow:** KD-2, KD-3; pure builder returning a list (no I/O) so Step 9 can assert it.
**Verification (offline, Step 9):** a default `Scope` profile contains `(deny default)`, a write-allow for `.../.claude/tmp`, secret denies AFTER the read-allow, and `(deny network*)`; `--ro` has NO `file-write*` allow; `--net` has NO `(deny network*)`.

### Step 5: `sbx` helper — pure Linux `bwrap` builder `_bwrap_argv`
**Files:** `ClaudeCode/skills/sandbox-run/scripts/sbx` (modify)
**Dependencies:** Steps 2, 3
**Description:** `_bwrap_argv(scope) -> list[str]` returns the `bwrap` argv (never a shell string): `--ro-bind / /` (read-all host) / for each writable scope `--bind <canonical> <canonical>` unless `scope.ro` (then none) / for each `secret_paths(cwd)` entry a mask (`--tmpfs <secret>` or an empty `--ro-bind` over it) layered AFTER the binds so the mask wins (R16, R17, R22, R22b) / `--unshare-net` unless `scope.net` (R18) / `--die-with-parent` / terminator / then `*scope.argv`. seccomp is an OFF-by-default `if scope.seccomp:` branch *inside this function* (locked decision 8) — localized, not a new path.
**Pattern to follow:** KD-2, KD-3; pure builder returning a list; argv-as-list is the injection-safe construction.
**Verification (offline, Step 9):** default argv contains `--ro-bind / /`, a `--bind` for scratch, a mask per secret, `--unshare-net`, `--die-with-parent`; `--net` omits `--unshare-net`; `--ro` omits every writable `--bind`.

### Step 6: `sbx` helper — the SINGLE `setrlimit` + `execvp` wiring (the only step that runs a child)
**Files:** `ClaudeCode/skills/sandbox-run/scripts/sbx` (modify)
**Dependencies:** Steps 4, 5
**Description:** With a fully-validated `Scope`: apply `resource.setrlimit` for `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NPROC` (sane ceilings — `RLIMIT_NPROC` per-UID caveat above; leave headroom for the engine to fork its target), **then** `os.execvp(argv[0], argv)` where `argv` is the builder's output. Because `execvp` replaces the process image (same PID) and rlimits survive exec, the target runs under the limits and its exit code passes straight through — no fork/`waitpid` wrapper (NFR-6). This is the FIRST and ONLY point at which `sbx` executes anything, consuming only already-guarded, already-tested inputs. Pin the setrlimit-before-execvp ordering in a code comment (KD-4).
- **ALL side-effecting code lives behind `if __name__ == "__main__":` (M3).** The argparse dispatch, the scratch `os.makedirs`, `resource.setrlimit`, and `os.execvp` MUST be inside the `__main__` guard (or in a `main()` called only from it). The module TOP LEVEL defines ONLY pure objects — `secret_paths`, `resolve_scope`, `project_root`, `canonicalize`, `inside_project`, `_seatbelt_argv`, `_bwrap_argv`, `BACKENDS` (all functions/registries doing NO I/O at import; M-A makes the secret set a FUNCTION precisely so it needs no import-time `cwd`) — so that Step 9's offline suite can `import` the module and call the pure builders WITHOUT triggering any exec/setrlimit/makedirs. Without this guard, importing `sbx` would run the dispatch at import time.
**Pattern to follow:** KD-4; M3 `__main__`-guard for all side effects; fail-closed from Step 2 still guards this path (it is unreachable if the engine could not be resolved).
**Verification:** covered by the live self-test (Step 10) and the offline builder assertions (Step 9); the setrlimit-before-execvp ordering is asserted in Step 9; **importing the module executes NO child, sets NO rlimit, creates NO directory** (asserted by Step 9's whitebox import — proof of the M3 guard).

### Step 7: Create the gate `sbx-gate.py` — self-contained, metacharacter-REJECTING, grant-only
**Files:** `ClaudeCode/hooks/sbx-gate.py` (create)
**Dependencies:** Step 1 (needs the finalized `sbx` flag semantics to classify scope) — parallelizable with Steps 2–6
**Description:** Realize KD-1 / P3. Copy ONLY the ~10-line I/O envelope + `main()` skeleton from `mcp-first-guard.py:560-599` (stdin JSON read; `tool_name=="Bash"` self-gate `:562-563`; `tool_input.command` read `:564`; empty-command early return `:565-566`; the `try/except: pass` + `sys.exit(0)` fail-open `:594-599`). **Import NOTHING from `mcp-first-guard.py`; do NOT reuse `split_top`/`_tokens`/`primary`/`substitutions`/`_strip_heredocs`.** Implement the self-contained `is_clean_sbx(cmd, cwd)` (§3 P3):
- Reject → empty stdout if `cwd is None`, if the stripped command is empty or starts with `#`, or if it contains ANY blocklisted metacharacter (`; & | $ \` ( ) < > { } newline ' " \ * ?`). **Do NOT reject a leading `~` (H2)** — the documented invocation form starts with `~/.claude/...`; the `~`-as-write-target danger is handled below by the KD-7/KD-8 scope check, not by a leading-char reject.
- **Wrapper IDENTITY, NOT basename (C1 / KD-6):** whitespace-split; canonicalize `toks[0]` — a `/`- or `~`-bearing form via the SHARED `resolve_scope(toks[0], cwd)` (KD-7), a bare name via `shutil.which` + `os.path.realpath` — and require it to equal `WRAPPER_PATH = os.path.realpath(os.path.expanduser("~/.claude/skills/p/skills/sandbox-run/scripts/sbx"))` EXACTLY. Never `os.path.basename(toks[0]) == "sbx"` (that is the C1 false-allow — a planted `./sbx` would pass). Requires `import os, shutil`.
- Parse sbx's own flags up to `--`, **matching argparse's grammar EXACTLY (M-B)**: recognize `--net`, `--ro`, `--write DIR` AND the equals-form `--write=DIR` (argparse accepts both; the gate MUST parse `--write=DIR` the same way — split on the first `=` — before applying `resolve_scope`). Refuse on `--net` (R11). For each `--write` (space-form OR equals-form) require the **separator-safe** containment (KD-8 / sec-MED-1): `os.path.commonpath([resolve_scope(DIR, cwd), project_root(cwd)]) == project_root(cwd)` (root itself allowed) — the SAME KD-7/KD-8 rules the helper uses (H1), NOT a bare `str.startswith` that would false-allow a `../<root>-evil` sibling (R12c). **Treat ANY unrecognized token before `--` — an unknown flag, or an equals-form the loop does not explicitly handle — as a HARD PROMPT (bare `return`), NEVER skip it (M-B):** a `== "--write"`-only scan would silently pass `--write=/etc` through while the helper's argparse opens `/etc` — a false-allow (R12b). On success emit the `allow` JSON (P2); on ANY failure or doubt → bare `return`.
- **Invocation contract (H2) — the gate accept-set and SKILL.md MUST intersect:** the ONE accepted form is the wrapper invoked by its absolute deployed path `~/.claude/skills/p/skills/sandbox-run/scripts/sbx`; KD-6 canonicalization makes the `~`/relative/`PATH` variants of that one real path resolve to `WRAPPER_PATH`. State this contract verbatim in both this step and SKILL.md (Step 11) so they cannot diverge.
- **Top-of-file docstring MUST record (a)** why fail-open is fail-safe for a grant-only gate (§0), **(b)** the inverted safety model + why this file copies-not-imports and shares no parser (§0 / P3-drift), **and (c)** the KD-6 identity rule (basename-matching is a forbidden false-allow), so no future edit reverts it to a basename compare, a fail-closed brick, or an imported see-through parser.
**Pattern to follow:** P1, P2, P3, P3-drift; KD-6/KD-7/KD-8 shared rules; grant-only — never emit `"deny"`.
**Verification:** feed a clean contained `sbx` payload invoking the deployed absolute path with `cwd` → stdout parses to `permissionDecision:"allow"`; `sbx --net -- x`, `sbx --write /etc -- x`, `sbx --write ~ -- x` (H1), `./sbx -- x` and `/tmp/x/sbx -- x` (C1 — planted look-alike), `sbx -- x && ls`, a non-Bash payload, and malformed JSON → each empty stdout, exit 0. (Full matrix in Step 8.)

### Step 8: Gate contract suite `tests/test_sbx_gate.py` + `run.py` registration + forge target (mandatory, CI)
**Files:** `tests/test_sbx_gate.py` (create), `tests/run.py` (modify), `project-forge.yaml` (modify)
**Dependencies:** Steps 6, 7 (Step 7 for the gate under test; Step 6 for the finalized helper the cross-file PARITY TEST imports)
**Description:** Mirror `tests/test_mcp_first_guard.py` (P9): `HOOK = H.repo_path("ClaudeCode","hooks","sbx-gate.py")`; extend the `case()` builder (`:76-81`) to inject an optional `cwd` into the payload; invert `classify()` (`:897-914`) so **empty stdout == PROMPT** and parsed `permissionDecision=="allow"` == **AUTOALLOW** (a malformed/other shape is a test failure); keep the invariants rc==0 & stderr empty (`:1002-1005`); expose `run(opts)` (`:992+`). Encode the full adversarial matrix — every R1–R13 (+R6b, R10b) row of §5 as at least one PROMPT case, and the minimal AUTOALLOW cases:
- **AUTOALLOW:** `<WRAPPER> -- echo hi` (cwd=/repo, default scratch); `<WRAPPER> --write . -- touch f` (cwd=/repo); `<WRAPPER> --ro -- true`.
- **PROMPT groups:** chaining (R1), pipe (R2), substitutions (R3), heredoc (R4), redirection (R5), wrappers (R6: one per `mcp-first-guard.py:134` SKIP_WRAPPERS member), **planted look-alike named `sbx` (R6b / C1): `./sbx -- echo hi`, `/tmp/x/sbx -- echo hi`, and a bare `sbx` shadowed earlier on `PATH` → each PROMPT**, assignment prefix (R7), subshell/brace (R8), `bash -c` (R9), ALL-CAPS (R10), quoted-metachar (R10b — documented as intentionally-safe under-allow), `--net` (R11), `--write` outside project incl. **`<WRAPPER> --write ~ -- x` (R12 / H1) → PROMPT** (`~`→$HOME, outside project), **equals-form + unknown-flag (R12b / M-B): `<WRAPPER> --write=/etc -- x` → PROMPT and `<WRAPPER> --frobnicate -- x` (any unknown flag before `--`) → PROMPT — the gate must not skip an equals-form/unrecognized token**, **sibling-prefix (R12c / sec-MED-1 / A04): `<WRAPPER> --write ../<root-basename>-evil -- x` (cwd inside the project) → PROMPT (a bare `startswith` would false-allow this sibling of the project root)**, plumbing/fail-safe (R13: non-Bash, null/missing command, missing `cwd`, malformed JSON).
- **Cross-file PARITY TEST (H-A — MANDATORY; closes the "divergence == escape" gap NFR-8 now requires):** because `resolve_scope`, `project_root`, and the separator-safe containment test are DELIBERATELY DUPLICATED across the gate/helper boundary (the gate imports nothing — P8; §3 shared-contract note), a one-sided edit would silently reintroduce the H1 gate/helper divergence — a containment escape. This suite MUST import BOTH copies — the gate's via the harness `load_module_from_path` (`tests/_harness.py:394-405`; the gate has a `.py` extension, so its `spec_from_file_location` path works) and the helper's `resolve_scope`/`project_root` via `importlib.machinery.SourceFileLoader` (H3, the helper is extension-less) — and assert BYTE-IDENTICAL output over a battery of inputs including `~`, `.`, `/etc`, a relative `../sibling`, and a symlink (create a temp symlink in a temp cwd and assert both copies realpath it identically). It also asserts the separator-safe containment verdict (sec-MED-1) agrees on both sides for the `../<root-basename>-evil` sibling. This test is the enforcement the "divergence == escape" invariant currently lacks.
- **Identity testability (consequence of C1 / KD-6):** `WRAPPER_PATH` resolves to `~/.claude/skills/p/skills/sandbox-run/scripts/sbx`, which does NOT exist in CI, so a true end-to-end subprocess AUTOALLOW cannot rely on the real path. Assert the AUTOALLOW cases via the harness's gated **whitebox import** (P9, `:65-66`): import `is_clean_sbx`, monkeypatch the module's `WRAPPER_PATH` to a temp fixture the test creates, and invoke that same fixture path in `<WRAPPER>` so identity matches — assert `(True, reason)` and the exact reason string. The PROMPT and rc/stderr invariant cases stay end-to-end subprocess cases (they reject regardless of `WRAPPER_PATH`, so they need no fixture). Do NOT add a production env override for the wrapper path — the whitebox monkeypatch keeps the identity surface out of the shipped gate.
Add a `run_sbx_gate(opts)` wrapper mirroring `run_mcp_first_guard` (`tests/run.py:133-134`) and one `SUITES` tuple `("sbx_gate", run_sbx_gate, "sbx PreToolUse grant-only gate", <N>)` (`:202-226`) with the exact case count `<N>` (a stale count is a hard failure `:260-261,284-290`). Add a `sbx_gate` **test** target to `project-forge.yaml` mirroring the `mcp_first_guard` target (`:51-57`). (The `syntax` build target already compile-checks `sbx-gate.py` and this file — see §3 Build System; no build-target change needed.)
**Pattern to follow:** P9; `only()`-style exact-reason assertion (`:92-99`) adapted so an AUTOALLOW case asserts its reason string.
**Verification:** `forge test sbx_gate` passes; declared count matches; `forge clean pycache` leaves no `.pyc`.

### Step 9: Offline helper containment suite `tests/test_sbx.py` (RECOMMENDED — flagged planned artifact)
**Files:** `tests/test_sbx.py` (create)
**Dependencies:** Steps 4, 5, 6 (the M3 `__main__` guard from Step 6 must be in place so the import is side-effect-free)
**Description:** A platform-independent suite that imports the helper's **pure** builders (`_seatbelt_argv`, `_bwrap_argv`) and guard layer and asserts, per backend, that a given `Scope` yields an argv/profile that: (a) denies every `secret_paths(cwd)` entry (read+write), including the `.git/config` of EVERY ancestor `.git` for a temp nested-`.git` tree (`root/.git` + `root/sub/.git`, `cwd=root/sub`) so both configs land in the deny set (sec-MED-2); (b) unshares/denies network unless `--net`; (c) confines writes to scope and denies ALL writes under `--ro`; (d) rejects injected metacharacters/`..` in `--write`. Also assert the `setrlimit`-before-`execvp` ordering (KD-4) via a structural/whitebox check, and that **importing the module runs NO exec/setrlimit/makedirs** (the M3 `__main__`-guard proof). Building a Linux `bwrap` argv on macOS (and vice-versa) is pure string work, so this runs in CI on any OS — it is the only way to pin the containment contract deterministically. Register it exactly like Step 8 (a `run_sbx` wrapper + a `SUITES` row with a declared count).
- **Import mechanism (H3 — mandatory):** the helper is deliberately named `sbx` with NO `.py` extension (the CLI name is intentional — do NOT rename it to `sbx.py`). The repo harness's `load_module_from_path` (`tests/_harness.py:394-405`) uses `importlib.util.spec_from_file_location`, which returns a `None`/loader-less spec for an extension-less path and would CRASH on import. This suite MUST import the helper via an **explicit loader** — `importlib.machinery.SourceFileLoader("sbx", H.repo_path("ClaudeCode","skills","sandbox-run","scripts","sbx")).load_module()` (or `SourceFileLoader(...).exec_module(module_from_spec(spec_from_loader(...)))`) — which handles a suffix-less file. Keep `sys.dont_write_bytecode = True` around the load (mirror `_harness.py:397-398`) so no `.pyc` leaks. This import is side-effect-free ONLY because of the Step 6 / M3 `__main__` guard.
- **Flag:** this is a perspective-recommended strengthening beyond the locked minimum; if the panel prefers the locked minimum, fold assertions (a)–(d) plus the H3 loader import into `test_sbx_gate.py` as a second group instead of a second suite.
**Pattern to follow:** P9 registration; H3 `SourceFileLoader` import for the extension-less helper; keep every assertion offline (no `sandbox-exec`/`bwrap` needed).
**Verification:** `forge test all` includes `sbx` (or its folded group) and passes on the host OS with no case-count drift.

### Step 10: Live self-test probe fixture `selftest_probe.py` (scratch/write/net + mandatory secret-READ; on-demand, NOT wired into portable CI)
**Files:** `ClaudeCode/skills/sandbox-run/scripts/selftest_probe.py` (create)
**Dependencies:** Step 6
**Description:** A clearly-named probe fixture (NOT `foo.py`) that attempts these actions and reports each outcome: (a) write to `<cwd>/.claude/tmp/probe.out` (must SUCCEED — scratch); (b) write to a path OUTSIDE the project, e.g. `os.path.expanduser("~/sbx_probe_escape")` (must be BLOCKED); (c) open a network socket / `urllib` fetch (must be BLOCKED); **(d) — MANDATORY, M2 / A05 / CWE-696 — attempt to READ a real secret path (e.g. `os.path.expanduser("~/.ssh/id_rsa")`, or any existing file under `~/.ssh`) while running under `--write .` (i.e. with the project itself writable), and assert the READ is BLOCKED.** Probe (d) is the ONLY proof that the SBPL last-match-wins secret-deny actually takes effect on macOS (the ordering is UNVERIFIED in-repo, §2) — a missing or out-of-scope-only probe could stay green while `~/.ssh` remained readable in-sandbox. The skill's self-test runs `sbx --write . -- python3 selftest_probe.py` and asserts a==ok, b==blocked, c==blocked, **d==blocked** — validated **live, zero iterations** ([SC-2], [SC-3]). Then a faked-missing-sandbox run (PATH stripped of `sandbox-exec`/`bwrap`) must exit non-zero with the command NOT run (R20); plus a `.git/config`/secret-WRITE denial probe (R17, R22). Because it needs a real `sandbox-exec`/`bwrap` and is platform-specific, it stays an **on-demand skill probe**, explicitly **NOT** part of `forge test all` — carry a one-line rationale in `SKILL.md` so no future engineer wires a platform-specific live probe into portable CI. The ADR (Step 12) MUST state that the macOS secret boundary is **probe-verified, not CI-verified**, with that residual risk. Delegate the actual run-fix loop to `p:minion-builder`/`p:minion-runner` per CLAUDE.md.
**Pattern to follow:** stdlib-only; `static-linking/` bundled verify-script precedent (`docs/subsystems/skills.md:32-34`).
**Verification:** on the host OS probes (a)–(d) report ok/blocked/blocked/blocked; the faked-missing probe fails closed.

### Step 11: Write `sandbox-run/SKILL.md`
**Files:** `ClaudeCode/skills/sandbox-run/SKILL.md` (create)
**Dependencies:** Steps 6, 7
**Description:** Frontmatter `name: sandbox-run` (matches the dir, no `p:` prefix — P6, `ARCHITECTURE.md:37`), a specific `description`, optional `model`. Body: a `## Usage` fenced block showing `sbx [--write DIR]... [--net] [--ro] -- <cmd>` invoked via the absolute deployed path `~/.claude/skills/p/skills/sandbox-run/scripts/sbx` (P5); a `### Step 1 — Parse arguments` prose section (no `$ARGUMENTS`); a **`## Invocation contract` section (H2) that states, in the SAME words as the gate (Step 7), the single coherent contract: the wrapper is invoked — and auto-allowed by `sbx-gate.py` — ONLY by that absolute deployed path, and the gate matches it by canonical-path IDENTITY (KD-6), never by filename; so a program merely named `sbx` elsewhere gets no auto-allow. This section and the gate step are the two ends the H2 fix keeps in sync**; a `## Trust boundary` section stating the **gate-and-helper-are-independent** rule (KD-5) and that the gate is bypassable *on purpose* because the helper is the boundary; a `## Registration` section with the **PascalCase** `PreToolUse` / matcher `"Bash"` `settings.json` snippet (P10), the explicit camelCase-README warning, a note that the two Bash hooks have disjoint polarities and are order-independent **(with the caveat that Claude Code's multi-hook aggregation semantics should be confirmed empirically at registration time — §5 multi-hook caveat)**, and that registration is the USER's responsibility with no installer (locked decision c); a `## Platforms` matrix (macOS Seatbelt FULL / Linux bwrap FULL / unknown → fail-closed) with the seccomp optional-extra note; a `## Self-test` section documenting the `selftest_probe.py` probes (including the mandatory secret-READ probe (d), M2) as on-demand/platform-specific and explicitly NOT in portable CI, and stating the macOS secret boundary is probe-verified not CI-verified; and the `RLIMIT_NPROC` per-UID caveat.
**Pattern to follow:** P5, P6, P10.
**Verification:** frontmatter `name` equals the directory name; any embedded JSON parses (`inspect_call validate`).

### Step 12: Wiki sync — hooks.md (de-stale + add both), skills.md, INDEX, ADR (scripts.md untouched)
**Files:** `docs/subsystems/hooks.md` (modify), `docs/subsystems/skills.md` (modify), `docs/INDEX.md` (regenerate), `docs/adr/0005-approve-the-wrapper-not-the-command.md` (create)
**Dependencies:** Steps 7, 11
**Description:**
- `docs/subsystems/hooks.md`: add TWO rows to the roster table (`:26-36`, which currently lists `attention-reminder.py` and the `post-edit-*` scripts but OMITS `mcp-first-guard.py`): the pre-existing-but-undocumented `mcp-first-guard.py` (PreToolUse / Bash / MCP-first routing guard) AND the new `sbx-gate.py` (PreToolUse / Bash / auto-allow contained `sbx` invocations). Bump the `verified:` frontmatter (currently `commit: d6659f7`, `date: 2026-07-16`, `:9-11`) to the landing commit/date.
- `docs/subsystems/skills.md`: add a `p:sandbox-run` row to the Notable-skills table (`:42-51`); bump `verified:` (currently `commit: 26200d1`, `date: 2026-07-31`, `:9-11`). **Note (I2 — prevent a footgun):** the skill invocation name is `p:sandbox-run`, but the on-disk skill DIRECTORY is bare `ClaudeCode/skills/sandbox-run/` (NOT `p:sandbox-run/`) and the frontmatter `name:` is bare `sandbox-run` — the `p:` is applied by the plugin at invocation time (`ARCHITECTURE.md:23,37`). This repo's `skills.md` prose (`:21,:36`) still says directories are named `p:<name>`, which is stale; do NOT copy that wording into the new row, and do NOT create a `p:sandbox-run/` directory. That stale prose at `:21` and `:36` is itself a candidate for a SEPARATE, LATER doc fix (OUT OF SCOPE here — do not touch it in this change) so it stops misleading readers; this step only avoids propagating it into the new row (LOW / I2 follow-up).
- `docs/INDEX.md`: regenerate/audit via the reindex tool (`ClaudeCode/skills/wiki/scripts/reindex.py --check` must pass, `:128`) — the feature adds no new wiki PAGE (the `feature-implementation-plan` slug already exists), only rows, but the audit is the gate.
- `docs/adr/0005-approve-the-wrapper-not-the-command.md`: create, following the ADR frontmatter/body format (`docs/adr/0003-the-trigger-travels-with-the-tool.md:1-27`; next free number is 0005). Capture the WHY: the trust boundary shifts from a per-command allowlist to one audited containment wrapper; the load-bearing invariant (gate never false-allows); **why fail-open is fail-safe for a grant-only gate; the inverted safety model — why the gate REJECTS metacharacters and copies-not-imports the parser** (§0 / KD-1); **why the gate authorizes wrapper IDENTITY (canonical path == `WRAPPER_PATH`) and NOT filename — a basename check is a false-allow (C1 / A01 / CWE-290)**; the rejected alternatives. **Residual-risk record (M2 / A05 / CWE-696): the macOS secret boundary rests on SBPL last-match-wins ordering that is UNVERIFIED in-repo and proven ONLY by the live self-test probe (Step 10, probe d), which is deliberately NOT in portable CI — so the boundary is _probe-verified, not CI-verified_; state this residual risk explicitly.** **Accepted-assumption note (INFO): the realpath→exec TOCTOU on the wrapper token — a symlink that resolves to `WRAPPER_PATH` at classify-time but is swapped before the shell execs it — is OUT of the stated adversary model (§0: the adversary is a command string, not a concurrent local process racing the filesystem); the ADR records it as an accepted assumption, NOT a defense the gate provides.** This is exactly the rationale the source cannot carry (CLAUDE.md wiki mandate).
- **`docs/subsystems/scripts.md` is intentionally NOT edited** (GRAFT 3): its `sources:` is `Scripts` (`:7-8`) and it covers top-level MCP servers + task utilities; the `sbx` helper is a *skill-bundled* script under `ClaudeCode/skills/`, so it belongs to `skills.md`. Editing `scripts.md` would mis-scope the page and invite future drift.
**Pattern to follow:** wiki `verified:` bump discipline; ADR format at `docs/adr/0003-...:1-27`; reindex audit gate.
**Verification:** `reindex.py --check` passes (no dup slugs / malformed frontmatter / orphans); both hook rows and the skill row present; `scripts.md` untouched.

### Step 13: Full-suite regression
**Files:** none (verification only)
**Dependencies:** Steps 1–12
**Description:** Run the entire suite via `forge test all` (delegated to `p:minion-builder`) to confirm the new `sbx_gate` (and, if adopted, `sbx`) suite passes, declared counts match, and no existing suite regresses (`mcp_first_guard` declared 335, `tests/run.py:205-206`). Confirm `forge build syntax` is green and `forge clean pycache` shows no leaked `.pyc`.
**Verification:** `forge test all` exits 0; no case-count drift.

---

## 7. Critical Files

| File | Role | Action |
|---|---|---|
| `ClaudeCode/skills/sandbox-run/scripts/sbx` | Containment enforcement point: argparse + `--` split, `Scope`, pure `secret_paths(cwd)`, `BACKENDS` registry, pure `_seatbelt_argv`/`_bwrap_argv`, injection/realpath guard, separator-safe `inside_project`, setrlimit-before-execvp, fail-closed | create |
| `ClaudeCode/skills/sandbox-run/scripts/selftest_probe.py` | Live self-test fixture: scratch OK / out-of-scope-write BLOCKED / net BLOCKED / secret-READ BLOCKED (probe d, M2) + faked-missing → fail-closed; on-demand, NOT in CI | create |
| `ClaudeCode/skills/sandbox-run/SKILL.md` | Skill entry: usage, trust boundary, PascalCase registration, platforms, self-test | create |
| `ClaudeCode/hooks/sbx-gate.py` | Grant-only PreToolUse(Bash) gate; self-contained metacharacter-REJECTING classifier; copies envelope only, imports nothing from `mcp-first-guard.py` | create |
| `ClaudeCode/hooks/mcp-first-guard.py` | Source of the I/O envelope only (parser NOT reused) | read-only reference |
| `tests/test_sbx_gate.py` | Adversarial gate suite (mirror of `test_mcp_first_guard.py`, inverted polarity) + the mandatory cross-file PARITY TEST that imports both the gate's and helper's `resolve_scope`/`project_root` and asserts byte-identical output (H-A) | create |
| `tests/test_sbx.py` | Offline pure-builder + injection containment assertions (RECOMMENDED — flagged) | create (optional) |
| `tests/run.py` | Suite registry (`SUITES`, `:202-226`) + `run_sbx_gate` (and optional `run_sbx`) wrapper | modify |
| `project-forge.yaml` | Add `sbx_gate` test target (mirror `mcp_first_guard`, `:51-57`) | modify |
| `docs/subsystems/hooks.md` | Roster (`:26-36`) — add stale-missing `mcp-first-guard.py` AND `sbx-gate.py`; bump `verified:` | modify |
| `docs/subsystems/skills.md` | Add `p:sandbox-run` row (`:42-51`); bump `verified:` | modify |
| `docs/INDEX.md` | Regenerate/audit via reindex tool | regenerate |
| `docs/adr/0005-approve-the-wrapper-not-the-command.md` | Trust-boundary + inverted-safety-model rationale | create |
| `docs/subsystems/scripts.md` | Intentionally NOT edited (out of `sources: Scripts` scope — GRAFT 3) | no change |
| `~/.claude/settings.json` | User-side PreToolUse registration (PascalCase) | documented only — NOT written by us |
| `ClaudeCode/.claude-plugin/plugin.json` | Ships NO `hooks` block (locked decision c) | unchanged (confirm) |

---

## 8. Post-Implementation Checklist

- [ ] `sbx-gate.py` emits `allow` **only** for a single, un-wrapped, metacharacter-free invocation of the ONE deployed wrapper inside the project with no `--net` (`is_clean_sbx`, §3 P3).
- [ ] **(C1 / A01 / CWE-290)** The gate authorizes the first token by canonical-path IDENTITY (`toks[0]` expand + resolve + `realpath` == `WRAPPER_PATH`), NEVER by `os.path.basename(...) == "sbx"`; a same-named non-helper (`./sbx`, `/tmp/x/sbx`, `PATH`-shadow) PROMPTS, proven by a `test_sbx_gate` case (KD-6, R6b). The gate no longer blanket-rejects a leading `~` (H2); the documented `~/.claude/...` deployed-path invocation is the ONE accepted form and the SKILL.md invocation-contract section states the same.
- [ ] `sbx-gate.py` imports NOTHING from `mcp-first-guard.py` and reuses none of its parser; a top-of-file docstring records the fail-open→fail-safe reasoning AND the inverted safety model / copy-not-import rationale (§0, KD-1).
- [ ] Every containment-escape class R1–R13 (+R10b) has a PROMPT (empty-stdout) test in `tests/test_sbx_gate.py`; the quoted-metachar case is documented as intentionally-safe under-allow (GRAFT 1).
- [ ] Gate ALWAYS exits 0; PROMPT means literally no stdout; AUTOALLOW means parsed `permissionDecision:"allow"`; stderr empty (invariants mirror `test_mcp_first_guard.py:1002-1005`).
- [ ] `sbx` fail-closes (non-zero, command never run) when the sandbox binary is missing or the platform is unknown; `BACKENDS.get(...)` has a fail-closed default (NFR-1, R20).
- [ ] `sbx` REJECTS any `--write` path containing `" ' ( ) \` newline before it reaches the SBPL/bwrap invocation (R14).
- [ ] **(H1 / A04 / CWE-706)** The gate and helper resolve every `--write` (and the wrapper token) via the SAME KD-7 rule `os.path.realpath(os.path.join(cwd, os.path.expanduser(DIR)))`, so `sbx --write ~` resolves to `$HOME` on BOTH sides and PROMPTS (proven by a `test_sbx_gate` case); no `expanduser`-vs-`join` divergence remains.
- [ ] **(M1)** "Project root" is computed ONCE via the KD-8 rule (nearest `.git`-bearing ancestor of `cwd`, else `cwd`) and used identically by the gate's scope-containment check AND the helper; `cwd` is not assumed to be the repo root. (The `.git/config` secret is NO LONGER anchored to only this project root — see the `secret_paths(cwd)` ancestor-walk item, sec-MED-2.)
- [ ] **(H-A — durability / divergence == escape)** `resolve_scope`, `project_root`, and the separator-safe containment test are documented as a SHARED CONTRACT that is DELIBERATELY DUPLICATED across the gate/helper boundary (the gate imports nothing — P8), NOT one physical function; NFR-8 lists the resolution rule among the drift-pinned contracts; and a MANDATORY cross-file PARITY TEST in `tests/test_sbx_gate.py` imports BOTH copies (gate via `load_module_from_path` `tests/_harness.py:394-405`, helper via `SourceFileLoader` — H3) and asserts byte-identical output over `~`, `.`, `/etc`, a relative `../sibling`, and a symlink.
- [ ] **(sec-MED-1 / A04)** Containment ("--write inside project") uses a SEPARATOR-SAFE test — `os.path.commonpath([resolve_scope(DIR,cwd), project_root(cwd)]) == project_root(cwd)` (root itself allowed) — in BOTH the gate and the helper's `inside_project`, NEVER a bare `str.startswith`; `<WRAPPER> --write ../<root-basename>-evil -- x` PROMPTS (proven by a `test_sbx_gate` case, R12c).
- [ ] **(M-B / false-allow)** The gate's flag parser matches argparse's grammar: it parses the equals-form `--write=DIR` the same as `--write DIR`, and treats ANY unrecognized token before `--` (unknown flag or unhandled equals-form) as a HARD PROMPT (bare `return`), never skipping it; `<WRAPPER> --write=/etc -- x` and `<WRAPPER> --frobnicate -- x` both PROMPT (proven by `test_sbx_gate` cases, R12b).
- [ ] **(L1)** `METACHARS` contains NO `~`; the risk table (R5) and the classifier are consistent — redirection is rejected by `>`/`<`, and `~`-as-a-write-target is handled by the KD-7/KD-8 scope check, not by the blocklist.
- [ ] **(M-A / sec-MED-2)** The secret deny set is produced by the single pure FUNCTION `secret_paths(cwd)` (NOT a module-level constant — a constant evaluated at import has no `cwd`) and both backends translate it; the home-anchored secrets (`~/.ssh ~/.aws ~/.gnupg ~/.config/gh ~/.claude`) PLUS the `.git/config` of EVERY ancestor `.git` from `cwd` to the filesystem root are denied BOTH read and write even inside a scope, so a planted nested `.git` cannot un-protect the real repo-root credentials (R16, R17, R22, R22b; KD-2/NFR-8).
- [ ] Network is denied by default; `--net` triggers a PROMPT in the gate, never an auto-allow (R11, R18).
- [ ] `--ro` denies all writes including scratch — zero writable scopes on both backends (R21).
- [ ] `resource.setrlimit(RLIMIT_AS/RLIMIT_CPU/RLIMIT_NPROC)` is applied BEFORE `os.execvp`; the ordering is pinned in a comment and asserted in a test; the RLIMIT_NPROC per-UID caveat is documented (KD-4, R19).
- [ ] **(M3)** ALL side-effecting code (argparse dispatch, scratch `os.makedirs`, `setrlimit`, `os.execvp`) is behind `if __name__ == "__main__":`; the module top level defines only pure objects (`secret_paths` (a pure FUNCTION, not a module-level constant — M-A), `resolve_scope`, `project_root`, `canonicalize`, `inside_project`, `_seatbelt_argv`, `_bwrap_argv`, `BACKENDS`), so importing `sbx` runs NO exec/setrlimit/makedirs (asserted by Step 9).
- [ ] The helper is built pure-pieces-first: Steps 1–5 (arg model, fail-closed detection, guards, both builders) land before the single `execvp` site (Step 6); `--dry-run` runs nothing (GRAFT 5).
- [ ] `_seatbelt_argv` and `_bwrap_argv` are pure `Scope -> argv` functions; adding a platform later touches one function + one `BACKENDS` entry; seccomp is an OFF-by-default `if` branch inside `_bwrap_argv` (KD-3, NFR-9).
- [ ] Exit code of the sandboxed command is passed through (NFR-6).
- [ ] Live self-test (`selftest_probe.py`, NOT `foo.py`) passes in ZERO iterations: scratch OK / outside-write BLOCKED / net BLOCKED / **secret-READ BLOCKED under `--write .` (probe d — M2 / A05 / CWE-696)** ([SC-2], [SC-3]); documented as on-demand/platform-specific and NOT wired into `forge test all`; the ADR records the macOS secret boundary as probe-verified, not CI-verified (Step 10 rationale).
- [ ] `tests/test_sbx_gate.py` registered in `tests/run.py` with an accurate declared count; `forge test sbx_gate` and `forge test all` pass with no count drift; `forge clean pycache` leaves no `.pyc`.
- [ ] (If adopted) `tests/test_sbx.py` offline assertions (a)–(d) pass on the host OS; **it imports the extension-less `sbx` helper via `importlib.machinery.SourceFileLoader`, NOT the harness's `spec_from_file_location`-based `load_module_from_path` (H3 / `tests/_harness.py:394-405`), and the helper is NOT renamed to `sbx.py`**.
- [ ] `SKILL.md` documents manual `~/.claude/settings.json` registration in PascalCase `PreToolUse`, matcher `"Bash"`, disjoint two-hook polarity, and USER responsibility (locked decision c).
- [ ] `docs/subsystems/hooks.md` now lists BOTH `mcp-first-guard.py` and `sbx-gate.py` with bumped `verified:`; `docs/subsystems/skills.md` lists `p:sandbox-run` with bumped `verified:`; `reindex.py --check` passes; `docs/subsystems/scripts.md` is untouched (GRAFT 3).
- [ ] **(I2)** The new skill DIRECTORY is bare `ClaudeCode/skills/sandbox-run/` (NOT `p:sandbox-run/`) and frontmatter `name:` is bare `sandbox-run`; the `p:` prefix is plugin-applied at invocation time (`ARCHITECTURE.md:23,37`) — the stale `skills.md` prose (`:21,:36`) was not copied; that stale prose is flagged as a candidate for a SEPARATE later doc fix (out of scope here) and left untouched (LOW).
- [ ] ADR `docs/adr/0005-approve-the-wrapper-not-the-command.md` added, including the accepted-assumption note that the realpath→exec TOCTOU on the wrapper token is OUT of the §0 adversary model — documented as an assumption, not a defense (INFO).
- [ ] No third-party imports anywhere; plugin ships no `hooks` block (confirm `plugin.json` unchanged).

---

_Verification note: all cited file:line references were re-read at HEAD via `purity_call`/`Read` for this synthesis. Stale anchors from the source drafts were corrected: `docs/subsystems/hooks.md` roster is at `:26-36` (not `:26-37`) and confirmed to OMIT `mcp-first-guard.py`; `docs/subsystems/skills.md` Notable-skills table is at `:42-51` (`verified:` `26200d1`/`2026-07-31`); `docs/subsystems/scripts.md` `sources:` is `Scripts` (`:7-8`) — confirming it is out of scope; the next free ADR number is `0005`; the forge `syntax` target globs `*.py` under `Scripts`/`tests`/`ClaudeCode/hooks` only, so it does NOT compile-check the extension-less `sbx` helper under `ClaudeCode/skills/` (the drafts implied it did). Two honest markers remain: an `<!-- UNVERIFIED -->` on the PreToolUse `cwd` payload field (the existing hook never reads `cwd`; the gate fails safe to prompt if it is absent, so the assumption can never cause a false-allow) and on SBPL last-match-wins (to be confirmed by the live self-test secret-READ probe d, Step 10 — probe-verified not CI-verified, M2), plus a `<!-- GAP -->` on the absence of any in-repo precedent for a hook emitting `permissionDecision:"allow"` (pinned by the Step-8 suite)._
