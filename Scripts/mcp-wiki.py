#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""MCP-Wiki: read + search MCP server for the p:wiki docs/ knowledge base.

Single-tool dispatcher pattern (like mcp-purity / mcp-git): one tool (wiki_call)
routes to internal handlers via the 'function' parameter. Makes the docs/ wiki
first-class searchable from inside a coding session — instead of shelling out to
the p:wiki scripts (freshness.py / reindex.py) by absolute path and reading pages
one at a time.

Functions:
  search           BM25F-ranked (prefix, per-field weighted) full-text search over
                   pages, behind a calibrated relevance gate so a query the wiki
                   cannot answer gets silence instead of the best-scoring noise.
                   A page's type is a ranking-only signal on top (genre words
                   promote their own kind); it can reorder an answer but never
                   changes what is claimed about a page's coverage. A page's
                   declared `aliases:` are the MIRROR of that: an alternative
                   name IS a claim about content, so it is a full search field
                   and does count toward coverage — that is how adr 0001 answers
                   for `merge` while its prose only ever says `unify` / `fold`
  source_to_pages  reverse lookup: a source file/anchor -> the pages that cover it
  get_page         read one page (whole, a single section, a window of file
                   lines, or — when the section misses or the body is declined —
                   just its section index) by slug/path
  list             list pages, grouped by type, with type/status/prefix filters
  freshness        corpus report: what verify can PROVE broken (gating) plus git
                   lag, which is now an advisory line that says it is a
                   measurement and not a verdict
  reindex          regenerate INDEX.md + structural audit (ports reindex.py logic)
  stats            page counts by type/status + dup/orphan/malformed audit
  verify           resolve every `path` / `path:symbol` anchor in the corpus and
                   report the ones that do not — the job a human currently does
                   one anchor at a time — plus every measured region's state
  measure          re-render the MEASURED REGIONS of the corpus: a page block
                   whose body comes from a command in docs/measurements.json
                   rather than from a human's keyboard. Check mode by default;
                   a hand edit is refused, never silently overwritten

