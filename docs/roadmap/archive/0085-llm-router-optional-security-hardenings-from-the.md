---
name: 0085-llm-router-optional-security-hardenings-from-the
type: roadmap-item
status: active
title: R-0085 · llm-router: optional security hardenings from the 2026-10-07 review
description: Closed roadmap item R-0085 (done 2026-10-07).
id: R-0085
state: done
horizon: unset
origin: user:2026-10-07:llm-router-optional-hardenings
blocked_by: []
severity: low
tags: [llm-router, oauth, security]
closed: 2026-10-07
commit: 225466c0e71a10117189be0841870d62df4a2bf3
---

# R-0085 · llm-router: optional security hardenings from the 2026-10-07 review

## Why

The 2026-10-07 code-mode security review of the codex/openai kinds and the _mcp_oauth source (docs/reviews/security-review-20261007-123500.md, verdict APPROVE) fixed all five verified LOW findings. Its verifiers also recorded optional hardenings next to SUPPRESSED verdicts; none is a finding, and these were not done:

- F6: count zero-byte callback connections against the bad-connection budget of the login callback acceptor.
- F10: refuse duplicate keys in _rt_loads (no differential parser exists today).
- F12: an OAuth access token sent over non-loopback http with allow_private has no cleartext opt-in, unlike the api_key path (CWE-319, undeclared).
- F15: log only the role's type name in the validator refusal DEBUG line.
- F22: bound tool-schema recursion depth in _rt_rs_schema and answer 400 instead of a RecursionError 500.
- F23: require err.param to equal the matched name, when present, before learning an unsupported sampling parameter.
- F26: refuse an upstream tool name that was not offered.
- F27: pop the adapter's pending entry in a finally on error exits.
- F30: a fixed text for an OAuth-backend 403 instead of the scrubbed upstream message.
- F33: test the login without the api.connectors scopes.
- F35: a scope fallback on refresh; check azp when aud is multi-valued.
- F37: reuse the _rt_cfg_oauth length and auth_token-collision checks when a disk token is adopted.
- F39: documentation nuance -- access-token claims are trusted on the TLS channel, not under OIDC Core 3.1.3.7.
- F42: pass the OAuthError kind through _log_value at the refresh-failure log line.
- F45: a smaller body cap when reading an upstream 400 error body (today the 64 MiB non-2xx read path).
- F51: refuse a device-flow user_code longer than about 64 characters.
- F52: print an only-enter-this-code-if-you-just-started-this-login warning next to the device code (phishing).

F41 (refresh token in the OS keychain) has its own item. Each is small; pick them up one at a time, red first where testable.

## Log

- 2026-10-07 new->unset: proposed by security review 2026-10-07
- 2026-10-07 unset->done: commit 225466c0e71a10117189be0841870d62df4a2bf3
