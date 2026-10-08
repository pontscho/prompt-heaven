#!/usr/bin/env python3
"""The accepted-parameter tables of the hosts that learned to refuse a name.

Nine servers used to drop an unknown parameter key without a word: mcp-forge,
mcp-jenkins, mcp-tshark, mcp-context7, mcp-lldb, mcp-gdc and the three legacy
LSP servers (mcp-clangd, mcp-cuda, mcp-lua-lsp). Each now refuses one through
the generated `_unknown_params_refusal` block (Scripts/_mcp_dispatch.py),
against a per-function table it owns, `ACCEPTED_PARAMS`. The decision is ADR
0030's deferred Alternative 5, taken by the user: a host must complain, and
complain sensibly enough that the model can work out what was wrong.

A refusal has one failure mode worse than the silence it replaces: a FALSE
refusal of a parameter that works today. A table is written by hand, so it can
drift from the handlers it describes, and this suite is what holds it to them.
Nothing here trusts the table; every rule compares it with something else:

  R0 COVERAGE  -- every callable function name (aliases included) resolves to
                  a table row, and every row names a real function.
  R1 READS     -- every key a function's handler READS, followed through every
                  module-level helper and method the params dict is handed to,
                  is accepted. A miss here is exactly the false refusal.
                  Read from the AST, never by regex, because both regex
                  false-positive shapes are live in this fleet.
  R2 NO DEAD   -- every accepted key is read by that handler, or by the
                  dispatcher on every function's behalf (the reply ceiling),
                  or by the dispatcher for that one function (declared per
                  host and itself verified against the dispatcher's AST).
                  Accept-and-ignore is the silent drop wearing a table.
  R3 DOCS      -- every parameter the host's documentation names for a
                  function -- its skill, and its own tool description -- is
                  accepted for that function, directly or through an alias.
  R4 ALIASES   -- every parameter alias resolves to a key some function
                  accepts; an alias into nothing is a documented spelling the
                  host would now refuse under its canonical name.

The live half -- the refusal on the wire, the suggestion, and a documented
call NOT refused -- is the smoke harness's (Scripts/_mcp_smoke_test.py:
near_miss_checks). Group C plants each defect in a synthetic host so every
rule is proven to fire; a checker that silently matches nothing is
indistinguishable from a clean tree.

Groups:
  A  per host: R0..R4
  B  the dispatcher judges the caller's keys: mcp-jenkins' project scope adds
     a job_path, and that injected key must never be what gets refused
  C  controls -- each rule fires on a planted defect, and stays silent on a
     correct synthetic host
  D  hygiene
"""

import ast
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "param_contract"
TABLE = "ACCEPTED_PARAMS"


def skill(name):
    return H.repo_path("ClaudeCode", "skills", name, "SKILL.md")


# ---------------------------------------------------------------------------
# The AST reader: which string keys does a function read off a params dict?
# ---------------------------------------------------------------------------

KEY_METHODS = ("get", "pop", "setdefault")
WHOLE_METHODS = ("items", "keys", "values")


