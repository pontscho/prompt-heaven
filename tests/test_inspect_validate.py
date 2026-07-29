#!/usr/bin/env python3
"""Functional suite for the mcp-inspect VALIDATION family (94 cases, groups A-N).

Spawns ONE long-lived `python3 Scripts/mcp-inspect.py` child, speaks
line-delimited JSON-RPC 2.0 to it, and asserts on `isError` plus substrings of
the returned Markdown.  Every fixture is regenerated from scratch on each run
into a tempfile.mkdtemp() sandbox, so the suite is idempotent and never writes
inside the repo tree.

Coverage by group:
  A  one valid fixture per format          json yaml toml xml ini csv tsv plist python
  B  one invalid fixture per format, and the reported error LINE NUMBER
  C  python depth: compile() catches that ast.parse() misses, SyntaxWarning
  D  read-only contract: no new/touched .pyc, fixtures byte-identical after
  E  xml security: internal entity, external entity (XXE), plain DOCTYPE
  F  parameter error matrix + unreadable / directory / unknown-format paths
  G  every function alias routes to a real handler
  H  max_mb cap semantics (0 = no cap, cap hit, default, negative, non-int)
  I  strict mode turns SKIP into NOT VERIFIED
  J  batch mode via `paths`: mixed verdicts and row count
  K  real repo files validate clean
  L  no regression in the non-validation functions (host stat sha256 pstree ...)
  M  python source encoding: PEP-263 cookie, latin-1 body, UTF-8 BOM, garbage
  N  robustness: hostile input must not kill the server

Usage:
  python3 tests/test_inspect_validate.py
  python3 tests/test_inspect_validate.py --show <case-id-substring> [...]
  python3 tests/test_inspect_validate.py --keep
Exit code 0 iff every case passes.
"""

import ast
import os
import plistlib
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "inspect_validate"
SERVER = H.repo_path("Scripts", "mcp-inspect.py")
SKILL = H.repo_path("ClaudeCode", "skills", "verify", "scripts", "validate.py")


# ---------------------------------------------------------------------------
# fixtures -- every planted error carries its line number in the name/comment
# ---------------------------------------------------------------------------

FIXTURES = {
    # ---- valid ----
    "valid.json": '{\n  "a": 1,\n  "b": [1, 2, 3],\n  "c": {"d": null}\n}\n',
    "valid.yaml": "a: 1\nb:\n  - one\n  - two\nc:\n  d: true\n",
    "valid.toml": 'title = "demo"\n\n[owner]\nname = "x"\nage = 3\n',
    "valid.xml": '<?xml version="1.0" encoding="UTF-8"?>\n<root>\n  <a>1</a>\n  <b attr="z">2</b>\n</root>\n',
    "valid.ini": "[sec]\na = 1\nb = 2\n\n[other]\nc = 3\n",
    "valid.csv": "a,b,c\n1,2,3\n4,5,6\n",
    "valid.tsv": "a\tb\tc\n1\t2\t3\n4\t5\t6\n",
    "valid.plist": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n  <key>k</key>\n  <string>v</string>\n'
        "</dict>\n</plist>\n"
    ),
    "valid.py": "import os\n\n\ndef f(a, b=2):\n    return a + b\n\n\nclass C:\n    pass\n",

    # ---- invalid; planted error line noted per case below ----
    # line 3: bare token `nope` is not a JSON value
    "bad.json": '{\n  "a": 1,\n  "b": nope,\n  "c": 3\n}\n',
    # line 3: a TAB starts the line -> YAML forbids tabs in indentation
    "bad.yaml": "a: 1\nb:\n\tc: 3\n",
    # line 3: `= =` is not a TOML value
    "bad.toml": 'a = 1\nb = "two"\nc = = 3\n',
    # line 4: </c> closes <b>
    "bad.xml": '<?xml version="1.0"?>\n<root>\n  <a>1</a>\n  <b>2</c>\n</root>\n',
    # line 4: duplicate option `a` in [sec]  (configparser strict=True)
    "bad.ini": "[sec]\na = 1\nb = 2\na = 3\n",
    # line 1: no section header at all
    "bad_nosection.ini": "a = 1\nb = 2\n",
    # line 3: 2 fields where the header declared 3
    "bad.csv": "a,b,c\n1,2,3\n4,5\n7,8,9\n",
    "bad.tsv": "a\tb\tc\n1\t2\t3\n4\t5\n7\t8\t9\n",
    # line 5: </dict> closes <string>
    "bad.plist": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<plist version="1.0">\n<dict>\n  <key>k</key>\n  <string>v</dict>\n'
        "</plist>\n"
    ),
    # line 3: `def f(:`
    "bad.py": "x = 1\n\ndef f(:\n    pass\n",

    # ---- python depth ----
    # line 4: `break` outside a loop -> compile() rejects, ast.parse accepts
    "py_break.py": "x = 1\n\nif x:\n    break\n",
    # line 4: `return` outside a function -> same story
    "py_return.py": "x = 1\n\nif x:\n    return x\n",
    # line 3: invalid escape "\d" -> SyntaxWarning, still compiles
    "py_escape.py": "import re\n\nx = \"\\d+\"\n\nprint(x)\n",
    # module-level nonlocal, line 3 -> another compile()-only catch
    "py_nonlocal.py": "x = 1\n\nnonlocal x\n",

    # ---- xml security ----
    "xml_internal_entity.xml": (
        '<?xml version="1.0"?>\n<!DOCTYPE lolz [\n  <!ENTITY a "aaa">\n'
        ']>\n<lolz>&a;</lolz>\n'
    ),
    "xml_external_entity.xml": (
        '<?xml version="1.0"?>\n<!DOCTYPE foo [\n'
        '  <!ENTITY x SYSTEM "file:///etc/passwd">\n]>\n<foo>&x;</foo>\n'
    ),
    "xml_plain_doctype.xml": (
        '<?xml version="1.0"?>\n<!DOCTYPE note>\n<note>\n  <to>a</to>\n</note>\n'
    ),

    # ---- misc ----
    "plain.txt": "just text, no known extension mapping\n",
    "asjson.txt": '{"a": 1}\n',
}

