#!/usr/bin/env python3
"""The MCP protocol version is read from a class constant, never re-inlined (A-E).

THE DEFECT THIS GATE EXISTS FOR
-------------------------------
`Scripts/MCP_SKELETON.md` sections 3 and 4 show the handshake version as a
class constant on `McpServer`, read at the `initialize` reply as
`self.PROTOCOL_VERSION`.  Seven of the fifteen servers had inlined the literal
into the reply dict instead, and the convergence commit that fixed them found
the drift by hand.

Nothing failed while that drift existed, and that is the point.  The VALUE is
gated live -- `Scripts/_mcp_smoke_test.py` handshakes every server and asserts
the string it gets back -- so a server that inlines the RIGHT literal is
indistinguishable, over the wire, from one that reads the constant.

What the shape buys is NOT fewer edits today, and saying so would be the kind
of overstatement this repo keeps deleting.  A server with no constant and one
inlined literal has exactly ONE place to change, which is precisely what the
seven drifted servers looked like -- the convergence commit ADDED the constant
to them, it did not collapse several copies into one.  What the shape buys is
the OPTION: a named class member can be carried by a generated region and a
literal buried in a dict cannot, so the shape is what decides whether this
value can ever be written once instead of fifteen times.  That commit argued it
in those words, then named the hole it was leaving -- the value had a live gate
and the shape had only the template, "which is the exact arrangement that let
seven servers drift away from it in the first place."  This suite is the
missing half.

THE INVARIANT, AS ONE SENTENCE
------------------------------
Every server declares the protocol version exactly once, as the first
non-docstring member of `class McpServer`, and every `initialize` reply reads
that member rather than restating its value.

Three clauses, one case each per server, because a failure should name which
one broke:

  * **decl**    `PROTOCOL_VERSION` is a class-level assignment with a single
    `Name` target and a `str` constant value, and it is the first member after
    an optional class docstring.  Nine servers put it at `body[0]` and six at
    `body[1]` behind a docstring; both are the same rule.
  * **use**     the `"protocolVersion"` entry of the reply dict is
    `self.PROTOCOL_VERSION` -- an attribute load off `self`, never a literal
    and never a bare module-level name.
  * **literal** the declared string survives nowhere else in the file.  A
    second copy anywhere is a second place to edit, which is the whole defect.

WHY AN ANNOTATED DECLARATION PASSES
-----------------------------------
`PROTOCOL_VERSION: str = "..."` is accepted.  No server writes one today, but
the rule here is the PROPERTY -- one class-level binding carrying the value --
and gating the spelling `ast.Assign` would fail a correct refactor for no
reason.  This is `tests/test_handler_crash.py`'s explicit reasoning about
`log.exception` versus `log.error(..., exc_info=True)`, applied to a
declaration instead of a call.  An annotation with NO value binds nothing and
is still a failure.

THE NUMBER IS DERIVED, NEVER TYPED
----------------------------------
This file does not contain the protocol version, and must not.  Two copies of
it already exist in the repo -- the smoke harness's live assertion and the
test client's own handshake constant -- and a third one sitting in the suite
that polices copies would be self-refuting.

So group B asks a different question: the fifteen declared values must AGREE
WITH EACH OTHER.  That is strictly stronger than comparing each to a typed
string, because it cannot go stale.  When the version is bumped, this suite
stays green and says the new value in its INFO row; a typed copy would fail
fifteen cases for a correct change.  The literal-survival sweep uses each
file's OWN declared value for the same reason.

The one thing group B deliberately does NOT do is check the value against the
wire.  That is the smoke harness's job and it already does it; duplicating it
here would put a third copy of the number in the tree by the back door.

THE PREFIX COLLISION IS REAL, NOT HYPOTHETICAL
----------------------------------------------
`Scripts/mcp-postgres.py` declares a module-level `PROTOCOL_VERSION_3 = 196608`
-- the PostgreSQL wire protocol, an int, on a completely different subject.  A
substring or regex matcher flags it and a maintainer learns to ignore this
suite.  Every name comparison here is an exact AST identifier match, and group
D plants that exact collision as a fixture the analyser must stay SILENT on.

WHY THE METHOD NAME IS NEVER MATCHED
------------------------------------
The enclosing dispatcher has three spellings in this fleet: `async def
handle_message` in six servers, sync `def _handle_message` in eight, and
`async def _handle_message` in `mcp-purity.py` alone.  `MCP_SKELETON.md:268`
declares that drift intentional and section 5 refuses to unify sync and async
dispatch.  A gate keyed on the method name would therefore encode a divergence
the template explicitly blesses.  The anchors used instead are the
`"protocolVersion"` dict key and the class name, both invariant across all
fifteen.

SEVERITY: FAIL, AND WHY IT CANNOT FLAP
--------------------------------------
Every gate case here compares an AST against text already in the repo.  No
environment, no binary, no ordering, no clock, no network, no server launched
-- so a failure can only be a regression, never weather.  That is the criterion
`docs/subsystems/tests.md` sets for FAIL and the same argument
`tests/test_generated_region.py:53-57` and `tests/test_mcp_footprint.py:1518`
make for their own gated rows.  The only INFO row is group B's census, which
reports the derived value: a measurement, not a verdict.

DECLARED BLIND SPOTS -- on the page, because an unstated scope is the same
defect as a false invariant:

  1. **This gate reads structure and can never prove what goes on the wire.**
     A server could read the constant and then overwrite the reply downstream.
     The live handshake in `Scripts/_mcp_smoke_test.py` bounds that, and the
     two suites are complementary by design rather than by accident.
  2. **Only `class McpServer` is inspected.**  A server that moved its
     handshake into a base class or a mixin would report no class and fail
     loudly -- which is correct today, since no server has one, but it is a
     structural assumption rather than a law.
  3. **The literal sweep sees `ast.Constant` nodes only.**  The version
     restated in a COMMENT or a docstring is invisible here, because comments
     are not in the tree.  A docstring copy is reachable in principle; it is
     not swept because a prose mention is not an edit site.
  4. **`serverInfo.version` is deliberately out of scope.**  The fleet's
     fifteen `"1.0.0"` strings are fifteen independent version numbers that
     happen to coincide, not one shared constant, and gating them as if they
     were shared would invent a coupling nobody decided on.

NEGATIVE CONTROL (group D) -- mandatory, and load-bearing here
--------------------------------------------------------------
The live fleet is ALREADY compliant, so this gate has never been observed red
against a real file and never will be until something breaks.  A checker that
matched nothing would be indistinguishable from the fleet it is watching.
Group D therefore points the same analyser at synthetic servers carrying one
defect each -- the literal re-inlined at the reply, the constant moved to
module level, removed entirely, demoted below `__init__`, read unqualified,
bound to a non-string, the class renamed away, the initialize branch deleted,
the file made unparseable, and the value restated in a second dict -- plus six
CORRECT forms it must stay silent on, including the tab-indented host, the
docstring-first host, the async-dispatcher host, the annotated declaration and
the `PROTOCOL_VERSION_3` prefix collision.

NO SANDBOX, BECAUSE THERE IS NOTHING TO WRITE.  The analyser is a pure
function of source text, so planting a defect costs a string --
`tests/test_mcp_footprint.py:1553` makes exactly this argument for its own
in-memory control, and `tests/test_table_cells.py` writes nothing at all and
asserts that it wrote nothing.  This suite does the same, which is the
stronger form of the claim rather than a weaker one.

The case count IS typed in run.py's SUITES table: a server appearing without
being analysed IS the defect, so a count that moves when the fleet moves is
the alarm working, not noise.  The count lives in the SUITES table in
tests/run.py, never here.

AST only.  Starts nothing, imports no server, writes nothing, ~0.2s.

Usage:
  python3 tests/test_protocol_version.py
  python3 tests/test_protocol_version.py --brief
Exit code 0 iff every non-informational case passes.

Groups:
  A  GATE     -- three clauses per server: decl, use, literal
  B  FLEET    -- the declared values agree with each other (derived, not typed)
  C  ROSTER   -- every server on disk was analysed, and the analyser saw it
  D  control  -- planted defects the analyser MUST flag, correct forms it must not
  E  hygiene  -- wrote nothing, no bytecode, no new repo paths
"""

