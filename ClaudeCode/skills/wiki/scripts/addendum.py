#!/usr/bin/env python3
"""addendum.py -- append one dated addendum to an accepted ADR.

Deterministic, stdlib-only, Python 3.9+, no LLM. An accepted ADR is
append-only (p:wiki schema §2), so the one legal write is a dated addendum at
its end. This script can make that write and no other: it never touches the
frontmatter or any existing byte, and it refuses every page that is not an
accepted ADR inside the wiki root.

The title and body travel in a STAGED JSON file, never on the shell line (the
p:roadmap staging rule): exactly {"title": "...", "body": "..."}. The title is
one line with no backtick and no control character. The body is markdown and
may span lines, but no line may be a level 1-2 heading -- ATX (`#`, `##`) or a
setext underline -- because that would open a sibling of the addendum instead
of a part of it; `###` and deeper are fine. Blank lines at its edges are
dropped. A `#` line inside a code fence is refused too: the check is per line.

Usage:
    addendum.py --page docs/adr/0009-slug.md --item-file STAGED.json [--today YYYY-MM-DD]

The page must be an existing regular file (not a symlink) under
<git top-level of the cwd>/docs, with frontmatter `type: adr` and
`status: active`. A `draft` ADR is refused: edit the draft directly.

Appends `## Addendum (<date>): <title>`, a blank line, the body and one final
newline, with exactly one blank line before the heading (a missing final
newline is supplied; an existing byte is never removed). The same heading
already in the page is refused, so a double run cannot append twice. The write
is a temp file in the page's directory + os.replace, keeping the file mode.

stdout on success: one line, the page path relative to the git top-level.
Every refusal: exit 2, one line on stderr, the page untouched.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wikilib as w  # noqa: E402

ITEM_KEYS = ("title", "body")
TMP_PREFIX = ".addendum-"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_ATX_RE = re.compile(r" {0,3}#{1,2}(?:[ \t].*)?", re.ASCII)   # level 1-2 ATX heading
_SETEXT_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*", re.ASCII)   # underline under a text line
_BAD_CATEGORIES = ("Cc", "Zl", "Zp", "Cs")   # controls, U+2028/9, lone surrogates


class Refusal(Exception):
	"""One stderr line; nothing has been written."""


def _bad_chars(text: str, allowed: str = "") -> bool:
	return any(unicodedata.category(c) in _BAD_CATEGORIES and c not in allowed for c in text)


def parse_today(value):
	if value is None:
		return datetime.date.today().isoformat()
	try:
		if not _DATE_RE.fullmatch(value):
			raise ValueError
		datetime.date.fromisoformat(value)
	except ValueError:
		raise Refusal("--today must be a real YYYY-MM-DD date, got %r" % value)
	return value


def resolve_page(page):
	"""(git top-level, real path of the page), or a Refusal."""
	root = os.path.realpath(w.repo_root())
	wiki = os.path.realpath(os.path.join(root, "docs"))
	if not os.path.isdir(wiki):
		raise Refusal("no wiki root at %s" % wiki)
	if os.path.islink(page):
		raise Refusal("%s is a symlink -- name the ADR file itself" % page)
	real = os.path.realpath(page)
	if not real.startswith(wiki + os.sep):
		raise Refusal("%s is not inside the wiki root %s" % (page, wiki))
	if not os.path.isfile(real):
		raise Refusal("%s is not an existing file" % page)
	return root, real


def read_accepted_adr(path, shown):
	"""The page's bytes, if it is an accepted ADR."""
	with open(path, "rb") as fh:
		data = fh.read()
	try:
		fm = w.parse_frontmatter(data.decode("utf-8"))
	except UnicodeDecodeError:
		raise Refusal("%s is not UTF-8" % shown)
	if fm.get("type") != "adr":
		raise Refusal("%s is not an ADR (type: %s)" % (shown, fm.get("type") or "missing"))
	if fm.get("status") == "draft":
		raise Refusal("%s is a draft ADR -- edit the draft directly; an addendum is for an accepted one" % shown)
	if fm.get("status") != "active":
		raise Refusal("%s has status %r, not active (accepted)" % (shown, fm.get("status")))
	return data


