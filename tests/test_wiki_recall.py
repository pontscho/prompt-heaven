#!/usr/bin/env python3
"""Mechanical suite for the `search` relevance gate in Scripts/mcp-wiki.py
(36 cases, A-I).

Drives `handle_wiki_call` IN-PROCESS against a SYNTHETIC six-page wiki built in
a temp workspace -- never the repo's real docs/.  Nothing is spawned, nothing
is written outside mkdtemp, and `reindex`/`freshness` are never called.

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
    """One in-process wiki server pointed at the synthetic corpus."""

    def __init__(self, project_root):
        self.mod = H.load_module_from_path("mcp_wiki_under_test", SERVER)
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

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-wiki `search` relevance gate: silence, "
                                "measured calibration window, floored "
                                "percentages, query-side stopwords",
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