import ast
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "protocol_version"

SCAN_ROOT = "Scripts"
SERVER_GLOB_PREFIX = "mcp-"

SERVER_CLASS = "McpServer"
CONST_NAME = "PROTOCOL_VERSION"
REPLY_KEY = "protocolVersion"

GA = "A. GATE: the version is declared once and read, never restated"
GB = "B. FLEET: the declared values agree with each other"
GC = "C. ROSTER: every server on disk was analysed"
GD = "D. negative control"
GE = "E. hygiene"

WRITES = []

# Problem codes.  The control group asserts these by name.
NO_SERVER_CLASS = "NO-MCPSERVER-CLASS"
NO_CONSTANT = "CONSTANT-NOT-DECLARED"
MODULE_LEVEL = "CONSTANT-AT-MODULE-LEVEL"
NOT_FIRST = "CONSTANT-NOT-FIRST-MEMBER"
NOT_STRING = "CONSTANT-NOT-A-STRING"
NO_USE_SITE = "INITIALIZE-REPLY-NOT-FOUND"
INLINED = "VERSION-INLINED-AT-USE-SITE"
UNQUALIFIED = "VERSION-READ-UNQUALIFIED"
LITERAL_SURVIVES = "VERSION-LITERAL-SURVIVES"


# ---------------------------------------------------------------------------
# the analyser -- one function, two worlds: the live tree and group D's strings
# ---------------------------------------------------------------------------

