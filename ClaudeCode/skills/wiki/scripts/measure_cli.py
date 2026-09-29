#!/usr/bin/env python3
"""measure_cli.py -- the measured-region BOOTSTRAP: `measure` / `verify` from a shell.

WHY THIS FILE EXISTS, and it is not "a CLI is handy"
----------------------------------------------------
A repo's FIRST measured-region migration predates its own tooling. The mechanism
ships inside `Scripts/mcp-wiki.py`, and the `mcp-wiki` process the session is
talking to was started before that code existed: it has no `measure`, no
`verify`, and a running MCP server is not something a migration can restart from
inside the conversation that needs it. So the one operation that converts a
page's hand-typed numbers into measured regions cannot be performed with the
tool it is converting the page TO. Every repo adopting this mechanism hits that
bootstrap exactly once, and it hits it at the worst possible moment -- before
there is a single working region to reason from.

This script is that bootstrap. It loads the COMMITTED server module off disk and
calls the same `handle_wiki_call` entry point the MCP process would, with the
same arguments. Nothing is restarted, nothing is patched, and no answer comes
from a process whose vintage the caller cannot see.

IT IS A BOOTSTRAP, NOT A SECOND IMPLEMENTATION
----------------------------------------------
`freshness.py` and `reindex.py`, in this same directory, are hand-mirrored
copies of server logic, and `docs/components/wiki-engine.md` records the price
in as many words: a change to either copy must still be mirrored in the other by
hand. This file is deliberately NOT a third instance of that bargain. It carries
no marker grammar, no digest rule, no fence rule, no state table and no
rendering step. Every one of those is CALLED on the module it loads:

    handle_wiki_call      `measure` and `verify`, verbatim, params and all
    measure_scan          which pages carry regions, and which are malformed
    measured_regions      the marker grammar and its three refusals
    _fenced_line_indices  the inertness oracle (rule 2)
    measured_digest       the recorded hash
    measured_state        ok / stale / hand-edited / not-rendered
    _page_text            the page reader the writer's line indices agree with

The last two names on that list are private by spelling, and reaching for them
is still the right call: `_fenced_line_indices` IS the scanner's own fence
oracle, and re-deriving the CommonMark subset here to avoid an underscore is
precisely the fourth copy this file exists not to be. If a change to this script
ever starts with "reimplement", it is the wrong change.

THE TRUST BOUNDARY IS NOT WIDENED HERE
--------------------------------------
Rendering a measured region runs a command a repo file named, and the server
gates that behind an `_ExecutionGrant` passed explicitly from the two handlers
allowed to build one. This script NEVER constructs one: every execution it can
cause goes through `handle_wiki_call("measure")` or `handle_wiki_call("verify",
measure=True)`, so the boundary holds for the bootstrap exactly as it holds for
the server. That is also why `body` prints what the PAGE carries rather than
re-rendering: showing you the command's current output would mean forging the
grant.

The child processes are therefore all the server's, and it already spawns them
with `stdin=subprocess.DEVNULL`. This script starts none of its own.

WHAT IT WRITES
--------------
`measure --write` writes pages, under the wiki root it was given and nowhere
else -- the server's `measure_apply` is the only writer reachable from here.
`verify`, `regions`, `body` and `server` write nothing at all; `reindex`, the
server's other writing function, is deliberately not exposed. Bytecode is
disabled before the module is loaded, so importing the server leaves no
`__pycache__` beside it (this repo's suites assert a bytecode-free tree).

FINDING THE SERVER
------------------
The plugin travels and the server may sit anywhere, so the path is discovered
rather than assumed, and the discovery is overridable with `--server`. The order
is printed by the `server` subcommand and named in full by the failure. It is:
`--server`, then `$MCP_WIKI_SERVER`, then `Scripts/` and `scripts/` under the
project root, then the same two under each ancestor of this script's own real
path (the deployed skill is a symlink into a repo, so the realpath is what
leads back to the tree the server lives in). Nothing is guessed beyond that list
and there is no bundled copy to fall back on.

EXIT CODES -- and what is deliberately NOT one
----------------------------------------------
    0  the call produced a report, or the diagnostic printed
    1  the server answered with a tool error (bad params, unreadable registry,
       a `--name` no region uses)
    2  this script never got as far as the server (no module found, no wiki
       root, an unreadable page, a usage error)

A corpus that is NOT clean -- a stale region, a hand edit, a broken anchor -- is
reported in the text and is NOT an exit code. Deriving a verdict here would mean
either parsing the server's rendered Markdown or restating its gating-state
table, and both are the second implementation this file refuses to be. Read the
report; the server already says which regions gate and why.

Usage:
    python3 -B measure_cli.py verify  [--measure] [--path-prefix P]
    python3 -B measure_cli.py measure [--write] [--force] [--name N]
                                      [--path-prefix P]
    python3 -B measure_cli.py regions <docs-relative page>
    python3 -B measure_cli.py body    <docs-relative page> <region name>
    python3 -B measure_cli.py server

Common to every subcommand: --server, --project-root, --wiki-root, --strict,
--max-chars. `--help` on the script or on any subcommand prints the full list.
"""