The stdlib-only frontmatter parser is vendored from the p:wiki `_wikilib.py`
(kept in sync by hand — the p:wiki schema's §5 parseable subset is the contract).
Symbol-level anchor verification is the WEAK half here on purpose: `verify` runs
a stdlib text matcher and declares its own limits, while the authoritative
resolution stays with the LLM (p:minion-librarian) via the language MCP servers.

`measure` and `verify` are the only two functions that may execute anything, and
that boundary is structural rather than conventional — see the TRUST BOUNDARY
note above `_ExecutionGrant`.

Output is always Markdown (no JSON/YAML).

Usage:
  python3 mcp-wiki.py --project-root <path>
                      [--wiki-root docs]    # wiki root, relative to project root
                      [--strict]            # reject root params outside project
                      [--debug]
                      [--log-file <path>]   # implies --debug

Call `wiki_call` with no `function` to print the function list.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger("mcp-wiki")


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_logging.py :: _configure_logging
def _configure_logging(debug, log_file):
    """DEBUG to *log_file* (mode 0600) or to stderr, else WARNING. Never stdout.

    Either flag enables DEBUG: `--log-file` does not redirect the log, it turns
    it on. `Scripts/_mcp_logging.py` carries the rest -- why the 0600 pair needs
    both calls, and what deliberately stays out of this block.
    """
    level = logging.DEBUG if (debug or log_file) else logging.WARNING
    handlers = []
    if log_file:
        fd = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        handlers.append(logging.StreamHandler(os.fdopen(fd, "a")))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
# END GENERATED: f77d402d6254


DEFAULT_WIKI_ROOT = "docs"

# The output ceiling, named rather than inlined twice in `_finalize`. This is
# NOT the fleet's 24000-char convention and does not take that block: a wiki
# reply is a whole page the caller asked for by name, where a cut costs a second
# call to a document the model was already reading. ADR 0013 ratifies the class.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_paging.py :: DEFAULT_MAX_CHARS
DEFAULT_MAX_CHARS = 100_000
# END GENERATED: 4a4d02ccb7a4

# Wall-clock ceiling on ONE git invocation. Every git call in this file is a
# local read-only query — `rev-parse --show-toplevel` (16.41 ms measured),
# `rev-parse --short HEAD` (19.04 ms) and `diff --name-only` (~47 ms, 98% of it
# process startup) — so 30 s is ~600x the slowest of them and can only ever be
# reached by a git that is not going to answer at all: a credential or editor
# prompt that got a terminal from somewhere despite stdin=DEVNULL, an NFS or
# FUSE-backed worktree that has stopped responding, or a stuck index.lock.
# `subprocess.run` with no timeout waits FOREVER, and forever is not a duration a
# server may spend inside a request. It matters even now that handlers run off
# the event-loop thread (see McpServer.run): a parked git no longer deafens the
# server, but it does hold one of MAX_INFLIGHT_REQUESTS workers for good, and 8
# such calls retire the pool permanently. A timeout returns 124 — the shell
# convention, also what mcp-inspect.py's runner returns — which every caller here
# already treats as "git could not answer": `_changed_files` records _INVALID and
# the page classifies as `unverified`, never as `current`. Degrading toward "not
# checkable" is the correct direction for a documentation freshness gate.
GIT_TIMEOUT_SEC = 30

# Page-type ordering for INDEX / list rendering (mirrors reindex.py).
TYPE_ORDER = ["overview", "subsystem", "component", "reference", "analysis",
              "concept", "spec", "runbook", "adr", "glossary"]

# Frontmatter `status:` is editorial intent, never freshness (p:wiki schema §3).
# `active` is the unmarked normal state, so INDEX.md labels only a deliberate
# `draft` or `deprecated`. `current`/`stale` are rejected: they are two of the
# eight git-measured states below, and a hand-written field borrowing HEAD's
# vocabulary is what made the index print `[current]` for all ten pages while
# git measured nine of them stale.
INDEX_LABELLED = ("draft", "deprecated")
STATUS_FORBIDDEN = ("current", "stale")

# Freshness statuses listed page-by-page; everything else is summarized.
DETAIL_STATUSES = ["stale", "orphaned-source", "unverified", "promotable"]
# Page types not bound to code sources and never freshness-tracked.
UNTRACKED_TYPES = {"overview", "adr", "glossary"}

# What `freshness` CALLS things — the vocabulary, not the measurement.
#
# `gating` used to mean `stale + orphaned-source + unverified`, where `stale`
# means only that git saw one of a page's listed sources move since the page was
# verified. Measured on this wiki, that put 22 of 32 pages in the gate at a
# MEDIAN LAG OF 61 COMMITS: any commit touching any listed source stales a page,
# so the number counted commits and was read as a verdict. Git lag cannot tell a
# moved comma from a reversed decision. It is a MEASUREMENT.
#
# The measurement does not change — every git figure the report published it
# still publishes, page by page, under the same status names. What changed is
# the CLAIM attached to it:
#
#   * `gating` now counts what `verify` can PROVE — a `sources:` or inline
#     anchor that does not resolve, or a measured region that is stale,
#     hand-edited, unregistered or malformed. Those are defects; a human fixes
#     them, or the page is wrong.
#   * git lag moves to an `advisory:` line that says what it is, in those words.
#     It is the honest end of the same sentence: these pages have sources that
#     moved, a human read may be owed, and nothing here claims they are wrong.
#
# CONSISTENT WITH adr 0002 rather than a departure from it. That decision's rule
# is that nothing may claim a freshness it cannot measure — it renamed a
# hand-written `current` and suppressed it in INDEX.md because a generated file
# cannot track HEAD. Git lag CAN measure that a file moved; what it cannot
# measure is that a page is wrong, and calling it `gating` was exactly that
# over-claim, one layer further in than adr 0002 reached. Nothing is stored,
# nothing is written into a file, and freshness is still asked per call.
#
# `orphaned-source` appears in BOTH vocabularies and that is not a collision:
# the state means a `sources:` path is gone from the tree, which is precisely a
# broken anchor, reached by the git path instead of by `verify`'s walk. The two
# agree by construction.
GATING_CLASSES = ("orphaned-source", "broken-anchor", "measured-region")
GATING_LINE_PREFIX = "gating: "
ADVISORY_LINE_PREFIX = "advisory: "
# The git-lag statuses the advisory line accounts for, in the order it names
# them: a source that moved, and a page nothing can be compared against at all.
ADVISORY_STATUSES = ("stale", "unverified")
# How many gating pages `freshness` names before it defers to `verify`. The
# report is a census with a summary; the per-defect list is `verify`'s answer,
# and a freshness report that grew into one would be two functions.
GATING_DETAIL_LIMIT = 10

# Files / dirs that are not wiki pages (mirrors _wikilib.py).
SKIP_FILES = {"INDEX.md"}
SKIP_DIRS = {"sources", "plans", ".git", ".claude", ".cache"}

# Search field weights — a term hit in the title counts far more than in the body.
# `aliases` sits at the anchor's weight: like `sources:`, it is DECLARED metadata
# rather than prose, and it is deliberately BELOW name/title, because an alias is
# not the page's canonical name — see the _SEARCH_FIELDS comment for the measured
# case and the curation rule that keeps it from moving the calibration.
FIELD_WEIGHTS = {"name": 8, "title": 8, "anchor": 5, "aliases": 5,
                 "description": 4, "heading": 3, "body": 1}

# W9 — the page TYPE as a ranking signal. Measured motivation: on `decision` the
# spec scored 1.81 and the adr 1.77, i.e. the page whose whole genre IS a
# decision record LOST to one that merely cites it ("...Decision recorded in adr
# 0001." sits in the spec's description, weight 4, while `type: adr` carried
# weight ZERO). The type was filterable and printable but never scored.
#
# RANKING ONLY — deliberately kept out of `_SEARCH_FIELDS`, and that exclusion is
# the whole design. `df`, `hit_terms` and therefore `coverage` all derive from
# _SEARCH_FIELDS (see `_fn_search`), so admitting a category signal there would
# let `architecture decision record` report 100% coverage on a page that writes
# not one line about those words — silently repealing the W8 gate's only claim
# ("this page carries information about your terms"). The type can reorder an
# answer; it can never change what we assert about a page's coverage.
#
# Tokens come from the SCHEMA's own type table (ClaudeCode/skills/wiki/SKILL.md
# Schema §2 "Page types"), not from invented synonyms — `adr` is described there as "A
# decision: what, why, alternatives, consequences", which is exactly the
# vocabulary a caller asking for a decision uses. Two curation rules, both
# mechanically checked by the suite:
#   * a type's token set may never contain ANOTHER type's name (the SCHEMA
#     describes `component` as "a single unit inside a subsystem", and inheriting
#     `subsystem` there would promote components on every subsystem query);
#   * function words are omitted — they are dropped from the query side by
#     QUERY_STOPWORDS anyway, so carrying them here is dead weight that only
#     inflates the field length;
#   * every token must survive `_tokenize` unchanged. `_TOKEN_RE` splits on the
#     hyphen, so the SCHEMA's "cross-cutting" and "how-to" would arrive as the
#     function-word halves `cross`/`cutting` and `how`/`to` — a compound written
#     here reads as one signal and silently scores as two weak ones.
TYPE_SIGNAL_TOKENS = {
    "overview":  ("overview", "project", "identity", "map"),
    "subsystem": ("subsystem", "area", "cluster", "directory"),
    "component": ("component", "unit", "module"),
    "reference": ("reference", "api", "symbol"),
    "analysis":  ("analysis", "performance", "network", "behavioral",
                  "investigation", "measurement"),
    "concept":   ("concept", "idea", "theme"),
    "spec":      ("spec", "specification", "design", "plan", "implementation"),
    "runbook":   ("runbook", "operational", "procedure", "recipe"),
    "adr":       ("adr", "decision", "rationale", "alternatives",
                  "consequences", "tradeoff"),
    "glossary":  ("glossary", "terminology", "definition"),
}
# Weight of a type-signal hit, on the same scale as FIELD_WEIGHTS. CALIBRATED,
# not chosen — `.claude/tmp/wiki-density/probe_w9.py` sweeps 0/2/3/4/6/8 over
# eight queries. Unlike DEFAULT_MIN_COVERAGE this is NOT a separation threshold
# with a window: raising it has no upper wall (collateral damage is ZERO through
# 8 — four control queries keep their exact ranking and scores). The floor and
# the ceiling come from different arguments:
#
#   * below 4 the ranking does not flip at all (`decision`: adr 1.77 vs spec
#     1.81 at 0, still 1.81 vs 1.81 at 3);
#   * at 4 it flips but the answer CONTRADICTS ITSELF ON SCREEN — on `clangd
#     purity decision` both pages render as 3.78 while one is ranked above the
#     other, the same defect [D57] floors the coverage percentages to avoid;
#   * at 6 both target cases flip with a visible margin (1.84 vs 1.81, 3.80 vs
#     3.78) and nothing else moves;
#   * 8 buys almost nothing more (BM25 saturation) and equals the weight of the
#     page TITLE, which a mere category label must not be worth.
#
# The saturation is a FEATURE here, not a limit worked around: because ftilde is
# saturated once, the signal can only decide a close race — on `clangd purity
# merge decision` the spec leads 6.53 to 3.74 and no weight up to 8 overturns it.
# That is a tie-breaker's behaviour without a tie-breaker's discrete gap knob.
TYPE_SIGNAL_WEIGHT = 6
# Relevance gate for `search`: the share of the query's total idf mass a page
# must actually carry to be reported at all. Without it the ONLY silencing
# condition is "zero terms matched", which never fires on this corpus -- the
# token `mcp` occurs in all 10 pages, so one ubiquitous term drags every page
# into every answer and the search cannot say "I don't know".
#
# CALIBRATED, not chosen: .claude/tmp/wiki-density/probe.py runs six fixed
# queries through both HEAD and the worktree. The best FALSE positive tops out
# at 49% coverage; the weakest REAL answer sits at 59%. The usable window is
# therefore (0.49, 0.59] and this value is the middle of it. Above 0.59 real
# answers start dying; at or below 0.49 false positives leak back in.
#
# The SCORE cannot do this job -- measured, a real hit scored 5.28 while a false
# one scored 5.70. Coverage separates them because a term the corpus has never
# seen takes the maximum idf, so an unknown topic drags coverage down hard.
DEFAULT_MIN_COVERAGE = 0.55

# Query-side stopwords, dropped BEFORE idf is computed.
#
# Not a nicety -- on a 10-page corpus idf is INVERTED for function words, and it
# corrupts both the ranking and the coverage gate above. Measured on the real
# wiki: `did` occurs in 1 page -> idf 1.99, while `merge` (2 pages) gets 1.48 and
# `clangd` (7 pages) only 0.38. So `why did we merge clangd into purity` hands 61%
# of its total idf mass to four function words, and the one page carrying NONE of
# the content terms wins the query on them. Rarity looks like information; on a
# corpus this small it is just an accident of prose style.
#
# Hand-curated on purpose. A df-based cutoff cannot do this job: the function
# words here are RARE, so any "too common to matter" rule leaves them untouched.
#
# Deliberately ABSENT: query verbs (search, find, show, get, list, build, run,
# create, make, use, add). A general-purpose stoplist drops those, and doing so
# here would be a bug -- in THIS corpus they are function names and topic words
# (`search`, `get_page`, `list`, `stats`), and one of the calibration queries is
# built on `search` itself. Also absent for the same reason: `done`, which reads
# as a participle but is a plausible frontmatter `status:` value, and `status` is
# a filter this very function accepts -- dropping it would answer "the query is
# all function words" about first-class schema metadata.
#
# Editing this set re-opens the DEFAULT_MIN_COVERAGE calibration, because it
# changes the denominator of every coverage figure. The set is an input to the
# measurement, not a cosmetic filter.
QUERY_STOPWORDS = frozenset("""
    a an the this that these those
    i we you he she it they me us him her them my our your his its their
    am is are was were be been being do does did have has had having
    can could will would shall should may might must
    and or but nor if then than because while although though whether
    as at by for from in into of on onto to up with within without
    over under about between through during before after above below off out
    again further here there when where why what which who whom whose how
    not no nor yes very just only also too either neither both each
    """.split())

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_INVALID = "<invalid-commit>"


# ---------------------------------------------------------------------------
# Sandbox utility (same shape as mcp-purity / mcp-git)
# ---------------------------------------------------------------------------

def safe_path(project_root: str, relative_path: str, strict: bool = False) -> str:
    if os.path.isabs(relative_path) and not strict:
        return os.path.realpath(relative_path)
    resolved = os.path.realpath(os.path.join(project_root, relative_path))
    if not resolved.startswith(project_root + os.sep) and resolved != project_root:
        raise ValueError(f"Path escapes project root: {relative_path}")
    return resolved


# ---------------------------------------------------------------------------
# Vendored wiki helpers (from p:wiki/scripts/_wikilib.py; tabs -> 4 spaces).
# Keep in sync with the p:wiki schema §5 (the stdlib-parseable frontmatter subset).
# ---------------------------------------------------------------------------

def git(args: List[str], cwd: str) -> Tuple[int, str, str]:
    """Run a git command; return (returncode, stdout, stderr).

    stdin=DEVNULL is load-bearing: the call below redirects only stdout/stderr,
    so stdin would be inherited -- and this server's stdin is the JSON-RPC stream.
    A git subcommand that reads stdin (hash-object --stdin, cat-file --batch,
    apply, or any credential/editor prompt) would consume protocol messages and
    desync the session. Same fix as mcp-git.py. No caller here needs stdin.
    """
    try:
        proc = subprocess.run(
            ["git"] + args, cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            timeout=GIT_TIMEOUT_SEC,   # never wait forever; see the constant
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git %s timed out after %ds" % (args[0] if args else "", GIT_TIMEOUT_SEC)


def repo_root(start: Optional[str] = None) -> str:
    """Best-effort repo root via git; falls back to the given/current dir."""
    base = start or os.getcwd()
    code, out, _ = git(["rev-parse", "--show-toplevel"], cwd=base)
    if code == 0 and out.strip():
        return out.strip()
    return os.path.abspath(base)


def split_frontmatter(text: str) -> Tuple[str, str]:
    """Split a document into (frontmatter_block, body)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "", text


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_unquote(x.strip()) for x in inner.split(",") if x.strip()]
    return _unquote(raw)


def _collect_block(lines: List[str], start: int) -> Tuple[Any, int]:
    """Collect an indented block (list or dict) beginning at index `start`."""
    items_list: List[Any] = []
    items_dict: Dict[str, Any] = {}
    mode: Optional[str] = None
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if not line[0].isspace():
            break  # dedent back to top level
        if stripped.startswith("- "):
            if mode is None:
                mode = "list"
            if mode == "list":
                items_list.append(_parse_scalar(stripped[2:]))
            i += 1
        elif ":" in stripped:
            if mode is None:
                mode = "dict"
            if mode == "dict":
                key, _, val = stripped.partition(":")
                items_dict[key.strip()] = _parse_scalar(val)
            i += 1
        else:
            break
    consumed = i - start
    if mode == "dict":
        return items_dict, consumed
    return items_list, consumed


def parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse the constrained frontmatter subset into a dict."""
    fm, _ = split_frontmatter(text)
    result: Dict[str, Any] = {}
    lines = fm.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[0].isspace() or ":" not in line:
            i += 1  # stray/indented line at top level; ignore
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = _parse_scalar(rest)
            i += 1
            continue
        block, consumed = _collect_block(lines, i + 1)
        result[key] = block
        i = i + 1 + consumed
    return result


def read_page(path: str) -> Tuple[Dict[str, Any], str]:
    """Read a page file; return (frontmatter_dict, body)."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    _, body = split_frontmatter(text)
    return parse_frontmatter(text), body


def iter_pages(root: str) -> Iterator[Tuple[str, Dict[str, Any], str]]:
    """Yield (relpath_from_root, frontmatter, body) for every wiki page."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            fm, body = read_page(full)
            yield rel, fm, body


def extract_wikilinks(body: str) -> List[str]:
    """Return the slugs referenced as [[slug]] in a page body."""
    return [m.strip() for m in _WIKILINK_RE.findall(body)]


def as_list(value: Any) -> List[Any]:
    """Coerce a frontmatter value to a list (tolerates scalar or missing)."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# Text / heading helpers
# ---------------------------------------------------------------------------

def _source_path(source: str, repo: Optional[str] = None) -> str:
    """Return the filesystem path part of an anchor (`path` or `path:symbol`).

    Repo path segments can themselves contain ':' (this repo's `p:<name>` skill/
    agent naming), which collides with the symbol separator. When `repo` is
    given, return the LONGEST colon-prefix that exists on disk — so both a
    `p:<name>` dir inside the path and a trailing `:symbol` are handled. Without
    `repo`, fall back to splitting at the first ':'.
    """
    source = str(source)
    if repo is None:
        return source.split(":", 1)[0]
    if os.path.exists(os.path.join(repo, source)):
        return source
    parts = source.split(":")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ":".join(parts[:i])
        if os.path.exists(os.path.join(repo, candidate)):
            return candidate
    return parts[0]


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _heading_slug(text: str) -> str:
    """GitHub-flavored heading anchor slug."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def _headings(body: str) -> List[Tuple[int, int, str]]:
    """Return [(line_index, level, text)] for ATX headings outside code fences."""
    out: List[Tuple[int, int, str]] = []
    in_fence = False
    for i, line in enumerate(body.splitlines()):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _nearest_heading(headings: List[Tuple[int, int, str]], line_index: int):
    """Last heading whose line index precedes `line_index`, or None."""
    found = None
    for h in headings:
        if h[0] <= line_index:
            found = h
        else:
            break
    return found


def _best_snippet(body: str, headings, terms, relpath: str, width: int = 200):
    """Return (snippet, section_title, anchor) for the first body line matching
    any term. Falls back to ('', '', relpath) when the match is frontmatter-only."""
    for i, line in enumerate(body.splitlines()):
        low = line.lower()
        if any(t in low for t in terms):
            snippet = " ".join(line.split())
            if len(snippet) > width:
                snippet = snippet[:width].rstrip() + "…"
            h = _nearest_heading(headings, i)
            if h:
                return snippet, h[2], "%s#%s" % (relpath, _heading_slug(h[2]))
            return snippet, "", relpath
    return "", "", relpath


def _extract_section(body: str, name: str) -> Optional[str]:
    """Return the markdown of the section whose heading matches `name`
    (case-insensitive text or slug), from the heading to the next same/higher
    level heading. None if not found."""
    headings = _headings(body)
    lines = body.splitlines()
    target = name.strip().lower()
    target_slug = _heading_slug(name)
    for idx, (line_i, level, text) in enumerate(headings):
        if text.lower() == target or _heading_slug(text) == target_slug:
            end = len(lines)
            for (nl_i, nl_level, _t) in headings[idx + 1:]:
                if nl_level <= level:
                    end = nl_i
                    break
            return "\n".join(lines[line_i:end]).strip()
    return None


def _body_line_offset(text: str) -> int:
    """File line (1-based) that the body's first line sits on.

    `read_page` hands the body back with the frontmatter already stripped, so a
    heading's index inside the body is NOT its line in the file. Printing the
    body-relative number as L<n> would be wrong by exactly the frontmatter
    height — silently, and only for pages that have frontmatter, which is all of
    them. Derived from `split_frontmatter` rather than re-scanning for the '---'
    delimiters, so there is one parser for where the body starts, not two.
    """
    _fm, body = split_frontmatter(text)
    if body == text:
        return 1
    return len(text.splitlines()) - len(body.splitlines()) + 1


def _section_list(body: str, line_offset: Optional[int] = 1, depth: int = 2):
    """Return (lines, deeper, deepest) for the page's section index.

    Each line reads `- <heading> (L<file line>, <n>c)`, where <n> is the size of
    the slice `get_page` would return for that heading. The caller is choosing a
    slice, so the size IS the decision — measured on this wiki, section sizes run
    from 17c to 69435c, and a list without them invites the caller to ask for the
    69435c one blind.

    A heading whose slice IS the whole page is skipped, because offering it as a
    section is offering the whole page under another name — which is what
    `get_page` without `section` already does. On this wiki that rule hides
    exactly the H1 of all ten pages (`max_sec == body_c` on every one), but it is
    written as the rule it actually is: `level < 2` would be a proxy that holds
    only while no page has a second H1. Two H1s bound each other, and then both
    slices are real and both belong in the list — a page is not required to have
    one title just because these ten do.

    `deeper` counts headings below `depth` and `deepest` is the level that would
    reach them, so the caller can be TOLD the escape hatch instead of hitting the
    same dead end one level down. `depth` is a caller knob, not a tuned
    threshold: level 2 keeps the common case at ~267c/page against ~944c for
    every heading, and nothing here is calibrated against the corpus.

    `line_offset` None means the file line could not be established, and then no
    L is printed at all. A number that is silently wrong by the frontmatter
    height is worse than no number: the caller cannot tell it is being misled,
    and this whole list exists so it does not have to guess.
    """
    headings = _headings(body)
    lines = body.splitlines()
    whole = len(body.strip())
    out: List[str] = []
    deeper, deepest = 0, 0
    for idx, (line_i, level, text) in enumerate(headings):
        end = len(lines)
        for (nl_i, nl_level, _t) in headings[idx + 1:]:
            if nl_level <= level:
                end = nl_i
                break
        size = len("\n".join(lines[line_i:end]).strip())
        # Sized BEFORE the depth test on purpose: a page-spanning heading is not
        # hidden by `depth`, so counting it as `deeper` would advertise an escape
        # hatch that reveals nothing.
        if size >= whole:
            continue
        if level > depth:
            deeper += 1
            deepest = max(deepest, level)
            continue
        out.append("- %s (%dc)" % (text, size) if line_offset is None else
                   "- %s (L%d, %dc)" % (text, line_i + line_offset, size))
    return out, deeper, deepest


def _section_index_block(body: str, path: str, depth: int) -> List[str]:
    """The `sections:` block, for the two answers that carry no body.

    Both callers are places where the caller is left holding a pointer: a
    `section` that did not match, and `include_body: false`. Measured on this
    wiki, discovering the heading names without this block costs a full-body
    re-read — 115026c across a four-page sample against 2035c of refusals, 56x —
    and `include_body: false` was no escape hatch, since it drops the headings
    along with the body.
    """
    # A second read of a file `iter_pages` already parsed — deliberately, because
    # the body arrives frontmatter-stripped and the offset is not recoverable
    # from it. If that read fails, the offset stays None and the L is dropped
    # rather than guessed; see `_section_list`.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            offset = _body_line_offset(fh.read())
    except OSError:
        offset = None
    listed, deeper, deepest = _section_list(body, offset, depth)
    if not listed and not deeper:
        # Two different empties, and neither may overstate. A page whose only
        # heading spans the whole body DOES have a heading — it was skipped on
        # purpose — so "no headings" is false there; and "nothing below its
        # title" is false in turn on a page whose one heading is not a title.
        # So name the RULE: it holds in both shapes, it explains why the list is
        # empty, and it tells the caller what to do instead — ask without a
        # section. A wiki that misreports its own shape is the thing this work
        # exists to stop.
        return ["", "_(this page has no headings)_" if not _headings(body)
                else "_(no section here is smaller than the whole page)_"]
    block = [""]
    if listed:
        block.append("sections:")
        block += listed
    if deeper:
        block.append("%d deeper heading(s) not listed — pass depth: %d to see them"
                     % (deeper, deepest))
    if listed:
        # The escape hatch FROM this list. Some of the slices it offers are tens
        # of thousands of chars on this wiki, and a caller that can only ask for
        # a whole section is back to the all-or-nothing choice the block exists
        # to remove -- it can now see the size, but not act on it. Advertised
        # unconditionally and only where L values exist to name: an
        # only-when-big rule would be a threshold with nothing to tune it
        # against, and [D66] holds that an escape hatch the caller cannot
        # discover does not exist.
        block.append("pass from: <L> and lines: <n> for a line window inside any "
                     "slice above")
    return block


# Window height when the caller names a `from` line but no `lines`. A caller
# knob's default, not a calibrated threshold -- there is nothing in the corpus to
# tune it against, and since the window header states how many lines lie outside
# it, a default that is too small costs one more call and never misleads.
DEFAULT_WINDOW_LINES = 40


def _window_int(params: dict, key: str) -> Optional[int]:
    """A 1-based line coordinate from `params`, or None when it is absent.

    Loud on everything else, unlike `depth` two callers down, and the difference
    is not style: a wrong depth shows FEWER headings, a wrong `from` shows the
    WRONG TEXT under a number the caller did not choose. Nothing in the rendered
    answer could reveal that substitution, so there is no safe default to fall
    back to -- only a refusal.

    bool is rejected before int() can see it, because `int(True) == 1` and a line
    coordinate is never spelled `true`. Same rule mcp-git's positional layer had
    to learn in `53894ea`, arrived at from the same direction: the boolean is a
    plausible-looking value that silently means something else.
    """
    raw = params.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError("get_page %s is a file line, not a flag; got %r" % (key, raw))
    try:
        # Via str() on purpose, so 3.7 and [3] raise instead of quietly becoming
        # 3 -- int() truncates a float and would answer a nonexistent request.
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("get_page %s must be an integer file line, got %r"
                         % (key, raw))
    if value < 1:
        raise ValueError("get_page %s counts from 1 (the file's first line), got %d"
                         % (key, value))
    return value


def _file_line_window(text: str, start: int, count: int) -> List[str]:
    """`count` lines from file line `start`, headed by what lies OUTSIDE them.

    FILE lines, deliberately: the section index prints `L<file line>`, and this
    is the call a caller makes with the number it read there. A window numbered
    in body coordinates would look identical from the outside, so the two systems
    would be indistinguishable in the one place it matters -- hence the RAW file
    text here, frontmatter included, not the frontmatter-stripped body.

    The header states the lines BEFORE and AFTER the window, not merely its own
    range, because that is the half the caller cannot compute: it does not know
    the file's height until something tells it, and `107 after` is the whole
    difference between asking again and stopping. `of N lines` follows the
    truncation marker's contract -- state the real total, never the parameter.
    """
    all_lines = text.splitlines()
    total = len(all_lines)
    if start > total:
        # No window rather than an empty one: a blank slice would read as "this
        # part of the page is empty", which is a different claim than "that line
        # is not in this file". The real height is what makes the next ask right.
        return ["", "_(no line %d — the file has %d line(s))_" % (start, total)]
    end = min(total, start + count - 1)
    return ["", "@@ L%d-L%d of %d lines — %d before, %d after @@"
            % (start, end, total, start - 1, total - end), ""] \
        + all_lines[start - 1:end]


# ---------------------------------------------------------------------------
# The page scope — ONE rule, read by `search`, `list` and `freshness`
#
# All three take `path_prefix`, and all three used to spell the filter as a bare
# `relpath.startswith(prefix)` copied from whichever of them was written first.
# That is not a path filter, it is a substring filter that happens to be anchored
# at the left: `sub` selected the whole of `subsystems/`, and any future
# directory sharing a leading substring with another would have been silently
# folded into it. This repo has already paid for that exact shape once, in
# `tests/_harness.py:_is_scratch_dir`, where a `startswith` on path names ate
# `.gitignore` because it starts with `.git`; the repair there was to match whole
# path COMPONENTS, and the comment above it ends "Do not reintroduce it."
#
# It is written once here rather than corrected in three places because the three
# disagreeing about what a prefix means is a worse defect than the one it repairs
# (adr 0018) -- a caller who learns a spelling from `list` must get the same set
# from `freshness`.
# ---------------------------------------------------------------------------

def _scope_prefix(value) -> str:
    """The docs-relative scope a caller asked for, normalized to a string.

    A prefix that is not a string is a request that matches nothing, which every
    one of the three answers can SAY -- where `startswith` would raise a
    TypeError and turn a harmless `path_prefix: 5` into a server error.
    """
    return str(value) if value else ""


def _path_prefix_matches(relpath: str, prefix) -> bool:
    """Does `relpath` (docs-relative, `/`-separated) fall inside `prefix`?

    Four clauses, and each one is a decision rather than a leftover:

    1. EXACT -- `relpath == prefix` matches. A whole page path is a legal scope
       selecting the one page it names, which is what makes the `path` alias on
       all three functions honest: a search hit prints `subsystems/scripts.md`,
       and sending that back must select that page.
    2. COMPONENT BOUNDARY -- a prefix ending at a `/` boundary matches everything
       beneath it, at any depth. `adr`, `adr/` and `subsystems/` are therefore
       all directory scopes, and a caller need not learn which spelling this
       server prefers.
    3. INSIDE THE FINAL COMPONENT -- a prefix whose remainder carries no `/`
       still matches, so `adr/001` selects the 0010-0019 records. That spelling
       is real and useful, and it is the reason the rule is not simply "match
       whole components": a numbered-record wiki is browsed by number stem.
    4. INSIDE A NON-FINAL COMPONENT -- everything else is refused. `sub` leaves
       the remainder `systems/x.md`, which crosses a boundary, so it selects
       NOTHING rather than the whole of `subsystems/`. This is the clause the
       `.gitignore`/`.git` precedent buys: a name is not a path.

    An empty prefix is no scope at all and admits every page, so the three call
    sites need no `if prefix and ...` guard of their own.
    """
    if not prefix:
        return True
    if not relpath.startswith(prefix):
        return False
    remainder = relpath[len(prefix):]
    if not remainder:
        return True                                  # 1: exact
    if prefix.endswith("/") or remainder.startswith("/"):
        return True                                  # 2: at a boundary
    return "/" not in remainder                      # 3 if inside the last
    #                                                # component, else 4


def _corpus_scopes(relpaths) -> List[str]:
    """The scopes that DO exist, built from the corpus rather than typed.

    Every top-level directory (with its trailing `/`, the spelling clause 2 makes
    unambiguous) plus every page sitting at the wiki root. Deriving it from the
    pages the walk just yielded is the whole point: a suggestion list written by
    hand goes stale the first time a directory is added, and a refusal that names
    a scope which no longer exists is worse than one that names none.

    Listing every page path would be noise on a corpus of any size -- the
    top-level scopes are few, and each one is a value the caller can send back
    verbatim.
    """
    scopes = set()
    for relpath in relpaths:
        head, sep, _tail = relpath.partition("/")
        scopes.add(head + "/" if sep else head)
    return sorted(scopes)


NO_SCOPE_SENTINEL = "NOTHING was selected"


def _no_scope_lines(prefix: str, scopes, claim: str) -> List[str]:
    """The shared body of the three empty-selection refusals.

    One condition, one wording: whichever function the caller reached, a prefix
    that admits no page is the same mistake with the same next move, so the
    answer states the boundary rule that refused it and then names the scopes
    that would have worked.

    `claim` is the one thing each function's empty answer would otherwise be
    read as -- `freshness` renders `gating: 0`, which over a set nobody looked
    at says nothing is stale; `search` renders a silence, which says the wiki
    has no answer. Same refusal, and each one denies its own false reading.
    """
    return ["no page is inside %r — %s, which is not the same as %s."
            % (prefix, NO_SCOPE_SENTINEL, claim),
            "",
            "A prefix is matched against the docs-relative path and must end "
            "either at a `/` boundary or inside the LAST component: "
            "`adr`, `adr/` and a whole page path all select; `adr/001` selects "
            "the records whose names start that way; `sub` selects nothing, "
            "because `subsystems/` is not a page called `sub`.",
            "",
            ("scopes that exist: %s" % ", ".join(scopes)) if scopes
            else "this wiki root holds no page at all."]


# ---------------------------------------------------------------------------
# Measured regions — a page block RENDERED from a command, never typed
#
# The measured shape of this wiki is what asks for this. Over its last twelve
# documentation commits 70.5% of the work-items (31 of 44) were re-measurement
# or stamp/INDEX bookkeeping and 11.4% (5 of 44) were new reasoning; the corpus
# carries 352 typed tree-measurements across 7,569 lines of prose — one every
# ~21 lines — and of the ten densest pages SIX cite numbers a script or a suite
# in this repo already computes. A number a human re-types is a number that
# rots. A number a command prints cannot. So a page may carry a block whose
# body is rendered rather than written:
#
#     <!-- BEGIN MEASURED: mcp-server-roster -->
#     Scripts/mcp-clangd.py
#     ...
#     <!-- END MEASURED: 3f9a1c2b7d40 -->
#
# HTML comments, so a rendered page shows nothing at all.
#
# The contract is `Scripts/amalgamate.py`'s, deliberately and line for line:
# the same marker pair, the same 12-char SHA-256 over the EMITTED BODY ALONE
# (markers excluded), the same three states — ok / stale / hand-edited — a hand
# edit REFUSED rather than silently overwritten, and a BEGIN without an END a
# hard error and never a skip. It is re-stated here instead of imported because
# the two differ on the one property that cannot be shared (see INERTNESS), and
# because `amalgamate.py` is a CLI over Python source while this is a handler
# over Markdown; an abstraction spanning both would have to re-declare every
# difference at run time anyway. `docs/components/generated-regions.md` is the
# WHY of the original, and it is the WHY of this one too.
#
# INERTNESS — the question Markdown cannot answer the way Python does.
# `amalgamate.py` finds its markers with `tokenize`, so a marker quoted inside
# a docstring is a STRING token and is structurally invisible; that file's own
# module docstring carries a full BEGIN/END pair for exactly that reason.
# Markdown has no tokenizer and no grammar this server may assume. The property
# is therefore obtained from two mechanical rules instead, stated here beside
# the code that implements them:
#
#   1. A MARKER IS THE WHOLE LINE, AT COLUMN 0. Nothing may precede it and
#      nothing but trailing blanks may follow. That one rule makes every inline
#      mention inert: a marker written in prose, a marker inside a `code span`,
#      a marker indented into a four-space code block, a marker nested under a
#      list item — none of them is a marker. It costs a feature the Python
#      generator has: a measured region cannot sit inside a list item, because
#      the indentation that would place it is the same indentation that starts
#      an indented code block, and a scanner cannot tell the two apart. The
#      trade is deliberate. On a documentation page inertness is worth more
#      than nesting.
#   2. A FENCED CODE BLOCK IS INERT. ``` and ~~~ fences are tracked while
#      scanning — CommonMark's rules: an opening fence indented at most three
#      spaces, a closing fence of the same character and at least as long with
#      an empty info string, an unterminated fence running to the end of the
#      document — and every line inside one is skipped. This is what lets a
#      page DOCUMENT the mechanism: quote the marker pair inside a fence and
#      nothing is ever written into the example.
#
# DECLARED LIMITATION, because an undefined limitation is worse than a stated
# one: HTML comments DO NOT NEST, so a marker sitting at column 0 inside
# another multi-line `<!-- ... -->` block is NOT inert. The first `-->` has
# already closed the outer comment as far as any HTML parser is concerned, and
# this scanner agrees with it rather than inventing a nesting rule the format
# does not have. Nothing else in Markdown can hide a column-0 line from rule 2.
#
# A NEAR MISS IS LOUD. A line that starts `<!-- BEGIN MEASURED` or
# `<!-- END MEASURED` at column 0 and does not match the one spelling is a hard
# error naming the line, never a silent skip. A marker that quietly stops being
# a marker is the entire failure this mechanism exists to prevent, and a typo
# inside an HTML comment is invisible in every rendered view of the page.
#
# TRUST BOUNDARY — rendering a region RUNS A COMMAND OUT OF A REPO FILE.
# `measurements.json` is a checked-in file that names argv, so rendering is
# execution, and execution is reachable from exactly two functions a caller has
# to ask for by name — `measure` and `verify` — and from nowhere else. The
# boundary is STRUCTURAL rather than a convention nobody enforces, in three
# layers that each fail closed:
#
#   * ONE SPAWN SITE. `_measure_run` is the only place in this file that starts
#     a child process for a command a repo file named. (`git()` is the file's
#     other spawn site and it runs a fixed argv written here, never one the
#     corpus supplies.)
#   * A CAPABILITY ARGUMENT WITH NO DEFAULT. `_measure_run` refuses anything
#     but an `_ExecutionGrant`, and `_ExecutionGrant` refuses to exist for a
#     function outside MEASURE_GRANTED_FUNCTIONS. A caller cannot reach the
#     spawn by forgetting a parameter, because the parameter is required and
#     the value cannot be improvised.
#   * THE READ PATHS NEVER HOLD A RENDERER. `search`, `source_to_pages`,
#     `get_page`, `list`, `freshness`, `reindex` and `stats` never construct a
#     grant, and — the part that makes this checkable instead of asserted —
#     nothing they call does either. `verify_analyze`, which `freshness` DOES
#     call, takes the rendered bodies as a plain `{name: str}` dict and has no
#     way to produce one; handed None it reports only the region states it can
#     prove WITHOUT running anything (hand-edited, unregistered, malformed) and
#     says so in the answer. The wiki suite walks the call graph of every read
#     handler over this file's AST and fails if `_measure_run` is reachable —
#     which is a claim about the code's shape, not about today's call order.
# ---------------------------------------------------------------------------

MEASUREMENTS_FILE = "measurements.json"
MEASURED_DIGEST_LEN = 12
MEASURED_BEGIN_HEAD = "<!-- BEGIN MEASURED:"
MEASURED_END_HEAD = "<!-- END MEASURED:"

# Wall-clock ceiling on ONE measurement command. Deliberately far above
# GIT_TIMEOUT_SEC: a measurement is allowed to be a whole test suite or a tree
# walk, where every git call in this file is a single local query. What it is
# NOT allowed to be is unbounded — `subprocess.run` with no timeout waits
# forever, and forever is not a duration a server may spend inside a request.
MEASURE_TIMEOUT_SEC = 120

# The two functions that may turn a registry NAME into a child process. Written
# as a set rather than checked at each call site so the grant itself can refuse,
# which is what keeps the rule in one place; see the TRUST BOUNDARY note above.
MEASURE_GRANTED_FUNCTIONS = frozenset(("measure", "verify"))

# Every state one measured region can be in. The first three are
# `amalgamate.py`'s; the last three are what a Markdown corpus adds — a name
# with no registry entry, a command that could not answer, and a region nobody
# re-rendered because the caller did not authorize execution.
MEASURED_STATES = ("ok", "stale", "hand-edited", "unregistered", "failed",
                   "not-rendered")
# The subset that GATES. `not-rendered` is deliberately out: it means "this was
# not checked", which is the one thing a freshness report must never dress up
# as either a pass or a defect.
MEASURED_GATING_STATES = ("stale", "hand-edited", "unregistered", "failed")

_MEASURED_LOOSE_RE = re.compile(r"^<!--\s*(?:BEGIN|END) MEASURED\b")
_MEASURED_BEGIN_RE = re.compile(
    r"^<!-- BEGIN MEASURED: (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*) -->[ \t]*$")
_MEASURED_END_RE = re.compile(
    r"^<!-- END MEASURED: (?P<digest>[0-9a-f]*) -->[ \t]*$")
_FENCE_RE = re.compile(r"^(?P<mark>`{3,}|~{3,})(?P<info>.*)$")


class MeasuredRegionError(ValueError):
    """A page's measured markers cannot be read as a region pair.

    A ValueError subclass on purpose: `handle_wiki_call` already turns a
    ValueError into a clean tool error, so a malformed marker reaches the
    caller as a sentence naming the page and the line instead of a traceback.
    """


def measured_digest(body: str) -> str:
    """The recorded hash: SHA-256 over the emitted body ALONE, first 12 chars.

    Identical rule to `amalgamate.py:body_hash`, and the markers are excluded
    for the same reason: the hash answers "did a human edit inside the region",
    and a hash covering its own END line could never be written.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:MEASURED_DIGEST_LEN]


def _fenced_line_indices(lines: List[str]) -> set:
    """The 0-based indices of every line inside a fenced code block.

    Inertness rule 2, implemented. CommonMark, as far as a documentation page
    can use it: an opening fence is three or more backticks or tildes indented
    at most three spaces; a backtick fence's info string may not itself contain
    a backtick; a closing fence is the same character, at least as long, and
    carries no info string; an UNTERMINATED fence runs to the end of the
    document. That last clause is a real consequence and it is the right one —
    a stray fence makes the rest of the page inert rather than making a marker
    inside the broken block live.

    The fence lines themselves are included in the set, so a marker that IS the
    fence line (it cannot be, but the set is also read by nothing else) never
    slips through on an off-by-one.
    """
    inside = set()
    fence_char = ""
    fence_len = 0
    for idx, raw in enumerate(lines):
        stripped = raw.rstrip("\n")
        token = stripped.lstrip(" ")
        indented = len(stripped) - len(token)
        match = _FENCE_RE.match(token) if indented <= 3 else None
        mark = match.group("mark") if match else ""
        info = match.group("info") if match else ""
        if mark and mark[0] == "`" and "`" in info:
            match, mark = None, ""      # not a fence: info strings may not
        if not fence_char:              # carry the fence character
            if mark:
                fence_char, fence_len = mark[0], len(mark)
                inside.add(idx)
            continue
        inside.add(idx)
        if mark and mark[0] == fence_char and len(mark) >= fence_len \
                and not info.strip():
            fence_char, fence_len = "", 0
    return inside


def measured_regions(label: str, text: str) -> List[dict]:
    """Locate every measured region in *text*, or raise naming the line.

    *label* is what a refusal calls the document — the docs-relative path at
    every real call site. Split from any file reading so the scanner can be
    exercised on a synthetic string: this repo's negative-control rule wants a
    checker proven against mutated inputs, and a checker that needs a scratch
    directory to prove itself has a second failure mode of its own.

    Three refusals, and each is a state that must never be reachable silently:
    a BEGIN inside an open region, an END with no BEGIN, and a BEGIN whose END
    never arrives. The last one is the important one — a region that quietly
    stops being maintained is the whole failure this mechanism prevents.
    """
    lines = text.splitlines(keepends=True)
    fenced = _fenced_line_indices(lines)
    regions: List[dict] = []
    open_at: Optional[int] = None
    open_name = ""
    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n")
        # Inertness rule 1: column 0, whole line. A leading space, a backtick,
        # a `- ` bullet or any prose before the marker and it is not one.
        if not line.startswith("<!--") or not _MEASURED_LOOSE_RE.match(line):
            continue
        if idx in fenced:
            continue                    # Inertness rule 2: inside a fence
        begin = _MEASURED_BEGIN_RE.match(line)
        end = None if begin else _MEASURED_END_RE.match(line)
        if begin is None and end is None:
            raise MeasuredRegionError(
                "%s:%d: a measured marker this server cannot read: %r. The one "
                "spelling is '%s <name> -->' / '%s <digest> -->', at column 0, "
                "one space around each field, the name matching "
                "[A-Za-z0-9][A-Za-z0-9._-]*. A marker that is ALMOST right is "
                "refused rather than skipped: a skipped one is a region nobody "
                "updates again."
                % (label, idx + 1, line, MEASURED_BEGIN_HEAD, MEASURED_END_HEAD))
        if begin is not None:
            if open_at is not None:
                raise MeasuredRegionError(
                    "%s:%d: BEGIN MEASURED inside the region opened at line %d"
                    % (label, idx + 1, open_at + 1))
            open_at, open_name = idx, begin.group("name")
            continue
        if open_at is None:
            raise MeasuredRegionError(
                "%s:%d: END MEASURED without a BEGIN" % (label, idx + 1))
        regions.append({
            "name": open_name,
            "begin": open_at,
            "end": idx,
            "recorded": end.group("digest"),
            "body": "".join(lines[open_at + 1:idx]),
        })
        open_at = None
    if open_at is not None:
        raise MeasuredRegionError(
            "%s:%d: BEGIN MEASURED %r without an END — the region is open, so "
            "everything below it would be swallowed by the next render"
            % (label, open_at + 1, open_name))
    return regions


def measured_state(region: dict, rendered: Optional[str]) -> str:
    """ok / stale / hand-edited / not-rendered for ONE region.

    The first three are `amalgamate.py:Region.state`, restated rather than
    re-derived so the two stay comparable:

      * the recorded digest matches the body AND the body matches what the
        command produced -> `ok`;
      * a recorded digest that does NOT match the body means a human typed
        inside the region -> `hand-edited`, which is REFUSED, never overwritten;
      * anything else -> `stale`.

    `not-rendered` is what a Markdown corpus adds and it is the honest answer to
    the trust boundary: when nobody authorized execution, a region whose digest
    still matches its body is UNCHECKED, not clean. Saying `ok` there would be
    a claim about a command that was never run.

    An EMPTY recorded digest classifies `stale` without rendering anything, and
    that is provable rather than assumed: the field has never been written, so
    nothing has ever rendered this region.
    """
    recorded = region["recorded"]
    body = region["body"]
    if not recorded:
        return "stale"
    if recorded != measured_digest(body):
        return "hand-edited"
    if rendered is None:
        return "not-rendered"
    return "ok" if body == rendered else "stale"


def measured_region_state(region: dict, registry: Optional[dict],
                          rendered: Optional[dict],
                          errors: Optional[dict]) -> str:
    """`measured_state` plus the two conditions that precede a render.

    A region naming a measurement the registry does not define can never be
    rendered at all, and a command that could not answer did not produce a body
    to compare against — both are defects of the mechanism rather than states
    of the text, so they are reported as themselves instead of collapsing into
    `stale`, which a caller would try to fix by re-running.
    """
    name = region["name"]
    if registry is not None and name not in registry:
        return "unregistered"
    if errors and errors.get(name):
        return "failed"
    return measured_state(region, (rendered or {}).get(name))


def load_measurements(abs_root: str) -> Dict[str, dict]:
    """Read `<wiki root>/measurements.json` — the name -> command registry.

    JSON and not TOML on purpose: `tomllib` is 3.11+ and this fleet treats it as
    optional, while `json` is already imported here and needs nothing. The file
    is the portable half of this mechanism — another repo inheriting it should
    have to learn one boring format, not a dependency.

    Shape:

        {"version": 1,
         "measurements": {
           "<name>": {"description": "what the number MEANS",
                      "command": ["git", "ls-files", "--", "Scripts/mcp-*.py"]}}}

    `description` is required rather than optional, and that is the one rule
    here that is not plumbing: a rendered block with no sentence saying what it
    counts is a number nobody can check, which is the defect this mechanism
    exists to remove rather than automate.

    An ABSENT file is not an error — a wiki with no measured region needs no
    registry. A malformed one is, because the alternative is a region silently
    reported `unregistered` for a reason that has nothing to do with the page.
    """
    path = os.path.join(abs_root, MEASUREMENTS_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("%s cannot be read: %s" % (MEASUREMENTS_FILE, exc))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Deliberately NOT `_json_error_window`. That helper exists because a
        # character offset ("char 1530") is unactionable to a caller who built
        # the string on the wire and cannot count to it. This one is a FILE the
        # reader can open, and `JSONDecodeError` already carries `line N
        # column M` -- the coordinates an editor understands. Quoting bytes back
        # would be the worse answer AND a second wording of a sentence the fleet
        # keeps identical in sixteen places.
        raise ValueError(
            "%s is not valid JSON: %s (line %d, column %d)"
            % (MEASUREMENTS_FILE, exc.msg, exc.lineno, exc.colno))
    entries = data.get("measurements") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise ValueError(
            "%s must be an object carrying a 'measurements' object mapping a "
            "name to {'description': str, 'command': [argv]}" % MEASUREMENTS_FILE)
    registry: Dict[str, dict] = {}
    for name, entry in entries.items():
        where = "%s: measurement %r" % (MEASUREMENTS_FILE, name)
        if not isinstance(entry, dict):
            raise ValueError("%s is not an object" % where)
        command = entry.get("command")
        if (not isinstance(command, list) or not command
                or not all(isinstance(arg, str) for arg in command)):
            raise ValueError(
                "%s has no 'command': it must be a non-empty argv list of "
                "strings, run with shell=False — a string would need a shell to "
                "split it, and a shell is a second interpreter nobody audited"
                % where)
        description = str(entry.get("description") or "").strip()
        if not description:
            raise ValueError(
                "%s has no 'description': one line saying what the number MEANS. "
                "A rendered block nobody can read is the defect being removed, "
                "not the one being automated" % where)
        registry[str(name)] = {"command": list(command),
                               "description": description}
    return registry


def load_measurements_safe(abs_root: str) -> Tuple[Dict[str, dict], str]:
    """`load_measurements` for a caller that must report rather than fail.

    `freshness` walks the whole corpus and publishes counts; a malformed
    registry there is a finding about the wiki, not a reason to refuse the
    report. `measure` keeps the raising form: it is about to WRITE, and it may
    not write against a file it could not read.
    """
    try:
        return load_measurements(abs_root), ""
    except ValueError as exc:
        return {}, str(exc)


class _ExecutionGrant:
    """The capability that turns a registry name into a child process.

    There is no default value for it anywhere, and `_measure_run` accepts
    nothing else, so the ability to execute is carried explicitly from the one
    handler that asked for it down to the one function that spawns. A read
    handler cannot reach the spawn by omission — the parameter is required and
    its value cannot be improvised.

    The constructor refuses a function outside MEASURE_GRANTED_FUNCTIONS, so
    widening the boundary is an edit to that one frozenset rather than a new
    call site nobody reviews.
    """

    __slots__ = ("function",)

    def __init__(self, function: str):
        if function not in MEASURE_GRANTED_FUNCTIONS:
            raise ValueError(
                "rendering a measured region runs a command named by a file in "
                "the repo, so it is reachable only from %s; %r is not one of "
                "them" % (", ".join(sorted(MEASURE_GRANTED_FUNCTIONS)),
                          function))
        self.function = function


def _measure_run(name: str, entry: dict, repo: str,
                 grant: "_ExecutionGrant") -> Tuple[Optional[str], str]:
    """Run ONE measurement command; return (stdout, error).

    THE only place in this file that starts a child for an argv the corpus
    named. Every knob is the conservative one and each is a decision:

      * `shell=False` (the default, kept by passing a list) — a shell is a
        second interpreter nobody audited, and a registry string would need one
        to be split at all.
      * `cwd=repo` — a measurement is about the repository, so it runs where
        the repository is, whatever directory the server was started in.
      * `stdin=DEVNULL` — this server's stdin is the JSON-RPC stream. A command
        that read it would eat protocol messages and desync the session, which
        is the same fix `git()` above carries and for the same reason.
      * an explicit timeout — see MEASURE_TIMEOUT_SEC.

    A failure is RETURNED, never raised: one broken entry must not blind a
    corpus-wide report about the other regions, and the caller renders the
    reason beside the region it belongs to.
    """
    if not isinstance(grant, _ExecutionGrant):
        raise TypeError(
            "_measure_run needs an _ExecutionGrant: rendering a measured region "
            "executes a command named by a repo file, and that capability is "
            "passed explicitly from the handler the caller asked for")
    argv = list(entry["command"])
    try:
        proc = subprocess.run(
            argv, cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=MEASURE_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        return None, "no such command: %s" % argv[0]
    except PermissionError:
        return None, "not executable: %s" % argv[0]
    except OSError as exc:
        return None, "cannot run %s: %s" % (argv[0], exc)
    except subprocess.TimeoutExpired:
        return None, ("timed out after %ds — a measurement is allowed to be "
                      "slow, not unbounded" % MEASURE_TIMEOUT_SEC)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, "exit %d%s" % (proc.returncode,
                                    (": " + detail[-1]) if detail else "")
    return _measure_normalize(proc.stdout), ""


def _measure_normalize(out: str) -> str:
    """The emitted body for a command's stdout: no trailing blank line runs.

    The body is what sits BETWEEN the markers, so it either ends in exactly one
    newline or is empty. Normalizing here rather than at the write site means
    the digest, the comparison and the file all see the same bytes — a
    normalization applied on only one of the three is how a region reports
    `stale` forever while its body is already correct.
    """
    trimmed = out.rstrip("\n")
    return (trimmed + "\n") if trimmed else ""


def _measure_bodies(registry: Dict[str, dict], repo: str,
                    grant: "_ExecutionGrant",
                    names=None) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Render the named measurements; return (bodies, errors), both by name.

    `names` defaults to every registry entry. Callers pass the names the corpus
    actually USES, so a registry entry no page references costs no subprocess.
    """
    wanted = sorted(registry) if names is None else sorted(set(names))
    bodies: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for name in wanted:
        entry = registry.get(name)
        if entry is None:
            continue                    # classified `unregistered`, not run
        body, error = _measure_run(name, entry, repo, grant)
        if error:
            errors[name] = error
        else:
            bodies[name] = body
    return bodies, errors


def _page_text(abs_root: str, relpath: str) -> str:
    """One page's WHOLE file text, frontmatter included.

    `read_page` hands back the body alone, which is the right unit for search
    and for anchors. A measured region is located by FILE line index, because
    that is the coordinate the writer has to slice with, so this reads the file
    rather than re-deriving an offset that `_body_line_offset` already proves
    is easy to get wrong.
    """
    with open(os.path.join(abs_root, relpath), "r", encoding="utf-8") as fh:
        return fh.read()


def measure_scan(abs_root: str, path_prefix=None) -> Tuple[List[dict], List[dict]]:
    """Every measured region in the corpus, unrendered, plus the unreadable pages.

    Returns (rows, malformed). A row carries the region dict plus the page it
    came from; `malformed` carries one entry per page whose markers could not be
    read, because ONE broken page must not hide the state of every other.
    """
    prefix = _scope_prefix(path_prefix)
    rows: List[dict] = []
    malformed: List[dict] = []
    for relpath, _fm, _body in iter_pages(abs_root):
        if not _path_prefix_matches(relpath, prefix):
            continue
        try:
            regions = measured_regions(relpath, _page_text(abs_root, relpath))
        except MeasuredRegionError as exc:
            malformed.append({"path": relpath, "error": str(exc)})
            continue
        except OSError as exc:
            malformed.append({"path": relpath,
                              "error": "cannot be read: %s" % exc})
            continue
        for region in regions:
            row = dict(region)
            row["path"] = relpath
            rows.append(row)
    return rows, malformed


def measure_apply(abs_root: str, relpath: str, rows: List[dict]) -> None:
    """Rewrite one page, replacing each region's body and its END digest.

    Back to front, so an earlier region's line indices stay valid — the same
    ordering `amalgamate.py:apply_regions` uses and for the same reason.
    """
    text = _page_text(abs_root, relpath)
    lines = text.splitlines(keepends=True)
    for row in sorted(rows, key=lambda r: r["begin"], reverse=True):
        body = row["rendered"]
        lines[row["begin"] + 1:row["end"] + 1] = [
            body,
            "%s %s -->\n" % (MEASURED_END_HEAD, measured_digest(body)),
        ]
    with open(os.path.join(abs_root, relpath), "w", encoding="utf-8") as fh:
        fh.write("".join(lines))


# ---------------------------------------------------------------------------
# `verify` — the anchors, resolved by a script instead of by hand
#
# A page's factual claims carry anchors (p:wiki schema §4): `path` or
# `path:symbol`, repo-root-relative, in the frontmatter `sources:` list and
# inline in the body beside the sentence that depends on them. Re-checking them
# is a job a human currently does one anchor at a time — and the corpus holds
# 689 body spans shaped like one, so doing it by hand is the bookkeeping this
# whole line of work is removing.
#
# WHAT THE MATCHER CAN AND CANNOT PROVE, declared rather than implied. This is
# a stdlib-only text matcher and it is deliberately the WEAK half of the pair:
# the fleet's language servers (purity_call's clangd and luals backends) are the
# real resolver, and the schema already says symbol-level verification belongs
# to them. So this one errs toward NOT reporting a defect — in a report a human
# acts on, a false BROKEN costs more than a missed one.
#
#   * DECLARED vs DISCOVERED, and it is the split that decides everything else.
#     A `sources:` entry is a DECLARATION: the page says "this is what I am
#     about", so every entry is checked exactly as written and a path that is
#     not there is a defect. A body span is DISCOVERED — the matcher has to
#     decide whether a backticked string is an anchor at all — so it is held to
#     the schema's own definition: an anchor is repo-root-relative, therefore
#     its FIRST PATH COMPONENT must be a real entry at the repo root. Measured
#     on this corpus, that one rule is the difference between 30 findings and 2:
#     `subsystems/`, `adr/`, `_lib/`, `hooks/`, `sandbox-run/` are wiki- or
#     skill-relative names used in prose; `src/deep/`, `cwd/.git`, `a/tests/`,
#     `other/foo.py` are worked examples inside an argument; and
#     `lite.duckduckgo.com/lite/` is a URL. None of them is an anchor and all of
#     them would have been reported as one.
#   * A body anchor must carry a `/`. A backticked bare word is overwhelmingly
#     an identifier, and a bare filename is ambiguous against the repo root.
#   * A span carrying anything outside [A-Za-z0-9_./:+-], or starting with `~`,
#     `/` or `.`, or carrying `..` anywhere, is not a candidate: a glob
#     (`Scripts/mcp-*.py`), a home path (`~/.claude`), an absolute path, a
#     parent traversal and an ELIDED one (`docs/adr/0003-...`) each name
#     something that is not a file in this tree.
#   * A `path:<digits>` or `path:<digits>-<digits>` tail is a LINE REFERENCE,
#     not a symbol. The file is resolved and the range is not — a line number is
#     exactly the kind of anchor this mechanism exists to stop trusting.
#   * For a `.py` file a symbol resolves on a def / class / single-target
#     assignment at any indentation. For a `.md` file, on a heading carrying it.
#   * For EVERY OTHER extension the fallback is a whole-word occurrence, which
#     proves the file MENTIONS the symbol and NOT that it defines it. Those are
#     counted separately and reported as `by mention only`, so the report never
#     silently converts a weak pass into a strong one.
# ---------------------------------------------------------------------------

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_ANCHOR_SHAPE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./:+-]*$")
_LINE_REF_RE = re.compile(r"^\d+(?:-\d+)?$")


def _anchor_shaped(span: str, need_slash: bool = True) -> bool:
    """Is this code span SHAPED like a repo-relative anchor? See the notes above."""
    span = span.strip()
    if not span or ".." in span or not _ANCHOR_SHAPE_RE.match(span):
        return False
    if need_slash and "/" not in span:
        return False
    head = span.split(":", 1)[0]
    if head.endswith("/"):
        return True
    return "." in head.rsplit("/", 1)[-1]


def _repo_top_level(repo: str) -> set:
    """The names at the repo root — the vocabulary a body anchor may start with.

    Read off the filesystem rather than typed, because a hand-written list is
    wrong the first time a directory is added, and its failure mode here is the
    worst one available: a real anchor silently demoted to prose.
    """
    try:
        return set(os.listdir(repo))
    except OSError:
        return set()


def _anchor_candidates(body: str, top_level: set) -> List[str]:
    """Every inline code span in a page body that is an anchor.

    `top_level` is the repo root's own entry names — see the DECLARED vs
    DISCOVERED note above. A span whose first component is not one of them is
    not an anchor at all and is not counted as one: prose full of worked
    examples (`src/deep/`, `a/tests/foo.py`) and of wiki-relative directory
    names (`adr/`, `subsystems/`) would otherwise fill the report with findings
    about sentences nobody wrote as a claim about code.
    """
    out: List[str] = []
    for span in _CODE_SPAN_RE.findall(body):
        span = span.strip()
        if not _anchor_shaped(span) or span in out:
            continue
        if span.split("/", 1)[0] not in top_level:
            continue
        out.append(span)
    return out


def _symbol_defined(path: str, text: str, symbol: str) -> Tuple[bool, str]:
    """Does *text* define *symbol*, and by which rule? See the matcher notes."""
    word = re.escape(symbol)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ".pyi"):
        for pattern, rule in (
                (r"^[ \t]*(?:async[ \t]+)?def[ \t]+%s\b" % word, "def"),
                (r"^[ \t]*class[ \t]+%s\b" % word, "class"),
                (r"^[ \t]*%s[ \t]*(?::[^=\n]+)?=[^=]" % word, "assignment")):
            if re.search(pattern, text, re.M):
                return True, rule
        return False, "no def, class or assignment of %r" % symbol
    if ext in (".md", ".markdown"):
        if re.search(r"^#{1,6}[ \t]+.*%s" % word, text, re.M):
            return True, "heading"
        return False, "no heading carrying %r" % symbol
    if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % word, text):
        return True, "mention"
    return False, "%r does not occur in the file" % symbol


