#!/usr/bin/env python3
"""Functional suite for the mcp-inspect VALIDATION family (groups A-R).

Spawns ONE long-lived `python3 Scripts/mcp-inspect.py` child, speaks
line-delimited JSON-RPC 2.0 to it, and asserts on `isError` plus substrings of
the returned Markdown.  Every fixture is regenerated from scratch on each run
into a tempfile.mkdtemp() sandbox, so the suite is idempotent and never writes
inside the repo tree.

Coverage by group:
  A  one valid fixture per format     json yaml toml xml ini csv tsv plist python bash
  B  one invalid fixture per format, and the reported error LINE NUMBER
  C  python depth: compile() catches that ast.parse() misses, SyntaxWarning
  D  read-only contract: no new/touched .pyc, fixtures byte-identical after,
     and nothing the `bash -n` validator was pointed at ever ran
  E  xml security: internal entity, external entity (XXE), plain DOCTYPE
  F  parameter error matrix + unreadable / directory / unknown-format paths,
     plus the .sh/.bash extension pair and the sh/shell format spellings
  G  every function alias routes to a real handler
  H  max_mb cap semantics (0 = no cap, cap hit, default, negative, non-int)
  I  strict mode turns SKIP into NOT VERIFIED
  J  batch mode via `paths`: mixed verdicts and row count
  K  real repo files validate clean
  L  no regression in the non-validation functions (host stat sha256 pstree ...)
  M  python source encoding: PEP-263 cookie, latin-1 body, UTF-8 BOM, garbage,
     then note rows cross-checking the p:verify skill's bundled validator --
     the MCP-free path -- on the python AND shell fixtures
  N  robustness: hostile input must not kill the server, and a script handed to
     `bash -n` is parsed and not executed
  O  envelope discipline, and every canonical handler either gated or skipped
     with a written reason
  P  hash: `algorithm` is an alias of `algo` (a conflict is refused, the algo
     that ran is named), and `recursive`/`max_files` directory expansion --
     sorted, symlinks skipped, subdirs descended, truncation never silent
  Q  the param gate, live: an unknown key is refused on EVERY function (isError,
     function and key named); every key a function reads -- or-chain aliases,
     `timeout`, `max_answer_chars` -- passes the gate; a pinned format
     validator refuses `format`/`fmt` instead of overriding it
  R  the accepted table cannot drift: every registry function has a row and no
     row names a missing one, and each row EQUALS the keys the handler reads,
     extracted from the server source by ast (helpers that receive `p`
     followed), with the pinned validators' format/fmt the one exclusion

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
import shutil
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
    "valid.sh": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "\n"
        "greet() {\n"
        '  local who="${1:-world}"\n'
        '  echo "hello, ${who}"\n'
        "}\n"
        "\n"
        "for n in 1 2 3; do\n"
        '  greet "$n"\n'
        "done\n"
    ),
    # the .bash extension, which must infer the same format as .sh
    "valid.bash": (
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in\n'
        "  a) echo A ;;\n"
        "  *) echo other ;;\n"
        "esac\n"
    ),

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
    # line 4: `for` on line 3 never says `do`, so bash faults on the token that
    # follows it.  Chosen over an unterminated quote or a missing `fi` because
    # both bashes on the supported platforms agree on line 4 here, while the
    # end-of-file family is reported at the opening construct by bash 5 and
    # ALSO at the last line by bash 3.2 -- a planted line that would move.
    "bad.sh": "#!/usr/bin/env bash\necho one\nfor x in 1 2 3\necho $x\ndone\n",

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
    "py_escape.py": 3, "py_nonlocal.py": 3, "bad.sh": 4,
}

# The bash validator shells out, so the binary is a hard dependency of the
# `bash` rows below -- unlike `node`, which group O skips for exactly that
# reason.  It is NOT gated here: bash exists on both platforms this server
# supports, eight of this repo's own post-edit hooks ARE bash scripts, and a
# host without it could not run the product these suites test.  Recorded as a
# note per run so a red row on such a host reads as the missing binary rather
# than as a broken validator.
BASH_BIN = shutil.which("bash")

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


MARKER = "bash-must-not-run.marker"


def side_effect_script(work):
    """A VALID bash script that leaves a file behind -- if anything runs it.

    `bash -n` is supposed to parse and stop, so the marker must not appear.
    The redirect target is absolute and inside the sandbox on purpose: the
    server child runs with cwd=REPO_ROOT, so a relative one would aim this at
    the repo itself.
    """
    return ('#!/usr/bin/env bash\nprintf \'ran\\n\' > "%s"\n'
            % work.join(MARKER))


def build_fixtures(work):
    """Materialise every fixture into the temp workspace; return the big.json path."""
    for name, body in FIXTURES.items():
        work.write_text(name, body)
    for name, blob in BIN_FIXTURES.items():
        work.write_bytes(name, blob)
    work.write_text("side_effect.sh", side_effect_script(work))
    with open(work.join("valid_binary.plist"), "wb") as fh:
        plistlib.dump({"k": "v", "n": [1, 2, 3]}, fh, fmt=plistlib.FMT_BINARY)
    work.subdir("adir")
    # group P's tree, in a SUBDIR on purpose: group D digests only the files
    # directly inside the workspace, and a symlink there would be read through.
    # Written b-before-a so a walk that trusts the filesystem order is caught.
    work.write_text(os.path.join("hashtree", "b.txt"), "bravo\n")
    work.write_text(os.path.join("hashtree", "a.txt"), "alpha\n")
    work.write_text(os.path.join("hashtree", "sub", "c.txt"), "charlie\n")
    os.symlink("a.txt", work.join("hashtree", "link.txt"))
    os.symlink("sub", work.join("hashtree", "linkdir"))
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
    """`want_error=None` asserts nothing about the flag -- for a call whose only
    claim is about the text (group Q's accepts-*, where a handler refusing a
    value is past the gate and so beside the point)."""
    err, text = cli.call_tool(fn, params)
    problems = []
    if want_error is not None and err != want_error:
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


def hex_tokens(text, width):
    """How many whitespace-separated tokens are exactly `width` lowercase hex."""
    return sum(1 for ln in text.splitlines() for w in ln.split()
               if len(w) == width and all(c in "0123456789abcdef" for c in w))


def _skill_verdict(path):
    """Cross-check a fixture against the p:verify skill's bundled validator.

    No `--format` is passed on purpose: the skill script has to infer the format
    from the extension exactly as the server does, so a `.sh`/`.bash` that came
    back SKIP here would be an EXT_MAP that drifted from `_VALIDATE_EXT`.
    """
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
# group R: the static half of the param gate -- what each handler READS, taken
# from the server's own source by ast, never typed here
# ---------------------------------------------------------------------------

# The formats a pinned wrapper fixes.  These are the one place the accepted set
# is deliberately SMALLER than what the handler reads: `h_json` calls
# `h_validate(p, "json")`, which does read `format`/`fmt` -- and then lets the
# pinned value override them in silence.  Refusing them is the whole point.
PINNED_FORMATS = ("json", "python", "yaml", "toml", "xml", "ini", "csv", "tsv",
                  "plist", "javascript", "bash")
PINNED_EXCLUDED = {"format", "fmt"}


def _server_tree():
    with open(SERVER, encoding="utf-8") as fh:
        return ast.parse(fh.read(), SERVER)


def _module_functions(tree):
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _registry_handlers(tree):
    """HANDLERS as written in the source: canonical name -> handler NAME."""
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "HANDLERS"
                and isinstance(node.value, ast.Dict)):
            continue
        out = {}
        for k, v in zip(node.value.keys, node.value.values):
            if (isinstance(k, ast.Constant) and isinstance(v, ast.Tuple)
                    and v.elts and isinstance(v.elts[0], ast.Name)):
                out[k.value] = v.elts[0].id
        return out
    return {}


def _str_consts(node):
    if (isinstance(node, (ast.Tuple, ast.List, ast.Set)) and node.elts
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in node.elts)):
        return {e.value for e in node.elts}
    return None


def param_reads(funcs, fname, pname, seen=None):
    """Every key function `fname` reads off its parameter `pname`.

    Returns (keys, unresolved).  Recognised reads: `p.get("k")`, `p["k"]`,
    `"k" in p` / `not in p`, and a loop variable standing in for "k" when it
    iterates a literal tuple of strings (`_hash_algo`'s `for k in ("algo",
    "algorithm")`).  `p` handed POSITIONALLY or by keyword to another
    module-level function is followed into that function under its own
    parameter name -- which is how `_timeout_param`, `_hash_algo` and the
    pinned wrappers' `h_validate` are reached without being listed.

    Anything else that touches `p` -- `p.items()`, `dict(p)`, a key computed at
    run time -- is UNRESOLVED and reported, never skipped: a read this
    extractor cannot see is exactly the read that would let the table drift.
    """
    seen = set() if seen is None else seen
    if (fname, pname) in seen or fname not in funcs:
        return set(), []
    seen.add((fname, pname))
    fn = funcs[fname]

    loop_vars = {}
    for node in ast.walk(fn):
        if isinstance(node, (ast.comprehension, ast.For)):
            vals = _str_consts(node.iter)
            if isinstance(node.target, ast.Name) and vals:
                loop_vars.setdefault(node.target.id, set()).update(vals)

    keys, unresolved, understood = set(), [], set()

    def is_p(n):
        return isinstance(n, ast.Name) and n.id == pname

    def take(expr, where):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            keys.add(expr.value)
        elif isinstance(expr, ast.Name) and expr.id in loop_vars:
            keys.update(loop_vars[expr.id])
        else:
            unresolved.append("%s:%s %s" % (fname, getattr(expr, "lineno", "?"),
                                            where))

    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and is_p(f.value) and f.attr == "get" \
                    and node.args:
                understood.add(id(f.value))
                take(node.args[0], "%s.get(<computed>)" % pname)
            elif isinstance(f, ast.Name) and f.id in funcs:
                spec = funcs[f.id].args
                names = [a.arg for a in spec.posonlyargs + spec.args]
                for i, a in enumerate(node.args):
                    if is_p(a) and i < len(names):
                        understood.add(id(a))
                        k2, u2 = param_reads(funcs, f.id, names[i], seen)
                        keys |= k2
                        unresolved += u2
                for kw in node.keywords:
                    if is_p(kw.value) and kw.arg:
                        understood.add(id(kw.value))
                        k2, u2 = param_reads(funcs, f.id, kw.arg, seen)
                        keys |= k2
                        unresolved += u2
        elif isinstance(node, ast.Subscript) and is_p(node.value):
            understood.add(id(node.value))
            take(node.slice, "%s[<computed>]" % pname)
        elif (isinstance(node, ast.Compare) and len(node.ops) == 1
              and isinstance(node.ops[0], (ast.In, ast.NotIn))
              and is_p(node.comparators[0])):
            understood.add(id(node.comparators[0]))
            take(node.left, "<computed> in %s" % pname)

    for node in ast.walk(fn):
        if is_p(node) and id(node) not in understood:
            unresolved.append("%s:%d %s escapes (not a keyed read)"
                              % (fname, node.lineno, pname))
    return keys, unresolved


# ---------------------------------------------------------------------------

def run(opts=None):
    """Build fixtures, drive the server, record every case; return the Suite.

    The case COUNT is declared once, in tests/run.py's SUITES table, and the
    runner asserts it against the run -- it is deliberately not repeated here.
    """
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
                          ("valid.py", "python"), ("valid.sh", "bash")]:
            case(suite, cli, "A", "valid-" + fmt, "validate", {"path": f(name)},
                 must=["OK", "**PASSED**", fmt], must_not=["FAIL", "SKIP"])

        # ================= B: invalid fixture per format =================
        for name, fmt in [("bad.json", "json"), ("bad.yaml", "yaml"),
                          ("bad.toml", "toml"), ("bad.xml", "xml"),
                          ("bad.ini", "ini"), ("bad.csv", "csv"),
                          ("bad.tsv", "tsv"), ("bad.plist", "plist"),
                          ("bad.py", "python"), ("bad.sh", "bash")]:
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
        # Cross-check against the p:verify skill's standalone validator -- the
        # OTHER path to the same verdicts, which ships MCP-free for hooks and CI
        # and so cannot be exercised through `cli`.  The shell fixtures are here
        # rather than in F because this is the only place the two paths are put
        # side by side, and `bash` is the format they most recently disagreed
        # about: the server grew `bash -n` first and the bundled script followed.
        # `valid.bash` earns its row by extension alone -- it is the one fixture
        # whose verdict depends on BOTH ext maps carrying the second spelling.
        for name in ["py_cookie_utf8.py", "py_latin1.py", "py_bom.py",
                     "py_garbage.py", "py_break.py", "py_return.py",
                     "py_escape.py", "valid.py", "bad.py",
                     "valid.sh", "valid.bash", "bad.sh"]:
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

        # -- shell scripts: the extension PAIR, and the three spellings of the
        # one parser.  Group A already covers `.sh` inference (it passes no
        # format either); `.bash` has to land on the same validator or the
        # second extension is decoration.  A caller who says `sh` or `shell`
        # must reach that same validator too -- proven by the verdict naming
        # `bash -n` while the format column echoes the word they actually used.
        case(suite, cli, "F", "dotbash-inferred", "validate",
             {"path": f("valid.bash")}, must=["OK", "bash -n"],
             must_not=["SKIP", "unknown format"])
        case(suite, cli, "F", "format-sh-spelling", "validate",
             {"path": f("valid.sh"), "format": "sh"},
             must=["OK", "sh", "bash -n"], must_not=["unsupported"])
        case(suite, cli, "F", "format-shell-spelling", "validate",
             {"path": f("valid.sh"), "format": "shell"},
             must=["OK", "shell", "bash -n"], must_not=["unsupported"])
        # `zsh` is NOT one of them -- bash would misreport zsh's own dialect --
        # and the refusal has to advertise the spellings that DO work, or it
        # sends the caller away from a validator that would have answered.
        case(suite, cli, "F", "format-zsh-refused", "validate",
             {"path": f("valid.sh"), "format": "zsh"}, want_error=True,
             must=["unsupported format", "bash", "shell"])

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
        # the FUNCTION half of the same two spellings group F checks as a
        # FORMAT: `function="sh"` and `params.format="sh"` may not disagree
        # about which parser they name, so both are asserted on `bash -n`
        for al in ["sh", "shell"]:
            case(suite, cli, "G", "alias-" + al, al, {"content": "echo hi\n"},
                 must=["OK", "bash -n"], must_not=["unknown function"])

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
        # `bash -n` must PARSE and stop.  This fixture is VALID bash whose only
        # statement would create a file, so a validator that executed anything
        # leaves evidence; both entry points are driven, because the path form
        # hands bash a filename and the content form hands it stdin.  The
        # marker's absence is asserted in group D, after the child is gone, so
        # a late write cannot slip past the check.
        case(suite, cli, "N", "bash-side-effect-path", "bash",
             {"path": f("side_effect.sh")}, must=["OK", "**PASSED**"])
        case(suite, cli, "N", "bash-side-effect-content", "bash",
             {"content": side_effect_script(work)}, must=["OK", "**PASSED**"])
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
        # The header ROW is gone too, so no column name is assertable any more.
        # What identifies a digest is its SHAPE -- exactly 64 hex characters --
        # and that is a stronger claim than any label would have been.
        t = case(suite, cli, "L", "sha256", "sha256", {"path": SERVER},
                 must=["mcp-inspect.py"])
        hexes = [w for ln in t.splitlines() for w in ln.split()
                 if len(w) == 64 and all(c in "0123456789abcdef" for c in w)]
        suite.record("L", "sha256-digest-shape",
                     [] if len(hexes) == 1 else
                     ["expected exactly one 64-hex token, found %d" % len(hexes)],
                     text=t)
        # `|- ` proves the tree actually rendered; `processes` is the count line
        # that says how much of the host it covered.
        case(suite, cli, "L", "pstree", "pstree", {"limit": 20},
             must=["processes", "|- "])
        # `sort cpu` pins the injected-value rule: the caller passed `limit` but
        # not `sort`, so the server owes them the ordering it chose.
        case(suite, cli, "L", "processes", "processes", {"limit": 5},
             must=["sort cpu", "PID", "COMM"])

        # ============ P: hash -- algorithm alias, recursive expansion ============
        # `algorithm` used to be dropped without a word and the reply carried no
        # header, so an md5 request came back as an unlabelled sha256.  The
        # digest's SHAPE (32 vs 64 hex) is what proves which algorithm ran; the
        # `algo:` line is what tells a caller who cannot count hex digits.
        tree = f("hashtree")
        t = case(suite, cli, "P", "algorithm-alias-md5", "hash",
                 {"path": f(os.path.join("hashtree", "a.txt")),
                  "algorithm": "md5"}, must=["algo: md5"])
        n32, n64 = hex_tokens(t, 32), hex_tokens(t, 64)
        suite.record("P", "algorithm-alias-digest-shape",
                     [] if (n32, n64) == (1, 0) else
                     ["expected one 32-hex token and no 64-hex, got %d/%d"
                      % (n32, n64)], text=t)
        case(suite, cli, "P", "algo-algorithm-conflict", "hash",
             {"path": f(os.path.join("hashtree", "a.txt")),
              "algo": "sha256", "algorithm": "md5"},
             want_error=True, must=["algo", "algorithm", "sha256", "md5"])
        case(suite, cli, "P", "algo-algorithm-agree", "hash",
             {"path": f(os.path.join("hashtree", "a.txt")),
              "algo": "md5", "algorithm": "MD5"}, must=["algo: md5"])
        # the fixed-algorithm wrappers have the same silent-override shape
        case(suite, cli, "P", "md5-wrapper-conflict", "md5",
             {"path": f(os.path.join("hashtree", "a.txt")),
              "algorithm": "sha256"},
             want_error=True, must=["md5", "sha256"])
        case(suite, cli, "P", "dir-without-recursive-hint", "hash",
             {"path": tree}, must=["is a directory", "recursive:true"])
        t = case(suite, cli, "P", "recursive-rows", "hash",
                 {"path": tree, "recursive": True, "algo": "md5"},
                 must=[os.path.join(tree, "a.txt"), os.path.join(tree, "b.txt"),
                       os.path.join(tree, "sub", "c.txt"), "algo: md5"],
                 must_not=["link.txt", "linkdir", "truncated"])
        order = [t.find(os.path.join(tree, n))
                 for n in ("a.txt", "b.txt", os.path.join("sub", "c.txt"))]
        nrows = hex_tokens(t, 32)
        suite.record("P", "recursive-sorted-3-rows",
                     [] if (-1 not in order and order == sorted(order)
                            and nrows == 3) else
                     ["positions %r, %d digest rows (want ascending, 3)"
                      % (order, nrows)], text=t)
        t = case(suite, cli, "P", "max-files-truncation", "hash",
                 {"path": tree, "recursive": True, "max_files": 2},
                 must=["truncated at 2 files", "max_files"],
                 must_not=[os.path.join("sub", "c.txt")])
        # the cap is shared across `paths`: 3 files from the tree fill it, so
        # the second element's one file is what gets truncated
        t = case(suite, cli, "P", "max-files-shared-across-paths", "hash",
                 {"paths": [tree, f(os.path.join("hashtree", "sub"))],
                  "recursive": True, "max_files": 3},
                 must=["truncated at 3 files"])
        suite.record("P", "max-files-shared-row-count",
                     [] if hex_tokens(t, 64) == 3 else
                     ["expected 3 digest rows, got %d" % hex_tokens(t, 64)],
                     text=t)
        case(suite, cli, "P", "expect-with-dir-refused", "hash",
             {"path": tree, "recursive": True, "expect": "0" * 64},
             want_error=True, must=["expect", "directory"])

        # ===== O: envelope discipline -- form, not values, so it cannot age =====
        # Two invariants over a successful reply: mcp-inspect writes no Markdown
        # heading of its own, and no line ends in whitespace.  Neither is a
        # value, so neither goes stale the way a character count would.
        gate = [
            ("processes", {"limit": 3}),
            ("process", {"pid": 1}),
            ("ports", {"proto": "udp"}),
            ("open_files", {"pid": 1, "limit": 3}),
            ("host", {}),
            ("memory", {}),
            ("disk", {"path": H.REPO_ROOT}),
            ("disk_usage", {"path": H.repo_path("tests"), "top": 3}),
            ("mounts", {}),
            ("which", {"name": "git"}),
            ("env", {"key": "HOME"}),
            ("stat", {"path": SERVER}),
            ("interfaces", {"filter": "lo0"}),
            ("route", {}),
            ("pstree", {"limit": 10}),
            ("limits", {}),
            ("services", {"filter": "ssh", "limit": 3}),
            ("versions", {"tools": ["git"]}),
            ("hash", {"path": SERVER}),
            ("sha256", {"path": SERVER}),
            ("md5", {"path": SERVER}),
            ("validate", {"path": SERVER}),
        ]
        # Named WITH the reason rather than quietly absent: an uncovered handler
        # is exactly how `### system (launchctl limit)` survived this group's
        # first version.
        skipped = {
            "connections": "same renderer as `ports`, and an unfiltered lsof "
                           "sweep costs seconds of suite time",
            "json": "thin wrapper over `validate`", "python": "ditto",
            "yaml": "ditto", "toml": "ditto", "xml": "ditto", "ini": "ditto",
            "csv": "ditto", "tsv": "ditto", "plist": "ditto",
            "javascript": "thin wrapper over `validate`, and one of the two "
                          "that need an external binary: without `node` it "
                          "FAILs by design, and this suite cannot assume node "
                          "is installed",
            "bash": "thin wrapper over `validate`; it also shells out, but "
                    "unlike node its binary is assumed present (see BASH_BIN), "
                    "so groups A/B/F/N drive this validator for real",
        }
        for fn, params in gate:
            err, t = cli.call_tool(fn, params)
            problems = []
            if err:
                problems.append("isError=True on a call that should succeed")
            heads = [ln for ln in unfenced(t) if ln.startswith("#")]
            if heads:
                problems.append("heading of its own: %r" % heads[:2])
            ragged = [ln for ln in t.splitlines() if ln != ln.rstrip()]
            if ragged:
                problems.append("%d line(s) end in whitespace, first %r"
                                % (len(ragged), ragged[0][-40:]))
            suite.record("O", "envelope-" + fn, problems, text=t,
                         showable=True)
        # Coverage, cross-checked against the server's OWN function list: every
        # canonical name is either exercised above or named in `skipped`.  A new
        # handler fails HERE instead of slipping in ungated.
        _, avail = cli.call_tool("__ph_gate_probe__", {})
        names = set()
        if "Available: " in avail:
            names = {n.strip() for n in
                     avail.split("Available: ", 1)[1].split(". Call with no")[0]
                     .split(",") if n.strip()}
        ungated = sorted(names - {f for f, _ in gate} - set(skipped))
        suite.record("O", "gate-covers-every-handler",
                     [] if (names and not ungated) else
                     ["server returned no function list"] if not names else
                     ["ungated: %s" % ", ".join(ungated)], text=avail)
        suite.note("      gated %d handler(s), %d skipped with a reason"
                   % (len(gate), len(skipped)))
        # A gate that cannot fail is not a gate: prove `unfenced` sees the
        # reply's OWN heading and not a `#` line inside someone else's payload.
        seen = [ln for ln in unfenced("## title\n\n```\n# not a heading\n```")
                if ln.startswith("#")]
        suite.record("O", "gate-discriminates",
                     [] if seen == ["## title"] else ["unfenced() saw %r" % seen])

        # ============ Q: the param gate, over live JSON-RPC ============
        # A misspelled key used to be dropped without a word, and every one of
        # these handlers has a no-filter default that is a plausible answer:
        # `processes {pattern}` came back as the top 30 of the whole host.  The
        # function list is the server's own registry, so a new handler is in
        # this loop the moment it exists.
        src_tree = _server_tree()
        registry = _registry_handlers(src_tree)
        for fn in sorted(registry):
            case(suite, cli, "Q", "bogus-" + fn, fn,
                 {"bogus_param": 1},
                 want_error=True,
                 must=["Unknown params for '%s'" % fn, "bogus_param",
                       "Accepted:", "max_answer_chars"])

        # Every key each function accepts, in ONE call per function, plus
        # `max_answer_chars` everywhere: the gate names every unknown key it
        # sees, so one call proves the whole set.  Values are chosen to fail
        # fast where they fail at all -- a handler refusing `path` together
        # with `paths` is past the gate, which is all this group asserts.
        small = f(os.path.join("hashtree", "a.txt"))
        hash_all = {"path": small, "file": small, "paths": [small],
                    "expect": "0" * 64, "max_mb": 1, "recursive": False,
                    "max_files": 5}
        validate_all = {"path": f("valid.json"), "file": f("valid.json"),
                        "paths": [f("valid.json")], "content": "{}",
                        "text": "{}", "max_mb": 1, "strict": False}
        accepts = {
            "processes": {"filter": "python", "name": "python", "user": "root",
                          "sort": "mem", "limit": 3, "timeout": 5},
            "process": {"pid": 1},
            "ports": {"proto": "udp", "timeout": 5},
            "connections": {"state": "established", "timeout": 3},
            "open_files": {"pid": 1, "port": 1, "user": "root",
                           "path": SERVER, "limit": 3, "timeout": 3},
            "host": {}, "memory": {}, "mounts": {}, "route": {},
            "disk": {"path": H.REPO_ROOT},
            "disk_usage": {"path": f("hashtree"), "depth": 1, "top": 3,
                           "timeout": 10},
            "which": {"name": "git", "cmd": "git", "command": "git"},
            "env": {"key": "HOME", "filter": "HOME", "show_secrets": False},
            "stat": {"path": SERVER, "file": SERVER},
            "interfaces": {"filter": "lo", "name": "lo"},
            "pstree": {"pid": 1, "depth": 1, "limit": 5},
            "limits": {"pid": 1},
            "services": {"filter": "ssh", "name": "ssh", "user": False,
                         "limit": 3},
            "versions": {"tools": ["git"], "tool": "git", "name": "git"},
            "hash": dict(hash_all, algo="md5", algorithm="md5"),
            "sha256": dict(hash_all, algo="sha256", algorithm="sha256"),
            "md5": dict(hash_all, algo="md5", algorithm="md5"),
            "validate": dict(validate_all, format="json", fmt="json"),
        }
        for fmt in PINNED_FORMATS:
            accepts[fmt] = dict(validate_all)
        for fn in sorted(registry):
            params = dict(accepts.get(fn, {"__no_accepts_row__": 1}),
                      max_answer_chars=50000)
            case(suite, cli, "Q", "accepts-" + fn, fn, params,
                 want_error=None, must_not=["Unknown params"])

        # A pinned format validator must REFUSE a conflicting format rather
        # than override it: `json {format: yaml}` used to parse as JSON.
        case(suite, cli, "Q", "json-format-yaml-refused", "json",
             {"path": f("valid.yaml"), "format": "yaml"}, want_error=True,
             must=["Unknown params for 'json'", "format"])
        case(suite, cli, "Q", "json-fmt-refused", "json",
             {"path": f("valid.yaml"), "fmt": "yaml"}, want_error=True,
             must=["Unknown params for 'json'", "fmt"])
        case(suite, cli, "Q", "validate-format-yaml-ok", "validate",
             {"path": f("valid.yaml"), "format": "yaml"},
             must=["OK", "**PASSED**", "yaml"], must_not=["Unknown params"])
        # the two worked examples of the plausible wrong answer
        case(suite, cli, "Q", "processes-pattern-refused", "processes",
             {"pattern": "node"}, want_error=True,
             must=["Unknown params for 'processes'", "pattern", "filter"])
        case(suite, cli, "Q", "limits-process-refused", "limits",
             {"process": 123}, want_error=True,
             must=["Unknown params for 'limits'", "process", "pid"])
        # the gate runs AFTER function-alias resolution, so the refusal names
        # the canonical function, whose accepted list is the one that applies
        case(suite, cli, "Q", "alias-names-canonical", "ps",
             {"pattern": "node"}, want_error=True,
             must=["Unknown params for 'processes'"])

        cli.close()

        # ============ R: the accepted table cannot drift from the code ============
        # In-process, AST plus one import of the server module (bytecode-free).
        # The live group Q proves the gate REFUSES; this one proves the table it
        # refuses against is the set of keys the handlers actually read.
        mod = H.load_module_from_path("mcp_inspect_params_r", SERVER)
        table = getattr(mod, "HANDLER_ACCEPTED_PARAMS", None)
        common = getattr(mod, "_COMMON_PARAMS", None)
        funcs = _module_functions(src_tree)
        suite.record("R", "table-exists",
                     [] if isinstance(table, dict) and isinstance(common, (set, frozenset))
                     else ["HANDLER_ACCEPTED_PARAMS=%s _COMMON_PARAMS=%s"
                           % (type(table).__name__, type(common).__name__)])
        table = table if isinstance(table, dict) else {}
        common = set(common) if isinstance(common, (set, frozenset)) else set()
        missing = sorted(set(registry) - set(table))
        orphan = sorted(set(table) - set(registry))
        suite.record("R", "every-function-has-a-row",
                     [] if registry and not missing else
                     ["no row for: %s" % ", ".join(missing or ["<empty registry>"])])
        suite.record("R", "no-row-for-a-missing-fn",
                     [] if not orphan else ["row names no function: %s"
                                            % ", ".join(orphan)])
        lacking = sorted(fn for fn, acc in table.items() if not common <= set(acc))
        suite.record("R", "common-in-every-row",
                     [] if common and not lacking else
                     ["_COMMON_PARAMS empty"] if not common else
                     ["rows without the common set: %s" % ", ".join(lacking)])
        # the common set is exactly what the DISPATCHER reads off params
        disp, _ = param_reads(funcs, "handle_inspect_call", "params")
        suite.record("R", "common-is-dispatcher-reads",
                     [] if common and disp == common else
                     ["dispatcher reads %s, _COMMON_PARAMS is %s"
                      % (sorted(disp), sorted(common))])
        leaked = sorted(fmt for fmt in PINNED_FORMATS
                        if PINNED_EXCLUDED & set(table.get(fmt, ())))
        suite.record("R", "pinned-refuse-format",
                     [] if table and not leaked else
                     ["no table"] if not table else
                     ["pinned validators accepting format/fmt: %s"
                      % ", ".join(leaked)])
        # The extractor has to be able to FAIL: prove it follows p into a helper
        # (`_timeout_param`), through a wrapper into its target (`h_md5` ->
        # `h_hash` -> `_hash_algo`), and resolves a loop over a literal tuple.
        k_proc, _ = param_reads(funcs, "h_processes", "p")
        k_md5, _ = param_reads(funcs, "h_md5", "p")
        k_json, _ = param_reads(funcs, "h_json", "p")
        ctl = []
        if "timeout" not in k_proc:
            ctl.append("did not follow h_processes -> _timeout_param")
        if not {"algo", "algorithm", "paths"} <= k_md5:
            ctl.append("did not follow h_md5 -> h_hash -> _hash_algo: %s"
                       % sorted(k_md5))
        if not {"format", "fmt", "content"} <= k_json:
            ctl.append("did not follow h_json -> h_validate: %s" % sorted(k_json))
        suite.record("R", "extractor-follows-helpers", ctl)
        # Per function, EXACT equality both ways: a key read but not accepted
        # would be refused on a call the handler honours; a key accepted but
        # never read is the silent drop this gate exists to end, moved one layer
        # up.  The pinned validators' one sanctioned difference is format/fmt.
        for fn in sorted(registry):
            reads, unresolved = param_reads(funcs, registry[fn], "p")
            if fn in PINNED_FORMATS:
                reads = reads - PINNED_EXCLUDED
            acc = set(table.get(fn, ())) - common
            problems = ["unresolved read: %s" % u for u in unresolved]
            if fn not in table:
                problems.append("no row")
            else:
                if reads - acc:
                    problems.append("read but not accepted: %s"
                                    % ", ".join(sorted(reads - acc)))
                if acc - reads:
                    problems.append("accepted but never read: %s"
                                    % ", ".join(sorted(acc - reads)))
            suite.record("R", "drift-" + fn, problems,
                         text="reads=%s" % sorted(reads))

        # ================= D: read-only contract =================
        pyc_after = H.pycache_snapshot()
        dig_after = H.file_digests(work.path)
        new_pyc = sorted(set(pyc_after) - set(pyc_before))
        touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                         if pyc_after[k] != pyc_before[k])
        changed = sorted(k for k in dig_before
                         if dig_after.get(k) != dig_before[k])
        # A NEW file is invisible to the digest diff above -- that compares the
        # keys it already knew -- so the one thing `bash -n` could have created
        # is named and checked by hand.
        ran = [f(MARKER)] if os.path.exists(f(MARKER)) else []
        for cid, bad, label in [("pycache-new", new_pyc, "new .pyc"),
                                ("pycache-touched", touched, "touched .pyc"),
                                ("fixtures-unchanged", changed, "changed fixture"),
                                ("bash-n-executed-nothing", ran,
                                 "the validated script RAN and left")]:
            suite.record("D", cid,
                         [] if not bad else ["%s: %s" % (label, bad)])
        suite.note("      pyc files before=%d after=%d"
                   % (len(pyc_before), len(pyc_after)))
        suite.note("      bash binary: %s"
                   % (BASH_BIN or "MISSING -- the `bash` rows above are red "
                                  "for that reason, not for a validator bug"))
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
