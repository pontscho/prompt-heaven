#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Inline the shared helper blocks from the canonical sources into the servers.

The servers stay single-file (see `_mcp_json.py` for why an import was rejected),
so the shared code is *generated into* each one instead. A region looks like:

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region.
    # BEGIN GENERATED: _mcp_json.py :: _json_error_window
    def _json_error_window(...):
        ...
    # END GENERATED: 3f9a1c2b7d40

Four rules earn their keep, and each one is a failure mode somebody has already
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
* **The source named on the BEGIN line SELECTS the block map.** There is more
  than one canonical file -- see `CANONICAL_SOURCES` -- and each one is a
  domain, not a shelf: a name is resolved only against the source its own
  region names, so a marker asking `_mcp_json.py` for an LSP block is refused
  rather than quietly served out of the other file. The registry is written
  down by hand instead of globbed from `_mcp_*.py`, because a glob would let an
  unrelated file become a generation source by merely existing, and the marker
  naming it would look exactly as legitimate as the ones that belong.

Paths are resolved from `__file__`, so `python3 ../../Scripts/amalgamate.py`
works from any working directory, and `resolve()` follows a symlink to the real
directory on purpose -- `~/.claude/scripts` is one.

Usage:
    python3 Scripts/amalgamate.py                 # rewrite every region
    python3 Scripts/amalgamate.py --check         # write nothing, exit 1 if stale
    python3 Scripts/amalgamate.py --force         # discard a hand edit
    python3 Scripts/amalgamate.py --census fleet  # count the live regions
    python3 Scripts/amalgamate.py --census sources  # count what the sources define
    python3 Scripts/amalgamate.py --census hosts  # which files carry each block
    python3 Scripts/amalgamate.py --census hand-copies  # names kept by hand, and why
    python3 Scripts/amalgamate.py Scripts/mcp-purity.py
