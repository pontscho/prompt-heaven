---
name: p:mcp-forge
description: >
  MANDATORY when `project-forge.yaml` exists in the project root. ALL build,
  test, and clean operations MUST go through the `forge_call` MCP tool
  (mcp__mcp-forge__forge_call) instead of Bash. This skill teaches you how to
  AUTHOR and EDIT `project-forge.yaml` (a custom minimal YAML subset) and how
  to INVOKE `forge_call` correctly. Using Bash for make/cmake/npm test/ctest/
  cargo build/rm -rf build when project-forge.yaml exists is a violation.
triggers:
  - forge
  - mcp-forge
  - forge_call
  - project-forge.yaml
  - build a target
  - run a test
  - clean build artifacts
  - add build pipeline
  - add test pipeline
  - make
  - cmake build
  - npm run build
  - npm test
  - cargo build
  - cargo test
  - pytest
  - ctest
---

# p:forge — Build and Test Orchestration

`mcp-forge` is an MCP server that reads `project-forge.yaml` from the project
root and exposes one tool: **`forge_call`** (`mcp__mcp-forge__forge_call`).
The YAML descriptor defines all build, test, and clean pipelines for the
project. Forge runs them with output filtering, env variable management, and
auto-build of test prerequisites.

## When to use this skill

**MANDATORY** — if `project-forge.yaml` exists in the project root, you MUST
route every build/test/clean command through `forge_call`. Bash for these
operations is forbidden.

**MANDATORY mapping table:**

| You would normally write | Use instead |
|---|---|
| `Bash("make ...")` | `forge_call(function="build", params={"targets":["..."]})` |
| `Bash("cmake --build ...")` | `forge_call(function="build", ...)` |
| `Bash("ninja ...")` | `forge_call(function="build", ...)` |
| `Bash("npm run build")` | `forge_call(function="build", ...)` |
| `Bash("cargo build")` | `forge_call(function="build", ...)` |
| `Bash("npm test")` | `forge_call(function="test", params={"targets":["..."]})` |
| `Bash("pytest ...")` | `forge_call(function="test", ...)` |
| `Bash("ctest ...")` | `forge_call(function="test", ...)` |
| `Bash("cargo test")` | `forge_call(function="test", ...)` |
| `Bash("./run-tests.sh")` | `forge_call(function="test", ...)` |
| `Bash("rm -rf build")` | `forge_call(function="clean", params={"targets":["all"]})` |
| `Bash("make clean")` | `forge_call(function="clean", ...)` |

**When NOT to use forge:**
- `project-forge.yaml` does not exist in the project → use `mcp-compile` for
  builds or Bash for one-off commands.
- Truly ad-hoc one-off shell commands unrelated to build/test → Bash is fine.
- File search/edit → `mcp-purity`. Git → `mcp-git`. C/C++ symbol nav →
  `mcp-clangd`. Lua symbol nav → `mcp-luals`.

## Quick Start

**Check what is available** (call with no `function`):
```
mcp__mcp-forge__forge_call(function="", params={})
```
Returns server status, config path, target counts, validation summary.

**List all targets:**
```
mcp__mcp-forge__forge_call(function="list")
```

**Run a build:**
```
mcp__mcp-forge__forge_call(
  function="build",
  params={"targets": ["app"]}
)
```

**Run a test (auto-builds requires first):**
```
mcp__mcp-forge__forge_call(
  function="test",
  params={"targets": ["unit"], "env": {"JEST_FILTER": "rtmp"}}
)
```

## Calling Convention

**Two-level dispatch** — ALL arguments go inside `params`:

```
forge_call(function="<name>", params={...all args here...})
```

- Top-level keys allowed: `function` (alias `f`), `params` (alias `p`).
- Nothing else at the top level.
- WRONG: `forge_call(targets=["app"])`
- RIGHT: `forge_call(function="build", params={"targets":["app"]})`

## Function Reference

### Status (empty `function`)
Returns server info, config path, target counts, validation issues.
```json
{"function": ""}
```

### `list` — Enumerate targets
| Param | Type | Default | Description |
|---|---|---|---|
| `kind` | string | `"all"` | One of `all`, `build`, `test`, `clean` |