def _no_duplicate_keys(pairs):
	keys = [k for k, _ in pairs]
	dup = sorted({k for k in keys if keys.count(k) > 1})
	if dup:
		raise Refusal("item file repeats key(s): %s" % ", ".join(dup))
	return dict(pairs)


def load_item(path):
	"""(title, body) from the staged JSON item file, validated."""
	try:
		with open(path, "r", encoding="utf-8") as fh:
			item = json.loads(fh.read(), object_pairs_hook=_no_duplicate_keys)
	except (OSError, UnicodeDecodeError) as exc:
		raise Refusal("cannot read item file %s: %s" % (path, exc))
	except ValueError as exc:
		raise Refusal("item file %s is not valid JSON: %s" % (path, exc))
	if not isinstance(item, dict):
		raise Refusal("item file must hold one JSON object")
	unknown, missing = sorted(set(item) - set(ITEM_KEYS)), [k for k in ITEM_KEYS if k not in item]
	if unknown or missing:
		raise Refusal("item file keys must be exactly title, body (unknown: %s; missing: %s)"
			% (", ".join(unknown) or "-", ", ".join(missing) or "-"))
	title, body = item["title"], item["body"]
	if not isinstance(title, str) or not isinstance(body, str):
		raise Refusal("title and body must be strings")
	title = title.strip()
	if not title or "`" in title or _bad_chars(title):
		raise Refusal("title must be one non-empty line with no backtick and no control character")
	if _bad_chars(body, "\n\t"):
		raise Refusal("body may not contain a control character other than newline and tab")
	lines = body.split("\n")
	while lines and not lines[0].strip():
		lines.pop(0)
	while lines and not lines[-1].strip():
		lines.pop()
	if not lines:
		raise Refusal("body is empty")
	for i, line in enumerate(lines):
		if _ATX_RE.fullmatch(line) or (i and lines[i - 1].strip() and _SETEXT_RE.fullmatch(line)):
			raise Refusal("body line %d is a level 1-2 heading -- it would forge a sibling section; use ### or deeper" % (i + 1))
	return title, "\n".join(lines)


def write_atomic(path, data):
	"""Temp file beside the page, fsync, the page's mode, then os.replace."""
	mode = os.stat(path).st_mode & 0o7777
	handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=TMP_PREFIX, suffix=".tmp")
	try:
		with os.fdopen(handle, "wb") as stream:
			stream.write(data)
			stream.flush()
			os.fsync(stream.fileno())
		os.chmod(tmp, mode)   # mkstemp is 0600; the page is not
		os.replace(tmp, path)
	except BaseException:
		try:
			os.unlink(tmp)
		except OSError:
			pass
		raise


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(description="Append one dated addendum to an accepted ADR.")
	parser.add_argument("--page", required=True, help="the ADR page, under <git top-level>/docs")
	parser.add_argument("--item-file", required=True, help="staged JSON: {\"title\": ..., \"body\": ...}")
	parser.add_argument("--today", help="YYYY-MM-DD; pins the heading date (tests)")
	args = parser.parse_args(argv)
	try:
		date = parse_today(args.today)
		root, path = resolve_page(args.page)
		old = read_accepted_adr(path, args.page)
		title, body = load_item(args.item_file)
		heading = "## Addendum (%s): %s" % (date, title)
		if heading in old.decode("utf-8").splitlines():
			raise Refusal("%s already has %r -- not appended twice" % (args.page, heading))
		sep = b"\n" * max(0, 2 - (len(old) - len(old.rstrip(b"\n"))))
		write_atomic(path, old + sep + ("%s\n\n%s\n" % (heading, body)).encode("utf-8"))
	except (Refusal, OSError) as exc:
		sys.stderr.write("addendum: %s\n" % " ".join(str(exc).splitlines()))
		return 2
	sys.stdout.write(os.path.relpath(path, root).replace(os.sep, "/") + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main())