# planted-error line numbers, asserted verbatim below
PLANTED = {
    "bad.json": 3, "bad.yaml": 3, "bad.toml": 3, "bad.xml": 4,
    "bad.ini": 4, "bad_nosection.ini": 1, "bad.csv": 3, "bad.tsv": 3,
    "bad.plist": 5, "bad.py": 3, "py_break.py": 4, "py_return.py": 4,
    "py_escape.py": 3, "py_nonlocal.py": 3,
}

# byte-exact fixtures: encoding cookies / BOM / undecodable garbage
BIN_FIXTURES = {
    # PEP-263 cookie, UTF-8 body
    "py_cookie_utf8.py": "# -*- coding: utf-8 -*-\ns = \"árvíztűrő\"\n".encode("utf-8"),
    # PEP-263 cookie declaring latin-1, body ACTUALLY latin-1 -> not valid UTF-8
    "py_latin1.py": "# -*- coding: latin-1 -*-\ns = \"café\"\n".encode("latin-1"),
    # UTF-8 BOM then plain source
    "py_bom.py": b"\xef\xbb\xbf" + "x = 1\ny = \"ok\"\n".encode("utf-8"),
    # genuinely undecodable, no cookie
    "py_garbage.py": b"x = 1\ns = \"\xff\xfe\x00\x81\xa0\"\n",
}


def build_fixtures(work):
    """Materialise every fixture into the temp workspace; return the big.json path."""
    for name, body in FIXTURES.items():
        work.write_text(name, body)
    for name, blob in BIN_FIXTURES.items():
        work.write_bytes(name, blob)
    with open(work.join("valid_binary.plist"), "wb") as fh:
        plistlib.dump({"k": "v", "n": [1, 2, 3]}, fh, fmt=plistlib.FMT_BINARY)
    work.subdir("adir")
    # >1 MB valid JSON for the max_mb cap test
    big = work.join("big.json")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write('{"pad": "' + ("x" * (1024 * 1024 + 4096)) + '"}')
    return big


# ---------------------------------------------------------------------------
# assertion helpers
# ---------------------------------------------------------------------------