class Reads:
    """Every literal key read off one tracked name, followed through helpers.

    A key is read by `d.get("k")`, `d.pop("k")`, `d.setdefault("k")`, a load
    of `d["k"]`, and `"k" in d`. When the tracked dict is handed to another
    function -- positionally or by keyword -- that function is analysed with
    the receiving parameter tracked, so a helper like `_resolve_session(mgr,
    args)` or `_offset(args)` counts as the caller's read. A method call
    resolves by name against every class in the module (a name two classes
    define is not followed). A plain rebinding `x = d` or `x = dict(d)` tracks
    `x` too. `d.items()` / `keys()` / `values()` and `**d` read EVERY key, which
    no table can bound, so they are reported as dynamic rather than guessed.
    """

    def __init__(self, source):
        self.tree = ast.parse(source)
        self.funcs = {}
        self.methods = {}
        dupes = set()
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name in self.methods:
                            dupes.add(item.name)
                        self.methods[item.name] = item
        for name in dupes:
            self.methods.pop(name, None)
        self.memo = {}

    @staticmethod
    def _params_of(node):
        args = node.args
        return [a.arg for a in list(getattr(args, "posonlyargs", [])) + list(args.args)]

    def _resolve(self, call):
        """(def node, positional offset) a call reaches, or (None, 0)."""
        func = call.func
        if isinstance(func, ast.Name) and func.id in self.funcs:
            return self.funcs[func.id], 0
        if isinstance(func, ast.Attribute) and func.attr in self.methods:
            target = self.methods[func.attr]
            static = any(isinstance(d, ast.Name) and d.id == "staticmethod"
                         for d in target.decorator_list)
            return target, (0 if static else 1)
        return None, 0

    def _arg_for(self, call, target, offset, param):
        """The call's argument node bound to `param` of `target`, or None."""
        names = self._params_of(target)
        if param not in names:
            return None
        index = names.index(param) - offset
        if 0 <= index < len(call.args):
            return call.args[index]
        for kw in call.keywords:
            if kw.arg == param:
                return kw.value
        return None

    def _callee(self, call, tracked):
        """(def node, receiving parameter name, offset) for every tracked arg."""
        out = []
        target, offset = self._resolve(call)
        if target is None:
            return out
        names = self._params_of(target)
        for i, arg in enumerate(call.args):
            if isinstance(arg, ast.Name) and arg.id in tracked and i + offset < len(names):
                out.append((target, names[i + offset], offset))
        for kw in call.keywords:
            if kw.arg and isinstance(kw.value, ast.Name) and kw.value.id in tracked:
                if kw.arg in names:
                    out.append((target, kw.arg, offset))
        return out

    def of(self, func_name, var, skip=()):
        """(keys, dynamic) read off `var` inside `func_name`, transitively."""
        node = self.funcs.get(func_name) or self.methods.get(func_name)
        if node is None:
            return set(), ["no function %r in the module" % func_name]
        keys, dynamic, keyparams = self._walk(node, var, frozenset(skip), set())
        dynamic = dynamic + ["a key taken from parameter %r of %s" % (k, func_name)
                             for k in sorted(keyparams)]
        return keys, dynamic

    @staticmethod
    def _loop_literals(node):
        """{name: [str, ...]} for every `for name in ("a", "b", ...)` in `node`."""
        out = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.For) and isinstance(sub.target, ast.Name) \
                    and isinstance(sub.iter, (ast.Tuple, ast.List)) and sub.iter.elts \
                    and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                            for e in sub.iter.elts):
                out.setdefault(sub.target.id, []).extend(e.value for e in sub.iter.elts)
        return out

    def _key_of(self, node, expr, keys, keyparams, dynamic):
        """Record the key `expr` names: a literal, a loop variable over a tuple
        of literals (`for key in ("url", "domain"): if key in args`), one of
        `node`'s parameters (resolved at each call site), or -- anything else --
        a computed key."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            keys.add(expr.value)
        elif isinstance(expr, ast.Name) and expr.id in self._loop_literals(node):
            keys.update(self._loop_literals(node)[expr.id])
        elif isinstance(expr, ast.Name) and expr.id in self._params_of(node):
            keyparams.add(expr.id)
        else:
            dynamic.append("a computed key in %s" % node.name)

    def _walk(self, node, var, skip, stack):
        """(keys, dynamic, keyparams). `keyparams` are parameters of `node` that
        are used AS a key -- `_bool_param(params, "keep_file", True)` reads its
        key off its own argument -- so each call site turns them into keys."""
        key = (id(node), var)
        if key in self.memo:
            return self.memo[key]
        if key in stack:
            return set(), [], set()
        stack = stack | {key}
        tracked = {var}
        changed = True
        while changed:
            changed = False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and len(sub.targets) == 1 \
                        and isinstance(sub.targets[0], ast.Name):
                    value = sub.value
                    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                            and value.func.id == "dict" and len(value.args) == 1:
                        value = value.args[0]
                    if isinstance(value, ast.Name) and value.id in tracked \
                            and sub.targets[0].id not in tracked:
                        tracked.add(sub.targets[0].id)
                        changed = True
        keys, dynamic, keyparams = set(), [], set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                        and func.value.id in tracked:
                    if func.attr in KEY_METHODS and sub.args:
                        self._key_of(node, sub.args[0], keys, keyparams, dynamic)
                    elif func.attr in WHOLE_METHODS:
                        dynamic.append("%s.%s() in %s" % (func.value.id, func.attr, node.name))
                for kw in sub.keywords:
                    if kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id in tracked:
                        dynamic.append("**%s in %s" % (kw.value.id, node.name))
                for target, param, offset in self._callee(sub, tracked):
                    if target.name in skip:
                        continue
                    sub_keys, sub_dyn, sub_kp = self._walk(target, param, skip, stack)
                    keys |= sub_keys
                    dynamic += sub_dyn
                    for kp in sorted(sub_kp):
                        arg = self._arg_for(sub, target, offset, kp)
                        if arg is None:
                            # The helper's default names the key.
                            default = self._default_of(target, kp)
                            if default is not None:
                                self._key_of(node, default, keys, keyparams, dynamic)
                            continue
                        self._key_of(node, arg, keys, keyparams, dynamic)
            elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Load) \
                    and isinstance(sub.value, ast.Name) and sub.value.id in tracked:
                index = sub.slice
                if isinstance(index, ast.Index):  # Python 3.8 and older
                    index = index.value
                self._key_of(node, index, keys, keyparams, dynamic)
            elif isinstance(sub, ast.Compare) and len(sub.ops) == 1 \
                    and isinstance(sub.ops[0], (ast.In, ast.NotIn)) \
                    and isinstance(sub.comparators[0], ast.Name) \
                    and sub.comparators[0].id in tracked:
                self._key_of(node, sub.left, keys, keyparams, dynamic)
        result = (keys, dynamic, keyparams)
        self.memo[key] = result
        return result

    def _default_of(self, target, param):
        """The default expression of `param` in `target`, or None."""
        args = target.args
        positional = list(getattr(args, "posonlyargs", [])) + list(args.args)
        defaults = list(args.defaults)
        pad = len(positional) - len(defaults)
        for i, arg in enumerate(positional):
            if arg.arg == param and i >= pad:
                return defaults[i - pad]
        return None


# ---------------------------------------------------------------------------
# The documentation reader: which (function, param) pairs does a doc name?
# ---------------------------------------------------------------------------

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
CALL_FN = re.compile(r'(?:"function"\s*:\s*|\bfunction\s*=\s*)"([A-Za-z0-9_\-]+)"')
CALL_PARAMS = re.compile(r'(?:"params"\s*:\s*|\bparams\s*=\s*)\{')
HEADING = re.compile(r"^(#{2,4})\s+(.*)$")
# A parameter name in this fleet starts lower-case (snake_case, or gdc's one
# camelCase `savePath`); a capitalised backticked word in a bullet is a reply
# FIELD ("- `Title` -- library name"), not something a caller sends.
PARAM_IDENT = r"[a-z_][A-Za-z0-9_]*"
TABLE_ROW = re.compile(r"^\|\s*`(%s)`\s*\|" % PARAM_IDENT)
BULLET = re.compile(r"^\s*[*\-]\s+`(%s)`" % PARAM_IDENT)
# Only the `→` arrow, and only on a line that says "alias": `->` is how these
# docs map a Bash command to a forge function, and prose uses `→` for "becomes"
# ("Present → `buildWithParameters`").
ARROW = re.compile(r"`?(%s(?:/%s)*)`?\s*→\s*`?(%s)`?" % (IDENT, IDENT, IDENT))
FENCE = "```"


def object_keys(text, start):
    """Top-level string keys of the {...} object opening at text[start]."""
    keys, depth, i, n = [], 0, start, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            token = text[i + 1:j]
            k = j + 1
            while k < n and text[k] in " \t":
                k += 1
            if depth == 1 and k < n and text[k] == ":" and re.fullmatch(IDENT, token):
                keys.append(token)
            i = j + 1
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return keys
        i += 1
    return keys


def doc_pairs(text, known):
    """(function or None, param) pairs the text documents.

    `known` maps every spelling of a function the host serves to its table
    row. Five shapes, each one this fleet's docs actually use:
      * a call form anywhere -- `function="x", params={...}` or the JSON
        `{"function":"x","params":{...}}` -- read to its params object's
        top-level keys;
      * under a heading naming a function: a fenced `{...}` block that is the
        params object itself (lldb, context7), a parameter table's first
        column (forge, luals), and a backticked bullet (jenkins);
      * an alias arrow `a/b→c`, a GLOBAL statement that `c` is a parameter,
        returned with function None.
    """
    pairs = set()
    for m in CALL_FN.finditer(text):
        fn = known.get(m.group(1))
        window = text[m.end():m.end() + 600]
        nxt = CALL_FN.search(window)
        pm = CALL_PARAMS.search(window)
        if fn is None or pm is None or (nxt is not None and nxt.start() < pm.start()):
            continue
        for key in object_keys(window, pm.end() - 1):
            pairs.add((fn, key))
    for line in text.splitlines():
        if "alias" in line.lower():
            for m in ARROW.finditer(line):
                pairs.add((None, m.group(2)))

    lines = text.splitlines()
    section_fn, section_level, in_fence, block = None, 0, False, []
    for line in lines:
        if line.strip().startswith(FENCE):
            if in_fence:
                body = "\n".join(block)
                stripped = body.lstrip()
                if section_fn and stripped.startswith("{") and not CALL_FN.search(body):
                    for key in object_keys(stripped, 0):
                        pairs.add((section_fn, key))
                block = []
            in_fence = not in_fence
            continue
        if in_fence:
            block.append(line)
            continue
        hm = HEADING.match(line)
        if hm:
            level = len(hm.group(1))
            if section_fn is None or level <= section_level:
                words = re.findall(r"[A-Za-z0-9_\-]+", hm.group(2))
                section_fn = next((known[w] for w in words if w in known), None)
                section_level = level
            continue
        if section_fn is None:
            continue
        for rx in (TABLE_ROW, BULLET):
            m = rx.match(line)
            if m:
                pairs.add((section_fn, m.group(1)))
    return pairs


# ---------------------------------------------------------------------------
# The hosts
# ---------------------------------------------------------------------------

def handler_rows(mod, map_name, arg_index, exclude=()):
    """{row: [(handler name, receiving param)]} and {spelling: row}.

    A row is a table key; every spelling in the handler map whose handler is
    that row's handler (an alias entry shares the function object) resolves
    to it. A spelling no row's handler serves is reported by R0.
    """
    table = getattr(mod, TABLE, {}) or {}
    handlers = getattr(mod, map_name)
    roots, spellings = {}, {}
    for row in table:
        handler = handlers.get(row)
        if handler is None:
            continue
        code = handler.__code__
        param = code.co_varnames[arg_index] if code.co_argcount > arg_index else None
        roots[row] = [(handler.__name__, param)]
    for spelling, handler in handlers.items():
        if spelling in exclude:
            continue
        row = next((r for r in table if handlers.get(r) is handler), None)
        spellings[spelling] = row
        if row is None:
            # Read anyway, so the reads row prints what the handler takes even
            # while the table is missing it -- the evidence a fix is written from.
            code = handler.__code__
            param = code.co_varnames[arg_index] if code.co_argcount > arg_index else None
            roots[spelling] = [(handler.__name__, param)]
    for alias, canonical in (getattr(mod, "FUNCTION_ALIASES", None) or {}).items():
        spellings[alias] = canonical if canonical in table else None
    return roots, spellings


def forge_rows(mod):
    table = getattr(mod, TABLE, {}) or {}
    names = {"list": "handle_list", "describe": "handle_describe",
             "build": "handle_build", "test": "handle_test", "clean": "handle_clean"}
    roots = {row: ([(names[row], "params")] if row in names else []) for row in table}
    spellings = {name: (name if name in table else None) for name in mod.FORGE_FUNCTIONS}
    return roots, spellings


def lldb_dispatch_for(mod):
    # `offset` is honoured only by the line-pageable functions, and the policy
    # table that says which is the host's own (CAP_POLICY), so it is read, not
    # restated. A non-pageable function never reads it.
    return {fn: {"offset"} for fn, (_bias, pageable) in mod.CAP_POLICY.items() if pageable}


HOSTS = [
    {"file": "mcp-forge.py",
     "rows": forge_rows,
     "every": {"max_answer_chars"},
     "every_roots": [("_answer_ceiling", "params")],
     "dispatch": ("handle_forge_call", "params"),
     "dispatch_for": lambda mod: {"validate": {"path"}},
     "aliases": "PARAM_ALIASES",
     "docs": [("skill", skill("mcp-forge")), ("attr", "FORGE_CALL_TOOL")],
     "doc_not_params": {
         "grep": "a key of the `filter` sub-object (FILTER_ALIASES), not a param",
         "grep_context": "a key of the `filter` sub-object, not a param",
         "invert_grep": "a key of the `filter` sub-object, not a param",
     }},
    {"file": "mcp-jenkins.py",
     "rows": lambda mod: handler_rows(mod, "HANDLERS", 0),
     "every": {"max_answer_chars"},
     "every_roots": [("handle_jenkins_call", "params")],
     "dispatch": ("handle_jenkins_call", "params"),
     "aliases": "PARAM_ALIASES",
     "docs": [("skill", skill("mcp-jenkins")), ("attr", "JENKINS_CALL_TOOL")],
     "doc_not_params": {
         "buildWithParameters": "the Jenkins REST endpoint start_build picks "
                                "(\"Present -> `buildWithParameters`\"), on a line "
                                "that also lists the `parameters` aliases",
     }},
    {"file": "mcp-tshark.py",
     "rows": lambda mod: handler_rows(mod, "HANDLERS", 0),
     "every": set(),
     "aliases": "PARAM_ALIASES",
     "docs": [("attr", "TSHARK_CALL_TOOL")]},
    {"file": "mcp-context7.py",
     "rows": lambda mod: handler_rows(mod, "ALL_HANDLERS", 0, exclude=("context7_call",)),
     "every": set(),
     "aliases": None,
     "docs": [("skill", skill("mcp-context7")), ("listed", "LISTED_TOOLS")]},
    {"file": "mcp-lldb.py",
     "rows": lambda mod: handler_rows(mod, "ALL_HANDLERS", 1, exclude=("lldb_call",)),
     "every": {"max_answer_chars"},
     "every_roots": [("_apply_cap", "args")],
     "dispatch": ("_apply_cap", "args"),
     "dispatch_for": lldb_dispatch_for,
     "aliases": None,
     "docs": [("skill", skill("mcp-lldb")), ("listed", "LISTED_TOOLS")]},
    {"file": "mcp-gdc.py",
     "rows": lambda mod: handler_rows(mod, "ALL_HANDLERS", 1, exclude=("gdc_call",)),
     "every": {"max_answer_chars"},
     "every_roots": [("_answer_ceiling", "source")],
     "aliases": None,
     "docs": [("skill", skill("mcp-gdc")), ("listed", "LISTED_TOOLS")]},
    {"file": "mcp-clangd.py",
     "rows": lambda mod: handler_rows(mod, "ALL_HANDLERS", 0, exclude=("clangd_call",)),
     "every": set(),
     "aliases": "PARAM_ALIASES",
     "docs": [("skill", skill("mcp-clangd")), ("listed", "LISTED_TOOLS")]},
    {"file": "mcp-cuda.py",
     "rows": lambda mod: handler_rows(mod, "ALL_HANDLERS", 0, exclude=("cuda_call",)),
     "every": set(),
     "aliases": "PARAM_ALIASES",
     "docs": [("skill", skill("mcp-cuda")), ("listed", "LISTED_TOOLS")]},
    {"file": "mcp-lua-lsp.py",
     "rows": lambda mod: handler_rows(mod, "ALL_HANDLERS", 0, exclude=("luals_call",)),
     "every": set(),
     "aliases": "PARAM_ALIASES",
     "docs": [("skill", skill("mcp-luals")), ("listed", "LISTED_TOOLS")],
     # The skill documents purity_call (it says luals_call no longer exists),
     # whose symbol_context / symbol_change_impact take a path; this legacy
     # server's handlers search the workspace by name and never read one, so a
     # path there was always dropped. Refusing it is the honest answer, and
     # accepting it to be ignored is the defect this suite exists to prevent.
     "doc_not_read": {
         ("luals_symbol_context", "relative_path"):
             "purity_call's parameter; this handler never scoped by path",
         ("luals_symbol_change_impact", "relative_path"):
             "purity_call's parameter; this handler never scoped by path",
     }},
]


def doc_texts(mod, docs):
    """[(label, text)] for a host's documentation sources."""
    out = []
    for kind, ref in docs:
        if kind == "skill":
            with open(ref, encoding="utf-8") as fh:
                out.append((os.path.relpath(ref, H.REPO_ROOT), fh.read()))
        elif kind == "attr":
            tool = getattr(mod, ref)
            out.append(("%s.description" % ref, tool.get("description", "")
                        + "\n" + repr(tool.get("inputSchema", {}))))
        elif kind == "listed":
            for tool in getattr(mod, ref):
                out.append(("%s[%s].description" % (ref, tool.get("name")),
                            tool.get("description", "")))
    return out


