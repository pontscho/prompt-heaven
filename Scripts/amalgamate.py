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
                   "_mcp_lsp.py", "_mcp_paging.py")
CANONICAL_SOURCES = {name: SCRIPTS_DIR / name for name in CANONICAL_NAMES}


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
    because the map is also pointed at the SERVERS (the suite's hand-copy
    census does exactly that) and ordinary module-level statements must not
    turn a drift gate into a traceback about an unrelated line. The skip is not
    silent where it matters: a name only enters the system by being written on
    a marker, and `render` refuses an unknown one BY NAME.
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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Inline the shared blocks from the canonical sources "
                    f"({', '.join(CANONICAL_NAMES)}) into the MCP servers."
    )
    ap.add_argument("targets", nargs="*", type=Path,
                    help=f"files to process (default: Scripts/{TARGET_GLOB})")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if any region is stale")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a hand-edited region instead of refusing")
    args = ap.parse_args(argv)

    sources = load_all_blocks()
    targets = args.targets or sorted(SCRIPTS_DIR.glob(TARGET_GLOB))

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