Alias: `k`, `type`.
```json
{"function":"list", "params":{"kind":"test"}}
```

### `describe` — Show target details
| Param | Type | Required | Description |
|---|---|---|---|
| `target` | string | yes | Target name (matched across all sections) |

Alias: `name`.
```json
{"function":"describe", "params":{"target":"unit"}}
```

### `validate` — Validate the YAML config
| Param | Type | Default | Description |
|---|---|---|---|
| `path` | string | server's `--config` | Path to a forge YAML to validate |

Alias: `file`. **ALWAYS** call `validate` after editing `project-forge.yaml`.
```json
{"function":"validate"}
```

### `build` — Run build target(s)
| Param | Type | Required | Description |
|---|---|---|---|
| `targets` | string \| list | yes (or use `target`) | One or many build target names |
| `target` | string | yes (alternative) | Single target convenience |
| `env` | object | no | env var overrides `{KEY: "value"}` |
| `filter` | object | no | `{grep, grep_context, invert_grep, head, tail}` |
| `ncpu` | int | no | Override `configuration.settings.ncpu` |
| `timeout` | int | no | Override per-target/global timeout (seconds) |
| `cwd` | string | no | Override working directory |

Aliases: `t→targets`, `e→env`, `f→filter`, `j→ncpu`, `timeout_sec→timeout`.
```json
{"function":"build", "params":{"targets":["app","deps"],"ncpu":8}}
```

### `test` — Run test target(s)
Same params as `build`, plus:
| Param | Type | Default | Description |
|---|---|---|---|
| `auto_build` | bool | `true` | If true, builds each test's `requires` before running |

Alias: `ab→auto_build`. Auto-build is your friend — leave it on unless you
are absolutely certain the build is fresh.
```json
{"function":"test", "params":{"targets":["unit"],"auto_build":true}}
```

### `clean` — Run clean target(s)
| Param | Type | Required | Description |
|---|---|---|---|
| `targets` | string \| list | yes | Clean target names |
| `filter` | object | no | Same filter shape |

```json
{"function":"clean", "params":{"targets":["all"]}}
```

## Filter sub-object

Applied as: `grep` → `head`/`tail`.

| Field | Type | Description |
|---|---|---|
| `grep` | string | Regex pattern (case-insensitive). Alias: `pattern`, `regex`. |
| `grep_context` | int | Lines of context. Alias: `context`. |
| `invert_grep` | bool | Invert match. Alias: `invert`. |
| `head` | int | Keep first N filtered lines, show `... (X more lines) ...`. |
| `tail` | int | Keep last N filtered lines. |

Filters in the YAML target definition act as defaults; call-level filter
overrides them per-key.

## YAML Schema Reference

### Top-level structure
```yaml
version: 1                           # required; currently only 1 is understood

configuration:                       # optional
  settings: {...}
  env: {...}

build:                               # optional, but you usually have it
  <target_name>: {...}
  ...

test:                                # optional
  <target_name>: {...}
  ...

clean:                               # optional
  <target_name>: {...}
  ...
```

Unknown top-level keys produce a validation warning.

### configuration
```yaml
configuration:
  settings:
    ncpu: 4                          # int, default = os.cpu_count()
    timeout: 600                     # int seconds, default 600
    cwd: .                           # default working dir (project-root-relative)
  env:
    NODE_ENV: "production"           # env vars applied to every command
    CMAKE_BUILD_PARALLEL_LEVEL: "${ncpu}"   # interpolation works here
```

### A build/clean target
```yaml
build:
  app:                               # target name (kebab-case OK)
    description: "..."               # optional; shown in list/describe
    commands:                        # required; list of shell strings
      - cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
      - make -C build -j${ncpu} app
    filter:                          # optional; default filter for this target
      grep: "error|warning"
    timeout: 900                     # optional; per-target timeout override
```

### A test target
Same as build, plus:
```yaml
test:
  unit:
    description: "Jest unit tests"
    requires: [app]                  # build targets to auto-build first
    commands:
      - npm test -- --testNamePattern="${env.JEST_FILTER}"
    env_schema:                      # documented env vars accepted by this test
      JEST_FILTER:
        description: "regex for test names"
        default: "."
      JEST_VERBOSE: "0|1"            # shorthand: just a description
    filter:
      grep: "PASS|FAIL|Tests:"
```

