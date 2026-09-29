#!/usr/bin/env python3
"""freshness.py -- read-only staleness detector for the p:wiki docs tree.

Determines which wiki pages may be out of date by comparing each page's
`verified.commit` against the current tree, using git only. No LLM, no code
navigation: this is the cheap pre-filter that tells the LLM lint pass *which*
pages to look at. Symbol-level checks (broken/drifted anchors) are NOT done here
-- they require the language MCP servers.

Usage:
    python scripts/freshness.py --root docs [--head HEAD] [--quiet]

Prints a compact prose report on stdout: actionable pages (stale /
orphaned-source / unverified / promotable) are listed in detail; everything else
is summarized as counts, followed by a `gating:` and an `advisory:` line.

The exit code gates only on what this script can PROVE (docs/adr/0019): it is
non-zero iff a page is `orphaned-source` -- a `sources:` path gone from the tree,
which is a filesystem fact and the same broken anchor the server's `verify`
gates. `stale` and `unverified` are git lag, a measurement rather than a verdict:
listed, counted on the `advisory:` line, never an exit code -- for every
editorial `status:`. Symbol anchors, body anchors and measured regions are
verified ONLY by `wiki_call verify` (Scripts/mcp-wiki.py); this script does not
repeat that verifier, so a clean exit here is a subset of the server's verdict.

Forward `spec` pages carry `targets:` (intended-but-unbuilt code anchors, the
forward pair of `sources:`). Two non-gating statuses describe them:
  * `planned`     -- has `targets:`, no `sources:`, no target materialized yet.
  * `promotable`  -- a `targets:` path now exists on disk; promote it to
                     `sources:`. Precedence: gating > promotable > current, so a
                     materialized target never masks stale/unverified sources.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wikilib as w  # noqa: E402

# Statuses that are listed page-by-page; everything else is summarized as a count.
# `promotable` is actionable (a forward target materialized) so it is detailed;
# `planned` stays a summarized count.
DETAIL_STATUSES = ["stale", "orphaned-source", "unverified", "promotable"]
# The verdict (adr 0019, half one). `orphaned-source` is the one provable class
# this script can reach; the server's `gating:` line counts it too, as a broken
# anchor found by `verify`. Git lag is ADVISORY_STATUSES, in the order the
# advisory line names them -- the same pair, in the same order, as the server's.
GATING_STATUSES = ("orphaned-source",)
ADVISORY_STATUSES = ("stale", "unverified")

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


def _source_path(source: str, repo=None) -> str:
	"""Return the filesystem path part of an anchor (`path` or `path:symbol`).

	Repo path segments can themselves contain ':' (this repo's `p:<name>` skill/
	agent naming), which collides with the symbol separator. When `repo` is
	given, return the LONGEST colon-prefix that exists on disk -- so both a
	`p:<name>` dir in the path and a trailing `:symbol` are handled. Without
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


def _evaluate(sources, changed, repo):
	"""Classify a page's sources against the changed-file set.
	Returns (changed_sources, missing_sources)."""
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
		targets = w.as_list(fm.get("targets"))
		materialized = [t for t in targets
			if os.path.exists(os.path.join(repo, _source_path(t, repo)))]
		verified = fm.get("verified") if isinstance(fm.get("verified"), dict) else {}
		commit = (verified or {}).get("commit")

		if not sources:
			# Forward / sourceless branch: promotable/planned dominates here --
			# with no sources there is nothing to gate on, so a materialized
			# target is the most actionable signal we can give.
			if materialized:
				pages.append({"name": name, "path": relpath, "type": typ,
					"status": "promotable", "materialized": materialized})
			elif targets:
				pages.append({"name": name, "path": relpath, "type": typ,
					"status": "planned"})
			else:
				status = "untracked" if typ in w.UNTRACKED_TYPES else "no-sources"
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
			# Sources are current AND a target has materialized -> promotable.
			# Reached only after the gating checks above, so gating always wins
			# (H1 precedence): a materialized target never masks stale sources.
			pages.append({"name": name, "path": relpath, "type": typ,
				"status": "promotable", "materialized": materialized,
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
	if status == "promotable":
		return " — materialized: %s (promote targets→sources)" % ", ".join(
			page.get("materialized", []))
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
	lines.append("gating: %d (%s; symbol anchors, body anchors and measured regions "
		"are checked only by wiki_call verify)"
		% (gating_count(report), " + ".join(GATING_STATUSES)))
	moved, unchecked = (report["summary"].get(s, 0) for s in ADVISORY_STATUSES)
	if moved or unchecked:
		# The server's advisory sentence, so the two reports read the same.
		lines.append(
			"advisory: %d page(s) list a source git says moved since they were "
			"verified, %d cannot be compared at all — git lag is a MEASUREMENT, "
			"not a verdict: it cannot tell a moved comma from a reversed "
			"decision. A human read may be owed; nothing here is claimed wrong."
			% (moved, unchecked))
	if not report["pages"]:
		lines.append("no pages found")
	return "\n".join(lines).rstrip() + "\n"


def gating_count(report) -> int:
	"""What the exit code gates on: the provable classes only (adr 0019)."""
	return sum(report["summary"].get(s, 0) for s in GATING_STATUSES)


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(description="Report wiki git lag (advisory) and "
		"exit non-zero only on an orphaned source path (git-only).")
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

	return 1 if gating_count(report) else 0


if __name__ == "__main__":
	sys.exit(main())
