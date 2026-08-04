#!/usr/bin/env python3
"""
Read sections out of a checkpoint markdown file by markdown header structure.
Usage: checkpoint.py {next-number|latest|mission|list|session <ID>} [--file PATH]

The parser keys entirely off markdown header level (`## `) plus a prefix token
(SESSION / MISSION); it never regexes arbitrary body content. A "block" runs from
its `## ` header line up to (but not including) the next `## ` line, or EOF.
"""

import argparse
import re
import sys
import os

DEFAULT_FILE = ".claude/tmp/checkpoint.md"

SESSION_RE = re.compile(r"^SESSION\s+S(\d+)\b")


def load_blocks(path):
    """Split the file into `## ` blocks. Each block is a list of raw lines,
    starting with its `## ` header line. Lines before the first `## ` (the H1
    title and any preamble) are ignored."""
    with open(path, "r") as f:
        lines = f.read().splitlines()

    blocks = []
    current = None
    for line in lines:
        if line.startswith("## "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def header_of(block):
    """Header line with the leading `## ` stripped and trailing space trimmed."""
    return block[0][3:].rstrip()


def text_of(block):
    """Full block text, trailing blank lines / whitespace trimmed, internal
    formatting preserved exactly."""
    return "\n".join(block).rstrip()


def session_num(block):
    """Integer session id if this block is a SESSION header, else None."""
    m = SESSION_RE.match(header_of(block))
    return int(m.group(1)) if m else None


def is_mission(block):
    return header_of(block) == "MISSION"


def require_file(path):
    if not os.path.exists(path):
        sys.stderr.write("checkpoint: file not found: %s\n" % path)
        sys.exit(2)


def cmd_next_number(path):
    if not os.path.exists(path):
        print("S001")
        return 0
    nums = [n for n in (session_num(b) for b in load_blocks(path)) if n is not None]
    if not nums:
        print("S001")
        return 0
    print("S%03d" % (max(nums) + 1))
    return 0


def cmd_latest(path):
    require_file(path)
    for block in load_blocks(path):
        if session_num(block) is not None:
            print(text_of(block))
            return 0
    return 2


def cmd_mission(path):
    require_file(path)
    for block in load_blocks(path):
        if is_mission(block):
            print(text_of(block))
            return 0
    return 2


def cmd_list(path):
    require_file(path)
    for block in load_blocks(path):
        if session_num(block) is not None:
            print(header_of(block))
    return 0


def cmd_session(path, raw_id):
    require_file(path)
    token = raw_id.strip()
    if token[:1] in ("S", "s"):
        token = token[1:]
    try:
        wanted = int(token)
    except ValueError:
        sys.stderr.write("checkpoint: session %s not found\n" % raw_id)
        return 2
    for block in load_blocks(path):
        if session_num(block) == wanted:
            print(text_of(block))
            return 0
    sys.stderr.write("checkpoint: session %s not found\n" % raw_id)
    return 2


def build_parser():
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--file", default=DEFAULT_FILE, help="checkpoint markdown file (default: %(default)s)"
    )

    parser = argparse.ArgumentParser(
        description="Read sections out of a checkpoint markdown file by header structure."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next-number", parents=[parent], help="print the next session id (S%%03d)")
    sub.add_parser("latest", parents=[parent], help="print the newest SESSION block")
    sub.add_parser("mission", parents=[parent], help="print the MISSION block")
    sub.add_parser("list", parents=[parent], help="list SESSION headers, newest first")
    sp = sub.add_parser("session", parents=[parent], help="print a specific SESSION block")
    sp.add_argument("id", help="session id, e.g. S042, s042, 42, 042")
    return parser


def main():
    args = build_parser().parse_args()
    path = args.file
    if args.command == "next-number":
        code = cmd_next_number(path)
    elif args.command == "latest":
        code = cmd_latest(path)
    elif args.command == "mission":
        code = cmd_mission(path)
    elif args.command == "list":
        code = cmd_list(path)
    elif args.command == "session":
        code = cmd_session(path, args.id)
    else:  # pragma: no cover - argparse enforces a valid command
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