# ---------------------------------------------------------------------------
# The rules, written once over plain data so group C can drive them too
# ---------------------------------------------------------------------------

def rule_coverage(table, spellings):
    problems = []
    for spelling, row in sorted(spellings.items()):
        if row is None:
            problems.append("function %r resolves to no %s row" % (spelling, TABLE))
    served = set(r for r in spellings.values() if r)
    for row in sorted(set(table) - served):
        problems.append("%s row %r names no callable function" % (TABLE, row))
    return problems


def rule_reads(table, reads, alias_keys):
    problems = []
    for row in sorted(table):
        keys, dynamic = reads.get(row, (set(), []))
        for d in dynamic:
            problems.append("%s: reads every key (%s) -- no table can bound it" % (row, d))
        missing = sorted(keys - set(alias_keys) - set(table[row]))
        if missing:
            problems.append("%s: the handler reads %s, which the table refuses"
                            % (row, ", ".join(missing)))
    return problems


def rule_no_dead(table, reads, every, dispatch_for):
    problems = []
    for row in sorted(table):
        keys = reads.get(row, (set(), []))[0]
        extra = set(every) | set(dispatch_for.get(row, ()))
        dead = sorted(set(table[row]) - keys - extra)
        if dead:
            problems.append("%s: accepts %s, which nothing reads (accept-and-ignore)"
                            % (row, ", ".join(dead)))
        lost = sorted(set(every) - set(table[row]))
        if lost:
            problems.append("%s: refuses the every-function key(s) %s" % (row, ", ".join(lost)))
    return problems