import os
import sys

# Before ANY import that could touch the loaded server module: this script's own
# job includes not littering the tree it is measuring.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import importlib.util  # noqa: E402

# A corpus-wide `verify` on a real wiki runs past the server's 100k default and
# comes back truncated. That ceiling is sized for a model's context window; a
# terminal does not have one, and a bootstrap that silently drops the tail of the
# first migration's evidence is worse than a slow one.
DEFAULT_MAX_CHARS = 200000

DEFAULT_WIKI_ROOT = "docs"
SERVER_BASENAME = "mcp-wiki.py"
SERVER_ENV_VAR = "MCP_WIKI_SERVER"
SERVER_DIRS = ("Scripts", "scripts")

# How far up from this script's own real path to look for a server. Deployed,
# this file sits at <plugin>/skills/wiki/scripts/, so a repo that vendors the
# plugin the way this one does puts the server four levels up; the two spare
# levels cover a plugin nested one or two directories deeper than that. The walk
# stops at the filesystem root either way, and every level it tried is named in
# the failure -- a bound that is printed is a bound nobody has to guess at.
SERVER_ANCESTOR_LEVELS = 6


class Refusal(Exception):
	"""Something this script could not do before the server was ever asked."""


# ---------------------------------------------------------------------------
# Locating things
# ---------------------------------------------------------------------------

def _candidates(explicit, project_root):
	"""The ordered (label, path-or-None, why-absent) rows the search walks.

	Built whole even when the first entry hits, because the FAILURE has to be
	able to name every place it looked -- an error that says only "not found"
	sends the reader to re-derive this list by hand.
	"""
	found = []
	found.append(("--server", explicit, "not given"))
	found.append(("$" + SERVER_ENV_VAR,
	              os.environ.get(SERVER_ENV_VAR) or None, "not set"))
	for name in SERVER_DIRS:
		found.append(("<project-root>/%s" % name,
		              os.path.join(project_root, name, SERVER_BASENAME), ""))
	here = os.path.dirname(os.path.realpath(__file__))
	for level in range(SERVER_ANCESTOR_LEVELS):
		here = os.path.dirname(here)
		if here in ("", os.sep):
			break
		for name in SERVER_DIRS:
			found.append(("<script>/%s%s" % ("../" * (level + 1), name),
			              os.path.join(here, name, SERVER_BASENAME), ""))
	return found


