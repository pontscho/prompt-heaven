#!/usr/bin/env python3
"""Convert Trac/MoinMoin wiki syntax to Markdown."""

import os
import re
import sys
from urllib.parse import unquote

link_root = os.environ.get("LINK_ROOT")
trac_root = os.environ.get("TRAC_ROOT")
add_h_ids = os.environ.get("ADD_H_IDS")

_code_blocks = []


def link_wiki2md(t):
    t = unquote(t)
    t = t.replace(" ", "_").replace(":", "_").replace("'", "_")
    if link_root:
        t = re.sub(r"^" + re.escape(link_root), "", t)
    idx = t.rfind("#")
    if idx >= 0:
        path = t[:idx]
        fragment = t[idx + 1:]
    else:
        path = t
        fragment = ""
    t = "/" + path + ".md"
    if fragment:
        t += "#" + fragment
    return t


def mkh(prefix, s):
    s = s.rstrip()
    if add_h_ids == "1":
        hid = " {#" + re.sub(r"[^A-Za-z0-9]", "", s) + "}"
    elif add_h_ids == "2":
        hid = " {#" + re.sub(r"[^A-Za-z0-9_.\-]", "", s) + "}"
    else:
        hid = ""
    return "\n\n" + prefix + s + hid


def _image_repl(m):
    t = m.group(1)
    t = re.sub(r",left$", "", t)
    t = re.sub(r",right$", "", t)
    alt = "Image: '" + t + "'"
    url = "/assets/" + t + "?raw=true"
    return "![{}]({})".format(alt, url)


def _link_repl(m):
    t = m.group(1).strip()
    c = (m.group(2) or "").strip()
    if t.startswith("wiki:"):
        t = t[5:]
        t = link_wiki2md(t)
    elif trac_root and t.startswith(trac_root):
        t = t[len(trac_root):]
        t = link_wiki2md(t)
    elif not t.startswith("http"):
        t = link_wiki2md(t)
    if not c:
        c = t
    return "[{}]({})".format(c, t)


def _protect_code_blocks(text):
    """Phase 1: extract multiline {{{#!lang ... }}} blocks, replace with placeholders."""
    _code_blocks.clear()

    def _repl(m):
        raw_mark = m.group(1)
        if raw_mark:
            mark = raw_mark.strip().lstrip("#!")
        else:
            mark = ""
        content = m.group(2).rstrip("\n")
        idx = len(_code_blocks)
        if mark == "html":
            _code_blocks.append(content)
        elif mark == "comment":
            _code_blocks.append("")
        else:
            _code_blocks.append("```{}\n{}\n```".format(mark, content))
        return "\x00CODEBLOCK{}\x00".format(idx)

    return re.sub(
        r"\{\{\{\s*(?:\n\s*)?(#![a-zA-Z0-9]+)?\n(.*?)\}\}\}",
        _repl,
        text,
        flags=re.DOTALL,
    )


def _restore_code_blocks(text):
    """Phase 4: put code blocks back."""
    def _repl(m):
        idx = int(m.group(1))
        return _code_blocks[idx]
    return re.sub(r"\x00CODEBLOCK(\d+)\x00", _repl, text)


def _process_tables(text):
    """Convert || delimited table groups into Markdown tables."""
    lines = text.split("\n")
    out = []
    table_rows = []

    def flush_table():
        if not table_rows:
            return
        for i, row in enumerate(table_rows):
            inner = row.strip()
            if inner.startswith("||"):
                inner = inner[2:]
            if inner.endswith("||"):
                inner = inner[:-2]
            cells = inner.split("||")
            md_row = "|" + "|".join(cells) + "|"
            out.append(md_row)
            if i == 0:
                sep = "|" + "---|" * len(cells)
                out.append(sep)
        table_rows.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("||") and stripped.endswith("||"):
            table_rows.append(stripped)
        else:
            flush_table()
            out.append(line)
    flush_table()
    return "\n".join(out)


def _process_headings(text):
    """Convert =...= headings to # headings, longest match first."""
    for level, eqs in [(4, "===="), (3, "==="), (2, "=="), (1, "=")]:
        prefix = "#" * level + " "
        pattern = r"\n" + re.escape(eqs) + r"\s*(.*?)\s*" + re.escape(eqs)
        def _repl(m, _prefix=prefix):
            content = m.group(1)
            content = _process_inline(content)
            return mkh(_prefix, content)
        text = re.sub(pattern, _repl, text)
    return text


def _process_def_lists(text):
    """Convert definition lists: '  term::' -> '* **term** '."""
    return re.sub(
        r"\n\s*([A-Za-z/  .(),]+)::",
        lambda m: "\n* **{}** ".format(m.group(1)),
        text,
    )


def _process_inline(text):
    """Phase 3: inline element processing within a single line/segment."""
    # Images: [[Image(file)]] — must precede link processing
    text = re.sub(r"\[\[Image\(([^)]+)\)\]\]", _image_repl, text)

    # [[BR]] / [[br]] — must precede link processing
    text = re.sub(r"\[\[(?:BR|br)\]\]\n?", "  \n", text)

    # Escape prefix: !letter or !{  — before styles so !''' works
    text = re.sub(r"!([A-Za-z{])", r"\1", text)

    # Links: [url caption] — single bracket only, not [[ or ![
    text = re.sub(r"(?<![!\[])\[([^\[\]\s]+)((?:\s[^\]]*)?)\](?!\])", _link_repl, text)

    # Bold '''...'''
    text = re.sub(r"'''(.*?)'''", r"**\1**", text)

    # Italic ''...''
    text = re.sub(r"''(.*?)''", r"*\1*", text)

    # Inline monospace {{{...}}} (single line)
    text = re.sub(r"\{\{\{(.*?)\}\}\}", r"`\1`", text)

    return text


def _process_inline_full(text):
    """Apply inline processing line-by-line, skipping code block placeholders."""
    lines = text.split("\n")
    out = []
    for line in lines:
        if "\x00CODEBLOCK" in line:
            out.append(line)
        else:
            out.append(_process_inline(line))
    return "\n".join(out)


def convert(text):
    text = "\n" + text
    text = _protect_code_blocks(text)
    text = _process_headings(text)
    text = _process_tables(text)
    text = _process_def_lists(text)
    text = _process_inline_full(text)
    text = _restore_code_blocks(text)
    text = text.strip("\n") + "\n"
    return text


def main():
    data = sys.stdin.read()
    sys.stdout.write(convert(data))


if __name__ == "__main__":
    main()
