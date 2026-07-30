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
                   cannot answer gets silence instead of the best-scoring noise
  source_to_pages  reverse lookup: a source file/anchor -> the pages that cover it
  get_page         read one page (whole, a single section, or — when the section
                   misses or the body is declined — just its section index) by
                   slug/path
  list             list pages, grouped by type, with type/status/prefix filters
  freshness        git-only staleness report (ports freshness.py logic)
  reindex          regenerate INDEX.md + structural audit (ports reindex.py logic)
  stats            page counts by type/status + dup/orphan/malformed audit

The stdlib-only frontmatter parser is vendored from the p:wiki `_wikilib.py`
(kept in sync by hand — the SCHEMA.md section-5 parseable subset is the contract).
Symbol-level anchor verification (broken/drifted) is deliberately NOT done here;
that stays with the LLM (p:minion-librarian) via the language MCP servers.

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
import json
import logging
import math
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger("mcp-wiki")

DEFAULT_WIKI_ROOT = "docs"

# Page-type ordering for INDEX / list rendering (mirrors reindex.py).
TYPE_ORDER = ["overview", "subsystem", "component", "reference", "analysis",
              "concept", "spec", "runbook", "adr", "glossary"]

# Freshness statuses listed page-by-page; everything else is summarized.
DETAIL_STATUSES = ["stale", "orphaned-source", "unverified", "promotable"]
# Page types not bound to code sources and never freshness-tracked.
UNTRACKED_TYPES = {"overview", "adr", "glossary"}

# Files / dirs that are not wiki pages (mirrors _wikilib.py).
SKIP_FILES = {"INDEX.md", "SCHEMA.md"}
SKIP_DIRS = {"sources", "plans", ".git", ".claude", ".cache"}

# Search field weights — a term hit in the title counts far more than in the body.
FIELD_WEIGHTS = {"name": 8, "title": 8, "anchor": 5, "description": 4,
                 "heading": 3, "body": 1}
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
# Keep in sync with SCHEMA.md section 5 (the stdlib-parseable frontmatter subset).
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
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "git executable not found"


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
    return block


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


def freshness_analyze(root: str, head: str) -> dict:
    repo = repo_root(root)
    code, head_sha, _ = git(["rev-parse", "--short", head], cwd=repo)
    head_sha = head_sha.strip() if code == 0 else head

    cache: dict = {}
    pages = []
    for relpath, fm, _body in iter_pages(root):
        name = fm.get("name") or relpath
        typ = fm.get("type") or ""
        sources = as_list(fm.get("sources"))
        targets = as_list(fm.get("targets"))
        materialized = [t for t in targets
                        if os.path.exists(os.path.join(repo, _source_path(t, repo)))]
        verified = fm.get("verified") if isinstance(fm.get("verified"), dict) else {}
        commit = (verified or {}).get("commit")

        if not sources:
            if materialized:
                pages.append({"name": name, "path": relpath, "type": typ,
                              "status": "promotable", "materialized": materialized})
            elif targets:
                pages.append({"name": name, "path": relpath, "type": typ,
                              "status": "planned"})
            else:
                status = "untracked" if typ in UNTRACKED_TYPES else "no-sources"
                pages.append({"name": name, "path": relpath, "type": typ, "status": status})
            continue
        if not commit:
            pages.append({"name": name, "path": relpath, "type": typ,
                          "status": "unverified", "reason": "no verified.commit"})
            continue
        changed = _changed_files(commit, head, repo, cache)
        if changed is None:
            pages.append({"name": name, "path": relpath, "type": typ,
                          "status": "unverified", "reason": "verified.commit not in history",
                          "commit": commit})
            continue
        changed_sources, missing = _evaluate(sources, changed, repo)
        if missing:
            pages.append({"name": name, "path": relpath, "type": typ,
                          "status": "orphaned-source", "missing": missing,
                          "changed_sources": changed_sources, "verified_at": commit})
        elif changed_sources:
            pages.append({"name": name, "path": relpath, "type": typ,
                          "status": "stale", "changed_sources": changed_sources,
                          "verified_at": commit})
        elif materialized:
            pages.append({"name": name, "path": relpath, "type": typ,
                          "status": "promotable", "materialized": materialized,
                          "verified_at": commit})
        else:
            pages.append({"name": name, "path": relpath, "type": typ,
                          "status": "current", "verified_at": commit})

    summary: dict = {}
    for page in pages:
        summary[page["status"]] = summary.get(page["status"], 0) + 1
    return {"root": root, "head": head_sha, "pages": pages, "summary": summary}


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
    by_status: dict = {}
    for page in report["pages"]:
        by_status.setdefault(page["status"], []).append(page)
    lines = ["# freshness @ %s" % report["head"], ""]
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
    gating = summary.get("stale", 0) + summary.get("orphaned-source", 0) + summary.get("unverified", 0)
    lines.append("gating: %d (stale + orphaned-source + unverified)" % gating)
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
            status = (" `[%s]`" % entry["status"]) if entry["status"] else ""
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
    try:
        limit = int(raw) if raw is not None else 100000
    except (TypeError, ValueError):
        limit = 100000
    if limit and len(md) > limit:
        md = md[:limit].rstrip() + (
            "\n\n… (truncated at %d chars — raise max_answer_chars for more)\n" % limit)
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
_SEARCH_FIELDS = ("name", "title", "description", "anchor", "heading", "body")