def _resolve_anchor(anchor: str, repo: str, cache: dict) -> Tuple[str, str]:
    """(kind, reason) for one anchor. `kind` is `ok`, `weak`, `line-ref`, or a
    failure name; `reason` is what the report prints beside a failure."""
    path = _source_path(anchor, repo)
    symbol = anchor[len(path):].lstrip(":") or None
    abs_path = os.path.join(repo, path)
    if not os.path.exists(abs_path):
        return "missing-path", "no such path in the repo"
    if symbol is None:
        return "ok", ""
    if _LINE_REF_RE.match(symbol):
        return "line-ref", ""
    if os.path.isdir(abs_path):
        return ("missing-symbol",
                "%s is a directory, so it carries no symbol %r" % (path, symbol))
    if path not in cache:
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                cache[path] = fh.read()
        except OSError as exc:
            cache[path] = None
            return "unreadable", "cannot be read: %s" % exc
    text = cache[path]
    if text is None:
        return "unreadable", "cannot be read"
    found, rule = _symbol_defined(path, text, symbol)
    if not found:
        return "missing-symbol", "%s: %s" % (path, rule)
    return ("weak" if rule == "mention" else "ok"), rule


def verify_analyze(root: str, registry=None, registry_error: str = "",
                   rendered=None, errors=None, path_prefix=None) -> dict:
    """Resolve every anchor and classify every measured region, page by page.

    `rendered` maps a measurement NAME to the body its command produced, and it
    is the ONLY way a `stale` measured region becomes visible here. This
    function cannot produce one — it takes strings — which is the structural
    half of the trust boundary: `freshness` calls it with None and gets the
    states that are provable without running anything.

    `path_prefix` reuses `_path_prefix_matches`, the one rule `search`, `list`
    and `freshness` already share. A second spelling of what a prefix means is
    the defect adr 0018 refused to create, and a fourth caller is not a reason
    to re-open it.
    """
    # A registry that could not be READ is one defect, named once. Classifying
    # against the empty dict it degrades to would instead report every region in
    # the corpus as `unregistered` -- N findings about pages nobody touched, for
    # a fault none of them has. `None` is the value `measured_region_state`
    # already reads as "nobody can say", so the regions fall back to what is
    # still provable about them (an empty digest is stale either way) and the
    # one real defect is counted by `verify_gating_count`.
    if registry_error:
        registry = None
    repo = _repo_root_cached(root)
    top_level = _repo_top_level(repo)
    prefix = _scope_prefix(path_prefix)
    cache: dict = {}
    seen: List[str] = []
    pages: List[dict] = []
    summary = {"pages": 0, "anchors": 0, "resolved": 0, "unresolved": 0,
               "weak": 0, "line_refs": 0, "regions": 0}
    for state in MEASURED_STATES:
        summary[state] = 0
    for relpath, fm, body in iter_pages(root):
        seen.append(relpath)
        if not _path_prefix_matches(relpath, prefix):
            continue
        summary["pages"] += 1
        row = {"path": relpath, "name": fm.get("name") or relpath,
               "broken": [], "regions": [], "malformed": ""}
        anchors = [("sources", str(a)) for a in as_list(fm.get("sources"))
                   if _anchor_shaped(str(a), need_slash=False)]
        anchors += [("body", a) for a in _anchor_candidates(body, top_level)]
        for where, anchor in anchors:
            summary["anchors"] += 1
            kind, reason = _resolve_anchor(anchor, repo, cache)
            if kind == "ok":
                summary["resolved"] += 1
            elif kind == "weak":
                summary["resolved"] += 1
                summary["weak"] += 1
            elif kind == "line-ref":
                summary["resolved"] += 1
                summary["line_refs"] += 1
            else:
                summary["unresolved"] += 1
                row["broken"].append({"anchor": anchor, "where": where,
                                      "kind": kind, "reason": reason})
        try:
            regions = measured_regions(relpath, _page_text(root, relpath))
        except MeasuredRegionError as exc:
            row["malformed"] = str(exc)
            regions = []
        except OSError as exc:
            row["malformed"] = "cannot be read: %s" % exc
            regions = []
        for region in regions:
            summary["regions"] += 1
            state = measured_region_state(region, registry, rendered, errors)
            summary[state] = summary.get(state, 0) + 1
            row["regions"].append({
                "name": region["name"], "state": state,
                "recorded": region["recorded"],
                "found": measured_digest(region["body"]),
                "error": (errors or {}).get(region["name"], "")})
        if (row["broken"] or row["malformed"]
                or any(r["state"] in MEASURED_GATING_STATES
                       for r in row["regions"])):
            pages.append(row)
    report = {"root": root, "repo": repo, "gating": pages, "summary": summary,
              "registry_error": registry_error, "rendered": rendered is not None}
    if prefix:
        report["path_prefix"] = prefix
        if not summary["pages"]:
            report["scopes"] = _corpus_scopes(seen)
    return report


