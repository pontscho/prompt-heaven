---
name: 0020-refuse-wikilinks-and-html-comments-in-roadmap-why
type: roadmap-item
status: active
title: R-0020 · Refuse wikilinks and HTML comments in roadmap why prose
description: Closed roadmap item R-0020 (dropped 2026-09-29).
id: R-0020
state: dropped
horizon: later
origin: user:2026-09-28:roadmap-why-markup
blocked_by: []
severity: low
tags: [roadmap, security]
closed: 2026-09-29
reason: Dropped by the R-0024 review: the effects are cosmetic or advisory (a harvested wikilink hides an orphan in an advisory report; an unclosed HTML comment distorts only a rendered view), roadmap.py's own parser is unaffected, every harvested why is user-approved, and refusing wikilinks would forbid a legitimate link from a why to its source ADR.
---

# R-0020 · Refuse wikilinks and HTML comments in roadmap why prose

## Why

Security review 2026-09-28 (F5, LOW): check_why lets a harvested [[slug]] through, which reindex counts as a link and so hides a real orphan; an unclosed HTML comment or a setext heading distorts the rendered page.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->dropped: Dropped by the R-0024 review: the effects are cosmetic or advisory (a harvested wikilink hides an orphan in an advisory report; an unclosed HTML comment distorts only a rendered view), roadmap.py's own parser is unaffected, every harvested why is user-approved, and refusing wikilinks would forbid a legitimate link from a why to its source ADR.