def resolve_server(explicit, project_root):
	"""Return (path, label) of the first server module that exists, or refuse.

	An EXPLICIT `--server` that is not there refuses by name instead of falling
	through to the search. Silently discovering a DIFFERENT module than the one
	the caller named is the failure mode this whole bootstrap exists to remove --
	the migration's premise is that the code answering and the code on disk may
	not be the same thing, and an override that quietly picks its own answer
	reintroduces exactly that doubt.
	"""
	if explicit and not os.path.isfile(explicit):
		raise Refusal(
			"--server names no file: %s\nNot falling back to the search: an "
			"override that quietly resolves elsewhere is how you end up "
			"measuring a server you did not choose." % explicit)
	rows = _candidates(explicit, project_root)
	for label, path, _absent in rows:
		if path and os.path.isfile(path):
			return os.path.abspath(path), label
	width = max(len(label) for label, _p, _a in rows)
	lines = ["cannot find the mcp-wiki server module (%s)." % SERVER_BASENAME,
	         "Looked, in this order:"]
	for label, path, absent in rows:
		lines.append("  %-*s  %s" % (width, label, path or absent))
	lines.append("")
	lines.append("Pass --server /path/to/%s (or set $%s)."
	             % (SERVER_BASENAME, SERVER_ENV_VAR))
	lines.append("There is no fallback: this script carries no copy of the "
	             "server's logic, on purpose -- see the header.")
	raise Refusal("\n".join(lines))


def load_server(path):
	"""Import the server module off disk, bytecode-free."""
	spec = importlib.util.spec_from_file_location("mcp_wiki_bootstrap", path)
	if spec is None or spec.loader is None:
		raise Refusal("not importable as a Python module: %s" % path)
	module = importlib.util.module_from_spec(spec)
	try:
		spec.loader.exec_module(module)
	except Exception as exc:                      # noqa: BLE001 -- reported, not raised
		raise Refusal("%s failed to import: %s: %s"
		              % (path, type(exc).__name__, exc))
	missing = [n for n in ("handle_wiki_call", "measure_scan", "measured_regions",
	                       "measured_digest", "measured_state", "_page_text",
	                       "_fenced_line_indices") if not hasattr(module, n)]
	if missing:
		raise Refusal(
			"%s predates the measured-region mechanism: it has no %s. This "
			"script bootstraps a server that HAS the code and is merely not "
			"running it; it cannot add the code to one that lacks it."
			% (path, ", ".join(missing)))
	return module


def default_project_root():
	"""The enclosing git work tree, or the working directory if there is none."""
	here = os.path.abspath(os.getcwd())
	while True:
		if os.path.exists(os.path.join(here, ".git")):
			return here
		parent = os.path.dirname(here)
		if parent == here:
			return os.path.abspath(os.getcwd())
		here = parent


def wiki_abs(args):
	"""The absolute wiki root, refusing before the server has to."""
	path = os.path.join(args.project_root, args.wiki_root)
	if not os.path.isdir(path):
		raise Refusal("wiki root not found: %s (project root: %s)"
		              % (path, args.project_root))
	return path


# ---------------------------------------------------------------------------
# Calling the server
# ---------------------------------------------------------------------------

def call(module, args, function, **params):
	"""One `handle_wiki_call`, exactly as the MCP process makes it.

	Returns (text, ok). A tool error is text too -- it is the server's sentence
	about what the caller asked for, and swallowing it to raise a shorter one
	here would lose the part that says what to do instead.
	"""
	params.setdefault("max_answer_chars", args.max_chars)
	params = {k: v for k, v in params.items() if v is not None}
	result = module.handle_wiki_call({"function": function, "params": params},
	                                 args.project_root, args.wiki_root,
	                                 args.strict)
	if result.get("error"):
		return "error: %s" % result["error"], False
	return result.get("__raw_text__") or "", True


def page_regions(module, abs_root, relpath):
	"""(text, regions) for one page, refusing with the pages that DO carry some."""
	full = os.path.join(abs_root, relpath)
	if not os.path.isfile(full):
		rows, _malformed = module.measure_scan(abs_root)
		carriers = sorted({row["path"] for row in rows})
		raise Refusal(
			"no such page under the wiki root: %s\nPaths are docs-relative, the "
			"spelling every report prints. Pages carrying a measured region: %s"
			% (full, ", ".join(carriers) or "none"))
	text = module._page_text(abs_root, relpath)
	return text, module.measured_regions(relpath, text)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_verify(module, args):
	text, ok = call(module, args, "verify", measure=args.measure or None,
	                path_prefix=args.path_prefix)
	sys.stdout.write(text.rstrip("\n") + "\n")
	return 0 if ok else 1