def verify_gating_count(report) -> int:
    """How many things `verify` can PROVE are broken in this report.

    Pages plus, when it is unreadable, the registry itself: a `measurements.json`
    nothing can parse breaks every region in the corpus at once, so counting it
    as zero would publish a clean number over a mechanism that is not running.
    """
    report = report or {}
    return len(report.get("gating") or []) + (1 if report.get("registry_error")
                                              else 0)


def _verify_page_reasons(page) -> List[str]:
    """One human line per defect on one page, in the order they were found."""
    out = []
    if page.get("malformed"):
        out.append("measured markers: %s" % page["malformed"])
    for broken in page.get("broken", []):
        out.append("%s: %s — %s" % (broken["where"], broken["anchor"],
                                    broken["reason"] or broken["kind"]))
    for region in page.get("regions", []):
        if region["state"] not in MEASURED_GATING_STATES:
            continue
        detail = region.get("error") or ""
        if region["state"] == "hand-edited":
            detail = ("recorded %s, found %s; re-run measure with force: true "
                      "to discard the edit"
                      % (region["recorded"] or "—", region["found"]))
        elif region["state"] == "unregistered":
            detail = "%s defines no such measurement" % MEASUREMENTS_FILE
        elif region["state"] == "stale" and not detail:
            detail = "the rendered body is not what the page carries"
        out.append("measured region %s — %s: %s"
                   % (region["name"], region["state"], detail))
    return out


def verify_render(report, rel_root: str) -> str:
    """The `verify` answer. Not paged: it is an audit, and a cut audit misleads."""
    summary = report["summary"]
    prefix = report.get("path_prefix") or ""
    head = "# verify: %d page(s) in %s/" % (summary["pages"], rel_root)
    if prefix:
        head += " — path_prefix %r" % prefix
    if prefix and not summary["pages"]:
        return "\n".join([head, ""] + _no_scope_lines(
            prefix, report.get("scopes") or [],
            "the wiki having nothing broken")) + "\n"
    lines = [head, ""]
    if report.get("registry_error"):
        lines += ["registry (1):", "- %s" % report["registry_error"], ""]
    if report["gating"]:
        lines.append("gating pages (%d):" % len(report["gating"]))
        for page in report["gating"]:
            lines.append("- %s `%s`" % (page["name"], page["path"]))
            for reason in _verify_page_reasons(page):
                lines.append("    %s" % reason)
        lines.append("")
    extra = []
    if summary["weak"]:
        extra.append("%d by mention only" % summary["weak"])
    if summary["line_refs"]:
        extra.append("%d line reference(s)" % summary["line_refs"])
    lines.append("anchors: %d checked, %d resolved%s, %d unresolved"
                 % (summary["anchors"], summary["resolved"],
                    (" (%s)" % ", ".join(extra)) if extra else "",
                    summary["unresolved"]))
    region_bits = ["%d %s" % (summary[state], state) for state in MEASURED_STATES
                   if summary.get(state)]
    lines.append("measured regions: %d%s"
                 % (summary["regions"],
                    (" — " + ", ".join(region_bits)) if region_bits else ""))
    if not report["rendered"]:
        lines.append("  not re-rendered: rendering runs the commands "
                     "%s names, so it is opt-in — pass measure: true, or call "
                     "measure. Everything above is provable without executing "
                     "anything." % MEASUREMENTS_FILE)
    lines.append("%s%d" % (GATING_LINE_PREFIX, verify_gating_count(report)))
    lines += ["",
              "The symbol matcher is stdlib text: a .py symbol resolves on a "
              "def/class/assignment, a .md symbol on a heading, and every other "
              "extension falls back to a whole-word MENTION — which proves the "
              "file names the symbol, not that it defines it. Those are the "
              "`by mention only` count above. clangd and luals, via purity_call, "
              "are the real resolver; this one errs toward reporting nothing, "
              "because a false BROKEN costs a human more than a missed one.",
              "A frontmatter sources: entry is checked as WRITTEN. A body span "
              "is only treated as an anchor when its first path component is a "
              "real entry at the repo root, which is what the schema means by "
              "repo-root-relative — so wiki-relative directory names and worked "
              "examples in prose are not reported. The residue that rule cannot "
              "remove: an illustrative path under a REAL top-level directory is "
              "indistinguishable from a dead anchor by shape, and is reported."]
    return "\n".join(lines).rstrip() + "\n"


def measure_render(rows, malformed, registry, rel_root: str, wrote: bool,
                   forced: bool) -> str:
    """The `measure` answer: one line per region that is not `ok`.

    Modelled on `amalgamate.py`'s CLI — `CHANGED:` in check mode, `updated:` when
    it wrote, `HAND-EDITED:` for the edit it refuses. A clean check prints its
    counts and nothing else, which is the same verdict-by-silence that file's
    `--check` gives a hook.
    """
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    names = sorted({row["name"] for row in rows})
    head = ("# measure: %d region(s) over %d name(s) in %s/ — %s"
            % (len(rows), len(names), rel_root,
               "wrote" if wrote else "check only"))
    lines = [head, ""]
    for item in malformed:
        lines.append("MALFORMED: %s — %s" % (item["path"], item["error"]))
    for row in rows:
        label = "%s [%s]" % (row["path"], row["name"])
        state = row["state"]
        if state == "ok":
            continue
        if state == "hand-edited" and not forced:
            lines.append("HAND-EDITED: %s — recorded %s, found %s; re-run with "
                         "force: true to discard the edit"
                         % (label, row["recorded"] or "—", row["found"]))
        elif state == "unregistered":
            lines.append("UNREGISTERED: %s — %s defines no such measurement%s"
                         % (label, MEASUREMENTS_FILE,
                            ("; it defines: " + ", ".join(sorted(registry)))
                            if registry else " (the registry is empty)"))
        elif state == "failed":
            lines.append("FAILED: %s — %s" % (label, row["error"]))
        elif row.get("written"):
            lines.append("updated: %s — %s -> %s"
                         % (label, row["recorded"] or "—",
                            measured_digest(row["rendered"])))
        else:
            lines.append("CHANGED: %s — recorded %s, rendered %s; call measure "
                         "with write: true"
                         % (label, row["recorded"] or "—",
                            measured_digest(row["rendered"] or "")))
    if len(lines) > 2:
        lines.append("")
    # AS FOUND, and it says so: in write mode the states above were measured
    # BEFORE the write, so `1 stale` beside `updated:` is the finding this call
    # acted on, not a claim about the file that is now on disk. Re-run to see
    # the after; that is what makes the second run's `1 ok` mean something.
    lines.append("summary (as found): %s" % (", ".join(
        "%d %s" % (counts.get(state, 0), state) for state in MEASURED_STATES
        if counts.get(state)) or "no measured region under this scope"))
    for name in names:
        entry = registry.get(name)
        if entry:
            lines.append("  %s — %s" % (name, entry["description"]))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Freshness (ports p:wiki/scripts/freshness.py)
# ---------------------------------------------------------------------------

def _changed_files(commit: str, head: str, repo: str, cache: dict):
    """Files changed between `commit` and `head`, or None if unresolvable."""
    if commit in cache:
        val = cache[commit]
        return None if val == _INVALID else val
    code, out, _ = git(["diff", "--name-only", commit, head], cwd=repo)
    if code != 0:
        cache[commit] = _INVALID
        return None
    changed = set(line for line in out.splitlines() if line.strip())
    cache[commit] = changed
    return changed


# The changed-file sets for the RECALL path, memoized across calls, keyed on
# (repo, HEAD sha) -> {verified commit: changed set}. `freshness` keeps its own
# per-call cache: it walks the whole corpus once and is asked for explicitly, so
# it should always be answering about the repo as it is right now.
#
# Why this cache exists at all: the classification itself is free (0.012-0.075 ms
# per page, measured), but each `git diff` is ~47 ms and 98% of that is process
# startup. Bolting the freshness computation onto `search` unmemoized measured
# 267 ms against 5.7 ms — 47x. With this cache a warm search pays +1.04 ms, and a
# cold one pays 47 ms per DISTINCT verified commit among its own hits (1-3 on real
# queries), once per HEAD.
_FRESH_CACHE: Dict[tuple, dict] = {}


_REPO_ROOT_CACHE: Dict[str, str] = {}


def _repo_root_cached(root: str) -> str:
    """`repo_root` memoized per path, because it SPAWNS git.

    Measured at 16.41 ms — `git rev-parse --show-toplevel` — which made it the
    single most expensive thing on the recall path, dwarfing the work it was there
    to support (classification is 0.037 ms per page). Paying git 16 ms to find out
    where the repo is, in order to decide whether a cache of git calls is still
    valid, is exactly the absurdity `_head_sha_nospawn` was written to avoid; the
    line calling it just recreated it one level up.

    Which directory contains a repository does not change under a running server,
    so one answer per path is enough. `freshness` deliberately keeps calling
    `repo_root` directly: it is an explicitly requested audit that already costs
    ~240 ms in diffs, and its output is pinned byte-for-byte.
    """
    if root not in _REPO_ROOT_CACHE:
        _REPO_ROOT_CACHE[root] = repo_root(root)
    return _REPO_ROOT_CACHE[root]


