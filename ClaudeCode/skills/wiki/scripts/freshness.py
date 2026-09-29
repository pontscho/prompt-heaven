#!/usr/bin/env python3
"""freshness.py -- the wiki's freshness gate from a shell: the SERVER's verdict.

A thin wrapper, the way `measure_cli.py` is one (roadmap R-0033). It loads the
committed `Scripts/mcp-wiki.py` off disk and calls the same
`handle_wiki_call("freshness")` the MCP process answers `wiki_call freshness`
with, prints that answer verbatim, and sets its exit code from it. It carries no
classifier, no renderer and no status table of its own: before R-0033 it did,
hand-mirrored from the server, and the copy gated only on `orphaned-source` --
a declared subset of the server's verdict, which is how five gating pages sat
behind a CLI exit of 0 (R-0036). The rule is now docs/adr/0019's addendum: a
clean exit here IS a clean server verdict.

THE EXIT CODE IS THE SERVER'S `gating:` NUMBER -- and why that is not parsing
------------------------------------------------------------------------------
`measure_cli.py` refuses to put a verdict in its exit code (see its header),
because deriving one would mean parsing the server's Markdown or restating its
gating-state table. This script makes the opposite call, for this one function,
on purpose:

  * the exit code is this CLI's whole reason to exist -- it is the human/CI gate
    that runs where no MCP session does; a freshness CLI whose exit code says
    nothing is a report printer, and `wiki_call freshness` already prints it;
  * the count is read off the ONE line the server renders with its exported
    `GATING_LINE_PREFIX` constant, as the integer that follows it. That is the
    same constant the server formats the line with, not a match on prose; no
    status is re-filtered and no classification is repeated here.

`measure_cli.py`'s own rule stands for `measure` and `verify`.

Nothing is executed on the way: `freshness` passes `rendered=None` to the
server's verifier, so measured regions are classified only by the states that
need no command, and the answer says so. No execution grant is built here.

FINDING THE SERVER
------------------
Exactly `measure_cli.py`'s order, through its own resolver and loader (imported,
not copied): `--server`, then `$MCP_WIKI_SERVER`, then `Scripts/` and
`scripts/` under the project root -- the git work tree enclosing `--root` --
then the same two under each ancestor of this script's real path. There is no
fallback: without the server file on disk this script cannot run. A copy-deployed
plugin with no repo beside it needs `--server` or `$MCP_WIKI_SERVER`.

Usage:
    python3 scripts/freshness.py [--root docs] [--head HEAD] [--quiet]
                                 [--server PATH]

`--root` is relative to the working directory (default: docs). `--quiet` prints
nothing on stdout and leaves the verdict to the exit code; a failure still goes
to stderr.

Exit codes:
    0  the server's `gating:` line counts 0
    1  the server's `gating:` line counts one or more
    2  the server was never asked or could not answer: no server module found,
       the root is missing, the server returned a tool error (an unresolvable
       `--head` is one), or its answer carried no `gating:` line
"""
import os
import sys

# Before the sibling import: loading the server off disk must not litter the
# tree it is reporting on with bytecode.
sys.dont_write_bytecode = True

import argparse  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_cli as m  # noqa: E402


def server_gating(module, text):
	"""The integer after the server's exported GATING_LINE_PREFIX, or None."""
	prefix = module.GATING_LINE_PREFIX
	for line in text.splitlines():
		if line.startswith(prefix):
			count = line[len(prefix):].split(" ", 1)[0]
			return int(count) if count.isdigit() else None
	return None


def main(argv=None):
	parser = argparse.ArgumentParser(
		prog="freshness.py",
		description=("The wiki freshness gate: prints the committed mcp-wiki "
			"server's `freshness` answer and exits with its verdict."),
		epilog=("exit codes: 0 server gating 0 | 1 server gating > 0 | 2 the "
			"server was not found, the root is missing, or the server returned "
			"a tool error. Git lag is advisory and never sets the exit code."))
	parser.add_argument("--root", default="docs",
		help="wiki root directory, relative to the working directory (default: docs)")
	parser.add_argument("--head", default="HEAD",
		help="ref representing current state (default: HEAD)")
	parser.add_argument("--quiet", action="store_true",
		help="print nothing on stdout; rely on the exit code only")
	parser.add_argument("--server", metavar="PATH",
		help="path to mcp-wiki.py (default: discovered, as measure_cli.py does)")
	args = parser.parse_args(argv)

	try:
		module, target = m.open_wiki(args.root, args.server)
		if not hasattr(module, "GATING_LINE_PREFIX"):
			raise m.Refusal("%s exports no GATING_LINE_PREFIX; this gate reads "
				"the verdict by that constant and by nothing else" % module.__file__)
		text, ok = m.call(module, target, "freshness", head=args.head)
	except (m.Refusal, OSError) as exc:
		sys.stderr.write("%s: %s\n" % (parser.prog, exc))
		return 2
	if not ok:
		sys.stderr.write("%s: %s\n" % (parser.prog, text))
		return 2
	count = server_gating(module, text)
	if count is None:
		sys.stderr.write("%s: the server's answer carries no `%s<N>` line; no "
			"verdict to report\n" % (parser.prog, module.GATING_LINE_PREFIX))
		return 2
	if not args.quiet:
		sys.stdout.write(text.rstrip("\n") + "\n")
	return 1 if count else 0


if __name__ == "__main__":
	sys.exit(main())
