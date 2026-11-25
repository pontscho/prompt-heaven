#!/usr/bin/env python3
"""Claude Code status line: host, cwd basename, branch, context tokens, PR link.

Reads the session JSON on stdin, prints one line. Python 3.9, stdlib only.
The branch comes from .git/HEAD (no subprocess: the script is re-run on every
update and a running one is cancelled by the next). Every failure degrades to
a shorter line; the script never prints a traceback and always exits 0.
"""
import getpass
import json
import os
import re
import socket
import sys

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean(text):
    return _CTRL.sub("", str(text))


def _git_head(start):
    """Return the branch name, a short sha when detached, or '' outside git."""
    d = os.path.abspath(start)
    while True:
        dot = os.path.join(d, ".git")
        if os.path.isdir(dot):
            gitdir = dot
            break
        if os.path.isfile(dot):
            with open(dot, "r", encoding="utf-8", errors="replace") as f:
                line = f.readline().strip()
            if not line.startswith("gitdir:"):
                return ""
            gitdir = os.path.join(d, line[7:].strip())
            break
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent
    with open(os.path.join(gitdir, "HEAD"), "r", encoding="utf-8", errors="replace") as f:
        head = f.readline().strip()
    if head.startswith("ref:"):
        ref = head[4:].strip()
        prefix = "refs/heads/"
        return ref[len(prefix):] if ref.startswith(prefix) else ref
    return head[:7]


def _ctx_limit():
    """The CONTEXT GUARD limit: the same default and env as Scripts/context-guard.sh."""
    try:
        v = int(os.environ.get("CLAUDE_CTX_LIMIT") or 250000)
    except ValueError:
        v = 250000
    return v if v > 0 else 250000


def _tokens(n):
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    if n >= 1000:
        return "%dk" % int(n / 1000.0 + 0.5)
    return str(n)


def _link(url, text):
    if not re.match(r"https?://", url):
        return text
    return "\033]8;;%s\033\\%s\033]8;;\033\\" % (url, text)


def build(data, color=True):
    def c(code, text):
        return "\033[%sm%s\033[0m" % (code, text) if color else text

    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    host = socket.gethostname().split(".")[0]
    ws = data.get("workspace") or {}
    cwd = ws.get("current_dir") or data.get("cwd") or ""
    base = os.path.basename(str(cwd).rstrip("/")) or str(cwd)

    # user@host green, ':' white, path light blue
    ident = "%s@%s" % (user, host) if user else host
    first = c("32", _clean(ident)) if ident else ""
    if base:
        first += c("97", ":") + c("94", _clean(base))
    parts = [first] if first else []

    # (branch, tokens), white
    inner = []
    if cwd:
        try:
            branch = _git_head(cwd)
        except Exception:
            branch = ""
        if branch:
            inner.append(_clean(branch))
    used = (data.get("context_window") or {}).get("total_input_tokens")
    if isinstance(used, (int, float)) and not isinstance(used, bool):
        text = _tokens(int(used))
        limit = _ctx_limit()
        if limit:
            text += "/%d%%" % int(used * 100.0 / limit + 0.5)
        inner.append(text)
    if inner:
        parts.append(c("37", "(" + ", ".join(inner) + ")"))

    pr = data.get("pr") or {}
    number = pr.get("number")
    if isinstance(number, int) and not isinstance(number, bool):
        label = c("37", ("MR !%d" if pr.get("kind") == "mr" else "PR #%d") % number)
        url = _clean(pr.get("url") or "")
        parts.append(_link(url, label) if url else label)

    return " ".join(parts)


def main():
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    color = "NO_COLOR" not in os.environ
    try:
        line = build(data, color)
    except Exception:
        line = ""
    sys.stdout.write(line + "\n")


if __name__ == "__main__":
    main()