def _head_sha_nospawn(repo: str) -> str:
    """The sha HEAD points at, read from the filesystem — NO subprocess.

    Used ONLY as a cache key, never rendered, so the full sha is fine and the
    `--short` formatting rules do not matter here. Measured at 0.076 ms against
    19.04 ms for `git rev-parse --short HEAD`: spawning git to decide whether a
    cache of git calls is still valid would cost more than the calls it saves.

    Returns "" when HEAD cannot be read from disk — a linked worktree or a
    submodule where `.git` is a FILE, a packed ref this does not find, or any IO
    error. The caller MUST treat "" as "do not cache", never as a key: an empty
    key would be identical across two different HEADs, which is precisely how a
    cache starts serving a stale answer with total confidence.
    """
    git_dir = os.path.join(repo, ".git")
    if not os.path.isdir(git_dir):
        return ""
    try:
        with open(os.path.join(git_dir, "HEAD"), "r", encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return ""
    if not head.startswith("ref: "):
        return head                      # detached HEAD holds the sha directly
    ref = head[5:].strip()
    try:
        with open(os.path.join(git_dir, ref), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        pass
    try:                                 # loose ref absent -> it may be packed
        with open(os.path.join(git_dir, "packed-refs"), "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass
    return ""


def _recall_diff_cache(repo: str) -> dict:
    """The commit-keyed changed-file cache for the recall path.

    Returns a plain dict for `_changed_files` to fill, so the diff logic itself is
    not duplicated here — this function only decides WHICH dict, and therefore how
    long the memory lives.

    A fresh dict (no cross-call memory) whenever HEAD cannot be read from disk,
    which degrades to the old cost and never to a wrong answer. Otherwise one dict
    per (repo, HEAD); when HEAD moves the whole store is dropped rather than grown,
    because only the current HEAD can ever be asked about again.
    """
    head_key = _head_sha_nospawn(repo)
    if not head_key:
        return {}
    key = (repo, head_key)
    cache = _FRESH_CACHE.get(key)
    if cache is None:
        _FRESH_CACHE.clear()
        cache = _FRESH_CACHE.setdefault(key, {})
    return cache


def _evaluate(sources, changed, repo):
    """Classify a page's sources against the changed-file set."""
    changed_sources = []
    missing = []
    for src in sources:
        path = _source_path(src, repo)
        abs_path = os.path.join(repo, path)
        if os.path.isdir(abs_path):
            prefix = path.rstrip("/") + "/"
            if any(c == path or c.startswith(prefix) for c in changed):
                changed_sources.append(src)
        elif os.path.isfile(abs_path):
            if path in changed:
                changed_sources.append(src)
        else:
            missing.append(src)
    return changed_sources, missing


def _classify_page(relpath: str, fm: dict, repo: str, changed_for) -> dict:
    """The git-derived state of ONE page.

    `changed_for(commit)` yields the set of files changed between that commit and
    HEAD, or None when the commit cannot be resolved. Everything else here is pure
    dict/filesystem work, which is why one page can be classified for the price of
    a few microseconds once the diff is in hand.

    EXTRACTED, NOT REIMPLEMENTED, and that is the whole point. Two callers need
    this rule: `freshness_analyze` for the whole corpus, and the recall replies
    (`search`, `source_to_pages`) for the handful of pages in one answer. A second
    copy would be a second place to edit, and this repo has paid for that shape
    three times already — the `_cap_text` twins, the `_md_fence` divergence, and a
    test suite that re-implemented a server step and then disagreed with it. The
    recall path is only allowed to be cheap; it is not allowed to be its own
    authority on what "stale" means.

    Eight states, and the distinction that matters for rendering: only `current`
    means "compared against HEAD and clean". `unverified`, `promotable`, `planned`,
    `untracked` and `no-sources` all mean NOT CHECKABLE — a page with no anchors
    can never be stale, which is emphatically not the same as being fresh. A label
    that showed any of those as `current` would be re-telling the lie this work
    removes.
    """
    name = fm.get("name") or relpath
    typ = fm.get("type") or ""
    sources = as_list(fm.get("sources"))
    targets = as_list(fm.get("targets"))
    materialized = [t for t in targets
                    if os.path.exists(os.path.join(repo, _source_path(t, repo)))]
    verified = fm.get("verified") if isinstance(fm.get("verified"), dict) else {}
    commit = (verified or {}).get("commit")
    base = {"name": name, "path": relpath, "type": typ}

    if not sources:
        if materialized:
            return dict(base, status="promotable", materialized=materialized)
        if targets:
            return dict(base, status="planned")
        return dict(base, status="untracked" if typ in UNTRACKED_TYPES else "no-sources")
    if not commit:
        return dict(base, status="unverified", reason="no verified.commit")
    changed = changed_for(commit)
    if changed is None:
        return dict(base, status="unverified",
                    reason="verified.commit not in history", commit=commit)
    changed_sources, missing = _evaluate(sources, changed, repo)
    if missing:
        return dict(base, status="orphaned-source", missing=missing,
                    changed_sources=changed_sources, verified_at=commit)
    if changed_sources:
        return dict(base, status="stale", changed_sources=changed_sources,
                    verified_at=commit)
    if materialized:
        return dict(base, status="promotable", materialized=materialized,
                    verified_at=commit)
    return dict(base, status="current", verified_at=commit)


def _head_resolves(head: str, repo: str) -> bool:
    """Does git answer for this ref — asked with the argv `freshness` will use?

    The SAME `rev-parse --short` invocation `freshness_analyze` resolves the head
    sha with, deliberately: a probe that answered where the real call fails would
    hand the corpus walk the very ref it was asked to vet, and the walk has no way
    left to report that. `--short` also keeps the two costs identical (19.04 ms
    measured), so this adds one rev-parse to an audit that already pays ~240 ms in
    diffs and nothing at all to the recall path, which never calls it.
    """
    code, _out, _err = git(["rev-parse", "--short", head], cwd=repo)
    return code == 0


def freshness_analyze(root: str, head: str, path_prefix=None) -> dict:
    repo = repo_root(root)
    code, head_sha, _ = git(["rev-parse", "--short", head], cwd=repo)
    head_sha = head_sha.strip() if code == 0 else head

    # The prefix filters on the way IN, never the rendered rows, and BOTH halves
    # of that are load-bearing.
    #
    # Cost: `_classify_page` is the expensive step — one `git diff` per distinct
    # `verified.commit` — so a page the caller excluded must never be classified.
    # This is the earliest point the data allows: `relpath` is what `iter_pages`
    # yields, and nothing before it knows which page it is looking at.
    #
    # Honesty: `summary` is derived from `pages` immediately below, and every
    # count the report renders (`ok:`, `gating:`) is derived from `summary`.
    # Filter the rows afterwards and those two lines keep describing the whole
    # corpus while the list above them describes a slice of it — a report whose
    # totals answer a question nobody asked is the one way this function can lie.
    #
    # Same rule as `search` and `list`, and now literally the same function:
    # `_path_prefix_matches` over the docs-relative path. It was three hand
    # copies of `relpath.startswith(prefix)` until adr 0018's declared gap was
    # closed; correcting one of the three alone would have left them disagreeing
    # about what a prefix means, which is why they moved together.
    prefix = _scope_prefix(path_prefix)
    cache: dict = {}
    # The walk is materialized rather than filtered in a comprehension because
    # the REFUSAL needs the paths the filter rejected: a prefix that selects
    # nothing has to name the scopes that exist, and those can only be built
    # from the corpus. Only the relpaths are kept -- the frontmatter and body of
    # an excluded page are dropped as they always were, and `_classify_page`,
    # the expensive step, still runs on the selected set alone.
    seen: List[str] = []
    pages = []
    for relpath, fm, _body in iter_pages(root):
        seen.append(relpath)
        if not _path_prefix_matches(relpath, prefix):
            continue
        pages.append(_classify_page(
            relpath, fm, repo, lambda c: _changed_files(c, head, repo, cache)))

    summary: dict = {}
    for page in pages:
        summary[page["status"]] = summary.get(page["status"], 0) + 1
    report = {"root": root, "head": head_sha, "pages": pages, "summary": summary}
    if prefix:
        # Declared only when there IS one, so an unfiltered report is the same
        # dict it has always been; `freshness_render` reads it with `.get`.
        report["path_prefix"] = prefix
        if not pages:
            # Same reason, one level deeper: the scope list is carried only when
            # the refusal will actually spend it, so no well-formed report grows
            # a key for an answer it is not giving. Computed HERE rather than in
            # the renderer because `root` is what knows the corpus, and a
            # renderer that had to re-walk the disk would be a second authority
            # on what a page is.
            report["scopes"] = _corpus_scopes(seen)
    return report


def _fresh_detail(page) -> str:
    status = page["status"]
    if status == "stale":
        return " — changed: %s (verified %s)" % (
            ", ".join(page.get("changed_sources", [])), page.get("verified_at", ""))
    if status == "orphaned-source":
        return " — missing: %s" % ", ".join(page.get("missing", []))
    if status == "unverified":
        return " — %s" % page.get("reason", "")
    if status == "promotable":
        return " — materialized: %s (promote targets→sources)" % ", ".join(
            page.get("materialized", []))
    return ""


def freshness_render(report) -> str:
    prefix = report.get("path_prefix") or ""
    if prefix and not report["pages"]:
        # NOT the "no pages found" sentence below, and emphatically not an empty
        # report: a report with no rows still renders `gating: 0`, which reads as
        # "nothing is stale" when what actually happened is that nothing was
        # looked at. A prefix that selects nothing is a question this answer can
        # only decline, and it declines in the caller's own word.
        #
        # It also names the scopes that WOULD have worked, from `scopes`, which
        # the analyser derived from the corpus. That half is what adr 0018 left
        # unresolved -- the refusal was right and unhelpful, where the comparable
        # refusal in purity_call builds its suggestion from the set it is
        # refusing against, so the caller's second call is a correction rather
        # than a guess.
        return "\n".join(
            ["# freshness @ %s — path_prefix %r" % (report["head"], prefix), ""]
            + _no_scope_lines(prefix, report.get("scopes") or [],
                              "nothing being stale")) + "\n"
    by_status: dict = {}
    for page in report["pages"]:
        by_status.setdefault(page["status"], []).append(page)
    head_line = "# freshness @ %s" % report["head"]
    if prefix:
        # The scope belongs in the HEADER because every number under it — each
        # bucket size, `ok:`, `gating:` — counts the filtered set alone.
        head_line += " — path_prefix %r, %d page(s)" % (prefix,
                                                        len(report["pages"]))
    lines = [head_line, ""]
    for status in DETAIL_STATUSES:
        bucket = by_status.get(status, [])
        if not bucket:
            continue
        lines.append("%s (%d):" % (status, len(bucket)))
        for page in bucket:
            lines.append("- %s `%s`%s" % (page["name"], page["path"], _fresh_detail(page)))
        lines.append("")
    clean = {k: v for k, v in report["summary"].items() if k not in DETAIL_STATUSES}
    if clean:
        lines.append("ok: " + ", ".join("%d %s" % (v, k) for k, v in sorted(clean.items())))
    summary = report["summary"]
    # `gating` counts what can be PROVEN broken; git lag is the advisory below.
    # See the GATING_CLASSES note for the whole argument and for how it squares
    # with adr 0002. `verify` is absent only when this renderer is driven
    # directly, which no handler does -- `_fn_freshness` always attaches it.
    verify = report.get("verify")
    gating_pages = (verify or {}).get("gating") or []
    lines.append("%s%d (%s)" % (GATING_LINE_PREFIX, verify_gating_count(verify),
                                " + ".join(GATING_CLASSES)))
    # Indented and WITHOUT a leading dash on purpose: these are not status
    # buckets and must not read as more of them. The per-defect answer is
    # `verify`; this is the pointer to it.
    if (verify or {}).get("registry_error"):
        lines.append("  %s — %s" % (MEASUREMENTS_FILE, verify["registry_error"]))
    for page in gating_pages[:GATING_DETAIL_LIMIT]:
        reasons = _verify_page_reasons(page)
        # ONE reason per page, and the count of the rest: this is a census with
        # a pointer, not the defect list. A page with four broken anchors that
        # rendered four rows here would have turned the summary into `verify`.
        more = (" (+%d more)" % (len(reasons) - 1)) if len(reasons) > 1 else ""
        lines.append("  %s `%s` — %s%s" % (page["name"], page["path"],
                                           reasons[0] if reasons else "gating",
                                           more))
    if len(gating_pages) > GATING_DETAIL_LIMIT:
        lines.append("  … and %d more — call verify for the whole list"
                     % (len(gating_pages) - GATING_DETAIL_LIMIT))
    moved, unchecked = (summary.get(s, 0) for s in ADVISORY_STATUSES)
    if moved or unchecked:
        lines.append(
            "%s%d page(s) list a source git says moved since they were "
            "verified, %d cannot be compared at all — git lag is a MEASUREMENT, "
            "not a verdict: it cannot tell a moved comma from a reversed "
            "decision. A human read may be owed; nothing here is claimed wrong."
            % (ADVISORY_LINE_PREFIX, moved, unchecked))
    if not report["pages"]:
        lines.append("no pages found")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Reindex (ports p:wiki/scripts/reindex.py)
# ---------------------------------------------------------------------------

def reindex_collect(root: str):
    entries = []
    by_name: dict = {}
    referenced = set()
    malformed = []
    for relpath, fm, body in iter_pages(root):
        name = fm.get("name")
        typ = fm.get("type")
        entry = {
            "path": relpath,
            "name": name,
            "type": typ or "unknown",
            "title": fm.get("title") or name or relpath,
            "description": fm.get("description") or "",
            "status": fm.get("status") or "",
        }
        entries.append(entry)
        for link in as_list(fm.get("links")) + extract_wikilinks(body):
            referenced.add(link)
        issues = []
        if not name:
            issues.append("missing name")
        if not typ:
            issues.append("missing type")
        if entry["status"] in STATUS_FORBIDDEN:
            issues.append("status `%s` is a git-measured state, not editorial"
                          " intent (use draft/active/deprecated)" % entry["status"])
        if issues:
            malformed.append({"path": relpath, "issues": issues})
        if name:
            by_name.setdefault(name, []).append(relpath)
    dups = {n: paths for n, paths in by_name.items() if len(paths) > 1}
    orphans = [e for e in entries
               if e["name"] and e["name"] not in referenced and e["type"] != "overview"]
    return entries, dups, orphans, malformed


def render_index(entries) -> str:
    groups: dict = {}
    for entry in entries:
        groups.setdefault(entry["type"], []).append(entry)
    order = TYPE_ORDER + sorted(t for t in groups if t not in TYPE_ORDER)
    lines = ["# Wiki Index", "",
             "_Generated by the p:wiki reindex tool — refresh with `/p:wiki`. Do not edit by hand._", ""]
    for typ in order:
        bucket = groups.get(typ)
        if not bucket:
            continue
        lines.append("## %s" % typ)
        for entry in sorted(bucket, key=lambda e: e["title"].lower()):
            desc = (" — " + entry["description"]) if entry["description"] else ""
            status = (" `[%s]`" % entry["status"]) if entry["status"] in INDEX_LABELLED else ""
            lines.append("- [%s](%s)%s%s" % (entry["title"], entry["path"], desc, status))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_reindex_report(entries, dups, orphans, malformed, wrote_path) -> str:
    if wrote_path:
        lines = ["# reindex: %d pages -> %s" % (len(entries), wrote_path), ""]
    else:
        lines = ["# reindex: %d pages (check only)" % len(entries), ""]
    if dups:
        lines.append("duplicate slugs (%d):" % len(dups))
        for name, paths in sorted(dups.items()):
            lines.append("- %s — %s" % (name, ", ".join(paths)))
        lines.append("")
    if malformed:
        lines.append("malformed (%d):" % len(malformed))
        for item in malformed:
            lines.append("- %s — %s" % (item["path"], ", ".join(item["issues"])))
        lines.append("")
    if orphans:
        lines.append("orphans (%d):" % len(orphans))
        for entry in orphans:
            lines.append("- %s `%s`" % (entry["name"], entry["path"]))
        lines.append("")
    lines.append("summary: %d pages, %d dup-slug, %d malformed, %d orphan"
                 % (len(entries), len(dups), len(malformed), len(orphans)))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Handlers — each returns {"__raw_text__": md} or {"error": msg}
# ---------------------------------------------------------------------------

def _finalize(md: str, params: dict) -> dict:
    """Wrap markdown for return, truncating at max_answer_chars (default 100k)."""
    raw = params.get("max_answer_chars")
    # OverflowError: `1e999` and the bare `Infinity` token arrive as a float
    # infinity, which `int()` refuses with neither a TypeError nor a ValueError.
    try:
        limit = int(raw) if raw is not None else DEFAULT_MAX_CHARS
    except (TypeError, ValueError, OverflowError):
        limit = DEFAULT_MAX_CHARS
    if limit and len(md) > limit:
        # Cut on a LINE boundary. Every line here is load-bearing structure — a
        # `path.md#heading-slug` anchor, a `- Context (L24, 595c)` section entry, a
        # `missed:` list — and half of one is worse than none of it: an anchor
        # truncated mid-slug still LOOKS like an anchor, so the caller spends a call
        # discovering it does not resolve. Floored at nothing, i.e. if the first
        # line alone exceeds the limit the character cut stands; a reply with no
        # newline in it has no boundary to honour.
        full = len(md)
        cut = md[:limit]
        nl = cut.rfind("\n")
        if nl > 0:
            cut = cut[:nl]
        # Report the REAL length, not the parameter. `full` is already computed by
        # the condition above, and the caller knows what it asked for — what it
        # cannot know is how much it is missing, which is the whole decision about
        # whether to ask again with a bigger ceiling or narrow the query instead.
        md = cut.rstrip() + (
            "\n\n… (truncated at %d of %d chars — raise max_answer_chars for more)\n"
            % (len(cut.rstrip()), full))
    return {"__raw_text__": md}


def _resolve_root(params: dict, project_root: str, wiki_root: str, strict: bool) -> Tuple[str, str]:
    """Return (abs_root, rel_root) for the wiki, honoring a per-call `root`."""
    rel = params.get("root") or wiki_root
    abs_root = safe_path(project_root, rel, strict)
    if not os.path.isdir(abs_root):
        raise ValueError("wiki root not found: %s" % rel)
    return abs_root, rel


# Weighted fields, most-to-least discriminating. FIELD_WEIGHTS supplies the
# BM25F per-field boost applied to the term frequency BEFORE global saturation.
#
# `aliases` is the SYNONYM layer, and it belongs HERE — which is the exact
# opposite of where the type signal belongs. Measured case: adr/0001 records a
# MERGE decision, but its prose says `fold` / `unify` / `unification` throughout
# and `merge` appears ZERO times on the page, while two other pages carry it
# (df 2). So on `clangd purity merge decision` the page reports coverage 54% with
# `missed: merge` and the 55% gate deletes the one right answer. Measured on both
# failing queries, the intended page sits at 38% and 54% — BELOW the gate — so
# the gate is not neutral about that page, it structurally excludes it.
#
# This is the mirror image of TYPE_SIGNAL_TOKENS. A category is not a claim about
# content, so it may never reach `hit_terms`/`coverage`. An alias IS a claim about
# content: saying "this page is also about `merge`" asserts the page answers for
# that word. It therefore MUST count toward coverage — otherwise the gate keeps
# deleting the page and the alias buys nothing. The W9 trick (score-only, isolated
# from the gate) is unavailable here, and that asymmetry is the design.
#
# CURATION RULE, and it is the one thing here that can break the W8 calibration:
# an alias may never introduce a word the corpus does not ALREADY carry in prose.
# Measured on a temp copy of docs/ (.claude/tmp/synonym-triage/triage.py) with the
# equivalence gate green on all six calibration queries:
#   * `merge` (prose df 2) -> the window stays bit-identical at (49%, 59%], the
#     0.55 gate stays inside it, and the intended page goes 38% -> 100%;
#   * `verbosity` (prose df 0) -> the window CLOSES at k=1: (67%, 59%], the gate
#     falls out, and a SILENT case starts answering. A df-0 term earns the maximum
#     idf and is the sole reason that case is silent, so importing one repeals the
#     abstention it was calibrated on. The leak is not even local: a page whose
#     bytes did not change went 49% -> 58% because the shared denominator shrank.
# Hence: every alias token must already appear in some page's NON-alias field. An
# alias RE-ROUTES vocabulary; it never invents it. Neutral filler is harmless —
# 15 invented tokens moved no page's coverage at all, only the 2nd decimal of the
# score — so the risk is carried by the token's identity, never by their number.
#
# And the aliases themselves must come from OBSERVATION, not from imagination.
# Furnas et al. (CACM 30(11), 1987 — verified against the paper, not recalled)
# measured that expert authors' keywords "fared no better than average" and that
# one person "rarely comes up with more than a half dozen names" out of the
# hundred a population produces; three guessed aliases are worth about one
# well-chosen title. Two channels qualify as evidence here: a FAILED query (the
# word an actual asker used — `merge` came from one) and a SIBLING page anchoring
# the same source file (spec-purity-unification says `merge` about the same code,
# tf 8). A guessed alias list is not a cheap version of this; it is the thing that
# was measured not to work.
_SEARCH_FIELDS = ("name", "title", "description", "anchor", "aliases",
                  "heading", "body")


def _page_field_tokens(fm, body, headings) -> Dict[str, List[str]]:
    """Tokenize each weighted field of a page into a token list (lowercased)."""
    fields = {
        "name": fm.get("name") or "",
        "title": fm.get("title") or "",
        "description": fm.get("description") or "",
        "anchor": " ".join(str(a) for a in as_list(fm.get("sources")) + as_list(fm.get("targets"))),
        "aliases": " ".join(str(a) for a in as_list(fm.get("aliases"))),
        "heading": " ".join(h[2] for h in headings),
        "body": body,
    }
    return {f: _tokenize(text) for f, text in fields.items()}


def _prefix_count(tokens: List[str], term: str) -> int:
    """Number of tokens that start with `term` (prefix match: build → builder)."""
    return sum(1 for tok in tokens if tok.startswith(term))


def _build_corpus(abs_root: str):
    """Read + tokenize every page (query-INDEPENDENT — the cacheable unit).

    Returns (corpus, avgfl, n_docs) where corpus is a list of per-page dicts
    carrying the per-field token lists and field lengths, and avgfl is the mean
    token length per field across the corpus.
    """
    corpus = []
    len_sums = {f: 0 for f in _SEARCH_FIELDS}
    for relpath, fm, body in iter_pages(abs_root):
        headings = _headings(body)
        tokens = _page_field_tokens(fm, body, headings)
        field_len = {f: len(tokens[f]) for f in _SEARCH_FIELDS}
        for f in _SEARCH_FIELDS:
            len_sums[f] += field_len[f]
        corpus.append({"relpath": relpath, "fm": fm, "body": body,
                       "headings": headings, "tokens": tokens, "field_len": field_len,
                       # Ranking-only signal, kept OUT of `tokens`/`field_len` so
                       # no df, coverage or avg-length computation can reach it.
                       "type_tokens": TYPE_SIGNAL_TOKENS.get(
                           str(fm.get("type") or "").strip().lower(), ())})
    n = len(corpus)
    avgfl = {f: (len_sums[f] / n if n else 0.0) for f in _SEARCH_FIELDS}
    return corpus, avgfl, n


# Memoized tokenized corpus, keyed on a cheap filesystem signature. The search
# index is a pure function of the on-disk pages, so it is invalidated by disk
# state alone — no write-path hook, correct even when a DIFFERENT process (the
# librarian's editor) changed a page.
_CORPUS_CACHE: Dict[str, tuple] = {}


def _corpus_signature(abs_root: str) -> tuple:
    """Stat-only walk (NO read/parse) mirroring iter_pages' skip rules; returns
    a hashable of sorted (relpath, mtime_ns, size). Rebuilds trigger on any
    page add/remove/edit; INDEX.md is skipped so a reindex never
    spuriously invalidates the search cache."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append((os.path.relpath(full, abs_root), st.st_mtime_ns, st.st_size))
    return tuple(entries)


def _build_corpus_cached(abs_root: str):
    """`_build_corpus` memoized on `_corpus_signature`. Reads + tokenizes only
    when the on-disk corpus changed since the last call for this root."""
    sig = _corpus_signature(abs_root)
    cached = _CORPUS_CACHE.get(abs_root)
    if cached is not None and cached[0] == sig:
        return cached[1], cached[2], cached[3]
    corpus, avgfl, n = _build_corpus(abs_root)
    _CORPUS_CACHE[abs_root] = (sig, corpus, avgfl, n)
    return corpus, avgfl, n


def _fn_search(params, project_root, wiki_root, strict):
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("search requires 'query'")
    abs_root, rel_root = _resolve_root(params, project_root, wiki_root, strict)
    raw_terms = list(dict.fromkeys(_tokenize(query)))  # unique, order-preserving
    if not raw_terms:
        raise ValueError("search 'query' contains no searchable tokens")
    # Stopwords go BEFORE df/idf: a term that never reaches `terms` cannot skew
    # the ranking, and cannot inflate the coverage denominator either.
    terms = [t for t in raw_terms if t not in QUERY_STOPWORDS]
    dropped = [t for t in raw_terms if t in QUERY_STOPWORDS]
    if not terms:
        # A THIRD kind of silence, and it needs its own words: the query carried no
        # content at all. Telling the caller "no matching pages" here would blame
        # the wiki for the question.
        return _finalize(
            "# search: %r — 0 hit(s) in %s/\n\n"
            "the query is all function words (%s) — nothing left to search for\n"
            % (query, rel_root, ", ".join(dropped)), params)
    f_type = params.get("type")
    f_status = params.get("status")
    prefix = _scope_prefix(params.get("path_prefix"))
    # OverflowError here and on `depth` below: `1e999` and the bare `Infinity`
    # token arrive as a float infinity, which `int()` refuses with neither a
    # TypeError nor a ValueError. The `float()` coercions that follow need no
    # entry -- `float(inf)` does not raise, and the clamps absorb it.
    try:
        limit = max(1, int(params.get("limit") or 10))
    except (TypeError, ValueError, OverflowError):
        limit = 10
    try:
        k1 = float(params.get("k1")) if params.get("k1") is not None else 1.2
    except (TypeError, ValueError):
        k1 = 1.2
    try:
        b = min(1.0, max(0.0, float(params.get("b")))) if params.get("b") is not None else 0.75
    except (TypeError, ValueError):
        b = 0.75
    try:
        raw_cov = params.get("min_coverage")
        min_cov = (min(1.0, max(0.0, float(raw_cov))) if raw_cov is not None
                   else DEFAULT_MIN_COVERAGE)
    except (TypeError, ValueError):
        min_cov = DEFAULT_MIN_COVERAGE

    # Corpus stats are GLOBAL (over all pages, pre-filter) so a term's rarity
    # does not shift with the caller's type/status/prefix filter.
    corpus, avgfl, n_docs = _build_corpus_cached(abs_root)
    # A FOURTH silence, and the only one that is not about the query at all.
    #
    # The other three -- nothing matched, nothing passed the gate, the query was
    # all function words -- are real answers ABOUT THE CORPUS, and adr 0017's
    # rule is that a silent zero is the defect, not the question. A scope holding
    # no page is the case where the zero is not about the corpus: the ranking ran
    # over an empty set, so "no matching pages" would blame the wiki for a
    # mistyped directory name. Refused BEFORE `_repo_root_cached` and the df loop,
    # so a scope nobody is in costs no git and no scoring.
    #
    # Deliberately narrow: it fires only when the PREFIX alone admits nothing.
    # A prefix that selects pages which then lose to the relevance gate, or to
    # `type`/`status`, still renders the gate's own silence -- that silence is a
    # real answer and turning it into an error would repeal the gate.
    if prefix and not any(_path_prefix_matches(pd["relpath"], prefix)
                          for pd in corpus):
        return _finalize("\n".join(
            ["# search: %r — 0 hit(s) in %s/ — path_prefix %r"
             % (query, rel_root, prefix), ""]
            + _no_scope_lines(prefix,
                              _corpus_scopes(pd["relpath"] for pd in corpus),
                              "the wiki having no answer to your query")
        ) + "\n", params)
    # W11: the state shown per hit is MEASURED against git, not read from the
    # frontmatter. Prepared here, spent only on pages that actually match — the
    # diff is the expensive part and a query with no hits must pay nothing.
    repo = _repo_root_cached(abs_root)
    _diff_cache = _recall_diff_cache(repo)
    changed_for = lambda c: _changed_files(c, "HEAD", repo, _diff_cache)  # noqa: E731
    df = {t: 0 for t in terms}
    for pd in corpus:
        for t in terms:
            if any(_prefix_count(pd["tokens"][f], t) for f in _SEARCH_FIELDS):
                df[t] += 1
    idf = {t: math.log((n_docs - df[t] + 0.5) / (df[t] + 0.5) + 1.0) for t in terms}
    # Total query "information mass": the denominator of the coverage gate. A term
    # NO page carries still counts here (df 0 earns the maximum idf), which is the
    # point -- an unknown word is evidence the corpus does not cover the topic.
    idf_total = sum(idf.values())

    results = []
    for pd in corpus:
        fm, relpath = pd["fm"], pd["relpath"]
        if not _path_prefix_matches(relpath, prefix):
            continue
        if f_type and (fm.get("type") or "") != f_type:
            continue
        score = 0.0
        hit_terms = []
        for t in terms:
            # BM25F pseudo-TF: each field length-normalized on its OWN avg length,
            # boosted, then summed — saturation is applied ONCE afterwards.
            ftilde = 0.0
            for f in _SEARCH_FIELDS:
                cnt = _prefix_count(pd["tokens"][f], t)
                if not cnt:
                    continue
                norm = (1.0 - b + b * (pd["field_len"][f] / avgfl[f])) if avgfl[f] > 0 else 1.0
                ftilde += FIELD_WEIGHTS[f] * cnt / norm
            # The COVERAGE verdict is settled HERE, over _SEARCH_FIELDS — which
            # now includes the declared `aliases`, and that is the point: an alias
            # IS a claim about content, so it has to be creditable, or the gate
            # keeps deleting the page it was written for (see the _SEARCH_FIELDS
            # note). What `hit_terms` must never learn about is the TYPE: a page
            # whose genre matches still has to say something about the word.
            # Do NOT re-collapse the two into "prose alone" — that sentence stood
            # here and became this repo's EIGHTH lying comment the moment the
            # alias field landed one line above it.
            if ftilde > 0:
                hit_terms.append(t)
            # W9: the type signal joins the RANKING only, after that verdict.
            # Unnormalized on purpose — the token set is a short closed list, not
            # prose, and a type is not "thinner" because the SCHEMA spends more
            # words describing it. A page matching NOTHING in prose is still
            # dropped below (`if not hit_terms`), so the type promotes, never
            # invents.
            ftilde += TYPE_SIGNAL_WEIGHT * _prefix_count(pd["type_tokens"], t)
            if ftilde > 0:
                score += idf[t] * (ftilde * (k1 + 1.0)) / (ftilde + k1)
        if not hit_terms:
            continue
        # What share of the query this page actually answers. Kept per-page (not
        # just for the winner) so the caller can see the ranking decay, and so a
        # weak page is filtered on its own merit rather than on the leader's.
        coverage = (sum(idf[t] for t in hit_terms) / idf_total) if idf_total else 0.0
        # The state is MEASURED, and WHERE it is measured depends on who is asking.
        #
        # With a `status` filter it must happen HERE, before the coverage gate: the
        # gate computes `best_cov` and may refuse with "no page passes", and that
        # message has to describe the set the caller actually asked for — a page
        # the filter excludes must not be the one the refusal quotes. The filter
        # selects on the measured state, never on the frontmatter field, because a
        # filter picking by one value while the reply prints another would
        # contradict itself on screen.
        #
        # Without a filter it is DEFERRED to after the gate and `limit`, because a
        # query the gate silences must pay no git at all. "After the lexical match"
        # sounded narrow and is not: one corpus-wide token drags every page in —
        # `mcp` is df10 here, the same fact that made the relevance gate
        # structurally unreachable in W8 — so measured, the gated query classified
        # 10/10 pages and spent 5 subprocesses to render 193 characters that say
        # there is no answer.
        state = None
        if f_status:
            state = _classify_page(relpath, fm, repo, changed_for)
            if state["status"] != f_status:
                continue
        snippet, _section, anchor = _best_snippet(pd["body"], pd["headings"], terms, relpath)
        if not snippet:
            snippet = fm.get("description") or ""
        results.append({
            "title": fm.get("title") or fm.get("name") or relpath,
            "slug": fm.get("name") or relpath,
            "type": fm.get("type") or "",
            "status": state["status"] if state else None,
            "anchor": anchor, "snippet": snippet,
            "score": score, "coverage": coverage,
            "missed": [t for t in terms if t not in hit_terms],
            "_relpath": relpath, "_fm": fm,   # only for the deferred classification
        })

    results.sort(key=lambda r: r["score"], reverse=True)  # pure BM25F relevance
    # The refusal quotes this number ("best coverage N%, need M%"), and it is the
    # caller's only measure of HOW CLOSE the wiki came to answering. So it is the
    # MAXIMUM, taken explicitly — not `results[0]`.
    #
    # Maximum over the pages the caller's filters ADMITTED, which for the common
    # unfiltered call is the whole corpus. Under a type/status/path_prefix filter
    # it is deliberately the admitted set: quoting a page the caller excluded
    # would answer a question nobody asked. Said precisely here on purpose — the
    # sentence being fixed below went wrong by claiming one word too many.
    #
    # `results[0]` was the top-SCORING page, which is a different page whenever
    # score and coverage disagree, and the comment here used to call it "the true
    # best of the whole corpus" while only being truncation-proof. Two guards are
    # needed and only one was present: running the gate BEFORE `limit` stops the
    # number being an artefact of truncation, and `max` stops it being an artefact
    # of ORDER. W9 made the omission visible by changing which page scores
    # highest: measured on the test fixture the same query reported "best coverage
    # 37%" before the type signal and "best coverage 1%" after it, while no page's
    # coverage moved at all — the reply understating the corpus by 36 points on a
    # sentence whose entire job is to say how close it got.
    n_lexical = len(results)          # pages sharing at least one term with the query
    best_cov = max((r["coverage"] for r in results), default=0.0)
    results = [r for r in results if r["coverage"] >= min_cov][:limit]
    # The deferred classification (see the loop above): now that the gate and the
    # limit have run, this is the handful of pages the answer will actually show —
    # at most `limit`, and zero when the gate silenced the query.
    for r in results:
        if r["status"] is None:
            r["status"] = _classify_page(r["_relpath"], r["_fm"], repo,
                                         changed_for)["status"]

    unknown = [t for t in terms if df[t] == 0]
    lines = ["# search: %r — %d hit(s) in %s/  (BM25F, k1=%g b=%g)"
             % (query, len(results), rel_root, k1, b), ""]
    # The caller asked for seven words and got an answer about three of them; that
    # is exactly the kind of thing a reply must volunteer. Silent when nothing was
    # dropped, so a clean query pays nothing for the disclosure.
    if dropped:
        lines.append("ignored function words: %s" % ", ".join(dropped))
    if unknown:
        lines.append("unknown to the corpus: %s" % ", ".join(unknown))
    # No frontmatter-vs-measured disagreement notice here, deliberately: the two
    # stopped being comparable. `status:` now carries the DISJOINT editorial enum
    # `draft|active|deprecated` (adr 0002), so it can never equal a measured state
    # and the check would fire on every reply forever — a warning with zero
    # variance is noise, which is the same argument that once justified printing
    # this one. The drift it guarded against is now caught upstream instead:
    # `current`/`stale` in that field is a `reindex --check` error.
    if not results:
        # TWO different silences, and the difference is the caller's next move:
        # nothing matched at all (rephrase / wrong wiki) versus matches that were
        # not good enough (the topic is probably undocumented). Only the first was
        # expressible before -- and it never fired, because one corpus-wide token
        # is enough to match every page.
        # Percentages are FLOORED, never rounded. Rounding 54.6% up to "55%"
        # against a 55% gate would make this very line contradict itself --
        # claiming the best page met the bar while refusing to show it.
        lines.append("no matching pages" if not n_lexical else
                     "no page passes the relevance gate "
                     "(best coverage %d%%, need %d%%)"
                     % (math.floor(100 * best_cov), math.floor(100 * min_cov)))
    elif unknown or dropped:
        lines.append("")              # keep the notes clear of the ranking
    for i, r in enumerate(results, 1):
        meta = "/".join(x for x in [r["type"], r["status"]] if x)
        meta = (" [%s]" % meta) if meta else ""
        lines.append("%d. **%s** — %s `%s`%s  (score %.2f, cov %d%%)" % (
            i, r["title"], r["slug"], r["anchor"], meta, r["score"],
            math.floor(100 * r["coverage"])))
        # Names the terms this page does NOT answer. Replaces the old `4/6 terms`
        # counter, which said HOW MANY were missing but never WHICH -- and stays
        # silent when nothing is missing, so a full match costs nothing.
        if r["missed"]:
            lines.append("   missed: %s" % ", ".join(r["missed"]))
        if r["snippet"]:
            lines.append("   %s" % r["snippet"])
    return _finalize("\n".join(lines).rstrip() + "\n", params)


def _fn_source_to_pages(params, project_root, wiki_root, strict):
    source = str(params.get("source") or "").strip()
    if not source:
        raise ValueError("source_to_pages requires 'source' (a path or path:symbol)")
    abs_root, rel_root = _resolve_root(params, project_root, wiki_root, strict)
    q_path = _source_path(source, project_root)
    q_sym = source[len(q_path):].lstrip(":") or None

    def _matches(anchor: str) -> bool:
        a_path = _source_path(anchor, project_root)
        a_sym = anchor[len(a_path):].lstrip(":") or None
        path_hit = (a_path == q_path
                    or q_path.startswith(a_path.rstrip("/") + "/")
                    or a_path.startswith(q_path.rstrip("/") + "/"))
        if not path_hit:
            return False
        if q_sym and a_sym:
            return q_sym == a_sym
        return True

    # Same measured state as `search` renders, via the same classifier and the same
    # cross-call diff cache — classified only for pages that matched, so a source
    # nothing documents costs no git at all.
    repo = _repo_root_cached(abs_root)
    _diff_cache = _recall_diff_cache(repo)
    changed_for = lambda c: _changed_files(c, "HEAD", repo, _diff_cache)  # noqa: E731

    hits = []
    for relpath, fm, _body in iter_pages(abs_root):
        matched_sources = [a for a in as_list(fm.get("sources")) if _matches(str(a))]
        matched_targets = [a for a in as_list(fm.get("targets")) if _matches(str(a))]
        if matched_sources or matched_targets:
            state = _classify_page(relpath, fm, repo, changed_for)
            hits.append({
                "title": fm.get("title") or fm.get("name") or relpath,
                "slug": fm.get("name") or relpath, "path": relpath,
                "type": fm.get("type") or "", "status": state["status"],
                "description": fm.get("description") or "",
                "sources": matched_sources, "targets": matched_targets,
            })

    lines = ["# source_to_pages: %s — %d page(s) in %s/" % (source, len(hits), rel_root), ""]
    if not hits:
        lines.append("no page references this source")
    # No fm-vs-measured notice — the two enums are disjoint now; see _fn_search.
    for h in hits:
        meta = "/".join(x for x in [h["type"], h["status"]] if x)
        meta = (" [%s]" % meta) if meta else ""
        lines.append("- **%s** — %s `%s`%s" % (h["title"], h["slug"], h["path"], meta))
        # The one line that answers the question actually being asked. This
        # handler was parsing `description` and then dropping it, so the reply
        # said WHICH pages cover a source and nothing about WHAT they say — a
        # pointer the caller has to spend another call to cash in, which is the
        # leak this whole line of work is chasing. Measured against the
        # alternative: a section list under every hit costs +116%..+178%, the
        # description +54%..+63%, and it is present on all ten pages, so it
        # never silently adds nothing. Structure is `get_page`'s answer; this
        # function is asked "which page", and a sentence answers that.
        if h["description"]:
            lines.append("    description: %s" % h["description"])
        if h["sources"]:
            lines.append("    sources: %s" % ", ".join(map(str, h["sources"])))
        if h["targets"]:
            lines.append("    targets: %s" % ", ".join(map(str, h["targets"])))
    return _finalize("\n".join(lines).rstrip() + "\n", params)


def _render_frontmatter(relpath: str, fm: dict) -> List[str]:
    lines = ["# %s" % (fm.get("title") or fm.get("name") or relpath), ""]
    lines.append("- **slug**: %s" % (fm.get("name") or "—"))
    lines.append("- **path**: %s" % relpath)
    lines.append("- **type**: %s" % (fm.get("type") or "—"))
    lines.append("- **status**: %s" % (fm.get("status") or "—"))
    if fm.get("description"):
        lines.append("- **description**: %s" % fm.get("description"))
    srcs = as_list(fm.get("sources"))
    if srcs:
        lines.append("- **sources**: %s" % ", ".join(map(str, srcs)))
    tgts = as_list(fm.get("targets"))
    if tgts:
        lines.append("- **targets**: %s" % ", ".join(map(str, tgts)))
    v = fm.get("verified") if isinstance(fm.get("verified"), dict) else {}
    if v:
        lines.append("- **verified**: %s @ %s" % (v.get("commit", "?"), v.get("date", "?")))
    links = as_list(fm.get("links"))
    if links:
        lines.append("- **links**: %s" % ", ".join(map(str, links)))
    return lines


def _fn_get_page(params, project_root, wiki_root, strict):
    slug = str(params.get("slug") or "").strip()
    if not slug:
        raise ValueError("get_page requires 'slug' (page name or relative path)")
    abs_root, _rel_root = _resolve_root(params, project_root, wiki_root, strict)
    want = slug[:-3] if slug.endswith(".md") else slug

    target = None
    for relpath, fm, body in iter_pages(abs_root):
        rel_noext = relpath[:-3] if relpath.endswith(".md") else relpath
        if fm.get("name") == slug or relpath == slug or rel_noext == want:
            target = (relpath, fm, body)
            break
    if target is None:
        raise ValueError("no page with slug/path %r under the wiki root" % slug)

    relpath, fm, body = target
    lines = _render_frontmatter(relpath, fm)

    section = params.get("section")
    include_body = _bool_param(params.get("include_body", True), True)
    try:
        depth = max(2, int(params.get("depth") or 2))
    except (TypeError, ValueError, OverflowError):
        depth = 2
    start = _window_int(params, "from")
    count = _window_int(params, "lines")
    if start is not None or count is not None:
        # The window wins over the other two selectors, and the answer says which
        # one it overrode. It is the most specific of the three -- an exact range
        # against a heading name or a yes/no -- and a caller that sent two is owed
        # the knowledge of which one it got instead of a silent pick [D6].
        overridden = [name for name, hit in (("section", bool(section)),
                                             ("include_body", not include_body))
                      if hit]
        try:
            with open(os.path.join(abs_root, relpath), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            # `_section_index_block` can drop its L and carry on when this read
            # fails; a window cannot, because here the numbers ARE the answer.
            raise ValueError("cannot read %s for a line window: %s" % (relpath, exc))
        if overridden:
            lines += ["", "_(line window takes precedence — ignored: %s)_"
                      % ", ".join(overridden)]
        lines += _file_line_window(text, start or 1, count or DEFAULT_WINDOW_LINES)
        return _finalize("\n".join(lines).rstrip() + "\n", params)
    if section:
        extracted = _extract_section(body, str(section))
        if extracted is None:
            # A refusal that does not say what IS there sends the caller back for
            # the whole page — the circular dependency this block exists to cut.
            lines += ["", "_(section %r not found)_" % section]
            lines += _section_index_block(body, os.path.join(abs_root, relpath),
                                          depth)
        else:
            lines += ["", extracted]
    elif include_body:
        lines += ["", body.strip()]
    else:
        lines += _section_index_block(body, os.path.join(abs_root, relpath), depth)
    return _finalize("\n".join(lines).rstrip() + "\n", params)


def _fn_list(params, project_root, wiki_root, strict):
    abs_root, rel_root = _resolve_root(params, project_root, wiki_root, strict)
    f_type = params.get("type")
    f_status = params.get("status")
    prefix = _scope_prefix(params.get("path_prefix"))

    entries = []
    # Every page the walk saw, for the refusal below: the scopes that exist can
    # only be derived from the corpus, and this walk is the one that has it.
    seen: List[str] = []
    in_scope = 0
    for relpath, fm, _body in iter_pages(abs_root):
        seen.append(relpath)
        if not _path_prefix_matches(relpath, prefix):
            continue
        in_scope += 1
        if f_type and (fm.get("type") or "") != f_type:
            continue
        if f_status and (fm.get("status") or "") != f_status:
            continue
        entries.append({
            "path": relpath, "type": fm.get("type") or "unknown",
            "title": fm.get("title") or fm.get("name") or relpath,
            "slug": fm.get("name") or relpath,
            "description": fm.get("description") or "", "status": fm.get("status") or "",
        })

    if prefix and not in_scope:
        # Same refusal as `search` and `freshness`, and it is counted on
        # `in_scope` rather than on `entries` on purpose: an empty answer under
        # a type/status filter is a real census result ("this wiki has no
        # runbook"), and only the PREFIX admitting nothing means the caller
        # named a place instead of a set. The two are different mistakes with
        # different fixes, so `no pages match the filter` below keeps its job.
        return _finalize("\n".join(
            ["# wiki pages: 0 in %s/ — path_prefix %r" % (rel_root, prefix), ""]
            + _no_scope_lines(prefix, _corpus_scopes(seen),
                              "the wiki holding no such page")) + "\n", params)

    groups: dict = {}
    for e in entries:
        groups.setdefault(e["type"], []).append(e)
    order = TYPE_ORDER + sorted(t for t in groups if t not in TYPE_ORDER)

    lines = ["# wiki pages: %d in %s/" % (len(entries), rel_root), ""]
    if not entries:
        lines.append("no pages match the filter")
    for typ in order:
        bucket = groups.get(typ)
        if not bucket:
            continue
        lines.append("## %s (%d)" % (typ, len(bucket)))
        for e in sorted(bucket, key=lambda x: x["title"].lower()):
            desc = (" — " + e["description"]) if e["description"] else ""
            status = (" `[%s]`" % e["status"]) if e["status"] else ""
            lines.append("- %s `%s`%s%s" % (e["title"], e["path"], desc, status))
        lines.append("")
    return _finalize("\n".join(lines).rstrip() + "\n", params)


def _fn_freshness(params, project_root, wiki_root, strict):
    abs_root, _rel_root = _resolve_root(params, project_root, wiki_root, strict)
    head = str(params.get("head") or "HEAD")
    # `head` is a git REF, and an unresolvable one used to be reported as a fault
    # of the CORPUS. `freshness_analyze` falls back to the literal string when
    # rev-parse fails, `_changed_files` returns None on ANY non-zero git exit, and
    # `_classify_page` renders that one None as `verified.commit not in history` —
    # so `head: 12`, from a caller reading the param as a display limit, accused
    # every page of the wiki, pages verified against HEAD itself included, of
    # pointing at a commit that is gone. The STATUS was right (nothing is
    # checkable against a ref that does not resolve); only the attribution was
    # invented, and it named the one half of the comparison that was innocent.
    #
    # Refused here, before the corpus walk, and ONLY when the caller's ref is
    # provably the broken half: git answers for HEAD and not for what was passed.
    # When rev-parse fails for BOTH, git cannot answer at all — a directory it
    # does not track, no git on PATH — which is a different report rather than a
    # bad parameter, so the literal fallback below stands and every page
    # classifies as not-checkable exactly as it did before.
    if not _head_resolves(head, abs_root) and _head_resolves("HEAD", abs_root):
        raise ValueError(
            "freshness 'head' is the git ref to compare the wiki against "
            "(default 'HEAD'), not a count or a limit; this repo cannot resolve "
            "%r" % head)
    report = freshness_analyze(abs_root, head, params.get("path_prefix"))
    # Attached HERE rather than inside `freshness_analyze`, and that placement is
    # load-bearing twice over. It keeps the analyser's return value the dict it
    # has always been -- a git measurement and nothing else, which the suite pins
    # as a KEY SET, absence included. And it keeps the provable half in
    # `verify_analyze`, which takes rendered bodies as strings and cannot make
    # one: `rendered` is None here, so this read path reports the region states
    # that need no subprocess and says so in the answer.
    registry, registry_error = load_measurements_safe(abs_root)
    report["verify"] = verify_analyze(abs_root, registry=registry,
                                      registry_error=registry_error,
                                      path_prefix=params.get("path_prefix"))
    return _finalize(freshness_render(report), params)


def _fn_verify(params, project_root, wiki_root, strict):
    abs_root, rel_root = _resolve_root(params, project_root, wiki_root, strict)
    registry, registry_error = load_measurements_safe(abs_root)
    rendered = errors = None
    # OPT-IN, and the default is the conservative one: everything `verify`
    # reports by default is provable without starting a process. Re-rendering a
    # measured region runs a command a repo file named, so it happens only when
    # the caller asks for it in as many words -- and then through the same grant
    # `measure` uses, never through a second door.
    if _bool_param(params.get("measure", False), False):
        rows, _malformed = measure_scan(abs_root, params.get("path_prefix"))
        rendered, errors = _measure_bodies(
            registry, _repo_root_cached(abs_root), _ExecutionGrant("verify"),
            names={row["name"] for row in rows})
    report = verify_analyze(abs_root, registry=registry,
                            registry_error=registry_error, rendered=rendered,
                            errors=errors,
                            path_prefix=params.get("path_prefix"))
    return _finalize(verify_render(report, rel_root), params)


def _fn_measure(params, project_root, wiki_root, strict):
    abs_root, rel_root = _resolve_root(params, project_root, wiki_root, strict)
    # The RAISING loader here, not the tolerant one `freshness` uses: this
    # handler is about to write, and it may not write against a registry it
    # could not read. The ValueError reaches the caller as a clean tool error.
    registry = load_measurements(abs_root)
    write = _bool_param(params.get("write", False), False)
    force = _bool_param(params.get("force", False), False)
    only = str(params.get("name") or "").strip()
    rows, malformed = measure_scan(abs_root, params.get("path_prefix"))
    if only:
        rows = [row for row in rows if row["name"] == only]
        if not rows:
            raise ValueError(
                "no measured region named %r under this scope; the corpus "
                "carries %s" % (only, ", ".join(sorted(
                    {r["name"] for r in measure_scan(abs_root)[0]})) or "none"))
    rendered, errors = _measure_bodies(registry, _repo_root_cached(abs_root),
                                       _ExecutionGrant("measure"),
                                       names={row["name"] for row in rows})
    pending: Dict[str, List[dict]] = {}
    for row in rows:
        row["state"] = measured_region_state(row, registry, rendered, errors)
        row["found"] = measured_digest(row["body"])
        row["rendered"] = rendered.get(row["name"])
        row["error"] = errors.get(row["name"], "")
        row["written"] = False
        if not write or row["rendered"] is None:
            continue
        if row["state"] == "ok":
            continue
        if row["state"] == "hand-edited" and not force:
            continue                    # refused, never silently overwritten
        row["written"] = True
        pending.setdefault(row["path"], []).append(row)
    for relpath, page_rows in pending.items():
        measure_apply(abs_root, relpath, page_rows)
    return _finalize(
        measure_render(rows, malformed, registry, rel_root, write, force),
        params)


def _fn_reindex(params, project_root, wiki_root, strict):
    abs_root, _rel_root = _resolve_root(params, project_root, wiki_root, strict)
    check = _bool_param(params.get("check", False), False)
    entries, dups, orphans, malformed = reindex_collect(abs_root)
    wrote_path = None
    if not check:
        wrote_path = os.path.join(abs_root, "INDEX.md")
        with open(wrote_path, "w", encoding="utf-8") as fh:
            fh.write(render_index(entries))
    return _finalize(
        render_reindex_report(entries, dups, orphans, malformed, wrote_path), params)


def _fn_stats(params, project_root, wiki_root, strict):
    abs_root, rel_root = _resolve_root(params, project_root, wiki_root, strict)
    entries, dups, orphans, malformed = reindex_collect(abs_root)
    by_type: dict = {}
    by_status: dict = {}
    for e in entries:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        by_status[e["status"] or "—"] = by_status.get(e["status"] or "—", 0) + 1

    lines = ["# wiki stats: %d pages in %s/" % (len(entries), rel_root), ""]
    lines.append("by type:")
    order = TYPE_ORDER + sorted(t for t in by_type if t not in TYPE_ORDER)
    for typ in order:
        if by_type.get(typ):
            lines.append("- %s: %d" % (typ, by_type[typ]))
    lines.append("")
    lines.append("by status:")
    for st in sorted(by_status):
        lines.append("- %s: %d" % (st, by_status[st]))
    lines.append("")
    lines.append("audit: %d dup-slug, %d orphan, %d malformed"
                 % (len(dups), len(orphans), len(malformed)))
    return _finalize("\n".join(lines).rstrip() + "\n", params)


# ---------------------------------------------------------------------------
# Dispatch registry (same shape as mcp-purity)
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Callable[..., dict]] = {
    "search": _fn_search,
    "source_to_pages": _fn_source_to_pages,
    "get_page": _fn_get_page,
    "list": _fn_list,
    "freshness": _fn_freshness,
    "reindex": _fn_reindex,
    "stats": _fn_stats,
    "verify": _fn_verify,
    "measure": _fn_measure,
}

# The read paths — every function whose answer is derived from the corpus alone
# and which may therefore NEVER reach `_measure_run`. Written down here rather
# than inferred from "the ones that happen not to", because the suite walks this
# file's call graph from each of these names and a handler added to HANDLERS
# without a decision about this list is exactly the drift it is checking for.
# `verify` and `measure` are the complement: see MEASURE_GRANTED_FUNCTIONS.
READ_ONLY_FUNCTIONS = ("search", "source_to_pages", "get_page", "list",
                       "freshness", "reindex", "stats")

_COMMON_PARAMS = {"root", "max_answer_chars"}
HANDLER_ACCEPTED_PARAMS: Dict[str, set] = {
    "search": _COMMON_PARAMS | {"query", "type", "status", "path_prefix", "limit",
                                "k1", "b", "min_coverage"},
    "source_to_pages": _COMMON_PARAMS | {"source"},
    "get_page": _COMMON_PARAMS | {"slug", "section", "include_body", "depth",
                                  "from", "lines"},
    "list": _COMMON_PARAMS | {"type", "status", "path_prefix"},
    "freshness": _COMMON_PARAMS | {"head", "path_prefix"},
    "reindex": _COMMON_PARAMS | {"check"},
    "stats": set(_COMMON_PARAMS),
    "verify": _COMMON_PARAMS | {"path_prefix", "measure"},
    "measure": _COMMON_PARAMS | {"path_prefix", "name", "write", "force"},
}

# Function-name aliases -> canonical handler name.
FUNCTION_ALIASES = {
    "find": "search",
    "query": "search",
    "q": "search",
    "page": "get_page",
    "get": "get_page",
    "read": "get_page",
    "sources": "source_to_pages",
    "src2pages": "source_to_pages",
    "index": "reindex",
    "ls": "list",
    "fresh": "freshness",
    # `status` is the spelling a caller reaches for; `stats` is the function that
    # exists. The word is already in this server's vocabulary three times over --
    # a search/list PARAM, the frontmatter field, and the git-measured state --
    # and as a FUNCTION name it can only mean the census, so there is nothing for
    # it to be ambiguous against (a row here collides only with another row here
    # or with a HANDLERS key, and `status` is neither).
    #
    # It does NOT take the no-function reply's job: `wiki_call()` with no
    # function at all stays the liveness answer -- server up, here are the
    # functions -- and a caller asking for `status` wants the CORPUS's state, not
    # the process's.
    "status": "stats",
    # `anchors` is what the job is CALLED in the schema and in the librarian's
    # own checklist ("verify anchors"), and `audit` is the word a caller reaches
    # for when they want to know what is broken. Neither collides: a row here
    # can only collide with another row or with a HANDLERS key, and both are
    # new. `measurements` is the file's name, which is the other spelling
    # somebody will try.
    "anchors": "verify",
    "audit": "verify",
    "measurements": "measure",
}

# Global param aliases — applied regardless of function.
PARAM_ALIASES = {
    "q": "query",
    "text": "query",
    "max": "limit",
    "count": "limit",
    "n": "limit",
    "max_chars": "max_answer_chars",
    "max_output_chars": "max_answer_chars",
    "ref": "head",
    "rev": "head",
    "commit": "head",
}

# Function-specific aliases — applied BEFORE the global PARAM_ALIASES.
#
# `path` occupies FIVE of the rows below and reaches THREE different canonical
# names -- `slug`, `source`, path_prefix -- which is at once why each row is
# right and why none of them may be global. Every one is the same trade: the
# handler already took a docs-relative path as a VALUE and was turning away only
# its KEY. _fn_get_page matches `relpath == slug`; _fn_search, _fn_list and
# _fn_freshness filter through `_path_prefix_matches`, whose FIRST clause is that
# same equality, so a WHOLE path is a scope selecting the single page it names.
# (It was `relpath.startswith(prefix)` in all three when these rows were written,
# and that is what made `sub` select `subsystems/`.) And the key the caller
# reaches for is the one
# the answers taught them -- a search hit prints `subsystems/scripts.md`,
# get_page's own header answers `- **path**: subsystems/scripts.md`, and nothing
# the server renders ever says path_prefix.
#
# The three prefix rows are spelled IDENTICALLY on purpose. A caller who learned
# `dir` from `list` must not have it rejected by `freshness`, which filters the
# same field the same way: a spelling that works on one of the three and not on
# the next is the divergence these tables exist to prevent.
#
# What a GLOBAL row would cost is now visible rather than argued. A global
# `path` -> `slug` would make search's row -- the SAME word, a different
# canonical name -- unwritable; and it would answer the two handlers that take
# no path under any name (reindex, stats) with `Unknown params for 'stats':
# slug`, renaming the caller's word inside another handler's rejection.
PARAM_ALIASES_BY_FUNC: Dict[str, Dict[str, str]] = {
    "search": {"prefix": "path_prefix", "dir": "path_prefix",
               "path": "path_prefix", "pattern": "query"},
    "list": {"prefix": "path_prefix", "dir": "path_prefix",
             "path": "path_prefix"},
    "freshness": {"prefix": "path_prefix", "dir": "path_prefix",
                  "path": "path_prefix"},
    "source_to_pages": {"file": "source", "path": "source", "anchor": "source"},
    # `count` -> `lines` is NOT redundant with the global table, it OVERRIDES it:
    # globally `count` means `limit`, the search result count, and get_page has no
    # result list for that to mean anything on. Without this entry the natural
    # spelling of a window height would arrive as `limit` and be rejected as an
    # unknown param -- loudly, but for a request that was never wrong.
    "get_page": {"name": "slug", "path": "slug", "heading": "section",
                 "body": "include_body", "count": "lines", "start": "from"},
    "reindex": {"check_only": "check", "dry_run": "check"},
    # The same three prefix spellings the other three filters take, for the same
    # reason: a caller who learned `dir` from `list` must not have it rejected
    # here. `verify` spells the execution opt-in `render`/`run` as well, because
    # `measure: true` inside `verify` reads as a function name to anyone who has
    # met the other function.
    "verify": {"prefix": "path_prefix", "dir": "path_prefix",
               "path": "path_prefix", "render": "measure", "run": "measure"},
    # `write` is the inverse of reindex's `check`, so the two spellings a caller
    # brings from there both land: `apply`/`fix` mean write, and `measurement`
    # is the longer word for the one name to re-render.
    "measure": {"prefix": "path_prefix", "dir": "path_prefix",
                "path": "path_prefix", "apply": "write", "fix": "write",
                "measurement": "name"},
}


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_json.py :: _bool_param
def _bool_param(value, default=False):
    """Coerce a possibly-stringy value to bool.

    The wire frequently carries booleans as strings ("false"/"0"/"no"), where a
    naive bool("false") would wrongly yield True.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)
# END GENERATED: df133cd7c299


def _canonical_function(function: str) -> str:
    return FUNCTION_ALIASES.get(function, function)


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_json.py :: _json_error_window
def _json_error_window(text: str, pos: int, radius: int = 48) -> str:
    """Return a repr'd slice of *text* centred on *pos*.

    A JSONDecodeError reports a character offset ("char 1530"), which the
    caller that produced the string cannot count to; the one broken escape is
    only actionable if it is shown. ``repr`` is what makes it visible -- the
    typical defect is a quote escaped one level too shallow, and a raw slice
    renders that identically to a correct one.
    """
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    lead = "..." if start > 0 else ""
    tail = "..." if end < len(text) else ""
    return f"{lead}{text[start:end]!r}{tail}"
# END GENERATED: 4c5e7e3f59cb


def _resolve_aliases(params: Any, function: Optional[str] = None) -> dict:
    """Return a new dict with aliased parameter names resolved to canonical names.

    Function-specific aliases take precedence over the global ones. A JSON-encoded
    string is decoded first; anything non-dict raises ValueError so the dispatcher
    can return a clean error instead of crashing.
    """
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'params' was a string but not valid JSON: {exc}. "
                f"Near the failure: {_json_error_window(params, exc.pos)}. "
                "Pass params as an object, not a JSON-encoded string."
            )
    if not isinstance(params, dict):
        raise ValueError(
            f"'params' must be an object (dict) or a JSON-encoded object string; "
            f"got {type(params).__name__}."
        )
    func_aliases = PARAM_ALIASES_BY_FUNC.get(function or "", {})
    resolved: dict = {}
    claimed: dict = {}
    for key, value in params.items():
        canonical = func_aliases.get(key) or PARAM_ALIASES.get(key, key)
        if canonical in resolved:
            first, second = sorted((claimed[canonical], key))
            raise ValueError(
                f"Ambiguous parameters: '{first}' and '{second}' both set "
                f"'{canonical}'. Pass exactly one."
            )
        resolved[canonical] = value
        claimed[canonical] = key
    return resolved


def _unknown_params(params: dict, accepted: set) -> list:
    """Sorted list of caller params not in `accepted`."""
    return sorted(set(params.keys()) - accepted)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def handle_wiki_call(arguments: dict, project_root: str, wiki_root: str,
                     strict: bool = False) -> dict:
    """Route a wiki_call invocation to the appropriate handler."""
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    canonical = _canonical_function(function)
    raw_params = arguments.get("params") or arguments.get("p") or {}

    try:
        params = _resolve_aliases(raw_params, canonical)
    except ValueError as exc:
        return {"error": str(exc)}

    if not function:
        func_list = "\n".join("  %s" % name for name in sorted(HANDLERS.keys()))
        return {"__raw_text__":
                "mcp-wiki OK — project: %s, wiki: %s/\nAvailable functions:\n%s"
                % (project_root, wiki_root, func_list)}

    handler = HANDLERS.get(canonical)
    if not handler:
        return {"error": "Unknown function: %s. Available: %s"
                % (function, ", ".join(sorted(HANDLERS.keys())))}

    accepted = HANDLER_ACCEPTED_PARAMS.get(canonical)
    if accepted is not None:
        unknown = _unknown_params(params, accepted)
        if unknown:
            return {"error": "Unknown params for '%s': %s. Accepted: %s."
                    % (canonical, ", ".join(unknown), ", ".join(sorted(accepted)))}

    try:
        return handler(params, project_root, wiki_root, strict)
    except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — dispatcher catch-all -> tool error
        log.exception("Unhandled exception in handler '%s'", canonical)
        return {"error": "Internal error in '%s': %s: %s"
                % (canonical, type(exc).__name__, exc)}


# ---------------------------------------------------------------------------
# MCP Server (same plumbing shape as mcp-git / MCP_SKELETON)
# ---------------------------------------------------------------------------

WIKI_CALL_TOOL = {
    "name": "wiki_call",
    "description": (
        "Search and read the project's docs/ wiki (the p:wiki knowledge base). "
        "PREFER THIS over Bash grep/find over docs/ and over shelling out to the "
        "p:wiki scripts (freshness.py / reindex.py) by absolute path — wiki_call "
        "gives frontmatter-aware, ranked, structured results with clickable "
        "`path#section` anchors.\n\n"
        "TRIGGER — ASK THE WIKI BEFORE YOU ANSWER, NOT AFTER\n"
        "The wiki carries the WHY the code cannot: decisions, the alternatives\n"
        "that lost, measurements, rationale. Source is authoritative for WHAT\n"
        "and HOW — it can never tell you why a threshold is the number it is,\n"
        "or which designs were measured and rejected before this one.\n"
        "\n"
        "Call function=\"search\" IMMEDIATELY when ANY of these is true:\n"
        "  - you are about to answer a WHY question (why is it like this, why\n"
        "    was X chosen over Y, what was the alternative);\n"
        "  - you are about to make a design decision inside a subsystem that\n"
        "    already exists;\n"
        "  - you are reconstructing intent from source, git log, or a note\n"
        "    that refers to reasoning which is not in front of you.\n"
        "\n"
        "ONE call settles it, because the gate is allowed to say no: 'no page\n"
        "passes the relevance gate' is a REAL ANSWER, not a failure — take it\n"
        "and go to the code. Skip this for a pure WHAT question (what does\n"
        "this thing do → read the code); it is about INTENT, not lookup. A\n"
        "knowledge base nobody knows about does not get consulted, which is\n"
        "why this is a rule and not left to your judgement.\n\n"
        "Functions (pass via 'function'):\n"
        "  search           BM25F-ranked token search (prefix match, per-field\n"
        "                   weighting); params: query (req), type, status,\n"
        "                   path_prefix, limit (default 10), k1/b (BM25 tuning),\n"
        "                   min_coverage (default 0.55: a page must carry that\n"
        "                   share of the query's idf mass or it is NOT reported,\n"
        "                   so an undocumented topic answers 'no page passes the\n"
        "                   relevance gate' instead of ranking noise; 0 disables)\n"
        "                   A page's type is also a RANKING signal: genre words in\n"
        "                   the query (decision, rationale, spec, runbook) promote\n"
        "                   pages of that type, drawing on the SCHEMA's own type\n"
        "                   table. It only REORDERS: a page matching nothing in\n"
        "                   the text is never pulled in by its type — pass type\n"
        "                   for a hard filter instead. Coverage, the missed list\n"
        "                   and the gate verdict are computed from the page's own\n"
        "                   words PLUS the alternative names it declares in its\n"
        "                   aliases frontmatter list. Unlike the type, an alias IS\n"
        "                   a claim about content, so it DOES count toward\n"
        "                   coverage and can admit a page whose prose never writes\n"
        "                   the word (adr 0001 answers for merge that way, while\n"
        "                   its text says unify and fold throughout).\n"
        "                   The state in each hit's [type/state] label is MEASURED\n"
        "                   against git (did a source anchor change since the page\n"
        "                   was last verified), not read from the frontmatter\n"
        "                   status field, which is hand-written and goes stale\n"
        "                   silently. The status param filters on that measured\n"
        "                   state too. Only current means checked-and-clean;\n"
        "                   unverified, promotable, planned, untracked and\n"
        "                   no-sources all mean NOT CHECKABLE, which is not the\n"
        "                   same as fresh. Use list or get_page to see the\n"
        "                   frontmatter field itself.\n"
        "  source_to_pages  reverse lookup — which pages document a source file,\n"
        "                   each with its one-line description, so the answer says\n"
        "                   what they cover and not merely that they do; params:\n"
        "                   source (req, a path or path:symbol)\n"
        "  get_page         read one page whole, a single section, or a window of\n"
        "                   file lines; params: slug (req — either the bare page\n"
        "                   name or the docs-relative path a search hit prints,\n"
        "                   subsystems/scripts.md), section, from, lines\n"
        "                   (default 40), include_body (default true), depth\n"
        "                   (default 2). When the section does not match, or\n"
        "                   include_body is false, the answer carries the page's\n"
        "                   section index — one dash line per heading with its\n"
        "                   file line and size in chars — so the next call can ask\n"
        "                   for one slice instead of re-reading the whole page.\n"
        "                   Raise depth to list headings below level 2. from and\n"
        "                   lines are FILE lines — the same numbers that index\n"
        "                   prints as L<n> — and the window is headed by how many\n"
        "                   lines lie before and after it, so a section too big to\n"
        "                   read whole can be walked instead. from wins over\n"
        "                   section and over include_body, and says so.\n"
        "  list             list pages grouped by type; params: type, status,\n"
        "                   path_prefix\n"
        "  freshness        corpus report; params: head — the git REF to compare\n"
        "                   the wiki against (default HEAD), never a count or a\n"
        "                   limit — and path_prefix (see below). Every count in\n"
        "                   the report, ok: and gating: included, then describes\n"
        "                   that subset and nothing else; a prefix matching no\n"
        "                   page says so instead of reporting clean. Read the two\n"
        "                   summary lines as what they are: gating counts what\n"
        "                   verify can PROVE is broken — an anchor that does not\n"
        "                   resolve, a measured region that is stale or hand-\n"
        "                   edited. advisory counts GIT LAG: pages whose listed\n"
        "                   sources moved since they were verified. Lag is a\n"
        "                   measurement, not a verdict — it cannot tell a moved\n"
        "                   comma from a reversed decision — so it says a human\n"
        "                   read may be owed and claims nothing more. This report\n"
        "                   is not paged.\n"
        "  verify           resolve every source anchor in the corpus and report\n"
        "                   the ones that do not; params: path_prefix, measure\n"
        "                   (default false). Every frontmatter sources: entry and\n"
        "                   every inline path or path:symbol span is resolved\n"
        "                   against the repo, and every measured region is\n"
        "                   classified. The symbol matcher is stdlib text — a .py\n"
        "                   symbol resolves on a def/class/assignment, a .md one\n"
        "                   on a heading, and any other extension falls back to a\n"
        "                   whole-word MENTION, counted separately and never\n"
        "                   silently promoted. purity_call is the real resolver.\n"
        "                   measure: true additionally RE-RENDERS every measured\n"
        "                   region, which runs the commands measurements.json\n"
        "                   names; without it nothing is executed and the answer\n"
        "                   says which regions it therefore did not check.\n"
        "  measure          re-render the measured regions of the corpus; params:\n"
        "                   write (default false = check only, writes nothing),\n"
        "                   force (discard a hand edit), name (one measurement),\n"
        "                   path_prefix. A page may carry a block whose body is\n"
        "                   rendered from a command instead of typed, between an\n"
        "                   HTML-comment marker pair carrying a digest of the\n"
        "                   emitted body; a body that no longer hashes to its\n"
        "                   recorded digest was edited by hand and is REFUSED,\n"
        "                   never overwritten, unless force says otherwise. THIS\n"
        "                   RUNS COMMANDS named by docs/measurements.json, which\n"
        "                   is why it and verify are the only two functions that\n"
        "                   can: no read path reaches an execution.\n"
        "  reindex          regenerate INDEX.md + audit; params: check (true =\n"
        "                   audit only, write nothing). WRITES docs/INDEX.md by default.\n"
        "  stats            page counts by type/status + dup/orphan/malformed\n"
        "                   audit; also answers to status, which is the word most\n"
        "                   callers reach for\n\n"
        "path_prefix — one scope, one rule, shared by search, list and freshness "
        "(and by verify and measure, which were written against it rather than "
        "beside it; spell it prefix, dir or path if you prefer, everywhere). "
        "It is matched against the docs-relative path a hit line prints, and it is "
        "a PATH BOUNDARY, not a substring: adr, adr/ and subsystems/ are directory "
        "scopes selecting everything beneath them; a whole page path selects that "
        "one page; a prefix ending inside the LAST component still selects, so "
        "adr/001 gives you the 0010-0019 records; and a prefix ending inside any "
        "EARLIER component selects nothing, so sub is not a way to spell "
        "subsystems/. A prefix no page is inside is refused by name and the "
        "refusal lists the scopes that do exist — it is never answered with an "
        "empty result, because zero pages in a scope nobody is in would read as a "
        "fact about the wiki.\n\n"
        "Common params: root (wiki root override — a different wiki root, never a "
        "filter and never a git ref), max_answer_chars (default 100000). "
        "Markdown output.\n\n"
        "Example: function=\"search\", params={\"query\":\"stream proxy\",\"type\":\"component\"}\n"
        "Call without 'function' for the function list — that reply is the "
        "server's own liveness, not a report about the wiki."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {"type": "string", "description": "Wiki function name."},
            "params":   {"type": "object", "description": "See main description."},
        },
    },
}