def _src(node):
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:                                        # pragma: no cover
        return "<unrenderable>"


def _docstring_offset(body):
    """Index of the first real member, skipping a leading docstring."""
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return 1
    return 0


def _binding(node):
    """(name, value_node) if `node` binds a single plain name, else None.

    Both `X = v` and `X: T = v` qualify: the rule is one class-level binding
    carrying the value, not a particular spelling of it.  `X: T` with no value
    binds nothing and is reported by the caller as an absent constant.
    """
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id, node.value
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            return node.target.id, node.value
    return None


class Report:
    """What one server file says about its protocol version."""

    def __init__(self, name):
        self.name = name
        self.class_found = False
        self.value = None           # the declared string, or None
        self.decl_line = None
        self.decl_index = None      # position after an optional docstring
        self.use_sites = []         # (owner, lineno, value_node)
        self.extra_literals = []    # (lineno, rendered) outside the declaration
        self.clauses = {"decl": [], "use": [], "literal": []}
        self.detail = {"decl": [], "use": [], "literal": []}

    def fail(self, clause, code, line):
        if code not in self.clauses[clause]:
            self.clauses[clause].append(code)
        self.detail[clause].append("%s: %s" % (code, line))

    @property
    def problems(self):
        out = []
        for clause in ("decl", "use", "literal"):
            out.extend(self.clauses[clause])
        return out


