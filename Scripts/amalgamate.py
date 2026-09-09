#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Inline the shared helper blocks from `_mcp_json.py` into the MCP servers.

The servers stay single-file (see `_mcp_json.py` for why an import was rejected),
so the shared code is *generated into* each one instead. A region looks like:

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
    # BEGIN GENERATED: _mcp_json.py :: _json_error_window
    def _json_error_window(...):
        ...
    # END GENERATED: 3f9a1c2b7d40

Three rules earn their keep, and each one is a failure mode somebody has already
shipped:

* **The trailing hash is over the emitted body alone.** On the next run the body
  is re-hashed, and a mismatch means somebody edited inside the region -- which
  is refused rather than silently overwritten. The file gets exactly one writer.
  The idea is Cog's `-c` checksum; the reason it is here rather than Cog is that
  a third-party tool this repo does not install degrades to a no-op, and a gate
  that does not run is worse than one that does not exist.
* **A `BEGIN` without an `END` is a hard error, never a skip.** A region that
  quietly stops being maintained is the whole failure this mechanism exists to
  prevent.
* **A file with no region at all is fine.** Servers are converted one at a time.

Paths are resolved from `__file__`, so `python3 ../../Scripts/amalgamate.py`
works from any working directory, and `resolve()` follows a symlink to the real
directory on purpose -- `~/.claude/scripts` is one.

Usage:
    python3 Scripts/amalgamate.py                 # rewrite every region
    python3 Scripts/amalgamate.py --check         # write nothing, exit 1 if stale
    python3 Scripts/amalgamate.py --force         # discard a hand edit
    python3 Scripts/amalgamate.py Scripts/mcp-purity.py
