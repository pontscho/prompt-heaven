---
name: 0050-the-linux-profile-question-one-macos-chrome
type: roadmap-item
status: active
title: R-0050 · The Linux profile question: one macOS Chrome profile over a Linux TCP stack
description: Closed roadmap item R-0050 (dropped 2026-10-02).
id: R-0050
state: dropped
horizon: unset
origin: docs/adr/0004-never-pin-a-browser-impersonation-version.md#addendum-2026-10-01-the-impersonation-branch-is-superseded-by-one-pinned-chrome-153-profile-adr-0026
blocked_by: []
tags: [linux, scripts, search]
closed: 2026-10-02
reason: User 2026-10-02: not needed -- the macOS Chrome profile is enough; a Linux-specific profile would leak the platform.
---

# R-0050 · The Linux profile question: one macOS Chrome profile over a Linux TCP stack

## Why

There is exactly one browser profile, Chrome 153 on macOS, and it is announced on every host. On Linux the User-Agent and sec-ch-ua-platform therefore say macOS over a Linux TCP stack: the UA-vs-OS incoherence that spec-ddg section 2.7 found, and that the old primp Linux branch and Linux-only ladder existed to avoid, returns there. The ADR 0004 addendum of 2026-10-01 accepts it rather than fixing it, and ADR 0026 lists a second browser profile as out of scope. The open question is whether to capture and ship a Linux Chrome profile (a second capture set, a profile selector keyed on the host OS), and whether the incoherence measurably costs blocks on a Linux host at all before paying for one.

## Log

- 2026-10-01 new->unset: follow-up of R-0044 (task-056)
- 2026-10-02 unset->dropped: User 2026-10-02: not needed -- the macOS Chrome profile is enough; a Linux-specific profile would leak the platform.