# How many tool calls may be in flight at once. The stdin reader owns a thread of
# its own, outside this pool, so saturating it delays queued CALLS and can never
# stop the server from READING — which is the entire point of the split in run().
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_concurrency.py :: MAX_INFLIGHT_REQUESTS
MAX_INFLIGHT_REQUESTS = 8
# END GENERATED: 0ffae9f02744


class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, project_root: str, wiki_root: str = DEFAULT_WIKI_ROOT,
                 strict: bool = False):
        self.project_root = os.path.realpath(project_root)
        self.wiki_root = wiki_root
        self.strict = strict

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, project_root=%s wiki_root=%s",
                 self.project_root, self.wiki_root)
        # TWO executors, and one task per request, on purpose. This loop used to
        # call the handler INLINE — `self._handle_message(msg)`, no await, no
        # executor — on the very thread that next had to await sys.stdin.readline.
        # A running handler therefore froze the whole server for its duration:
        # every other request sat unread in the pipe, timed out client-side (~60s)
        # and was then answered against an id the client had already abandoned.
        # From the caller's chair that is a dead server, and a restart was the only
        # lever. Measured on this very server before the fix: a `freshness` over a
        # 120-page tree took 2540 ms, and a `ping` sent 152 ms into it was answered
        # at 2541 ms — one millisecond AFTER the slow reply, having waited out the
        # whole call. Most calls here are milliseconds (the hot path deliberately
        # avoids spawning git: 0.076 ms vs 19.04 ms, see `_head_sha_nospawn`), so
        # this was the mild end of the fleet — but `freshness` and `reindex` walk
        # the entire corpus and `freshness` spends one subprocess per distinct
        # verified commit, so seconds are reachable by ordinary use, not only by
        # failure.
        #
        # ONE POOL WOULD NOT DO. If the readline shared the handler pool, eight
        # slow calls would occupy every worker and the readline would sit in the
        # pool's QUEUE — reintroducing exactly the deafness above, just with more
        # steps. The reader's single dedicated thread is what makes reading
        # unconditional.
        #
        # CONCURRENT HANDLERS ARE SAFE HERE, and this was AUDITED rather than
        # assumed — this server is a search engine with three memo caches, so
        # "no state" would have been a lie. The module declares no `global`
        # anywhere, and exactly four runtime mutation sites of module-level state
        # exist; everything else at module level (TYPE_ORDER, FIELD_WEIGHTS,
        # TYPE_SIGNAL_TOKENS, QUERY_STOPWORDS, SKIP_*, HANDLERS, the alias tables,
        # WIKI_CALL_TOOL) is built once at import and only ever read. The four:
        #
        #   * `_CORPUS_CACHE` (the BM25F index) — already built-fresh-and-swapped,
        #     which is precisely what makes it safe. `_build_corpus` returns brand
        #     new lists/dicts and `_build_corpus_cached` rebinds ONE key; nothing
        #     is ever mutated in place, and no reader writes back (`_fn_search` /
        #     `_fn_source_to_pages` only read `pd[...]` and append to their own
        #     local `results`). So a reader holding the previous corpus keeps a
        #     complete, immutable, self-consistent snapshot while a concurrent
        #     rebuild installs the next one. Two cold misses duplicate the
        #     read+tokenize work and the later writer wins — wasted CPU, never a
        #     mixed index, because a half-built corpus is never reachable through
        #     the cache. Invalidation is by `_corpus_signature` (mtime+size), so
        #     it cannot go stale behind a concurrent editor either.
        #   * `_REPO_ROOT_CACHE` — check-then-set. Two concurrent misses spawn
        #     `git rev-parse` twice and store the SAME value; a lost update costs
        #     16 ms, never an answer.
        #   * `_FRESH_CACHE` .clear() + .setdefault() in `_recall_diff_cache` —
        #     the one worth the thought, because it clears. The key is
        #     (repo, HEAD sha), i.e. it ENCODES everything the cached diffs depend
        #     on, so a HEAD move produces a different key and a different inner
        #     dict; a dict built for one HEAD can never be handed out for another.
        #     A racing clear can orphan an inner dict another request still holds,
        #     which merely loses the memo — that dict's entries stay correct for
        #     the HEAD it was keyed on, and the request re-spawns the diffs it
        #     needs. Values are pure functions of (repo, HEAD, commit), so a
        #     double write stores identical bytes.
        #
        # The inner dicts are only get/setitem'd, never iterated while written, so
        # the GIL makes each op atomic and no plain-dict resize can be observed
        # half-done. `reindex` — the one WRITING function — writes docs/INDEX.md,
        # which both `iter_pages` and `_corpus_signature` skip (SKIP_FILES), so it
        # can neither invalidate the search cache nor be half-read by a concurrent
        # search. That is why no lock appears below: no invariant here spans more
        # than one atomic dict operation, and every cached value is a pure function
        # of a key that already carries its own dependencies. A lock across
        # `_build_corpus` would only serialize searches behind whole-corpus reads.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wiki-stdin")
        workers = ThreadPoolExecutor(max_workers=MAX_INFLIGHT_REQUESTS,
                                     thread_name_prefix="wiki-call")
        inflight: set = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    log.warning("stdin read failed, shutting down: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    # Answering is not optional: a bare `continue` here left the
                    # caller's request id unanswered until it timed out.
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    # `5` is valid JSON. It used to reach msg.get() and take the
                    # process down with an AttributeError that escaped run() —
                    # measured before the fix: the very next write to the server's
                    # stdin raised BrokenPipeError, because there was no server
                    # left. An MCP client does not respawn a dead stdio server.
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue

                # F12/CWE-532: log protocol structure only, never payload
                # values (params.content / result text can carry file contents).
                # Defensive: params/arguments may be a non-dict on a malformed
                # message; this is a debug log and must never crash the loop.
                _p = msg.get("params")
                _p = _p if isinstance(_p, dict) else {}
                _args = _p.get("arguments")
                _args = _args if isinstance(_args, dict) else {}
                log.debug(
                    "← method=%s id=%s fn=%s keys=%s",
                    msg.get("method"), msg.get("id"), _p.get("name"),
                    list(_args.keys()),
                )
                task = loop.create_task(self._serve(loop, workers, msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
        finally:
            for task in inflight:
                task.cancel()
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)
            log.info("MCP server shutting down")

    async def _serve(self, loop, workers: ThreadPoolExecutor, msg: dict) -> None:
        """One request, from dispatch to written reply. Runs as its own task."""
        try:
            response = await loop.run_in_executor(workers, self._handle_message, msg)
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            self._write(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message.

        Called only from the event-loop thread: handlers run in the worker pool,
        but `_serve` resumes on the loop after its await, so concurrent replies
        cannot interleave and this needs no lock.
        """
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {exc}"))
        # F12/CWE-532: structure only (id + outcome), no body.
        log.debug(
            "→ id=%s %s", response.get("id"),
            "error" if "error" in response else "ok",
        )
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            # Unguarded, this escaped run() and killed the process.
            log.warning("stdout write failed: %s", exc)

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-wiki", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [WIKI_CALL_TOOL]})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name != "wiki_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        if not isinstance(arguments, dict):
            return self._result(msg_id, {
                "content": [{"type": "text", "text":
                    f"'arguments' must be an object; got {type(arguments).__name__}."}],
                "isError": True,
            })
        try:
            result = handle_wiki_call(arguments, self.project_root, self.wiki_root, self.strict)
        except Exception as exc:
            log.exception("Unhandled exception in handle_wiki_call")
            result = {"error": f"Internal server error: {type(exc).__name__}: {exc}"}
        is_error = "error" in result
        text = result.get("__raw_text__") or result.get("error", "")
        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
    # BEGIN GENERATED: _mcp_json.py :: _result, _error
    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    # END GENERATED: 0a5c31c9ccd8


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MCP-Wiki: read + search the docs/ wiki")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--wiki-root", default=DEFAULT_WIKI_ROOT,
                        help="Wiki root, relative to project root (default: docs)")
    parser.add_argument("--strict", action="store_true",
                        help="Reject root params that resolve outside project root")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()

    _configure_logging(args.debug, args.log_file)

    if not os.path.isdir(args.project_root):
        print(f"Error: project root is not a directory: {args.project_root}", file=sys.stderr)
        sys.exit(1)

    server = McpServer(args.project_root, wiki_root=args.wiki_root, strict=args.strict)
    asyncio.run(server.run())
    # stdin is closed, so the client is gone. Handler threads live in the server's
    # own executors rather than the loop's default one, so asyncio does not join
    # them — but concurrent.futures registers an atexit hook that WOULD, and one
    # handler mid-`git diff` would hold this process open for the rest of its
    # GIT_TIMEOUT_SEC. Every reply is flushed as it is written and logging flushes
    # per record, so there is nothing left to drain.
    os._exit(0)


if __name__ == "__main__":
    main()
