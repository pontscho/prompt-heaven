#!/usr/bin/env python3
"""reindex.py -- regenerate docs/INDEX.md and audit the wiki: the SERVER's reindex.

A thin wrapper, the way `measure_cli.py` is one (roadmap R-0033). It loads the
committed `Scripts/mcp-wiki.py` off disk and calls the same
`handle_wiki_call("reindex")` the MCP process answers `wiki_call reindex` with:
the server walks the wiki root, writes INDEX.md (unless `--check`), and returns
the audit -- orphans, duplicate slugs, malformed frontmatter -- which is printed
verbatim. Before R-0033 this script carried a hand-mirrored copy of the collect,
render and report steps; now they exist once, in the server.

Only INDEX.md is ever written, and only by the server. With --check, nothing is
written. The report's header names the INDEX.md the server wrote by its
absolute path (the pre-R-0033 CLI printed the `--root` spelling).

THE EXIT CODE
-------------
Non-zero iff the server's answer carries a duplicate-slug or a malformed block.
That is read by the server's exported `REINDEX_BLOCKING_PREFIXES` -- the
prefixes it renders those two block heads with -- never by matching the prose
around them. Orphans are reported and never fail.

FINDING THE SERVER
------------------
Exactly `measure_cli.py`'s order, through its own resolver and loader (imported,
not copied): `--server`, then `$MCP_WIKI_SERVER`, then `Scripts/` and
`scripts/` under the project root -- the git work tree enclosing `--root` --
then the same two under each ancestor of this script's real path. There is no
fallback: without the server file on disk this script cannot run. A copy-deployed
plugin with no repo beside it needs `--server` or `$MCP_WIKI_SERVER`.

Usage:
    python3 scripts/reindex.py --root docs            # regenerate INDEX.md + audit
    python3 scripts/reindex.py --root docs --check    # audit only, write nothing
    (either form also takes --server PATH)

Exit codes:
    0  audited (and, without --check, written); no duplicate slug, nothing malformed
    1  the server's audit names a duplicate slug or malformed frontmatter
    2  the server was never asked or could not answer: no server module found,
       the root is missing, or the server returned a tool error
"""
import os
import sys

# Before the sibling import: loading the server off disk must not litter the
# tree it is indexing with bytecode.
sys.dont_write_bytecode = True

import argparse  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_cli as m  # noqa: E402

# The server module main() loaded; `render_index` resolves one itself when it
# is called without main() having run.
_SERVER = None


def _loaded_server():
	global _SERVER
	if _SERVER is None:
		path, _label = m.resolve_server(None, m.default_project_root())
		_SERVER = m.load_server(path)
	return _SERVER


def render_index(entries):
	# Kept by NAME only: docs/adr/0022 -- a frozen, append-only page -- anchors
	# `reindex.py:render_index` in its frontmatter `sources:`, and that anchor
	# must keep resolving. The renderer itself lives once, in the server.
	return _loaded_server().render_index(entries)


def main(argv=None):
	global _SERVER
	parser = argparse.ArgumentParser(
		prog="reindex.py",
		description=("Regenerate INDEX.md and audit the wiki through the "
			"committed mcp-wiki server's `reindex`."),
		epilog=("exit codes: 0 clean | 1 duplicate slug or malformed frontmatter "
			"| 2 the server was not found, the root is missing, or the server "
			"returned a tool error. Orphans never fail."))
	parser.add_argument("--root", default="docs",
		help="wiki root directory, relative to the working directory (default: docs)")
	parser.add_argument("--check", action="store_true",
		help="audit only; do not write INDEX.md")
	parser.add_argument("--server", metavar="PATH",
		help="path to mcp-wiki.py (default: discovered, as measure_cli.py does)")
	args = parser.parse_args(argv)

	try:
		module, target = m.open_wiki(args.root, args.server)
		missing = [name for name in ("render_index", "REINDEX_BLOCKING_PREFIXES")
			if not hasattr(module, name)]
		if missing:
			raise m.Refusal("%s exports no %s; this wrapper needs the server "
				"that has them" % (module.__file__, ", ".join(missing)))
		_SERVER = module
		text, ok = m.call(module, target, "reindex", check=args.check or None)
	except (m.Refusal, OSError) as exc:
		sys.stderr.write("%s: %s\n" % (parser.prog, exc))
		return 2
	if not ok:
		sys.stderr.write("%s: %s\n" % (parser.prog, text))
		return 2
	sys.stdout.write(text.rstrip("\n") + "\n")
	blocking = tuple(module.REINDEX_BLOCKING_PREFIXES)
	return 1 if any(line.startswith(blocking) for line in text.splitlines()) else 0


if __name__ == "__main__":
	sys.exit(main())