def rule_docs(table, pairs, aliases):
    problems = []
    union = set().union(*table.values()) if table else set()
    for fn, key in sorted(pairs, key=lambda p: (p[0] or "", p[1])):
        offered = union if fn is None else set(table.get(fn, ()))
        if key in offered or aliases.get(key) in offered:
            continue
        problems.append("%s documents %r, which is refused"
                        % ("the host" if fn is None else fn, key))
    return problems


def rule_aliases(table, aliases):
    union = set().union(*table.values()) if table else set()
    return ["alias %r -> %r: no function accepts %r" % (a, c, c)
            for a, c in sorted(aliases.items()) if c not in union]


# ---------------------------------------------------------------------------
# Group A -- per host
# ---------------------------------------------------------------------------

def check_host(suite, row):
    label = row["file"][len("mcp-"):-len(".py")]
    path = H.repo_path("Scripts", row["file"])
    problems_load = []
    try:
        mod = H.load_module_from_path("param_contract_" + label.replace("-", "_"), path)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except Exception as exc:  # noqa: BLE001 -- recorded as the finding
        mod, source = None, ""
        problems_load.append("does not load: %s: %s" % (type(exc).__name__, exc))
    table = dict(getattr(mod, TABLE, None) or {}) if mod is not None else {}
    if mod is not None and not table:
        problems_load.append("defines no %s table" % TABLE)
    g = "A"

    def rec(rule, problems, detail=()):
        suite.record(g, "%s-%s" % (label, rule), problems_load + problems, detail=list(detail))

    if mod is None:
        for rule in ("coverage", "reads", "no-dead", "docs", "aliases"):
            rec(rule, [])
        return

    roots, spellings = row["rows"](mod)
    rec("coverage", rule_coverage(table, spellings),
        ["%d row(s), %d callable spelling(s)" % (len(table), len(spellings))])

    reader = Reads(source)
    handler_names = set(name for rs in roots.values() for name, _ in rs)
    reads = {}
    for fn, rs in roots.items():
        keys, dynamic = set(), []
        for func_name, param in rs:
            if param is None:
                dynamic.append("handler %s takes no params argument" % func_name)
                continue
            k, d = reader.of(func_name, param)
            keys |= k
            dynamic += d
        reads[fn] = (keys, dynamic)
    aliases = dict(getattr(mod, row["aliases"]) if row.get("aliases") else {})
    rec("reads", rule_reads(table, reads, aliases),
        ["%s: %s" % (fn, ", ".join(sorted(reads[fn][0])) or "-") for fn in sorted(reads)])

    every = set(row.get("every", ()))
    dispatch_for = row.get("dispatch_for", lambda m: {})(mod)
    verify = []
    seen = set()
    for func_name, var in row.get("every_roots", []):
        seen |= reader.of(func_name, var, skip=handler_names)[0]
    verify += ["every-function key %r is read by no dispatcher root" % k
               for k in sorted(every - seen)]
    if dispatch_for:
        func_name, var = row["dispatch"]
        dkeys = reader.of(func_name, var, skip=handler_names)[0]
        for fn, keys in sorted(dispatch_for.items()):
            verify += ["%s: the dispatcher is declared to read %r for it, and does not"
                       % (fn, k) for k in sorted(set(keys) - dkeys)]
    rec("no-dead", verify + rule_no_dead(table, reads, every, dispatch_for),
        ["every-function: %s" % (", ".join(sorted(every)) or "-"),
         "dispatcher-for: %s" % ("; ".join("%s=%s" % (f, ",".join(sorted(k)))
                                            for f, k in sorted(dispatch_for.items())) or "-")])

    known = {s: r for s, r in spellings.items() if r}
    known.update({r: r for r in table})
    pairs, sources = set(), []
    for doc_label, text in doc_texts(mod, row["docs"]):
        found = doc_pairs(text, known)
        sources.append("%s: %d pair(s)" % (doc_label, len(found)))
        pairs |= found
    # A word the reader picks up that is not a parameter of this host, each
    # with its reason. A declared word nothing reads any more is stale.
    not_params = row.get("doc_not_params", {})
    stale = ["doc_not_params %r: the docs no longer name it (stale)" % k
             for k in sorted(set(not_params) - set(key for _fn, key in pairs))]
    pairs = set(p for p in pairs if p[1] not in not_params)
    sources += ["not a param: %s -- %s" % (k, why) for k, why in sorted(not_params.items())]
    # A documented (function, param) pair the handler genuinely never read,
    # declared with its reason and REFUSED on the wire -- never accepted to be
    # ignored. Stale when the docs stop naming it, or when the host starts
    # accepting it (then the declaration hides nothing and must go).
    not_read = row.get("doc_not_read", {})
    for (fn, key), why in sorted(not_read.items()):
        if (fn, key) not in pairs:
            stale.append("doc_not_read %s/%s: the docs no longer name it (stale)" % (fn, key))
        elif key in table.get(fn, ()) or aliases.get(key) in table.get(fn, ()):
            stale.append("doc_not_read %s/%s: now accepted, so the declaration is stale"
                         % (fn, key))
        sources.append("documented, not read: %s/%s -- %s" % (fn, key, why))
    pairs -= set(not_read)
    rec("docs", stale + rule_docs(table, pairs, aliases), sources)

    rec("aliases", rule_aliases(table, aliases),
        ["%d alias(es)" % len(aliases)])