def _find_use_sites(tree):
    """Every `"protocolVersion": <value>` entry, with the function owning it.

    Anchored on the dict KEY, never on the enclosing method name: this fleet
    spells that method three ways and `MCP_SKELETON.md:268` declares the drift
    intentional.
    """
    sites = []
    owners = [(None, tree)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owners.append((node.name, node))
    for owner, scope in owners:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == REPLY_KEY:
                    sites.append((owner, key.lineno, value))
    # A dict inside a function is reached twice -- once via the module walk and
    # once via its owner.  Keep the owned view, which is the informative one.
    best = {}
    for owner, lineno, value in sites:
        if lineno not in best or (best[lineno][0] is None and owner):
            best[lineno] = (owner, lineno, value)
    return [best[k] for k in sorted(best)]


def _reads_constant(value):
    """`self.PROTOCOL_VERSION` -- an attribute load off `self`, exactly."""
    return isinstance(value, ast.Attribute) \
        and value.attr == CONST_NAME \
        and isinstance(value.value, ast.Name) \
        and value.value.id == "self"


def analyse(path, source=None):
    """Measure one server file's protocol-version wiring."""
    report = Report(os.path.basename(path))
    if source is None:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        report.fail("decl", NO_SERVER_CLASS, "unparseable: %s" % exc)
        return report

    cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == SERVER_CLASS:
            cls = node
            break

    # -- the decl clause ---------------------------------------------------
    decl_value_node = None
    if cls is None:
        report.fail("decl", NO_SERVER_CLASS,
                    "no top-level `class %s` to inspect" % SERVER_CLASS)
    else:
        report.class_found = True
        offset = _docstring_offset(cls.body)
        for index, item in enumerate(cls.body):
            bound = _binding(item)
            if not bound or bound[0] != CONST_NAME:
                continue
            name, value_node = bound
            report.decl_line = item.lineno
            report.decl_index = index - offset
            if value_node is None:
                report.fail("decl", NO_CONSTANT,
                            "line %d: `%s` is annotated but bound to nothing, "
                            "so there is no value to read"
                            % (item.lineno, name))
            elif not (isinstance(value_node, ast.Constant)
                      and isinstance(value_node.value, str)):
                report.fail("decl", NOT_STRING,
                            "line %d: bound to %s, and the handshake reply "
                            "must carry a string"
                            % (item.lineno, _src(value_node)))
            else:
                report.value = value_node.value
                decl_value_node = value_node
            if report.decl_index != 0:
                report.fail("decl", NOT_FIRST,
                            "line %d: member %d of the class body, not the "
                            "first after the docstring -- the template shows "
                            "it above `__init__` so it is the first thing read"
                            % (item.lineno, report.decl_index))
            break
        else:
            at_module = [n.lineno for n in tree.body
                         if (_binding(n) or (None,))[0] == CONST_NAME]
            if at_module:
                report.fail("decl", MODULE_LEVEL,
                            "line %d: declared at module level; a class member "
                            "can be carried by a generated region and reached "
                            "as `self.%s`, a module global cannot"
                            % (at_module[0], CONST_NAME))
            else:
                report.fail("decl", NO_CONSTANT,
                            "`class %s` declares no `%s`"
                            % (SERVER_CLASS, CONST_NAME))

    # -- the use clause ----------------------------------------------------
    report.use_sites = _find_use_sites(tree)
    if not report.use_sites:
        report.fail("use", NO_USE_SITE,
                    "no dict carries a %r key, so either the handshake is gone "
                    "or this analyser has gone blind" % REPLY_KEY)
    for owner, lineno, value in report.use_sites:
        if _reads_constant(value):
            continue
        if isinstance(value, ast.Constant):
            report.fail("use", INLINED,
                        "line %d (%s): the reply restates %s instead of "
                        "reading the constant, so the version now has two "
                        "homes in this file"
                        % (lineno, owner or "module level", _src(value)))
        elif isinstance(value, ast.Name) and value.id == CONST_NAME:
            report.fail("use", UNQUALIFIED,
                        "line %d (%s): reads a bare `%s`, which resolves to a "
                        "module global rather than the class member"
                        % (lineno, owner or "module level", CONST_NAME))
        else:
            report.fail("use", INLINED,
                        "line %d (%s): the reply value is %s, not `self.%s`"
                        % (lineno, owner or "module level", _src(value),
                           CONST_NAME))

    # -- the literal clause ------------------------------------------------
    # Judged against this file's OWN declared value, so the suite never needs
    # to know what the fleet's version is.  The declaration itself and the
    # reply values are excluded: those are the use clause's business, and
    # reporting one defect under two codes would make the control table lie.
    if report.value is not None:
        skip = {id(decl_value_node)}
        skip.update(id(v) for _o, _l, v in report.use_sites)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or id(node) in skip:
                continue
            if isinstance(node.value, str) and node.value == report.value:
                report.extra_literals.append((node.lineno, _src(node)))
        if report.extra_literals:
            report.fail("literal", LITERAL_SURVIVES,
                        "the declared value is restated at line(s) %s -- each "
                        "is a second place to edit when the version moves"
                        % ", ".join(str(n) for n, _s in report.extra_literals))
    return report


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _server_files():
    root = H.repo_path(SCAN_ROOT)
    return sorted(n for n in os.listdir(root)
                  if n.startswith(SERVER_GLOB_PREFIX) and n.endswith(".py"))


CLAUSE_NOTE = {
    "decl": "declared once, as the first member of the class",
    "use": "the reply reads the member rather than restating it",
    "literal": "the value survives nowhere else in the file",
}


def group_gate(suite, reports):
    """A. three clauses per server, each its own case."""
    for name in sorted(reports):
        report = reports[name]
        for clause in ("decl", "use", "literal"):
            problems = report.clauses[clause]
            detail = ["file        : %s/%s" % (SCAN_ROOT, name),
                      "clause      : %s -- %s" % (clause, CLAUSE_NOTE[clause])]
            if clause == "decl":
                detail.append("declared    : %s"
                              % ("line %s, member %s of the class body"
                                 % (report.decl_line, report.decl_index)
                                 if report.decl_line else "NOWHERE"))
            elif clause == "use":
                detail.append("read at     : %s"
                              % (", ".join("line %d (%s)" % (ln, owner or "?")
                                           for owner, ln, _v
                                           in report.use_sites) or "NOWHERE"))
            else:
                detail.append("extra copies: %d" % len(report.extra_literals))
                if report.value is None:
                    detail.append("note        : no value was resolved, so "
                                  "there is nothing to sweep for -- the decl "
                                  "clause above owns that failure")
            detail += ["  " + line for line in report.detail[clause]]
            suite.record(GA, "%s [%s]" % (name, clause), problems,
                         detail=detail,
                         brief="%s | %s [%s] | %s"
                               % (H.FAIL if problems else H.PASS, name, clause,
                                  problems or "ok"))


def group_fleet(suite, reports):
    """B. the declared values agree -- derived, never typed."""
    by_value = {}
    for name in sorted(reports):
        value = reports[name].value
        by_value.setdefault(value, []).append(name)

    resolved = {v: names for v, names in by_value.items() if v is not None}
    problems = []
    if len(resolved) > 1:
        problems.append("the fleet declares %d different values: %s"
                        % (len(resolved),
                           "; ".join("%r in %s" % (v, ", ".join(names))
                                     for v, names in sorted(resolved.items()))))
    if None in by_value:
        problems.append("no value resolved for: %s"
                        % ", ".join(by_value[None]))
    suite.record(GB, "every server declares the same version", problems,
                 detail=["distinct    : %d" % len(resolved),
                         "servers     : %d" % len(reports),
                         "note        : agreement is asserted between the "
                         "files, never against a string typed here.  A typed "
                         "copy would fail fifteen cases on a correct version "
                         "bump, and would put a third copy of the number in a "
                         "suite whose whole subject is copies"])

    suite.record(GB, "the value the fleet agrees on", [], status=H.INFO,
                 detail=["value       : %s"
                         % (", ".join(sorted(repr(v) for v in resolved))
                            or "NONE RESOLVED"),
                         "note        : DERIVED from the tree at run time and "
                         "printed, not stored.  The wire-level assertion that "
                         "this is the right value lives in the smoke harness, "
                         "which handshakes every server; repeating it here "
                         "would duplicate the number this suite polices"])


def group_roster(suite, reports, files):
    """C. the tree was covered, and the analyser could actually see it."""
    missing = sorted(set(files) - set(reports))
    suite.record(GC, "every server file on disk was analysed",
                 [] if not missing
                 else ["not analysed: %s" % ", ".join(missing)],
                 detail=["in tree     : %d" % len(files),
                         "analysed    : %d" % len(reports),
                         "root        : %s/%s*.py"
                         % (SCAN_ROOT, SERVER_GLOB_PREFIX)])

    seen = [n for n, r in reports.items() if r.class_found]
    suite.record(GC, "the analyser resolved class %s in every server"
                 % SERVER_CLASS,
                 [] if len(seen) == len(files)
                 else ["resolved %d of %d: missing %s"
                       % (len(seen), len(files),
                          ", ".join(sorted(set(files) - set(seen))))],
                 detail=["resolved    : %d/%d" % (len(seen), len(files)),
                         "note        : a blindness floor -- an analyser that "
                         "stopped finding the class would report a clean fleet "
                         "while measuring nothing at all"])

    read = [n for n, r in reports.items() if r.use_sites]
    suite.record(GC, "the analyser resolved a handshake reply in every server",
                 [] if len(read) == len(files)
                 else ["resolved %d of %d: missing %s"
                       % (len(read), len(files),
                          ", ".join(sorted(set(files) - set(read))))],
                 detail=["resolved    : %d/%d" % (len(read), len(files)),
                         "note        : the second half of the floor.  The "
                         "decl clause alone would stay green on a server whose "
                         "reply the analyser never found"])


# -- group D fixtures --------------------------------------------------------

# A deliberately IMPOSSIBLE date.  The fixtures need a value and this one can
# never be mistaken for the fleet's, which is the string this file must not
# contain.  Substituted into the template so it is written exactly once.
CTL_VERSION = "0000-00-00"

_TEMPLATE = '''
import logging

log = logging.getLogger("ctl")


class McpServer:
    PROTOCOL_VERSION = "@VERSION@"

    def __init__(self):
        self.seen = 0

    @staticmethod
    def _result(msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _handle_message(self, msg):
        msg_id = msg.get("id")
        method = msg.get("method", "")

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "ctl", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        return self._result(msg_id, {"error": "unknown"})
'''

GOOD = _TEMPLATE.replace("@VERSION@", CTL_VERSION)

DECL_LINE = '    PROTOCOL_VERSION = "' + CTL_VERSION + '"\n'
USE_LINE = '                "protocolVersion": self.PROTOCOL_VERSION,\n'
INIT_LINE = '        self.seen = 0\n'
LOGGER_LINE = 'log = logging.getLogger("ctl")\n'
PING_BLOCK = ('        if method == "ping":\n'
              '            return self._result(msg_id, {})\n')
INIT_BRANCH = (
    '        if method == "initialize":\n'
    '            return self._result(msg_id, {\n'
    + USE_LINE
    + '                "serverInfo": {"name": "ctl", "version": "1.0.0"},\n'
      '                "capabilities": {"tools": {}},\n'
      '            })\n\n'
)

FIXTURES = {
    # name -> (source, expected problem codes)

    # -- defects the analyser MUST flag ------------------------------------
    "ctl_inlined.py": (
        GOOD.replace(USE_LINE,
                     '                "protocolVersion": "' + CTL_VERSION
                     + '",\n'),
        [INLINED]),
    "ctl_module_level.py": (
        GOOD.replace(DECL_LINE + "\n", "").replace(
            LOGGER_LINE,
            LOGGER_LINE + "\nPROTOCOL_VERSION = \"" + CTL_VERSION + "\"\n"),
        [MODULE_LEVEL]),
    "ctl_absent.py": (
        GOOD.replace(DECL_LINE + "\n", ""),
        [NO_CONSTANT]),
    "ctl_demoted.py": (
        GOOD.replace(DECL_LINE + "\n", "").replace(
            INIT_LINE, INIT_LINE + "\n" + DECL_LINE),
        [NOT_FIRST]),
    "ctl_unqualified.py": (
        GOOD.replace(USE_LINE,
                     '                "protocolVersion": PROTOCOL_VERSION,\n'),
        [UNQUALIFIED]),
    "ctl_not_string.py": (
        GOOD.replace(DECL_LINE, '    PROTOCOL_VERSION = 0\n'),
        [NOT_STRING]),
    "ctl_annotation_only.py": (
        GOOD.replace(DECL_LINE, '    PROTOCOL_VERSION: str\n'),
        [NO_CONSTANT]),
    "ctl_no_class.py": (
        GOOD.replace("class McpServer:", "class OtherServer:"),
        [NO_SERVER_CLASS]),
    "ctl_no_use_site.py": (
        GOOD.replace(INIT_BRANCH, ""),
        [NO_USE_SITE]),
    "ctl_unparseable.py": (
        GOOD + "\n\ndef broken(:\n    pass\n",
        [NO_SERVER_CLASS]),
    "ctl_literal_survives.py": (
        GOOD.replace(PING_BLOCK,
                     '        if method == "legacy":\n'
                     '            return self._result(msg_id, {"v": "'
                     + CTL_VERSION + '"})\n\n' + PING_BLOCK),
        [LITERAL_SURVIVES]),

    # -- correct forms it must stay SILENT on ------------------------------
    "ctl_good.py": (GOOD, []),
    "ctl_good_docstring.py": (
        GOOD.replace("class McpServer:\n",
                     'class McpServer:\n    """Six servers look like this."""\n\n'),
        []),
    "ctl_good_tabs.py": (GOOD.replace("    ", "\t"), []),
    "ctl_good_async.py": (
        GOOD.replace("    def _handle_message(self, msg):",
                     "    async def handle_message(self, msg):"),
        []),
    "ctl_good_annotated.py": (
        GOOD.replace(DECL_LINE,
                     '    PROTOCOL_VERSION: str = "' + CTL_VERSION + '"\n'),
        []),
    "ctl_prefix_collision.py": (
        GOOD.replace(LOGGER_LINE,
                     LOGGER_LINE + "\nPROTOCOL_VERSION_3 = 196608\n").replace(
            INIT_LINE,
            INIT_LINE + "        self.pg = PROTOCOL_VERSION_3\n"),
        []),
}


def group_control(suite):
    """D. planted defects the analyser MUST flag, correct forms it must not."""
    flagged = 0
    for name, (source, expected) in sorted(FIXTURES.items()):
        report = analyse(name, source=source)
        got = sorted(report.problems)
        want = sorted(expected)
        problems = []
        if got != want:
            problems.append("codes %r != expected %r" % (got, want))
        if expected:
            flagged += 1 if report.problems else 0
        detail = ["expected    : %r" % want,
                  "got         : %r" % got]
        for clause in ("decl", "use", "literal"):
            detail += ["  " + line for line in report.detail[clause]]
        suite.record(GD, "control-" + name, problems, detail=detail,
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got))

    must_flag = sum(1 for _n, (_s, e) in FIXTURES.items() if e)
    suite.record(GD, "control fires at all",
                 [] if flagged == must_flag
                 else ["only %d of %d defective fixtures were flagged"
                       % (flagged, must_flag)],
                 detail=["fixtures    : %d (%d defective, %d correct)"
                         % (len(FIXTURES), must_flag,
                            len(FIXTURES) - must_flag),
                         "note        : this suite can never be observed red "
                         "against the live tree, because the fleet converged "
                         "before the gate was written.  Without this group an "
                         "analyser that silently matched nothing would be "
                         "indistinguishable from the fleet it is watching"])


