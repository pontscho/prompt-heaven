#!/usr/bin/env python3
"""validate.py -- structural/format validator for structured-data files (stdlib-first).

Answers ONE question per file: "is this *formally* well-formed for its format?"
i.e. does it parse. This is NOT schema/semantic validation -- it does not check
that a JSON has the right keys or a YAML matches an expected shape, only that the
bytes are a valid document of that format.

Usage:
    python scripts/validate.py FILE [FILE ...]
    python scripts/validate.py --format json -          # read stdin
    python scripts/validate.py --format yaml a.txt       # force a format
    python scripts/validate.py --strict config.yaml      # LIMITED/SKIP also fail
    python scripts/validate.py --list-formats

Format is auto-detected from the extension; override with --format. Use '-' as a
path to read from stdin (then --format is required).

Coverage with the Python 3.9 standard library ONLY:
    json   -> json                full parse
    xml    -> xml.parsers.expat   well-formedness (DTD/external-entity guarded)
    ini    -> configparser        full parse (INI flavour)
    csv    -> csv                 row/column-count consistency
    tsv    -> csv                 as csv, tab delimiter
    plist  -> plistlib            full parse (binary + XML plist)
    toml   -> tomllib (3.11+) or third-party `tomli`; SKIPPED if neither present
    yaml   -> PyYAML if importable (full); else a stdlib STRUCTURAL PRE-CHECK
              (UTF-8, no tab indentation, balanced flow collections) that is
              explicitly NOT a full parse -- install PyYAML for real validation.

Output: one line per file. Exit code 0 when nothing FAILED; non-zero if any file
FAILED (and, with --strict, if any file was LIMITED or SKIPPED). Usable as a
pre-commit / CI gate.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import io
import json
import os
import plistlib
import sys
from typing import List, Optional

# --- result model ----------------------------------------------------------

OK = "OK"
FAIL = "FAIL"
LIMITED = "LIMITED"
SKIP = "SKIP"


class Result:
	def __init__(self, status: str, message: str,
			line: Optional[int] = None, col: Optional[int] = None):
		self.status = status
		self.message = message
		self.line = line
		self.col = col


def ok(msg: str) -> Result:
	return Result(OK, msg)


def fail(msg: str, line: Optional[int] = None, col: Optional[int] = None) -> Result:
	return Result(FAIL, msg, line, col)


def limited(msg: str, line: Optional[int] = None) -> Result:
	# A LIMITED result with a line number means the pre-check found a real defect.
	return Result(LIMITED, msg, line)


def skipped(msg: str) -> Result:
	return Result(SKIP, msg)


# --- format detection -------------------------------------------------------

EXT_MAP = {
	".json": "json",
	".yaml": "yaml", ".yml": "yaml",
	".toml": "toml",
	".xml": "xml", ".svg": "xml", ".xsd": "xml", ".rss": "xml", ".plist": "plist",
	".ini": "ini", ".cfg": "ini",
	".csv": "csv",
	".tsv": "tsv",
}

KNOWN_FORMATS = ["json", "yaml", "toml", "xml", "ini", "csv", "tsv", "plist"]


def detect_format(path: str) -> Optional[str]:
	ext = os.path.splitext(path)[1].lower()
	return EXT_MAP.get(ext)


# --- text decoding ----------------------------------------------------------

def decode_text(data: bytes) -> str:
	# utf-8-sig tolerates (and strips) a leading BOM, which is common and legal
	# for these formats and otherwise trips up parsers.
	return data.decode("utf-8-sig")


# --- per-format validators --------------------------------------------------

def v_json(data: bytes) -> Result:
	try:
		text = decode_text(data)
	except UnicodeDecodeError as e:
		return fail("not valid UTF-8: %s" % e)
	try:
		json.loads(text)
	except json.JSONDecodeError as e:
		return fail(e.msg, line=e.lineno, col=e.colno)
	return ok("valid JSON")


def v_xml(data: bytes) -> Result:
	import xml.parsers.expat as expat

	p = expat.ParserCreate()

	def _block_entity_decl(name, is_param, value, base, system_id, public_id, notation):
		# An entity with no inline value but a SYSTEM/PUBLIC id is external (XXE);
		# one with an inline value is internal (the billion-laughs vector).
		if value is None and (system_id is not None or public_id is not None):
			raise ValueError("external entity declaration not allowed (XXE guard)")
		raise ValueError("entity declaration not allowed (entity-expansion guard)")

	def _block_external(*_a, **_k):
		raise ValueError("external entity reference not allowed (XXE guard)")

	# Block the XXE / entity-expansion vectors while still accepting a plain
	# DOCTYPE. This keeps the validator safe on untrusted input. expat fires the
	# entity-declaration handler before any reference, so both vectors are caught
	# at declaration time.
	p.EntityDeclHandler = _block_entity_decl
	p.ExternalEntityRefHandler = _block_external
	try:
		p.Parse(data, True)
	except expat.ExpatError as e:
		# expat offset is 0-based column.
		return fail(expat.ErrorString(e.code), line=e.lineno, col=e.offset + 1)
	except ValueError as e:
		return fail(str(e))
	return ok("well-formed XML")


def v_ini(data: bytes) -> Result:
	try:
		text = decode_text(data)
	except UnicodeDecodeError as e:
		return fail("not valid UTF-8: %s" % e)
	cp = configparser.ConfigParser(strict=True)
	try:
		cp.read_string(text)
	except configparser.Error as e:
		# configparser messages already carry the line where known.
		return fail(" ".join(str(e).split()))
	return ok("valid INI (%d section(s))" % len(cp.sections()))


def _v_delim(data: bytes, delimiter: str, label: str) -> Result:
	try:
		text = decode_text(data)
	except UnicodeDecodeError as e:
		return fail("not valid UTF-8: %s" % e)
	reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
	expected = None
	rows = 0
	try:
		for i, row in enumerate(reader, 1):
			rows += 1
			if expected is None:
				expected = len(row)
			elif len(row) != expected:
				return fail("inconsistent column count: row %d has %d field(s), "
					"expected %d" % (i, len(row), expected))
	except csv.Error as e:
		return fail("%s parse error: %s" % (label, e))
	if expected is None:
		return ok("empty %s (no rows)" % label)
	return ok("consistent %s (%d row(s) x %d column(s))" % (label, rows, expected))


def v_csv(data: bytes) -> Result:
	return _v_delim(data, ",", "CSV")


def v_tsv(data: bytes) -> Result:
	return _v_delim(data, "\t", "TSV")


def v_plist(data: bytes) -> Result:
	try:
		plistlib.loads(data)
	except Exception as e:  # plistlib raises a grab-bag of exception types
		return fail("%s: %s" % (type(e).__name__, e))
	return ok("valid plist")


def v_toml(data: bytes) -> Result:
	loads = None
	for modname in ("tomllib", "tomli"):  # tomllib is 3.11+, tomli is the backport
		try:
			mod = __import__(modname)
			loads = mod.loads
			break
		except ImportError:
			continue
	if loads is None:
		return skipped("no TOML parser available (tomllib is Python 3.11+; "
			"`pip install tomli` to validate TOML on 3.9/3.10)")
	try:
		text = decode_text(data)
	except UnicodeDecodeError as e:
		return fail("not valid UTF-8: %s" % e)
	try:
		loads(text)
	except Exception as e:
		return fail("%s: %s" % (type(e).__name__, " ".join(str(e).split())))
	return ok("valid TOML")


# --- YAML: PyYAML if present, else a conservative stdlib pre-check -----------

def v_yaml(data: bytes) -> Result:
	try:
		text = decode_text(data)
	except UnicodeDecodeError as e:
		return fail("not valid UTF-8: %s" % e)
	try:
		import yaml  # PyYAML -- NOT stdlib
	except ImportError:
		return _yaml_precheck(text)
	try:
		# Validate every document in the stream; safe_load_all rejects arbitrary
		# Python object construction.
		for _ in yaml.safe_load_all(text):
			pass
	except yaml.YAMLError as e:
		mark = getattr(e, "problem_mark", None)
		line = (mark.line + 1) if mark is not None else None
		col = (mark.column + 1) if mark is not None else None
		first = str(e).splitlines()[0] if str(e) else "YAML error"
		return fail(first, line=line, col=col)
	return ok("valid YAML (PyYAML safe_load)")


def _line_has_block_scalar(line: str) -> bool:
	# A block scalar indicator (| or >, optionally with +/-/digits) in value
	# position, or as a bare value. Presence means we cannot reliably balance
	# flow collections, so we skip that check rather than risk a false positive.
	s = line.rstrip()
	if not s:
		return False
	# strip a trailing comment that starts after whitespace
	for sep in (": ", ":\t"):
		idx = s.find(sep)
		if idx != -1:
			val = s[idx + len(sep):].strip()
			if val[:1] in ("|", ">"):
				return True
	if s.endswith("|") or s.endswith(">"):
		return True
	body = s.lstrip()
	if body[:1] in ("|", ">") and (len(body) == 1 or body[1] in "+-0123456789 "):
		return True
	return False


def _flow_balance(text: str):
	"""Quote/comment-aware balance check for flow collections [] {}.

	Returns None when balanced, else (message, line). Conservative by design:
	when in doubt it under-reports (false negatives) rather than flagging valid
	YAML (false positives).
	"""
	stack = []  # (opener_char, line)
	close_to_open = {"]": "[", "}": "{"}
	in_squote = in_dquote = False
	line = 1
	i = 0
	n = len(text)
	prev = "\n"
	while i < n:
		c = text[i]
		if c == "\n":
			line += 1
			prev = "\n"
			i += 1
			continue
		if in_squote:
			if c == "'":
				if i + 1 < n and text[i + 1] == "'":  # '' = escaped quote
					i += 2
					prev = "'"
					continue
				in_squote = False
			prev = c
			i += 1
			continue
		if in_dquote:
			if c == "\\":
				i += 2
				prev = c
				continue
			if c == '"':
				in_dquote = False
			prev = c
			i += 1
			continue
		# A '#' starts a comment only at line start or after whitespace (YAML rule).
		if c == "#" and prev in " \t\n":
			while i < n and text[i] != "\n":
				i += 1
			continue
		if c == "'":
			in_squote = True
		elif c == '"':
			in_dquote = True
		elif c in ("[", "{"):
			stack.append((c, line))
		elif c in ("]", "}"):
			if not stack:
				return ("unbalanced flow collection: stray '%s'" % c, line)
			opener, _ = stack.pop()
			if opener != close_to_open[c]:
				return ("mismatched flow collection: '%s' closed by '%s'"
					% (opener, c), line)
		prev = c
		i += 1
	if in_squote or in_dquote:
		return ("unterminated quoted string", line)
	if stack:
		opener, oline = stack[-1]
		return ("unclosed flow collection '%s'" % opener, oline)
	return None


def _yaml_precheck(text: str) -> Result:
	lines = text.splitlines()
	for i, ln in enumerate(lines, 1):
		stripped = ln.lstrip(" \t")
		indent = ln[:len(ln) - len(stripped)]
		if "\t" in indent:
			return limited("tab character in indentation (YAML forbids tabs for "
				"indentation) [stdlib pre-check; no PyYAML]", line=i)
	checks = ["UTF-8 decodes", "no tab indentation"]
	has_block_scalar = any(_line_has_block_scalar(ln) for ln in lines)
	if not has_block_scalar:
		try:
			problem = _flow_balance(text)
		except Exception:
			problem = None  # never let the heuristic crash a validation run
		if problem is not None:
			return limited("%s [stdlib pre-check; no PyYAML]" % problem[0],
				line=problem[1])
		checks.append("balanced flow collections")
	return limited("structural pre-check passed (%s) -- NOT a full parse; "
		"install PyYAML for real YAML validation" % ", ".join(checks))


VALIDATORS = {
	"json": v_json,
	"xml": v_xml,
	"ini": v_ini,
	"csv": v_csv,
	"tsv": v_tsv,
	"plist": v_plist,
	"toml": v_toml,
	"yaml": v_yaml,
}


# --- driver -----------------------------------------------------------------

def read_bytes(path: str) -> bytes:
	if path == "-":
		return sys.stdin.buffer.read()
	with open(path, "rb") as f:
		return f.read()


def validate_one(path: str, fmt: Optional[str]) -> Result:
	resolved = fmt or detect_format(path)
	if resolved is None:
		return skipped("unknown format for %r (pass --format)"
			% os.path.splitext(path)[1])
	if resolved not in VALIDATORS:
		return fail("unsupported format: %s (known: %s)"
			% (resolved, ", ".join(KNOWN_FORMATS)))
	try:
		data = read_bytes(path)
	except OSError as e:
		return fail("cannot read: %s" % e)
	return VALIDATORS[resolved](data)


def format_line(path: str, fmt: Optional[str], r: Result) -> str:
	loc = ""
	if r.line is not None:
		loc = ":%d" % r.line
		if r.col is not None:
			loc += ":%d" % r.col
	tag = "%-7s" % r.status
	label = "%s [%s]" % (path, fmt or detect_format(path) or "?")
	return "%s %s%s -- %s" % (tag, label, loc, r.message)


def main(argv: Optional[List[str]] = None) -> int:
	ap = argparse.ArgumentParser(
		description="Validate structured-data files for formal/structural "
			"correctness (stdlib-first).")
	ap.add_argument("files", nargs="*", help="files to validate ('-' = stdin)")
	ap.add_argument("--format", choices=KNOWN_FORMATS,
		help="force a format instead of detecting from the extension")
	ap.add_argument("--strict", action="store_true",
		help="treat LIMITED and SKIP results as failures too")
	ap.add_argument("--quiet", action="store_true",
		help="print only failing (and, with --strict, limited/skipped) lines")
	ap.add_argument("--list-formats", action="store_true",
		help="list supported formats and exit")
	args = ap.parse_args(argv)

	if args.list_formats:
		for f in KNOWN_FORMATS:
			print(f)
		return 0
	if not args.files:
		ap.error("no files given (use '-' for stdin)")
	if "-" in args.files and not args.format:
		ap.error("--format is required when reading from stdin ('-')")

	counts = {OK: 0, FAIL: 0, LIMITED: 0, SKIP: 0}
	worst = 0
	for path in args.files:
		r = validate_one(path, args.format)
		counts[r.status] += 1
		failing = r.status == FAIL or (args.strict and r.status in (LIMITED, SKIP))
		if failing:
			worst = 1
		if not args.quiet or failing:
			print(format_line(path, args.format, r))

	summary = "%d OK, %d FAILED, %d LIMITED, %d SKIPPED" % (
		counts[OK], counts[FAIL], counts[LIMITED], counts[SKIP])
	print("--- %s ---" % summary, file=sys.stderr)
	return worst


if __name__ == "__main__":
	sys.exit(main())