def cmd_measure(module, args):
	text, ok = call(module, args, "measure", write=args.write or None,
	                force=args.force or None, name=args.name or None,
	                path_prefix=args.path_prefix)
	sys.stdout.write(text.rstrip("\n") + "\n")
	return 0 if ok else 1


def cmd_regions(module, args):
	"""List the regions a page carries -- and every marker-shaped line it has.

	The second half is the half that is not decoration. A clean `measure` proves
	nothing about a marker the page SHOWS: one inside a fenced block is inert by
	rule 2, so the scanner never reports it and a reader cannot tell the two
	apart by eye. A page documenting this mechanism has both kinds, and getting
	that backwards is how a live region ends up inside an example.
	"""
	abs_root = wiki_abs(args)
	text, regions = page_regions(module, abs_root, args.page)
	print("%s -- %d measured region(s) the scanner accepts"
	      % (args.page, len(regions)))
	if regions:
		print()
		print("  %-26s %-7s %-7s %-8s %-14s %-14s %s"
		      % ("name", "begin", "end", "body", "recorded", "computed",
		         "state (unrendered)"))
		for region in regions:
			computed = module.measured_digest(region["body"])
			print("  %-26s L%-6d L%-6d %-8d %-14s %-14s %s"
			      % (region["name"], region["begin"] + 1, region["end"] + 1,
			         len(region["body"].encode("utf-8")),
			         region["recorded"] or "(empty)", computed,
			         module.measured_state(region, None)))
	fenced = module._fenced_line_indices(text.splitlines(keepends=True))
	marker_lines = [(i + 1, line) for i, line in enumerate(text.splitlines())
	                if "MEASURED" in line]
	live = [n for n, _ in marker_lines if (n - 1) not in fenced]
	print()
	print("  every line mentioning MEASURED (%d: %d live, %d inert inside a "
	      "fence):" % (len(marker_lines), len(live),
	                   len(marker_lines) - len(live)))
	for lineno, line in marker_lines:
		print("    L%-6d %-16s %s"
		      % (lineno, "inert (fenced)" if (lineno - 1) in fenced else "LIVE",
		         line))
	return 0


def cmd_body(module, args):
	"""Print one region's body byte-for-byte, with nothing around it."""
	abs_root = wiki_abs(args)
	_text, regions = page_regions(module, abs_root, args.page)
	for region in regions:
		if region["name"] == args.name:
			sys.stdout.write(region["body"])
			return 0
	raise Refusal("no region named %r in %s; it carries %s"
	              % (args.name, args.page,
	                 ", ".join(r["name"] for r in regions) or "none"))


def cmd_server(module, args):
	"""Report the resolution, so the discovery order is inspectable, not folklore."""
	print("server   : %s" % args._server_path)
	print("found via: %s" % args._server_label)
	print("project  : %s" % args.project_root)
	print("wiki     : %s/ (%s)" % (args.wiki_root, wiki_abs(args)))
	print("functions: %s" % ", ".join(sorted(module.HANDLERS)))
	return 0


COMMANDS = {"verify": cmd_verify, "measure": cmd_measure,
            "regions": cmd_regions, "body": cmd_body, "server": cmd_server}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common(parser, suppress):
	"""The flags every subcommand takes, accepted on BOTH sides of it.

	The subcommand copies default to SUPPRESS rather than to None: argparse
	copies a subparser's defaults over the values the top-level parser already
	set, so a real default here would silently discard `measure_cli.py --server X
	verify` -- which is the spelling a reader of the usage line writes first.
	"""
	kw = {"default": argparse.SUPPRESS} if suppress else {}
	parser.add_argument("--server", metavar="PATH", help=(
		"path to mcp-wiki.py (default: discovered -- see the `server` "
		"subcommand for the search order)"), **kw)
	parser.add_argument("--project-root", metavar="PATH", help=(
		"repo root (default: the enclosing git work tree, else cwd)"), **kw)
	parser.add_argument("--wiki-root", metavar="DIR", help=(
		"wiki root, relative to the project root (default: %s)"
		% DEFAULT_WIKI_ROOT), **kw)
	parser.add_argument("--strict", action="store_true", help=(
		"reject a root that resolves outside the project root"), **kw)
	parser.add_argument("--max-chars", type=int, metavar="N", help=(
		"answer ceiling (default: %d, above the server's own, because a "
		"terminal has no context window to protect)" % DEFAULT_MAX_CHARS), **kw)


