#!/usr/bin/env python3
"""Mechanical suite for the `search` relevance gate, the `get_page` section
index, the `source_to_pages` per-hit description and the MEASURED state in
every recall reply's `[type/state]` label, in Scripts/mcp-wiki.py
(73 cases, A-L).

Drives `handle_wiki_call` IN-PROCESS against a SYNTHETIC six-page wiki built in
a temp workspace -- never the repo's real docs/.  Nothing is written outside
mkdtemp.

Two spawns exist, both in group L and both deliberate.  (1) `git show
HEAD:Scripts/mcp-wiki.py`, which is the only way to compare the worktree's
`freshness` output against the code that shipped.  (2) The unpatched-driver
case, which MEASURES what the recall path costs in a directory git does not
track -- production spawns there, so a stub would hide the finding.  Every
other group-L driver has the git boundary replaced by a lookup table, so
`stale` and `orphaned-source` are reachable offline and deterministically.

The fixture is not decoration.  It reproduces, in miniature, the measured
pathologies the gate and the query-side stoplist exist for:

  * one token (`mcp`) sits in EVERY page, so "zero terms matched" -- the only
    silencing condition the pre-gate search had -- can never fire.  It is also
    what keeps a should-be-silent query LEXICALLY matching once the function
    words are dropped: every page still enters the candidate set, so the refusal
    has to come from the COVERAGE GATE rather than from the "nothing matched"
    arm.  That is the shape measured on the real wiki -- `mcp server output
    verbosity token cost`, where `output`/`token`/`cost` hit real pages and
    `verbosity` exists nowhere, coverage 49%, gate refuses.
  * the DISCRIMINATING term of a should-be-silent query (`telemetry`, `vendor`)
    is absent from the corpus, and a df-0 term takes the MAXIMUM idf, so it
    drags coverage under the bar no matter how many ubiquitous tokens the query
    also hits.
  * one page (detour-notes) carries ONLY the function words of a question
    ("why did we ...") and NONE of its content terms, and those function words
    are RARE in a six-page corpus, so their idf is near the maximum: measured,
    idf(`why`) = 1.540 while idf(`mcp`) = 0.074, so pre-stoplist the page that
    answers nothing outranked every page that answers something.  That is the
    small-corpus inversion, and it is the reason QUERY_STOPWORDS exists.  Group D
    still MEASURES it on the raw term list (the only place it is visible now);
    group I asserts the drop defuses it -- the decoy no longer wins.
  * `zzqx wibblefrotz` matches nothing lexically at all, so the "no matching
    pages" arm stays covered and group B's distinction stays real rather than
    hypothetical.

Everything numeric is DERIVED, never typed:
  * the gate value comes from `mod.DEFAULT_MIN_COVERAGE`;
  * the stoplist comes from `mod.QUERY_STOPWORDS` -- group I keeps no copy of
    it, because a hardcoded list would still pass after somebody swapped in a
    standard stoplist, which is the exact edit group I exists to catch;
  * the calibration window in group D is MEASURED by driving the server at
    `min_coverage: 0.0` and parsing `cov N%` off the rendered hit lines;
  * group F's exact coverages are recomputed from the server's OWN building
    blocks (`_tokenize`, `QUERY_STOPWORDS`, `_build_corpus_cached`,
    `_prefix_count`, the idf formula), so the floor-vs-round claim is checked
    against a real float instead of a constant somebody once observed.

Coverage by group:
  A  the gate can be SILENT: a query whose discriminating term is unknown to
     the corpus returns zero hits, names the unknown term, and says why -- while
     still matching every page lexically, so it is the GATE that refuses
  B  nothing-matched and gated-out are DIFFERENT silences.  The caller's next
     move differs -- rephrase versus "this topic is undocumented" -- so the two
     sentences must never be confusable
  C  the gate does not kill real answers: every query with a genuine answer
     still returns it, top hit slug AND page type included -- including one
     query carrying a df-0 term that the gate must SPARE because the winner
     answers enough of the rest
  D  the calibration window, MEASURED, not typed:
       max(best coverage over the should-be-silent queries)
         < DEFAULT_MIN_COVERAGE
         <= min(coverage of the should-answer winners)
     A CLOSED window is a hard failure, and deliberately so: it means the two
     populations are no longer separable by any threshold, so the gate is not
     mis-tuned, it is unsalvageable
  E  `missed:` speaks only when it has something to say, and names exactly the
     unmatched terms -- no more, no fewer; `unknown to the corpus:` appears
     only when some query term has df 0
  F  percentages are FLOORED, never rounded, so a rendered number can never
     claim a bar it missed: `need 99%` for min_coverage 0.999, `cov 78%` for a
     true 78.759%, and no excluded page ever renders as admissible
  G  `min_coverage` is honored: 0.0 restores the pre-gate behaviour (the
     regression escape hatch), 1.0 admits only pages matching every term, the
     dispatcher accepts the param, and garbage falls back to the default
  H  hygiene: the run leaves no __pycache__ behind
  I  the query-side stopword drop: it is DISCLOSED and names exactly the words
     it dropped, it stays silent when there was nothing to drop, an
     all-function-words query gets a THIRD silence of its own (all three are
     mutually distinguishable), the drop actually re-orders the ranking so the
     function-word decoy stops winning, and the two deliberate NON-drops
     (`search`, a function name here; `done`, a plausible frontmatter status)
     still reach the search
  J  `get_page`'s SECTION INDEX -- the answer to "your section did not match":
     both no-body branches list what IS there, the line numbers are FILE-
     relative (each one checked against the fixture file itself), every
     advertised size equals the slice `section:` actually serves, the depth hint
     is mechanically honest (re-calling at the depth it names empties it and
     shows exactly the promised extra headings), a heading whose slice IS the
     page is never offered (measured per page, not assumed from its level: two
     H1s bound each other and BOTH are listed), the ORDER of the two skip tests
     is pinned on the one page that can see it, and the two WORKING branches --
     default body, successful extraction -- stay free of the new block.  Two cases pin what the index
     does when it CANNOT be complete: an unreadable file drops the L and NOTHING
     else (the block is the readable one minus its `L<n>, ` prefixes, byte for
     byte), and the two empty answers stay different sentences -- a page with no
     headings and a page whose only heading is its title imply opposite next
     moves, so a merge of the two must fail here
  K  `source_to_pages` now says WHAT each page covering a source has to say, not
     merely THAT it covers it: one `description:` line per hit, verbatim from
     the frontmatter, above the anchor lines and indented with them.  The group
     asserts the line, its POSITION (by line index, not by presence), its indent
     (derived from the sibling anchor line, never typed), the SILENCE on a page
     that has no `description` key -- with the missing key asserted as the
     premise -- and, strongest, that the rest of the answer did not move: the
     reply with its description lines filtered out is BYTE-IDENTICAL to what the
     same corpus renders with the `description:` frontmatter lines removed
  L  the state in `[type/state]` is MEASURED against git, not read from the
     frontmatter `status:` field.  The defect was measured on the real wiki:
     `freshness` said 9 of 10 pages stale while all 10 wrote `current`, and
     `search` rendered the field -- so one query answered with four hits
     labelled `current`, all four of them stale.  The group pins the label (a
     moved anchor renders `stale`; a page with no anchors renders one of the
     NOT-CHECKABLE states and never `current`; all eight states are reachable),
     the disclosure (said ONCE, right N of M, silent on a corpus whose field
     agrees and on a page that has no field to disagree with), the `status`
     param (it now selects on the measurement, so a value the field claims but
     the measurement does not returns nothing), the AGREEMENT of the two recall
     callers (`search` and `source_to_pages` render one classifier's answer,
     which is also, dict for dict, what `freshness` publishes), the two
     invariants of the cross-call diff memo (it spans calls, it dies with HEAD,
     and two repos never share one changed-file set), and the two claims the
     refactor itself makes: `freshness` is byte-identical to the committed
     server, and moving the `status` filter to AFTER the scoring loop moved no
     score, no coverage and no `best coverage N%`

Group J runs on its OWN six-page fixture in a SECOND workspace, group K on its
own five-page one in a THIRD (plus a FOURTH that mirrors it), and group L on a
FIFTH (the frontmatter disagreeing), a SIXTH (the same pages with the field
written to agree) and a SEVENTH (a synthetic `.git/HEAD`, the only way the diff
memo can be keyed).  That is not tidiness.  The six pages above ARE the
calibration window group D measures: one more page moves `n_docs`, moves every
term's idf, and shifts -- or closes -- the window, so a page added here for a
`get_page` test would fail group D for a reason that has nothing to do with
search.  The fixtures never mix.

Usage:
  python3 tests/test_wiki_recall.py
  python3 tests/test_wiki_recall.py --brief
Exit code 0 iff every non-informational case passes.
"""

import math
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "wiki_recall"
SERVER = H.repo_path("Scripts", "mcp-wiki.py")

WIKI_REL = "wiki"


# ---------------------------------------------------------------------------
# The synthetic corpus.
#
# Token placement is the whole design, so it is spelled out here rather than
# left to the prose:
#
#   df 6 : mcp (every page, via `sources` and the bodies), the (every page)
#   df 3 : search
#   df 2 : coverage, gate, field, frontmatter
#   df 1 : why, did, we, replace, pipeline, calibration, threshold, relevance,
#          bm25f, boost, slug, dispatcher, cache, signature, tokenizer
#   df 0 : telemetry, quantile, vendor, grafana, dashboard, done, zzqx,
#          wibblefrotz
#
# `mcp` is the load-bearing one twice over: it defeats "zero terms matched", and
# because it is not a function word it SURVIVES the query-side stoplist -- which
# is what keeps a should-be-silent query in the candidate set at all now, so the
# coverage gate (not the nothing-matched arm) is what does the refusing.
#
# `why`, `did` and `we` live on ONE page only -- the decoy -- which is why they
# are worth 1.540 each while `mcp` is worth 0.074.  Every other page therefore
# avoids any token BEGINNING with "we" (no "weight", no "were", no "well"):
# matching is by prefix, so a single "weighting" elsewhere would collapse the
# inversion this fixture exists to reproduce.  Group D re-measures the df table
# on every run, so a drift shows up as a named failure rather than as silence.
#
# `cache` + `signature` sit on corpus-cache and `tokenizer` sits on the decoy
# ALONE: that split is group I's ranking contest, and it only proves anything
# because neither side carries any of the other's terms.  `done` is absent from
# every page on purpose -- being reported `unknown to the corpus` is how the
# suite OBSERVES that the server did not silently drop it as a stopword.
# ---------------------------------------------------------------------------

P_GATE = ("relevance-gate.md", "relevance-gate", "adr", """\
---
name: relevance-gate
title: ADR: the coverage relevance gate
type: adr
status: accepted
description: The coverage threshold that lets a ranked lookup admit it has no answer.
sources:
  - Scripts/mcp-wiki.py:_fn_search
  - Scripts/mcp-wiki.py:DEFAULT_MIN_COVERAGE
---

# Decision

The ranked lookup now carries a coverage gate. Coverage is the share of the
query idf mass a page actually answers, and a page under the threshold is
dropped before the limit is applied.

# Calibration

The calibration window came from measurement, not from taste: the loudest
false answer topped out under the bar and the faintest true answer sat above
it. The threshold is the middle of that window.

# Consequences

Silence is expressible at last. An mcp caller that gets nothing back learns
that the topic is undocumented, not that the ranking is shy.
""")

P_RANKING = ("bm25f-ranking.md", "bm25f-ranking", "spec", """\
---
name: bm25f-ranking
title: BM25F ranking and per-field boosts
type: spec
status: current
description: How the search score folds per-field boosts, length normalization and idf into one number.
sources:
  - Scripts/mcp-wiki.py:_prefix_count
  - Scripts/mcp-wiki.py:_build_corpus
---

# Scoring

Each search term is counted per field, boosted by that field's own factor, and
normalized on that field's average length. Saturation is applied once at the
end, so a long body cannot buy rank by repetition alone.

# Coverage

The score alone cannot separate a true answer from a plausible one, so the mcp
server reports coverage beside it. A raw term count was the first attempt and
it got replaced by the boosted form.

# Prefix matching

A term matches any token that begins with it, which is how a stem reaches its
inflections without a stemmer.
""")

P_SERVER = ("wiki-search-server.md", "wiki-search-server", "component", """\
---
name: wiki-search-server
title: The wiki search server
type: component
status: current
description: One dispatcher over the docs tree: page reads, listings and frontmatter parsing.
sources:
  - Scripts/mcp-wiki.py:handle_wiki_call
  - Scripts/mcp-wiki.py:iter_pages
---

# Shape

One mcp tool, one function parameter, one handler per function. The dispatcher
resolves aliases first, then rejects any parameter the handler does not accept.

# Pages

A page is a markdown file with frontmatter. The slug carries the identity and
the path does not. Body text is split from the frontmatter before either half
is tokenized for search.

# Listings

Listings group by type and honour a path prefix, so a caller can narrow to one
subtree without a second call.
""")

# The decoy: the fixture's DuckDuckGo page.  It owns `why`, `did` and `we`
# outright and carries none of the content terms of the questions those words
# appear in, so pre-gate it wins a query it cannot answer at all.
P_DETOUR = ("detour-notes.md", "detour-notes", "analysis", """\
---
name: detour-notes
title: Why did we take the long road to prefix matching
type: analysis
status: draft
description: A retrospective on the two designs we tried and then threw away.
sources:
  - Scripts/mcp-wiki.py:_tokenize
---

# The question

Why did the first two designs not survive? The honest answer is that neither
of them could ever say no.

# What we tried

We tried a plain token counter, and we tried a hand written stop list. Both
looked fine on a handful of pages and both fell apart once the corpus grew.

# What we kept

We kept the tokenizer and threw the rest away. The mcp server is smaller for
it, and nothing on this page tells you how the ranking behaves today.
""")

P_SNIPPET = ("snippet-pipeline.md", "snippet-pipeline", "concept", """\
---
name: snippet-pipeline
title: The snippet pipeline
type: concept
status: current
description: How a hit line picks its snippet, its section title and its anchor.
sources:
  - Scripts/mcp-wiki.py:_best_snippet
  - Scripts/mcp-wiki.py:_headings
---

# Stages

The pipeline runs in three stages: find the first body line that carries a
search term, trim it to a fixed width, then attach the nearest heading above
it as a section anchor.

# Fences

Headings inside a code fence are skipped, so a comment that looks like a
heading cannot capture a snippet. Fence tracking is gated on the opening
delimiter and its exact width.

# Fallback

When the only match sits in the header block, the mcp server falls back to the
description instead of printing an empty line.
""")

P_CACHE = ("corpus-cache.md", "corpus-cache", "reference", """\
---
name: corpus-cache
title: The tokenized corpus cache
type: reference
status: current
description: A stat only signature keyed cache over the parsed field tokens.
sources:
  - Scripts/mcp-wiki.py:_corpus_signature
  - Scripts/mcp-wiki.py:_build_corpus_cached
---

# Signature

The cache key is a stat only walk: one entry per page carrying the relative
path, the modification time and the size. No page is opened to build it.

# Invalidation

Any edit to any page moves the signature, so a stale index cannot outlive the
file it came from. An edit made by another process invalidates the cache just
as reliably as one made by this mcp server.

# What is cached

The parsed frontmatter, the per field token lists and the field lengths. The
query side keeps nothing at all.
""")

PAGES = [P_GATE, P_RANKING, P_SERVER, P_DETOUR, P_SNIPPET, P_CACHE]
SLUG_TO_REL = {slug: fname for fname, slug, _t, _x in PAGES}
DECOY_REL = P_DETOUR[0]
DECOY_SLUG = P_DETOUR[1]
# The page group I's ranking contest must be won by: it carries the CONTENT terms
# of a question whose function words all live on the decoy.
CONTENT_SLUG = P_CACHE[1]

# ---------------------------------------------------------------------------
# The queries.  Two populations, and the whole suite turns on keeping them apart.
# ---------------------------------------------------------------------------

# Should be SILENT -- and silenced by the GATE, which is a stronger claim than
# it looks.  Each query keeps LEXICAL matches on every page (`mcp` is in all six
# and survives the stoplist) while its DISCRIMINATING term is unknown to the
# corpus, so the candidate set is full and the refusal has to be earned on
# coverage.  A query whose survivors were ALL unknown would take the
# nothing-matched arm instead and prove something else entirely -- Q_NONSENSE
# below is there to cover that arm deliberately, not by accident.
#
# The function words are still in both queries on purpose: the refusal has to
# co-exist with the `ignored function words:` disclosure, and group D probes the
# decoy with Q_SILENT_NEAR's raw term list.
Q_SILENT_NEAR = "why did we replace the mcp telemetry pipeline"
Q_SILENT_FAR = "how does the mcp search vendor its grafana dashboard"
SILENT = [("silent-near", Q_SILENT_NEAR), ("silent-far", Q_SILENT_FAR)]
# The content terms of Q_SILENT_NEAR that DISCRIMINATE -- i.e. everything except
# the corpus-wide `mcp`.  The decoy must carry none of them.
NEAR_DISCRIMINATING = ("replace", "telemetry", "pipeline")

# Matches NOTHING, lexically -- a different silence with a different remedy.
Q_NONSENSE = "zzqx wibblefrotz"

# Should ANSWER, with the page and type each one is supposed to land on.
Q_GATE_ADR = "coverage relevance gate"
Q_RANK_SPEC = "bm25f field boost"
Q_COMPONENT = "frontmatter slug dispatcher"
Q_SPARED = "coverage gate calibration threshold quantile"
ANSWERS = [
    ("answer-gate-adr", Q_GATE_ADR, "relevance-gate", "adr"),
    ("answer-ranking-spec", Q_RANK_SPEC, "bm25f-ranking", "spec"),
    ("answer-page-component", Q_COMPONENT, "wiki-search-server", "component"),
    ("answer-spared-despite-unknown-term", Q_SPARED, "relevance-gate", "adr"),
]

# Winner coverage 0.787587 -> floors to 78, rounds to 79.  Group F's whole point.
Q_FLOOR = "calibration coverage search"

# ---- group I: the query-side stopword drop ---------------------------------
# A real question, mixing four function words with four content words, that
# still has an answer -- so the disclosure is checked next to a ranking rather
# than next to a refusal.
Q_MIXED = "why is the coverage gate in the mcp search"
# Nothing but function words: the THIRD silence, which must not borrow either of
# the other two sentences.
Q_ALL_FUNCTION = "why did we do that"
# The ranking contest.  `cache` + `signature` are on corpus-cache, `tokenizer` is
# on the decoy, and the decoy also owns `why`/`did`/`we` -- the words that used
# to hand it the query.  The content page has to win on content alone.
Q_DECOY = "why did we cache the tokenizer signature"
# The two deliberate NON-stopwords.  `search` has df 3 so its survival is visible
# as hits; `done` has df 0 so its survival is visible as `unknown to the corpus`.
# Neither observation is possible if the word is dropped before df is computed.
Q_KEEP = "search done"
KEEP_WORDS = ("search", "done")

# Every token whose df this fixture pins.  df is a per-term quantity, so one
# probe query measures the entire table in a single corpus pass.
PROBE_ALL = ("mcp the why did we replace pipeline coverage gate calibration "
             "threshold search relevance bm25f field boost frontmatter slug "
             "dispatcher telemetry quantile vendor grafana dashboard")

GATE_MSG = "no page passes the relevance gate"
NO_MATCH_MSG = "no matching pages"
ALL_FUNC_MSG = "the query is all function words"
UNKNOWN_MSG = "unknown to the corpus:"
IGNORED_MSG = "ignored function words:"
MISSED_PREFIX = "   missed: "


# ---------------------------------------------------------------------------
# rendered-answer parsing -- the suite asserts on PROPERTIES of the markdown,
# never against a whole expected blob
# ---------------------------------------------------------------------------

_HIT_RE = re.compile(
    r"^(?P<rank>\d+)\. \*\*(?P<title>.*?)\*\* — (?P<slug>.+?) "
    r"`(?P<anchor>[^`]*)`(?: \[(?P<meta>[^\]]*)\])?"
    r"  \(score (?P<score>[-\d.]+), cov (?P<cov>\d+)%\)$")

_HEADER_RE = re.compile(r"— (\d+) hit\(s\) in ")
_UNKNOWN_RE = re.compile(r"^unknown to the corpus: (.*)$", re.M)
_IGNORED_RE = re.compile(r"^ignored function words: (.*)$", re.M)
_GATE_RE = re.compile(GATE_MSG + r" \(best coverage (\d+)%, need (\d+)%\)")
# The third silence names the words it threw away, so the list is parsed out and
# compared term-by-term -- "it said something about function words" is not the
# contract, "it said WHICH ones" is.
_ALL_FUNC_RE = re.compile(
    r"^" + ALL_FUNC_MSG + r" \(([^)]*)\) — nothing left to search for$", re.M)


def _csv(raw):
    return [t.strip() for t in raw.split(",") if t.strip()]


def parse_answer(text):
    """Structured view of one rendered `search` answer."""
    lines = text.split("\n")
    hits = []
    for i, line in enumerate(lines):
        m = _HIT_RE.match(line)
        if not m:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        meta = m.group("meta") or ""
        hits.append({
            "rank": int(m.group("rank")),
            "title": m.group("title"),
            "slug": m.group("slug"),
            "anchor": m.group("anchor"),
            "type": meta.split("/")[0] if meta else "",
            "status": meta.split("/")[1] if "/" in meta else "",
            "score": float(m.group("score")),
            "cov": int(m.group("cov")),
            "missed": _csv(nxt[len(MISSED_PREFIX):])
            if nxt.startswith(MISSED_PREFIX) else [],
        })
    hm = _HEADER_RE.search(text)
    um = _UNKNOWN_RE.search(text)
    im = _IGNORED_RE.search(text)
    gm = _GATE_RE.search(text)
    fm = _ALL_FUNC_RE.search(text)
    return {
        "hits": hits,
        "header_hits": int(hm.group(1)) if hm else None,
        "unknown": _csv(um.group(1)) if um else [],
        "has_unknown_line": um is not None,
        "ignored": _csv(im.group(1)) if im else [],
        "has_ignored_line": im is not None,
        "best_pct": int(gm.group(1)) if gm else None,
        "need_pct": int(gm.group(2)) if gm else None,
        "has_gate_msg": GATE_MSG in text,
        "has_no_match_msg": NO_MATCH_MSG in text,
        "has_all_func_msg": ALL_FUNC_MSG in text,
        "all_func_words": _csv(fm.group(1)) if fm else [],
        "missed_lines": [ln for ln in lines if ln.startswith(MISSED_PREFIX)],
    }