### Multi-line shell commands (`|` and `>`)

Use `|` (literal block) to keep newlines — useful for line-continuations:
```yaml
commands:
  - |
    cmake -S . -B build \
      -DCMAKE_BUILD_TYPE=Debug \
      -DENABLE_TESTS=ON
  - make -C build
```

Use `>` (folded block) to collapse newlines into spaces (rarer):
```yaml
description: >
  This is a long description that
  spans multiple lines but is read
  as one line with spaces.
```

The parser uses default chomping (strips trailing newline). Chomping
indicators (`|-`, `|+`, `>-`, `>+`) and explicit indents (`|2`, `>4`) are NOT
supported — keep it simple.

## Variable Interpolation

Variables are resolved at execution time, NOT during YAML parse.

| Variable | Source |
|---|---|
| `${ncpu}` | `params.ncpu` > `configuration.settings.ncpu` > `os.cpu_count()` |
| `${target}` | Name of the target currently running |
| `${cwd}` | `params.cwd` > `configuration.settings.cwd` > project root |
| `${env.NAME}` | Final merged env (see precedence below) |

**env precedence** (highest wins):
1. Call-level `params.env`
2. `configuration.env`
3. `env_schema.<KEY>.default` (test targets only)
4. Otherwise empty string + a warning

Unknown variable references become empty strings; the validator emits a
warning at validate time.

## env_schema (test targets only)

Document every env var a test consumes. Two forms accepted:

**Shorthand (description only):**
```yaml
env_schema:
  JEST_FILTER: "regex for test names"
```

**Full form:**
```yaml
env_schema:
  TEST_VERBOSE:
    description: "0|1 verbose output"
    default: "0"
    required: false
  API_KEY:
    description: "Auth token for integration tests"
    required: true
```

Keys MUST match `[A-Z_][A-Z0-9_]*`. A `required: true` var that is not set
from any source produces a runtime warning and the target may still run with
empty value — write your commands defensively.

Passing an env var that is NOT in `env_schema` at call time also produces a
warning but is forwarded to the subprocess. This is intentional — you can
still experiment, but please document new vars in the YAML afterwards.

## YAML Subset Rules

`project-forge.yaml` is parsed by a custom minimal YAML parser. Stick to this
subset; everything else is a parse error.

**Supported:**
- Block mappings (`key: value`, with nested indented children)
- Block sequences (`- item`) — items must be scalars (no inline mappings)
- Flow sequence `[a, b, c]` (scalars only)
- Flow mapping `{k: v, k: v}` (scalar → scalar)
- Block scalars `|` (literal) and `>` (folded), default chomping only
- Scalars: bare, double-quoted (`\n`, `\t`, `\\`, `\"` escapes),
  single-quoted (`''` for literal apostrophe), integer, lowercase boolean,
  `null` or empty for null
- Comments: `#` to end of line (outside quoted strings)
- Indent: 2 spaces, 4 spaces, OR single tab — pick one and be consistent

**NOT supported (will fail to parse):**
- Mixed tab/space indent in the same file
- Sequence items that are mappings (no `- key: value` style)
- Nested flow style (`[[a,b],[c,d]]` or `{k: {nested: v}}`)
- Multi-line scalar chomping/indent indicators (`|-`, `|+`, `>2`)
- Anchors (`&name`), aliases (`*name`)
- Document separators (`---`, `...`)
- Tags (`!!str`, `!type`)
- Merge keys (`<<:`)
- YAML 1.1 booleans (`yes`/`no`/`on`/`off`/`True`) — only `true`/`false`

If you need a list of structured objects, model it as a named mapping
instead. For example:

```yaml
# NOT supported (sequence of mappings):
items:
  - name: foo
    value: 42

# Use this instead:
items:
  foo: 42
  bar: 99
```

## Example Configs