def build_parser():
	parser = argparse.ArgumentParser(
		prog="measure_cli.py",
		formatter_class=argparse.RawDescriptionHelpFormatter,
		description=(
			"Bootstrap CLI for the wiki's measured regions: drives the "
			"COMMITTED mcp-wiki.py off disk, because a repo's first migration "
			"predates the running server that would otherwise do it."),
		epilog=(
			"It is a bootstrap, not a second implementation: every rendering, "
			"digest, marker and state rule is called on the loaded server "
			"module, never restated here.\n\n"
			"exit codes: 0 report produced | 1 server returned a tool error | "
			"2 never reached the server.\nA corpus that is not clean is in the "
			"REPORT, never in the exit code."))
	_add_common(parser, suppress=False)
	parser.set_defaults(project_root=None, wiki_root=DEFAULT_WIKI_ROOT,
	                    server=None, strict=False, max_chars=DEFAULT_MAX_CHARS)
	subs = parser.add_subparsers(dest="command", metavar="<command>")

	p = subs.add_parser("verify", help=(
		"resolve every anchor in the corpus and classify every measured region"))
	p.add_argument("--measure", "--render", action="store_true", dest="measure",
	               help=("also RE-RENDER every region, which runs the commands "
	                     "measurements.json names; without it nothing is "
	                     "executed and the answer says what it did not check"))
	p.add_argument("--path-prefix", metavar="P",
	               help="limit to one docs scope (a directory or a page path)")
	_add_common(p, suppress=True)

	p = subs.add_parser("measure", help=(
		"re-render the measured regions; check mode unless --write"))
	p.add_argument("--write", action="store_true",
	               help="apply the rendered bodies to the pages (writes)")
	p.add_argument("--force", action="store_true",
	               help="overwrite a hand-edited region, which is otherwise refused")
	p.add_argument("--name", metavar="N", help="one measurement by registry name")
	p.add_argument("--path-prefix", metavar="P",
	               help="limit to one docs scope (a directory or a page path)")
	_add_common(p, suppress=True)

	p = subs.add_parser("regions", help=(
		"list one page's regions AND every marker line with its fenced flag"))
	p.add_argument("page", help="docs-relative page path, e.g. components/x.md")
	_add_common(p, suppress=True)

	p = subs.add_parser("body", help="print one region's body, byte-for-byte")
	p.add_argument("page", help="docs-relative page path")
	p.add_argument("name", help="the region name in its BEGIN marker")
	_add_common(p, suppress=True)

	p = subs.add_parser("server", help=(
		"print which server module was found, and where the wiki is"))
	_add_common(p, suppress=True)

	return parser


def main(argv=None):
	parser = build_parser()
	args = parser.parse_args(argv)
	if not args.command:
		parser.print_help()
		return 2
	if args.project_root is None:
		args.project_root = default_project_root()
	args.project_root = os.path.abspath(args.project_root)
	try:
		path, label = resolve_server(args.server, args.project_root)
		args._server_path, args._server_label = path, label
		module = load_server(path)
		return COMMANDS[args.command](module, args)
	except Refusal as exc:
		sys.stderr.write("%s: %s\n" % (parser.prog, exc))
		return 2
	except OSError as exc:
		sys.stderr.write("%s: %s\n" % (parser.prog, exc))
		return 2
	except ValueError as exc:
		# The server's own refusals -- a malformed marker reaches here as a
		# MeasuredRegionError, which is a ValueError naming the page and line.
		sys.stderr.write("%s: %s\n" % (parser.prog, exc))
		return 1


if __name__ == "__main__":
	sys.exit(main())