def _page_field_tokens(fm, body, headings) -> Dict[str, List[str]]:
    """Tokenize each weighted field of a page into a token list (lowercased)."""
    fields = {
        "name": fm.get("name") or "",
        "title": fm.get("title") or "",
        "description": fm.get("description") or "",
        "anchor": " ".join(str(a) for a in as_list(fm.get("sources")) + as_list(fm.get("targets"))),
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
                       "headings": headings, "tokens": tokens, "field_len": field_len})
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
    page add/remove/edit; INDEX.md/SCHEMA.md are skipped so a reindex never
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
    prefix = params.get("path_prefix")
    try:
        limit = max(1, int(params.get("limit") or 10))
    except (TypeError, ValueError):
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
        if prefix and not relpath.startswith(prefix):
            continue
        if f_type and (fm.get("type") or "") != f_type:
            continue
        if f_status and (fm.get("status") or "") != f_status:
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
            if ftilde > 0:
                hit_terms.append(t)
                score += idf[t] * (ftilde * (k1 + 1.0)) / (ftilde + k1)
        if not hit_terms:
            continue
        # What share of the query this page actually answers. Kept per-page (not
        # just for the winner) so the caller can see the ranking decay, and so a
        # weak page is filtered on its own merit rather than on the leader's.
        coverage = (sum(idf[t] for t in hit_terms) / idf_total) if idf_total else 0.0
        snippet, _section, anchor = _best_snippet(pd["body"], pd["headings"], terms, relpath)
        if not snippet:
            snippet = fm.get("description") or ""
        results.append({
            "title": fm.get("title") or fm.get("name") or relpath,
            "slug": fm.get("name") or relpath,
            "type": fm.get("type") or "", "status": fm.get("status") or "",
            "anchor": anchor, "snippet": snippet,
            "score": score, "coverage": coverage,
            "missed": [t for t in terms if t not in hit_terms],
        })

    results.sort(key=lambda r: r["score"], reverse=True)  # pure BM25F relevance
    # The gate runs BEFORE `limit`: `best_cov` is then the true best of the whole
    # corpus, so the "not good enough" message cannot be an artefact of truncation.
    n_lexical = len(results)          # pages sharing at least one term with the query
    best_cov = results[0]["coverage"] if results else 0.0
    results = [r for r in results if r["coverage"] >= min_cov][:limit]

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

    hits = []
    for relpath, fm, _body in iter_pages(abs_root):
        matched_sources = [a for a in as_list(fm.get("sources")) if _matches(str(a))]
        matched_targets = [a for a in as_list(fm.get("targets")) if _matches(str(a))]
        if matched_sources or matched_targets:
            hits.append({
                "title": fm.get("title") or fm.get("name") or relpath,
                "slug": fm.get("name") or relpath, "path": relpath,
                "type": fm.get("type") or "", "status": fm.get("status") or "",
                "description": fm.get("description") or "",
                "sources": matched_sources, "targets": matched_targets,
            })

    lines = ["# source_to_pages: %s — %d page(s) in %s/" % (source, len(hits), rel_root), ""]
    if not hits:
        lines.append("no page references this source")
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
    except (TypeError, ValueError):
        depth = 2
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
    prefix = params.get("path_prefix")

    entries = []
    for relpath, fm, _body in iter_pages(abs_root):
        if prefix and not relpath.startswith(prefix):
            continue
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
    head = params.get("head") or "HEAD"
    report = freshness_analyze(abs_root, str(head))
    return _finalize(freshness_render(report), params)


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
}

_COMMON_PARAMS = {"root", "max_answer_chars"}
HANDLER_ACCEPTED_PARAMS: Dict[str, set] = {
    "search": _COMMON_PARAMS | {"query", "type", "status", "path_prefix", "limit",
                                "k1", "b", "min_coverage"},
    "source_to_pages": _COMMON_PARAMS | {"source"},
    "get_page": _COMMON_PARAMS | {"slug", "section", "include_body", "depth"},
    "list": _COMMON_PARAMS | {"type", "status", "path_prefix"},
    "freshness": _COMMON_PARAMS | {"head"},
    "reindex": _COMMON_PARAMS | {"check"},
    "stats": set(_COMMON_PARAMS),
}

