---
name: verify
description: Validate that a file is FORMALLY well-formed (parses) for its format — JSON, Python, YAML, TOML, XML, INI/.cfg, CSV/TSV, plist. Use when asked "is this valid JSON/YAML/Python", to check a config/data file or script before committing or deploying, to verify a file you just wrote or generated, or as a pre-commit/CI format gate. Bundles a Python 3.9 stdlib-first validator at scripts/validate.py for shell and CI use; inside a Claude session prefer the pre-approved MCP tool inspect_call (function validate/json/python/yaml/...), which costs no permission prompt. This is FORMAT/structural validation (does it parse), NOT schema validation (does it match an expected shape).
---

# Verify — structural/format validation for data files and Python sources

Answer one question per file: **is this formally well-formed for its format?** —
i.e. does it parse cleanly. This is *not* schema or semantic validation: it does
not check that a JSON has the right keys, that a YAML matches an expected shape,
or that values are sensible — only that the bytes are a valid document of that
format. For schema checks, see *When NOT to use* below.

## Quick start — if you are an agent with MCP access, THIS is the way

All nine formats are exposed by the pre-approved `mcp-inspect` server: no
permission prompt, a Markdown table back, and a batch of files in one call.

```
inspect_call {function: "validate", params: {path: "settings.json"}}          # auto-detect
inspect_call {function: "python",   params: {path: "hooks/guard.py"}}
inspect_call {function: "validate", params: {paths: ["a.json", "b.yaml"]}}    # batch: ONE call
inspect_call {function: "json",     params: {content: "{\"a\":1}"}}           # inline, no file
```

Reach for Bash **only** when MCP is genuinely unavailable. Never validate by
shelling out to `python3 -c "import ast; ast.parse(...)"`, `python3 -m py_compile`,
`python3 -m json.tool`, `jq .` or `xmllint --noout` — the first two are weaker or
dirtier than what this skill does (see the Python row below), and all of them cost
a prompt. See the `p:mcp-inspect` skill for the full function reference.

## Quick start — shell and CI

The bundled script covers the same nine formats for pre-commit hooks, `make`
targets and humans at a terminal:

```bash
python ~/.claude/skills/p/skills/verify/scripts/validate.py path/to/file.json
python ~/.claude/skills/p/skills/verify/scripts/validate.py a.json b.yaml hook.py   # batch
echo '{"a":1}' | python ~/.claude/skills/p/skills/verify/scripts/validate.py --format json -
```

Format is auto-detected from the extension; override with `--format`. Use `-` as
a path to read stdin (then `--format` is required). One line of output per file;
a one-line summary goes to stderr.

**Exit code** — `0` when nothing FAILED, `1` if any file FAILED. With `--strict`,
LIMITED and SKIPPED also make the exit non-zero. This makes the tool usable as a
pre-commit / CI gate.

## How to use it

1. **Prefer the bundled script over eyeballing.** It is deterministic, reports
   the exact `line:col` of the first error, and is safe on untrusted XML. Run it
   on every config/data file you write, generate, or are asked to check.
2. **Batch in one call.** Pass every file at once — you get one line each plus a
   summary; no need for a loop.
3. **Validating content you hold in the conversation** (not yet on disk): pipe it
   in with `--format`, e.g. `printf '%s' "$YAML" | validate.py --format yaml -`.
4. **Read the status, not just the exit code**, when YAML/TOML is involved — a
   `LIMITED` YAML result is a *pre-check*, not a guarantee (see below).

## What the script reports

| Status    | Meaning |
|-----------|---------|
| `OK`      | Parses cleanly — formally valid for its format. |
| `FAIL`    | Does not parse. Message + `line:col` of the first error. |
| `LIMITED` | YAML checked by the stdlib pre-check only (no PyYAML). Either a real defect was found (with a line) or only the conservative checks passed — **not a full parse.** |
| `SKIP`    | Could not validate: unknown extension (pass `--format`), or no TOML parser present. |

## Coverage with the Python 3.9 standard library ONLY