"""

import argparse
import ast
import builtins
import hashlib
import io
import sys
import textwrap
import tokenize
from pathlib import Path
from typing import Collection, Dict, List, NamedTuple, Optional, Set, Tuple

BUILTIN_NAMES = frozenset(dir(builtins))

BEGIN_PREFIX = "# BEGIN GENERATED:"
END_PREFIX = "# END GENERATED:"
TARGET_GLOB = "mcp-*.py"

SCRIPTS_DIR = Path(__file__).resolve().parent

# The canonical sources, one per DOMAIN, named explicitly on purpose: a glob
# over `_mcp_*.py` would silently promote the next helper file somebody drops
# into Scripts/ to a generation source, and a region naming it would read as
# legitimate as any other. Adding a source is a deliberate edit here.
CANONICAL_NAMES = ("_mcp_concurrency.py", "_mcp_json.py", "_mcp_logging.py",
                   "_mcp_lsp.py", "_mcp_paging.py", "_mcp_websocket.py")
CANONICAL_SOURCES = {name: SCRIPTS_DIR / name for name in CANONICAL_NAMES}

# Hosts OUTSIDE `TARGET_GLOB` that take generated blocks, each named by hand for
# the reason the source registry is: widening the glob would make every script
# in Scripts/ a target by merely existing. A host listed here is a single-file
# script with the same import-free constraint as a server -- agents run the
# search script by path and without -B, so an imported sibling would write
# Scripts/__pycache__ on every search, and a copy of the one file taken alone
# would stop at an ImportError. It takes the default run and `--check` exactly
# as a server does; the
# `--census fleet` count stays the MCP glob's, because that census is ABOUT the
# server fleet and says so in its first line.
DECLARED_HOSTS = ("search_duckduckgo.py",)

# The repo root, derived the same way `SCRIPTS_DIR` is and for the same reason:
# `Scripts/` is a directory of this repository, so its parent is the root that
# every anchor in the wiki is relative to. Used only by the census, to spell a
# path the way a documentation page spells one.
REPO_ROOT = SCRIPTS_DIR.parent

# The census subjects. The fleet and the sources answer different questions and
# change on different events: a region is added when a SERVER is converted, a
# block when a canonical SOURCE grows. The last two turn the region walk round
# to face the blocks -- which files carry each one, and which files keep a
# canonical name by hand instead. See `census_text`.
CENSUS_KINDS = ("fleet", "sources", "hosts", "hand-copies")

# ONE WRITER, with the exceptions NAMED rather than hidden: every hand-written
# copy of a canonical block name a host keeps outside a generated region, keyed
# by (host filename, block name), with the MEASURED reason it is not generated.
#
# It lives HERE and not in the suite because the census renders it: a reason is
# half of what `--census hand-copies` prints, and a page cannot render a test
# comment. The suite consumes this map and the walk below rather than carrying a
# copy of either -- one implementation of the census, gated from the outside.
#
# An absent entry is not an error, it is a row the census marks UNDECLARED: "this
# host keeps its own" is a legitimate answer, so the census reports rather than
# judges -- but an undeclared copy cannot appear without landing on the page. An
# entry whose copy has gone is reported too, as a reason with nothing left to
# explain.
#
# The two `_max_answer_chars` / `_rows_note` entries are excluded TWICE OVER, so
# settling either half alone would not make the block adoptable. The `_error`
# entry is the odd one out: NOTHING measurable keeps it out -- it is
# byte-identical to the canonical once re-indented, tab-safe, and its one free
# name is imported there. It stays by hand only because every other server takes
# `_result` and `_error` co-listed on one marker and mcp-webfetch cannot take the
# first; split that marker and it stops being an exclusion, while `_result` stays
# one for a reason no granularity can touch.
HAND_COPY_REASONS: Dict[Tuple[str, str], str] = {
    ("mcp-git.py", "_max_answer_chars"):
        "excluded twice over: it defaults to its own DEFAULT_MAX_CHARS rather "
        "than to the value the canonical block renders its reader with, and its "
        "body carries a camelCase fallback loop the canonical has no trace of",
    ("mcp-inspect.py", "_int_param"):
        "takes a parameter NAME and raises, where the canonical takes a default "
        "and falls back to it",
    ("mcp-tshark.py", "_bool_param"):
        "keeps the older (params, key, default) signature",
    ("mcp-webfetch.py", "_bool_param"):
        "an ALLOW-list, so an unrecognised string reads False here and True "
        "canonically",
    ("mcp-webfetch.py", "_error"):
        "nothing measurable: byte-identical to the canonical once re-indented "
        "for a tab host, kept by hand only because every other server takes it "
        "co-listed with _result on one marker and this host cannot take _result",
    ("mcp-webfetch.py", "_result"):
        "annotates result as dict where the canonical says Any -- a body "
        "difference no re-indenting removes",
    ("mcp-webfetch.py", "_rows_note"):
        "excluded twice over: it is not tab-safe (its else aligns under an open "
        "paren) and its body diverged -- (start, shown, total) against the "
        "canonical (start, shown, total, exact), with no lower-bound branch",
}


class Region(NamedTuple):
    """One generated region located in a target file."""

    source: str         # the canonical file its names are resolved against
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


def load_all_blocks() -> Dict[str, Dict[str, str]]:
    """Map every canonical FILENAME to its own block map.

    The two levels are the whole of multi-source support: a region names a
    source, and only that source's names are in scope for it. Flattening these
    into one namespace would make a marker's source field decorative, and the
    first collision between two domains would resolve to whichever file loaded
    last -- a silent choice nobody wrote down.
    """
    return {name: load_blocks(path) for name, path in CANONICAL_SOURCES.items()}


def assign_name(node: ast.stmt) -> Optional[str]:
    """The one name a module-level CONSTANT binds, or None if it is not one.

    A constant is a block like any other -- `DEFAULT_MAX_ANSWER_CHARS` is a
    number six servers agree on, and agreement on disk is what rots -- but only
    ONE assignment shape can carry a block, and the rest are out for reasons
    that differ:

    * `A = B = 1` and `A, B = f()` bind SEVERAL names from ONE statement. The
      slice is indivisible, so `blocks["A"]` would emit a statement that also
      defines `B` in the host -- a name the marker never mentions, in a region
      no human is supposed to read closely. A marker must be the complete
      statement of what its region brings in.
    * `x.attr = 1` and `x[0] = 1` bind no name at all; there is nothing to key
      the map on, and the target is a name the HOST owns.
    * `X += 1` is the dangerous one. Its target carries a `Store` context, so
      `free_names` BINDS `X` and reports nothing -- yet the statement cannot
      run unless the host already defines `X`. A block whose precondition is
      structurally invisible to the contract check is exactly the failure
      `free_names` exists to prevent, so the shape is refused rather than
      checked by a check that cannot see it.
    * `X: int = 1` is left out on demand, not on principle: no queued block is
      one, and accepting the form would also accept `X: int`, which binds
      nothing at run time -- a block that renders cleanly and NameErrors at the
      host's first read.

    Nothing is raised here. This maps what it can and leaves the rest alone,
    because the rule is also pointed at the HOSTS (`bound_names`, the walk the
    hand-copy census runs, does exactly that) and ordinary module-level
    statements must not turn a drift gate into a traceback about an unrelated
    line. The skip is not silent where it matters: a name only enters the
    system by being written on a marker, and `render` refuses an unknown one BY
    NAME.
    """
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def load_blocks_text(label: str, text: str) -> Dict[str, str]:
    """Map every top-level name in *text* to its verbatim source text.

    Three definition shapes qualify -- a function, a class, and a single-target
    constant (`assign_name` holds the argument for that last one and for every
    assignment form it turns down).

    The slice starts at the first DECORATOR line when there is one, not at the
    `def`: `ast` puts a decorated function's `lineno` on the `def`, so slicing
    from there would drop `@staticmethod` and silently turn a method into an
    instance method that takes the class's first argument as its own. That is
    the difference between a shareable `_result` -- byte-identical in fourteen
    servers -- and a server that raises on its first reply. An assignment has
    no `decorator_list` at all, so the hoist is reached only for the node types
    that have one.
    """
    lines = text.splitlines(keepends=True)
    blocks: Dict[str, str] = {}
    try:
        tree = ast.parse(text, filename=label)
    except SyntaxError as exc:
        raise SystemExit(f"{label}: cannot be parsed ({exc})")
    for node in tree.body:
        start = node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name: Optional[str] = node.name
            if node.decorator_list:
                start = min(start, min(d.lineno for d in node.decorator_list))
        else:
            name = assign_name(node)
            if name is None:
                continue
        blocks[name] = "".join(lines[start - 1:node.end_lineno])
    return blocks


def free_names(block: str) -> Set[str]:
    """Names *block* READS but does not define -- what its host must provide.

    This is the block contract turned into a computation instead of a promise.
    Two failure modes it catches, both of which were live holes:

    * A block that calls another canonical block. Emitting `_max_answer_chars`
      without `_int_param` leaves a call to a name the host may not define.
    * A block whose ANNOTATION needs an import. `msg_id: Any` is evaluated at
      def time, so a host that does not import `Any` dies at startup -- and the
      canonical file imports it, so the block looks fine where it is written.

    A CONSTANT block needs nothing added here, and that is a property of the
    walk rather than luck: the target of `_FENCE_LINE_RE = re.compile(...)`
    is an `ast.Name` in a `Store` context, so the generic arm below binds it,
    while `re` is read and reported. Nothing in this function assumes a
    function scope -- `ast.walk` descends the whole tree and every binder is
    matched by node type, so a module-level statement is analysed on the same
    terms as a `def` body. The consequence is the one that matters: the first
    constant to reference a stdlib name carries that import requirement to
    every host that asks for it, exactly as an annotation does.

    Scope analysis is deliberately crude and errs toward reporting too much: a
    false report is a loud refusal naming the symbol, which costs a line in the
    region marker, while a miss is a dead server.
    """
    tree = ast.parse(textwrap.dedent(block))
    bound: Set[str] = set()
    used: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.Name):
            (bound if isinstance(node.ctx, ast.Store) else used).add(node.id)
    return {name for name in used - bound if name not in BUILTIN_NAMES}


def host_provides(text: str) -> Set[str]:
    """Module-level names *text* imports -- the host half of the contract."""
    provided: Set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return provided
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                provided.add((alias.asname or alias.name).split(".")[0])
    return provided


def parse_names(marker: str, label: str, lineno: int,
                known: Collection[str]) -> Tuple[str, List[str]]:
    """Split a BEGIN marker into (source filename, requested names).

    *known* is the set of canonical filenames in play -- normally the keys of
    `load_all_blocks()`. An unrecognised source is refused by NAME and the
    refusal lists what is on offer: the reader of that message is somebody who
    just mistyped a filename or invented one, and a bare "unknown source" would
    send them to the generator's source to find out what the alternatives are.
    """
    spec = marker[len(BEGIN_PREFIX):].strip()
    if "::" not in spec:
        raise SystemExit(
            f"{label}:{lineno}: expected "
            f"'{BEGIN_PREFIX} <source> :: <name>[, <name>...]', got {marker!r}"
        )
    source, listed = spec.split("::", 1)
    source = source.strip()
    if source not in known:
        raise SystemExit(
            f"{label}:{lineno}: unknown source {source!r}; "
            f"the generated sources are {', '.join(sorted(known))}"
        )
    names = [n.strip() for n in listed.split(",") if n.strip()]
    if not names:
        raise SystemExit(f"{label}:{lineno}: the region lists no names")
    return source, names


def block_is_tab_safe(block: str) -> bool:
    """True when *block*'s indentation is purely STRUCTURAL, so tabs can carry it.

    Re-indenting is only safe where every leading run of whitespace means "one
    more nesting level". Where it means "line this token up under that one",
    a tab puts the code in a column nobody chose -- and the reader of a
    generated region is the least likely person to notice.

    Two independent checks, and a block must pass both, because they fail on
    different halves of the same hazard:

    * **No implicit line join.** A physical newline while a bracket is open is
      the shape that produces alignment in the first place. Decided with
      `tokenize`, which is already how markers are found.
    * **Every indent is a whole 4-space level.** A leading run that is not a
      multiple of four is alignment by arithmetic, wherever it came from --
      including inside a string, where the tokenizer sees one atom and has
      nothing to say.

    Anything unprovable is unsafe: a tokenizer failure, a stray tab, a
    backslash continuation. Of the canonical blocks exactly one fails --
    `_rows_note`, whose `else` aligns under an open paren three times.
    """
    lines = block.splitlines()
    for line in lines:
        if not line.strip():
            continue
        lead = line[:len(line) - len(line.lstrip())]
        if "\t" in lead or len(lead) % 4:
            return False
        if line.rstrip().endswith("\\"):
            return False
    try:
        depth = 0
        for tok in tokenize.generate_tokens(io.StringIO(block).readline):
            if tok.type == tokenize.OP:
                if tok.string in "([{":
                    depth += 1
                elif tok.string in ")]}":
                    depth -= 1
            elif tok.type == tokenize.NL and depth > 0:
                return False
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return False
    return True


def to_tabs(block: str) -> str:
    """Rewrite each 4-space indent LEVEL as one tab. Leading whitespace only.

    Only ever called on a block `block_is_tab_safe` has cleared, so the integer
    division cannot silently drop a remainder: there is none.
    """
    out = []
    for line in block.splitlines(keepends=True):
        body = line.lstrip(" ")
        out.append("\t" * ((len(line) - len(body)) // 4) + body)
    return "".join(out)


def host_indent(text: str) -> str:
    """'tab' or 'space' -- the host's own convention, read off its INDENT tokens.

    The marker's own column cannot answer this: a module-level region sits at
    column 0 and carries no signal at all, and that is the majority of them.
    A host that cannot be tokenized reads as 'space', which is the status quo
    and therefore the change-nothing answer.
    """
    tabs = spaces = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.INDENT:
                if tok.string.startswith("\t"):
                    tabs += 1
                else:
                    spaces += 1
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return "space"
    return "tab" if tabs > spaces else "space"


def render(source: str, names: List[str], blocks: Dict[str, str],
           indent: str = "", tabs: bool = False, label: str = "") -> str:
    """Emit the named blocks, each line prefixed with *indent*.

    *blocks* is the block map of *source* alone, and *source* is carried only
    so the refusal can name the file that was actually asked. A name defined in
    a DIFFERENT canonical file must not resolve here: crossing that line is how
    a JSON helper would end up pasted in on the strength of a marker that says
    LSP, and the region would look correct in every server that carries it.

    The prefix is taken from the BEGIN marker's own column, which is what lets a
    region sit inside a class body: `_result` and `_error` are byte-identical
    methods in fourteen servers, and the marker's indentation is the only piece
    of information needed to place them.

    *tabs* re-indents the block for a TAB-indented host, one tab per 4-space
    level, before the prefix is applied. **This refusal is PER BLOCK, and that
    narrows an earlier decision that made it per FILE** (recorded as "tab
    re-indentation refused"). The old reasoning was right about the block it
    was drawn from and wrong to generalise: `_rows_note` aligns an `else` under
    an open paren, so tabs would move it, but EVERY OTHER canonical block
    contains no bracket continuation at all and its indentation is purely
    structural. That is not a count typed here and checked by nobody -- the
    suite's `tab-safety-real-blocks` asserts the unsafe set is exactly
    `_rows_note`, over the real canonical text, and a constant added to a
    source moves that set or fails there. Refusing those cost `mcp-forge`
    three hand copies that
    were byte-identical to the canonical text modulo the indent character.
    `block_is_tab_safe` now decides it mechanically, per block, and a block it
    cannot clear is refused BY NAME rather than quietly emitted with spaces --
    a file mixing both is worse than either.

    `mcp-webfetch` hosts two tab-rendered regions of its own, so what keeps its
    `_result` and `_bool_param` out is NOT indentation: its `_result` annotates
    `result: dict` where the canonical says `result: Any`, and its `_bool_param`
    is an ALLOW-list where this one is a deny-list, so an unrecognised string
    reads False there and True here. Those are body and behaviour differences;
    they would survive any amount of re-indenting.
    """
    where = f"{label}: " if label else ""
    out = []
    for name in names:
        if name not in blocks:
            raise SystemExit(
                f"{where}{source} defines no top-level {name!r}"
            )
        body = blocks[name]
        if tabs:
            if not block_is_tab_safe(body):
                raise SystemExit(
                    f"{where}{source} :: {name!r} cannot be generated into a "
                    f"TAB-indented host: some of its indentation is ALIGNMENT, "
                    f"not nesting -- an implicit line join, or a leading run "
                    f"that is not a whole 4-space level -- and a tab there "
                    f"moves the code to a column nobody chose. Keep this one "
                    f"as a hand copy in this file and say why"
                )
            body = to_tabs(body)
        out.append(body.rstrip("\n"))
    # Two blank lines between top-level definitions, one between members of a
    # class -- PEP 8's own split, and the indent already says which we are in.
    #
    # This is the one place that still assumes a block is a DEFINITION: PEP 8
    # puts no blank line between two adjacent constants, and nothing here can
    # say so. It still costs nothing, though the reason narrowed when
    # `_max_answer_chars` was lifted: a constant is given a region of its OWN
    # unless a block READS it, and the one pair that exists renders a constant
    # followed by a def, which is exactly where PEP 8 wants two blank lines.
    # Two adjacent CONSTANTS in one region is the shape that would be wrong,
    # and nothing asks for it. A region per constant also keeps the sentence
    # explaining what the number is for -- host-specific prose -- above the
    # BEGIN marker where it was written.
    separator = "\n\n" if indent else "\n\n\n"
    text = separator.join(out) + "\n"
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


def audit(path: Path, sources: Dict[str, Dict[str, str]]) -> List[Region]:
    """Locate and classify every generated region in *path*."""
    if not path.is_file():
        raise SystemExit(f"{path}: not a file")
    return audit_text(path.name, path.read_text(encoding="utf-8"), sources)


def audit_text(label: str, text: str,
               sources: Dict[str, Dict[str, str]]) -> List[Region]:
    """Locate and classify every generated region in *text*.

    *sources* maps a canonical FILENAME to that file's block map -- what
    `load_all_blocks()` returns. Each region is resolved against the one entry
    its own BEGIN marker names, never against the union.

    Split from `audit` so the region logic can be exercised on a synthetic
    source without writing a file: the fleet's negative-control rule wants a
    checker proven against mutated inputs, and a gate that needs a scratch
    directory to prove itself is a gate with a second failure mode.

    Raises SystemExit on an unbalanced or malformed marker pair -- a lost marker
    must be loud, because its silent form is a region nobody updates again.
    """
    lines = text.splitlines(keepends=True)
    provided = host_provides(text)
    tabs = host_indent(text) == "tab"
    regions: List[Region] = []
    open_at: Optional[int] = None
    open_names: List[str] = []
    open_source = ""

    open_indent = ""
    for idx, comment in marker_comments(label, text):
        if comment.startswith(BEGIN_PREFIX):
            if open_at is not None:
                raise SystemExit(
                    f"{label}:{idx + 1}: BEGIN inside the region opened at "
                    f"line {open_at + 1}"
                )
            open_at = idx
            open_source, open_names = parse_names(comment, label, idx + 1, sources)
            raw = lines[idx]
            open_indent = raw[:len(raw) - len(raw.lstrip())]
        else:
            if open_at is None:
                raise SystemExit(f"{label}:{idx + 1}: END without a BEGIN")
            wanted = render(open_source, open_names, sources[open_source],
                            open_indent, tabs=tabs, label=label)
            missing = sorted(free_names(wanted) - provided)
            if missing:
                raise SystemExit(
                    f"{label}:{open_at + 1}: the region needs {missing}, which "
                    f"{label} neither imports nor lists in the region -- add the "
                    f"name to the marker if it is another block, import it in the "
                    f"host if it is not"
                )
            regions.append(Region(
                source=open_source,
                names=open_names,
                begin=open_at,
                end=idx,
                recorded=comment[len(END_PREFIX):].strip(),
                body="".join(lines[open_at + 1:idx]),
                wanted=wanted,
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


# ---------------------------------------------------------------------------
# `--census` -- the numbers a page states in prose, printed by the one thing
# that already knows them
#
# Every count `docs/components/generated-regions.md` makes about this mechanism
# is already derivable from the walk above: how many regions are live, how many
# block instances they emit, which markers name two blocks, which blocks reach
# every server, what each canonical source defines. Typed into prose those
# numbers rot -- two of them had been found stale and corrected by hand before
# this flag existed -- so the wiki RENDERS them from here instead, through
# `docs/measurements.json`, which names this argv.
#
# Three properties, each a requirement of that consumer rather than a taste:
#
#   * IT WRITES NOTHING, AND IT IS THE ONLY MODE THAT CANNOT. The branch in
#     `main` returns before the staleness loop, so the one mode a documentation
#     page runs unattended can neither repair nor rewrite the tree it is
#     describing. `--check` and `--force` are IGNORED beside it rather than
#     combined with it: a pair of flags meaning "report" and "write" has no
#     useful intersection, and the safe reading of the pair is the read one.
#   * IT IS SORTED, NOT MERELY STABLE. The body is hashed and the digest is
#     compared against a page, so an iteration order that merely happens to
#     come out the same way today turns every page carrying it into a false
#     `stale` tomorrow. Every list here is sorted by name, and the one
#     frequency table is sorted by count descending with ties broken by name --
#     an order a test can ASSERT, where "it came out the same twice" is only a
#     run it reproduced.
#   * EVERY NUMBER CARRIES ITS SUBJECT. This body lands inside a page, where
#     "111 regions" is a number no reader can check. What is being counted is
#     written beside each count, once.
#
# A CELL CANNOT FORGE A COLUMN (adr 0016). Every cell rendered here is a Python
# identifier or a repo-relative path, and neither can contain a `|`, so these
# tables have no escaping scheme because they need none -- which is a declared
# answer, not an omission. Names and paths are rendered in the wiki's own anchor
# spelling, backticked and repo-root-relative, so `wiki_call verify` resolves
# them against the tree instead of taking the rendered page's word for them.
# ---------------------------------------------------------------------------


def repo_relative(path: Path) -> str:
    """*path* as a repo-root-relative POSIX string -- the wiki's anchor spelling.

    A path outside the repo falls back to its bare name rather than raising: the
    census is a report, and a target somebody passed from elsewhere is a thing
    to name, not a reason to refuse the whole count.
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def census_names(names: Collection[str]) -> str:
    """A list of names as backticked code spans, in the order given.

    The ORDER IS THE CALLER'S. Every caller here sorts, and sorts for the reason
    in the note above; taking the sort away from this helper keeps the one place
    that could silently unsort everything from being a shared default.
    """
    return ", ".join("`%s`" % name for name in names)


def census_fleet(sources: Dict[str, Dict[str, str]],
                 targets: List[Path]) -> List[str]:
    """What the LIVE regions are, across the servers -- one page body, as lines."""
    servers = sorted(targets, key=lambda p: p.name)
    server_names = [repo_relative(path) for path in servers]
    rows: List[Tuple[str, Region]] = []
    for path, host in zip(servers, server_names):
        for region in audit(path, sources):
            rows.append((host, region))

    defined = {name for blocks in sources.values() for name in blocks}
    named = {name for _host, region in rows for name in region.names}
    hosting = {host for host, _region in rows}
    instances = sum(len(region.names) for _host, region in rows)

    arity: Dict[int, int] = {}
    markers: Dict[Tuple[str, ...], int] = {}
    hosts: Dict[str, Set[str]] = {}
    for host, region in rows:
        size = len(region.names)
        arity[size] = arity.get(size, 0) + 1
        if size > 1:
            key = tuple(region.names)
            markers[key] = markers.get(key, 0) + 1
        for name in region.names:
            hosts.setdefault(name, set()).add(host)

    out = [
        "- MCP servers matching `Scripts/%s`: %d, of which %d carry at least "
        "one generated region" % (TARGET_GLOB, len(servers), len(hosting)),
        "- live generated regions in them: %d" % len(rows),
        "- block instances those regions emit: %d" % instances,
        "- distinct canonical blocks named on a marker: %d, out of the %d "
        "defined by the %d canonical sources" % (len(named), len(defined),
                                                 len(sources)),
        "",
        "Regions by how many blocks one marker names: %s."
        % "; ".join("%d name %d block%s"
                    % (count, size, "" if size == 1 else "s")
                    for size, count in sorted(arity.items())),
    ]

    if markers:
        # Sorted by frequency and then by the name list, so the table reads as a
        # ranking AND cannot reorder on a tie. The names keep the marker's own
        # order -- dependency first, which is the order every co-listed region
        # is required to spell -- so two markers that disagree about the order
        # tally as the two different spellings they are.
        out += ["",
                "The %d region(s) that name more than one block, by the list "
                "written on the marker:" % sum(markers.values()),
                "",
                "| Blocks named on one marker | Regions |",
                "|---|---|"]
        out += ["| %s | %d |" % (census_names(key), count)
                for key, count in sorted(markers.items(),
                                         key=lambda kv: (-kv[1], kv[0]))]

    total = len(servers)
    everywhere = sorted(name for name, seen in hosts.items()
                        if len(seen) == total)
    out += ["",
            "Generated into every one of the %d servers: %s."
            % (total, census_names(everywhere) if everywhere
               else "no block at all")]

    # "In all but one" is reported WITH the holdout named, because the count
    # alone does not say whether the missing server keeps a hand copy or simply
    # has no use for the block -- and the name is what a reader checks.
    holdouts: Dict[str, List[str]] = {}
    for name, seen in hosts.items():
        if len(seen) == total - 1:
            holdouts.setdefault(sorted(set(server_names) - seen)[0],
                                []).append(name)
    if holdouts:
        out += ["Generated into every server but `%s`: %s."
                % (missing, census_names(sorted(holdouts[missing])))
                for missing in sorted(holdouts)]
    else:
        out.append("No block is generated into all but exactly one server.")
    return out


def census_sources(sources: Dict[str, Dict[str, str]]) -> List[str]:
    """What the canonical sources DEFINE -- one page body, as lines."""
    out = ["| Canonical source | Blocks | Block names |", "|---|---|---|"]
    owners: Dict[str, List[str]] = {}
    total = 0
    for name in sorted(sources):
        blocks = sorted(sources[name])
        total += len(blocks)
        for block in blocks:
            owners.setdefault(block, []).append(name)
        path = CANONICAL_SOURCES.get(name)
        out.append("| `%s` | %d | %s |"
                   % (repo_relative(path) if path else name,
                      len(blocks), census_names(blocks)))
    # Disjointness is REPORTED, not assumed. A name defined by two sources makes
    # the marker's source field decorative for it, which is the one property the
    # multi-source design exists to protect -- so it is named here rather than
    # left to be inferred from a total that still adds up.
    shared = sorted(block for block, held in owners.items() if len(held) > 1)
    out += ["",
            "%d canonical sources define %d blocks between them, and %s."
            % (len(sources), total,
               "no name is defined by two of them" if not shared
               else "these names are defined by more than one: %s"
                    % census_names(shared))]
    return out


def census_hosts(sources: Dict[str, Dict[str, str]],
                 targets: List[Path]) -> List[str]:
    """Every canonical block and the files that carry it -- one page body.

    One row per block EVERY source defines, hosted or not: a block no file asks
    for is a row reading 0, never a missing row, because a table that drops the
    zeros cannot be told apart from one that forgot a source. Keyed by (source,
    block) since a region resolves its names against its OWN source; a host is
    counted once however many of its regions name the block.
    """
    hosts: Dict[Tuple[str, str], Set[str]] = {
        (source, block): set()
        for source, blocks in sources.items() for block in blocks}
    for path in targets:
        for region in audit(path, sources):
            for block in region.names:
                hosts.setdefault((region.source, block), set()).add(
                    repo_relative(path))

    def spelled(source: str) -> str:
        path = CANONICAL_SOURCES.get(source)
        return repo_relative(path) if path else source

    out = ["| Canonical block | Source | Hosts | Generated into |",
           "|---|---|---|---|"]
    for (source, block), held in sorted(hosts.items(),
                                        key=lambda kv: (spelled(kv[0][0]),
                                                        kv[0][1])):
        out.append("| `%s` | `%s` | %d | %s |"
                   % (block, spelled(source), len(held),
                      census_names(sorted(held)) if held else "no host"))
    idle = sorted(block for (_source, block), held in hosts.items() if not held)
    out += ["",
            "%d hosts scanned; %d canonical blocks, of which %d are generated "
            "into at least one host; %s."
            % (len(targets), len(hosts), len(hosts) - len(idle),
               "every block reaches a host" if not idle
               else "generated into no host: %s" % census_names(idle))]
    return out


def bound_names(label: str, text: str) -> Set[str]:
    """Every name *text* binds at module top level OR as a direct class member.

    `load_blocks_text` walks `tree.body` and nothing else, and that is RIGHT for
    a canonical SOURCE: a block is a thing a marker can name and paste at the
    marker's column, and a method buried in a class is not one. The hand-copy
    census points the question at a HOST, where it is a different one -- "does
    this file bind a canonical name anywhere the generator is not writing it"
    -- and the answer has to include the class body, because that is where the
    shared shape actually lives. `_result` and `_error` are METHODS in every
    server; a census that saw only module level would report no hand copy of
    either, anywhere, ever -- green by construction rather than by measurement.

    So this is a SECOND walk, not a widened `load_blocks_text`: widening that
    one would widen what may become a BLOCK, which is the one thing that must
    not happen. `assign_name` is reused for both scopes, because which
    assignment shapes bind exactly one nameable thing is one rule.
    """
    try:
        tree = ast.parse(text, filename=label)
    except SyntaxError as exc:
        raise SystemExit(f"{label}: cannot be parsed ({exc})")
    scopes = [tree.body] + [node.body for node in tree.body
                            if isinstance(node, ast.ClassDef)]
    names: Set[str] = set()
    for body in scopes:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                names.add(node.name)
            else:
                bound = assign_name(node)
                if bound is not None:
                    names.add(bound)
    return names


def hand_copies_text(label: str, text: str,
                     sources: Dict[str, Dict[str, str]]) -> List[str]:
    """The canonical block names *text* binds by hand, outside every region.

    Split from `hand_copies` for the reason `audit_text` is split from `audit`:
    the suite proves the walk on a synthetic host without writing a file.
    """
    known = {name for blocks in sources.values() for name in blocks}
    covered: Set[str] = set()
    for region in audit_text(label, text, sources):
        covered.update(region.names)
    return sorted((bound_names(label, text) & known) - covered)


def hand_copies(sources: Dict[str, Dict[str, str]],
                targets: List[Path]) -> List[Tuple[str, str]]:
    """(repo-relative host, name) for every hand copy in *targets*, sorted."""
    found: List[Tuple[str, str]] = []
    for path in targets:
        if not path.is_file():
            raise SystemExit(f"{path}: not a file")
        text = path.read_text(encoding="utf-8")
        found += [(repo_relative(path), name)
                  for name in hand_copies_text(path.name, text, sources)]
    return sorted(found)


def census_hand_copies(sources: Dict[str, Dict[str, str]],
                       targets: List[Path]) -> List[str]:
    """Every hand-kept copy of a canonical block name, with its reason."""
    entries = hand_copies(sources, targets)
    out = []
    declared = 0
    for host, name in entries:
        reason = HAND_COPY_REASONS.get((Path(host).name, name))
        declared += reason is not None
        out.append("- `%s`: `%s` -- %s"
                   % (host, name, "declared: %s" % reason if reason is not None
                      else "UNDECLARED: no reason is on record for this copy"))
    carrying = len({host for host, _name in entries})
    if entries:
        out += ["",
                "%d hand-written copies of a canonical block name, bound at "
                "module level or as a direct class member outside every "
                "generated region, in %d of the %d hosts scanned; %d carry a "
                "declared reason and %d do not."
                % (len(entries), carrying, len(targets), declared,
                   len(entries) - declared)]
    else:
        out.append("None of the %d hosts scanned keeps a hand copy of a "
                   "canonical block name." % len(targets))
    # A reason whose copy is gone is a false sentence on the page, so it is
    # NAMED -- scoped to the hosts actually scanned, since a reason about a file
    # nobody asked for is not stale, only out of view.
    scanned = {path.name: repo_relative(path) for path in targets}
    live = {(Path(host).name, name) for host, name in entries}
    orphans = sorted((scanned[host], name) for host, name in HAND_COPY_REASONS
                     if host in scanned and (host, name) not in live)
    out.append("Declared reasons with no hand copy left to explain: %s."
               % ("; ".join("`%s`: `%s`" % pair for pair in orphans)
                  if orphans else "none"))
    return out


def census_text(kind: str, sources: Dict[str, Dict[str, str]],
                targets: List[Path]) -> str:
    """One census body, ending in exactly one newline.

    The unknown-kind refusal is not dead code behind argparse's `choices`: the
    suite calls this function directly, and a census kind that silently printed
    nothing would render an EMPTY region into a page -- a block that says
    nothing where a number is owed, which is worse than the typed number it
    replaced.
    """
    if kind == "fleet":
        lines = census_fleet(sources, targets)
    elif kind == "sources":
        lines = census_sources(sources)
    elif kind == "hosts":
        lines = census_hosts(sources, targets)
    elif kind == "hand-copies":
        lines = census_hand_copies(sources, targets)
    else:
        raise SystemExit("unknown census %r; the subjects are %s"
                         % (kind, ", ".join(CENSUS_KINDS)))
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Inline the shared blocks from the canonical sources "
                    f"({', '.join(CANONICAL_NAMES)}) into the MCP servers."
    )
    ap.add_argument("targets", nargs="*", type=Path,
                    help=f"files to process (default: Scripts/{TARGET_GLOB} "
                         f"plus {', '.join(DECLARED_HOSTS)})")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if any region is stale")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a hand-edited region instead of refusing")
    ap.add_argument("--census", choices=CENSUS_KINDS,
                    help="write NOTHING and print a census: 'fleet' counts the "
                         "live regions in the targets, 'sources' counts what "
                         "the canonical sources define, 'hosts' lists which "
                         "files carry each canonical block, 'hand-copies' "
                         "lists the canonical names a file keeps by hand, "
                         "with the declared reason. Returns before any "
                         "staleness check, so --check and --force do not apply")
    args = ap.parse_args(argv)

    sources = load_all_blocks()
    fleet = sorted(SCRIPTS_DIR.glob(TARGET_GLOB))
    targets = args.targets or fleet + [SCRIPTS_DIR / name for name in DECLARED_HOSTS]

    # THE READ PATH, and it is first on purpose: nothing below this line runs
    # for a census, so there is no ordering, no flag combination and no later
    # edit to this function that can make the reporting mode write. The fleet
    # census defaults to the server fleet alone -- see `DECLARED_HOSTS` for why
    # -- while `hosts` and `hand-copies` ask about every file the generator
    # WRITES, so they default to the same set the rewrite and `--check` walk.
    if args.census:
        default = fleet if args.census == "fleet" else targets
        print(census_text(args.census, sources, args.targets or default),
              end="")
        return 0

    stale = refused = 0
    for path in targets:
        pending = []
        for region in audit(path, sources):
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