# ---------------------------------------------------------------------------
# Group C -- controls: every rule fires on a planted defect
# ---------------------------------------------------------------------------

SYNTHETIC = '''
ALIASES = {"q": "query"}

def _offset(args):
    return int(args.get("offset", 0))

def _flag(params, key, default):
    return params.get(key, default)

class Mgr:
    def page(self, args):
        return args.get("target_id")

def handle_search(params):
    mgr = Mgr()
    mgr.page(params)
    _flag(params, "keep", True)
    for key in ("domain", "secure"):
        if key in params:
            pass
    return params.get("query"), _offset(params), "limit" in params

def handle_dump(params):
    return [k for k, v in params.items()]
'''


def group_controls(suite):
    reader = Reads(SYNTHETIC)
    keys, dynamic = reader.of("handle_search", "params")
    want = {"query", "offset", "target_id", "limit", "keep", "domain", "secure"}
    suite.record("C", "reads-follow-helpers-and-methods",
                 [] if keys == want and not dynamic else
                 ["read %s (dynamic %s), wanted %s" % (sorted(keys), dynamic, sorted(want))],
                 detail=["a module helper (_offset), a method (Mgr.page), a get, an `in`,",
                         "a key handed INTO a helper (_flag(params, \"keep\", True)), and",
                         "a loop over a tuple of literal keys (gdc's set_cookie shape)"])
    _k, dyn = reader.of("handle_dump", "params")
    suite.record("C", "reads-flag-a-whole-dict-read",
                 [] if dyn else ["params.items() was not reported as dynamic"])

    good = {"search": {"query", "offset", "target_id", "limit", "keep", "domain", "secure"}}
    reads = {"search": (keys, [])}
    clean = (rule_reads(good, reads, {}) + rule_no_dead(good, reads, set(), {})
             + rule_docs(good, {("search", "query"), ("search", "q"), (None, "limit")},
                         {"q": "query"})
             + rule_aliases(good, {"q": "query"})
             + rule_coverage(good, {"search": "search", "find": "search"}))
    suite.record("C", "correct-host-is-silent", clean)

    plants = (
        ("R1 a read the table refuses", rule_reads(
            {"search": good["search"] - {"secure"}}, reads, {}),
         "which the table refuses"),
        ("R2 an entry nothing reads", rule_no_dead(
            {"search": good["search"] | {"dead"}}, reads, set(), {}), "accept-and-ignore"),
        ("R2 an every-function key missing", rule_no_dead(
            good, reads, {"max_answer_chars"}, {}), "every-function"),
        ("R3 a documented param refused", rule_docs(
            good, {("search", "context_chars")}, {}), "documents 'context_chars'"),
        ("R4 an alias into nothing", rule_aliases(good, {"n": "nothing"}), "no function accepts"),
        ("R0 a spelling with no row", rule_coverage(good, {"search": "search", "x": None}),
         "resolves to no"),
        ("R0 a row with no function", rule_coverage(
            {"search": set(), "ghost": set()}, {"search": "search"}), "names no callable"),
    )
    problems, caught = [], []
    for what, found, needle in plants:
        if any(needle in f for f in found):
            caught.append(what)
        else:
            problems.append("%s: not reported (got %r)" % (what, found))
    suite.record("C", "each-rule-fires", problems,
                 detail=["caught: %s" % "; ".join(caught)])

    doc = (
        "## Tools\n### `search` -- find\n"
        "| Param | Type |\n|-|-|\n| `query` | string |\n"
        "* `offset` -- int\n"
        "```json\n{\"limit\": 3, // a comment \"x\" hex\n\"nested\": {\"inner\": 1}}\n```\n"
        "## Other\n| `notaparam` | x |\n"
        "Call: search_call(function=\"search\", params={\"target_id\": \"t\", \"env\": {\"K\": 1}})\n"
        "Aliases: q/qq→query\n"
    )
    got = doc_pairs(doc, {"search": "search"})
    want_pairs = {("search", "query"), ("search", "offset"), ("search", "limit"),
                  ("search", "nested"), ("search", "target_id"), ("search", "env"),
                  (None, "query")}
    suite.record("C", "doc-reader-shapes",
                 [] if got == want_pairs else
                 ["read %s, wanted %s" % (sorted(got, key=str), sorted(want_pairs, key=str))],
                 detail=["table, bullet, params block, call form, alias arrow; a nested",
                         "key and a table outside any function section are NOT read"])