| Format     | stdlib module        | Completeness |
|------------|----------------------|--------------|
| JSON       | `json`               | **Full** parse, with `line:col` on error. |
| Python     | `compile()`          | **Syntax + symtable**, compiled **in memory**. Two deliberate choices: it is *stronger* than `ast.parse` (the codegen pass also rejects `break` outside a loop, `return` outside a function, module-level `nonlocal`), and it never writes anything — `python3 -m py_compile` would leave a `__pycache__/*.pyc` behind. `SyntaxWarning`s (invalid escape sequence, `assert (x, y)`, `is` with a literal) are reported alongside an `OK`. Grammar is that of the *running* interpreter, which the message states. |
| XML        | `xml.parsers.expat`  | **Well-formedness**, with a DTD/entity guard (blocks billion-laughs internal-entity expansion and external-entity XXE; a plain DOCTYPE is accepted). Not DTD/XSD *schema* validation. |
| INI / .cfg | `configparser`       | **Full** parse of the INI flavour `configparser` accepts (sections, `key = value`, duplicate-key detection). Not every `.ini` dialect. |
| CSV / TSV  | `csv`                | **Structural**: every row has the same column count. CSV is delimiter-flexible, so "parses" mostly means consistent shape. |
| plist      | `plistlib`           | **Full** parse — both binary and XML plist. |
| TOML       | `tomllib` (3.11+) or `tomli` | **Full** parse when a parser is importable; otherwise **SKIPPED** — there is no TOML parser in the 3.9/3.10 stdlib (`tomllib` landed in 3.11). `pip install tomli` to validate TOML on older interpreters. |
| YAML       | PyYAML if importable | **Full** parse via `yaml.safe_load_all` when PyYAML is present. **PyYAML is NOT stdlib.** Without it, the script falls back to a conservative stdlib **pre-check** (see below). |

### The YAML caveat — read this

There is **no YAML parser in the Python standard library** (any version). So:

- **PyYAML present** → full validation (`OK` / `FAIL` with line/col). Best case.
- **PyYAML absent** → `LIMITED`: a stdlib structural pre-check that catches the
  common, high-confidence formal errors **without ever flagging valid YAML**:
  - non-UTF-8 input,
  - **tab characters in indentation** (YAML forbids them) — the single most
    common stdlib-detectable YAML mistake,
  - **unbalanced flow collections** (`[ ]`, `{ }`) — quote- and comment-aware,
    and automatically skipped when block scalars (`|`, `>`) are present to avoid
    false positives.

  A `LIMITED` "pre-check passed" is **not** a guarantee the YAML parses — it only
  means none of those specific defects were found. For a real verdict, install
  PyYAML (`pip install pyyaml`) and re-run. Use `--strict` if a `LIMITED` result
  must not pass a gate silently.

## Doing it by hand (the underlying methods)

If you cannot run the script, the equivalent stdlib one-liners — the same checks
the script performs — are:

```python
import json;        json.loads(text)                       # JSON
compile(text, "<validate>", "exec", dont_inherit=True)     # Python (NOT py_compile)
import plistlib;     plistlib.loads(data_bytes)             # plist (bytes)
import configparser; configparser.ConfigParser().read_string(text)   # INI
import csv, io;      list(csv.reader(io.StringIO(text)))    # CSV (+ check col counts)
import xml.parsers.expat as x; p = x.ParserCreate(); p.Parse(data_bytes, True)  # XML
# TOML (3.11+):  import tomllib; tomllib.loads(text)
# YAML (PyYAML):  import yaml;   list(yaml.safe_load_all(text))
```

A clean call = valid; the raised exception (`json.JSONDecodeError`,
`SyntaxError`, `expat.ExpatError`, `configparser.Error`,
`tomllib.TOMLDecodeError`, `yaml.YAMLError`, …) carries the position and message.
For XML on untrusted input, install entity-declaration / external-entity handlers
that raise, as the script does — bare `ET.fromstring` is vulnerable to
entity-expansion attacks. For Python, prefer `compile()` over
`python3 -m py_compile`: py_compile writes a `.pyc` into `__pycache__/`, and
`ast.parse` alone accepts code that cannot actually run.

## When NOT to use this skill

- **Schema / shape validation** ("does this JSON have the required fields", "does
  this match the OpenAPI/JSON-Schema spec") — out of scope. Use `jsonschema`,
  `pydantic`, an XSD validator, etc.
- **Linting / style** (key ordering, indentation style, formatting) — use a
  formatter/linter (`prettier`, `yamllint`, `ruff`, …).
- **Semantic correctness** (do the values make sense for the app) — that needs
  domain logic, not a parser.

## Notes

- `--list-formats` prints the supported formats.
- The script is dependency-free on JSON/Python/XML/INI/CSV/plist. TOML needs a
  parser (stdlib on 3.11+); YAML is best with PyYAML.
- `.py` and `.pyi` auto-detect as Python; force it elsewhere with
  `--format python`.
- Tabs, not spaces, in `scripts/validate.py` (matches the repo's helper-script
  convention).
