---
name: 0051-refresh-the-chrome-profile
type: roadmap-item
status: active
title: R-0051 · Refresh the Chrome profile
description: Closed roadmap item R-0051 (done 2026-10-02).
id: R-0051
state: done
horizon: later
origin: docs/reference/chrome-profile-refresh.md#the-procedure
blocked_by: []
tags: [scripts, search]
closed: 2026-10-02
commit: bdbf852d1f1a08f225ba35f8e047c82d9e2ab734
---

# R-0051 · Refresh the Chrome profile

## Why

The Chrome 153 profile is a pin (pinned_on 2026-09-30) and can only age; the mcp_chrome suite's profile-age row reports the age as INFO, never FAIL. When Chrome moves on, run the refresh procedure of the chrome-profile-refresh reference page: capture on loopback, export fixtures, edit the profile table until chrome_capture.py diff exits 0, regenerate the hosts, re-gate, retire the old fixture directory, and record the new pin as a further addendum on ADR 0004.

## Log

- 2026-10-01 new->later: follow-up of R-0044 (task-056)
- 2026-10-02 later->done: commit bdbf852d1f1a08f225ba35f8e047c82d9e2ab734
