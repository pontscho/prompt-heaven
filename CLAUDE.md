# prompt-heaven — project instructions

## The wiki is not optional reading

`docs/` is this repo's wiki, and it carries the **WHY the code cannot**:
decisions, the alternatives that lost, measurements, rationale. The source is
authoritative for WHAT and HOW — it can never tell you why a threshold is 0.55,
or which two designs were measured and rejected before this one.

**Call `wiki_call search` BEFORE, not after:**

- before answering a WHY question (why is it like this, why did we pick X, what
  was the alternative);
- before a design decision inside a subsystem that already exists;
- before reconstructing intent from source, `git log`, or a checkpoint that
  refers to a decision whose reasoning is not in front of you.

It sits behind a calibrated relevance gate, so it can answer *"no page passes the
relevance gate"* — **that is a real answer, not a failure.** One call settles
whether the wiki knows anything; if it says no, stop asking it and go to the code.

Not required for a pure WHAT question (what does this function do → read the
code), and not before every file read. This is about **intent**, not lookup.

Why the rule exists, measured rather than assumed: three sessions went into making
`search` answer well — relevance gate, query stopwords, section slices, line
windows, git-measured freshness labels, a type ranking signal, an alias synonym
field. The answers got much better and the wiki still went unasked. In the very
session that shipped the last of those, **zero** wiki calls were made while the
wiki's own server code was being read, and a memoization model that a wiki page
describes was re-derived from source instead. Answer quality was never the missing
signal: a knowledge base whose contents nobody knows does not get consulted, and
no rule had ever named it.

## The catalogue

@docs/INDEX.md

**Treat the `[current]` labels above as UNVERIFIED.** They copy the hand-written
frontmatter `status:` field, and that field is measurably wrong — git says 9 of the
10 pages are stale while the index prints `[current]` for all 10. For the real,
git-measured state use `wiki_call search` (it labels every hit against git) or
`wiki_call freshness`; never this file.