### CMake + Make project
```yaml
version: 1

configuration:
  settings:
    ncpu: 4
    timeout: 600
  env:
    CMAKE_BUILD_PARALLEL_LEVEL: "${ncpu}"

build:
  configure:
    description: "Configure CMake build directory"
    commands:
      - cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
    filter:
      grep: "error|CMake Error"

  app:
    description: "Build the main application"
    commands:
      - cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
      - make -C build -j${ncpu} app
    filter:
      grep: "error|warning|undefined reference"

  tests:
    description: "Build all test binaries"
    commands:
      - cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
      - make -C build -j${ncpu} tests

test:
  unit:
    description: "All unit tests via ctest"
    requires: [tests]
    commands:
      - ctest --test-dir build --output-on-failure -R "${env.TEST_FILTER}"
    env_schema:
      TEST_FILTER:
        description: "ctest -R regex"
        default: "."
    filter:
      grep: "Passed|Failed|FAIL|tests passed"

clean:
  all:
    description: "Remove the entire build directory"
    commands:
      - rm -rf build
```

### npm / Node.js project
```yaml
version: 1

configuration:
  settings:
    ncpu: 4
    timeout: 600
  env:
    NODE_ENV: "production"

build:
  app:
    description: "Production bundle"
    commands:
      - npm ci
      - npm run build
    filter:
      grep: "error|ERROR|warning|failed"

  app-dev:
    description: "Development bundle (sourcemaps, no minification)"
    commands:
      - npm run build:dev

test:
  unit:
    description: "Jest unit tests"
    requires: [app]
    commands:
      - npm test -- --ci --testNamePattern="${env.JEST_FILTER}"
    env_schema:
      JEST_FILTER:
        description: "regex passed to --testNamePattern"
        default: "."
      JEST_VERBOSE: "0|1"
    filter:
      grep: "PASS|FAIL|Tests:|Snapshots:|\u25cf"

  e2e:
    description: "Playwright end-to-end tests"
    requires: [app]
    commands:
      - npx playwright test --grep="${env.PW_GREP}"
    env_schema:
      PW_GREP:
        description: "regex for test titles"
        default: "."
      HEADED: "0|1"
    filter:
      grep: "passed|failed|skipped|Error:"

clean:
  artifacts:
    description: "Built output and coverage"
    commands:
      - rm -rf dist coverage .next
  deps:
    description: "node_modules and lockfile reset"
    commands:
      - rm -rf node_modules
  all:
    commands:
      - rm -rf dist coverage .next node_modules
```

### Cargo (Rust) project
```yaml
version: 1

build:
  debug:
    description: "Debug build of all crates"
    commands:
      - cargo build
    filter:
      grep: "error|warning|^error\\["

  release:
    description: "Optimized release build"
    commands:
      - cargo build --release

test:
  unit:
    description: "All unit + integration tests"
    requires: [debug]
    commands:
      - cargo test -- --test-threads=${ncpu} ${env.TEST_FILTER}
    env_schema:
      TEST_FILTER:
        description: "test name filter"
        default: ""
    filter:
      grep: "test result|FAILED|panicked"

clean:
  all:
    commands:
      - cargo clean
```

### Python (pytest) project
```yaml
version: 1

build:
  deps:
    description: "Install editable + dev dependencies"
    commands:
      - pip install -e .[dev]

test:
  unit:
    description: "pytest unit tests"
    requires: [deps]
    commands:
      - pytest -k "${env.PYTEST_K}" -m "${env.PYTEST_M}" -x
    env_schema:
      PYTEST_K:
        description: "-k expression"
        default: ""
      PYTEST_M:
        description: "-m marker expression"
        default: ""
    filter:
      grep: "PASSED|FAILED|ERROR|====|---"

clean:
  artifacts:
    commands:
      - rm -rf .pytest_cache .coverage build dist *.egg-info
```

## Filter pattern cookbook

| Toolchain | Recommended `grep` regex |
|---|---|
| GCC/Clang | `error:\|warning:\|undefined reference\|ld:` |
| CMake | `CMake Error\|CMake Warning\|FAILED` |
| Make | `Error\|Warning\|recipe for target` |
| Jest | `PASS\|FAIL\|Tests:\|Snapshots:\|●` |
| Vitest | `PASS\|FAIL\|Test Files\|Tests` |
| Mocha | `passing\|failing\|pending` |
| pytest | `PASSED\|FAILED\|ERROR\|====\|---` |
| ctest | `Passed\|Failed\|FAIL\|tests passed` |
| Cargo build | `error\[E\|warning:\|^error` |
| Cargo test | `test result\|FAILED\|panicked` |
| Lua busted | `Failure →\|Error →\|successes\|failures` |
| Playwright | `passed\|failed\|skipped\|Error:` |

