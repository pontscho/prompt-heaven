#!/usr/bin/env python3
"""validate.py -- structural/format validator for data + Python files (stdlib-first).

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

Coverage. No pip dependencies: every format below is validated with the Python 3.9
standard library alone, EXCEPT javascript -- nothing in the stdlib parses
JavaScript, so that one runs the external `node` BINARY via `subprocess` (stdlib
itself; the binary is not, and its absence FAILS rather than skips).
    json   -> json                full parse
    python -> compile()           syntax + symtable, IN MEMORY (never py_compile,
                                  which would write __pycache__/*.pyc)
    xml    -> xml.parsers.expat   well-formedness (DTD/external-entity guarded)
    ini    -> configparser        full parse (INI flavour)
    csv    -> csv                 row/column-count consistency
    tsv    -> csv                 as csv, tab delimiter
    plist  -> plistlib            full parse (binary + XML plist)
    toml   -> tomllib (3.11+) or third-party `tomli`; SKIPPED if neither present
    yaml   -> PyYAML if importable (full); else a stdlib STRUCTURAL PRE-CHECK
              (UTF-8, no tab indentation, balanced flow collections) that is
              explicitly NOT a full parse -- install PyYAML for real validation.
    javascript -> `node --check`  SYNTAX ONLY, and the code is never executed.
                                  .js/.mjs/.cjs -- NOT .jsx/.ts/.tsx (node parses
                                  neither JSX nor TypeScript). FAILED, not
                                  skipped, when `node` is not in PATH.

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
	".py": "python", ".pyi": "python",
	# .js/.mjs/.cjs ONLY. `node --check` parses neither JSX nor TypeScript, so
	# .jsx/.ts/.tsx/.mts/.cts stay unmapped on purpose: an honest "unknown format"
	# SKIP beats a bogus FAIL on a file that is perfectly fine.
	".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
}

# Same set the mcp-inspect server exposes, so `--list-formats` and that server's
# function list cannot drift apart.
KNOWN_FORMATS = ["json", "python", "yaml", "toml", "xml", "ini", "csv", "tsv",
	"plist", "javascript"]


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


def v_python(data: bytes) -> Result:
	import warnings

	try:
		with warnings.catch_warnings(record=True) as caught:
			# Invalid escape sequences, `assert (x, y)`, `is` with a literal:
			# real defects that a bare parse never surfaces.
			warnings.simplefilter("always", SyntaxWarning)
			# compile() IN MEMORY -- deliberately not py_compile, which writes
			# __pycache__/*.pyc as a side effect, and deliberately stronger than
			# ast.parse: the symtable/codegen pass also rejects `break` outside a
			# loop, `return` outside a function and module-level `nonlocal`.
			# Compile the BYTES, never a decoded str: this way the PEP-263 coding
			# cookie (`# -*- coding: latin-1 -*-`) and a UTF-8 BOM are honoured by
			# the tokenizer exactly as the interpreter would -- decoding first
			# would report a correctly-declared non-UTF-8 source as invalid.
			compile(data, "<validate>", "exec", dont_inherit=True)
	except SyntaxError as e:
		return fail(e.msg or "syntax error", line=e.lineno, col=e.offset)
	except Exception as e:  # null bytes, recursion limit, ...
		return fail("%s: %s" % (type(e).__name__, e))
	ver = "%d.%d" % sys.version_info[:2]
	warned = [w for w in caught if issubclass(w.category, SyntaxWarning)]
	if warned:
		first = warned[0]
		return ok("compiles on Python %s but emits %d SyntaxWarning(s); "
			"first: %s (line %s)"
			% (ver, len(warned), first.message, first.lineno))
	return ok("valid Python syntax (compiled on %s)" % ver)


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


# --- JavaScript: the one format with no stdlib parser (external `node`) -------

def _run_node(argv: List[str], stdin_text: Optional[str] = None):
	"""(returncode, stderr) for a fixed argv. Never raises. shell=False.

	`stdin=` / `input=` is always passed explicitly: the two are mutually
	exclusive in subprocess.run, so the choice is spelled out per branch rather
	than assembled as kwargs.
	"""
	import subprocess

	try:
		if stdin_text is None:
			r = subprocess.run(argv, capture_output=True, text=True, timeout=15,
				stdin=subprocess.DEVNULL)
		else:
			r = subprocess.run(argv, capture_output=True, text=True, timeout=15,
				input=stdin_text)
		return r.returncode, r.stderr or ""
	except FileNotFoundError:
		return 127, "%s: not found in PATH" % argv[0]
	except subprocess.TimeoutExpired:
		return 124, "%s: timed out after 15s" % argv[0]
	except OSError as e:
		return 1, "%s: %s" % (argv[0], e)


def _node_error(err: str, target: str, rc: int) -> Result:
	"""node's stderr -> a FAIL result. Its stack trace is dropped, not reported.

	node prints `<file>:<line>`, the offending source line, a caret line, then
	`SyntaxError: ...` and eight frames of its own loader. Only the position and
	the one-line message say anything about the validated file.
	"""
	import re

	if rc == 127:  # node vanished between which() and the spawn
		return fail(" ".join(err.split()) or "node: not found in PATH")
	if rc == 124:  # timed out, so nothing was actually checked
		return limited(" ".join(err.split()) or "node --check timed out")
	lines = err.splitlines()
	line = col = None
	# matched on the basename so a realpath'd target still lands, and anchored so
	# a `(node:123) Warning:` preamble cannot be mistaken for the header
	head = re.compile(r"^.*" + re.escape(os.path.basename(target)) + r":(\d+)$")
	for i, ln in enumerate(lines):
		m = head.match(ln)
		if not m:
			continue
		line = int(m.group(1))
		for cand in lines[i + 1:i + 4]:
			if "^" in cand and not cand.strip("^ "):  # spaces + carets only
				col = cand.index("^") + 1
				break
		break
	msg = ""
	for ln in lines:
		if re.match(r"^\w*Error\b", ln):  # SyntaxError, and nothing indented
			msg = " ".join(ln.split())
			break
	if not msg:
		msg = next((" ".join(ln.split()) for ln in lines if ln.strip()),
			"node --check failed (rc=%d)" % rc)
	return fail(msg, line=line, col=col)


def v_javascript(data: bytes, path: Optional[str] = None) -> Result:
	"""`node --check` -- a PARSE, and never an execution.

	The one validator that shells out, because no stdlib module parses
	JavaScript. `--check` only: `node -e`/`--eval`/`-p`, or requiring/importing
	the file, would RUN it, so none of them appear here -- and no temp file is
	written for stdin input either.

	A missing `node` is a FAIL, not the SKIP a missing tomllib/PyYAML gets. The
	asymmetry is deliberate: those are optional PARSERS whose absence is a
	property of this interpreter, and a SKIP still exits 0. Here the caller asked
	whether a JS file parses and got no answer at all, so a skip would read as
	success. The mcp-inspect server draws the same line for the same reason.
	"""
	import shutil

	node = shutil.which("node")
	if node is None:
		return fail("no `node` in PATH (install Node.js to validate JavaScript)")
	if path is not None:
		# node decides script-vs-module ITSELF -- extension, nearest package.json
		# "type", and on newer node its own syntax detection. Not re-implemented.
		target = os.path.abspath(path)
		rc, err = _run_node([node, "--check", target])
		if rc == 0:
			return ok("valid JavaScript syntax (node --check)")
		return _node_error(err, target, rc)
	# stdin, so there is no extension to decide from: try the module goal and
	# then the script goal -- text that parses under EITHER is valid JavaScript.
	try:
		text = decode_text(data)
	except UnicodeDecodeError as e:
		return fail("not valid UTF-8: %s" % e)
	first = None
	for goal, argv in (("ES module", [node, "--input-type=module", "--check"]),
			("CommonJS", [node, "--check"])):
		rc, err = _run_node(argv, text)
		if rc == 0:
			return ok("valid JavaScript syntax (parsed as %s)" % goal)
		if first is None:
			first = _node_error(err, "[stdin]", rc)  # the module goal's: precise
	return first


VALIDATORS = {
	"json": v_json,
	"python": v_python,
	"xml": v_xml,
	"ini": v_ini,
	"csv": v_csv,
	"tsv": v_tsv,
	"plist": v_plist,
	"toml": v_toml,
	"yaml": v_yaml,
	"javascript": v_javascript,
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
	if resolved == "javascript":
		# The only validator that needs the PATH and not just the bytes: node
		# reads the extension and the nearest package.json to pick script vs
		# module. Stdin ('-') has neither, and node --check reads stdin too.
		return v_javascript(data, None if path == "-" else path)
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
