---
name: 0007-a-path-spelled-deny-protects-the-spelling
type: adr
status: active
title: A path-spelled deny protects the spelling, not the file
description: Decision to open the ~/.claude read-deny fail-closed (two named subdirectories rather than a list of secrets to hide), and to close the symlink name-divergence with realpath-derived write-only shadow denies emitted as the profile's last file rules.
sources:
  - ClaudeCode/skills/sandbox-run/scripts/sbx
  - ClaudeCode/skills/sandbox-run/SKILL.md
verified:
  commit: 1b21d48
  date: 2026-08-10
links:
  - 0005-approve-the-wrapper-not-the-command
  - skills
  - hooks
---

# ADR 0007: A path-spelled deny protects the spelling, not the file

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the [[skills]] `p:sandbox-run` skill and its SKILL.md.

## Context

[[0005-approve-the-wrapper-not-the-command]] shifted the Bash trust boundary onto
one audited containment wrapper and gave it a secret deny set: `~/.ssh`, `~/.aws`,
`~/.gnupg`, `~/.config/gh`, `~/.claude`, and every ancestor `.git/config`, each
denied **both read and write**. That set was written once and never exercised
against the way the wrapper is actually used.

Two things were wrong with it, and neither was visible from the code.

**It made the feature useless for its most common case.** `~/.claude` holds the
project's own tooling — the skill scripts, the task utilities, the checkpoint
helper. Denying it for *read* meant the interpreter could not even load them:

```
sbx -- python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py mission
  → [Errno 1] Operation not permitted
```

A sandbox that refuses the commands you actually run is not a conservative
sandbox, it is an unused one. The observable symptom was that the wrapper went
unused after it shipped — not from distrust, but because the natural invocation
failed and the alternative (a bare Bash call) was already allow-listed and free.

**It protected a name, not a file.** On a plugin-developer host `~/.claude` is a
symlink farm into the project: `hooks` → `<repo>/ClaudeCode/hooks`, `scripts` →
`<repo>/Scripts`, `skills/p` → `<repo>/ClaudeCode`, `CLAUDE.md` likewise. The
deny is spelled `~/.claude`, so it never matched the repo spelling of the same
inodes — and `--write .`, the wrapper's own documented example, made all of them
writable. Measured, with a probe run inside the sandbox:

```
WROTE     <repo>/ClaudeCode/hooks/ (marker)
DENIED    ~/.claude/hooks/ (marker)          ← the same directory
OPENABLE  ClaudeCode/hooks/sbx-gate.py       ← the LIVE gate, append mode
```

A sandboxed command could rewrite the very hook that auto-allowed it, and the
next Bash call would run the rewritten gate. That is the exact invariant 0005
names as load-bearing.

## Decision 1 — open the carve-out fail-closed

`~/.claude` stays **read+write denied in full**. Exactly two subdirectories are
re-allowed for **read only**, emitted after the deny so they win: `~/.claude/skills`
and `~/.claude/scripts`.

The rejected alternative was the intuitive one: keep the read-deny narrow by
*listing the secrets* — `settings*.json`, `.credentials.json`, `hooks/`. It was
rejected because it is **fail-open**. That list already omitted `~/.claude/projects`,
the complete session-transcript history, and every file that lands under
`~/.claude` in the future would have defaulted to readable. Naming what to OPEN
costs one line per genuine need and leaves the default at denied; naming what to
HIDE silently widens every time the directory grows.

## Decision 2 — shadow write-denies derived from realpath

`sbx` walks the direct children of `~/.claude` and of each carve-out, and for every
symlink whose target leaves `~/.claude` it emits a **write-only** deny on the
resolved target. These are the profile's **last file rules**, so no later allow can
undo them.

Write-only, not read+write: a read-deny on those targets would cancel the carve-out
it was meant to complement and would also close the repo to the sandbox. The asset
being protected is *executable configuration* — hooks, MCP servers, skill scripts —
and the threat is modification, not disclosure. The plugin's source is public
content in this very repository.

Matching on inodes rather than paths was considered and is not available: neither
Seatbelt SBPL nor bwrap accepts an inode, both take paths. Deriving the second
*path* for the same inode is the closest reachable equivalent, which is what
`realpath` gives.

## Consequences

Under `--write .` the plugin's own executable configuration (`ClaudeCode/`,
`Scripts/`) is **read-only** inside the sandbox. This is intended, not a
regression: those files are edited with ordinary tools, and a sandboxed command has
no business writing the code that contains it.

The deny set is now host-shaped — it resolves differently on a symlink-farm
developer install than on a copy-deployed one. That is correct (the escape only
exists where the farm does) but it means the emitted policy is no longer
reconstructible from the flags alone. `--dry-run` prints all three sets for that
reason.

One defect was introduced and is recorded rather than hidden: the carve-out is a
*subtree* re-allow emitted after the per-file `.git/config` masks, so a
`.git/config` whose realpath sits under a carve-out loses its deny. Unreachable on
a symlink-farm host (the realpaths diverge), reachable on a copy-deployed install
with the cwd inside it.

## What the measurements changed

Two explanations that were written down before being measured turned out to be
false, and both were caught before they reached a commit:

- *"The deny fires during path traversal."* Half right. Seatbelt matches the
  terminal node canonically **and** checks each symlink at its own un-followed
  path — that second check is what killed the tooling. Plain ancestor traversal is
  not gated; if it were, the carve-out could not work at all, since its own parent
  stays denied.
- *"bwrap's tmpfs buries the carve-out's mount source."* False. bubblewrap resolves
  an op's source in the pristine pre-pivot root and its dest in the evolving
  newroot, so a later `--ro-bind` sees the untouched host path.

A third measurement settled the question the carve-out actually raises: a symlink
planted by the sandboxed process itself, inside its own writable scratch scope and
pointing at `~/.claude`, is still denied on read. The carve-out opens no lateral
path back into the masked parent.

## Two bugs fixed in the same pass

Neither is a design decision, but both come from the same root — a rule that was
believed to work because nothing exercised it.

`--net` was **inert on macOS**. The profile opens with `(deny default)`, and the
network was granted by *omitting* `(deny network*)` — which grants nothing under a
default-deny profile. It now emits an explicit `(allow network*)`. Fail-closed
throughout, so this was a broken feature, not a hole. On Linux the flag always
worked, because dropping `--unshare-net` is a real grant.

bwrap imported the host `/proc` through the recursive `--ro-bind / /` with no
`--unshare-pid`/`--proc`, so `/proc/<pid>/root/...` read around every mask. Both
flags are now emitted; `--unshare-pid` is not optional, since the kernel only
grants a fresh procfs to a process owning its PID namespace.