def case(suite, cli, group, cid, fn, params, want_error=False,
         must=(), must_not=()):
    err, text = cli.call_tool(fn, params)
    problems = []
    if err != want_error:
        problems.append("isError=%s expected %s" % (err, want_error))
    for s in must:
        if s not in text:
            problems.append("MISSING %r" % s)
    for s in must_not:
        if s in text:
            problems.append("UNEXPECTED %r" % s)
    suite.record(group, cid, problems, text=text, showable=True)
    return text


def unfenced(text):
    """The reply's OWN markdown lines, fenced payload dropped.

    A `#` inside a fence is somebody else's text -- a config dump, a comment in
    a validated file -- and flagging it would make the envelope gate lie.  What
    the gate is about is markup mcp-inspect itself wrote.
    """
    out, in_fence = [], False
    for ln in text.splitlines():
        if re.match(r"^`{3,}", ln):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(ln)
    return out


def row_for(text, target):
    """Return the table row whose target column mentions `target`."""
    for ln in text.splitlines():
        if target in ln and not ln.startswith("#"):
            return ln
    return ""


def _skill_verdict(path):
    """Cross-check a python fixture against the p:verify skill's validator."""
    if not os.path.exists(SKILL):
        return "(skill validator not present)"
    rc, out, err = H.run_process([sys.executable, SKILL, path],
                                 cwd=H.REPO_ROOT)
    combined = (out or "") + (err or "")
    for tok in ("OK", "FAIL", "LIMITED", "SKIP"):
        for ln in combined.splitlines():
            if ln.strip().startswith(tok):
                return "rc=%d %s" % (rc, ln.strip()[:100])
    return "rc=%d %s" % (rc, " ".join(combined.split())[:100])