In YAML, escape `|` inside a quoted regex with `\\|` or wrap the regex in
single quotes to avoid interpretation issues.

## Authoring Workflow

When you create or edit `project-forge.yaml`:

1. **Write or edit** the YAML using the schema above.
2. **Always validate** immediately after saving:
   ```
   forge_call(function="validate")
   ```
   Fix every error and review every warning before continuing.
3. **Describe** any new target to verify it parsed the way you expected:
   ```
   forge_call(function="describe", params={"target":"<name>"})
   ```
4. **Dry-run** with a cheap command first if you are adding a complex
   pipeline (e.g. `echo ok` then swap to the real command).
5. **Document env vars** the test consumes in `env_schema` — undocumented
   env vars passed at call time only produce warnings and are easy to miss.
6. **Set a sensible `filter`** for each target. The output goes back to
   Claude as context; unfiltered build noise wastes tokens fast.

## Common errors and how to fix them

| Error message | Likely cause | Fix |
|---|---|---|
| `parse error at line N, col 1: indent width X is not a multiple of 2` | Inconsistent indent | Re-indent the file with consistent 2 or 4 spaces or tabs |
| `parse error: expected space indent, found tabs or mix` | Tabs and spaces mixed | Pick one (file-wide) and re-indent |
| `parse error: nested flow style is not supported` | `[[a,b],[c,d]]` or `{k:{nested:v}}` | Convert to block style |
| `target 'X' not found in 'build' (did you mean 'Y'?)` | Typo | Use the suggestion or `list` |
| `config has N validation error(s): ...requires references unknown build target` | Test `requires` a missing build target | Fix the target name or add the build target |
| `BLOCKED: command contains dangerous pattern` | Command matches the safety blocklist | Reword the command; the YAML is for build/test/clean, not destructive ops |
| `TIMEOUT (killed after Ns)` | Command exceeded its timeout | Bump the per-target `timeout:` or call-level `timeout` param |
| `target 'X' has no commands` | Missing `commands:` list | Add at least one command string |
| `_warning: env var 'X' passed but not declared in env_schema_` | Undocumented env at call time | Add `X:` to the test's `env_schema` |
| `_warning: ${X} references undeclared env var` | Interpolation uses an unknown var | Add to `env_schema` or `configuration.env`, or remove the reference |

## Anti-patterns — do NOT do these

- **Do not** call `Bash("make ...")` when `project-forge.yaml` is present.
  If the right target does not exist yet, ADD it to the YAML and call
  `forge_call`.
- **Do not** loop `forge_call(... build ...)` until something works — use
  `validate` and `describe` to understand the state first.
- **Do not** embed secrets in `configuration.env` — set them externally
  and reference via `${env.SECRET}` after declaring them in `env_schema`.
- **Do not** create one giant `build.everything` target that runs all
  sub-builds — split them so users can build just what they need.
- **Do not** invent YAML features that are not in the supported subset.
  If you really need anchors / merge keys / nested flow, you are
  abusing the format; restructure.

## Quick reference card

```
# status                    forge_call(function="")
# list targets              forge_call(function="list")
# describe target           forge_call(function="describe", params={"target":"X"})
# validate YAML             forge_call(function="validate")
# build                     forge_call(function="build", params={"targets":["X"]})
# test (auto-builds deps)   forge_call(function="test",  params={"targets":["X"]})
# test, skip auto-build     forge_call(function="test",  params={"targets":["X"],"auto_build":false})
# clean                     forge_call(function="clean", params={"targets":["all"]})
# build with env override   forge_call(function="build", params={"targets":["X"],"env":{"K":"V"}})
# build with filter         forge_call(function="build", params={"targets":["X"],"filter":{"grep":"error"}})
# build with head limit     forge_call(function="build", params={"targets":["X"],"filter":{"head":50}})
```
