# prompt-heaven — project instructions

## The wiki

`docs/` is this repo's wiki — the **WHY the code cannot** carry: decisions, the
alternatives that lost, measurements, rationale. The source is authoritative for
WHAT and HOW; it can never tell you why a threshold is 0.55, or which two designs
were measured and rejected before this one.

The **trigger** for consulting it — which questions oblige a `wiki_call search`
before you answer — lives in the `wiki_call` tool description, which is loaded on
every request. It is deliberately not repeated here; the reasoning is in
`docs/adr/0003-the-trigger-travels-with-the-tool.md`.

## The catalogue

@docs/INDEX.md

**The index says what exists, not how fresh it is** — by design: a generated file
cannot track HEAD, so it makes no freshness claim you could mistake for one
(`docs/adr/0002-index-claims-no-freshness.md`). For the git-measured state ask
`wiki_call search` (it labels every hit against git) or `wiki_call freshness`.
