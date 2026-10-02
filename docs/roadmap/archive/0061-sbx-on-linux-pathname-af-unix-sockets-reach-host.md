---
name: 0061-sbx-on-linux-pathname-af-unix-sockets-reach-host
type: roadmap-item
status: active
title: R-0061 · sbx on Linux: pathname AF_UNIX sockets reach host daemons
description: Closed roadmap item R-0061 (done 2026-10-02).
id: R-0061
state: done
horizon: next
origin: docs/roadmap/archive/0003-prove-sbx-seccomp-end-to-end-through-bwrap-on-a.md
blocked_by: []
follows: R-0003
tags: [sandbox, security]
closed: 2026-10-02
commit: 329653de59f4d3119706d4ac5429980284bb0780
---

# R-0061 · sbx on Linux: pathname AF_UNIX sockets reach host daemons

## Why

Found by the security re-verify of 4061b15 (out of that delta). `--ro-bind / /` does not stop connect() on a pathname socket, and --unshare-net/--unshare-ipc do not cover them, so a contained command can talk to e.g. /var/run/docker.sock (root-equivalent for a docker-group user) or the D-Bus session bus at /run/user/<uid>/bus. Decide whether this is in the sbx contract; if not, mask the known socket directories (e.g. --tmpfs /run/user/<uid>, a mask over /var/run/docker.sock) or deny connect() under --seccomp. Not measured yet.

## Log

- 2026-10-02 new->unset: added
- 2026-10-02 unset->next: User 2026-10-02: fix it.
- 2026-10-02 next->done: commit 329653de59f4d3119706d4ac5429980284bb0780
