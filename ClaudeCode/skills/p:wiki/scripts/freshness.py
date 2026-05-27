#!/usr/bin/env python3
"""freshness.py -- read-only staleness detector for the p:wiki docs tree.

Determines which wiki pages may be out of date by comparing each page's
`verified.commit` against the current tree, using git only. No LLM, no code
navigation: this is the cheap pre-filter that tells the LLM lint pass *which*
pages to look at. Symbol-level checks (broken/drifted anchors) are NOT done here
-- they require the language MCP servers.

Usage:
    python scripts/freshness.py --root docs [--head HEAD] [--quiet]

Prints a compact prose report on stdout: only the actionable pages (stale /
orphaned-source / unverified) are listed in detail; clean pages are summarized
as counts. Exits non-zero if any page is stale, orphaned-source, or unverified
(usable as a pre-PR CI gate).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wikilib as w  # noqa: E402

# Page types that are not bound to code sources and are never freshness-tracked.
UNTRACKED_TYPES = {"overview", "adr", "glossary"}

# Statuses that are listed page-by-page; everything else is summarized as a count.
DETAIL_STATUSES = ["stale", "orphaned-source", "unverified"]

_INVALID = "<invalid-commit>"


def _changed_files(commit: str, head: str, repo: str, cache: dict):
	"""Return the set of files changed between `commit` and `head`, or None if
	`commit` is not resolvable. Results are cached per commit."""
	if commit in cache:
		val = cache[commit]
		return None if val == _INVALID else val
	code, out, _ = w.git(["diff", "--name-only", commit, head], cwd=repo)
	if code != 0:
		cache[commit] = _INVALID
		return None
	changed = set(line for line in out.splitlines() if line.strip())
	cache[commit] = changed
	return changed


def _source_path(source: str) -> str:
	"""Strip an optional `:symbol` suffix, returning the path part."""
	return source.split(":", 1)[0]


def _evaluate(sources, changed, repo):
	"""Classify a page's sources against the changed-file set.
	Returns (changed_sources, missing_sources)."""
	changed_sources = []
	missing = []
	for src in sources:
		path = _source_path(src)
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


def analyze(root: str, head: str):
	repo = w.repo_root(root)
	code, head_sha, _ = w.git(["rev-parse", "--short", head], cwd=repo)
	head_sha = head_sha.strip() if code == 0 else head

	cache: dict = {}
	pages = []
	for relpath, fm, _body in w.iter_pages(root):
		name = fm.get("name") or relpath
		typ = fm.get("type") or ""
		sources = w.as_list(fm.get("sources"))
		verified = fm.get("verified") if isinstance(fm.get("verified"), dict) else {}
		commit = (verified or {}).get("commit")

		if not sources:
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
		else:
			pages.append({"name": name, "path": relpath, "type": typ,
				"status": "current", "verified_at": commit})

	summary = {}
	for page in pages:
		summary[page["status"]] = summary.get(page["status"], 0) + 1
	return {"root": root, "head": head_sha, "pages": pages, "summary": summary}


def _detail(page) -> str:
	status = page["status"]
	if status == "stale":
		return " — changed: %s (verified %s)" % (
			", ".join(page.get("changed_sources", [])), page.get("verified_at", ""))
	if status == "orphaned-source":
		return " — missing: %s" % ", ".join(page.get("missing", []))
	if status == "unverified":
		return " — %s" % page.get("reason", "")
	return ""


def render(report) -> str:
	"""Render the report as compact markdown prose."""
	by_status = {}
	for page in report["pages"]:
		by_status.setdefault(page["status"], []).append(page)
	lines = ["# freshness @ %s" % report["head"], ""]
	for status in DETAIL_STATUSES:
		bucket = by_status.get(status, [])
		if not bucket:
			continue
		lines.append("%s (%d):" % (status, len(bucket)))
		for page in bucket:
			lines.append("- %s `%s`%s" % (page["name"], page["path"], _detail(page)))
		lines.append("")
	clean = {k: v for k, v in report["summary"].items() if k not in DETAIL_STATUSES}
	if clean:
		lines.append("ok: " + ", ".join("%d %s" % (v, k) for k, v in sorted(clean.items())))
	if not report["pages"]:
		lines.append("no pages found")
	return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(description="Detect stale wiki pages (git-only).")
	parser.add_argument("--root", default="docs", help="wiki root directory (default: docs)")
	parser.add_argument("--head", default="HEAD", help="ref representing current state (default: HEAD)")
	parser.add_argument("--quiet", action="store_true", help="print nothing; rely on the exit code only")
	args = parser.parse_args(argv)

	if not os.path.isdir(args.root):
		sys.stderr.write("freshness: root not found: %s\n" % args.root)
		return 2

	report = analyze(args.root, args.head)
	if not args.quiet:
		sys.stdout.write(render(report))

	summary = report["summary"]
	gating = summary.get("stale", 0) + summary.get("orphaned-source", 0) + summary.get("unverified", 0)
	return 1 if gating else 0


if __name__ == "__main__":
	sys.exit(main())