def _ast_ok(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        ast.parse(src)
        return "ACCEPTS (ast.parse succeeded)"
    except SyntaxError as exc:
        return "rejects (%s line %s)" % (exc.msg, exc.lineno)


# ---------------------------------------------------------------------------

def run(opts=None):
    """Build fixtures, drive the server, record all 94 cases; return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-inspect validation family", opts=opts,
                    mode="stream", group_width=3, cid_width=28)
    work = H.TempWorkspace("ph-inspect-validate-", keep=opts.keep)

    def f(name):
        return work.join(name)

    try:
        big = build_fixtures(work)
        pyc_before = H.pycache_snapshot()
        dig_before = H.file_digests(work.path)
        cli = H.JsonRpcClient([sys.executable, SERVER], tool="inspect_call",
                              cwd=H.REPO_ROOT, client_name="drv")

        # ================= A: valid fixture per format =================
        for name, fmt in [("valid.json", "json"), ("valid.yaml", "yaml"),
                          ("valid.toml", "toml"), ("valid.xml", "xml"),
                          ("valid.ini", "ini"), ("valid.csv", "csv"),
                          ("valid.tsv", "tsv"), ("valid.plist", "plist"),
                          ("valid.py", "python")]:
            case(suite, cli, "A", "valid-" + fmt, "validate", {"path": f(name)},
                 must=["OK", "**PASSED**", fmt], must_not=["FAIL", "SKIP"])

        # ================= B: invalid fixture per format =================
        for name, fmt in [("bad.json", "json"), ("bad.yaml", "yaml"),
                          ("bad.toml", "toml"), ("bad.xml", "xml"),
                          ("bad.ini", "ini"), ("bad.csv", "csv"),
                          ("bad.tsv", "tsv"), ("bad.plist", "plist"),
                          ("bad.py", "python")]:
            text = case(suite, cli, "B", "bad-" + fmt, "validate",
                        {"path": f(name)}, must=["FAIL", "**FAILED**"])
            # the `at` column must carry the planted line number
            line = row_for(text, name)
            cols = line.split()
            at = cols[2] if len(cols) > 2 else ""
            want = str(PLANTED[name])
            ok = at.split(":")[0] == want
            suite.record("B", "at-" + fmt,
                         [] if ok else ["at=%r expected line %s | row=%r"
                                        % (at, want, line.strip())],
                         text=line)

        case(suite, cli, "B", "bad-ini-nosection", "validate",
             {"path": f("bad_nosection.ini")}, must=["FAIL", "**FAILED**"])

        # ================= C: python depth =================
        case(suite, cli, "C", "py-valid", "python", {"path": f("valid.py")},
             must=["OK", "**PASSED**", "compiled on"])
        case(suite, cli, "C", "py-syntaxerror", "python", {"path": f("bad.py")},
             must=["FAIL", "**FAILED**"])
        case(suite, cli, "C", "py-break-outside-loop", "python",
             {"path": f("py_break.py")}, must=["FAIL", "**FAILED**"])
        suite.note("      ast.parse(py_break.py) -> " + _ast_ok(f("py_break.py")))
        case(suite, cli, "C", "py-return-outside-func", "python",
             {"path": f("py_return.py")}, must=["FAIL", "**FAILED**"])
        suite.note("      ast.parse(py_return.py) -> " + _ast_ok(f("py_return.py")))
        case(suite, cli, "C", "py-nonlocal-module", "python",
             {"path": f("py_nonlocal.py")}, must=["FAIL", "**FAILED**"])
        suite.note("      ast.parse(py_nonlocal.py) -> " + _ast_ok(f("py_nonlocal.py")))
        case(suite, cli, "C", "py-syntaxwarning", "python",
             {"path": f("py_escape.py")},
             must=["OK", "**PASSED**", "SyntaxWarning", "line 3"],
             must_not=["FAIL"])

        # ============ M: python source encoding (bytes vs str) ============
        case(suite, cli, "M", "py-cookie-utf8", "python",
             {"path": f("py_cookie_utf8.py")},
             must=["OK", "**PASSED**"], must_not=["FAIL", "not valid UTF-8"])
        case(suite, cli, "M", "py-latin1-cookie", "python",
             {"path": f("py_latin1.py")},
             must=["OK", "**PASSED**"], must_not=["FAIL", "not valid UTF-8"])
        case(suite, cli, "M", "py-utf8-bom", "python", {"path": f("py_bom.py")},
             must=["OK", "**PASSED**"], must_not=["FAIL"])
        case(suite, cli, "M", "py-garbage", "python", {"path": f("py_garbage.py")},
             must=["FAIL", "**FAILED**"])
        # cross-check against the p:verify skill's standalone validator
        for name in ["py_cookie_utf8.py", "py_latin1.py", "py_bom.py",
                     "py_garbage.py", "py_break.py", "py_return.py",
                     "py_escape.py", "valid.py", "bad.py"]:
            suite.note("      skill-validate %-20s -> %s"
                       % (name, _skill_verdict(f(name))))

        # ================= E: xml security =================
        case(suite, cli, "E", "xml-internal-entity", "xml",
             {"path": f("xml_internal_entity.xml")},
             must=["FAIL", "**FAILED**", "entity"])
        case(suite, cli, "E", "xml-external-entity", "xml",
             {"path": f("xml_external_entity.xml")},
             must=["FAIL", "**FAILED**", "XXE"])
        case(suite, cli, "E", "xml-plain-doctype", "xml",
             {"path": f("xml_plain_doctype.xml")},
             must=["OK", "**PASSED**"], must_not=["FAIL"])

        # ================= F: param error matrix =================
        case(suite, cli, "F", "path+paths", "validate",
             {"path": f("valid.json"), "paths": [f("valid.json")]},
             want_error=True, must=["either"])
        case(suite, cli, "F", "content+path", "validate",
             {"content": "{}", "path": f("valid.json"), "format": "json"},
             want_error=True, must=["either"])
        case(suite, cli, "F", "content-no-format", "validate", {"content": "{}"},
             want_error=True, must=["format"])
        case(suite, cli, "F", "bad-format", "validate",
             {"path": f("valid.json"), "format": "yamlx"},
             want_error=True, must=["unsupported format"])
        case(suite, cli, "F", "empty-paths", "validate", {"paths": []},
             want_error=True, must=["empty list"])
        case(suite, cli, "F", "content-is-number", "validate",
             {"content": 12345, "format": "json"},
             want_error=True, must=["must be a string"])
        case(suite, cli, "F", "no-args", "validate", {}, want_error=True,
             must=["requires"])

        case(suite, cli, "F", "nonexistent", "validate",
             {"path": f("does-not-exist.json")}, must=["FAIL", "cannot read"])
        case(suite, cli, "F", "directory", "validate", {"path": f("adir")},
             must=["SKIP", "is a directory"])
        case(suite, cli, "F", "txt-unknown", "validate", {"path": f("plain.txt")},
             must=["SKIP", "unknown format"])
        case(suite, cli, "F", "txt-forced-json", "validate",
             {"path": f("asjson.txt"), "format": "json"},
             must=["OK", "**PASSED**"], must_not=["SKIP"])

        # ================= G: aliases =================
        for al in ["lint", "check", "verify", "syntax", "parse", "wellformed"]:
            case(suite, cli, "G", "alias-" + al, al,
                 {"content": "{}", "format": "json"},
                 must=["OK"], must_not=["unknown function"])
        for al in ["py", "ast", "py_compile", "pycompile", "python3"]:
            case(suite, cli, "G", "alias-" + al, al, {"content": "x = 1\n"},
                 must=["OK"], must_not=["unknown function"])
        case(suite, cli, "G", "alias-yml", "yml", {"content": "a: 1\n"},
             must=["OK"], must_not=["unknown function"])
        case(suite, cli, "G", "alias-jsonlint", "jsonlint", {"content": "[]"},
             must=["OK"], must_not=["unknown function"])
        case(suite, cli, "G", "alias-xmllint", "xmllint", {"content": "<a/>"},
             must=["OK"], must_not=["unknown function"])
        case(suite, cli, "G", "alias-plutil", "plutil",
             {"content": FIXTURES["valid.plist"]},
             must=["OK"], must_not=["unknown function"])

        # ================= H: max_mb =================
        case(suite, cli, "H", "maxmb-0-means-nocap", "validate",
             {"path": big, "max_mb": 0}, must=["OK", "**PASSED**"],
             must_not=["SKIP", "max_mb"])
        case(suite, cli, "H", "maxmb-1-caps", "validate",
             {"path": big, "max_mb": 1}, must=["SKIP", "max_mb=1"])
        case(suite, cli, "H", "maxmb-default-passes", "validate", {"path": big},
             must=["OK", "**PASSED**"])
        case(suite, cli, "H", "maxmb-negative", "validate",
             {"path": big, "max_mb": -1}, must=["OK"])
        case(suite, cli, "H", "maxmb-not-int", "validate",
             {"path": big, "max_mb": "abc"}, want_error=True, must=["max_mb"])

        # ================= I: strict =================
        case(suite, cli, "I", "strict-off-skip", "validate",
             {"path": f("plain.txt")}, must=["SKIP", "**PASSED**"])
        case(suite, cli, "I", "strict-on-skip", "validate",
             {"path": f("plain.txt"), "strict": True},
             must=["SKIP", "**NOT VERIFIED (strict)**"])
        case(suite, cli, "I", "strict-on-ok", "validate",
             {"path": f("valid.json"), "strict": True}, must=["**PASSED**"])

        # ================= J: batch =================
        t = case(suite, cli, "J", "batch-mixed", "validate",
                 {"paths": [f("valid.json"), f("bad.json"), f("plain.txt")]},
                 must=["OK", "FAIL", "SKIP", "**FAILED**",
                       "1 OK, 1 FAIL, 1 SKIP"])
        nrows = sum(1 for ln in t.splitlines()
                    if ln.startswith(("OK ", "FAIL ", "SKIP ", "LIMITED ")))
        ok = nrows == 3
        suite.record("J", "batch-rowcount",
                     [] if ok else ["got %d rows, expected 3" % nrows], text=t)

        # ================= K: real repo files =================
        for rel in ["ClaudeCode/.claude-plugin/plugin.json", "requirements.yaml",
                    "Scripts/mcp-inspect.py", ".claude/settings.local.json"]:
            p = H.repo_path(*rel.split("/"))
            exists = os.path.exists(p)
            t = case(suite, cli, "K", "repo-" + os.path.basename(rel), "validate",
                     {"path": p}, must=["OK"] if exists else ["cannot read"])
            suite.note("      %s -> %s"
                       % (rel, row_for(t, os.path.basename(rel)).strip()[:120]))

        # ====== N: robustness -- a hostile input must not kill the server ======
        case(suite, cli, "N", "binary-plist", "plist",
             {"path": f("valid_binary.plist")}, must=["OK", "**PASSED**"])
        case(suite, cli, "N", "json-50k-nesting", "json",
             {"content": "[" * 50000 + "]" * 50000},
             must=["FAIL", "RecursionError"])
        case(suite, cli, "N", "py-5k-nesting", "python",
             {"content": "x = " + "(" * 5000 + "1" + ")" * 5000},
             must=["FAIL", "nested parentheses"])
        case(suite, cli, "N", "py-null-byte", "python", {"content": "x = 1\x00\n"},
             must=["FAIL", "null bytes"])
        case(suite, cli, "N", "alive-after-stress", "host", {},
             must=["hostname"])

        # ================= L: no regression =================
        # These assert PAYLOAD markers, not the `## <fn>` titles they used to:
        # a title can be present over an empty body, a body marker cannot, so
        # every one of these is a strictly stronger liveness check than before.
        case(suite, cli, "L", "host", "host", {},
             must=["hostname", "platform"])
        case(suite, cli, "L", "stat", "stat", {"path": SERVER},
             must=["mode", "inode"])
        # `sha256` names the table's FIRST COLUMN -- the very thing that took
        # over from the old title.
        case(suite, cli, "L", "sha256", "sha256", {"path": SERVER},
             must=["sha256", "size", "path"])
        # `|- ` proves the tree actually rendered; `processes` is the count line
        # that says how much of the host it covered.
        case(suite, cli, "L", "pstree", "pstree", {"limit": 20},
             must=["processes", "|- "])
        # `sort cpu` pins the injected-value rule: the caller passed `limit` but
        # not `sort`, so the server owes them the ordering it chose.
        case(suite, cli, "L", "processes", "processes", {"limit": 5},
             must=["sort cpu", "PID", "COMM"])

        # ===== O: envelope discipline -- form, not values, so it cannot age =====
        for cid, fn, params in [
                ("host", "host", {}),
                ("stat", "stat", {"path": SERVER}),
                ("processes", "processes", {"limit": 3}),
                ("disk_usage", "disk_usage",
                 {"path": H.repo_path("tests"), "top": 3}),
                ("sha256", "sha256", {"path": SERVER}),
                ("validate", "validate", {"path": SERVER}),
                ("versions", "versions", {"tools": ["git"]}),
                ("which", "which", {"name": "git"}),
                ("pstree", "pstree", {"limit": 10}),
                ("env", "env", {"key": "HOME"}),
        ]:
            err, t = cli.call_tool(fn, params)
            bad = [ln for ln in unfenced(t) if ln.startswith("#")]
            problems = []
            if err:
                problems.append("isError=True on a call that should succeed")
            if bad:
                problems.append("heading on a successful reply: %r" % bad[:2])
            suite.record("O", "no-heading-" + cid, problems, text=t,
                         showable=True)
        # A gate that cannot fail is not a gate: prove `unfenced` sees the
        # reply's OWN heading and not a `#` line inside someone else's payload.
        seen = [ln for ln in unfenced("## title\n\n```\n# not a heading\n```")
                if ln.startswith("#")]
        suite.record("O", "gate-discriminates",
                     [] if seen == ["## title"] else ["unfenced() saw %r" % seen])

        cli.close()

        # ================= D: read-only contract =================
        pyc_after = H.pycache_snapshot()
        dig_after = H.file_digests(work.path)
        new_pyc = sorted(set(pyc_after) - set(pyc_before))
        touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                         if pyc_after[k] != pyc_before[k])
        changed = sorted(k for k in dig_before
                         if dig_after.get(k) != dig_before[k])
        for cid, bad, label in [("pycache-new", new_pyc, "new .pyc"),
                                ("pycache-touched", touched, "touched .pyc"),
                                ("fixtures-unchanged", changed, "changed fixture")]:
            suite.record("D", cid,
                         [] if not bad else ["%s: %s" % (label, bad)])
        suite.note("      pyc files before=%d after=%d"
                   % (len(pyc_before), len(pyc_after)))
    finally:
        work.cleanup()

    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