def group_hygiene(suite, pyc_before, tree_before):
    """E. writes nothing, no bytecode, no new repo paths."""
    suite.record(GE, "this suite writes nothing at all",
                 [] if not WRITES
                 else ["%d write(s): %s" % (len(WRITES), WRITES[:5])],
                 detail=["writes      : %d" % len(WRITES),
                         "note        : there is no sandbox because there is "
                         "no fixture on disk -- the analyser is a pure "
                         "function of source text, so every control is a "
                         "string in this file"])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GE, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d"
                         % (len(pyc_before), len(pyc_after))])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GE, "no new repo paths",
                 [] if not added
                 else ["%d new path(s): %s" % (len(added), added[:5])],
                 detail=["note        : nothing here opens a file for "
                         "writing, so this case is a floor under that claim "
                         "rather than a check on a sandbox"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="every MCP server declares the protocol version once "
                          "as a class constant and reads it at the handshake, "
                          "with the fleet's agreement derived rather than typed",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    files = _server_files()
    reports = {n: analyse(H.repo_path(SCAN_ROOT, n)) for n in files}

    group_gate(suite, reports)
    group_fleet(suite, reports)
    group_roster(suite, reports, files)
    group_control(suite)
    group_hygiene(suite, pyc_before, tree_before)

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
