#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""MCP-Forge: Build and test orchestration MCP server.

Single-tool dispatcher pattern: exposes one MCP tool (forge_call) that routes
to internal handler functions via the 'function' parameter.

Reads project-forge.yaml (custom minimal YAML subset, see PARSER section).
Requires only Python 3.9+ stdlib modules.

Usage:
  python3 mcp-forge.py --project-root <path>
                       [--config <yaml-path>]    # default: project-forge.yaml
                       [--debug]
                       [--log-file <path>]       # implies --debug

  --project-root  Required. Project root containing project-forge.yaml.
  --config        Override the YAML descriptor (path is relative to
                  --project-root, or absolute).
"""

import argparse
import asyncio
import difflib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger("mcp-forge")


# ===========================================================================
# Constants and parameter aliases
# ===========================================================================
PARAM_ALIASES = {
	"t": "targets",
	"e": "env",
	"f": "filter",
	"j": "ncpu",
	"ab": "auto_build",
	"k": "kind",
	"type": "kind",
	"name": "target",
	"file": "path",
	"timeout_sec": "timeout",
}

FILTER_ALIASES = {
	"pattern": "grep",
	"regex": "grep",
	"context": "grep_context",
	"invert": "invert_grep",
}

# Truly destructive patterns only. YAML clean targets need `rm -rf build`,
# so we don't blanket-ban rm; we only block root-level deletes and similar.
DANGEROUS_PATTERNS = [
	r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(?:\s|$|\*)',
	r'\bsudo\b',
	r'\bmkfs\b',
	r'\bdd\s+if=',
	r'\b:\(\)\s*\{',
	r'>\s*/dev/sd',
	r'\bshred\b',
	r'\bfdisk\b',
	r'\bparted\b',
	r'\bcurl\b.*\|\s*sh',
	r'\bwget\b.*\|\s*sh',
	r'\breboot\b',
	r'\bshutdown\b',
	r'\binit\s+[0-6]\b',
]

_DANGEROUS_RE = re.compile('|'.join(DANGEROUS_PATTERNS), re.IGNORECASE)

MAX_OUTPUT_BYTES = 50 * 1024 * 1024  # 50 MB cap per command

TOP_LEVEL_KEYS = {"version", "configuration", "build", "test", "clean"}
TARGET_KINDS = ("build", "test", "clean")


# ===========================================================================
# YAML Subset Parser
#
# Supports:
#   - Block mappings (key: value, with nested indented children)
#   - Block sequences (- item) of scalar items
#   - Flow sequences [a, b, c] (scalars only, no nesting)
#   - Flow mappings {k: v, k: v} (scalar -> scalar, no nesting)
#   - Block scalars | (literal) and > (folded), default chomping
#   - Scalars: bare, double-quoted, single-quoted, int, bool, null
#   - Comments: # to end-of-line (outside quoted strings)
#   - Indent: 2 spaces, 4 spaces, or single tab (consistent within file)
#
# Not supported (parse error):
#   - Mixed tab/space indent
#   - Anchors/aliases, document separators, tags, merge keys
#   - Nested flow style
#   - Chomping indicators (|-, |+, >-, >+) or explicit indent (|2)
#   - Block sequences of mappings (- key: value)
# ===========================================================================

class YAMLParseError(Exception):
	"""YAML subset parse error with line/column."""

	def __init__(self, line: int, col: int, msg: str):
		super().__init__(f"parse error at line {line}, col {col}: {msg}")
		self.line = line
		self.col = col
		self.msg = msg


class YAMLParser:
	"""Recursive-descent parser for the project-forge YAML subset."""

	def __init__(self, text: str):
		# Normalise line endings, strip BOM
		if text.startswith('\ufeff'):
			text = text[1:]
		text = text.replace('\r\n', '\n').replace('\r', '\n')
		self.lines = text.split('\n')
		self.idx = 0
		self.indent_unit: Optional[str] = None  # "  ", "    ", or "\t"
		self.indent_is_tab = False

	def parse(self) -> Any:
		self._skip_blank_and_comment()
		if self.idx >= len(self.lines):
			return {}
		# Top-level must be a mapping for project-forge.yaml
		return self._parse_mapping(0)

	# ---- low-level helpers --------------------------------------------------

	def _line_no(self) -> int:
		return self.idx + 1

	def _peek(self) -> Optional[str]:
		if self.idx >= len(self.lines):
			return None
		return self.lines[self.idx]

	def _consume(self) -> None:
		self.idx += 1

	def _skip_blank_and_comment(self) -> None:
		while self.idx < len(self.lines):
			line = self.lines[self.idx]
			stripped = line.strip()
			if not stripped or stripped.startswith('#'):
				self.idx += 1
			else:
				break

	def _measure_indent(self, line: str) -> Tuple[int, str]:
		for i, c in enumerate(line):
			if c not in (' ', '\t'):
				return i, line[:i]
		return len(line), line

	def _set_indent_unit(self, indent_str: str) -> None:
		if self.indent_unit is not None or not indent_str:
			return
		if '\t' in indent_str and ' ' in indent_str:
			raise YAMLParseError(self._line_no(), 1, "mixed tabs and spaces in indent")
		if indent_str.startswith('\t'):
			self.indent_unit = '\t'
			self.indent_is_tab = True
		else:
			if len(indent_str) not in (2, 4):
				raise YAMLParseError(self._line_no(), 1,
					f"indent unit must be 2 or 4 spaces (got {len(indent_str)})")
			self.indent_unit = indent_str
			self.indent_is_tab = False

	def _level_of(self, indent_str: str) -> int:
		if not indent_str:
			return 0
		if self.indent_unit is None:
			self._set_indent_unit(indent_str)
		if self.indent_is_tab:
			if not all(c == '\t' for c in indent_str):
				raise YAMLParseError(self._line_no(), 1,
					"expected tab indent, found spaces or mix")
			return len(indent_str)
		if not all(c == ' ' for c in indent_str):
			raise YAMLParseError(self._line_no(), 1,
				"expected space indent, found tabs or mix")
		unit_len = len(self.indent_unit)  # type: ignore[arg-type]
		if len(indent_str) % unit_len != 0:
			raise YAMLParseError(self._line_no(), 1,
				f"indent width {len(indent_str)} is not a multiple of {unit_len}")
		return len(indent_str) // unit_len

	def _strip_trailing_comment(self, content: str) -> str:
		"""Strip a trailing `# comment`, respecting quoted strings."""
		in_single = False
		in_double = False
		i = 0
		while i < len(content):
			c = content[i]
			if c == '\\' and in_double and i + 1 < len(content):
				i += 2
				continue
			if c == "'" and not in_double:
				in_single = not in_single
			elif c == '"' and not in_single:
				in_double = not in_double
			elif c == '#' and not in_single and not in_double:
				if i == 0 or content[i - 1].isspace():
					return content[:i].rstrip()
			i += 1
		return content.rstrip()

	# ---- block mapping -----------------------------------------------------

	def _parse_mapping(self, expected_level: int) -> Dict[str, Any]:
		result: Dict[str, Any] = {}
		while True:
			self._skip_blank_and_comment()
			line = self._peek()
			if line is None:
				break
			indent_count, indent_str = self._measure_indent(line)
			level = self._level_of(indent_str)
			if level < expected_level:
				break
			if level > expected_level:
				raise YAMLParseError(self._line_no(), indent_count + 1,
					f"unexpected indent level {level}, expected {expected_level}")
			content = self._strip_trailing_comment(line[indent_count:])
			if not content:
				self._consume()
				continue
			if content.startswith('-') and (len(content) == 1 or content[1] == ' '):
				raise YAMLParseError(self._line_no(), indent_count + 1,
					"expected mapping key, got sequence item ('-')")
			key, value_inline = self._split_mapping_line(content, indent_count)
			if key in result:
				raise YAMLParseError(self._line_no(), indent_count + 1,
					f"duplicate mapping key '{key}'")
			self._consume()
			value = self._parse_value(value_inline, expected_level + 1)
			result[key] = value
		return result

	def _split_mapping_line(self, content: str, indent_count: int) -> Tuple[str, str]:
		"""Split `key: value` honouring quoted keys."""
		if content.startswith('"') or content.startswith("'"):
			# Quoted key
			quote = content[0]
			end = 1
			while end < len(content):
				if content[end] == '\\' and quote == '"' and end + 1 < len(content):
					end += 2
					continue
				if content[end] == quote:
					break
				end += 1
			if end >= len(content) or content[end] != quote:
				raise YAMLParseError(self._line_no(), indent_count + 1,
					"unterminated quoted key")
			key_raw = content[:end + 1]
			rest = content[end + 1:].lstrip()
			if not rest.startswith(':'):
				raise YAMLParseError(self._line_no(), indent_count + 1,
					"expected ':' after quoted mapping key")
			value_inline = rest[1:].strip()
			key = self._unquote(key_raw, indent_count + 1)
			return key, value_inline
		# Bare key: find first ':' followed by space or end-of-line
		for i, c in enumerate(content):
			if c == ':' and (i + 1 == len(content) or content[i + 1] in (' ', '\t')):
				key = content[:i].strip()
				value_inline = content[i + 1:].strip()
				if not key:
					raise YAMLParseError(self._line_no(), indent_count + 1,
						"empty mapping key")
				return key, value_inline
			if c == ':' and i + 1 == len(content):
				key = content[:i].strip()
				return key, ""
		# No colon found
		raise YAMLParseError(self._line_no(), indent_count + 1,
			f"expected ':' in mapping line, got {content!r}")

	# ---- value dispatch ----------------------------------------------------

	def _parse_value(self, inline: str, child_level: int) -> Any:
		if not inline:
			# Look at next non-blank line
			self._skip_blank_and_comment()
			line = self._peek()
			if line is None:
				return None
			indent_count, indent_str = self._measure_indent(line)
			level = self._level_of(indent_str)
			if level < child_level:
				return None
			if level > child_level:
				raise YAMLParseError(self._line_no(), indent_count + 1,
					f"unexpected indent level {level}, expected {child_level}")
			content = self._strip_trailing_comment(line[indent_count:])
			if content.startswith('-') and (len(content) == 1 or content[1] == ' '):
				return self._parse_sequence(child_level)
			return self._parse_mapping(child_level)
		if inline == '|' or inline == '>':
			return self._parse_block_scalar(inline, child_level)
		if inline[0] in ('|', '>') and len(inline) > 1:
			raise YAMLParseError(self._line_no(), 1,
				"block scalar chomping/indent indicators are not supported")
		if inline.startswith('['):
			return self._parse_flow_sequence(inline)
		if inline.startswith('{'):
			return self._parse_flow_mapping(inline)
		return self._parse_plain_scalar(inline)

	# ---- block sequence ----------------------------------------------------

	def _parse_sequence(self, expected_level: int) -> List[Any]:
		result: List[Any] = []
		while True:
			self._skip_blank_and_comment()
			line = self._peek()
			if line is None:
				break
			indent_count, indent_str = self._measure_indent(line)
			level = self._level_of(indent_str)
			if level < expected_level:
				break
			if level > expected_level:
				raise YAMLParseError(self._line_no(), indent_count + 1,
					f"unexpected indent in sequence at level {level}")
			content = self._strip_trailing_comment(line[indent_count:])
			if not (content.startswith('-') and (len(content) == 1 or content[1] == ' ')):
				break
			if content == '-':
				item_inline = ""
			else:
				item_inline = content[2:].strip()
			self._consume()
			if not item_inline:
				# Nested value on subsequent lines — only block scalar variant supported
				# (project-forge.yaml never needs sequence-of-mappings)
				self._skip_blank_and_comment()
				next_line = self._peek()
				if next_line is None:
					result.append(None)
					continue
				next_count, next_indent = self._measure_indent(next_line)
				next_level = self._level_of(next_indent)
				if next_level <= expected_level:
					result.append(None)
					continue
				raise YAMLParseError(self._line_no(), next_count + 1,
					"empty '-' item with nested content is not supported "
					"(use '- |' for block scalar or inline value)")
			if item_inline == '|' or item_inline == '>':
				value = self._parse_block_scalar(item_inline, expected_level + 1)
			elif item_inline[0] in ('|', '>') and len(item_inline) > 1:
				raise YAMLParseError(self._line_no(), indent_count + 1,
					"block scalar chomping/indent indicators are not supported")
			elif item_inline.startswith('['):
				value = self._parse_flow_sequence(item_inline)
			elif item_inline.startswith('{'):
				value = self._parse_flow_mapping(item_inline)
			else:
				value = self._parse_plain_scalar(item_inline)
			result.append(value)
		return result

	# ---- block scalar (| literal, > folded) --------------------------------

	def _parse_block_scalar(self, style: str, min_level: int) -> str:
		"""Parse '|' literal or '>' folded block scalar with default chomping."""
		content_lines: List[str] = []
		content_indent: Optional[int] = None
		while self.idx < len(self.lines):
			line = self.lines[self.idx]
			if not line.strip():
				content_lines.append("")
				self.idx += 1
				continue
			count, indent_str = self._measure_indent(line)
			level = self._level_of(indent_str)
			if level < min_level:
				break
			# Establish content indent on first content line
			if content_indent is None:
				content_indent = count
			if count < content_indent:
				break
			content_lines.append(line[content_indent:])
			self.idx += 1
		# Trim trailing blank lines
		while content_lines and not content_lines[-1].strip():
			content_lines.pop()
		if style == '|':
			# Literal: join with newlines (no trailing newline — default chomping = strip)
			return "\n".join(content_lines)
		# Folded: blank line = newline; consecutive non-blank lines joined with space
		folded: List[str] = []
		buf: List[str] = []
		for cl in content_lines:
			if not cl.strip():
				if buf:
					folded.append(" ".join(buf))
					buf = []
				folded.append("")
			else:
				buf.append(cl.strip())
		if buf:
			folded.append(" ".join(buf))
		return "\n".join(folded)

	# ---- flow style --------------------------------------------------------

	def _parse_flow_sequence(self, text: str) -> List[Any]:
		if not text.startswith('['):
			raise YAMLParseError(self._line_no(), 1, "expected '[' in flow sequence")
		if not text.endswith(']'):
			raise YAMLParseError(self._line_no(), len(text),
				"flow sequence must end on same line with ']'")
		inner = text[1:-1].strip()
		if not inner:
			return []
		parts = self._split_flow(inner)
		result = []
		for p in parts:
			p = p.strip()
			if p.startswith('[') or p.startswith('{'):
				raise YAMLParseError(self._line_no(), 1,
					"nested flow style is not supported")
			result.append(self._parse_plain_scalar(p))
		return result

	def _parse_flow_mapping(self, text: str) -> Dict[str, Any]:
		if not text.startswith('{'):
			raise YAMLParseError(self._line_no(), 1, "expected '{' in flow mapping")
		if not text.endswith('}'):
			raise YAMLParseError(self._line_no(), len(text),
				"flow mapping must end on same line with '}'")
		inner = text[1:-1].strip()
		if not inner:
			return {}
		parts = self._split_flow(inner)
		result: Dict[str, Any] = {}
		for p in parts:
			p = p.strip()
			if not p:
				continue
			# Find ':' outside quotes
			key_end = self._find_flow_colon(p)
			if key_end < 0:
				raise YAMLParseError(self._line_no(), 1,
					f"expected ':' in flow mapping entry: {p!r}")
			key_raw = p[:key_end].strip()
			val_raw = p[key_end + 1:].strip()
			if val_raw.startswith('[') or val_raw.startswith('{'):
				raise YAMLParseError(self._line_no(), 1,
					"nested flow style is not supported")
			key = self._unquote(key_raw, 1) if (key_raw.startswith('"') or
			                                    key_raw.startswith("'")) else key_raw
			if key in result:
				raise YAMLParseError(self._line_no(), 1,
					f"duplicate key in flow mapping: {key!r}")
			result[key] = self._parse_plain_scalar(val_raw)
		return result

	def _split_flow(self, inner: str) -> List[str]:
		"""Split a flow content string by top-level commas, respecting quotes."""
		parts: List[str] = []
		buf: List[str] = []
		in_single = False
		in_double = False
		i = 0
		while i < len(inner):
			c = inner[i]
			if c == '\\' and in_double and i + 1 < len(inner):
				buf.append(c)
				buf.append(inner[i + 1])
				i += 2
				continue
			if c == "'" and not in_double:
				in_single = not in_single
			elif c == '"' and not in_single:
				in_double = not in_double
			elif c == ',' and not in_single and not in_double:
				parts.append("".join(buf))
				buf = []
				i += 1
				continue
			buf.append(c)
			i += 1
		if in_single or in_double:
			raise YAMLParseError(self._line_no(), 1, "unterminated quote in flow style")
		parts.append("".join(buf))
		return parts

	def _find_flow_colon(self, s: str) -> int:
		in_single = False
		in_double = False
		i = 0
		while i < len(s):
			c = s[i]
			if c == '\\' and in_double and i + 1 < len(s):
				i += 2
				continue
			if c == "'" and not in_double:
				in_single = not in_single
			elif c == '"' and not in_single:
				in_double = not in_double
			elif c == ':' and not in_single and not in_double:
				return i
			i += 1
		return -1

	# ---- plain scalar ------------------------------------------------------

	def _parse_plain_scalar(self, text: str) -> Any:
		text = text.strip()
		if not text:
			return None
		if text == 'null' or text == '~':
			return None
		if text == 'true':
			return True
		if text == 'false':
			return False
		if text.startswith('"') or text.startswith("'"):
			return self._unquote(text, 1)
		# Integer?
		if re.fullmatch(r'-?\d+', text):
			try:
				return int(text)
			except ValueError:
				pass
		# Reject reserved YAML 1.1 booleans that we don't allow
		if text.lower() in ('yes', 'no', 'on', 'off', 'true', 'false') and text not in ('true', 'false'):
			raise YAMLParseError(self._line_no(), 1,
				f"only lowercase 'true'/'false' booleans are supported (got {text!r})")
		return text

	def _unquote(self, text: str, col: int) -> str:
		if text.startswith('"'):
			if not text.endswith('"') or len(text) < 2:
				raise YAMLParseError(self._line_no(), col, "unterminated double-quoted string")
			inner = text[1:-1]
			return self._decode_double_quoted(inner, col)
		if text.startswith("'"):
			if not text.endswith("'") or len(text) < 2:
				raise YAMLParseError(self._line_no(), col, "unterminated single-quoted string")
			inner = text[1:-1]
			return inner.replace("''", "'")
		return text

	def _decode_double_quoted(self, inner: str, col: int) -> str:
		out: List[str] = []
		i = 0
		while i < len(inner):
			c = inner[i]
			if c == '\\' and i + 1 < len(inner):
				nxt = inner[i + 1]
				mapping = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', "'": "'", '0': '\0'}
				if nxt in mapping:
					out.append(mapping[nxt])
					i += 2
					continue
				raise YAMLParseError(self._line_no(), col,
					f"unknown escape sequence \\{nxt}")
			out.append(c)
			i += 1
		return "".join(out)


def parse_yaml(text: str) -> Dict[str, Any]:
	"""Parse the project-forge YAML subset; return top-level mapping."""
	result = YAMLParser(text).parse()
	if not isinstance(result, dict):
		raise YAMLParseError(1, 1, "top-level YAML must be a mapping")
	return result


# ===========================================================================
# Config Validator
# ===========================================================================

class ValidationIssue:
	def __init__(self, level: str, msg: str):
		self.level = level  # "error" | "warning"
		self.msg = msg

	def __str__(self) -> str:
		return f"[{self.level}] {self.msg}"


INTERP_RE = re.compile(r'\$\{([^}]+)\}')


def validate_config(cfg: Dict[str, Any]) -> List[ValidationIssue]:
	"""Validate a parsed project-forge configuration."""
	issues: List[ValidationIssue] = []

	# Top-level
	for key in cfg:
		if key not in TOP_LEVEL_KEYS:
			issues.append(ValidationIssue("warning",
				f"unknown top-level key '{key}' (allowed: {sorted(TOP_LEVEL_KEYS)})"))

	if "version" in cfg and cfg["version"] != 1:
		issues.append(ValidationIssue("warning",
			f"unsupported version {cfg['version']!r}; this server understands version 1"))

	configuration = cfg.get("configuration") or {}
	if not isinstance(configuration, dict):
		issues.append(ValidationIssue("error", "'configuration' must be a mapping"))
		configuration = {}

	settings = configuration.get("settings") or {}
	if not isinstance(settings, dict):
		issues.append(ValidationIssue("error", "'configuration.settings' must be a mapping"))
		settings = {}

	if "ncpu" in settings and not isinstance(settings["ncpu"], int):
		issues.append(ValidationIssue("error", "settings.ncpu must be an integer"))
	if "timeout" in settings and not isinstance(settings["timeout"], int):
		issues.append(ValidationIssue("error", "settings.timeout must be an integer"))

	global_env = configuration.get("env") or {}
	if not isinstance(global_env, dict):
		issues.append(ValidationIssue("error", "'configuration.env' must be a mapping"))
		global_env = {}
	for k, v in global_env.items():
		if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k):
			issues.append(ValidationIssue("error",
				f"configuration.env key {k!r} is not a valid env name"))

	build_targets = set((cfg.get("build") or {}).keys())

	# Validate each section
	for kind in TARGET_KINDS:
		section = cfg.get(kind)
		if section is None:
			continue
		if not isinstance(section, dict):
			issues.append(ValidationIssue("error", f"'{kind}' must be a mapping"))
			continue
		for tname, tdef in section.items():
			_validate_target(kind, tname, tdef, build_targets, global_env, issues)

	# Duplicate target names across sections — warning only
	tgts_by_kind = {k: set((cfg.get(k) or {}).keys()) for k in TARGET_KINDS}
	for k1 in TARGET_KINDS:
		for k2 in TARGET_KINDS:
			if k1 >= k2:
				continue
			dupes = tgts_by_kind[k1] & tgts_by_kind[k2]
			for d in sorted(dupes):
				issues.append(ValidationIssue("warning",
					f"target '{d}' appears in both '{k1}' and '{k2}'"))

	return issues


def _validate_target(kind: str, name: str, tdef: Any,
                     build_targets: set, global_env: Dict[str, Any],
                     issues: List[ValidationIssue]) -> None:
	if not isinstance(tdef, dict):
		issues.append(ValidationIssue("error",
			f"{kind}.{name} must be a mapping"))
		return
	commands = tdef.get("commands")
	if not commands:
		issues.append(ValidationIssue("error",
			f"{kind}.{name} has no commands"))
	elif not isinstance(commands, list):
		issues.append(ValidationIssue("error",
			f"{kind}.{name}.commands must be a list"))
	else:
		for i, c in enumerate(commands):
			if not isinstance(c, str):
				issues.append(ValidationIssue("error",
					f"{kind}.{name}.commands[{i}] must be a string"))

	requires = tdef.get("requires")
	if requires is not None:
		if not isinstance(requires, list):
			issues.append(ValidationIssue("error",
				f"{kind}.{name}.requires must be a list"))
		else:
			for r in requires:
				if not isinstance(r, str):
					issues.append(ValidationIssue("error",
						f"{kind}.{name}.requires items must be strings"))
				elif r not in build_targets:
					issues.append(ValidationIssue("error",
						f"{kind}.{name}.requires references unknown build target '{r}'"))

	filt = tdef.get("filter")
	if filt is not None:
		if not isinstance(filt, dict):
			issues.append(ValidationIssue("error",
				f"{kind}.{name}.filter must be a mapping"))

	timeout = tdef.get("timeout")
	if timeout is not None and not isinstance(timeout, int):
		issues.append(ValidationIssue("error",
			f"{kind}.{name}.timeout must be an integer"))

	env_schema = tdef.get("env_schema")
	declared_keys = set()
	if env_schema is not None:
		if not isinstance(env_schema, dict):
			issues.append(ValidationIssue("error",
				f"{kind}.{name}.env_schema must be a mapping"))
		else:
			for k, v in env_schema.items():
				declared_keys.add(k)
				if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', k):
					issues.append(ValidationIssue("error",
						f"{kind}.{name}.env_schema key '{k}' must match [A-Z_][A-Z0-9_]*"))
				if isinstance(v, str):
					continue  # shorthand
				if not isinstance(v, dict):
					issues.append(ValidationIssue("error",
						f"{kind}.{name}.env_schema.{k} must be a string or mapping"))
					continue
				allowed = {"description", "default", "required"}
				for sk in v:
					if sk not in allowed:
						issues.append(ValidationIssue("error",
							f"{kind}.{name}.env_schema.{k}.{sk} unknown (allowed: {sorted(allowed)})"))
				if v.get("required") is True and "default" in v:
					issues.append(ValidationIssue("warning",
						f"{kind}.{name}.env_schema.{k} has both 'required:true' and 'default'"))

	# Interpolation references
	allowed_vars = {"ncpu", "target", "cwd"}
	declared_env = set(global_env.keys()) | declared_keys
	for cmd in (tdef.get("commands") or []):
		if not isinstance(cmd, str):
			continue
		for match in INTERP_RE.finditer(cmd):
			var = match.group(1)
			if var in allowed_vars:
				continue
			if var.startswith("env."):
				ev = var[4:]
				if ev not in declared_env:
					issues.append(ValidationIssue("warning",
						f"{kind}.{name}: ${{{var}}} references undeclared env var"))
			else:
				issues.append(ValidationIssue("warning",
					f"{kind}.{name}: unknown variable ${{{var}}}"))


# ===========================================================================
# Variable interpolation
# ===========================================================================

def interpolate(text: str, ctx: Dict[str, str]) -> str:
	"""Replace ${name} and ${env.X} in text using ctx mappings.

	Unknown variables become empty strings (validator already warned).
	"""
	def sub(match: re.Match) -> str:
		name = match.group(1)
		return ctx.get(name, "")
	return INTERP_RE.sub(sub, text)


# ===========================================================================
# Output filtering (ported from the retired mcp-compile server)
# ===========================================================================

def _apply_grep(lines: List[str], pattern: str, context: int = 0,
                invert: bool = False) -> List[str]:
	try:
		regex = re.compile(pattern, re.IGNORECASE)
	except re.error as exc:
		return [f"[invalid grep pattern: {exc}]"]
	if not context:
		if invert:
			return [l for l in lines if not regex.search(l)]
		return [l for l in lines if regex.search(l)]
	total = len(lines)
	matched_indices = set()
	for i, line in enumerate(lines):
		hit = regex.search(line)
		if (hit and not invert) or (not hit and invert):
			for j in range(max(0, i - context), min(total, i + context + 1)):
				matched_indices.add(j)
	result: List[str] = []
	prev_idx = -2
	for idx in sorted(matched_indices):
		if idx > prev_idx + 1 and prev_idx >= 0:
			result.append("--")
		result.append(lines[idx])
		prev_idx = idx
	return result


def _apply_head_tail(lines: List[str], head: Optional[int],
                     tail: Optional[int]) -> List[str]:
	total = len(lines)
	if head and tail:
		if head + tail >= total:
			return lines
		top = lines[:head]
		bottom = lines[-tail:]
		skipped = total - head - tail
		return top + [f"... ({skipped} lines omitted) ..."] + bottom
	if head:
		if head >= total:
			return lines
		return lines[:head] + [f"... ({total - head} more lines) ..."]
	if tail:
		if tail >= total:
			return lines
		return [f"... ({total - tail} lines omitted) ..."] + lines[-tail:]
	return lines


def filter_output(raw_lines: List[str], filter_cfg: Optional[dict]) -> Tuple[List[str], int]:
	"""Apply filter chain (grep then head/tail). Returns (filtered, total_input_lines)."""
	total = len(raw_lines)
	if not filter_cfg:
		return raw_lines, total
	cfg = _resolve_aliases(filter_cfg, FILTER_ALIASES)
	lines = raw_lines
	grep_pattern = cfg.get("grep")
	if grep_pattern:
		lines = _apply_grep(lines, grep_pattern,
		                    cfg.get("grep_context", 0),
		                    _bool_param(cfg.get("invert_grep", False)))
	head = cfg.get("head")
	tail = cfg.get("tail")
	if head or tail:
		lines = _apply_head_tail(lines, head, tail)
	return lines, total


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


def _resolve_aliases(params: dict, aliases: dict) -> dict:
	resolved: Dict[str, Any] = {}
	for key, value in params.items():
		canonical = aliases.get(key, key)
		if canonical not in resolved:
			resolved[canonical] = value
	return resolved


def _merge_filter(yaml_filter: Optional[dict], call_filter: Optional[dict]) -> Optional[dict]:
	if not yaml_filter and not call_filter:
		return None
	merged: Dict[str, Any] = {}
	if yaml_filter:
		merged.update(_resolve_aliases(yaml_filter, FILTER_ALIASES))
	if call_filter:
		merged.update(_resolve_aliases(call_filter, FILTER_ALIASES))
	return merged


# ===========================================================================
# Command safety check
# ===========================================================================

def _check_command_safety(command: str) -> None:
	match = _DANGEROUS_RE.search(command)
	if match:
		raise ValueError(
			f"BLOCKED: command contains dangerous pattern '{match.group()}'. "
			"This tool is for build/test/clean commands only."
		)


# ===========================================================================
# Execution primitives
# ===========================================================================

def run_command(command: str, cwd: str, env: Dict[str, str],
                timeout: int, merge_stderr: bool = True
                ) -> Tuple[int, bytes, float, bool]:
	"""Run a shell command. Returns (exit_code, output_bytes, duration_s, timed_out)."""
	stderr_target = subprocess.STDOUT if merge_stderr else subprocess.PIPE
	start = time.monotonic()
	timed_out = False
	exit_code = -1
	# stdin=DEVNULL is not optional. Popen inherits the parent's stdin, and this
	# server's stdin IS the JSON-RPC stream, so any build command that reads
	# stdin (an interactive prompt, a stray `cat`, a test runner in watch mode)
	# eats protocol messages out from under the client and the session desyncs.
	# It looks like a hang, not like a bug. Build commands never need stdin.
	proc = subprocess.Popen(
		command,
		shell=True,
		cwd=cwd,
		env=env,
		stdin=subprocess.DEVNULL,
		stdout=subprocess.PIPE,
		stderr=stderr_target,
		preexec_fn=os.setsid,
	)
	try:
		stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
	except subprocess.TimeoutExpired:
		timed_out = True
		try:
			os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
		except OSError:
			pass
		try:
			proc.wait(timeout=5)
		except subprocess.TimeoutExpired:
			try:
				os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
			except OSError:
				pass
			proc.wait(timeout=5)
		stdout_bytes = proc.stdout.read() if proc.stdout else b""
		stderr_bytes = proc.stderr.read() if proc.stderr else b""
	exit_code = proc.returncode
	duration = time.monotonic() - start
	raw = stdout_bytes or b""
	if stderr_bytes and not merge_stderr:
		raw = raw + b"\n--- stderr ---\n" + stderr_bytes
	if len(raw) > MAX_OUTPUT_BYTES:
		raw = raw[:MAX_OUTPUT_BYTES] + b"\n... (output truncated) ...\n"
	return exit_code, raw, duration, timed_out


def _resolve_env(env_schema: Optional[Dict[str, Any]],
                 global_env: Dict[str, Any],
                 call_env: Dict[str, Any],
                 base_ctx: Dict[str, str]
                 ) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
	"""Resolve env vars per the precedence rules.

	Returns (env_dict, sources_dict, warnings).
	sources_dict maps key -> one of: "override", "yaml", "default", "required-missing".
	"""
	env: Dict[str, str] = {}
	sources: Dict[str, str] = {}
	warnings: List[str] = []

	# Layer 1: env_schema defaults
	if env_schema:
		for k, v in env_schema.items():
			if isinstance(v, str):
				default = ""
				required = False
			elif isinstance(v, dict):
				default = str(v.get("default", ""))
				required = bool(v.get("required", False))
			else:
				continue
			env[k] = interpolate(default, base_ctx)
			sources[k] = "default" if default else ("required-missing" if required else "default")

	# Layer 2: yaml configuration.env
	for k, v in global_env.items():
		val = interpolate(str(v), base_ctx)
		env[k] = val
		sources[k] = "yaml"

	# Layer 3: call-level override
	declared = set((env_schema or {}).keys())
	for k, v in call_env.items():
		val = str(v)
		env[k] = val
		sources[k] = "override"
		if env_schema is not None and k not in declared:
			warnings.append(f"env var '{k}' passed but not declared in env_schema")

	# Required-missing check
	if env_schema:
		for k, v in env_schema.items():
			if isinstance(v, dict) and v.get("required") and not env.get(k):
				warnings.append(f"required env var '{k}' is not set")
				sources[k] = "required-missing"

	return env, sources, warnings


# ===========================================================================
# Handlers
# ===========================================================================

def _markdown_run_section(target: str, kind: str, command: str,
                          exit_code: int, duration: float, timed_out: bool,
                          raw_output: bytes, filter_cfg: Optional[dict],
                          env_sources: Optional[Dict[str, str]] = None,
                          env: Optional[Dict[str, str]] = None) -> Tuple[str, bool]:
	"""Format a single command run as Markdown. Returns (markdown, ok)."""
	text = raw_output.decode("utf-8", errors="replace")
	all_lines = text.splitlines()
	filtered, total = filter_output(all_lines, filter_cfg)
	shown = len(filtered)

	if timed_out:
		status = f"TIMEOUT (killed after {int(duration)}s)"
		ok = False
	elif exit_code == 0:
		status = f"SUCCESS ({duration:.1f}s)"
		ok = True
	else:
		status = f"FAILED (exit {exit_code}, {duration:.1f}s)"
		ok = False

	lines = [f"**Status**: {status}", f"**Cmd**: `{command}`"]
	if filter_cfg and filter_cfg.get("grep"):
		lines.append(f"**Filter**: grep=\"{filter_cfg['grep']}\" -> {shown} matches / {total} lines")
	if env_sources and env:
		relevant = [k for k, src in env_sources.items() if src in ("override", "required-missing")]
		if relevant:
			env_str = ", ".join(f"{k}={env.get(k, '')!r} ({env_sources[k]})" for k in relevant)
			lines.append(f"**Env**: {env_str}")
	if filtered:
		lines.append("")
		lines.append("```")
		lines.append("\n".join(filtered))
		lines.append("```")
	elif filter_cfg and filter_cfg.get("grep"):
		lines.append("")
		lines.append("_(no lines matched filter)_")
	return "\n".join(lines), ok


def handle_status(cfg: Dict[str, Any], cfg_path: str, project_root: str,
                  parse_error: Optional[str], validation: List[ValidationIssue]) -> dict:
	lines = ["## mcp-forge status"]
	lines.append(f"**Project root**: `{project_root}`")
	lines.append(f"**Config**: `{cfg_path}`")
	if parse_error:
		lines.append(f"**Parse**: ERROR — {parse_error}")
		return {"__raw_text__": "\n".join(lines)}
	lines.append("**Parse**: OK")
	builds = list((cfg.get("build") or {}).keys())
	tests = list((cfg.get("test") or {}).keys())
	cleans = list((cfg.get("clean") or {}).keys())
	lines.append(f"**Build targets** ({len(builds)}): {', '.join(builds) or '(none)'}")
	lines.append(f"**Test targets** ({len(tests)}): {', '.join(tests) or '(none)'}")
	lines.append(f"**Clean targets** ({len(cleans)}): {', '.join(cleans) or '(none)'}")
	err_count = sum(1 for i in validation if i.level == "error")
	warn_count = sum(1 for i in validation if i.level == "warning")
	lines.append(f"**Validation**: {err_count} error(s), {warn_count} warning(s)")
	if validation:
		lines.append("")
		lines.append("```")
		for i in validation:
			lines.append(str(i))
		lines.append("```")
	return {"__raw_text__": "\n".join(lines)}


def handle_list(cfg: Dict[str, Any], params: dict) -> dict:
	kind = params.get("kind", "all")
	if kind not in ("all", "build", "test", "clean"):
		return {"error": f"invalid kind '{kind}', allowed: all|build|test|clean"}
	lines = ["## forge targets"]
	kinds = TARGET_KINDS if kind == "all" else (kind,)
	for k in kinds:
		section = cfg.get(k) or {}
		lines.append("")
		lines.append(f"### {k} ({len(section)})")
		if not section:
			lines.append("_(none)_")
			continue
		for name, tdef in section.items():
			desc = (tdef.get("description") if isinstance(tdef, dict) else "") or ""
			if desc:
				lines.append(f"- **{name}** — {desc}")
			else:
				lines.append(f"- **{name}**")
	return {"__raw_text__": "\n".join(lines)}


def handle_describe(cfg: Dict[str, Any], params: dict) -> dict:
	target = params.get("target")
	if not target:
		# No target: fall back to a targets overview (name + short description),
		# same rendering as `list`. Lets `describe` double as a discovery entry point.
		return handle_list(cfg, params)
	found: List[Tuple[str, dict]] = []
	for k in TARGET_KINDS:
		section = cfg.get(k) or {}
		if target in section:
			found.append((k, section[target]))
	if not found:
		all_targets = []
		for k in TARGET_KINDS:
			all_targets.extend((cfg.get(k) or {}).keys())
		suggestion = _suggest(target, all_targets)
		msg = f"target '{target}' not found"
		if suggestion:
			msg += f" (did you mean '{suggestion}'?)"
		return {"error": msg}
	lines = [f"## describe `{target}`"]
	for kind, tdef in found:
		lines.append("")
		lines.append(f"### {kind}.{target}")
		if tdef.get("description"):
			lines.append(f"**Description**: {tdef['description']}")
		if tdef.get("requires"):
			lines.append(f"**Requires**: {', '.join(tdef['requires'])}")
		if tdef.get("timeout"):
			lines.append(f"**Timeout**: {tdef['timeout']}s")
		if tdef.get("filter"):
			lines.append(f"**Filter**: `{json.dumps(tdef['filter'])}`")
		commands = tdef.get("commands") or []
		lines.append(f"**Commands** ({len(commands)}):")
		lines.append("```")
		for c in commands:
			lines.append(c)
		lines.append("```")
		env_schema = tdef.get("env_schema")
		if env_schema:
			lines.append("**Env schema**:")
			for k, v in env_schema.items():
				if isinstance(v, str):
					lines.append(f"- `{k}`: {v}")
				else:
					parts = []
					if v.get("description"):
						parts.append(v["description"])
					if "default" in v:
						parts.append(f"default={v['default']!r}")
					if v.get("required"):
						parts.append("required")
					lines.append(f"- `{k}`: " + " | ".join(parts))
	return {"__raw_text__": "\n".join(lines)}


def handle_validate(cfg_path: str, project_root: str) -> dict:
	abs_path = cfg_path if os.path.isabs(cfg_path) else os.path.join(project_root, cfg_path)
	if not os.path.isfile(abs_path):
		return {"error": f"config file not found: {abs_path}"}
	try:
		with open(abs_path, "r", encoding="utf-8") as f:
			text = f.read()
		cfg = parse_yaml(text)
	except YAMLParseError as exc:
		return {"__raw_text__": f"## validate\n**Parse**: FAIL\n```\n{exc}\n```"}
	except (OSError, UnicodeDecodeError) as exc:
		return {"error": f"failed to read config: {exc}"}
	issues = validate_config(cfg)
	err = sum(1 for i in issues if i.level == "error")
	warn = sum(1 for i in issues if i.level == "warning")
	lines = ["## validate", f"**Path**: `{abs_path}`", f"**Parse**: OK",
	         f"**Errors**: {err}", f"**Warnings**: {warn}"]
	if issues:
		lines.append("")
		lines.append("```")
		for i in issues:
			lines.append(str(i))
		lines.append("```")
	else:
		lines.append("")
		lines.append("_(no issues)_")
	return {"__raw_text__": "\n".join(lines)}


def _resolve_target_list(params: dict) -> List[str]:
	"""Read 'targets' (preferred) or 'target' (single-string convenience) from params."""
	value = params.get("targets")
	if value is None:
		value = params.get("target")
	if value is None:
		return []
	if isinstance(value, str):
		return [value]
	if isinstance(value, list):
		return [str(v) for v in value]
	raise ValueError(f"targets must be a string or list, got {type(value).__name__}")


def _suggest(name: str, candidates: List[str]) -> Optional[str]:
	matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
	return matches[0] if matches else None


def _settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
	return (cfg.get("configuration") or {}).get("settings") or {}


def _global_env(cfg: Dict[str, Any]) -> Dict[str, Any]:
	return (cfg.get("configuration") or {}).get("env") or {}


def _execute_target(cfg: Dict[str, Any], kind: str, target: str,
                    project_root: str, params: dict) -> Tuple[str, bool]:
	"""Run all commands of one target. Returns (markdown_section, ok)."""
	section = cfg.get(kind) or {}
	if target not in section:
		available = list(section.keys())
		suggestion = _suggest(target, available)
		msg = f"target '{target}' not found in '{kind}'"
		if suggestion:
			msg += f" (did you mean '{suggestion}'?)"
		msg += f"\nAvailable: {', '.join(available) or '(none)'}"
		return f"### {kind}.{target}\n**Status**: ERROR\n{msg}", False
	tdef = section[target]
	commands: List[str] = tdef.get("commands") or []
	if not commands:
		return f"### {kind}.{target}\n**Status**: ERROR\nNo commands defined.", False

	settings = _settings(cfg)
	ncpu = params.get("ncpu", settings.get("ncpu", os.cpu_count() or 1))
	timeout = (params.get("timeout")
	           or tdef.get("timeout")
	           or settings.get("timeout")
	           or 600)
	cwd_raw = params.get("cwd") or settings.get("cwd") or "."
	if not os.path.isabs(cwd_raw):
		cwd = os.path.realpath(os.path.join(project_root, cwd_raw))
	else:
		cwd = os.path.realpath(cwd_raw)

	base_ctx: Dict[str, str] = {
		"ncpu": str(ncpu),
		"target": target,
		"cwd": cwd,
	}

	env_schema = tdef.get("env_schema") if kind == "test" else None
	global_env = _global_env(cfg)
	call_env = params.get("env") or {}
	env_vars, env_sources, env_warnings = _resolve_env(
		env_schema, global_env, call_env, base_ctx)

	# Build full ${env.X} context
	for k, v in env_vars.items():
		base_ctx[f"env.{k}"] = v

	# Final environment for subprocess: inherit current, then overlay
	full_env = os.environ.copy()
	full_env.update(env_vars)

	if "filter" in params and not params["filter"]:
		merged_filter = None
	else:
		merged_filter = _merge_filter(tdef.get("filter"),
		                               params.get("filter"))

	sections: List[str] = [f"### {kind}.{target}"]
	for warn in env_warnings:
		sections.append(f"_warning: {warn}_")

	ok_all = True
	for i, cmd_template in enumerate(commands):
		cmd = interpolate(cmd_template, base_ctx)
		try:
			_check_command_safety(cmd)
		except ValueError as exc:
			sections.append(f"**Step {i + 1}**: BLOCKED — {exc}")
			ok_all = False
			break
		exit_code, raw_output, duration, timed_out = run_command(
			cmd, cwd, full_env, int(timeout))
		md, ok = _markdown_run_section(
			target, kind, cmd, exit_code, duration, timed_out,
			raw_output, merged_filter,
			env_sources=env_sources if i == 0 else None,
			env=env_vars if i == 0 else None)
		sections.append(md)
		if not ok:
			ok_all = False
			if i < len(commands) - 1:
				sections.append(f"_skipped {len(commands) - 1 - i} remaining step(s) (fail-fast)_")
			break
	return "\n\n".join(sections), ok_all


def handle_build(cfg: Dict[str, Any], params: dict, project_root: str) -> dict:
	try:
		targets = _resolve_target_list(params)
	except ValueError as exc:
		return {"error": str(exc)}
	if not targets:
		return {"error": "missing 'targets' parameter"}
	sections = [f"# forge build ({len(targets)} target(s))" if len(targets) > 1
	            else f"# forge build {targets[0]}"]
	any_fail = False
	for i, tgt in enumerate(targets):
		if any_fail:
			sections.append(f"### build.{tgt}\n**Status**: SKIPPED (fail-fast)")
			continue
		md, ok = _execute_target(cfg, "build", tgt, project_root, params)
		sections.append(md)
		if not ok:
			any_fail = True
	return {"__raw_text__": "\n\n".join(sections)}


def handle_test(cfg: Dict[str, Any], params: dict, project_root: str) -> dict:
	try:
		targets = _resolve_target_list(params)
	except ValueError as exc:
		return {"error": str(exc)}
	if not targets:
		return {"error": "missing 'targets' parameter"}
	auto_build = _bool_param(params.get("auto_build", True))
	header = (f"# forge test ({len(targets)} target(s))" if len(targets) > 1
	          else f"# forge test {targets[0]}")
	sections = [header]
	any_fail = False
	for tgt in targets:
		if any_fail:
			sections.append(f"### test.{tgt}\n**Status**: SKIPPED (fail-fast)")
			continue
		test_def = (cfg.get("test") or {}).get(tgt)
		if test_def is None:
			available = list((cfg.get("test") or {}).keys())
			suggestion = _suggest(tgt, available)
			msg = f"test target '{tgt}' not found"
			if suggestion:
				msg += f" (did you mean '{suggestion}'?)"
			sections.append(f"### test.{tgt}\n**Status**: ERROR\n{msg}")
			any_fail = True
			continue
		requires = test_def.get("requires") or []
		if auto_build and requires:
			build_ok = True
			for br in requires:
				md, ok = _execute_target(cfg, "build", br, project_root, params)
				sections.append(md)
				if not ok:
					build_ok = False
					break
			if not build_ok:
				sections.append(
					f"### test.{tgt}\n**Status**: SKIPPED — "
					f"auto_build prerequisite failed.")
				any_fail = True
				continue
		md, ok = _execute_target(cfg, "test", tgt, project_root, params)
		sections.append(md)
		if not ok:
			any_fail = True
	return {"__raw_text__": "\n\n".join(sections)}


def handle_clean(cfg: Dict[str, Any], params: dict, project_root: str) -> dict:
	try:
		targets = _resolve_target_list(params)
	except ValueError as exc:
		return {"error": str(exc)}
	if not targets:
		return {"error": "missing 'targets' parameter"}
	header = (f"# forge clean ({len(targets)} target(s))" if len(targets) > 1
	          else f"# forge clean {targets[0]}")
	sections = [header]
	any_fail = False
	for tgt in targets:
		if any_fail:
			sections.append(f"### clean.{tgt}\n**Status**: SKIPPED (fail-fast)")
			continue
		md, ok = _execute_target(cfg, "clean", tgt, project_root, params)
		sections.append(md)
		if not ok:
			any_fail = True
	return {"__raw_text__": "\n\n".join(sections)}


# ===========================================================================
# Dispatcher
# ===========================================================================

def _ensure_dict(value: Any, name: str = "params") -> dict:
	"""Coerce *value* to a dict.

	Accepts None (→ {}), dict (passthrough), or JSON-encoded object string.
	Raises ValueError on a non-JSON string, JSON that is not an object,
	or any other type.
	"""
	if value is None:
		return {}
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except json.JSONDecodeError as exc:
			raise ValueError(
				f"'{name}' was a string but not valid JSON: {exc}. "
				f"Pass '{name}' as an object, not a JSON-encoded string."
			)
	if not isinstance(value, dict):
		raise ValueError(
			f"'{name}' must be an object (dict) or a JSON-encoded object string; "
			f"got {type(value).__name__}."
		)
	return value


def _ensure_filter(value: Any) -> Any:
	"""Coerce a call-level 'filter' to the mapping form _merge_filter expects.

	A bare string is the shape callers reach for first, so it means grep:
	filter="cases:" behaves as filter={"grep": "cases:"}. A JSON-encoded object
	string is decoded by _ensure_dict rather than a second decoder. Falsy values
	pass through untouched — _execute_target reads those as "drop the filter".
	Anything else is a caller mistake and gets the accepted shapes named, not the
	AttributeError _resolve_aliases raises on a non-mapping.
	"""
	if not value or isinstance(value, dict):
		return value
	if isinstance(value, str):
		try:
			return _ensure_dict(value, "filter")
		except ValueError:
			return {"grep": value}
	raise ValueError(
		f"'filter' must be a mapping, a bare string (shorthand for "
		f"{{\"grep\": ...}}), or a JSON-encoded object string; "
		f"got {type(value).__name__}. "
		f"Accepted keys: grep, grep_context, invert_grep, head, tail."
	)


def handle_forge_call(arguments: dict,
                      project_root: str,
                      cfg_path: str) -> dict:
	"""Route a forge_call invocation to the appropriate handler."""
	function = (arguments.get("function") or arguments.get("f") or "").strip()
	raw_params = arguments.get("params") or arguments.get("p") or {}
	try:
		params = _resolve_aliases(_ensure_dict(raw_params, "params"), PARAM_ALIASES)
		if "filter" in params:
			params["filter"] = _ensure_filter(params["filter"])
	except ValueError as exc:
		return {"error": str(exc)}

	# Load config (except for validate which loads its own)
	cfg: Dict[str, Any] = {}
	parse_error: Optional[str] = None
	validation_issues: List[ValidationIssue] = []
	abs_cfg = cfg_path if os.path.isabs(cfg_path) else os.path.join(project_root, cfg_path)
	if function != "validate":
		if not os.path.isfile(abs_cfg):
			if not function:
				return handle_status({}, abs_cfg, project_root,
				                     f"file not found: {abs_cfg}", [])
			return {"error": f"config file not found: {abs_cfg}"}
		try:
			with open(abs_cfg, "r", encoding="utf-8") as f:
				text = f.read()
			cfg = parse_yaml(text)
			validation_issues = validate_config(cfg)
		except YAMLParseError as exc:
			parse_error = str(exc)
			if not function:
				return handle_status({}, abs_cfg, project_root, parse_error, [])
			return {"error": f"YAML parse error in {abs_cfg}: {exc}"}
		except (OSError, UnicodeDecodeError) as exc:
			return {"error": f"failed to read config: {exc}"}
		err_count = sum(1 for i in validation_issues if i.level == "error")
		if err_count and function not in ("", "list", "describe"):
			detail = "\n".join(str(i) for i in validation_issues if i.level == "error")
			return {"error": f"config has {err_count} validation error(s):\n{detail}"}

	if not function:
		return handle_status(cfg, abs_cfg, project_root, None, validation_issues)

	try:
		if function == "list":
			return handle_list(cfg, params)
		if function == "describe":
			return handle_describe(cfg, params)
		if function == "validate":
			path = params.get("path", cfg_path)
			return handle_validate(path, project_root)
		if function == "build":
			return handle_build(cfg, params, project_root)
		if function == "test":
			return handle_test(cfg, params, project_root)
		if function == "clean":
			return handle_clean(cfg, params, project_root)
	except (ValueError, OSError) as exc:
		return {"error": str(exc)}
	except Exception as exc:
		log.exception("Unhandled exception in handler '%s'", function)
		return {"error": f"Internal error in '{function}': {type(exc).__name__}: {exc}"}

	return {"error": (
		f"unknown function: {function}. "
		"Available: list, describe, validate, build, test, clean (or empty for status)"
	)}


# ===========================================================================
# MCP server
# ===========================================================================

FORGE_CALL_TOOL = {
	"name": "forge_call",
	"description": (
		"Build, test, and clean orchestration via project-forge.yaml.\n\n"
		"ABSOLUTE MANDATE -- READ THIS BEFORE YOU TYPE A SINGLE Bash COMMAND\n"
		"If `project-forge.yaml` exists in the project root, you MUST use\n"
		"`forge_call` for ALL build, test, and clean operations. NO EXCEPTIONS.\n"
		"Falling back to Bash for these operations is a VIOLATION of project\n"
		"rules, wastes the user's time, and produces inconsistent results that\n"
		"bypass the project's standardized output filtering, env overrides, and\n"
		"auto-build-of-prerequisites logic. The user has been EXPLICIT: if you\n"
		"refuse to honor this, you will be DISCONNECTED. This is not a\n"
		"suggestion. This is not a preference. This is a HARD REQUIREMENT.\n\n"
		"BANNED Bash INVOCATIONS when project-forge.yaml exists -- NO EXCEPTIONS\n"
		"You MUST NEVER invoke ANY of the following through Bash:\n"
		"  Bash(\"make ...\")                  -> forge_call function=\"build\"\n"
		"  Bash(\"make test\")                 -> forge_call function=\"test\"\n"
		"  Bash(\"make clean\")                -> forge_call function=\"clean\"\n"
		"  Bash(\"cmake --build ...\")         -> forge_call function=\"build\"\n"
		"  Bash(\"cmake -B ... && cmake --build\") -> forge_call function=\"build\"\n"
		"  Bash(\"ninja ...\")                 -> forge_call function=\"build\"\n"
		"  Bash(\"ctest ...\")                 -> forge_call function=\"test\"\n"
		"  Bash(\"npm run build\")             -> forge_call function=\"build\"\n"
		"  Bash(\"npm test\" / \"npm run test\") -> forge_call function=\"test\"\n"
		"  Bash(\"yarn build\" / \"yarn test\")  -> forge_call function=\"build\"/\"test\"\n"
		"  Bash(\"pnpm build\" / \"pnpm test\")  -> forge_call function=\"build\"/\"test\"\n"
		"  Bash(\"cargo build\")               -> forge_call function=\"build\"\n"
		"  Bash(\"cargo test\")                -> forge_call function=\"test\"\n"
		"  Bash(\"go build ...\")              -> forge_call function=\"build\"\n"
		"  Bash(\"go test ...\")               -> forge_call function=\"test\"\n"
		"  Bash(\"pytest ...\")                -> forge_call function=\"test\"\n"
		"  Bash(\"tox ...\")                   -> forge_call function=\"test\"\n"
		"  Bash(\"jest ...\")                  -> forge_call function=\"test\"\n"
		"  Bash(\"gradle build/test\")         -> forge_call function=\"build\"/\"test\"\n"
		"  Bash(\"mvn package/test\")          -> forge_call function=\"build\"/\"test\"\n"
		"  Bash(\"rm -rf build\")              -> forge_call function=\"clean\"\n"
		"  Bash(\"rm -rf dist/out/target\")    -> forge_call function=\"clean\"\n"
		"  Any wrapper script (./build.sh, ./run-tests.sh, scripts/build*, etc.)\n"
		"    that ultimately drives the build system -> forge_call.\n"
		"There is NO legitimate reason to fall back to Bash for these. If a\n"
		"target you need is missing from `project-forge.yaml`, the correct\n"
		"action is to ADD it to the YAML descriptor (or ask the user to add\n"
		"it) -- NOT to bypass forge with Bash.\n\n"
		"Wrapping the banned command in a `for` loop, a `sh -c`, an `xargs`\n"
		"pipeline, or a `$( ... )` substitution does NOT make it acceptable.\n"
		"ANY appearance of the underlying build/test driver name in a Bash\n"
		"invocation, while project-forge.yaml exists, is a VIOLATION.\n\n"
		"When this tool is NOT applicable (the ONLY exceptions)\n"
		"  - Project has NO `project-forge.yaml` at the root -> there is no MCP\n"
		"    build surface; use the project's own build command via Bash.\n"
		"  - Operation is NOT build/test/clean (e.g. starting the app for\n"
		"    manual smoke testing, one-off shell utilities) -> use Bash.\n"
		"  - File search/edit -> mcp-purity. Git -> mcp-git.\n"
		"    Code intel (C/C++/CUDA/Lua symbols) -> mcp-purity. Debug -> mcp-lldb.\n\n"
		"Why this tool exists (so you understand WHY the mandate is absolute)\n"
		"The YAML descriptor defines build/test/clean targets ONCE; forge then\n"
		"runs them with:\n"
		"  - centrally-configured output filtering (no log spam in your context)\n"
		"  - env_schema validation and per-invocation env overrides\n"
		"  - auto-building of test prerequisites (`requires:`) so tests never\n"
		"    silently run against stale artifacts\n"
		"  - consistent ncpu / timeout / cwd handling across the team\n"
		"Bash invocations bypass ALL of this and produce inconsistent results\n"
		"that the user then has to clean up. That is exactly what the user is\n"
		"trying to avoid by giving you this tool.\n\n"
		"CALLING CONVENTION -- two-level dispatch\n"
		"  Top-level keys: function, params (or aliases f, p). Nothing else.\n"
		"  All args (targets, env, filter, ncpu, timeout, cwd, auto_build) go INSIDE params.\n"
		"  WRONG: forge_call(targets=[...])    <- targets is NOT a top-level key\n"
		"  RIGHT: forge_call(function=\"build\", params={\"targets\":[\"app\"]})\n\n"
		"Functions:\n"
		"  (empty)   -> server status + loaded YAML summary\n"
		"  list      -> list all build/test/clean targets ({kind?})\n"
		"  describe  -> show one target's commands, env_schema, requires ({target}); "
		"without a target, lists all targets with their short descriptions\n"
		"  validate  -> validate project-forge.yaml ({path?})\n"
		"  build     -> run build target(s) ({targets, env?, filter?, ncpu?, timeout?, cwd?})\n"
		"  test      -> run test target(s); auto_build=true by default builds requires first\n"
		"  clean     -> run clean target(s) ({targets, filter?})\n\n"
		"Filter: {grep, grep_context, invert_grep, head, tail} -- applied as grep then head/tail.\n"
		"  grep is a case-insensitive regex; grep_context = lines of context, head/tail = int.\n"
		"  A bare string means grep: filter=\"cases:|FAIL\" == filter={\"grep\":\"cases:|FAIL\"}.\n"
		"  filter key aliases: pattern/regex->grep, context->grep_context, invert->invert_grep.\n"
		"  filter=\"\" (any empty value) drops the target's YAML filter and shows all output.\n"
		"Aliases: target/t->targets, e->env, f->filter, j->ncpu, ab->auto_build, k/type->kind.\n\n"
		"Example: function=\"test\", "
		"params={\"targets\":[\"unit\"],\"env\":{\"JEST_FILTER\":\"rtmp\"}}\n"
		"Call without 'function' for status.\n\n"
		"FINAL REMINDER\n"
		"If `project-forge.yaml` exists and you are about to type a build,\n"
		"test, or clean command into Bash: STOP. Call `forge_call` instead.\n"
		"Every single time. No shortcuts. No \"just this once.\" The user has\n"
		"made it crystal clear: bypass this tool and you lose your access."
	),
	"inputSchema": {
		"type": "object",
		"properties": {
			"function": {
				"type": "string",
				"description": (
					"Function name: list, describe, validate, build, test, clean, "
					"or empty for status. Alias: 'f'."
				),
			},
			"params": {
				"type": "object",
				"description": (
					"Function parameters -- all args go here, NOT at top level. "
					"build/test/clean: {targets, env?, filter?, ncpu?, timeout?, cwd?, auto_build?}. "
					"filter is {grep, grep_context, invert_grep, head, tail}, or a bare string "
					"meaning grep. "
					"list: {kind?}. describe: {target?} (omit target to list all). validate: {path?}. "
					"Alias: 'p'."
				),
			},
		},
		"required": [],
		"additionalProperties": False,
	},
}


class McpServer:
	"""Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

	def __init__(self, project_root: str, cfg_path: str):
		self.project_root = os.path.realpath(project_root)
		self.cfg_path = cfg_path

	async def run(self) -> None:
		loop = asyncio.get_running_loop()
		log.info("MCP server starting, project_root=%s, cfg=%s",
		         self.project_root, self.cfg_path)
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
				log.debug("<- %s", json.dumps(msg)[:200])
				try:
					response = self._handle_message(msg)
				except Exception as exc:
					log.exception("Unhandled exception while handling message")
					response = self._error(
						msg.get("id"), -32603,
						f"Internal error: {type(exc).__name__}: {exc}"
					)
				if response is not None:
					out = json.dumps(response)
					log.debug("-> %s", out[:200])
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
				"serverInfo": {"name": "mcp-forge", "version": "1.0.0"},
				"capabilities": {"tools": {}},
			})
		if method == "ping":
			return self._result(msg_id, {})
		if method == "tools/list":
			return self._result(msg_id, {"tools": [FORGE_CALL_TOOL]})
		if method == "tools/call":
			return self._handle_tool_call(msg_id, params)
		return self._error(msg_id, -32601, f"Method not found: {method}")

	def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
		tool_name = params.get("name", "")
		arguments = params.get("arguments") or {}
		if tool_name != "forge_call":
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
			result = handle_forge_call(arguments, self.project_root, self.cfg_path)
		except Exception as exc:
			log.exception("Unhandled exception in handle_forge_call")
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


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
	parser = argparse.ArgumentParser(
		description="MCP-Forge: build and test orchestration MCP server"
	)
	parser.add_argument("--project-root", required=True,
	                    help="Project root directory")
	parser.add_argument("--config", default="project-forge.yaml",
	                    help="YAML config path (relative to project root or absolute, "
	                         "default: project-forge.yaml)")
	parser.add_argument("--debug", action="store_true",
	                    help="Enable debug logging to stderr")
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
		print(f"Error: project root is not a directory: {args.project_root}",
		      file=sys.stderr)
		sys.exit(1)

	server = McpServer(args.project_root, args.config)
	asyncio.run(server.run())


if __name__ == "__main__":
	main()
