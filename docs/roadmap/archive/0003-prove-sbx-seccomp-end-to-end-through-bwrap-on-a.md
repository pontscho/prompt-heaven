---
name: 0003-prove-sbx-seccomp-end-to-end-through-bwrap-on-a
type: roadmap-item
status: active
title: R-0003 · Prove sbx --seccomp end to end through bwrap on a Linux host
description: Closed roadmap item R-0003 (done 2026-10-02).
id: R-0003
state: done
horizon: now
origin: docs/adr/0005-approve-the-wrapper-not-the-command.md#consequences
blocked_by: []
tags: [sandbox]
closed: 2026-10-02
commit: 4061b15f9b369c216bb2eb5c022f433ab141bd06
---

# R-0003 · Prove sbx --seccomp end to end through bwrap on a Linux host

## Why

The x86_64 syscall allowlist behind sbx --seccomp shipped (see ADR 0005's R-0003 addendum) and was validated in-process with prctl on t42, but the real path -- bwrap receiving the program via --seccomp FD and installing it -- is unproven. t42 cannot run it: bubblewrap is not installed, unprivileged_userns_clone=0 and apparmor_restrict_unprivileged_userns=1. The user named the host for it on 2026-10-02: the Linux machine called para. Run seccomp_probe.py under sbx --seccomp there, compare with a control run without the flag, clean up.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->next: User 2026-09-29: go.
- 2026-09-29 next->now: User 2026-09-29: go.
- 2026-09-29 now->now [idea->active]: User 2026-09-29: go.
- 2026-09-29 now->now: edited title, why: Filter shipped in 95f5692; only the bwrap end-to-end proof remains, pending a host with bwrap.
- 2026-10-02 now->now: edited why: User 2026-10-02: run it on the Linux host para.
- 2026-10-02 now->done: commit 4061b15f9b369c216bb2eb5c022f433ab141bd06
