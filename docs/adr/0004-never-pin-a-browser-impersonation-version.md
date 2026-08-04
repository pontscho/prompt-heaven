---
name: 0004-never-pin-a-browser-impersonation-version
type: adr
status: active
title: Never pin a browser impersonation version; the two backends are asymmetric on purpose
description: Decision to forbid pinned impersonation names on primp while tolerating them on curl_cffi, because primp rots silently into a random browser, and to make the retry ladder escalate by engine on a platform-dependent list.
links:
  - spec-ddg
  - scripts
---

# ADR 0004: Never pin a browser impersonation version

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is [[scripts]] and the measurement log in [[spec-ddg]].

This page deliberately carries no `sources:`/`verified:` frontmatter, which
SCHEMA §3 makes optional for an `adr`. A decision's rationale does not expire
when the code it governs is edited, and binding a frozen page to a churning file
produces exactly the false positive visible today on
[[0001-purity-server-unification]] — an append-only page that gates CI whenever
`Scripts/mcp-purity.py` moves. A freshness signal that cries wolf teaches people
to ignore freshness.

## Context

Three files impersonate a browser through two libraries: `primp` on Linux
(because it has `impersonate_os`) and `curl_cffi` everywhere else. Until
2026-08-04 the primp side named four exact Chrome majors —
`chrome_133`/`131`/`130`/`128` — and paired them with a hand-written Linux Chrome
header dict whose version string was kept "coherent" with the pin.

Measured against the installed primp 1.3.1 on 2026-08-04: **all four names are
gone.** primp 1.3.1 dropped everything below `chrome_144`. The pins were written
on 2026-05-24 and were already stale that same day — [[spec-ddg]] records real
Chrome at 148 on that date, so a pin at 133 was fifteen majors behind the day it
landed.

A stale pin would be a maintenance nuisance. What actually happened is worse,
and it is the whole reason this decision exists: **primp does not raise on an
unknown impersonate name.** It prints one line — `Impersonate 'chrome_133' does
not exist, using 'random'` — to stderr and silently substitutes a *random*
browser. Asking for Chrome 133 on Linux put a **macOS Safari 26.3** fingerprint
on the wire while the hand-built dict was still announcing Linux Chrome 133. That
is a self-contradicting fingerprint: worse than no impersonation at all, because
it fails the very UA-vs-OS coherence check the Linux branch exists to satisfy
([[spec-ddg]] §2.7). It was invisible on macOS, since only the Linux branch takes
that path.

Two properties of primp make the failure undiagnosable from inside the process:

- It offers **no way to enumerate** its valid names.
- Its `Client.impersonate` property **echoes the input back**. Measured, it
  reports `chrome_133` — a name that does not exist — while the client
  impersonates something else entirely.

There *is* an indirect readback: `client.headers["user-agent"]`, read straight
after construction, reveals the real profile with no network call and matches the
wire.

`curl_cffi` fails the opposite way. An unknown name raises `ImpersonateError`
when the request goes out — noisy, attributable, fixed in one reading.

## Decision

**No impersonation name in this repo may name a browser version.** On primp the
rule is enforced: a frozen whitelist of the six bare aliases primp accepts —
chrome, firefox, edge, safari, opera, random — and anything else raises before a
packet leaves `Scripts/mcp-webfetch.py:PRIMP_ALIASES`. A bare alias cannot rot:
primp's `chrome` spans whatever majors the installed build supports and rotates
the major per client on its own (measured: 144, 145, 146, 147, 148 across fresh
clients), which is exactly what the hand-rolled rotation was reaching for.

**The curl_cffi pin stays, and the asymmetry is the substance of this decision,
not an inconsistency.** `Scripts/search_duckduckgo.py:CURL_CFFI_PROFILES` keeps
its four names because they are valid in curl_cffi 0.16.0 and are the exact
configuration the ~80% DDG pass-through of [[spec-ddg]] was measured with — and
because when they *do* rot, they rot loudly. The rule is not "pins are ugly", it
is **a failure mode must be visible to be tolerated.**

The validation sets differ accordingly and deliberately: curl_cffi names are
checked against `BrowserTypeLiteral.__args__` (53 entries — the *union* of
aliases and pinned names), so an explicit `profile="chrome146"` is accepted there
while `PRIMP_ALIASES` refuses the equivalent on Linux
`Scripts/mcp-webfetch.py:_create_session`.