"""

import argparse
import ast
import hashlib
import io
import sys
import tokenize
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

BEGIN_PREFIX = "# BEGIN GENERATED:"
END_PREFIX = "# END GENERATED:"
TARGET_GLOB = "mcp-*.py"

SCRIPTS_DIR = Path(__file__).resolve().parent
CANONICAL = SCRIPTS_DIR / "_mcp_json.py"


class Region(NamedTuple):
    """One generated region located in a target file."""

    names: List[str]
    begin: int          # 0-based index of the BEGIN line
    end: int            # 0-based index of the END line
    recorded: str       # hash written on the END line ("" when never written)
    body: str           # what is between the markers right now
    wanted: str         # what the canonical source says it should be
    indent: str         # the BEGIN marker's own leading whitespace

    @property
    def state(self) -> str:
        if self.body == self.wanted and self.recorded == body_hash(self.wanted):
            return "ok"
        if self.recorded and self.recorded != body_hash(self.body):
            return "hand-edited"
        return "stale"

    @property
    def label(self) -> str:
        return ", ".join(self.names)


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def load_blocks(path: Path) -> Dict[str, str]:
    """Map every top-level name in *path* to its verbatim source text."""
    if not path.is_file():
        raise SystemExit(f"{path}: not a file")
    return load_blocks_text(path.name, path.read_text(encoding="utf-8"))


def load_blocks_text(label: str, text: str) -> Dict[str, str]:
    """Map every top-level name in *text* to its verbatim source text.

    The slice starts at the first DECORATOR line when there is one, not at the
    `def`: `ast` puts a decorated function's `lineno` on the `def`, so slicing
    from there would drop `@staticmethod` and silently turn a method into an
    instance method that takes the class's first argument as its own. That is
    the difference between a shareable `_result` -- byte-identical in thirteen
    servers -- and a server that raises on its first reply.
    """
    lines = text.splitlines(keepends=True)
    blocks: Dict[str, str] = {}
    try:
        tree = ast.parse(text, filename=label)
    except SyntaxError as exc:
        raise SystemExit(f"{label}: cannot be parsed ({exc})")
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        if node.decorator_list:
            start = min(start, min(d.lineno for d in node.decorator_list))
        blocks[node.name] = "".join(lines[start - 1:node.end_lineno])
    return blocks


def parse_names(marker: str, label: str, lineno: int) -> List[str]:
    spec = marker[len(BEGIN_PREFIX):].strip()
    if "::" not in spec:
        raise SystemExit(
            f"{label}:{lineno}: expected "
            f"'{BEGIN_PREFIX} <source> :: <name>[, <name>...]', got {marker!r}"
        )
    source, listed = spec.split("::", 1)
    if source.strip() != CANONICAL.name:
        raise SystemExit(
            f"{label}:{lineno}: unknown source {source.strip()!r}; "
            f"only {CANONICAL.name} is generated"
        )
    names = [n.strip() for n in listed.split(",") if n.strip()]
    if not names:
        raise SystemExit(f"{label}:{lineno}: the region lists no names")
    return names


def render(names: List[str], blocks: Dict[str, str], indent: str = "") -> str:
    """Emit the named blocks, each line prefixed with *indent*.

    The prefix is taken from the BEGIN marker's own column, which is what lets a
    region sit inside a class body: `_result` and `_error` are byte-identical
    methods in thirteen servers, and the marker's indentation is the only piece
    of information needed to place them.

    This shifts a block sideways; it does NOT re-indent the block internally, so
    a target that indents with TABS cannot host a space-indented block. That is a
    refusal rather than a gap: converting leading spaces to tabs would also
    convert ALIGNMENT to tabs -- `_rows_note`'s continuation line aligns its
    `else` under an open paren -- and a tab there moves the code to a column
    nobody chose. Two tab-indented servers therefore keep their own copies.
    """
    out = []
    for name in names:
        if name not in blocks:
            raise SystemExit(
                f"{CANONICAL.name} defines no top-level {name!r}"
            )
        out.append(blocks[name].rstrip("\n"))
    text = "\n\n\n".join(out) + "\n"
    if not indent:
        return text
    return "".join(
        f"{indent}{line}" if line.strip() else line
        for line in text.splitlines(keepends=True)
    )


def marker_comments(label: str, text: str) -> List[Tuple[int, str]]:
    """Return (0-based line index, comment text) for every marker COMMENT token.

    Tokenizing instead of scanning lines is what makes a marker *quoted* in a
    docstring inert, and the need is not hypothetical: this file's own docstring
    carries a full BEGIN/END pair as its example. A line scanner pointed at a
    server that documents the mechanism would treat the prose as a region and
    write generated code into the middle of a docstring.
    """
    found: List[Tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            comment = tok.string.strip()
            if comment.startswith(BEGIN_PREFIX) or comment.startswith(END_PREFIX):
                found.append((tok.start[0] - 1, comment))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise SystemExit(f"{label}: cannot be tokenized ({exc})")
    return found


def audit(path: Path, blocks: Dict[str, str]) -> List[Region]:
    """Locate and classify every generated region in *path*."""
    if not path.is_file():
        raise SystemExit(f"{path}: not a file")
    return audit_text(path.name, path.read_text(encoding="utf-8"), blocks)


def audit_text(label: str, text: str, blocks: Dict[str, str]) -> List[Region]:
    """Locate and classify every generated region in *text*.

    Split from `audit` so the region logic can be exercised on a synthetic
    source without writing a file: the fleet's negative-control rule wants a
    checker proven against mutated inputs, and a gate that needs a scratch
    directory to prove itself is a gate with a second failure mode.

    Raises SystemExit on an unbalanced or malformed marker pair -- a lost marker
    must be loud, because its silent form is a region nobody updates again.
    """
    lines = text.splitlines(keepends=True)
    regions: List[Region] = []
    open_at: Optional[int] = None
    open_names: List[str] = []

    open_indent = ""
    for idx, comment in marker_comments(label, text):
        if comment.startswith(BEGIN_PREFIX):
            if open_at is not None:
                raise SystemExit(
                    f"{label}:{idx + 1}: BEGIN inside the region opened at "
                    f"line {open_at + 1}"
                )
            open_at = idx
            open_names = parse_names(comment, label, idx + 1)
            raw = lines[idx]
            open_indent = raw[:len(raw) - len(raw.lstrip())]
        else:
            if open_at is None:
                raise SystemExit(f"{label}:{idx + 1}: END without a BEGIN")
            regions.append(Region(
                names=open_names,
                begin=open_at,
                end=idx,
                recorded=comment[len(END_PREFIX):].strip(),
                body="".join(lines[open_at + 1:idx]),
                wanted=render(open_names, blocks, open_indent),
                indent=open_indent,
            ))
            open_at = None

    if open_at is not None:
        raise SystemExit(
            f"{label}:{open_at + 1}: BEGIN without an END -- the region is open"
        )
    return regions


def apply_regions(path: Path, regions: List[Region]) -> None:
    """Rewrite *path*, replacing each region's body and END hash."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # Back to front, so an earlier region's indices stay valid.
    for region in sorted(regions, key=lambda r: r.begin, reverse=True):
        lines[region.begin + 1:region.end + 1] = [
            region.wanted,
            f"{region.indent}{END_PREFIX} {body_hash(region.wanted)}\n",
        ]
    path.write_text("".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Inline the shared _mcp_json.py blocks into the MCP servers."
    )
    ap.add_argument("targets", nargs="*", type=Path,
                    help=f"files to process (default: Scripts/{TARGET_GLOB})")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if any region is stale")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a hand-edited region instead of refusing")
    args = ap.parse_args(argv)

    blocks = load_blocks(CANONICAL)
    targets = args.targets or sorted(SCRIPTS_DIR.glob(TARGET_GLOB))

    stale = refused = 0
    for path in targets:
        pending = []
        for region in audit(path, blocks):
            if region.state == "ok":
                continue
            if region.state == "hand-edited" and not args.force:
                print(f"HAND-EDITED: {path.name} [{region.label}] -- recorded "
                      f"{region.recorded}, found {body_hash(region.body)}; "
                      f"re-run with --force to discard the edit")
                refused += 1
                continue
            pending.append(region)

        if not pending:
            continue
        stale += len(pending)
        labels = "; ".join(r.label for r in pending)
        if args.check:
            print(f"CHANGED: {path.name} [{labels}] -- run: "
                  f"python3 Scripts/amalgamate.py")
        else:
            apply_regions(path, pending)
            print(f"updated: {path.name} [{labels}]")

    if refused:
        return 1
    if args.check:
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