# ---------------------------------------------------------------------------
# Group B -- what the dispatcher judges: the caller's keys, not its own
# ---------------------------------------------------------------------------

def group_judged_keys(suite):
    """mcp-jenkins ADDS a `job_path` when a project scope is configured
    (_apply_project_scope). A refusal that judged the params after that would
    refuse every call to a function taking no job_path -- get_queue_item, and
    the empty call, which is status -- on exactly the installs that set a
    project, which the smoke harness (no project) can never see. Driven
    in-process with the scope set; neither call reaches the network. The
    control proves the same path still refuses a key the caller DID send."""
    problems = []
    try:
        mod = H.load_module_from_path("param_contract_jenkins_scope",
                                      H.repo_path("Scripts", "mcp-jenkins.py"))
        saved = mod.JenkinsConfig.project
        mod.JenkinsConfig.project = "tf/scope"
        try:
            calls = (
                ("get_queue_item", {"function": "get_queue_item", "params": {"queue_url": ""}}),
                ("status (empty call)", {"params": {}}),
            )
            for label, arguments in calls:
                text = (mod.handle_jenkins_call(arguments) or {}).get("__raw_text__", "")
                if "Unknown params" in text:
                    problems.append("%s with a project scope was refused: %s" % (label, text[:160]))
            control = (mod.handle_jenkins_call(
                {"function": "get_queue_item", "params": {"queue_url": "", "queue_ulr": 1}})
                or {}).get("__raw_text__", "")
            if "Unknown params for 'get_queue_item': queue_ulr." not in control:
                problems.append("CONTROL: a caller key was not refused: %s" % control[:160])
        finally:
            mod.JenkinsConfig.project = saved
    except Exception as exc:  # noqa: BLE001 -- recorded as the finding
        problems.append("raised %s: %s" % (type(exc).__name__, exc))
    suite.record("B", "jenkins-project-scope-key-not-judged", problems,
                 detail=["with JENKINS_PROJECT set, the injected job_path is not the caller's"])


# ---------------------------------------------------------------------------
# Group D -- hygiene
# ---------------------------------------------------------------------------

def group_hygiene(suite, before, pyc_before):
    after = H.repo_tree()
    new = sorted(after - before)
    suite.record("D", "no-new-repo-paths",
                 [] if not new else ["this suite wrote into the repo tree: %s" % new[:12]])
    # A delta, not the absolute form: `tests/test_purity_lsp.py:_pyc_problems`
    # is where zero is asserted outright.
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after if pyc_before.get(p) != pyc_after[p])
    suite.record("D", "no-new-bytecode",
                 [] if not changed else ["this run wrote or touched bytecode: %s" % changed[:6]])


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="accepted-parameter tables held to what each handler "
                          "reads and what each host's documentation names",
                    opts=opts, mode="grouped")
    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    for row in HOSTS:
        check_host(suite, row)
    group_judged_keys(suite)
    group_controls(suite)
    group_hygiene(suite, before, pyc_before)
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