**The retry ladder escalates by ENGINE, and its list is platform-dependent**
`Scripts/mcp-webfetch.py:IMPERSONATE_LADDER`. Bot detection that refuses one
engine's fingerprint often accepts another, so a second Chrome major is the least
informative thing to try next. Linux gets `("chrome", "edge", "firefox")`;
everywhere else `("chrome", "safari", "firefox")`. Measured: primp's `safari`
with `impersonate_os="linux"` still announces `Macintosh; Intel Mac OS X` in the
UA. There is no coherent Linux Safari to impersonate — Safari does not exist on
Linux — so putting it in the Linux ladder would reintroduce, on the *second*
attempt, exactly the incoherence the primp branch exists to remove. `chrome` and
`edge` both report `X11; Linux x86_64`. A caller who genuinely wants Safari from
a Linux host can still ask for it explicitly via `profile=`.

## Alternatives Evaluated

### Option 1 — Keep the pins on both backends and bump them on a schedule
- **Pros:** a pin is reproducible; two runs of the same code put the same
  fingerprint on the wire, which is what you want when a measurement is the
  point.
- **Cons:** falsified by this repo's own history. The pins were fifteen majors
  behind on the day they were written, and nobody noticed for ten weeks. The
  schedule that would have caught it does not exist and would have to be
  maintained against two independent upstream release cadences.

### Option 2 — Forbid pins on both backends, for symmetry
- **Pros:** one rule, no asymmetry to explain, nothing to get wrong.
- **Cons:** discards a measurement. `CURL_CFFI_PROFILES` is the configuration
  the ~80% pass-through was measured with; swapping it for aliases would
  invalidate that number for no defect. Symmetry is not a reason to give up a
  result, and curl_cffi's loud failure means the pin costs nothing to keep.

### Option 3 — Detect primp degradation at runtime instead of whitelisting
Read `client.headers["user-agent"]` after construction and parse it to confirm
the profile actually took.
- **Pros:** catches every degradation cause, including ones a whitelist cannot
  anticipate (a future primp dropping an *alias*). Network-free, and measured to
  match the wire.
- **Cons:** UA-string parsing is a second fingerprint-shaped thing to maintain,
  and it detects the problem *after* a client exists rather than refusing the
  name up front. A whitelist is cheaper, more direct, and fails at the call site.
  **Chosen against, not ruled out** — the readback is the right escalation if an
  alias is ever dropped, and it is recorded here so it need not be rediscovered.

### Option 4 — One retry ladder for every platform
- **Pros:** one tuple, nothing platform-conditional to reason about.
- **Cons:** whichever tuple you pick is wrong somewhere. Including `safari` puts
  a macOS UA on a Linux host on attempt two; excluding it gives up a genuinely
  different engine on macOS, where it is coherent and free.

## Consequences

- **The silent-degradation class of bug is now unreachable on primp.** An unknown
  name raises at the call site instead of quietly producing an arbitrary
  fingerprint. The two backends are validated against different sets, and that
  difference is now documented rather than looking like an oversight.
- **The sets are not identical in either direction, which is a trap worth
  naming:** curl_cffi's `REAL_TARGET_MAP` holds 9 alias keys including `tor`,
  which `BrowserTypeLiteral` omits — so `profile="tor"` is refused while
  `tor145` passes. An alias existing in one table does not mean it validates.
- **Reproducibility is traded away on Linux, knowingly.** A bare alias means two
  runs may impersonate different Chrome majors. That is acceptable here because
  the majors are all current, and it is precisely the wrong trade for a
  controlled measurement — anyone re-running a fingerprint capture should pin
  explicitly for the duration and record what they pinned.
- **`primp` cannot be audited from inside the process.** No enumeration, and
  `Client.impersonate` lies by echo. Any future check must go through the
  `client.headers["user-agent"]` readback; do not trust the property.
- **Deleted alongside the pins, for independent reasons:** the hand-built Linux
  Chrome header dict. Its premise ("primp does not auto-inject over HTTP/2") is
  false for 1.3.1, and client-level `headers=` loses every conflict against
  `impersonate=`, so it was already inert. The measurement it appeared to support
  is unaffected and still stands — see [[spec-ddg]], which records why.
- **Not changed:** `ROTATE_EVERY`, and the four `CURL_CFFI_PROFILES` names.
