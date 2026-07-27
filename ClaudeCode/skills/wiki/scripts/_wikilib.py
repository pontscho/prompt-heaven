"""Shared helpers for the p:wiki scripts.

stdlib-only, Python 3.9+. No third-party dependencies, no LLM calls.
Implements a deliberately minimal frontmatter parser covering only the subset
documented in SCHEMA.md section 5:
  - top-level `key: scalar`
  - one level of nesting (block `key:` then indented `subkey: value`)
  - block lists (`key:` then indented `- item`)
  - inline lists (`key: [a, b]`)
Full-line `#` comments and blank lines are ignored. Indentation is spaces only.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Files and directories that are not wiki pages.
SKIP_FILES = {"INDEX.md", "SCHEMA.md"}
SKIP_DIRS = {"sources", "plans", ".git", ".claude", ".cache"}

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def git(args: List[str], cwd: str) -> Tuple[int, str, str]:
	"""Run a git command; return (returncode, stdout, stderr)."""
	try:
		proc = subprocess.run(
			["git"] + args, cwd=cwd,
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
	"""Split a document into (frontmatter_block, body).

	Returns an empty frontmatter block if the document does not start with '---'.
	"""
	lines = text.splitlines()
	if not lines or lines[0].strip() != "---":
		return "", text
	for i in range(1, len(lines)):
		if lines[i].strip() == "---":
			return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
	return "", text


def _parse_scalar(raw: str) -> Any:
	raw = raw.strip()
	if raw.startswith("[") and raw.endswith("]"):
		inner = raw[1:-1].strip()
		if not inner:
			return []
		return [_unquote(x.strip()) for x in inner.split(",") if x.strip()]
	return _unquote(raw)


def _unquote(raw: str) -> str:
	if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
		return raw[1:-1]
	return raw


def _collect_block(lines: List[str], start: int) -> Tuple[Any, int]:
	"""Collect an indented block (list or dict) beginning at index `start`.

	Returns (value, consumed_line_count). value is a list or a dict depending on
	the first meaningful indented line encountered.
	"""
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
	"""Yield (relpath_from_root, frontmatter, body) for every wiki page.

	Skips INDEX.md, SCHEMA.md, and the raw `sources/` layer.
	"""
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