# Function-name aliases -> canonical handler name.
FUNCTION_ALIASES = {
    "find": "search",
    "query": "search",
    "q": "search",
    "page": "get_page",
    "get": "get_page",
    "sources": "source_to_pages",
    "src2pages": "source_to_pages",
    "index": "reindex",
    "ls": "list",
    "fresh": "freshness",
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
PARAM_ALIASES_BY_FUNC: Dict[str, Dict[str, str]] = {
    "search": {"prefix": "path_prefix", "dir": "path_prefix", "pattern": "query"},
    "list": {"prefix": "path_prefix", "dir": "path_prefix"},
    "source_to_pages": {"file": "source", "path": "source", "anchor": "source"},
    "get_page": {"name": "slug", "heading": "section", "body": "include_body"},
    "reindex": {"check_only": "check", "dry_run": "check"},
}


def _bool_param(value, default=False):
    """Coerce a possibly-stringy value to bool ("false"/"0"/"no" -> False)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


def _canonical_function(function: str) -> str:
    return FUNCTION_ALIASES.get(function, function)


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
                "Pass params as an object, not a JSON-encoded string."
            )
    if not isinstance(params, dict):
        raise ValueError(
            f"'params' must be an object (dict) or a JSON-encoded object string; "
            f"got {type(params).__name__}."
        )
    func_aliases = PARAM_ALIASES_BY_FUNC.get(function or "", {})
    resolved: dict = {}
    for key, value in params.items():
        canonical = func_aliases.get(key) or PARAM_ALIASES.get(key, key)
        resolved[canonical] = value  # last-wins
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
        "Functions (pass via 'function'):\n"
        "  search           BM25F-ranked token search (prefix match, per-field\n"
        "                   weighting); params: query (req), type, status,\n"
        "                   path_prefix, limit (default 10), k1/b (BM25 tuning),\n"
        "                   min_coverage (default 0.55: a page must carry that\n"
        "                   share of the query's idf mass or it is NOT reported,\n"
        "                   so an undocumented topic answers 'no page passes the\n"
        "                   relevance gate' instead of ranking noise; 0 disables)\n"
        "  source_to_pages  reverse lookup — which pages document a source file,\n"
        "                   each with its one-line description, so the answer says\n"
        "                   what they cover and not merely that they do; params:\n"
        "                   source (req, a path or path:symbol)\n"
        "  get_page         read one page whole or a single section; params: slug\n"
        "                   (req), section, include_body (default true), depth\n"
        "                   (default 2). When the section does not match, or\n"
        "                   include_body is false, the answer carries the page's\n"
        "                   section index — one dash line per heading with its\n"
        "                   file line and size in chars — so the next call can ask\n"
        "                   for one slice instead of re-reading the whole page.\n"
        "                   Raise depth to list headings below level 2.\n"
        "  list             list pages grouped by type; params: type, status,\n"
        "                   path_prefix\n"
        "  freshness        git-only staleness report; params: head (default HEAD)\n"
        "  reindex          regenerate INDEX.md + audit; params: check (true =\n"
        "                   audit only, write nothing). WRITES docs/INDEX.md by default.\n"
        "  stats            page counts by type/status + dup/orphan/malformed audit\n\n"
        "Common params: root (wiki root override, default from --wiki-root), "
        "max_answer_chars (default 100000). Markdown output.\n\n"
        "Example: function=\"search\", params={\"query\":\"stream proxy\",\"type\":\"component\"}\n"
        "Call without 'function' for the function list."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {"type": "string", "description": "Wiki function name."},
            "params":   {"type": "object", "description": "See main description."},
        },
    },
}


class McpServer:
    def __init__(self, project_root: str, wiki_root: str = DEFAULT_WIKI_ROOT,
                 strict: bool = False):
        self.project_root = os.path.realpath(project_root)
        self.wiki_root = wiki_root
        self.strict = strict

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, project_root=%s wiki_root=%s",
                 self.project_root, self.wiki_root)
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("Invalid JSON: %s", exc)
                    continue
                log.debug("← %s", json.dumps(msg)[:200])
                try:
                    response = self._handle_message(msg)
                except Exception as exc:
                    log.exception("Unhandled exception while handling message")
                    response = self._error(
                        msg.get("id"), -32603,
                        f"Internal error: {type(exc).__name__}: {exc}",
                    )
                if response is not None:
                    out = json.dumps(response)
                    log.debug("→ %s", out[:200])
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()
        finally:
            log.info("MCP server shutting down")

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": "2024-11-05",
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

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


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

    level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
    log_handlers: list = []
    if args.log_file:
        log_handlers.append(logging.FileHandler(args.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    if not os.path.isdir(args.project_root):
        print(f"Error: project root is not a directory: {args.project_root}", file=sys.stderr)
        sys.exit(1)

    server = McpServer(args.project_root, wiki_root=args.wiki_root, strict=args.strict)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