class Driver:
    """One in-process wiki server pointed at the synthetic corpus.

    `server` exists for group L alone: the same class drives HEAD's copy of the
    server beside the worktree's, so "the extraction changed nothing" is a
    comparison against the code that shipped rather than against a description
    of it.  Each Driver holds its OWN module instance, which is what makes
    patching one module's git seam invisible to every other group.
    """

    def __init__(self, project_root, server=None, name="mcp_wiki_under_test"):
        self.mod = H.load_module_from_path(name, server or SERVER)
        self.root = project_root
        self.abs_wiki = os.path.join(project_root, WIKI_REL)

    def search(self, query, **params):
        payload = {"query": query}
        payload.update(params)
        res = self.mod.handle_wiki_call(
            {"function": "search", "params": payload},
            self.root, WIKI_REL)
        text = res.get("__raw_text__") or res.get("error") or ""
        out = parse_answer(text)
        out["error"] = "error" in res
        out["text"] = text
        out["params"] = payload
        return out

    def get_page(self, slug, **params):
        """One rendered `get_page` answer, parsed for its section index."""
        payload = {"slug": slug}
        payload.update(params)
        res = self.mod.handle_wiki_call(
            {"function": "get_page", "params": payload},
            self.root, WIKI_REL)
        text = res.get("__raw_text__") or res.get("error") or ""
        out = parse_page(text)
        out["error"] = "error" in res
        out["text"] = text
        out["params"] = payload
        return out

    def sources(self, source, **params):
        """One rendered `source_to_pages` answer, parsed for its hit blocks."""
        payload = {"source": source}
        payload.update(params)
        res = self.mod.handle_wiki_call(
            {"function": "source_to_pages", "params": payload},
            self.root, WIKI_REL)
        text = res.get("__raw_text__") or res.get("error") or ""
        out = parse_sources(text)
        out["error"] = "error" in res
        out["params"] = payload
        return out

    def frontmatter(self, fname):
        """The frontmatter of a fixture page, read back through the SERVER's own
        parser -- so a case can assert its own premise (a key really is absent)
        against the same reader the handler used, not against the string the
        test believes it wrote."""
        fm, _body = self.mod.read_page(os.path.join(self.abs_wiki, fname))
        return fm

    def freshness(self, **params):
        """The rendered `freshness` report, raw."""
        res = self.mod.handle_wiki_call(
            {"function": "freshness", "params": params}, self.root, WIKI_REL)
        return res.get("__raw_text__") or res.get("error") or ""

    def classify(self, relpath):
        """`_classify_page` for ONE page, driven the way the recall path drives
        it: the same repo root, the same cross-call diff cache, the same
        `changed_for` closure shape.  Group L's oracle for "one authority": what
        this returns must be, dict for dict, what `freshness` reports."""
        mod = self.mod
        repo = mod.repo_root(self.abs_wiki)
        cache = mod._recall_diff_cache(repo)
        fm, _body = mod.read_page(os.path.join(self.abs_wiki, relpath))
        return mod._classify_page(
            relpath, fm, repo,
            lambda c: mod._changed_files(c, "HEAD", repo, cache))

    def split_query(self, query):
        """(dropped, kept) for a query, split by the server's OWN stoplist.

        Reads `QUERY_STOPWORDS` out of the module under test instead of keeping a
        copy here: a suite that hardcoded the word list would still pass after
        somebody swapped in a standard stoplist, and that edit is exactly what
        group I exists to catch.
        """
        raw = list(dict.fromkeys(self.mod._tokenize(query)))
        stop = self.mod.QUERY_STOPWORDS
        return ([t for t in raw if t in stop], [t for t in raw if t not in stop])

    def measure(self, query, drop_stopwords=True):
        """Recompute df / idf / per-page coverage from the server's OWN parts.

        Not a second implementation of search: it reuses `_tokenize`,
        `QUERY_STOPWORDS`, `_build_corpus_cached`, `_prefix_count`,
        `_SEARCH_FIELDS` and the idf expression from the module under test, so
        what it produces is the exact float the renderer was handed.  That is
        what makes "floored, not rounded" checkable at all -- the rendered
        integer alone cannot say which operation produced it.

        `drop_stopwords` mirrors `_fn_search`, which drops the function words
        BEFORE it counts df, so the default is the only setting that reproduces
        what the server actually computed.  Pass False for the RAW table: the
        small-corpus idf inversion lives entirely in terms the search now refuses
        to score, so group D can measure it nowhere else.
        """
        mod = self.mod
        terms = list(dict.fromkeys(mod._tokenize(query)))
        if drop_stopwords:
            terms = [t for t in terms if t not in mod.QUERY_STOPWORDS]
        corpus, _avgfl, n_docs = mod._build_corpus_cached(self.abs_wiki)
        per_page = {}
        for pd in corpus:
            per_page[pd["relpath"]] = [
                t for t in terms
                if any(mod._prefix_count(pd["tokens"][f], t)
                       for f in mod._SEARCH_FIELDS)]
        df = {t: sum(1 for hs in per_page.values() if t in hs) for t in terms}
        idf = {t: math.log((n_docs - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
               for t in terms}
        total = sum(idf.values())
        cov = {rel: (sum(idf[t] for t in hs) / total if total else 0.0)
               for rel, hs in per_page.items()}
        return {"terms": terms, "n_docs": n_docs, "df": df, "idf": idf,
                "idf_total": total, "hits": per_page, "cov": cov}


def _d(label, value):
    """One detail line: a 12-wide label, then ': ', then the value."""
    return "%-12s: %s" % (label, value)


def build_fixture(work):
    """Write the synthetic wiki; return the project root to hand the server.

    realpath, not `work.path`: `safe_path` compares the RESOLVED wiki root
    against the project root it was given, and on macOS mkdtemp returns
    /var/folders/... while realpath yields /private/var/folders/... -- an
    unresolved project root would make the server refuse its own sandbox.
    `McpServer.__init__` realpaths project_root for exactly this reason, so
    doing it here is what production does, not a workaround.
    """
    for fname, _slug, _typ, text in PAGES:
        work.write_text(os.path.join(WIKI_REL, fname), text)
    return os.path.realpath(work.path)


def pct(value):
    """The server's own rendering rule for a coverage fraction."""
    return math.floor(100 * value)


def naive_round(value):
    """What a rounding implementation would have printed instead."""
    return math.floor(100 * value + 0.5)


# ---------------------------------------------------------------------------
# Group J's fixture: `get_page`'s SECTION INDEX.
#
# A SECOND workspace, three pages, and NOT one line added to the six above --
# see the module docstring: those six are group D's calibration window, and an
# extra page moves every idf in it.
#
# The six pages are the six shapes the index has to survive.  Note what the
# skip rule now IS: a heading whose slice is the WHOLE PAGE is not offered, and
# that is a statement about size, not about level.  Four of these pages are
# there because the two readings agree on them and two because they do NOT:
#
#   layered-page    an H1, three level-2 sections and two level-3 ones under the
#                   second of them, with a frontmatter block TALL on purpose --
#                   the whole point of `_body_line_offset` is that a body-
#                   relative line number is wrong by exactly that height, so a
#                   short header would let the bug hide.  The section sizes are
#                   deliberately unequal, or a renderer that printed one constant
#                   would pass the size case.
#   flat-page       no headings at all -> there is nothing to list, and an empty
#                   `sections:` label would be worse than saying so.
#   title-only-page an H1 and NOTHING else -> it DOES have a heading, the index
#                   skips it on purpose, and telling the caller the page has no
#                   headings would be a plain lie.  Its whole job is to be
#                   confusable with flat-page and to prove it is not.
#   deep-only-page  every heading below the title is level 3, so the DEFAULT
#                   listing is empty and the hint is the entire answer.  This is
#                   the page where "the hint is not decoration" is checkable.
#   two-titles-page TWO H1s, nothing else.  They BOUND each other, so neither
#                   slice is the whole page and both are real sections -- the
#                   page where `level < 2` and `size >= whole` disagree, and the
#                   one the old proxy silently hid a usable slice on.
#   deep-solo-page  ONE level-3 heading and no other, so its slice IS the page
#                   AND its level is past the default depth.  The only page
#                   where the ORDER of the two tests is observable: sized first
#                   it vanishes, depth-tested first it becomes a `deeper` count
#                   and the answer advertises an escape hatch that leads to the
#                   same empty answer.
#
# No page carries a code fence: group J's oracle for "which headings exist" is a
# naive ATX scan of the RAW file (`fixture_headings`), which shares no code with
# the server's `_headings` -- and that independence only holds while the naive
# rule is exact, i.e. while no fenced `# comment` exists to be skipped.
# ---------------------------------------------------------------------------

SEC_LAYERED = ("layered-page.md", "layered-page", """\
---
name: layered-page
title: A page with two heading levels
type: reference
status: current
description: Three level two sections and two level three ones under a frontmatter block tall enough that a body relative line number would be visibly wrong.
sources:
  - Scripts/mcp-wiki.py:_section_list
  - Scripts/mcp-wiki.py:_body_line_offset
  - Scripts/mcp-wiki.py:_section_index_block
---

# A page with two heading levels

Intro prose that sits under no level two heading at all.

## First section

Short on purpose.

## Second section

Longer than the first one, so the two advertised sizes differ and a size column
that reported the same number everywhere would be caught here instead of looking
plausible. This section also owns the two level three headings below it, so its
slice runs past them.

### A nested detail

The nested headings are what make the depth hint fire at all.

### Another nested detail

Two of them, so the number in the hint is a count and not a coincidence.

## Third section

The last section runs to the end of the file, which is the one slice whose size
is not bounded by a following heading.
""")

SEC_FLAT = ("flat-page.md", "flat-page", """\
---
name: flat-page
title: A page with no headings whatsoever
type: reference
status: current
description: Prose only, so the section index has nothing to list and has to say so instead of rendering an empty label.
sources:
  - Scripts/mcp-wiki.py:_section_index_block
---

This page is one paragraph and no headings at all. An index that rendered an
empty label here would tell the caller nothing, which is the one thing worse
than a refusal.

A second paragraph, still with no heading above it.
""")

SEC_TITLE = ("title-only-page.md", "title-only-page", """\
---
name: title-only-page
title: A page whose only heading is its own title
type: reference
status: current
description: One H1 and prose. The index skips the H1 on purpose, so it lists nothing and must not claim the page has no headings.
sources:
  - Scripts/mcp-wiki.py:_section_index_block
---

# A page whose only heading is its own title

One heading exists on this page and the index deliberately declines to offer it,
because its slice runs to the end of the file and is therefore the whole page.

That is a different fact from having no headings at all, and the two sentences
are not interchangeable: one says look elsewhere, the other says read the page.
""")

SEC_DEEP = ("deep-only-page.md", "deep-only-page", """\
---
name: deep-only-page
title: A page whose only headings are level three
type: reference
status: current
description: Every heading below the title is level three, so the default listing is empty and the hint is the entire answer.
sources:
  - Scripts/mcp-wiki.py:_section_list
---

# A page whose only headings are level three

### First deep heading

Nothing above level three here except the title.

### Second deep heading

So the default depth lists nothing and the hint has to carry the whole answer.

### Third deep heading

Three of them, so the count the hint reports is checkable against the file.
""")

SEC_TWO = ("two-titles-page.md", "two-titles-page", """\
---
name: two-titles-page
title: A page that carries two top level headings
type: reference
status: current
description: Two H1s that bound each other, so neither slice is the whole page and both are sections a caller can actually ask for.
sources:
  - Scripts/mcp-wiki.py:_section_list
---

# The first top level heading

Prose under the first title. This block ends where the second title begins, so
its slice is strictly smaller than the body — and a level based skip rule hid a
section the caller could have asked for and been served.

# The second top level heading

Prose under the second title. This one runs to the end of the file and is still
not the whole page, because the first title's block sits above it.
""")

SEC_SOLO = ("deep-solo-page.md", "deep-solo-page", """\
---
name: deep-solo-page
title: A page with one level three heading and nothing else
type: reference
status: current
description: The single heading starts the body and runs to the end, so its slice is the whole page while its level sits past the default depth.
sources:
  - Scripts/mcp-wiki.py:_section_list
---

### A single deep heading

This heading is the first line of the body and nothing follows it at its own
level or above, so asking for it as a section returns the entire page.

Its level is below the default depth, which is what makes the ORDER of the two
skip tests visible: depth-tested first it would be counted as hidden, and the
caller would be sent to a depth that reveals nothing.
""")

SEC_PAGES = [SEC_LAYERED, SEC_FLAT, SEC_TITLE, SEC_DEEP, SEC_TWO, SEC_SOLO]
LAYERED_FILE, LAYERED_SLUG = SEC_LAYERED[0], SEC_LAYERED[1]
FLAT_FILE, FLAT_SLUG = SEC_FLAT[0], SEC_FLAT[1]
TITLE_FILE, TITLE_SLUG = SEC_TITLE[0], SEC_TITLE[1]
DEEP_FILE, DEEP_SLUG = SEC_DEEP[0], SEC_DEEP[1]
TWO_FILE, TWO_SLUG = SEC_TWO[0], SEC_TWO[1]
SOLO_FILE, SOLO_SLUG = SEC_SOLO[0], SEC_SOLO[1]

# A heading no fixture page has, and no page may ever grow.
MISSING_SECTION = "there-is-no-such-heading"
# A path that cannot be opened: the shortest route to `_section_index_block`'s
# OSError arm, where the file line is UNKNOWABLE and must therefore go unprinted.
UNREADABLE_FILE = "no-such-file-on-disk.md"

SECTIONS_LABEL = "sections:"
# The two empties.  They are NOT interchangeable: "no headings" means there is
# nothing here to slice, while the second names the RULE -- every heading on the
# page spans it -- and so implies the next move, `get_page` without a section.
# The second one deliberately mentions no title: it has to stay true on a page
# whose single heading is not one.
NO_HEADINGS_MSG = "_(this page has no headings)_"
NO_SECTIONS_MSG = "_(no section here is smaller than the whole page)_"
EMPTY_MSGS = (NO_HEADINGS_MSG, NO_SECTIONS_MSG)
HINT_SENTINEL = "deeper heading"

# `L<n>, ` is OPTIONAL: when the offset cannot be established the server drops
# the file line and keeps the size, so both shapes are one line format with one
# part missing -- and parsing them with one regex is what lets a case assert
# that ONLY that part went missing.
_INDEX_LINE_RE = re.compile(
    r"^- (?P<name>.+) \((?:L(?P<line>\d+), )?(?P<size>\d+)c\)$", re.M)
# What a line loses when the offset is unknown, and nothing else.
_L_PREFIX_RE = re.compile(r" \(L\d+, ")
_HINT_RE = re.compile(
    r"^(?P<hidden>\d+) deeper heading\(s\) not listed — pass depth: "
    r"(?P<depth>\d+) to see them$", re.M)
_NOT_FOUND_RE = re.compile(r"^_\(section (?P<name>.+) not found\)_$", re.M)
_ATX_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*$")


def parse_page(text):
    """Structured view of one rendered `get_page` answer.

    The index lines are matched anywhere in the answer, not only under the
    label: "the H1 is never offered as a section" is a claim about the whole
    reply, and a parser that only looked inside a `sections:` block could not
    see a dash line rendered somewhere else.
    """
    sections = [{"name": m.group("name"),
                 "line": int(m.group("line")) if m.group("line") else None,
                 "size": int(m.group("size"))}
                for m in _INDEX_LINE_RE.finditer(text)]
    hint = _HINT_RE.search(text)
    miss = _NOT_FOUND_RE.search(text)
    return {
        "sections": sections,
        "names": [s["name"] for s in sections],
        "sized": [(s["name"], s["size"]) for s in sections],
        "with_line": [s for s in sections if s["line"] is not None],
        "has_sections_label": any(ln == SECTIONS_LABEL
                                  for ln in text.split("\n")),
        "hint": (int(hint.group("hidden")), int(hint.group("depth")))
        if hint else None,
        "has_hint_sentinel": HINT_SENTINEL in text,
        "has_no_headings_msg": NO_HEADINGS_MSG in text,
        "has_no_sections_msg": NO_SECTIONS_MSG in text,
        # Which of the two empties this answer actually rendered.  A list, so a
        # server that printed BOTH is a visible failure rather than a coin toss.
        "empty_msgs": [ln for ln in text.split("\n") if ln in EMPTY_MSGS],
        "not_found": miss.group("name") if miss else None,
    }


def triples(answer):
    """The section index as comparable tuples -- what the two arms must share."""
    return [(s["name"], s["line"], s["size"]) for s in answer["sections"]]


def build_section_fixture(work):
    """Write group J's three pages; return the project root for the server.

    realpath for the same reason `build_fixture` does it: `safe_path` compares
    the RESOLVED wiki root against the project root it was handed.
    """
    for fname, _slug, text in SEC_PAGES:
        work.write_text(os.path.join(WIKI_REL, fname), text)
    return os.path.realpath(work.path)


def file_lines(root, fname):
    """The RAW fixture file: frontmatter included, nothing stripped."""
    with open(os.path.join(root, WIKI_REL, fname), "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


def fixture_headings(lines):
    """[(file_line_1based, level, text)] for every ATX heading in the RAW file.

    Deliberately naive -- no frontmatter split, no fence tracking -- so it is an
    INDEPENDENT oracle rather than a second call into the code under test.  It
    is exact for these three pages because none of them carries a code fence or
    a '#' inside its frontmatter block.
    """
    out = []
    for i, line in enumerate(lines):
        m = _ATX_RE.match(line)
        if m:
            out.append((i + 1, len(m.group("hashes")), m.group("text")))
    return out


def fixture_body_offset(lines):
    """File line (1-based) the body starts on, read off the RAW file.

    Used as the PREMISE of the file-relative case: if this is 1 the page has no
    frontmatter, body-relative and file-relative numbering agree, and the case
    could not fail however the server numbered its headings.
    """
    if not lines or lines[0].strip() != "---":
        return 1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i + 2
    return 1


def served_section(text, name):
    """The slice a `section:` call actually returned, or None.

    `_fn_get_page` appends the extracted markdown last, after a blank line, and
    rstrips the whole answer -- so everything from the heading line onward,
    stripped, IS the extracted string, byte for byte.

    ANY level, including H1: since two H1s bound each other, an H1 can be a real
    section, and a helper that could not see one would silently return None for
    exactly the slice the level-based skip rule used to hide.  The rendered
    answer's own first line is `# <frontmatter title>` and is not a section, so
    the scan starts at line 1 -- and the two-H1 case asserts the fixture's title
    differs from its headings, which is what keeps that exclusion sufficient.
    """
    pattern = re.compile(r"^#{1,6}\s+%s\s*$" % re.escape(name))
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i and pattern.match(line):
            return "\n".join(lines[i:]).strip()
    return None


# ---------------------------------------------------------------------------
# Group K's fixture: the per-hit `description` line of `source_to_pages`.
#
# A THIRD workspace -- and a FOURTH that MIRRORS it -- for the reason group J
# has its own: the six calibration pages ARE group D's measured window, and one
# more page there moves every idf in it.
#
# The reverse lookup used to parse `description` out of the frontmatter and then
# throw it away, so the answer said WHICH pages cover a source and nothing about
# WHAT they say.  These five pages are the four shapes the new line has to
# survive, plus one page that must never be listed at all:
#
#   hit-alpha     described, TWO matching `sources:` anchors -- so one query
#                 returns several hits and the anchor line the description has
#                 to sit above is a LIST, not a lone value.
#   hit-beta      described, one matching anchor, and its slug
#                 (`zeta-second-page`) sorts LAST while its filename sorts
#                 second.  Document order and slug order therefore DISAGREE on
#                 this corpus, which is what makes "the hit order did not move"
#                 a claim capable of failing.
#   hit-mute      NO `description` key at all, same query anchor: the SILENCE.
#                 Its case asserts the premise too -- the key really is absent,
#                 read back through the server's own parser -- because "no
#                 description line" is trivially true for a page the fixture
#                 forgot to match, or one that has a description nobody dropped.
#   hit-target    described, matched through `targets:` and NOT through
#                 `sources:` (its source anchor points elsewhere), so the
#                 description is pinned above the OTHER anchor line as well.
#   zz-elsewhere  described, matches nothing: present so "only the matching
#                 pages are listed" runs against a corpus that has something to
#                 leak, and so the no-hit query has a described page to ignore.
#
# Every description is token-DISJOINT from its own title, and the cases assert
# it.  On a fixture whose two fields shared any vocabulary, a renderer that
# printed the TITLE under each hit could not be told apart from one that printed
# the description -- which is mutation (ii) of the proof harness.
# ---------------------------------------------------------------------------

# (filename, slug, title, type, status, description|None, sources, targets, body)
K_FILE, K_SLUG, K_TITLE, K_TYPE, K_STATUS, K_DESC, K_SRCS, K_TGTS, K_BODY = range(9)

K_PAGES = [
    ("hit-alpha.md", "hit-alpha", "Reverse lookup, first page",
     "component", "current",
     "Every anchor that names a symbol keeps its colon suffix intact, and a "
     "bare path matches all of them at once.",
     ["Scripts/reverse-lookup.py:resolve_anchor",
      "Scripts/reverse-lookup.py:iter_anchors"], [],
     "Two anchors on one file, so the anchor line under this hit is a list."),
    ("hit-beta.md", "zeta-second-page", "Second document over identical ground",
     "reference", "draft",
     "Two pages can cover one module without either being a duplicate of its "
     "neighbour.",
     ["Scripts/reverse-lookup.py:render_hits"], [],
     "The slug and the filename sort differently, on purpose."),
    ("hit-mute.md", "hit-mute", "Frontmatter that stops at its anchors",
     "analysis", "draft", None,
     ["Scripts/reverse-lookup.py:parse_row"], [],
     "No description key at all: the line under this hit must not appear."),
    ("hit-target.md", "hit-target", "Downstream consumer",
     "spec", "current",
     "Written by the generator rather than read by it, which is what puts this "
     "page on the far side of an arrow.",
     ["Scripts/unrelated-writer.py:emit"],
     ["Scripts/reverse-lookup.py:emit_report"],
     "Matched through targets, and through targets only."),
    ("zz-elsewhere.md", "zz-elsewhere", "Page about a different module",
     "concept", "current",
     "Its only anchor points somewhere else, so no query in this group may "
     "ever list it.",
     ["Scripts/other-module.py:unrelated"], [],
     "Never a hit, always in the corpus."),
]

# The anchor every hit page carries and the one page that does not.
K_SHARED_SOURCE = "Scripts/reverse-lookup.py"
# The same file, narrowed to ONE symbol: a second live query, so the byte-for-
# byte preservation claim is not made about a single rendering.
K_SYMBOL_SOURCE = "Scripts/reverse-lookup.py:render_hits"
# A path no fixture anchor mentions: the `no page references this source` arm.
K_ABSENT_SOURCE = "Scripts/never-documented.py"
# A description shorter than this could not tell a VERBATIM renderer from one
# that truncates -- the check is exact equality, so the only way it goes blind
# is a fixture whose strings are shorter than the cut somebody introduces.
K_DESC_MIN_CHARS = 80
# The state every page in THIS corpus measures, and why it is a constant here:
# each of the five carries `sources:` and no `verified:` block, and that shape is
# decided before any git diff is asked for -- so the label does not depend on the
# workspace being a git repo, on HEAD, or on anything a temp dir cannot promise.
# Deliberately NOT one of the `status:` values the fixture writes, so a renderer
# that fell back to the frontmatter field would fail rather than coincide.
K_MEASURED_STATE = "unverified"

_S2P_HEADER_RE = re.compile(
    r"^# source_to_pages: (?P<source>.+) — (?P<count>\d+) page\(s\) in "
    r"(?P<root>.+)/$", re.M)
_S2P_HIT_RE = re.compile(
    r"^- \*\*(?P<title>.*?)\*\* — (?P<slug>.+?) `(?P<path>[^`]*)`"
    r"(?: \[(?P<meta>[^\]]*)\])?$")
# The value is OPTIONAL: `description:` with nothing after it is a rendering the
# silence case has to be able to SEE, and a parser that demanded a value would
# report the empty line as no line at all.
_S2P_ATTR_RE = re.compile(r"^(?P<indent>[ \t]+)(?P<key>[a-z_]+):(?: (?P<value>.*))?$")

DESC_KEY = "description"
NO_REF_MSG = "no page references this source"


def _attr_key(line):
    m = _S2P_ATTR_RE.match(line)
    return m.group("key") if m else None


def parse_sources(text):
    """Structured view of one rendered `source_to_pages` answer.

    Every element keeps its LINE INDEX, because half of what group K pins is an
    ORDER: the description belongs between a hit's header line and its anchor
    lines, and a parser that only collected values could not tell a correctly
    placed line from one rendered after `sources:`.

    Attribute lines are attached to the hit ABOVE them; one that appears before
    any hit becomes an orphan rather than being silently dropped, so a renderer
    that emitted the description above its own header is visible instead of
    invisible.
    """
    lines = text.split("\n")
    hits, orphans = [], []
    for i, line in enumerate(lines):
        m = _S2P_HIT_RE.match(line)
        if m:
            meta = m.group("meta") or ""
            hits.append({"line_i": i, "title": m.group("title"),
                         "slug": m.group("slug"), "path": m.group("path"),
                         "meta": meta, "attrs": []})
            continue
        a = _S2P_ATTR_RE.match(line)
        if not a:
            continue
        rec = {"line_i": i, "indent": a.group("indent"), "key": a.group("key"),
               "value": a.group("value") if a.group("value") is not None else ""}
        (hits[-1]["attrs"] if hits else orphans).append(rec)
    hm = _S2P_HEADER_RE.search(text)
    return {
        "text": text, "lines": lines, "hits": hits, "orphan_attrs": orphans,
        "header_source": hm.group("source") if hm else None,
        "header_count": int(hm.group("count")) if hm else None,
        "header_root": hm.group("root") if hm else None,
        "has_header": hm is not None,
        "has_no_ref_msg": NO_REF_MSG in text,
        "desc_lines": [ln for ln in lines if _attr_key(ln) == DESC_KEY],
        "attr_lines": [ln for ln in lines if _attr_key(ln) is not None],
    }


def hit_attrs(hit, key):
    return [a for a in hit["attrs"] if a["key"] == key]


def strip_desc_lines(text):
    """The answer with every `description:` attribute line removed, and nothing
    else touched -- the left-hand side of group K's preservation claim."""
    return "\n".join(ln for ln in text.split("\n")
                     if _attr_key(ln) != DESC_KEY)


def k_page_text(page, with_description=True):
    """Render one group-K page.

    `with_description=False` drops EXACTLY the `description:` frontmatter line
    and touches nothing else.  That identity is ASSERTED by the case that uses
    it, not assumed here: the mirror corpus is only a control if it is the same
    corpus minus one line per page.
    """
    out = ["---",
           "name: %s" % page[K_SLUG],
           "title: %s" % page[K_TITLE],
           "type: %s" % page[K_TYPE],
           "status: %s" % page[K_STATUS]]
    if page[K_DESC] and with_description:
        out.append("description: %s" % page[K_DESC])
    if page[K_SRCS]:
        out.append("sources:")
        out += ["  - %s" % s for s in page[K_SRCS]]
    if page[K_TGTS]:
        out.append("targets:")
        out += ["  - %s" % t for t in page[K_TGTS]]
    out += ["---", "", page[K_BODY], ""]
    return "\n".join(out)


def build_desc_fixture(work, with_description=True):
    """Write group K's five pages; return the project root for the server.

    realpath for the same reason the other two builders do it: `safe_path`
    compares the RESOLVED wiki root against the project root it was handed.
    """
    for page in K_PAGES:
        work.write_text(os.path.join(WIKI_REL, page[K_FILE]),
                        k_page_text(page, with_description))
    return os.path.realpath(work.path)


def k_expected_hits(source):
    """[(page, matched_anchors)] a `source_to_pages` query must land on.

    An INDEPENDENT oracle: a naive first-colon split over the fixture table,
    sharing no code with the server's `_matches`/`_source_path`.  It is exact
    for these five pages because every anchor here is a plain `path:symbol` with
    no colon inside the path -- the case that consumes it asserts that premise.

    Document order is `os.walk`'s: filenames, sorted.
    """
    q_path, _, q_sym = source.partition(":")
    out = []
    for page in sorted(K_PAGES, key=lambda p: p[K_FILE]):
        matched = []
        for anchor in list(page[K_SRCS]) + list(page[K_TGTS]):
            a_path, _, a_sym = anchor.partition(":")
            if a_path == q_path and (not q_sym or not a_sym or a_sym == q_sym):
                matched.append(anchor)
        if matched:
            out.append((page, matched))
    return out


# ---------------------------------------------------------------------------
# Group L's fixture: the state in a recall reply's `[type/state]` label is
# MEASURED against git, not read from the frontmatter `status:` field.
#
# The defect this group exists for was measured on the real wiki: `freshness`
# said 9 of 10 pages were stale while every one of those pages carried
# `status: current` in its frontmatter -- and `search` rendered the frontmatter
# value, so on one real query all four hits were labelled `current` while all
# four were stale.  A caller reading `[adr/current]` trusted a page whose
# sources had moved.
#
# A FIFTH and SIXTH workspace, for the reason group J and K have their own: the
# six calibration pages ARE group D's measured window.  These ten are also the
# only fixture in this file that needs SOURCE FILES on disk -- `_evaluate` calls
# a source `missing` when its path does not exist under the repo, so the
# orphaned-source shape cannot be built out of frontmatter alone.
#
# The eight states, one page each (two pages for `unverified`, which has two
# distinct reasons, and one extra page carrying NO status field at all):
#
#   st-stale        sources changed since verified.commit -> `stale`, while its
#                   frontmatter says `current`.  THE case.
#   st-current      sources unchanged since verified.commit -> `current`.  The
#                   control: without it, `stale` could be a constant.
#   st-orphan       its source anchor names a file that is not on disk ->
#                   `orphaned-source`, which outranks `stale`.
#   st-unverified   sources, no `verified:` block -> `unverified` (reason: no
#                   verified.commit).  Needs no git at all to reach.
#   st-lostcommit   sources and a verified.commit git cannot resolve ->
#                   `unverified` (reason: not in history).  The SAME word for a
#                   different fact, which is why both shapes are here.
#   st-nosources    no anchors, type NOT in UNTRACKED_TYPES -> `no-sources`.
#   st-untracked    no anchors, type in UNTRACKED_TYPES -> `untracked`.
#   st-planned      targets only, none materialized -> `planned`.
#   st-promotable   targets only, one materialized -> `promotable`.
#   st-nostatus     no `status:` key at all -> the label still carries a state,
#                   and the disagreement tally must NOT count it: there is no
#                   hand-written claim on that page to disagree with.
#
# Every page carries the token `statepage`, so ONE query returns the whole
# corpus and the "said once, N of M" claim is made against every state at once.
# `anchors` sits on the three pages with real source anchors only, so coverage
# VARIES across the hit list -- a filter that perturbed a score would show up.
# `zzunknownword` is absent everywhere: a df-0 term is how group L reaches the
# relevance gate, which is where `best coverage N%` is rendered.
#
# The git boundary is STUBBED, not spawned: `_changed_files` is replaced by a
# lookup over L_DIFFS in the module under test.  That is the only external
# oracle `_classify_page` consults, and stubbing it is what makes `stale` and
# `orphaned-source` reachable offline, deterministically, in every one of the
# eight arms.  The cases that consume it assert the stub was CONSULTED, so a
# server that stopped asking git fails here rather than passing quietly.
# ---------------------------------------------------------------------------

(L_FILE, L_SLUG, L_TITLE, L_TYPE, L_STATUS, L_SRCS, L_TGTS, L_COMMIT, L_STATE,
 L_BODY) = range(10)

# The three source files the fixture materializes, and the two it deliberately
# does not.  `src/moved.py` is the one the stubbed diff reports as changed.
L_SRC_CHANGED = "src/moved.py"
L_SRC_CLEAN = "src/kept.py"
L_SRC_ABSENT = "src/deleted.py"
L_TGT_ABSENT = "src/future.py"
L_ON_DISK = (L_SRC_CHANGED, L_SRC_CLEAN)

# The verified commits, and what the stubbed `git diff` reports for each.
L_C_CHANGED = "c0ffee1"
L_C_CLEAN = "c0ffee2"
L_C_LOST = "deadbee"
L_DIFFS = {
    L_C_CHANGED: {L_SRC_CHANGED},
    L_C_CLEAN: {"src/somewhere-else.py"},
    L_C_LOST: None,                  # None == git cannot resolve the commit
}

L_SHARED_TERM = "statepage"
L_ANCHOR_TERM = "anchors"
L_UNKNOWN_TERM = "zzunknownword"
# The probe that reaches the relevance GATE, which is the only place `best
# coverage N%` is rendered: `zzunknownword` has df 0 and therefore the maximum
# idf, so no page can clear 55% -- while `anchors` (df 3) keeps the coverage of
# the three sourced pages well above the other seven, so the hit list has a real
# spread for a filter to disturb.
L_GATED_QUERY = "%s %s %s" % (L_SHARED_TERM, L_ANCHOR_TERM, L_UNKNOWN_TERM)

L_PAGES = [
    ("st-stale.md", "st-stale", "The page whose source moved", "component",
     "current", [L_SRC_CHANGED], [], L_C_CHANGED, "stale",
     "This statepage owns anchors that moved after it was last verified."),
    ("st-current.md", "st-current", "The page nothing touched", "component",
     "current", [L_SRC_CLEAN], [], L_C_CLEAN, "current",
     "This statepage owns anchors that nobody has touched since."),
    ("st-orphan.md", "st-orphan", "The page pointing at a deleted file",
     "component", "current", [L_SRC_ABSENT], [], L_C_CLEAN, "orphaned-source",
     "This statepage owns anchors on a file that is gone from the tree."),
    ("st-unverified.md", "st-unverified", "The page nobody ever verified",
     "component", "current", [L_SRC_CLEAN], [], None, "unverified",
     "This statepage was never compared against the code it claims."),
    ("st-lostcommit.md", "st-lostcommit", "The page verified against a ghost",
     "component", "current", [L_SRC_CLEAN], [], L_C_LOST, "unverified",
     "This statepage names a commit the history no longer holds."),
    ("st-nosources.md", "st-nosources", "The page tied to no code at all",
     "reference", "current", [], [], None, "no-sources",
     "This statepage claims nothing about code, so nothing can go stale."),
    ("st-untracked.md", "st-untracked", "The page whose type needs no code",
     "adr", "current", [], [], None, "untracked",
     "This statepage records a decision, and decisions do not drift."),
    ("st-planned.md", "st-planned", "The page whose code is not written yet",
     "spec", "current", [], [L_TGT_ABSENT], None, "planned",
     "This statepage describes a file that does not exist yet."),
    ("st-promotable.md", "st-promotable", "The page whose target arrived",
     "spec", "current", [], [L_SRC_CLEAN], None, "promotable",
     "This statepage described a file that has since been written."),
    # `promotable` is the one state with TWO branches: no sources at all (above),
    # and sources that are VERIFIED AND CLEAN plus a target that has since
    # materialized (here).  Without this page the second branch is never
    # executed, and a rule change inside it survives every case in the file --
    # measured: the mutant that turns this branch into `current` was the one
    # mutant the suite let through until this page existed.
    ("st-promoted.md", "st-promoted", "The page verified, clean, and overtaken",
     "spec", "current", [L_SRC_CLEAN], [L_SRC_CHANGED], L_C_CLEAN, "promotable",
     "This statepage owns a target that landed while its sources stayed put."),
    ("st-nostatus.md", "st-nostatus", "The page with no status field",
     "reference", None, [], [], None, "no-sources",
     "This statepage carries no hand-written status to be wrong."),
]

L_BY_FILE = {p[L_FILE]: p for p in L_PAGES}
L_BY_SLUG = {p[L_SLUG]: p for p in L_PAGES}
# Every group-L query passes this: the corpus is larger than `limit`'s default of
# 10, and the disclosure's denominator is the number of RENDERED hits -- so a
# truncated answer would make "N of M" a claim about the page count `limit`
# happened to allow.  Derived from the table, so a page added here cannot
# silently re-introduce the truncation.
L_LIMIT = len(L_PAGES)
# The states that mean NOT CHECKABLE.  None of them may ever render as anything
# a caller could read as checked-and-clean, and `current` is not among them.
L_NOT_CHECKABLE = {"unverified", "promotable", "planned", "untracked",
                   "no-sources"}
L_ALL_STATES = sorted({p[L_STATE] for p in L_PAGES})

_FM_DISAGREE_RE = re.compile(
    r"^frontmatter status: disagrees on (?P<n>\d+) of (?P<m>\d+) "
    r"(?:hit|page)\(s\) — ", re.M)
FM_DISAGREE_SENTINEL = "frontmatter status:"


def l_page_text(page, agree=False):
    """Render one group-L page.

    `agree=True` writes the MEASURED state into the frontmatter `status:` field
    instead of the page's own value, which is what turns the corpus into the
    control the "no disclosure when they agree" case needs.  The page that
    carries no status field keeps none in either corpus.
    """
    out = ["---",
           "name: %s" % page[L_SLUG],
           "title: %s" % page[L_TITLE],
           "type: %s" % page[L_TYPE]]
    if page[L_STATUS] is not None:
        out.append("status: %s" % (page[L_STATE] if agree else page[L_STATUS]))
    if page[L_SRCS]:
        out.append("sources:")
        out += ["  - %s" % s for s in page[L_SRCS]]
    if page[L_TGTS]:
        out.append("targets:")
        out += ["  - %s" % t for t in page[L_TGTS]]
    if page[L_COMMIT]:
        out += ["verified:", "  commit: %s" % page[L_COMMIT],
                "  date: 2026-01-01"]
    out += ["---", "", "# %s" % page[L_TITLE], "", page[L_BODY], ""]
    return "\n".join(out)


def build_state_fixture(work, agree=False):
    """Write group L's ten pages plus the source files on disk.

    The source files are the point of the workspace, not decoration:
    `_evaluate` reports a source `missing` when its path does not exist under
    the repo, so `orphaned-source` and `promotable` are decided by the
    filesystem and cannot be faked in frontmatter.
    """
    for page in L_PAGES:
        work.write_text(os.path.join(WIKI_REL, page[L_FILE]),
                        l_page_text(page, agree))
    for rel in L_ON_DISK:
        work.write_text(rel, "# materialized source file\n")
    return os.path.realpath(work.path)


def patch_git_boundary(mod, repo):
    """Replace every git call the state path makes; return the MISS log.

    Three seams, and each one is a spawn this suite must not make:
      `repo_root`      -> the fixture root (production runs `git rev-parse
                          --show-toplevel`, which in a temp dir answers the temp
                          dir anyway -- but only after paying a subprocess).
      `_changed_files` -> a lookup over L_DIFFS.  THE external oracle
                          `_classify_page` consults; the log is what lets a case
                          assert it was consulted at all.
      `git`            -> a hard failure, which is what a temp dir really
                          produces, so `freshness`'s head sha falls back to the
                          literal `head` deterministically instead of spawning.

    The log records CACHE MISSES, not every ask: a miss is exactly where the
    real implementation spawns `git diff`, so the list length is the number of
    subprocesses production would have paid for.  That is what makes "the memo
    memoizes" and "the memo dies with HEAD" measurable rather than asserted.

    Every seam is patched on the module OBJECT, and each Driver loads its own
    module instance, so no other group sees any of this.
    """
    log = []

    def changed_files(commit, head, repo_arg, cache):
        if commit in cache:
            val = cache[commit]
            return None if val == mod._INVALID else val
        log.append((commit, head))
        value = L_DIFFS.get(commit)
        cache[commit] = mod._INVALID if value is None else value
        return value

    mod.repo_root = lambda start=None: repo
    mod._changed_files = changed_files
    mod.git = lambda args, cwd: (1, "", "stubbed: no git in this fixture")
    return log


def log_git_calls(mod):
    """Wrap `git` on a module and return the argv log.  The spawns still happen.

    Used on the UNPATCHED driver only, where the point is to measure what a
    plain (non-repo) directory really costs the recall path.
    """
    log = []
    inner = mod.git

    def counting(args, cwd):
        log.append(list(args))
        return inner(args, cwd)

    mod.git = counting
    return log


def state_of(hit):
    """The state half of a rendered `[type/state]` label."""
    return hit["status"]


def tool_paragraph(desc, name):
    """The `  <name>   ...` entry of the tool description, continuations included.

    Entries start at column 2 and their continuation lines are indented far
    deeper, so the block ends at the next line that is not deeply indented.
    Scoping the check to ONE entry matters: `description` is a word that appears
    elsewhere in this text, and a bare `in desc` would pass on somebody else's
    sentence.
    """
    lines = desc.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("  %s " % name):
            block = [line]
            for nxt in lines[i + 1:]:
                if not nxt.startswith("    "):
                    break
                block.append(nxt)
            return "\n".join(block)
    return ""


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-wiki `search` relevance gate: silence, "
                                "measured calibration window, floored "
                                "percentages, query-side stopwords, "
                                "`get_page`'s section index, and the MEASURED "
                                "state in every recall label",
                    opts=opts, mode="grouped")
    work = H.TempWorkspace("ph-wiki-recall-", keep=opts.keep)
    pyc_before = H.pycache_snapshot()

    try:
        root = build_fixture(work)
        drv = Driver(root)
        gate = drv.mod.DEFAULT_MIN_COVERAGE
        gate_pct = pct(gate)

        # ============ A: the gate can be SILENT ============
        near = drv.search(Q_SILENT_NEAR)
        far = drv.search(Q_SILENT_FAR)

        problems = []
        if near["error"]:
            problems.append("call failed: %s" % near["text"][:160])
        if near["header_hits"] != 0:
            problems.append("header claims %r hit(s), want 0"
                            % near["header_hits"])
        if near["hits"]:
            problems.append("%d hit line(s) rendered: %r"
                            % (len(near["hits"]),
                               [h["slug"] for h in near["hits"]]))
        suite.record("A", "silent-zero-hits", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("min_coverage", "default (%.2f)" % gate),
                             _d("header", "%r hit(s)" % near["header_hits"]),
                             _d("why", "`telemetry` is unknown to the corpus and "
                                       "takes the maximum idf, while the only "
                                       "term every page shares is the near-"
                                       "worthless `mcp`")],
                     text=near["text"])

        problems = []
        if not near["has_unknown_line"]:
            problems.append("no %r line at all" % UNKNOWN_MSG)
        if near["unknown"] != ["telemetry"]:
            problems.append("unknown list %r, want ['telemetry']"
                            % near["unknown"])
        suite.record("A", "silent-names-unknown-term", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("unknown", "%r" % near["unknown"]),
                             _d("contract", "the caller must learn WHICH word "
                                            "the corpus has never seen")],
                     text=near["text"])

        problems = []
        if not near["has_gate_msg"]:
            problems.append("missing %r" % GATE_MSG)
        if near["need_pct"] != gate_pct:
            problems.append("need %r%%, want %d%% (floor of "
                            "DEFAULT_MIN_COVERAGE=%r)"
                            % (near["need_pct"], gate_pct, gate))
        if near["best_pct"] is None:
            problems.append("the message does not report a best coverage")
        suite.record("A", "silent-explains-the-gate", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("message", "best coverage %r%%, need %r%%"
                                % (near["best_pct"], near["need_pct"])),
                             _d("derived", "need%% = floor(100 * "
                                           "mod.DEFAULT_MIN_COVERAGE) = %d"
                                % gate_pct)],
                     text=near["text"])

        problems = []
        if far["header_hits"] != 0 or far["hits"]:
            problems.append("expected zero hits, got header=%r lines=%d"
                            % (far["header_hits"], len(far["hits"])))
        if not far["has_gate_msg"]:
            problems.append("missing %r" % GATE_MSG)
        if far["has_no_match_msg"]:
            problems.append("claims %r, but `mcp` and `search` matched -- this "
                            "query must be refused by the GATE, or it stops "
                            "testing the gate" % NO_MATCH_MSG)
        if far["unknown"] != ["vendor", "grafana", "dashboard"]:
            problems.append("unknown list %r, want ['vendor', 'grafana', "
                            "'dashboard']" % far["unknown"])
        suite.record("A", "silent-second-query-same-shape", problems,
                     detail=[_d("query", repr(Q_SILENT_FAR)),
                             _d("header", "%r hit(s)" % far["header_hits"]),
                             _d("unknown", "%r" % far["unknown"]),
                             _d("ignored", "%r" % far["ignored"]),
                             _d("why", "three df-0 terms drag coverage down to "
                                       "single digits, but `mcp` and `search` "
                                       "keep all six pages in the candidate set, "
                                       "so the GATE has to do the refusing")],
                     text=far["text"])

        # ============ B: two silences, and they must not be confusable ======
        nonsense = drv.search(Q_NONSENSE)

        problems = []
        if nonsense["error"]:
            problems.append("call failed: %s" % nonsense["text"][:160])
        if not nonsense["has_no_match_msg"]:
            problems.append("missing %r" % NO_MATCH_MSG)
        if nonsense["has_gate_msg"]:
            problems.append("claims %r, but nothing matched lexically" % GATE_MSG)
        if nonsense["hits"]:
            problems.append("hit lines rendered: %r"
                            % [h["slug"] for h in nonsense["hits"]])
        suite.record("B", "nonsense-says-no-matching-pages", problems,
                     detail=[_d("query", repr(Q_NONSENSE)),
                             _d("sentinels", "no-match=%r gate=%r"
                                % (nonsense["has_no_match_msg"],
                                   nonsense["has_gate_msg"])),
                             _d("next move", "rephrase, or the wrong wiki -- "
                                             "NOT 'the topic is undocumented'")],
                     text=nonsense["text"])

        problems = []
        if not near["has_gate_msg"]:
            problems.append("missing %r" % GATE_MSG)
        if near["has_no_match_msg"]:
            problems.append("claims %r, but 6 pages matched `mcp`" % NO_MATCH_MSG)
        suite.record("B", "gated-out-not-no-matching", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("sentinels", "no-match=%r gate=%r"
                                % (near["has_no_match_msg"],
                                   near["has_gate_msg"])),
                             _d("why", "every page shares `mcp` with the query "
                                       "and `mcp` is no function word, so "
                                       "'nothing matched' would be a lie")],
                     text=near["text"])

        flags_nonsense = (nonsense["has_no_match_msg"], nonsense["has_gate_msg"])
        flags_gated = (near["has_no_match_msg"], near["has_gate_msg"])
        problems = []
        if flags_nonsense != (True, False):
            problems.append("nonsense flags %r, want (True, False)"
                            % (flags_nonsense,))
        if flags_gated != (False, True):
            problems.append("gated flags %r, want (False, True)" % (flags_gated,))
        if flags_nonsense == flags_gated:
            problems.append("both silences render the same sentinel pair -- the "
                            "caller cannot tell them apart")
        suite.record("B", "two-silences-are-distinct", problems,
                     detail=[_d("nonsense", "(no_match, gate) = %r"
                               % (flags_nonsense,)),
                             _d("gated", "(no_match, gate) = %r"
                                % (flags_gated,)),
                             _d("contract", "exactly one sentinel per answer, "
                                            "and never the same one")],
                     text=nonsense["text"])

        # ============ C: the gate does not kill real answers ============
        answered = {}
        for cid, query, want_slug, want_type in ANSWERS:
            res = drv.search(query)
            answered[query] = res
            problems = []
            if res["error"]:
                problems.append("call failed: %s" % res["text"][:160])
            if not res["hits"]:
                problems.append("gate silenced a query with a real answer "
                                "(header=%r)" % res["header_hits"])
            else:
                top = res["hits"][0]
                if top["slug"] != want_slug:
                    problems.append("top hit is %r, want %r"
                                    % (top["slug"], want_slug))
                if top["type"] != want_type:
                    problems.append("top hit type is %r, want %r"
                                    % (top["type"], want_type))
                if top["cov"] < gate_pct:
                    problems.append("top hit renders cov %d%% below the gate's "
                                    "%d%%" % (top["cov"], gate_pct))
            top = res["hits"][0] if res["hits"] else None
            suite.record("C", cid, problems,
                         detail=[_d("query", repr(query)),
                                 _d("want", "%s [%s]" % (want_slug, want_type)),
                                 _d("got", "%s [%s] cov %d%%"
                                    % (top["slug"], top["type"], top["cov"])
                                    if top else "NOTHING"),
                                 _d("hits", "%r" % res["header_hits"]),
                                 _d("unknown", "%r" % res["unknown"])],
                         text=res["text"])

        # ============ D: the calibration window, MEASURED ============
        # Everything below is driven at min_coverage 0.0 -- the gate is switched
        # OFF so the raw coverage of every candidate is visible, including the
        # candidates the default gate would have hidden.
        # RAW (drop_stopwords=False): the inversion this fixture models lives in
        # `why`/`did`/`we`/`the`, and the search now refuses to score those, so
        # the post-drop term list cannot show the df table at all.  Measuring it
        # raw is not measuring the wrong thing -- it is measuring the INPUT that
        # QUERY_STOPWORDS was introduced to neutralize, which is the one thing
        # that must not quietly disappear from the fixture.
        probe = drv.measure(PROBE_ALL, drop_stopwords=False)
        df, idf, n_docs = probe["df"], probe["idf"], probe["n_docs"]
        problems = []
        for token in ("mcp", "the"):
            if df[token] != n_docs:
                problems.append("df[%r]=%d, want %d (a token in EVERY page is "
                                "what defeats a zero-terms-matched gate)"
                                % (token, df[token], n_docs))
        for token in ("why", "did", "we"):
            if df[token] != 1:
                problems.append("df[%r]=%d, want 1 (the function words must stay "
                                "rare, or their idf collapses and the inversion "
                                "disappears)" % (token, df[token]))
        for token in ("telemetry", "quantile", "vendor", "grafana", "dashboard"):
            if df[token] != 0:
                problems.append("df[%r]=%d, want 0" % (token, df[token]))
        if not idf["why"] > idf["mcp"]:
            problems.append("idf(why)=%.4f is not above idf(mcp)=%.4f -- the "
                            "small-corpus inversion is gone and the fixture no "
                            "longer models the pathology"
                            % (idf["why"], idf["mcp"]))
        decoy_hits = set(drv.measure(Q_SILENT_NEAR,
                                     drop_stopwords=False)["hits"][DECOY_REL])
        leaked = sorted(decoy_hits & set(NEAR_DISCRIMINATING))
        if leaked:
            problems.append("the decoy page carries discriminating content "
                            "term(s) %r; it must carry only the query's function "
                            "words and the corpus-wide `mcp`" % leaked)
        if not decoy_hits >= {"why", "did", "we"}:
            problems.append("the decoy no longer carries the question's function "
                            "words (%r); it is not a decoy any more"
                            % sorted(decoy_hits))
        suite.record("D", "fixture-reproduces-pathologies", problems,
                     detail=[_d("n_docs", n_docs),
                             _d("ubiquitous", "df[mcp]=%d df[the]=%d"
                                % (df["mcp"], df["the"])),
                             _d("function", "df[why]=%d df[did]=%d df[we]=%d"
                                % (df["why"], df["did"], df["we"])),
                             _d("inversion", "idf(why)=%.4f vs idf(mcp)=%.4f"
                                % (idf["why"], idf["mcp"])),
                             _d("decoy hits", "%r" % sorted(decoy_hits)),
                             _d("df 0", "%s" % ", ".join(
                                 "%s=%d" % (t, df[t]) for t in
                                 ("telemetry", "quantile", "vendor", "grafana",
                                  "dashboard")))],
                     text="")

        silent_best = {}
        problems = []
        for cid, query in SILENT:
            res = drv.search(query, min_coverage=0.0)
            best = max([h["cov"] for h in res["hits"]] or [0])
            silent_best[query] = best
            if best / 100.0 >= gate:
                problems.append("%s: best coverage %d%% is NOT below the gate "
                                "(%d%%) -- this query can no longer be silenced"
                                % (cid, best, gate_pct))
        max_silent = max(silent_best.values()) if silent_best else 0
        suite.record("D", "measured-silent-ceiling", problems,
                     detail=[_d(cid, "%r -> best %d%%"
                                % (query, silent_best[query]))
                             for cid, query in SILENT]
                            + [_d("ceiling", "%d%% (measured at "
                                             "min_coverage=0.0)" % max_silent),
                               _d("gate", "%d%% (from "
                                          "mod.DEFAULT_MIN_COVERAGE)"
                                  % gate_pct)],
                     text="")

        answer_floor = {}
        problems = []
        for cid, query, want_slug, _want_type in ANSWERS:
            res = drv.search(query, min_coverage=0.0)
            found = [h for h in res["hits"] if h["slug"] == want_slug]
            if not found:
                problems.append("%s: %r not in the ungated result set at all"
                                % (cid, want_slug))
                continue
            cov = found[0]["cov"]
            answer_floor[query] = cov
            if cov / 100.0 < gate:
                problems.append("%s: winner %r sits at %d%%, BELOW the gate's "
                                "%d%% -- the gate kills a real answer"
                                % (cid, want_slug, cov, gate_pct))
        min_answer = min(answer_floor.values()) if answer_floor else 0
        suite.record("D", "measured-answer-floor", problems,
                     detail=[_d(cid, "%s -> %d%%"
                                % (want_slug, answer_floor.get(query, -1)))
                             for cid, query, want_slug, _t in ANSWERS]
                            + [_d("floor", "%d%% (the thinnest real answer)"
                                  % min_answer),
                               _d("gate", "%d%%" % gate_pct)],
                     text="")

        problems = []
        if not max_silent / 100.0 < gate:
            problems.append("max silent coverage %d%% >= gate %d%%"
                            % (max_silent, gate_pct))
        if not gate <= min_answer / 100.0:
            problems.append("gate %d%% > thinnest real answer %d%%"
                            % (gate_pct, min_answer))
        if max_silent >= min_answer:
            problems.append("THE WINDOW IS CLOSED: the best false answer (%d%%) "
                            "is at or above the thinnest true answer (%d%%). No "
                            "single threshold can separate them, so the gate is "
                            "not mis-tuned -- it is unsalvageable, and the "
                            "fixture (or the scorer) must be revisited"
                            % (max_silent, min_answer))
        suite.record("D", "calibration-window-open", problems,
                     detail=[_d("window", "(%d%%, %d%%]"
                                % (max_silent, min_answer)),
                             _d("gate", "%d%% (DEFAULT_MIN_COVERAGE=%r)"
                                % (gate_pct, gate)),
                             _d("assertion", "max(silent) < "
                                             "DEFAULT_MIN_COVERAGE <= "
                                             "min(answers)"),
                             _d("margins", "%d points of slack below, %d above"
                                % (gate_pct - max_silent,
                                   min_answer - gate_pct)),
                             _d("derived", "every number here was parsed off "
                                           "`cov N%%` at min_coverage=0.0; none "
                                           "is typed in this file")],
                     text="")
        suite.note("  measured window: max silent %d%%  <  gate %d%%  <=  "
                   "thinnest answer %d%%" % (max_silent, gate_pct, min_answer))

        problems = []
        if near["best_pct"] != silent_best[Q_SILENT_NEAR]:
            problems.append("the gate message reports best coverage %r%% but the "
                            "ungated run's best hit renders %d%% -- the message "
                            "is an artefact of truncation, not the true best"
                            % (near["best_pct"], silent_best[Q_SILENT_NEAR]))
        suite.record("D", "pre-gate-best-matches-gate-message", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("gated", "best coverage %r%%" % near["best_pct"]),
                             _d("ungated", "top hit cov %d%%"
                                % silent_best[Q_SILENT_NEAR]),
                             _d("why", "the gate runs BEFORE `limit`, so the "
                                       "reported best must be the corpus best")],
                     text=near["text"])

        # ============ E: missed / unknown speak only when they must ==========
        full = answered[Q_GATE_ADR]
        problems = []
        if not full["hits"]:
            problems.append("no hits to inspect")
        else:
            top = full["hits"][0]
            if top["missed"]:
                problems.append("a full match still printed missed: %r"
                                % top["missed"])
        if full["missed_lines"]:
            problems.append("%r line(s) present although every term matched: %r"
                            % (len(full["missed_lines"]), full["missed_lines"]))
        suite.record("E", "full-match-emits-no-missed", problems,
                     detail=[_d("query", repr(Q_GATE_ADR)),
                             _d("hits", "%r" % full["header_hits"]),
                             _d("missed", "%r" % full["missed_lines"]),
                             _d("contract", "a full match costs nothing to "
                                            "render")],
                     text=full["text"])

        spared = answered[Q_SPARED]
        problems = []
        if not spared["hits"]:
            problems.append("no hits to inspect")
        else:
            top = spared["hits"][0]
            if top["missed"] != ["quantile"]:
                problems.append("missed %r, want exactly ['quantile']"
                                % top["missed"])
        if len(spared["missed_lines"]) != 1:
            problems.append("%d missed: line(s), want exactly 1"
                            % len(spared["missed_lines"]))
        suite.record("E", "partial-match-names-exactly-the-gap", problems,
                     detail=[_d("query", repr(Q_SPARED)),
                             _d("missed", "%r" % (spared["hits"][0]["missed"]
                                                  if spared["hits"] else None)),
                             _d("contract", "name the terms the page does NOT "
                                            "answer -- no more, no fewer")],
                     text=spared["text"])

        problems = []
        if full["has_unknown_line"]:
            problems.append("%r printed although every term has df > 0 (%r)"
                            % (UNKNOWN_MSG, full["unknown"]))
        suite.record("E", "unknown-silent-when-corpus-knows-all", problems,
                     detail=[_d("query", repr(Q_GATE_ADR)),
                             _d("df", "coverage=%d relevance=%d gate=%d"
                                % (df["coverage"], df["relevance"], df["gate"])),
                             _d("unknown", "line present=%r"
                                % full["has_unknown_line"])],
                     text=full["text"])

        far_probe = drv.measure(Q_SILENT_FAR)
        want_unknown = [t for t in far_probe["terms"] if far_probe["df"][t] == 0]
        problems = []
        if not far["has_unknown_line"]:
            problems.append("no %r line" % UNKNOWN_MSG)
        if far["unknown"] != want_unknown:
            problems.append("unknown %r, want %r (query order, df 0 only)"
                            % (far["unknown"], want_unknown))
        suite.record("E", "unknown-lists-exactly-the-df0-terms", problems,
                     detail=[_d("query", repr(Q_SILENT_FAR)),
                             _d("rendered", "%r" % far["unknown"]),
                             _d("measured", "%r" % want_unknown),
                             _d("derived", "the expected list is the df table's "
                                           "df==0 subset, not a literal")],
                     text=far["text"])

        # ============ F: floored, never rounded ============
        tight = drv.search(Q_SILENT_NEAR, min_coverage=0.999)
        problems = []
        if tight["need_pct"] != 99:
            problems.append("need %r%%, want 99%% (floor(99.9), not round)"
                            % tight["need_pct"])
        if "need 100%" in tight["text"]:
            problems.append("rendered 'need 100%' for min_coverage=0.999 -- a "
                            "rounded bar is a bar nobody asked for")
        suite.record("F", "need-percent-floored-not-rounded", problems,
                     detail=[_d("min_coverage", "0.999"),
                             _d("floor", "floor(100 * 0.999) = 99"),
                             _d("round", "round(100 * 0.999) = 100"),
                             _d("rendered", "need %r%%" % tight["need_pct"])],
                     text=tight["text"])

        floor_run = drv.search(Q_FLOOR)
        floor_probe = drv.measure(Q_FLOOR)
        exact = floor_probe["cov"][SLUG_TO_REL["relevance-gate"]]
        problems = []
        top = floor_run["hits"][0] if floor_run["hits"] else None
        if top is None:
            problems.append("no hit to inspect")
        else:
            if top["cov"] != pct(exact):
                problems.append("rendered cov %d%%, want floor(100 * %.6f) = %d"
                                % (top["cov"], exact, pct(exact)))
            if top["cov"] == naive_round(exact):
                problems.append("rendered cov %d%% equals the ROUNDED value -- "
                                "the display is rounding, not flooring"
                                % top["cov"])
        if pct(exact) == naive_round(exact):
            problems.append("fixture drift: floor and round agree on %.6f, so "
                            "this case no longer discriminates between them"
                            % exact)
        suite.record("F", "hit-cov-floored-not-rounded", problems,
                     detail=[_d("query", repr(Q_FLOOR)),
                             _d("exact", "%.6f" % exact),
                             _d("floor", "%d" % pct(exact)),
                             _d("round", "%d" % naive_round(exact)),
                             _d("rendered", "%r" % (top["cov"] if top else None)),
                             _d("source", "exact value recomputed from the "
                                          "module's own _prefix_count + idf")],
                     text=floor_run["text"])

        ungated = drv.search(Q_SILENT_NEAR, min_coverage=0.0)
        near_probe = drv.measure(Q_SILENT_NEAR)
        problems = []
        rows, discriminating = [], 0
        for hit in ungated["hits"]:
            rel = SLUG_TO_REL.get(hit["slug"], hit["slug"])
            value = near_probe["cov"].get(rel)
            if value is None:
                problems.append("hit %r is not a fixture page" % hit["slug"])
                continue
            want, rounded = pct(value), naive_round(value)
            rows.append("%-22s exact %.6f floor %3d round %3d rendered %3d"
                        % (rel, value, want, rounded, hit["cov"]))
            if want != rounded:
                discriminating += 1
            if hit["cov"] != want:
                problems.append("%s: rendered %d%%, floor is %d%% (round would "
                                "be %d%%)" % (rel, hit["cov"], want, rounded))
        if len(ungated["hits"]) != len(PAGES):
            problems.append("swept %d hit(s), the fixture has %d pages"
                            % (len(ungated["hits"]), len(PAGES)))
        if not discriminating:
            problems.append("no page in the sweep has a coverage where floor and "
                            "round differ, so the sweep proves nothing")
        suite.record("F", "every-rendered-cov-is-floored", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("min_coverage", "0.0 (gate off, all pages "
                                                "visible)"),
                             _d("discrim.", "%d of %d rows would render "
                                            "differently under rounding"
                                % (discriminating, len(rows)))]
                            + ["        " + r for r in rows],
                     text=ungated["text"])

        problems = []
        need = near["need_pct"]
        if need is None:
            problems.append("the gated answer reports no `need` percentage")
        else:
            for hit in ungated["hits"]:
                if hit["cov"] >= need:
                    problems.append("%s renders cov %d%% while the gate says it "
                                    "needs %d%% -- an excluded page reads as "
                                    "admissible" % (hit["slug"], hit["cov"], need))
        if near["best_pct"] is not None and need is not None:
            if near["best_pct"] >= need:
                problems.append("the refusal claims best coverage %d%% against a "
                                "need of %d%%, contradicting itself"
                                % (near["best_pct"], need))
        suite.record("F", "excluded-page-never-looks-admissible", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("need", "%r%%" % need),
                             _d("rendered", "%r" % [h["cov"]
                                                    for h in ungated["hits"]]),
                             _d("why", "rounding 54.6%% up to 55%% against a "
                                       "55%% gate would make the refusal line "
                                       "contradict itself")],
                     text=near["text"])

        # ============ G: min_coverage is honored ============
        problems = []
        if ungated["header_hits"] != len(PAGES):
            problems.append("header claims %r hit(s), want %d (every lexically "
                            "matching page)"
                            % (ungated["header_hits"], len(PAGES)))
        got_slugs = sorted(h["slug"] for h in ungated["hits"])
        want_slugs = sorted(SLUG_TO_REL)
        if got_slugs != want_slugs:
            problems.append("slugs %r, want %r" % (got_slugs, want_slugs))
        suite.record("G", "min-coverage-zero-restores-pre-gate", problems,
                     detail=[_d("query", repr(Q_SILENT_NEAR)),
                             _d("min_coverage", "0.0"),
                             _d("hits", "%r of %d pages"
                                % (ungated["header_hits"], len(PAGES))),
                             _d("why", "the escape hatch: 0.0 is the behaviour "
                                       "that shipped before the gate, so a "
                                       "regression stays reachable in one param")],
                     text=ungated["text"])

        strict = drv.search(Q_GATE_ADR, min_coverage=1.0)
        problems = []
        if not strict["hits"]:
            problems.append("min_coverage 1.0 dropped a page that matches EVERY "
                            "term")
        for hit in strict["hits"]:
            if hit["cov"] != 100:
                problems.append("%s survives at cov %d%%, not 100%%"
                                % (hit["slug"], hit["cov"]))
            if hit["missed"]:
                problems.append("%s survives with unmatched terms %r"
                                % (hit["slug"], hit["missed"]))
        if strict["missed_lines"]:
            problems.append("missed: line(s) under a total-coverage gate: %r"
                            % strict["missed_lines"])
        suite.record("G", "min-coverage-one-demands-every-term", problems,
                     detail=[_d("query", repr(Q_GATE_ADR)),
                             _d("min_coverage", "1.0"),
                             _d("survivors", "%r"
                                % [(h["slug"], h["cov"]) for h in strict["hits"]]),
                             _d("contract", "only pages matching every single "
                                            "term survive")],
                     text=strict["text"])

        strict_unknown = drv.search(Q_SPARED, min_coverage=1.0)
        problems = []
        if strict_unknown["hits"]:
            problems.append("a page passed a 100%% gate although `quantile` has "
                            "df 0: %r" % [h["slug"]
                                          for h in strict_unknown["hits"]])
        if not strict_unknown["has_gate_msg"]:
            problems.append("missing %r" % GATE_MSG)
        if strict_unknown["need_pct"] != 100:
            problems.append("need %r%%, want 100%%" % strict_unknown["need_pct"])
        suite.record("G", "min-coverage-one-with-unknown-empties", problems,
                     detail=[_d("query", repr(Q_SPARED)),
                             _d("min_coverage", "1.0"),
                             _d("hits", "%r" % strict_unknown["header_hits"]),
                             _d("why", "a df-0 term is unmatchable, so total "
                                       "coverage is unreachable by construction")],
                     text=strict_unknown["text"])

        accepted = drv.mod.HANDLER_ACCEPTED_PARAMS["search"]
        live = drv.search(Q_GATE_ADR, min_coverage=0.5)
        problems = []
        if "min_coverage" not in accepted:
            problems.append("'min_coverage' missing from "
                            "HANDLER_ACCEPTED_PARAMS['search'] -- the "
                            "dispatcher would reject every caller that sets it")
        if live["error"] or "Unknown params" in live["text"]:
            problems.append("a call carrying min_coverage was refused: %s"
                            % live["text"][:160])
        suite.record("G", "min-coverage-is-an-accepted-param", problems,
                     detail=[_d("accepted", "%r" % sorted(accepted)),
                             _d("live call", "min_coverage=0.5 -> %r hit(s), "
                                             "error=%r"
                                % (live["header_hits"], live["error"]))],
                     text=live["text"])

        garbage = drv.search(Q_SILENT_NEAR, min_coverage="abc")
        problems = []
        if garbage["error"]:
            problems.append("a garbage min_coverage produced an error: %s"
                            % garbage["text"][:160])
        if garbage["need_pct"] != gate_pct:
            problems.append("need %r%%, want the default's %d%%"
                            % (garbage["need_pct"], gate_pct))
        if garbage["text"] != near["text"]:
            problems.append("the reply differs from the no-param reply, so the "
                            "fallback is not the default")
        suite.record("G", "garbage-min-coverage-falls-back", problems,
                     detail=[_d("min_coverage", "'abc'"),
                             _d("need", "%r%% (default is %d%%)"
                                % (garbage["need_pct"], gate_pct)),
                             _d("identical", "%r" % (garbage["text"]
                                                     == near["text"])),
                             _d("contract", "float('abc') raises, and the "
                                            "handler must absorb it instead of "
                                            "500-ing")],
                     text=garbage["text"])

        # ============ I: the query-side stopword drop ============
        # What counts as a query TERM is not a tokenizer detail here: `terms` is
        # the denominator of the coverage gate, so dropping a word changes every
        # percentage the previous eight groups measure.  These six cases pin the
        # drop itself -- which words go, that the answer admits it, that an
        # all-function-words question gets its own refusal, that the drop really
        # re-orders the ranking, and that the two deliberate NON-drops survive.
        #
        # Every expected word list is derived through `drv.split_query`, which
        # reads `mod.QUERY_STOPWORDS` live.  Each case therefore also asserts its
        # OWN premise (that the query does mix the two kinds of word, that a
        # "clean" query really has no function words, that df[search] > 0): a
        # derived expectation can collapse into `[] == []` and pass while proving
        # nothing, and that is worse than a red case.
        mixed = drv.search(Q_MIXED)
        mixed_dropped, mixed_kept = drv.split_query(Q_MIXED)
        problems = []
        if not mixed_dropped or not mixed_kept:
            problems.append("fixture drift: %r no longer MIXES the two kinds of "
                            "word (function=%r content=%r), so this case cannot "
                            "test the mixture"
                            % (Q_MIXED, mixed_dropped, mixed_kept))
        if not mixed["has_ignored_line"]:
            problems.append("no %r line although %r were dropped"
                            % (IGNORED_MSG, mixed_dropped))
        if mixed["ignored"] != mixed_dropped:
            problems.append("ignored %r, want exactly %r (query order)"
                            % (mixed["ignored"], mixed_dropped))
        wrongly_named = [t for t in mixed["ignored"] if t in mixed_kept]
        if wrongly_named:
            problems.append("content word(s) %r reported as function words -- the "
                            "caller is told the search ignored what it searched "
                            "for" % wrongly_named)
        undisclosed = [t for t in mixed_dropped if t not in mixed["ignored"]]
        if undisclosed:
            problems.append("function word(s) %r were dropped but never named"
                            % undisclosed)
        if not mixed["hits"]:
            problems.append("the query lost its answer (%r hit(s)) -- the "
                            "disclosure must ride along with a ranking, not "
                            "replace it" % mixed["header_hits"])
        suite.record("I", "ignored-line-names-exactly-the-dropped-words", problems,
                     detail=[_d("query", repr(Q_MIXED)),
                             _d("rendered", "%r" % mixed["ignored"]),
                             _d("dropped", "%r" % mixed_dropped),
                             _d("kept", "%r" % mixed_kept),
                             _d("hits", "%r" % mixed["header_hits"]),
                             _d("derived", "both lists come from "
                                           "mod.QUERY_STOPWORDS, not from a copy "
                                           "of it in this file")],
                     text=mixed["text"])

        clean_dropped, clean_kept = drv.split_query(Q_GATE_ADR)
        clean = answered[Q_GATE_ADR]
        problems = []
        if clean_dropped:
            problems.append("fixture drift: %r carries function word(s) %r, so "
                            "'no line' would prove nothing about silence"
                            % (Q_GATE_ADR, clean_dropped))
        if not clean_kept:
            problems.append("fixture drift: %r has no content terms at all"
                            % Q_GATE_ADR)
        if clean["has_ignored_line"]:
            problems.append("%r printed for a query with no function words: %r"
                            % (IGNORED_MSG, clean["ignored"]))
        if IGNORED_MSG in clean["text"]:
            problems.append("the phrase %r appears in the answer anyway (in some "
                            "other shape than a header line)" % IGNORED_MSG)
        suite.record("I", "clean-query-emits-no-ignored-line", problems,
                     detail=[_d("query", repr(Q_GATE_ADR)),
                             _d("premise", "function words in the query: %r"
                                % clean_dropped),
                             _d("line", "present=%r" % clean["has_ignored_line"]),
                             _d("contract", "silence when there is nothing to "
                                            "report -- a clean query pays nothing "
                                            "for the disclosure")],
                     text=clean["text"])

        allfunc = drv.search(Q_ALL_FUNCTION)
        af_dropped, af_kept = drv.split_query(Q_ALL_FUNCTION)
        problems = []
        if af_kept:
            problems.append("fixture drift: %r still carries content term(s) %r, "
                            "so it can never reach the third silence"
                            % (Q_ALL_FUNCTION, af_kept))
        if allfunc["error"]:
            problems.append("call failed: %s" % allfunc["text"][:160])
        if not allfunc["has_all_func_msg"]:
            problems.append("missing %r" % ALL_FUNC_MSG)
        if allfunc["all_func_words"] != af_dropped:
            problems.append("names %r, want exactly %r -- the caller has to see "
                            "WHICH words were the whole query"
                            % (allfunc["all_func_words"], af_dropped))
        if allfunc["has_no_match_msg"]:
            problems.append("also says %r, which blames the wiki for a question "
                            "that asked nothing" % NO_MATCH_MSG)
        if allfunc["has_gate_msg"]:
            problems.append("also says %r, but nothing was ever ranked" % GATE_MSG)
        if allfunc["header_hits"] != 0 or allfunc["hits"]:
            problems.append("expected zero hits, got header=%r lines=%d"
                            % (allfunc["header_hits"], len(allfunc["hits"])))
        suite.record("I", "all-function-words-gets-its-own-refusal", problems,
                     detail=[_d("query", repr(Q_ALL_FUNCTION)),
                             _d("named", "%r" % allfunc["all_func_words"]),
                             _d("dropped", "%r" % af_dropped),
                             _d("borrowed", "no-match=%r gate=%r"
                                % (allfunc["has_no_match_msg"],
                                   allfunc["has_gate_msg"])),
                             _d("why", "the corpus is never even walked, so both "
                                       "of the other refusals would be false")],
                     text=allfunc["text"])

        def sentinels(res):
            return (res["has_no_match_msg"], res["has_gate_msg"],
                    res["has_all_func_msg"])

        trio = {"nothing-matched": sentinels(nonsense),
                "gated-out": sentinels(near),
                "all-function": sentinels(allfunc)}
        want_trio = {"nothing-matched": (True, False, False),
                     "gated-out": (False, True, False),
                     "all-function": (False, False, True)}
        problems = []
        for label in sorted(want_trio):
            if trio[label] != want_trio[label]:
                problems.append("%s renders (no_match, gate, all_func) = %r, want "
                                "%r" % (label, trio[label], want_trio[label]))
        if len(set(trio.values())) != len(trio):
            problems.append("two of the THREE silences render the same sentinel "
                            "triple -- the caller cannot tell them apart, and "
                            "each one implies a different next move")
        suite.record("I", "three-silences-mutually-distinct", problems,
                     detail=[_d(label, "%r" % (trio[label],))
                             for label in sorted(trio)]
                            + [_d("moves", "rephrase / the topic is undocumented "
                                           "/ ask an actual question"),
                               _d("contract", "exactly one sentinel per answer, "
                                              "and never the same one twice")],
                     text=allfunc["text"])

        # The old server is gone, so "the decoy used to win this" is not runnable.
        # What IS runnable is the invariant that made it a bug: a page which
        # answers NOTHING must never be rank 1.  Driven at min_coverage=0.0 on
        # purpose -- with the gate off, the ORDER is the only thing under test, so
        # a pass cannot be an accident of the decoy being filtered out.
        decoy_run = drv.search(Q_DECOY, min_coverage=0.0)
        dq_dropped, dq_kept = drv.split_query(Q_DECOY)
        ranks = {h["slug"]: h["rank"] for h in decoy_run["hits"]}
        problems = []
        if not dq_dropped:
            problems.append("fixture drift: %r carries no function words, so the "
                            "decoy was never in the contest" % Q_DECOY)
        if DECOY_SLUG not in ranks:
            problems.append("the decoy %r is not a candidate at all, so "
                            "'the content page outranks it' is vacuous"
                            % DECOY_SLUG)
        elif ranks[DECOY_SLUG] == 1:
            problems.append("the decoy %r is rank 1 -- it carries the query's "
                            "function words and one content term, and it is "
                            "beating the page that carries the rest"
                            % DECOY_SLUG)
        if not decoy_run["hits"]:
            problems.append("no hits at min_coverage=0.0")
        else:
            top = decoy_run["hits"][0]
            if top["slug"] != CONTENT_SLUG:
                problems.append("rank 1 is %r, want the content page %r"
                                % (top["slug"], CONTENT_SLUG))
            if set(top["missed"]) >= set(dq_kept):
                problems.append("rank 1 (%s) missed EVERY content term %r -- a "
                                "page that answers nothing is winning the query"
                                % (top["slug"], dq_kept))
        suite.record("I", "stopword-drop-reorders-the-ranking", problems,
                     detail=[_d("query", repr(Q_DECOY)),
                             _d("min_coverage", "0.0 (gate off: order is the only "
                                                "thing under test)"),
                             _d("dropped", "%r" % dq_dropped),
                             _d("content", "%r" % dq_kept),
                             _d("ranking", "%r" % [(h["rank"], h["slug"],
                                                    h["cov"], h["missed"])
                                                   for h in decoy_run["hits"]]),
                             _d("invariant", "the winner's `missed:` must not be "
                                             "the whole content term list")],
                     text=decoy_run["text"])

        keep = drv.search(Q_KEEP, min_coverage=0.0)
        keep_probe = drv.measure(Q_KEEP)
        kp_dropped, kp_kept = drv.split_query(Q_KEEP)
        want_keep_hits = keep_probe["df"].get("search", 0)
        problems = []
        for word in KEEP_WORDS:
            if word in drv.mod.QUERY_STOPWORDS:
                problems.append("%r is in QUERY_STOPWORDS -- in THIS corpus it is "
                                "a function name / a frontmatter status value, not "
                                "a function word, and dropping it answers the "
                                "wrong question" % word)
            if word not in kp_kept:
                problems.append("%r did not survive tokenize+drop (kept=%r)"
                                % (word, kp_kept))
        if kp_dropped:
            problems.append("%r dropped %r, but neither of %r is a function word"
                            % (Q_KEEP, kp_dropped, list(KEEP_WORDS)))
        if keep["has_ignored_line"]:
            problems.append("%r printed for %r: %r"
                            % (IGNORED_MSG, Q_KEEP, keep["ignored"]))
        if keep["has_all_func_msg"]:
            problems.append("refused as all-function-words -- the whole query was "
                            "thrown away")
        if keep["unknown"] != ["done"]:
            problems.append("unknown %r, want ['done'] -- `done` can only be "
                            "reported unknown if it reached the df table, which is "
                            "the observable proof it was not dropped"
                            % keep["unknown"])
        if want_keep_hits < 1:
            problems.append("fixture drift: df[search]=0, so `search` surviving "
                            "is unobservable through the hit list")
        if len(keep["hits"]) != want_keep_hits:
            problems.append("%d hit(s), want df[search]=%d -- `search` is the only "
                            "term that can pull a page in here"
                            % (len(keep["hits"]), want_keep_hits))
        for hit in keep["hits"]:
            if hit["missed"] != ["done"]:
                problems.append("%s missed %r, want ['done']"
                                % (hit["slug"], hit["missed"]))
        suite.record("I", "query-verb-and-done-are-not-stopwords", problems,
                     detail=[_d("query", repr(Q_KEEP)),
                             _d("in stoplist", "%r"
                                % {w: w in drv.mod.QUERY_STOPWORDS
                                   for w in KEEP_WORDS}),
                             _d("kept", "%r" % kp_kept),
                             _d("unknown", "%r (df[done]=%d)"
                                % (keep["unknown"],
                                   keep_probe["df"].get("done", -1))),
                             _d("hits", "%d, df[search]=%d"
                                % (len(keep["hits"]), want_keep_hits)),
                             _d("contract", "a future 'let us just use a standard "
                                            "stoplist' edit has to fail HERE, "
                                            "loudly")],
                     text=keep["text"])
    finally:
        work.cleanup()

    # ============ J: `get_page`'s section index ============
    # A SEPARATE workspace and a SEPARATE three-page corpus: the six pages above
    # are group D's measured calibration window, so a page added there for a
    # `get_page` case would move every idf and fail a group it has nothing to do
    # with.  Nothing below touches `search`.
    sec_work = H.TempWorkspace("ph-wiki-sections-", keep=opts.keep)
    try:
        sec_root = build_section_fixture(sec_work)
        sdrv = Driver(sec_root)

        # The oracles: the RAW fixture file, scanned naively.  Every expectation
        # in this group is derived from these, never typed.
        raw = file_lines(sec_root, LAYERED_FILE)
        heads = fixture_headings(raw)
        offset = fixture_body_offset(raw)
        h1 = [h for h in heads if h[1] == 1]
        want_l2 = [h for h in heads if h[1] == 2]
        want_deep = [h for h in heads if h[1] > 2]
        want_all = [h for h in heads if h[1] >= 2]
        deepest = max([h[1] for h in want_deep] or [2])
        _sec_fm, layered_body = sdrv.mod.read_page(
            os.path.join(sec_root, WIKI_REL, LAYERED_FILE))
        # A body line that is neither blank nor a heading: the marker for "the
        # body was served" / "the body was withheld".
        body_probe = [ln for ln in layered_body.splitlines()
                      if ln.strip() and not ln.startswith("#")][0]

        miss = sdrv.get_page(LAYERED_SLUG, section=MISSING_SECTION)
        deep_miss = sdrv.get_page(LAYERED_SLUG, section=MISSING_SECTION,
                                  depth=deepest)

        problems = []
        if miss["error"]:
            problems.append("call failed: %s" % miss["text"][:160])
        if miss["not_found"] != repr(MISSING_SECTION):
            problems.append("the refusal names %r, want %r -- the OLD contract "
                            "(the caller learns its section did not match) has "
                            "to survive the new block"
                            % (miss["not_found"], repr(MISSING_SECTION)))
        if not want_l2:
            problems.append("fixture drift: the page carries no level-2 heading, "
                            "so 'it lists them' proves nothing")
        if not miss["has_sections_label"]:
            problems.append("no %r label: the refusal still does not say what IS "
                            "there, so the caller's only remaining move is the "
                            "whole page again -- the circle this block cuts"
                            % SECTIONS_LABEL)
        if miss["names"] != [h[2] for h in want_l2]:
            problems.append("listed %r, want %r (every level-2 heading of the "
                            "fixture file, in document order)"
                            % (miss["names"], [h[2] for h in want_l2]))
        suite.record("J", "missing-section-lists-what-is-there", problems,
                     detail=[_d("call", "get_page %r section=%r"
                                % (LAYERED_SLUG, MISSING_SECTION)),
                             _d("refusal", "%s" % miss["not_found"]),
                             _d("listed", "%r" % miss["names"]),
                             _d("oracle", "%r (naive ATX scan of the fixture "
                                          "file)" % [h[2] for h in want_l2]),
                             _d("measured", "4 real pages cost 2035c of refusals "
                                            "against 115026c of full-body "
                                            "re-reads without this block")],
                     text=miss["text"])

        problems = []
        rows = []
        if offset <= 1:
            problems.append("fixture drift: the body starts on file line %d, so "
                            "body-relative and file-relative numbering AGREE and "
                            "this case cannot fail" % offset)
        if not miss["sections"]:
            problems.append("no index lines to check")
        if len(miss["with_line"]) != len(miss["sections"]):
            problems.append("%d of %d line(s) carry no L at all -- the offset is "
                            "establishable here (the file is readable), so "
                            "dropping it is the OSError arm firing on the happy "
                            "path" % (len(miss["sections"])
                                      - len(miss["with_line"]),
                                      len(miss["sections"])))
        for sec in miss["with_line"]:
            n = sec["line"]
            if not 1 <= n <= len(raw):
                problems.append("%s: L%d is outside the %d-line file"
                                % (sec["name"], n, len(raw)))
                continue
            m = _ATX_RE.match(raw[n - 1])
            if not m or m.group("text") != sec["name"]:
                problems.append("%s: file line %d is %r, not that heading -- L%d "
                                "is NOT a file line"
                                % (sec["name"], n, raw[n - 1], n))
            body_rel = n - (offset - 1)
            other = raw[body_rel - 1] if 1 <= body_rel <= len(raw) else "<off file>"
            if other == raw[n - 1]:
                problems.append("%s: the body-relative line %d carries the same "
                                "text, so the two readings are indistinguishable "
                                "here and the case is blind"
                                % (sec["name"], body_rel))
            rows.append("%-16s L%-3d %-34r   body-relative L%-3d %r"
                        % (sec["name"], n, raw[n - 1][:34], body_rel,
                           other[:34]))
        suite.record("J", "index-line-numbers-are-file-relative", problems,
                     detail=[_d("frontmatter", "%d line(s); the body's first line "
                                               "is file line %d"
                                % (offset - 1, offset)),
                             _d("checked", "%d line(s), each against the FILE"
                                % len(miss["sections"])),
                             _d("why", "read_page hands the body back with the "
                                       "frontmatter stripped, so a body-relative "
                                       "number is short by exactly the header "
                                       "height -- silently, and on every page")]
                            + ["        " + r for r in rows],
                     text=miss["text"])

        # The PAIR of the case above.  When the file cannot be re-read the file
        # line is not knowable, and the server drops the L instead of guessing
        # one -- so this case has to assert both halves: that the number is GONE
        # (the ban) and that everything else survived it (the preservation).
        # `_section_index_block` is driven DIRECTLY, because an unreadable path
        # is the whole input and `get_page` would never hand it one.
        good_path = os.path.join(sec_root, WIKI_REL, LAYERED_FILE)
        gone_path = os.path.join(sec_root, WIKI_REL, UNREADABLE_FILE)
        # Depth 2 on purpose: at that depth the block carries the label, the
        # dash lines AND the hint, so "everything else survived" is a claim
        # about all three, not only about the lines.
        good_block = "\n".join(
            sdrv.mod._section_index_block(layered_body, good_path, 2))
        gone_block = "\n".join(
            sdrv.mod._section_index_block(layered_body, gone_path, 2))
        good_parsed, gone_parsed = parse_page(good_block), parse_page(gone_block)
        problems = []
        if os.path.exists(gone_path):
            problems.append("fixture drift: %r EXISTS, so the OSError arm was "
                            "never reached" % UNREADABLE_FILE)
        if not good_parsed["with_line"]:
            problems.append("the readable path printed no L at all, so 'the L is "
                            "gone' below distinguishes nothing")
        if gone_parsed["with_line"]:
            problems.append("%d line(s) still carry an L although the file could "
                            "not be read: %r -- the number is a guess, and a "
                            "guess the caller cannot tell from a fact"
                            % (len(gone_parsed["with_line"]),
                               [s["name"] for s in gone_parsed["with_line"]]))
        if re.search(r"\(L\d+", gone_block):
            problems.append("an L<n> survives somewhere in the block: %r"
                            % gone_block)
        if gone_parsed["sized"] != good_parsed["sized"]:
            problems.append("names/sizes changed with the offset: %r vs %r -- "
                            "only the line number is unknowable, the sizes came "
                            "from the body and are still facts"
                            % (gone_parsed["sized"], good_parsed["sized"]))
        # The strongest form: the whole block, byte for byte, minus the `L<n>, `.
        if _L_PREFIX_RE.sub(" (", good_block) != gone_block:
            problems.append("the block is not the readable one minus its L "
                            "prefixes:\n  want %r\n  got  %r"
                            % (_L_PREFIX_RE.sub(" (", good_block), gone_block))
        if _L_PREFIX_RE.sub(" (", good_block) == good_block:
            problems.append("the substitution changed nothing, so this "
                            "comparison is vacuous")
        suite.record("J", "unknowable-line-drops-only-the-number", problems,
                     detail=[_d("readable", "%d line(s), all with L: %r"
                                % (len(good_parsed["sections"]),
                                   len(good_parsed["with_line"])
                                   == len(good_parsed["sections"]))),
                             _d("unreadable", "%d line(s), %d with L"
                                % (len(gone_parsed["sections"]),
                                   len(gone_parsed["with_line"]))),
                             _d("sizes", "identical=%r"
                                % (gone_parsed["sized"] == good_parsed["sized"])),
                             _d("hint", "%r vs %r"
                                % (good_parsed["hint"], gone_parsed["hint"])),
                             _d("contract", "a silently-shifted number is worse "
                                            "than a missing one -- the caller "
                                            "cannot tell it is being misled")]
                            + ["        " + ln for ln in gone_block.split("\n")
                               if ln],
                     text=gone_block)

        listed_sizes = {}
        for answer in (miss, deep_miss):
            for sec in answer["sections"]:
                listed_sizes[sec["name"]] = sec["size"]
        problems = []
        rows = []
        for name in sorted(listed_sizes):
            advertised = listed_sizes[name]
            extracted = sdrv.mod._extract_section(layered_body, name)
            served = served_section(
                sdrv.get_page(LAYERED_SLUG, section=name)["text"], name)
            rows.append("%-22s advertised %4d   _extract_section %s   served %s"
                        % (name, advertised,
                           "%4d" % len(extracted) if extracted is not None
                           else "NONE",
                           "%4d" % len(served) if served is not None else "NONE"))
            if extracted is None:
                problems.append("%r is offered in the index but `section: %s` "
                                "extracts nothing" % (name, name))
                continue
            if advertised != len(extracted):
                problems.append("%s: the index advertises %dc, _extract_section "
                                "returns %dc" % (name, advertised, len(extracted)))
            if served is None:
                problems.append("%s: the served answer carries no such heading, "
                                "so the advertised size describes nothing"
                                % name)
            elif advertised != len(served):
                problems.append("%s: the index advertises %dc, the slice the "
                                "server actually SERVES is %dc"
                                % (name, advertised, len(served)))
        if len(set(listed_sizes.values())) < 2:
            problems.append("fixture drift: every listed section advertises the "
                            "same size %r, so a renderer printing one constant "
                            "would pass" % sorted(set(listed_sizes.values())))
        suite.record("J", "advertised-size-is-the-served-size", problems,
                     detail=[_d("checked", "%d section(s) across depth 2 and "
                                           "depth %d" % (len(listed_sizes),
                                                         deepest)),
                             _d("oracles", "mod._extract_section AND a live "
                                           "`section:` call, both per section"),
                             _d("why", "the size is the caller's decision basis "
                                       "-- a list that lies about it is worse "
                                       "than no list")]
                            + ["        " + r for r in rows],
                     text=miss["text"])

        problems = []
        if not want_deep:
            problems.append("fixture drift: nothing on the page is below level 2, "
                            "so the hint can never fire")
        if miss["hint"] is None:
            problems.append("no hint although the page carries %d heading(s) "
                            "below level 2 (%r)"
                            % (len(want_deep), [h[2] for h in want_deep]))
        else:
            hidden, escape = miss["hint"]
            if hidden != len(want_deep):
                problems.append("the hint claims %d hidden heading(s), the file "
                                "has %d" % (hidden, len(want_deep)))
            if escape != deepest:
                problems.append("the hint sends the caller to depth %d, the "
                                "deepest heading is level %d" % (escape, deepest))
            if deep_miss["hint"] is not None:
                problems.append("depth %d STILL prints a hint (%r) -- the escape "
                                "hatch it named does not empty it, so the caller "
                                "is sent one level down into the same dead end"
                                % (escape, deep_miss["hint"]))
            if deep_miss["has_hint_sentinel"]:
                problems.append("depth %d still mentions %r somewhere in the "
                                "answer" % (escape, HINT_SENTINEL))
            if deep_miss["names"] != [h[2] for h in want_all]:
                problems.append("depth %d lists %r, want every level>=2 heading "
                                "of the fixture file: %r"
                                % (escape, deep_miss["names"],
                                   [h[2] for h in want_all]))
            if len(deep_miss["sections"]) != len(miss["sections"]) + hidden:
                problems.append("depth %d shows %d section(s); depth 2 showed %d "
                                "and the hint promised %d more"
                                % (escape, len(deep_miss["sections"]),
                                   len(miss["sections"]), hidden))
        suite.record("J", "depth-hint-does-not-lie", problems,
                     detail=[_d("hint", "%r" % (miss["hint"],)),
                             _d("hidden", "%d heading(s) below level 2 in the "
                                          "file: %r"
                                % (len(want_deep), [h[2] for h in want_deep])),
                             _d("re-called", "depth %d -> %d line(s), hint %r"
                                % (deepest, len(deep_miss["sections"]),
                                   deep_miss["hint"])),
                             _d("gate", "the hint names its own escape hatch, so "
                                        "taking it must both empty the hint and "
                                        "produce exactly the promised extras")],
                     text=deep_miss["text"])

        # NOT "H1s are skipped" -- that was the proxy.  The rule is "a slice that
        # IS the page is not a section", and on THIS page the H1 is the heading
        # that satisfies it.  The premise is therefore measured, not assumed: if
        # the H1 ever stopped spanning the body, the correct answer would be to
        # LIST it, and this case has to fail rather than keep demanding silence.
        problems = []
        layered_whole = len(layered_body.strip())
        h1_slice = (sdrv.mod._extract_section(layered_body, h1[0][2])
                    if h1 else None)
        if not h1:
            problems.append("fixture drift: the page has no H1, so 'the H1 is "
                            "never listed' is vacuous")
        elif h1_slice is None or len(h1_slice) != layered_whole:
            problems.append("premise gone: the H1's slice is %r of a %dc body, "
                            "so it no longer spans the page -- it is a real "
                            "section now and hiding it would be the defect"
                            % (len(h1_slice) if h1_slice else None,
                               layered_whole))
        else:
            title = h1[0][2]
            for label, answer in (("depth 2", miss),
                                  ("depth %d" % deepest, deep_miss)):
                if title in answer["names"]:
                    problems.append("%s offers the H1 %r as a section -- its "
                                    "slice is the whole body (%dc), so it is the "
                                    "page under another name"
                                    % (label, title, layered_whole))
                if ("- %s (L" % title) in answer["text"]:
                    problems.append("%s renders a dash line for the H1 %r"
                                    % (label, title))
        suite.record("J", "h1-is-never-offered-as-a-section", problems,
                     detail=[_d("h1", "%r" % (h1[0][2] if h1 else None)),
                             _d("spans", "slice %r == body %dc"
                                % (len(h1_slice) if h1_slice else None,
                                   layered_whole)),
                             _d("depth 2", "%r" % miss["names"]),
                             _d("depth %d" % deepest, "%r" % deep_miss["names"]),
                             _d("why", "offering a page-spanning slice as a "
                                       "section is offering `include_body: true` "
                                       "again, under another name")],
                     text=deep_miss["text"])

        whole = sdrv.get_page(LAYERED_SLUG)
        hit_name = want_l2[0][2] if want_l2 else MISSING_SECTION
        hit = sdrv.get_page(LAYERED_SLUG, section=hit_name)
        problems = []
        if body_probe not in whole["text"]:
            problems.append("the default answer no longer carries the body "
                            "(%r missing), so 'no index leaked in' proves nothing"
                            % body_probe[:60])
        if served_section(hit["text"], hit_name) is None:
            problems.append("`section: %s` served no such heading, so this case "
                            "is not looking at the SUCCESS arm" % hit_name)
        for label, answer in (("include_body default", whole),
                              ("section hit", hit)):
            if answer["has_sections_label"]:
                problems.append("%s carries a %r block" % (label, SECTIONS_LABEL))
            if answer["sections"]:
                problems.append("%s carries %d index line(s): %r"
                                % (label, len(answer["sections"]),
                                   answer["names"]))
            if answer["hint"] is not None:
                problems.append("%s carries the depth hint %r"
                                % (label, answer["hint"]))
            if answer["empty_msgs"]:
                problems.append("%s carries %r" % (label, answer["empty_msgs"]))
        suite.record("J", "working-branches-carry-no-index", problems,
                     detail=[_d("default", "body served=%r, index lines=%d"
                                % (body_probe in whole["text"],
                                   len(whole["sections"]))),
                             _d("hit", "section %r served=%r, index lines=%d"
                                % (hit_name,
                                   served_section(hit["text"], hit_name)
                                   is not None, len(hit["sections"]))),
                             _d("contract", "the block belongs to the two answers "
                                            "that carry NO body; a caller who got "
                                            "what it asked for pays nothing")],
                     text=hit["text"])

        nobody = sdrv.get_page(LAYERED_SLUG, include_body=False)
        problems = []
        if not nobody["has_sections_label"]:
            problems.append("no %r block: include_body false still drops the "
                            "headings along with the body, which was the real "
                            "leak -- the 'cheap' call teaches the caller nothing "
                            "and it re-reads the page" % SECTIONS_LABEL)
        if body_probe in nobody["text"]:
            problems.append("the body came back anyway (%r), so include_body "
                            "false is not doing its own half" % body_probe[:60])
        if triples(nobody) != triples(miss):
            problems.append("the block differs from the missing-section arm's: "
                            "%r vs %r" % (triples(nobody), triples(miss)))
        if nobody["hint"] != miss["hint"]:
            problems.append("hint %r, the other arm renders %r -- one block, two "
                            "renderings" % (nobody["hint"], miss["hint"]))
        suite.record("J", "include-body-false-carries-the-index", problems,
                     detail=[_d("call", "get_page %r include_body=False"
                                % LAYERED_SLUG),
                             _d("listed", "%r" % nobody["names"]),
                             _d("body", "withheld=%r"
                                % (body_probe not in nobody["text"])),
                             _d("identical", "same triples as the missing-section "
                                             "arm: %r"
                                % (triples(nobody) == triples(miss)))],
                     text=nobody["text"])

        flat_heads = fixture_headings(file_lines(sec_root, FLAT_FILE))
        flat_nobody = sdrv.get_page(FLAT_SLUG, include_body=False)
        flat_miss = sdrv.get_page(FLAT_SLUG, section=MISSING_SECTION)
        problems = []
        if flat_heads:
            problems.append("fixture drift: the page grew heading(s) %r"
                            % [h[2] for h in flat_heads])
        for label, answer in (("include_body false", flat_nobody),
                              ("missing section", flat_miss)):
            if answer["empty_msgs"] != [NO_HEADINGS_MSG]:
                problems.append("%s rendered %r, want exactly [%r]"
                                % (label, answer["empty_msgs"], NO_HEADINGS_MSG))
            if answer["has_no_sections_msg"]:
                problems.append("%s says %r on a page that really has NO heading "
                                "-- that sentence belongs to the page whose only "
                                "heading is its title"
                                % (label, NO_SECTIONS_MSG))
            if answer["has_sections_label"]:
                problems.append("%s renders an EMPTY %r label"
                                % (label, SECTIONS_LABEL))
            if answer["sections"]:
                problems.append("%s listed %r on a page with no headings"
                                % (label, answer["names"]))
            if answer["hint"] is not None:
                problems.append("%s offers a depth escape (%r) on a page with no "
                                "headings at all" % (label, answer["hint"]))
        suite.record("J", "page-without-headings-says-so", problems,
                     detail=[_d("page", "%r, %d heading(s) in the file"
                                % (FLAT_SLUG, len(flat_heads))),
                             _d("both arms", "%r / %r"
                                % (flat_nobody["empty_msgs"],
                                   flat_miss["empty_msgs"])),
                             _d("contract", "an empty `sections:` label reads as "
                                            "a rendering bug; the sentence reads "
                                            "as an answer")],
                     text=flat_nobody["text"])

        # The page that is CONFUSABLE with the one above and must not be
        # confused with it: it HAS a heading, the index skips it on purpose, and
        # "this page has no headings" would simply be false.  The two sentences
        # imply opposite next moves -- read the page vs. look elsewhere -- so the
        # gate here is that they are never the same string.
        title_raw = file_lines(sec_root, TITLE_FILE)
        title_heads = fixture_headings(title_raw)
        title_nobody = sdrv.get_page(TITLE_SLUG, include_body=False)
        title_miss = sdrv.get_page(TITLE_SLUG, section=MISSING_SECTION)
        problems = []
        if [h[1] for h in title_heads] != [1]:
            problems.append("fixture drift: the page's heading levels are %r, "
                            "want exactly [1] -- one H1 and nothing else is the "
                            "whole point of this page"
                            % [h[1] for h in title_heads])
        for label, answer in (("include_body false", title_nobody),
                              ("missing section", title_miss)):
            if answer["empty_msgs"] != [NO_SECTIONS_MSG]:
                problems.append("%s rendered %r, want exactly [%r]"
                                % (label, answer["empty_msgs"], NO_SECTIONS_MSG))
            if answer["has_no_headings_msg"]:
                problems.append("%s claims the page has NO headings, but it has "
                                "%d -- the index declined to offer the H1, which "
                                "is not the same fact"
                                % (label, len(title_heads)))
            if answer["sections"] or answer["has_sections_label"]:
                problems.append("%s listed %r / label=%r for an H1-only page"
                                % (label, answer["names"],
                                   answer["has_sections_label"]))
            if answer["hint"] is not None:
                problems.append("%s offers a depth escape (%r); no depth reaches "
                                "a heading that is skipped by LEVEL, so the "
                                "escape hatch leads nowhere"
                                % (label, answer["hint"]))
        if title_nobody["empty_msgs"] == flat_nobody["empty_msgs"]:
            problems.append("the H1-only page and the heading-less page render "
                            "the SAME sentence (%r) -- one of the two is being "
                            "told something untrue, and the caller cannot tell "
                            "which" % title_nobody["empty_msgs"])
        suite.record("J", "h1-only-and-headingless-are-different-empties",
                     problems,
                     detail=[_d("h1-only", "%r -> %r"
                                % (TITLE_SLUG, title_nobody["empty_msgs"])),
                             _d("headingless", "%r -> %r"
                                % (FLAT_SLUG, flat_nobody["empty_msgs"])),
                             _d("headings", "%r vs %r"
                                % ([h[2] for h in title_heads],
                                   [h[2] for h in flat_heads])),
                             _d("moves", "'no section is smaller than the page' "
                                         "-> ask without a section; 'no "
                                         "headings' -> there is nothing here to "
                                         "slice at all"),
                             _d("gate", "the two sentences must never be equal, "
                                        "so a merge cannot pass")],
                     text=title_nobody["text"])

        # The page where the OLD rule and the new one disagree.  `level < 2` was
        # a proxy for "this slice is the whole page", exact only while no page
        # carries a second H1 -- and two H1s BOUND each other, so both slices are
        # real and both are servable.  The first one is the section the proxy hid.
        two_raw = file_lines(sec_root, TWO_FILE)
        two_heads = fixture_headings(two_raw)
        two_fm, two_body = sdrv.mod.read_page(
            os.path.join(sec_root, WIKI_REL, TWO_FILE))
        two_whole = len(two_body.strip())
        want_two = [h[2] for h in two_heads]
        two_nobody = sdrv.get_page(TWO_SLUG, include_body=False)
        two_miss = sdrv.get_page(TWO_SLUG, section=MISSING_SECTION)
        problems = []
        rows = []
        if [h[1] for h in two_heads] != [1, 1]:
            problems.append("fixture drift: heading levels %r, want exactly "
                            "[1, 1] -- two top-level headings and nothing else "
                            "is the entire point of this page"
                            % [h[1] for h in two_heads])
        if two_fm.get("title") in want_two:
            problems.append("fixture drift: the frontmatter title %r is also a "
                            "heading, so `served_section` cannot tell the "
                            "rendered title line from the section"
                            % two_fm.get("title"))
        # THE premise: the first H1's slice must be STRICTLY smaller than the
        # body.  If it were not, the size rule would hide it for a good reason
        # and every assertion below would be green for the wrong one.
        first_slice = (sdrv.mod._extract_section(two_body, want_two[0])
                       if want_two else None)
        if first_slice is None:
            problems.append("the first heading is not extractable at all, so "
                            "'the caller could have asked for it' is false")
        elif len(first_slice) >= two_whole:
            problems.append("premise gone: the first H1's slice is %dc of a %dc "
                            "body -- it spans the page, so hiding it is correct "
                            "and this case proves nothing"
                            % (len(first_slice), two_whole))
        for label, answer in (("include_body false", two_nobody),
                              ("missing section", two_miss)):
            if answer["names"] != want_two:
                problems.append("%s lists %r, want both titles in document "
                                "order: %r" % (label, answer["names"], want_two))
            if answer["empty_msgs"]:
                problems.append("%s claims the page is empty (%r) although both "
                                "of its headings delimit a real slice"
                                % (label, answer["empty_msgs"]))
            if not answer["has_sections_label"]:
                problems.append("%s renders the lines without a %r label"
                                % (label, SECTIONS_LABEL))
            if answer["hint"] is not None:
                problems.append("%s offers a depth escape (%r) although nothing "
                                "here sits below level 1"
                                % (label, answer["hint"]))
        for sec in two_nobody["sections"]:
            extracted = sdrv.mod._extract_section(two_body, sec["name"])
            served = served_section(
                sdrv.get_page(TWO_SLUG, section=sec["name"])["text"],
                sec["name"])
            rows.append("%-30s advertised %4d  extract %s  served %s  whole %d"
                        % (sec["name"], sec["size"],
                           "%4d" % len(extracted) if extracted is not None
                           else "NONE",
                           "%4d" % len(served) if served is not None else "NONE",
                           two_whole))
            if extracted is None or sec["size"] != len(extracted):
                problems.append("%s: advertised %dc, _extract_section gives %r"
                                % (sec["name"], sec["size"],
                                   len(extracted) if extracted is not None
                                   else None))
            if served is None:
                problems.append("%s is offered but `section:` serves nothing -- "
                                "the index points at a slice the server will "
                                "not hand over" % sec["name"])
            elif sec["size"] != len(served):
                problems.append("%s: advertised %dc, served %dc"
                                % (sec["name"], sec["size"], len(served)))
            if served is not None and len(served) >= two_whole:
                problems.append("%s: the served slice is %dc of a %dc body -- it "
                                "IS the page, so it should never have been listed"
                                % (sec["name"], len(served), two_whole))
        suite.record("J", "two-h1s-bound-each-other-so-both-are-sections",
                     problems,
                     detail=[_d("page", "%r, heading levels %r"
                                % (TWO_SLUG, [h[1] for h in two_heads])),
                             _d("listed", "%r" % two_nobody["names"]),
                             _d("body", "%dc; the first slice is %r"
                                % (two_whole,
                                   len(first_slice) if first_slice else None)),
                             _d("was", "`level < 2` hid BOTH of these, and the "
                                       "first one is a bounded, servable slice "
                                       "the caller was never told about"),
                             _d("rule", "skip when the slice IS the page, which "
                                        "is what the level test was standing in "
                                        "for")]
                            + ["        " + r for r in rows],
                     text=two_nobody["text"])

        # The ORDER of the two skip tests, made observable.  A heading that spans
        # the page AND sits past the default depth is the only shape that can
        # tell them apart: sized first it disappears, depth-tested first it is
        # counted as `deeper` and the answer advertises a depth that reveals
        # nothing -- the exact lie the hint may never tell.
        solo_raw = file_lines(sec_root, SOLO_FILE)
        solo_heads = fixture_headings(solo_raw)
        _solo_fm, solo_body = sdrv.mod.read_page(
            os.path.join(sec_root, WIKI_REL, SOLO_FILE))
        solo_whole = len(solo_body.strip())
        solo_level = solo_heads[0][1] if solo_heads else 0
        solo_nobody = sdrv.get_page(SOLO_SLUG, include_body=False)
        solo_miss = sdrv.get_page(SOLO_SLUG, section=MISSING_SECTION)
        solo_deep = sdrv.get_page(SOLO_SLUG, include_body=False,
                                  depth=solo_level)
        solo_slice = (sdrv.mod._extract_section(solo_body, solo_heads[0][2])
                      if solo_heads else None)
        problems = []
        if [h[1] for h in solo_heads] != [solo_level] or solo_level <= 2:
            problems.append("fixture drift: heading levels %r -- this page needs "
                            "exactly one heading and it must sit BELOW the "
                            "default depth 2, or the two tests cannot be told "
                            "apart" % [h[1] for h in solo_heads])
        if solo_slice is None or len(solo_slice) != solo_whole:
            problems.append("premise gone: the heading's slice is %r of a %dc "
                            "body -- it must BE the page, or the size test never "
                            "fires and the ordering is invisible"
                            % (len(solo_slice) if solo_slice else None,
                               solo_whole))
        for label, answer in (("include_body false", solo_nobody),
                              ("missing section", solo_miss),
                              ("depth %d" % solo_level, solo_deep)):
            if answer["hint"] is not None:
                problems.append("%s advertises %r for a heading whose slice is "
                                "the WHOLE page -- the depth it names reveals "
                                "nothing, so the escape hatch is a lie"
                                % (label, answer["hint"]))
            if answer["has_hint_sentinel"]:
                problems.append("%s mentions %r anyway"
                                % (label, HINT_SENTINEL))
            if answer["sections"]:
                problems.append("%s offers %r, which is the whole page under "
                                "another name" % (label, answer["names"]))
            # The empty here must name the RULE, not a title: this page has no
            # title -- its one heading is an H3 -- so a sentence about what sits
            # "below its title" would be describing a page that does not exist.
            if answer["empty_msgs"] != [NO_SECTIONS_MSG]:
                problems.append("%s rendered %r, want exactly [%r]"
                                % (label, answer["empty_msgs"], NO_SECTIONS_MSG))
            if "title" in answer["text"].lower():
                problems.append("%s says 'title' somewhere (%r) although this "
                                "page has none -- its only heading is level %d, "
                                "and the reason the list is empty is the SIZE of "
                                "the slice, not where it sits relative to a title"
                                % (label,
                                   [ln for ln in answer["text"].split("\n")
                                    if "title" in ln.lower()], solo_level))
        if solo_deep["text"] != solo_nobody["text"]:
            problems.append("depth %d renders something different from the "
                            "default, so the level DOES hide something and the "
                            "silence above is wrong" % solo_level)
        suite.record("J", "page-spanning-heading-is-not-a-depth-secret", problems,
                     detail=[_d("page", "%r, heading levels %r, body %dc"
                                % (SOLO_SLUG, [h[1] for h in solo_heads],
                                   solo_whole)),
                             _d("slice", "%r == body -> the size test must fire "
                                         "FIRST"
                                % (len(solo_slice) if solo_slice else None)),
                             _d("default", "listed %r, hint %r, empty %r"
                                % (solo_nobody["names"], solo_nobody["hint"],
                                   solo_nobody["empty_msgs"])),
                             _d("no title", "the word appears in the answer: %r"
                                % ("title" in solo_nobody["text"].lower())),
                             _d("depth %d" % solo_level, "identical to the "
                                                         "default: %r"
                                % (solo_deep["text"] == solo_nobody["text"])),
                             _d("gate", "depth-tested first this page would "
                                        "report `1 deeper heading(s) ... pass "
                                        "depth: %d`, and taking it would return "
                                        "this same empty answer" % solo_level)],
                     text=solo_nobody["text"])

        deep_heads = fixture_headings(file_lines(sec_root, DEEP_FILE))
        deep_l2 = [h for h in deep_heads if h[1] == 2]
        deep_l3 = [h for h in deep_heads if h[1] > 2]
        deep_level = max([h[1] for h in deep_l3] or [2])
        only = sdrv.get_page(DEEP_SLUG, include_body=False)
        only_deep = sdrv.get_page(DEEP_SLUG, include_body=False, depth=deep_level)
        problems = []
        if deep_l2 or not deep_l3:
            problems.append("fixture drift: level-2 %r / deeper %r -- this page "
                            "must carry ONLY headings below level 2"
                            % ([h[2] for h in deep_l2], [h[2] for h in deep_l3]))
        if only["sections"]:
            problems.append("listed %r at the default depth, want nothing"
                            % only["names"])
        if only["has_sections_label"]:
            problems.append("an empty %r label with no lines under it"
                            % SECTIONS_LABEL)
        if only["has_no_headings_msg"]:
            problems.append("claims %r although %d heading(s) exist -- the caller "
                            "is told to stop looking"
                            % (NO_HEADINGS_MSG, len(deep_l3)))
        if only["hint"] != (len(deep_l3), deep_level):
            problems.append("hint %r, want %r -- on this page the hint is the "
                            "ENTIRE index, so a wrong one is the whole answer "
                            "being wrong"
                            % (only["hint"], (len(deep_l3), deep_level)))
        if only_deep["names"] != [h[2] for h in deep_l3]:
            problems.append("depth %d lists %r, want %r"
                            % (deep_level, only_deep["names"],
                               [h[2] for h in deep_l3]))
        if only_deep["hint"] is not None:
            problems.append("depth %d still hints %r"
                            % (deep_level, only_deep["hint"]))
        suite.record("J", "only-deeper-headings-leaves-the-hint-alone", problems,
                     detail=[_d("page", "%r, headings %r"
                                % (DEEP_SLUG,
                                   [(h[1], h[2]) for h in deep_heads])),
                             _d("default", "listed %r, hint %r"
                                % (only["names"], only["hint"])),
                             _d("depth %d" % deep_level, "listed %r, hint %r"
                                % (only_deep["names"], only_deep["hint"])),
                             _d("why", "empty list + hint is not a degenerate "
                                       "case: it is the only output this page's "
                                       "index has")],
                     text=only["text"])

        accepted = sdrv.mod.HANDLER_ACCEPTED_PARAMS["get_page"]
        problems = []
        if "depth" not in accepted:
            problems.append("'depth' missing from "
                            "HANDLER_ACCEPTED_PARAMS['get_page'] -- the "
                            "dispatcher would reject the very call the hint "
                            "tells the caller to make")
        if deep_miss["error"] or "Unknown params" in deep_miss["text"]:
            problems.append("a call carrying depth was refused: %s"
                            % deep_miss["text"][:160])
        if deep_miss["text"] == miss["text"]:
            problems.append("depth %d renders exactly the default answer, so "
                            "'lower values fall back to the default' would prove "
                            "nothing" % deepest)
        floored = []
        for value in (1, 0, "abc", None):
            low = sdrv.get_page(LAYERED_SLUG, section=MISSING_SECTION,
                                depth=value)
            floored.append((value, low["text"] == miss["text"]))
            if low["error"]:
                problems.append("depth %r produced an error: %s"
                                % (value, low["text"][:160]))
            if low["text"] != miss["text"]:
                problems.append("depth %r renders something other than the "
                                "default depth-2 answer -- the floor is not a "
                                "floor" % (value,))
        suite.record("J", "depth-is-accepted-and-floored-at-two", problems,
                     detail=[_d("accepted", "%r" % sorted(accepted)),
                             _d("live", "depth=%d -> %d line(s), error=%r"
                                % (deepest, len(deep_miss["sections"]),
                                   deep_miss["error"])),
                             _d("floored", "%r" % floored),
                             _d("contract", "max(2, ...) plus a swallowed "
                                            "int() failure: no value of depth may "
                                            "produce less than the default, and "
                                            "none may 500")],
                     text=miss["text"])

        desc = sdrv.mod.WIKI_CALL_TOOL["description"]
        problems = []
        if "depth" not in desc:
            problems.append("the tool description never mentions depth, so the "
                            "only way to find the knob the hint names is reading "
                            "the source")
        if "`depth`" in desc:
            problems.append("the description BACKTICKS depth: the name_existence "
                            "suite reads backticked identifiers in server text as "
                            "function-name prescriptions, so a backticked param "
                            "turns into a dead-name finding over there -- leave "
                            "it bare")
        suite.record("J", "depth-is-discoverable-unbackticked", problems,
                     detail=[_d("mentioned", "%r" % ("depth" in desc)),
                             _d("backticked", "%r" % ("`depth`" in desc)),
                             _d("why", "a parameter nobody can see is a parameter "
                                       "nobody passes; a BACKTICKED one is a "
                                       "failure in another suite")],
                     text="")
    finally:
        sec_work.cleanup()

    # ============ K: `source_to_pages` says WHAT each page covers ============
    # A THIRD workspace, and a FOURTH holding the same five pages with their
    # `description:` frontmatter lines removed.  The mirror is what turns the
    # preservation claim from a shape check into a byte-for-byte one: whatever
    # the reverse lookup renders for a corpus WITHOUT the field is, definitionally,
    # the answer this commit was not allowed to change.
    desc_work = H.TempWorkspace("ph-wiki-desc-", keep=opts.keep)
    bare_work = H.TempWorkspace("ph-wiki-desc-bare-", keep=opts.keep)
    try:
        desc_root = build_desc_fixture(desc_work, True)
        bare_root = build_desc_fixture(bare_work, False)
        kdrv = Driver(desc_root)
        bdrv = Driver(bare_root)

        shared = kdrv.sources(K_SHARED_SOURCE)
        want = k_expected_hits(K_SHARED_SOURCE)
        want_pages = [p for p, _m in want]
        matched_by_file = {p[K_FILE]: m for p, m in want}
        # Indexed by PATH, never by position: the hit ORDER is a separate claim
        # below, and a misordered answer must fail that case rather than quietly
        # mispair every other one.
        by_path = {h["path"]: h for h in shared["hits"]}
        described_pages = [p for p in want_pages if p[K_DESC]]

        problems = []
        if shared["error"]:
            problems.append("call failed: %s" % shared["text"][:160])
        if len(want) < 2:
            problems.append("fixture drift: the shared anchor lands on %d "
                            "page(s), so a multi-hit answer is never rendered"
                            % len(want))
        if any(a.count(":") > 1 for p in K_PAGES
               for a in list(p[K_SRCS]) + list(p[K_TGTS])):
            problems.append("fixture drift: an anchor carries more than one "
                            "colon, so the oracle's first-colon split is no "
                            "longer the server's rule and this case is guessing")
        want_paths = [p[K_FILE] for p in want_pages]
        want_slugs = [p[K_SLUG] for p in want_pages]
        if want_slugs == sorted(want_slugs):
            problems.append("fixture drift: document order and slug order agree "
                            "on the hit set, so a renderer that sorted by slug "
                            "would pass this case")
        if not shared["has_header"]:
            problems.append("no header line at all: %r" % shared["lines"][:1])
        if shared["header_source"] != K_SHARED_SOURCE:
            problems.append("the header echoes %r, want %r"
                            % (shared["header_source"], K_SHARED_SOURCE))
        if shared["header_root"] != WIKI_REL:
            problems.append("the header names root %r, want %r"
                            % (shared["header_root"], WIKI_REL))
        if shared["header_count"] != len(want):
            problems.append("the header claims %r page(s), want %d"
                            % (shared["header_count"], len(want)))
        if shared["header_count"] != len(shared["hits"]):
            problems.append("the header claims %r page(s) but %d hit line(s) "
                            "were rendered"
                            % (shared["header_count"], len(shared["hits"])))
        if [h["path"] for h in shared["hits"]] != want_paths:
            problems.append("listed %r, want %r (every matching fixture page, "
                            "in document order)"
                            % ([h["path"] for h in shared["hits"]], want_paths))
        if shared["orphan_attrs"]:
            problems.append("%d attribute line(s) sit above the first hit: %r "
                            "-- an indented line belongs to a hit, and one that "
                            "precedes every header belongs to none"
                            % (len(shared["orphan_attrs"]),
                               [a["key"] for a in shared["orphan_attrs"]]))
        for page in want_pages:
            hit = by_path.get(page[K_FILE])
            if hit is None:
                problems.append("%s: matching page not rendered" % page[K_FILE])
                continue
            # The state half of the label is MEASURED, never the frontmatter's
            # `status:` -- and on these five pages the measurement needs no git
            # at all: `sources:` present with no `verified:` block IS
            # `unverified`, decided before any diff is asked for.  The premise
            # (that shape, on every hit page) is asserted below, so the constant
            # is a consequence of the rule rather than an observation.
            want_meta = "/".join(x for x in (page[K_TYPE], K_MEASURED_STATE)
                                 if x)
            page_fm = kdrv.frontmatter(page[K_FILE])
            if not page_fm.get("sources"):
                problems.append("premise broken: %s carries no `sources:`, so "
                                "%r is not the state the rule assigns it"
                                % (page[K_FILE], K_MEASURED_STATE))
            if page_fm.get("verified"):
                problems.append("premise broken: %s carries a `verified:` block "
                                "(%r), so its state depends on a git diff and "
                                "this fixture cannot predict it"
                                % (page[K_FILE], page_fm.get("verified")))
            if page[K_STATUS] == K_MEASURED_STATE:
                problems.append("fixture drift: %s writes %r in its frontmatter, "
                                "which is also the measured state -- a renderer "
                                "reading the FIELD would pass on this page"
                                % (page[K_FILE], page[K_STATUS]))
            for label, got, wanted in (("title", hit["title"], page[K_TITLE]),
                                       ("slug", hit["slug"], page[K_SLUG]),
                                       ("meta", hit["meta"], want_meta)):
                if got != wanted:
                    problems.append("%s: %s %r, want %r"
                                    % (page[K_FILE], label, got, wanted))
        suite.record("K", "hit-lines-are-the-fixture-with-the-measured-state",
                     problems,
                     detail=[_d("call", "source_to_pages %r" % K_SHARED_SOURCE),
                             _d("header", "%r page(s) in %r/"
                                % (shared["header_count"], shared["header_root"])),
                             _d("listed", "%r" % [h["path"]
                                                  for h in shared["hits"]]),
                             _d("oracle", "%r (naive first-colon split over the "
                                          "fixture table)" % want_paths),
                             _d("slug order", "%r -- deliberately NOT the "
                                              "document order" % want_slugs),
                             _d("labels", "%r" % [h["meta"]
                                                  for h in shared["hits"]]),
                             _d("state", "%r for every hit -- sources with no "
                                         "`verified:` block, measured, not the "
                                         "frontmatter's %r"
                                % (K_MEASURED_STATE,
                                   sorted({p[K_STATUS] for p in want_pages}))),
                             _d("why", "the description line is an ADDITION and "
                                       "the state label is a REPLACEMENT; the "
                                       "count, the order, the titles and the "
                                       "slugs must not have moved for either")],
                     text=shared["text"])

        problems = []
        rows = []
        if len(described_pages) < 2:
            problems.append("fixture drift: %d described page(s) in the hit "
                            "set, so 'every described hit carries the line' is "
                            "a claim about one page" % len(described_pages))
        for page in described_pages:
            hit = by_path.get(page[K_FILE])
            fm_value = kdrv.frontmatter(page[K_FILE]).get(DESC_KEY)
            if fm_value != page[K_DESC]:
                problems.append("%s: the fixture file parses back as %r, not "
                                "the table's %r -- the premise is broken, not "
                                "the renderer"
                                % (page[K_FILE], fm_value, page[K_DESC]))
            if len(page[K_DESC]) < K_DESC_MIN_CHARS:
                problems.append("fixture drift: %s carries a %d-char "
                                "description, under the %d chars that make a "
                                "truncating renderer visible at all"
                                % (page[K_FILE], len(page[K_DESC]),
                                   K_DESC_MIN_CHARS))
            overlap = sorted(set(kdrv.mod._tokenize(page[K_TITLE]))
                             & set(kdrv.mod._tokenize(page[K_DESC])))
            if overlap:
                problems.append("fixture drift: %s shares %r between its title "
                                "and its description, so 'the title was printed "
                                "instead' stops being distinguishable"
                                % (page[K_FILE], overlap))
            if hit is None:
                problems.append("%s: not rendered at all" % page[K_FILE])
                continue
            got = hit_attrs(hit, DESC_KEY)
            if len(got) != 1:
                problems.append("%s: %d description line(s), want exactly 1"
                                % (page[K_FILE], len(got)))
                continue
            if got[0]["value"] != fm_value:
                problems.append("%s: rendered %r, want the frontmatter value "
                                "%r -- verbatim, not trimmed and not the title"
                                % (page[K_FILE], got[0]["value"], fm_value))
            rows.append("%-16s %dc  %r"
                        % (page[K_FILE], len(got[0]["value"]),
                           got[0]["value"][:52]))
        suite.record("K", "description-is-the-frontmatter-value", problems,
                     detail=[_d("described", "%d of %d hit page(s)"
                                % (len(described_pages), len(want_pages))),
                             _d("oracle", "the page re-parsed by the server's "
                                          "OWN read_page, not the string the "
                                          "test believes it wrote"),
                             _d("why", "the handler already PARSED this field "
                                       "and then dropped it, so 'which page' "
                                       "was answered and 'what does it say' "
                                       "cost the caller a second call")]
                            + ["        " + r for r in rows],
                     text=shared["text"])

        problems = []
        rows = []
        anchor_keys = set()
        for page in described_pages:
            hit = by_path.get(page[K_FILE])
            if hit is None:
                continue
            got = hit_attrs(hit, DESC_KEY)
            others = [a for a in hit["attrs"] if a["key"] != DESC_KEY]
            anchor_keys.update(a["key"] for a in others)
            if not got:
                problems.append("%s: no description line to place" % page[K_FILE])
                continue
            if not others:
                problems.append("%s: the hit renders no anchor line, so "
                                "'the description comes first' orders a set of "
                                "one" % page[K_FILE])
                continue
            d = got[0]
            if d["line_i"] <= hit["line_i"]:
                problems.append("%s: the description sits on line %d, at or "
                                "above its own hit header on line %d"
                                % (page[K_FILE], d["line_i"], hit["line_i"]))
            late = [(a["key"], a["line_i"]) for a in others
                    if a["line_i"] < d["line_i"]]
            if late:
                problems.append("%s: %r render(s) BEFORE the description"
                                % (page[K_FILE], late))
            if hit["attrs"][0]["key"] != DESC_KEY:
                problems.append("%s: the first line under the hit header is %r"
                                % (page[K_FILE], hit["attrs"][0]["key"]))
            rows.append("%-16s header L%-3d description L%-3d then %r"
                        % (page[K_FILE], hit["line_i"], d["line_i"],
                           [(a["key"], a["line_i"]) for a in others]))
        if anchor_keys != {"sources", "targets"}:
            problems.append("fixture drift: the described hits carry %r, so the "
                            "position is not pinned above BOTH anchor kinds"
                            % sorted(anchor_keys))
        suite.record("K", "description-precedes-the-anchor-lines", problems,
                     detail=[_d("checked", "%d described hit(s), by LINE INDEX"
                                % len(rows)),
                             _d("anchors", "%r" % sorted(anchor_keys)),
                             _d("why", "presence is not the contract: a "
                                       "sentence rendered under the anchors "
                                       "reads as a note ON them, and the block "
                                       "stops scanning as one thing")]
                            + ["        " + r for r in rows],
                     text=shared["text"])

        problems = []
        rows = []
        pairs = 0
        for page in described_pages:
            hit = by_path.get(page[K_FILE])
            if hit is None:
                continue
            got = hit_attrs(hit, DESC_KEY)
            others = [a for a in hit["attrs"] if a["key"] != DESC_KEY]
            if not got or not others:
                continue
            d = got[0]
            if not d["indent"]:
                problems.append("%s: the description line carries no indent at "
                                "all, so it does not read as part of the hit "
                                "block" % page[K_FILE])
            for a in others:
                pairs += 1
                if a["indent"] != d["indent"]:
                    problems.append("%s: description indented %r, `%s:` "
                                    "indented %r -- the two lines no longer "
                                    "line up under their hit"
                                    % (page[K_FILE], d["indent"], a["key"],
                                       a["indent"]))
            rows.append("%-16s description %r == %r"
                        % (page[K_FILE], d["indent"],
                           [a["indent"] for a in others]))
        if not pairs:
            problems.append("no description/anchor pair was compared, so the "
                            "indent claim is vacuous")
        suite.record("K", "description-indent-matches-the-anchor-line", problems,
                     detail=[_d("pairs", "%d, indent DERIVED from the sibling "
                                         "anchor line" % pairs),
                             _d("why", "the width is not the point -- agreeing "
                                       "with the line it was inserted above is, "
                                       "and a typed space count would pass a "
                                       "server that changed both")]
                            + ["        " + r for r in rows],
                     text=shared["text"])

        mute = [p for p in K_PAGES if p[K_DESC] is None]
        problems = []
        if len(mute) != 1:
            problems.append("fixture drift: %d page(s) carry no description, "
                            "want exactly 1" % len(mute))
        mute_page = mute[0] if mute else None
        mute_fm = kdrv.frontmatter(mute_page[K_FILE]) if mute_page else {}
        mute_hit = by_path.get(mute_page[K_FILE]) if mute_page else None
        if mute_page and DESC_KEY in mute_fm:
            problems.append("premise broken: %s DOES carry a description key "
                            "(%r), so an absent line proves nothing about the "
                            "renderer" % (mute_page[K_FILE], mute_fm[DESC_KEY]))
        if not shared["desc_lines"]:
            problems.append("premise broken: no hit in this answer carries a "
                            "description, so the silence is global and says "
                            "nothing about this page")
        if mute_hit is None:
            problems.append("premise broken: %s is not in the hit set, so it "
                            "has no block to be silent in"
                            % (mute_page[K_FILE] if mute_page else "?"))
        else:
            got = hit_attrs(mute_hit, DESC_KEY)
            if got:
                problems.append("a description line was rendered anyway (%r): "
                                "an empty or placeholder one is worse than "
                                "none, because the caller cannot tell a page "
                                "with nothing to say from a field nobody filled"
                                % [a["value"] for a in got])
            if not hit_attrs(mute_hit, "sources"):
                problems.append("the silent page lost its `sources:` line too "
                                "-- what goes missing is the description alone")
        suite.record("K", "page-without-description-stays-silent", problems,
                     detail=[_d("page", "%s" % (mute_page[K_FILE] if mute_page
                                                else "?")),
                             _d("premise", "frontmatter keys %r -- %r absent"
                                % (sorted(mute_fm), DESC_KEY)),
                             _d("rendered", "%r"
                                % ([a["key"] for a in mute_hit["attrs"]]
                                   if mute_hit else None)),
                             _d("others", "%d description line(s) elsewhere in "
                                          "the same answer"
                                % len(shared["desc_lines"]))],
                     text=shared["text"])

        tgt_pages = [p for p in want_pages if p[K_TGTS]]
        problems = []
        if len(tgt_pages) != 1:
            problems.append("fixture drift: %d hit page(s) carry `targets:`, "
                            "want exactly 1" % len(tgt_pages))
        tgt_page = tgt_pages[0] if tgt_pages else None
        tgt_matched = matched_by_file.get(tgt_page[K_FILE], []) if tgt_page else []
        tgt_hit = by_path.get(tgt_page[K_FILE]) if tgt_page else None
        if tgt_page and not tgt_page[K_DESC]:
            problems.append("premise broken: the targets page has no "
                            "description, so there is nothing to place")
        if tgt_page and [a for a in tgt_matched if a in tgt_page[K_SRCS]]:
            problems.append("premise broken: the query ALSO matches a "
                            "`sources:` anchor of %s, so this is not the "
                            "targets-only shape" % tgt_page[K_FILE])
        if tgt_hit is None:
            problems.append("the targets page is not in the answer at all")
        else:
            if hit_attrs(tgt_hit, "sources"):
                problems.append("a `sources:` line was rendered although no "
                                "source anchor matched: %r"
                                % [a["value"] for a in hit_attrs(tgt_hit,
                                                                 "sources")])
            tg = hit_attrs(tgt_hit, "targets")
            dl = hit_attrs(tgt_hit, DESC_KEY)
            if len(tg) != 1:
                problems.append("%d `targets:` line(s), want 1" % len(tg))
            elif tg[0]["value"] != ", ".join(tgt_matched):
                problems.append("the targets line reads %r, want %r"
                                % (tg[0]["value"], ", ".join(tgt_matched)))
            if len(dl) != 1:
                problems.append("%d description line(s) on the targets-only "
                                "hit, want 1" % len(dl))
            if len(tg) == 1 and len(dl) == 1:
                if dl[0]["line_i"] > tg[0]["line_i"]:
                    problems.append("the description (line %d) renders AFTER "
                                    "the targets line (%d)"
                                    % (dl[0]["line_i"], tg[0]["line_i"]))
                if dl[0]["indent"] != tg[0]["indent"]:
                    problems.append("description indent %r, targets indent %r"
                                    % (dl[0]["indent"], tg[0]["indent"]))
        suite.record("K", "targets-only-hit-is-described-too", problems,
                     detail=[_d("page", "%s" % (tgt_page[K_FILE] if tgt_page
                                                else "?")),
                             _d("matched", "%r (all from `targets:`)"
                                % tgt_matched),
                             _d("rendered", "%r"
                                % ([(a["key"], a["line_i"])
                                    for a in tgt_hit["attrs"]]
                                   if tgt_hit else None)),
                             _d("why", "a page can be a hit through the OTHER "
                                       "anchor list, and the same line has to "
                                       "come first there too")],
                     text=shared["text"])

        problems = []
        rows = []
        for page in K_PAGES:
            full_text = k_page_text(page, True)
            bare_text = k_page_text(page, False)
            manual = "\n".join(ln for ln in full_text.split("\n")
                               if not ln.startswith("description: "))
            if bare_text != manual:
                problems.append("%s: the mirror page is NOT the full page minus "
                                "its description line, so the control corpus "
                                "differs in more than the field" % page[K_FILE])
            dropped = len(full_text.split("\n")) - len(bare_text.split("\n"))
            if dropped != (1 if page[K_DESC] else 0):
                problems.append("%s: the mirror drops %d line(s), want %d"
                                % (page[K_FILE], dropped,
                                   1 if page[K_DESC] else 0))
        for source in (K_SHARED_SOURCE, K_SYMBOL_SOURCE):
            full = kdrv.sources(source)
            bare = bdrv.sources(source)
            stripped = strip_desc_lines(full["text"])
            if not full["desc_lines"]:
                problems.append("%s: the described corpus rendered no "
                                "description line at all" % source)
            if bare["desc_lines"]:
                problems.append("%s: the mirror rendered %d description line(s) "
                                "although no page carries the field: %r"
                                % (source, len(bare["desc_lines"]),
                                   bare["desc_lines"]))
            if full["text"] == bare["text"]:
                problems.append("%s: the two answers are identical, so the "
                                "comparison below cannot fail" % source)
            if stripped == full["text"]:
                problems.append("%s: the filter removed nothing, so the "
                                "comparison is vacuous" % source)
            if stripped != bare["text"]:
                problems.append("%s: with its description lines filtered out "
                                "the answer is NOT what the same corpus renders "
                                "without the field:\n  want %r\n  got  %r"
                                % (source, bare["text"], stripped))
            rows.append("%-42s full %4dc  bare %4dc  filtered==bare %r"
                        % (source, len(full["text"]), len(bare["text"]),
                           stripped == bare["text"]))
        suite.record("K", "only-the-description-lines-are-new", problems,
                     detail=[_d("control", "the same five pages with their "
                                           "`description:` frontmatter line "
                                           "removed, in a separate workspace"),
                             _d("claim", "answer minus its description lines == "
                                         "answer of the corpus without the "
                                         "field, BYTE for byte")]
                            + ["        " + r for r in rows],
                     text=kdrv.sources(K_SHARED_SOURCE)["text"])

        none_full = kdrv.sources(K_ABSENT_SOURCE)
        none_bare = bdrv.sources(K_ABSENT_SOURCE)
        problems = []
        if k_expected_hits(K_ABSENT_SOURCE):
            problems.append("fixture drift: %r matches %d fixture page(s), so "
                            "the empty arm is not being exercised"
                            % (K_ABSENT_SOURCE,
                               len(k_expected_hits(K_ABSENT_SOURCE))))
        if not shared["desc_lines"]:
            problems.append("premise broken: this corpus renders no description "
                            "line on ANY query, so an empty answer without one "
                            "is not evidence")
        if none_full["error"]:
            problems.append("call failed: %s" % none_full["text"][:160])
        if none_full["header_count"] != 0:
            problems.append("the header claims %r page(s), want 0"
                            % none_full["header_count"])
        if not none_full["has_no_ref_msg"]:
            problems.append("missing %r" % NO_REF_MSG)
        if none_full["hits"]:
            problems.append("%d hit line(s) rendered: %r"
                            % (len(none_full["hits"]),
                               [h["path"] for h in none_full["hits"]]))
        if none_full["attr_lines"]:
            problems.append("the empty answer carries %d attribute line(s): %r"
                            % (len(none_full["attr_lines"]),
                               none_full["attr_lines"]))
        if none_full["text"] != none_bare["text"]:
            problems.append("the empty answer differs between the described "
                            "corpus and the mirror:\n  bare %r\n  full %r"
                            % (none_bare["text"], none_full["text"]))
        suite.record("K", "empty-result-is-untouched", problems,
                     detail=[_d("call", "source_to_pages %r" % K_ABSENT_SOURCE),
                             _d("answer", "%r" % none_full["text"]),
                             _d("mirror", "identical: %r"
                                % (none_full["text"] == none_bare["text"])),
                             _d("why", "a hit-less reply has no hit to describe, "
                                       "and the sentence it does carry is the "
                                       "one the caller already knew")],
                     text=none_full["text"])

        tool_desc = kdrv.mod.WIKI_CALL_TOOL["description"]
        para = tool_paragraph(tool_desc, "source_to_pages")
        problems = []
        if not para:
            problems.append("no `source_to_pages` entry in the tool description "
                            "at all, so this case is reading nothing")
        if DESC_KEY not in para:
            problems.append("the source_to_pages entry never mentions the "
                            "description, so the only way to learn the answer "
                            "carries one is to call it and look -- the same "
                            "invisibility min_coverage had")
        if "`%s`" % DESC_KEY in tool_desc:
            problems.append("the description BACKTICKS description: the "
                            "name_existence suite reads backticked identifiers "
                            "in server text as function-name prescriptions, so "
                            "a backticked field name turns into a dead-name "
                            "finding over there -- leave it bare")
        suite.record("K", "description-is-discoverable-unbackticked", problems,
                     detail=[_d("entry", "%d char(s)" % len(para)),
                             _d("mentions", "%r" % (DESC_KEY in para)),
                             _d("backticked", "%r"
                                % ("`%s`" % DESC_KEY in tool_desc)),
                             _d("why", "a reverse lookup that now answers 'what '"
                                       "do they say' is worth nothing if the "
                                       "caller still expects only 'which page'")],
                     text=para)
    finally:
        desc_work.cleanup()
        bare_work.cleanup()

    # ============ L: the state label is MEASURED, not the frontmatter ========
    # A FIFTH workspace (the ten states, frontmatter DISAGREEING), a SIXTH (the
    # same ten with the frontmatter written to AGREE -- the control), and a
    # SEVENTH carrying a synthetic `.git/HEAD`, which is the only way the
    # cross-call diff cache can be keyed at all.
    st_work = H.TempWorkspace("ph-wiki-state-", keep=opts.keep)
    ag_work = H.TempWorkspace("ph-wiki-state-agree-", keep=opts.keep)
    cache_work = H.TempWorkspace("ph-wiki-state-cache-", keep=opts.keep)
    try:
        st_root = build_state_fixture(st_work, agree=False)
        ag_root = build_state_fixture(ag_work, agree=True)

        # ---- the UNPATCHED driver, on a directory that is not a git repo -----
        # Its own module instance, and it runs FIRST: the whole point is the
        # behaviour with every git seam intact.
        rawdrv = Driver(st_root, name="mcp_wiki_state_raw")
        raw_repo = rawdrv.mod.repo_root(rawdrv.abs_wiki)
        raw_head = rawdrv.mod._head_sha_nospawn(raw_repo)
        rawdrv.mod._FRESH_CACHE.clear()
        cache_a = rawdrv.mod._recall_diff_cache(raw_repo)
        cache_b = rawdrv.mod._recall_diff_cache(raw_repo)
        raw_log = log_git_calls(rawdrv.mod)
        raw_run = rawdrv.search(L_SHARED_TERM, limit=L_LIMIT)
        raw_first = list(raw_log)
        raw_log.clear()
        rawdrv.search(L_SHARED_TERM, limit=L_LIMIT)
        raw_second = list(raw_log)
        problems = []
        if raw_head != "":
            problems.append("premise gone: _head_sha_nospawn reads %r for the "
                            "fixture's repo root, so TMPDIR sits inside a git "
                            "repo and this case is measuring that repo instead "
                            "of the bypass" % raw_head)
        if cache_a is cache_b:
            problems.append("two calls got the SAME dict although HEAD is "
                            "unreadable -- an unkeyable cache that is shared "
                            "anyway is a cache keyed on the empty string, and "
                            "every unknown HEAD would collide on it")
        if rawdrv.mod._FRESH_CACHE:
            problems.append("_FRESH_CACHE holds %r although HEAD could not be "
                            "read: an empty key is exactly the collision the "
                            "bypass exists to prevent"
                            % list(rawdrv.mod._FRESH_CACHE))
        rendered = sorted({state_of(h) for h in raw_run["hits"]})
        checkable = [s for s in rendered if s not in L_NOT_CHECKABLE]
        if not raw_run["hits"]:
            problems.append("no hits at all, so nothing was labelled")
        if checkable:
            problems.append("state(s) %r rendered although git can answer NOTHING "
                            "in a directory it does not track -- `current`, "
                            "`stale` and `orphaned-source` are all claims about a "
                            "comparison that never happened" % checkable)
        if not raw_first:
            problems.append("the reply spawned no git at all, so 'the bypass "
                            "re-pays on every call' is not what is being measured")
        # Count the DIFFS, not every spawn. `repo_root` is memoized per path, so
        # the second reply legitimately skips its `rev-parse --show-toplevel` --
        # that is a separate optimisation and not what this case is about. What
        # must repeat is the diff behind the state, because THAT is the thing with
        # no memo when HEAD cannot be keyed.
        diff_first = [a for a in raw_first if "diff" in a]
        diff_second = [a for a in raw_second if "diff" in a]
        if len(diff_second) != len(diff_first):
            problems.append("the second identical reply spawned %d git diff(s) "
                            "against the first's %d -- with HEAD unreadable there "
                            "is no memo, so the diffs must be re-paid in full"
                            % (len(diff_second), len(diff_first)))
        suite.record("L", "unkeyable-head-bypasses-the-cache-and-repays",
                     problems,
                     detail=[_d("repo root", "%r (git rev-parse failed, so "
                                             "`repo_root` fell back to the path "
                                             "it was handed)"
                                % os.path.basename(raw_repo)),
                             _d("head", "%r -> not cacheable" % raw_head),
                             _d("states", "%r" % rendered),
                             _d("git/reply", "%d then %d spawn(s): %r"
                                % (len(raw_first), len(raw_second),
                                   [" ".join(a) for a in raw_first])),
                             _d("cost", "the bypass is not 'the old cost of "
                                        "search' -- search paid ZERO git before "
                                        "this work; it is the full uncached cost, "
                                        "on every call, forever"),
                             _d("gate", "slow is acceptable, a wrong label is not: "
                                        "no page may read as checked-and-clean "
                                        "where nothing could be checked")],
                     text=raw_run["text"])

        # ---- the patched drivers: the git boundary is a table, not a repo ----
        ldrv = Driver(st_root, name="mcp_wiki_state")
        l_log = patch_git_boundary(ldrv.mod, st_root)
        adrv = Driver(ag_root, name="mcp_wiki_state_agree")
        patch_git_boundary(adrv.mod, ag_root)

        run_all = ldrv.search(L_SHARED_TERM, limit=L_LIMIT)
        by_slug = {h["slug"]: h for h in run_all["hits"]}

        # ---- the page whose source moved ------------------------------------
        stale_page = L_BY_SLUG["st-stale"]
        stale_fm = ldrv.frontmatter(stale_page[L_FILE])
        stale_hit = by_slug.get(stale_page[L_SLUG])
        problems = []
        if stale_fm.get("status") != "current":
            problems.append("premise broken: the page's frontmatter says %r, so "
                            "'the field claims current' is not what is being "
                            "contradicted" % stale_fm.get("status"))
        if L_SRC_CHANGED not in (L_DIFFS.get(L_C_CHANGED) or set()):
            problems.append("premise broken: the stubbed diff for %r does not "
                            "report %r as changed" % (L_C_CHANGED, L_SRC_CHANGED))
        if not os.path.exists(os.path.join(st_root, L_SRC_CHANGED)):
            problems.append("premise broken: %r is not on disk, so the page is "
                            "`orphaned-source` for a different reason entirely"
                            % L_SRC_CHANGED)
        if (L_C_CHANGED, "HEAD") not in l_log:
            problems.append("the classifier never asked git about %r -- the label "
                            "cannot be a measurement if nothing was measured"
                            % L_C_CHANGED)
        if stale_hit is None:
            problems.append("the page is not in the answer at all")
        else:
            if state_of(stale_hit) != "stale":
                problems.append("rendered %r, want 'stale'"
                                % state_of(stale_hit))
            if state_of(stale_hit) == stale_fm.get("status"):
                problems.append("rendered the frontmatter value %r -- this is the "
                                "defect: a caller reads [%s] and trusts a page "
                                "whose sources moved"
                                % (stale_fm.get("status"), stale_hit["type"]
                                   + "/" + state_of(stale_hit)))
        suite.record("L", "moved-anchor-renders-stale-not-the-fields-current",
                     problems,
                     detail=[_d("page", "%s" % stale_page[L_FILE]),
                             _d("frontmatter", "status: %r"
                                % stale_fm.get("status")),
                             _d("measured", "%r"
                                % (state_of(stale_hit) if stale_hit else None)),
                             _d("diff", "%r changed %r since %r"
                                % (L_C_CHANGED, sorted(L_DIFFS[L_C_CHANGED]),
                                   stale_page[L_SRCS])),
                             _d("measured on", "the real wiki: freshness said 9 of "
                                               "10 pages stale while all 10 wrote "
                                               "`current`, and one query returned "
                                               "4 hits labelled `current` of which "
                                               "4 were stale")],
                     text=run_all["text"])

        # ---- the pages nothing can be checked against ------------------------
        problems = []
        rows = []
        unsourced = [p for p in L_PAGES if not p[L_SRCS]]
        if len(unsourced) < 3:
            problems.append("fixture drift: %d page(s) without `sources:`, so the "
                            "not-checkable shapes are not covered" % len(unsourced))
        for page in unsourced:
            hit = by_slug.get(page[L_SLUG])
            fm = ldrv.frontmatter(page[L_FILE])
            if fm.get("sources"):
                problems.append("premise broken: %s carries `sources:` after all"
                                % page[L_FILE])
            if hit is None:
                problems.append("%s: not in the answer" % page[L_FILE])
                continue
            got = state_of(hit)
            rows.append("%-18s fm=%-9s measured=%s"
                        % (page[L_SLUG], fm.get("status"), got))
            if got == "current":
                problems.append("%s renders `current` although it has no anchors "
                                "at all -- a page with nothing to compare can "
                                "never be stale, which is emphatically not the "
                                "same as being fresh" % page[L_FILE])
            if got not in L_NOT_CHECKABLE:
                problems.append("%s renders %r, which is not one of the "
                                "not-checkable states %r"
                                % (page[L_FILE], got, sorted(L_NOT_CHECKABLE)))
            if got != page[L_STATE]:
                problems.append("%s renders %r, the rule assigns %r"
                                % (page[L_FILE], got, page[L_STATE]))
        suite.record("L", "page-without-sources-is-never-current", problems,
                     detail=[_d("pages", "%d without `sources:`" % len(unsourced)),
                             _d("states", "%r"
                                % sorted({p[L_STATE] for p in unsourced})),
                             _d("why", "`no-sources`, `untracked`, `planned` and "
                                       "`promotable` all mean NOT CHECKABLE; a "
                                       "label that showed any of them as `current` "
                                       "would re-tell the lie this work removes")]
                            + ["        " + r for r in rows],
                     text=run_all["text"])

        # ---- the disclosure: once, with the right N of M ---------------------
        # The expected count comes from the FIXTURE TABLE, not from the server's
        # own `fm_status` bookkeeping: a page counts only if it wrote a status AND
        # that status differs from the state the rule assigns it.
        want_n = sum(1 for h in run_all["hits"]
                     for p in [L_BY_SLUG[h["slug"]]]
                     if p[L_STATUS] is not None and p[L_STATUS] != p[L_STATE])
        want_m = len(run_all["hits"])
        found = _FM_DISAGREE_RE.findall(run_all["text"])
        agreeing = [p[L_SLUG] for p in L_PAGES if p[L_STATUS] == p[L_STATE]]
        silent = [p[L_SLUG] for p in L_PAGES if p[L_STATUS] is None]
        problems = []
        if want_m != len(L_PAGES):
            problems.append("fixture drift: %d of %d page(s) in the answer, so N "
                            "of M is not being read against the whole corpus"
                            % (want_m, len(L_PAGES)))
        if not agreeing:
            problems.append("fixture drift: every page disagrees, so 'the "
                            "agreeing page is not counted' is vacuous")
        if not silent:
            problems.append("fixture drift: every page writes a status, so 'a "
                            "page with no field is not counted' is vacuous")
        if len(found) != 1:
            problems.append("%d disclosure line(s), want exactly 1 -- it is ONE "
                            "fact about how the wiki is kept, not per-hit news"
                            % len(found))
        elif (int(found[0][0]), int(found[0][1])) != (want_n, want_m):
            problems.append("says %s of %s, want %d of %d"
                            % (found[0][0], found[0][1], want_n, want_m))
        if run_all["text"].count(FM_DISAGREE_SENTINEL) != 1:
            problems.append("the phrase %r appears %d time(s) in the answer"
                            % (FM_DISAGREE_SENTINEL,
                               run_all["text"].count(FM_DISAGREE_SENTINEL)))
        for slug in agreeing + silent:
            if slug not in by_slug:
                problems.append("%s is not rendered, so it cannot be counted in "
                                "M either" % slug)
        if want_n >= want_m:
            problems.append("fixture drift: N (%d) is not strictly below M (%d), "
                            "so a server that printed 'all of them' would pass"
                            % (want_n, want_m))
        suite.record("L", "disagreement-is-disclosed-once-with-the-right-count",
                     problems,
                     detail=[_d("rendered", "%r" % found),
                             _d("oracle", "%d of %d, counted off the fixture table"
                                % (want_n, want_m)),
                             _d("excluded", "agreeing %r, no status field %r"
                                % (agreeing, silent)),
                             _d("why", "a page with no hand-written status has no "
                                       "claim to disagree WITH -- and one whose "
                                       "claim happens to be right is not evidence "
                                       "the field is unmaintained")],
                     text=run_all["text"])

        # ---- the control: frontmatter that AGREES says nothing ---------------
        agree_run = adrv.search(L_SHARED_TERM, limit=L_LIMIT)
        problems = []
        mismatched = []
        for hit in agree_run["hits"]:
            fm = adrv.frontmatter(L_BY_SLUG[hit["slug"]][L_FILE])
            if fm.get("status") and fm.get("status") != state_of(hit):
                mismatched.append((hit["slug"], fm.get("status"),
                                   state_of(hit)))
        if len(agree_run["hits"]) != len(L_PAGES):
            problems.append("%d hit(s), want %d -- the control corpus must render "
                            "the same pages as the other one"
                            % (len(agree_run["hits"]), len(L_PAGES)))
        if mismatched:
            problems.append("premise broken: %r still disagree in the control "
                            "corpus, so its silence proves nothing" % mismatched)
        if _FM_DISAGREE_RE.search(agree_run["text"]):
            problems.append("the disclosure fired although every field matches "
                            "its measured state")
        if FM_DISAGREE_SENTINEL in agree_run["text"]:
            problems.append("the phrase %r appears anyway, in some other shape"
                            % FM_DISAGREE_SENTINEL)
        if not _FM_DISAGREE_RE.search(run_all["text"]):
            problems.append("premise broken: the DISAGREEING corpus prints no "
                            "disclosure either, so this silence is unconditional "
                            "rather than earned")
        suite.record("L", "agreeing-frontmatter-earns-no-disclosure", problems,
                     detail=[_d("control", "the same ten pages with `status:` "
                                           "written to the measured state"),
                             _d("hits", "%d, all agreeing"
                                % len(agree_run["hits"])),
                             _d("line", "present=%r (the other corpus: %r)"
                                % (bool(_FM_DISAGREE_RE.search(
                                    agree_run["text"])),
                                   bool(_FM_DISAGREE_RE.search(
                                       run_all["text"])))),
                             _d("contract", "silence when there is nothing to "
                                            "report -- a maintained wiki pays "
                                            "nothing for the disclosure")],
                     text=agree_run["text"])

        # ---- the status filter selects on the MEASURED state -----------------
        problems = []
        rows = []
        for value in sorted({p[L_STATE] for p in L_PAGES}
                            | {p[L_STATUS] for p in L_PAGES if p[L_STATUS]}):
            got = sorted(h["slug"] for h in
                         ldrv.search(L_SHARED_TERM, status=value,
                                     limit=L_LIMIT)["hits"])
            want = sorted(p[L_SLUG] for p in L_PAGES if p[L_STATE] == value)
            claim = sorted(p[L_SLUG] for p in L_PAGES
                           if (p[L_STATUS] or "") == value)
            rows.append("status=%-16s -> %d hit(s); measured %d, field claims %d"
                        % (value, len(got), len(want), len(claim)))
            if got != want:
                problems.append("status=%r selected %r, want the pages whose "
                                "MEASURED state is %r: %r"
                                % (value, got, value, want))
            leaked = sorted(set(claim) - set(want))
            if leaked and set(leaked) & set(got):
                problems.append("status=%r returned %r, whose FRONTMATTER says "
                                "%r but whose measured state does not -- the "
                                "filter is still reading the field"
                                % (value, sorted(set(leaked) & set(got)), value))
        # The one that matters most: `current` is what every page in this corpus
        # claims and what exactly one page measures.
        cur = sorted(h["slug"] for h in
                     ldrv.search(L_SHARED_TERM, status="current",
                                 limit=L_LIMIT)["hits"])
        claim_cur = sorted(p[L_SLUG] for p in L_PAGES if p[L_STATUS] == "current")
        if len(claim_cur) < 2:
            problems.append("fixture drift: only %d page(s) write "
                            "`status: current`, so the filter has almost nothing "
                            "to refuse" % len(claim_cur))
        if len(cur) != 1:
            problems.append("status='current' returned %r, want the single page "
                            "whose sources are verified and unchanged" % cur)
        suite.record("L", "status-filter-selects-the-measured-state", problems,
                     detail=[_d("current", "%r selected of %d claiming the field"
                                % (cur, len(claim_cur))),
                             _d("why", "a filter that selected on the field while "
                                       "the reply displayed the measurement would "
                                       "contradict itself on screen")]
                            + ["        " + r for r in rows],
                     text=ldrv.search(L_SHARED_TERM, status="current",
                                      limit=L_LIMIT)["text"])

        # ---- source_to_pages renders the same state as search ----------------
        s2p = ldrv.sources(L_SRC_CLEAN)
        problems = []
        rows = []
        s2p_states = {}
        for hit in s2p["hits"]:
            page = L_BY_FILE.get(hit["path"])
            got = hit["meta"].split("/", 1)[1] if "/" in hit["meta"] else ""
            s2p_states[hit["path"]] = got
            search_hit = by_slug.get(page[L_SLUG]) if page else None
            rows.append("%-20s s2p=%-16s search=%s"
                        % (hit["path"], got,
                           state_of(search_hit) if search_hit else None))
            if page is None:
                problems.append("%r is not a fixture page" % hit["path"])
                continue
            if search_hit is None:
                problems.append("%s: `search` did not render it, so the two "
                                "cannot be compared" % hit["path"])
                continue
            if got != state_of(search_hit):
                problems.append("%s: source_to_pages says %r, search says %r -- "
                                "two renderings of one fact, and the caller has "
                                "no way to know which one moved"
                                % (hit["path"], got, state_of(search_hit)))
            if got != page[L_STATE]:
                problems.append("%s: rendered %r, the rule assigns %r"
                                % (hit["path"], got, page[L_STATE]))
        if len(s2p["hits"]) < 2:
            problems.append("fixture drift: %d hit(s), so 'the same states' is a "
                            "claim about one page" % len(s2p["hits"]))
        if len(set(s2p_states.values())) < 2:
            problems.append("fixture drift: every hit renders %r, so a server "
                            "that printed one constant would pass"
                            % sorted(set(s2p_states.values())))
        if not _FM_DISAGREE_RE.search(s2p["text"]):
            problems.append("the reverse lookup prints no disclosure although "
                            "%d of its hits disagree with their own field"
                            % sum(1 for p, s in s2p_states.items()
                                  if (L_BY_FILE[p][L_STATUS] or "") not in ("", s)))
        suite.record("L", "source-to-pages-renders-the-same-state-as-search",
                     problems,
                     detail=[_d("call", "source_to_pages %r" % L_SRC_CLEAN),
                             _d("states", "%r" % sorted(set(s2p_states.values()))),
                             _d("why", "one classifier, two callers: a second copy "
                                       "of the rule would be a second place to "
                                       "edit, and this repo has paid for that "
                                       "shape three times")]
                            + ["        " + r for r in rows],
                     text=s2p["text"])

        # ---- the extraction changed nothing: `freshness`, byte for byte ------
        # HEAD's copy of the server, driven against the SAME workspace with the
        # SAME stubbed git boundary.  The claim is not "freshness still works" --
        # it is that lifting the if/else chain out of `freshness_analyze` into
        # `_classify_page` did not move one character of its output.
        rc, blob, err = H.run_process(
            ["git", "show", "HEAD:Scripts/mcp-wiki.py"], cwd=H.REPO_ROOT)
        problems = []
        b_text = c_text = ""
        b_report = c_report = None
        differs = None
        if rc != 0 or not blob:
            problems.append("could not read HEAD's copy of the server (rc=%d, "
                            "stderr=%r) -- without it this case has no baseline "
                            "to compare against" % (rc, err[:120]))
        else:
            with open(SERVER, "r", encoding="utf-8") as fh:
                differs = fh.read() != blob
            base_path = st_work.write_text("baseline_server.py", blob)
            bsdrv = Driver(st_root, server=base_path,
                           name="mcp_wiki_state_baseline")
            patch_git_boundary(bsdrv.mod, st_root)
            b_text, c_text = bsdrv.freshness(), ldrv.freshness()
            b_report = bsdrv.mod.freshness_analyze(bsdrv.abs_wiki, "HEAD")
            c_report = ldrv.mod.freshness_analyze(ldrv.abs_wiki, "HEAD")
            if b_text != c_text:
                problems.append("the rendered report is NOT byte-identical:\n"
                                "  HEAD %r\n  work %r" % (b_text, c_text))
            if b_report != c_report:
                problems.append("freshness_analyze returns a different dict:\n"
                                "  HEAD %r\n  work %r" % (b_report, c_report))
            states = set((c_report or {}).get("summary", {}))
            if len(states) < 5:
                problems.append("the report covers only %r, so most of the "
                                "extracted if/else chain was never executed and "
                                "the identity claim is thin" % sorted(states))
        suite.record("L", "freshness-is-byte-identical-to-the-committed-server",
                     problems,
                     detail=[_d("baseline", "git show HEAD:Scripts/mcp-wiki.py "
                                            "(%d chars, differs from the "
                                            "worktree: %r)" % (len(blob), differs)),
                             _d("identical", "%r" % (b_text == c_text)),
                             _d("summary", "%r"
                                % ((c_report or {}).get("summary"),)),
                             _d("note", "vacuous when the worktree is clean -- the "
                                        "DURABLE form of this claim is the "
                                        "one-authority case below, which needs no "
                                        "baseline at all")],
                     text=c_text)

        # ---- one authority: `_classify_page` IS what freshness reports -------
        # The durable half of the case above: no matter what the two callers do
        # later, the recall path's state for a page must be, dict for dict, the
        # entry `freshness` publishes for it -- extra keys included, because
        # `changed_sources` / `missing` / `verified_at` are the evidence.
        report = ldrv.mod.freshness_analyze(ldrv.abs_wiki, "HEAD")
        by_rel = {p["path"]: p for p in report["pages"]}
        problems = []
        rows = []
        for page in L_PAGES:
            got = ldrv.classify(page[L_FILE])
            want = by_rel.get(page[L_FILE])
            rows.append("%-20s %-16s %s"
                        % (page[L_FILE], got.get("status"),
                           "== freshness" if got == want else "!= freshness"))
            if want is None:
                problems.append("%s: freshness does not report this page at all"
                                % page[L_FILE])
                continue
            if got != want:
                problems.append("%s: the recall path classifies %r, freshness "
                                "publishes %r" % (page[L_FILE], got, want))
            if got.get("status") != page[L_STATE]:
                problems.append("%s: state %r, the rule assigns %r"
                                % (page[L_FILE], got.get("status"),
                                   page[L_STATE]))
        if len(by_rel) != len(L_PAGES):
            problems.append("freshness reports %d page(s), the fixture has %d"
                            % (len(by_rel), len(L_PAGES)))
        if len({p[L_STATE] for p in L_PAGES}) < 8:
            problems.append("fixture drift: only %d distinct state(s), so the "
                            "agreement is not tested over the whole rule"
                            % len({p[L_STATE] for p in L_PAGES}))
        suite.record("L", "classify-page-is-the-only-authority-on-state", problems,
                     detail=[_d("pages", "%d, %d distinct state(s)"
                                % (len(L_PAGES),
                                   len({p[L_STATE] for p in L_PAGES}))),
                             _d("states", "%r" % L_ALL_STATES),
                             _d("compared", "the whole dict, not just `status`: "
                                            "changed_sources / missing / "
                                            "verified_at / reason are the evidence "
                                            "behind the word")]
                            + ["        " + r for r in rows],
                     text="")

        # ---- the state is classified once per page per reply ------------------
        counted = []
        _orig_classify = ldrv.mod._classify_page

        def _counting_classify(relpath, fm, repo, changed_for):
            counted.append(relpath)
            return _orig_classify(relpath, fm, repo, changed_for)

        ldrv.mod._classify_page = _counting_classify
        try:
            counted.clear()
            ldrv.search(L_SHARED_TERM, limit=L_LIMIT)
            search_calls = list(counted)
            counted.clear()
            ldrv.sources(L_SRC_CLEAN)
            s2p_calls = list(counted)
            counted.clear()
            ldrv.freshness()
            fresh_calls = list(counted)
            # The gated query, measured for its own sake: the classification
            # happens INSIDE the scoring loop, i.e. before the relevance gate
            # runs, so a query that is refused still pays for every lexical
            # candidate.  Reported, not gated -- the position is what makes the
            # `status` filter agree with the label, and that is worth the cost.
            counted.clear()
            gated_run = ldrv.search(L_GATED_QUERY)
            gated_calls = list(counted)
            # A query only SOME pages match, which is the only shape that can
            # see whether the classifier sits after the lexical test or before
            # it.  Both positions render the same answer, so nothing else in
            # this file can tell them apart -- and the difference is a git diff
            # per non-matching page on every call.
            counted.clear()
            narrow_run = ldrv.search(L_ANCHOR_TERM)
            narrow_calls = list(counted)
        finally:
            ldrv.mod._classify_page = _orig_classify
        problems = []
        if not (0 < len(narrow_run["hits"]) < len(L_PAGES)):
            problems.append("fixture drift: %r matches %d of %d page(s), so "
                            "'classified only for pages that matched' cannot be "
                            "distinguished from 'classified for the corpus'"
                            % (L_ANCHOR_TERM, len(narrow_run["hits"]),
                               len(L_PAGES)))
        elif len(narrow_calls) != len(narrow_run["hits"]):
            problems.append("%r rendered %d hit(s) but classified %d page(s) -- "
                            "the state is being measured before the lexical "
                            "test, so a query with one answer pays a git diff "
                            "for every page that has nothing to do with it"
                            % (L_ANCHOR_TERM, len(narrow_run["hits"]),
                               len(narrow_calls)))
        for label, calls in (("search", search_calls),
                             ("source_to_pages", s2p_calls),
                             ("freshness", fresh_calls),
                             ):
            if not calls:
                problems.append("%s classified nothing, so the count proves "
                                "nothing" % label)
            dupes = sorted({p for p in calls if calls.count(p) > 1})
            if dupes:
                problems.append("%s classified %r more than once -- every extra "
                                "call is a git diff the reply did not need"
                                % (label, dupes))
        if len(search_calls) > len(L_PAGES):
            problems.append("search classified %d time(s) over a %d-page corpus"
                            % (len(search_calls), len(L_PAGES)))
        # A SILENCED query must pay nothing. The classifier runs after the coverage
        # gate and the limit, so the pages the caller never sees are never
        # classified -- and one corpus-wide token drags every page into the lexical
        # match, which is why "after the match" was not narrow enough on its own.
        if gated_calls:
            problems.append("the gated query classified %d page(s) for a reply "
                            "that renders none -- a silenced query must pay no git"
                            % len(gated_calls))
        suite.record("L", "state-is-classified-once-per-page-per-reply", problems,
                     detail=[_d("search", "%d call(s), %d distinct"
                                % (len(search_calls), len(set(search_calls)))),
                             _d("source_to_pages", "%d call(s), %d distinct"
                                % (len(s2p_calls), len(set(s2p_calls)))),
                             _d("freshness", "%d call(s), %d distinct"
                                % (len(fresh_calls), len(set(fresh_calls)))),
                             _d("gated", "%d classification(s) for %d rendered "
                                         "hit(s) -- the gate and the limit run "
                                         "BEFORE the classifier, so a silenced "
                                         "query pays nothing"
                                % (len(gated_calls), len(gated_run["hits"]))),
                             _d("narrow", "%r: %d hit(s), %d classification(s) "
                                          "-- equal, so the state is measured "
                                          "AFTER the lexical test"
                                % (L_ANCHOR_TERM, len(narrow_run["hits"]),
                                   len(narrow_calls))),
                             _d("why", "the classification is cheap only because "
                                       "the diff behind it is memoized; asking "
                                       "twice for one page is asking git twice")],
                     text="")

        # ---- the filter moved AFTER the scoring loop: nothing else moved -----
        # `status` used to be applied BEFORE the score was computed and is now
        # applied after it.  A surviving page's score, coverage and `missed:` are
        # functions of the GLOBAL corpus statistics alone, so the position cannot
        # touch them -- and `best coverage N%` is the coverage of the top-scoring
        # SURVIVOR, before and after, which this case pins in both directions.
        gated = ldrv.search(L_GATED_QUERY)
        ungated = ldrv.search(L_GATED_QUERY, min_coverage=0.0, limit=L_LIMIT)
        problems = []
        rows = []
        top = ungated["hits"][0] if ungated["hits"] else None
        top_state = state_of(top) if top else ""
        keep = ldrv.search(L_GATED_QUERY, status=top_state)
        keep_ungated = ldrv.search(L_GATED_QUERY, min_coverage=0.0,
                                   status=top_state, limit=L_LIMIT)
        drop_state = next((s for s in L_ALL_STATES if s != top_state
                           and any(p[L_STATE] == s for p in L_PAGES)), "")
        drop = ldrv.search(L_GATED_QUERY, status=drop_state)
        if not ungated["hits"]:
            problems.append("the probe query matches nothing, so there is nothing "
                            "to compare")
        if gated["hits"]:
            problems.append("premise broken: %r is not gated out (%d hit(s)), so "
                            "no `best coverage` line is rendered at all"
                            % (L_GATED_QUERY, len(gated["hits"])))
        if gated["best_pct"] is None:
            problems.append("premise broken: the refusal reports no best coverage")
        if len(keep_ungated["hits"]) >= len(ungated["hits"]):
            problems.append("premise broken: status=%r removed nothing (%d of %d), "
                            "so 'the survivors kept their numbers' is not a claim "
                            "about a filtered run"
                            % (top_state, len(keep_ungated["hits"]),
                               len(ungated["hits"])))
        base_nums = {h["slug"]: (h["score"], h["cov"], tuple(h["missed"]))
                     for h in ungated["hits"]}
        for hit in keep_ungated["hits"]:
            got = (hit["score"], hit["cov"], tuple(hit["missed"]))
            rows.append("%-18s unfiltered %r  filtered %r"
                        % (hit["slug"], base_nums.get(hit["slug"]), got))
            if base_nums.get(hit["slug"]) != got:
                problems.append("%s: score/cov/missed moved when a status filter "
                                "was added: %r -> %r"
                                % (hit["slug"], base_nums.get(hit["slug"]), got))
        if top is not None and keep["best_pct"] != gated["best_pct"]:
            problems.append("the refusal reports best coverage %r%% with the "
                            "filter and %r%% without it, although the top-scoring "
                            "page (%s, state %r) survives BOTH"
                            % (keep["best_pct"], gated["best_pct"], top["slug"],
                               top_state))
        # The other direction, so the case states the real semantics rather than
        # only the invariant half: a filter that removes the best page DOES move
        # the number, and always has -- `best_cov` is the best SURVIVOR's.
        if drop_state and drop["best_pct"] == gated["best_pct"]:
            problems.append("status=%r reports the same best coverage %r%% "
                            "although it removes every page that scored that -- "
                            "the number would then be an artefact, not the best "
                            "survivor's" % (drop_state, drop["best_pct"]))
        suite.record("L", "status-filter-does-not-perturb-score-or-best-coverage",
                     problems,
                     detail=[_d("query", repr(L_GATED_QUERY)),
                             _d("top", "%s [%s] cov %r%%"
                                % (top["slug"] if top else None, top_state,
                                   top["cov"] if top else None)),
                             _d("best cov", "no filter %r%%, status=%r %r%%, "
                                            "status=%r %r%%"
                                % (gated["best_pct"], top_state,
                                   keep["best_pct"], drop_state,
                                   drop["best_pct"])),
                             _d("survivors", "%d of %d at min_coverage=0.0"
                                % (len(keep_ungated["hits"]),
                                   len(ungated["hits"]))),
                             _d("proof", "score and coverage are functions of the "
                                         "GLOBAL df/idf table, which is built "
                                         "before any filter runs -- so no filter, "
                                         "at any position, can move them")]
                            + ["        " + r for r in rows],
                     text=gated["text"])

        # ---- the cross-call memo: it memoizes, and it dies with HEAD ---------
        cache_root = build_state_fixture(cache_work, agree=False)
        cache_work.write_text(os.path.join(".git", "HEAD"), "%s\n" % ("a" * 40))
        cdrv = Driver(cache_root, name="mcp_wiki_state_memo")
        c_log = patch_git_boundary(cdrv.mod, cache_root)
        cdrv.mod._FRESH_CACHE.clear()
        cdrv.search(L_SHARED_TERM)
        first = list(c_log)
        c_log.clear()
        cdrv.search(L_SHARED_TERM)
        second = list(c_log)
        cache_work.write_text(os.path.join(".git", "HEAD"), "%s\n" % ("b" * 40))
        c_log.clear()
        cdrv.search(L_SHARED_TERM)
        third = list(c_log)
        problems = []
        if cdrv.mod._head_sha_nospawn(cache_root) != "b" * 40:
            problems.append("premise broken: _head_sha_nospawn reads %r, not the "
                            "sha just written -- the memo cannot be keyed on HEAD "
                            "here" % cdrv.mod._head_sha_nospawn(cache_root))
        if not first:
            problems.append("the first reply resolved no commit, so there is "
                            "nothing to memoize")
        if second:
            problems.append("the second identical reply re-resolved %r -- the memo "
                            "is not memoizing, and each entry is a git diff"
                            % second)
        if sorted(third) != sorted(first):
            problems.append("after HEAD moved the reply resolved %r, want the "
                            "same commits as the cold run %r -- a memo that "
                            "survives a HEAD change answers about a repo that no "
                            "longer exists" % (sorted(third), sorted(first)))
        if len(cdrv.mod._FRESH_CACHE) != 1:
            problems.append("_FRESH_CACHE holds %d entr(ies) %r; the old HEAD's "
                            "one can never be asked about again"
                            % (len(cdrv.mod._FRESH_CACHE),
                               list(cdrv.mod._FRESH_CACHE)))
        suite.record("L", "diff-memo-spans-calls-and-dies-with-head", problems,
                     detail=[_d("cold", "%d commit(s) resolved: %r"
                                % (len(first), sorted(c for c, _h in first))),
                             _d("warm", "%d" % len(second)),
                             _d("after HEAD", "%d" % len(third)),
                             _d("store", "%r" % [k[1][:8]
                                                 for k in cdrv.mod._FRESH_CACHE]),
                             _d("why", "each miss is a ~47 ms subprocess and 98% "
                                       "of that is process startup, so the memo "
                                       "is the difference between a 5 ms reply and "
                                       "a 250 ms one")],
                     text="")

        # ---- two repos in one process: slow is fine, wrong is not ------------
        # `_recall_diff_cache` CLEARS the whole store on a miss, not only on a
        # HEAD move -- and the `root` param lets one process be asked about two
        # different repos.  What must never happen is one repo being served the
        # other's changed-file sets.
        mem = cdrv.mod
        repo_a = os.path.join(cache_work.path, "repoA")
        repo_b = os.path.join(cache_work.path, "repoB")
        for path, sha in ((repo_a, "1" * 40), (repo_b, "2" * 40)):
            cache_work.write_text(os.path.join(os.path.basename(path), ".git",
                                               "HEAD"), "%s\n" % sha)
        mem._FRESH_CACHE.clear()
        dict_a = mem._recall_diff_cache(repo_a)
        dict_a["sentinel-A"] = {"a"}
        dict_b = mem._recall_diff_cache(repo_b)
        dict_b["sentinel-B"] = {"b"}
        dict_a2 = mem._recall_diff_cache(repo_a)
        dict_b2 = mem._recall_diff_cache(repo_b)
        problems = []
        if mem._head_sha_nospawn(repo_a) == mem._head_sha_nospawn(repo_b):
            problems.append("premise broken: both fixture repos report HEAD %r, "
                            "so the two keys are indistinguishable"
                            % mem._head_sha_nospawn(repo_a))
        if "sentinel-B" in dict_a2:
            problems.append("repo A was handed repo B's changed-file sets (%r) -- "
                            "the label it renders would be measured against the "
                            "wrong repository" % sorted(dict_a2))
        if "sentinel-A" in dict_b2:
            problems.append("repo B was handed repo A's changed-file sets (%r)"
                            % sorted(dict_b2))
        if "sentinel-A" in dict_b or "sentinel-B" in dict_a:
            problems.append("the two dicts are the same object, so every entry "
                            "either repo computes is served to the other")
        suite.record("L", "two-repos-never-share-a-changed-file-set", problems,
                     detail=[_d("A then B", "A kept its memo across B's call: %r"
                                % ("sentinel-A" in dict_a2)),
                             _d("A again", "%r" % sorted(dict_a2)),
                             _d("B again", "%r" % sorted(dict_b2)),
                             _d("store", "%d entr(ies) after four calls"
                                % len(mem._FRESH_CACHE)),
                             _d("measured", "the store is CLEARED on any miss, not "
                                            "only on a HEAD move, so alternating "
                                            "roots recomputes every time -- slow, "
                                            "and correct: a dropped memo can only "
                                            "cost a subprocess, never a wrong "
                                            "label")],
                     text="")
    finally:
        st_work.cleanup()
        ag_work.cleanup()
        cache_work.cleanup()

    # ============ M: the truncation ceiling (W1/W2) ============
    # `_finalize` is a pure function of (markdown, params) — no workspace, no git.
    # These cases are about the CUT, not about the corpus.
    fin = H.load_module_from_path("mcp_wiki_finalize", SERVER)
    HIT = "1. **Page** — slug `subsystems/scripts.md#mcp-servers` [subsystem/stale]"
    doc = "\n".join([HIT] * 6) + "\n"
    whole_lines = set(doc.split("\n"))

    cut_at = len(HIT) * 3 + 20            # lands well inside the 4th line
    got = fin._finalize(doc, {"max_answer_chars": cut_at})["__raw_text__"]
    kept, _, marker = got.partition("\n\n… (truncated")

    partial = [ln for ln in kept.split("\n") if ln and ln not in whole_lines]
    suite.record("M", "cut-lands-on-a-line-boundary",
                 ["kept a PARTIAL line, so a broken anchor still reads as an "
                  "anchor: %r" % partial] if partial else [],
                 detail=[_d("limit", "%d chars, mid-way through line 4" % cut_at),
                         _d("kept", "%d line(s), every one whole"
                            % len([ln for ln in kept.split("\n") if ln])),
                         _d("why", "every line here is structure -- an anchor, a "
                                   "section entry with its size, a missed: list. "
                                   "Half an anchor costs the caller a call to "
                                   "discover it does not resolve")])

    nums = [int(x) for x in marker.replace("(", " ").replace(")", " ").split()
            if x.isdigit()]
    problems = []
    if len(nums) < 2:
        problems.append("the marker does not state N of M: %r" % marker)
    else:
        said_kept, said_full = nums[0], nums[1]
        if said_full != len(doc):
            problems.append("marker claims %d total against a %d-char document"
                            % (said_full, len(doc)))
        if said_kept != len(kept):
            problems.append("marker claims %d kept against %d delivered"
                            % (said_kept, len(kept)))
        if said_full == cut_at:
            problems.append("the marker echoes the PARAMETER, not the real length")
    suite.record("M", "marker-states-the-real-length-not-the-parameter", problems,
                 detail=[_d("marker", marker.strip()),
                         _d("document", "%d chars, ceiling %d" % (len(doc), cut_at)),
                         _d("why", "the caller knows the ceiling it asked for; what "
                                   "it cannot know is how much is missing, which is "
                                   "the whole ask-again-or-narrow decision")])

    # A first line longer than the entire ceiling: no boundary exists to honour,
    # and an empty reply would be worse than a hard character cut.
    got = fin._finalize(doc, {"max_answer_chars": 20})["__raw_text__"]
    suite.record("M", "no-boundary-falls-back-to-the-character-cut",
                 ["a first line over the limit produced an EMPTY answer"]
                 if not got.split("\n")[0].strip() else [],
                 detail=[_d("limit", "20 chars against a %d-char first line"
                            % len(HIT)),
                         _d("kept", "%r" % got.split("\n")[0][:40])])

    # ============ H: hygiene ============
    pyc_after = H.pycache_snapshot()
    new_pyc = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record("H", "no-pycache-written",
                 [] if not (new_pyc or touched)
                 else ["new=%r touched=%r" % (new_pyc, touched)],
                 detail=[_d("pyc files", "before=%d after=%d"
                            % (len(pyc_before), len(pyc_after)))])

    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
