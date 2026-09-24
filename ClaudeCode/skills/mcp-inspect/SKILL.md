---
name: mcp-inspect
description: >-
  NEVER use Bash for read-only system inspection or for file validation. `ps`, `lsof`,
  `netstat`, `ss`, `df`, `du`, `free`, `env`, `stat`, `ifconfig`/`ip addr`, `pstree`,
  `ulimit`, `launchctl`/`systemctl`, `<tool> --version`, `shasum`/`md5sum` — and every
  "is this file valid" one-liner (`python3 -c "import ast; ast.parse(...)"`,
  `python3 -m py_compile`, `python3 -m json.tool`, `jq .`, `xmllint --noout`,
  `node --check`, `bash -n`) — are
  DEPRECATED as primary Bash commands. Use `inspect_call` instead: it is pre-approved
  (no permission prompt), read-only, shell=False, and returns structured Markdown.
  Full API reference for the mcp-inspect MCP server. The server exposes ONE tool in
  tools/list: `inspect_call` (universal dispatcher). All 34 functions are invoked
  through it. Called with no `function`, it returns server status + the live function
  list. Covers system/process/network inspection, file metadata, file digests, and
  FORMAL well-formedness validation of json, python, yaml, toml, xml, ini, csv, tsv,
  plist, javascript and bash/sh shell scripts.
triggers:
  - mcp-inspect
  - inspect_call
  - list processes
  - what is listening on port
  - open files
  - disk usage
  - free memory
  - environment variable
  - process tree
  - file metadata
  - checksum
  - sha256
  - is this valid JSON
  - is this valid YAML
  - validate the file
  - check the syntax
  - does this parse
  - ast.parse
  - py_compile
  - json.tool
  - xmllint
  - node --check
  - is this valid JavaScript
  - bash -n
  - is this valid shell script
---

# mcp-inspect — read-only inspection + format validation

Two capability families behind **one** MCP tool:

1. **System inspection** — live process/network/disk/host state, file metadata, digests.
2. **Validation** — does a file *parse* for its format (11 formats, Python, JavaScript
   and Bash included).

Everything is READ-ONLY: no mutation, `shell=False`, fixed argv per function, no way
to pass a raw shell string, numeric params int-validated. There is no injection
surface and nothing on disk is ever written or touched. JavaScript and Bash are the
two formats validated by a subprocess (`node --check`, `bash -n`), and both of those
modes **parse only** — never `node -e`/`-p`, never a require/import, never `bash -c`
or a plain `bash <file>` — so validating a script cannot run it.

## The rule

If you are about to type any of these as the **primary** Bash command, STOP and call
`inspect_call` instead. A PreToolUse guard already denies most of them.

| Instead of Bash | Call |
|---|---|
| `ps aux`, `ps -ef` | `inspect_call {function:"processes"}` |
| `ps -p 123` | `inspect_call {function:"process", params:{pid:123}}` |
| `lsof -i -P -n \| grep LISTEN`, `netstat -an`, `ss -tlnp` | `inspect_call {function:"ports"}` |
| `lsof -p 123`, `lsof /path` | `inspect_call {function:"open_files", params:{pid:123}}` |
| `df -h` | `inspect_call {function:"disk"}` |
| `du -sh dir`, `du -d1` | `inspect_call {function:"disk_usage", params:{path:"dir"}}` |
| `free -h`, `vm_stat` | `inspect_call {function:"memory"}` |
| `uname -a`, `sw_vers`, `uptime` | `inspect_call {function:"host"}` |
| `env`, `printenv`, `echo $FOO` | `inspect_call {function:"env", params:{key:"FOO"}}` |
| `stat file`, `ls -l file` (metadata) | `inspect_call {function:"stat", params:{path:"file"}}` |
| `ifconfig -a`, `ip addr` | `inspect_call {function:"interfaces"}` |
| `netstat -rn`, `ip route` | `inspect_call {function:"route"}` |
| `pstree` | `inspect_call {function:"pstree"}` |
| `ulimit -a` | `inspect_call {function:"limits"}` |
| `launchctl list`, `systemctl list-units` | `inspect_call {function:"services"}` |
| `which foo`, `command -v foo` | `inspect_call {function:"which", params:{name:"foo"}}` |
| `foo --version` | `inspect_call {function:"versions", params:{tools:"foo"}}` |
| `shasum -a 256 f`, `md5 f`, `md5sum f` | `inspect_call {function:"sha256", params:{path:"f"}}` |
| `python3 -c "import ast; ast.parse(open('f').read())"` | `inspect_call {function:"python", params:{path:"f"}}` |
| `python3 -m py_compile f.py` | `inspect_call {function:"python", params:{path:"f.py"}}` |
| `python3 -m json.tool f.json`, `jq . f.json` | `inspect_call {function:"json", params:{path:"f.json"}}` |
| `xmllint --noout f.xml` | `inspect_call {function:"xml", params:{path:"f.xml"}}` |
| `node --check f.js` | `inspect_call {function:"javascript", params:{path:"f.js"}}` |
| `bash -n f.sh`, `sh -n f.sh` | `inspect_call {function:"bash", params:{path:"f.sh"}}` |
| "is this YAML valid?" | `inspect_call {function:"yaml", params:{path:"f.yaml"}}` |

**Still fine in Bash:** piping a stream into a filter (`python3 x.py | grep foo`) — the
guard only checks the *primary* command, and this server does not replace stream
filtering. Also fine: anything that MUTATES (that is out of scope here by design).

## How to call

```
mcp__mcp-inspect__inspect_call(function="<name>", params={...})
```

- `function` (alias `f`) — a canonical name or any alias below.
- `params` (alias `p`) — an object. May also be a JSON-encoded object string.
- **No `function` at all** → server status: platform, hostname, which underlying
  binaries exist, which optional validation parsers exist, and the full function list.
  Do this first when unsure — it is self-describing and free.
- `max_answer_chars` (default `100000`) is accepted on **every** function; output past
  it is truncated with an explicit note.

Errors come back as `isError: true` with a one-line explanation (bad params, unknown
function, missing binary). Unknown/typo'd params are **refused**, not ignored: each
function has an accepted-params table, checked after the function alias is resolved and
before anything runs, so `process {pdi: 123}` answers
`Unknown params for 'process': pdi. Accepted: max_answer_chars, pid.` The pinned format
validators (`json`, `python`, … `bash`) refuse `format`/`fmt` instead of letting the pinned
format override it — use `validate` to choose a format.

## Function index (34)

**Processes / network**
`processes` (ps, proc, procs) · `process` · `ports` (netstat, ss, listening, port) ·
`connections` (conn, connection) · `open_files` (lsof, openfiles, fds) ·
`pstree` (tree, ptree, processtree)

**Host / resources**
`host` (uname, sysinfo, system) · `memory` (mem, free, vm_stat, vmstat) ·
`disk` (df, filesystem, fs) · `disk_usage` (du, usage) · `mounts` (mount) ·
`limits` (ulimit, rlimit, rlimits) · `services` (service, launchctl, systemctl, units,
daemons) · `interfaces` (ifconfig, ip, interface, nics, addr) ·
`route` (routes, routing, routetable) · `env` (environment, printenv) ·
`which` · `versions` (version, toolchain, tools)

**Files**
`stat` (file_info, fileinfo, metadata) · `hash` (checksum, digest) ·
`sha256` (sha256sum, shasum, sha) · `md5` (md5sum)

**Validation**
`validate` (lint, check, verify, syntax, parse, wellformed) · `json` (jsonlint) ·
`python` (py, ast, py_compile, pycompile, python3) · `yaml` (yml) · `toml` ·
`xml` (xmllint) · `ini` · `csv` · `tsv` · `plist` (plutil) ·
`javascript` (js, mjs, cjs, node, nodejs) · `bash` (sh, shell)

---

# Validation family

**FORMAL well-formedness only** — "does it parse". NOT schema validation (right keys,
expected shape) and NOT linting/style. For those, use a schema validator or a linter.

## Input shapes — the same three for every validation function

```
params:{path: "settings.json"}                      # one file
params:{paths: ["a.json", "b.py", "c.yaml"]}        # batch, one table, one verdict
params:{content: "{\"a\":1}", format: "json"}       # inline text, no file needed
```

- `path` (alias `file`) and `paths` **together** → hard error (nothing is silently
  dropped). `content` together with either → hard error.
- `content` **requires** `format` on `validate` — there is no filename to detect from.
  A pinned function (`json`, `yaml`, …) already has its format and **refuses** a
  `format`/`fmt` param, so there it is `params:{content: "..."}` alone.
- With `validate`, `format` is optional and auto-detected from the extension:
  `.json` · `.yaml`/`.yml` · `.toml` · `.xml`/`.svg`/`.xsd`/`.rss` · `.plist` ·
  `.ini`/`.cfg` · `.csv` · `.tsv` · `.py`/`.pyi` · `.js`/`.mjs`/`.cjs` ·
  `.sh`/`.bash`. An unknown
  extension yields `SKIP` — pass `format` explicitly to force it (that is how you
  validate a `.txt` holding JSON, or a `.jsonc`-style file you know is plain JSON).
  **`.jsx`/`.ts`/`.tsx`/`.mts`/`.cts` are deliberately NOT mapped**: `node --check`
  parses neither JSX nor TypeScript, so they `SKIP` rather than report a bogus `FAIL`.
  Forcing `format:"javascript"` on one of them will fail on the first type
  annotation or tag — that is the tool's limit, not a defect in the file.
  **`.zsh`/`.fish`/`.ksh` are NOT mapped either**, for the same reason: bash would
  report another shell's dialect as a bogus `FAIL`. An extension-less script — the
  usual shape of a tool on `PATH` — has nothing to infer from, so pass
  `format:"bash"` there.
- The 11 format-named functions are thin wrappers that pin `format`; everything else is
  identical. Use them when you already know the format, `validate` when you do not.

**Optional params:** `format`, `strict` (bool, default false), `max_mb` (default 32;
`0` = no cap).

## Output

A Markdown table — `status  format  at  target  detail` — where `at` is `line:col` of
the first error, plus one overall verdict line and a count summary.

| Status | Meaning |
|---|---|
| `OK` | Parses cleanly. |
| `FAIL` | Does not parse (or cannot be read) — **or JavaScript/Bash was asked for and `node`/`bash` is not installed**. `at` carries the position (`bash -n` reports a line, never a column). |
| `LIMITED` | Only a partial check ran because a parser is absent (YAML without PyYAML), or `node --check`/`bash -n` timed out. **Not a guarantee.** |
| `SKIP` | Not validated: unknown extension, a directory, over `max_mb`, or no TOML parser. |

**Missing PyYAML/tomllib degrades to `LIMITED`/`SKIP`; a missing `node` or `bash`
FAILs.** That asymmetry is deliberate: those two are optional *parsers* and their
absence is a property of this host's Python, while a `SKIP`ped `.js`/`.sh` would leave
the batch verdict at `**PASSED**` over a file nobody ever checked.

| Verdict | When |
|---|---|
| `**PASSED**` | no `FAIL` rows |
| `**FAILED**` | any `FAIL` row |
| `**NOT VERIFIED (strict)**` | no `FAIL`, but `strict:true` and some row was `LIMITED`/`SKIP` |

Use `strict:true` whenever a `LIMITED`/`SKIP` must not quietly read as success.

## Per-format specifics

| Function | Parser | Notes |
|---|---|---|
| `json` | `json` (stdlib) | Full parse, `line:col` on error. BOM tolerated. No duplicate-key detection (`json` keeps the last). |
| `python` | `compile()` **in memory** | Two deliberate choices. (1) Stronger than `ast.parse`: the symtable/codegen pass also rejects `break`/`continue` outside a loop, `return`/`yield` outside a function, module-level `nonlocal` — code `ast.parse` happily accepts but Python cannot run. (2) Never `py_compile`, which writes `__pycache__/*.pyc` and would break this server's read-only contract. `SyntaxWarning`s (invalid escape sequence, `assert (x, y)`, `is` with a literal) are reported *alongside* `OK`, with a line number — real defects worth reading. The grammar is the **running interpreter's**, which the message names. |
| `yaml` | PyYAML `safe_load_all` | Validates **every document** in the stream and refuses arbitrary object construction. PyYAML is NOT stdlib: when absent you get `LIMITED` (UTF-8 + tab-in-indentation pre-check only). Check the status line, not just the verdict. |
| `toml` | `tomllib` (3.11+) or `tomli` | `SKIP` when neither is importable. |
| `xml` | `xml.parsers.expat` | Well-formedness. **All entity declarations are refused** — internal (billion-laughs) and external (XXE) — so it is safe on untrusted input; a plain `<!DOCTYPE note>` with no entities still passes. Not DTD/XSD schema validation. |
| `ini` | `configparser(strict=True)` | Full parse of the INI flavour configparser accepts, **including duplicate section/key detection**. Not every `.ini` dialect. |
| `csv` / `tsv` | `csv` | Parse **plus column-count consistency** against row 1; reports the offending row. Fixed delimiter (`,` / tab) — no dialect sniffing. Empty file is `OK`. |
| `plist` | `plistlib` | Full parse, binary and XML plist. |
| `javascript` | `node --check` (subprocess) | **Syntax only** — not type checking, not linting, and TypeScript/JSX are not JavaScript (see the extension note above). `--check` parses and stops: the file is never executed. With a `path`, node picks script-vs-module itself — extension, nearest `package.json` `"type"`, plus its own module-syntax detection on Node ≥22 — so ESM passes in a `.mjs`, in a `"type":"module"` package, and (on a recent node) in a plain `.js`. With `content` there is no extension, so it is checked as an ES module and, failing that, as CommonJS — valid under either goal is `OK`. **`FAIL` when `node` is not in PATH** (not `SKIP` — see the status table). |

| `bash` | `bash -n` (subprocess) | **Syntax only** — not shellcheck, not a style or portability review. `-n` is bash's own read-but-do-not-execute mode: it parses and stops, so nothing in the script runs (measured: with `BASH_ENV` set, a plain `bash <file>` sources it and `bash -n <file>` does not). Reports a **line, never a column**, and the FIRST diagnostic — bash likes to add a second line re-printing the offending source, and an unterminated construct is also reported at end-of-file by older bashes. `sh` and `shell` are the same validator under a different name, which is honest in one direction only: a `sh` script is parsed by **bash**, whose grammar is a superset, so a bashism (`[[ ... ]]`) passes here and would still die under `dash` — a missed defect, never an invented one. **`FAIL` when `bash` is not in PATH** (not `SKIP` — see the status table). |

Call `inspect_call` with no `function` to see whether PyYAML, tomllib/tomli, `node` and
`bash` are actually present on this host before trusting a YAML/TOML/JavaScript/Bash
verdict.

## Examples

```
# after writing a config, before trusting it
inspect_call {function:"json", params:{path:".claude/settings.local.json"}}

# a hook you just edited — syntax + SyntaxWarnings, writes no .pyc
inspect_call {function:"python", params:{path:"ClaudeCode/hooks/mcp-first-guard.py"}}

# a .js/.mjs/.cjs you just wrote — `node --check` parses it, never runs it
inspect_call {function:"javascript", params:{path:"web/app.mjs"}}

# a shell script — `bash -n` parses it, never runs it (`sh`/`shell` name the same one)
inspect_call {function:"bash", params:{path:"ClaudeCode/hooks/post-edit-lint.sh"}}

# everything you touched, in one call
inspect_call {function:"validate", params:{paths:["a.json","b.yaml","c.py"], strict:true}}

# text you have not written to disk yet
inspect_call {function:"validate", params:{content:"a: [1, 2\n", format:"yaml"}}
```

---

# System inspection family

Cross-platform (macOS + Linux); the command per function is chosen per platform and a
missing binary yields a clear error, never a crash.

### processes
`filter` (alias `name`, substring match on the full command line), `user` (exact),
`sort` = `cpu`|`mem`|`pid` (default `cpu`), `limit` (default 30, `0` = all),
`timeout` (default 15). Header shows how many of the total matched.

### process
`pid` **[required]**. One-process detail (ppid, pgid, user, cpu, mem, rss, vsz, state,
nice, elapsed, command) plus an open-FD count when `lsof` exists.

### ports
`proto` = `tcp`|`udp`|`all` (default `all`), `timeout` (default 15). Prefers `lsof`,
then `ss` (Linux), then `netstat`.

### connections
`state` = `established`|`all` (default `established`), `timeout` (default 15).

### open_files
`pid`, `port`, `user`, `path`, `limit` (default 200), `timeout` (default 20).
**Requires `lsof`.** With no filter the output is huge and says so — always narrow it.

### pstree
`pid` (subtree root; default = every root), `depth` (default 0 = unlimited),
`limit` (default 200 rows). The tree is built in Python from `ps`, so no `pstree`
binary is needed; loops in the ps snapshot are guarded.

### host
No params. hostname, platform, `uname -a`, macOS `sw_vers` / Linux `/etc/os-release`,
cpu count, loadavg, uptime.

### memory
No params. macOS: `hw.memsize` + `vm_stat`. Linux: `free -h`, falling back to
`/proc/meminfo`.

### disk
`path` (optional — restrict to the filesystem holding it). `df -kP` with sizes
rendered human.

### disk_usage
`path` **[required]**, `depth` (default 1), `top` (default 20 largest),
`timeout` (default 30). Unreadable subpaths are skipped with a note. A nonexistent
path is an error.

### mounts
No params. `mount`, falling back to `/proc/mounts`.

### limits
`pid` — **Linux only** (`/proc/<pid>/limits`). macOS has no per-process rlimits and
says so explicitly, then shows this server's own limits. Those are inherited from
whatever launched the server and are **not** the Bash tool's shell limits; the output
states this. macOS also adds `launchctl limit`, Linux adds `fs.file-max`.

### services
`filter` (alias `name`), `user` (bool → `systemctl --user`), `limit` (default 100).
macOS `launchctl list`, Linux `systemctl list-units --type=service --all`.

### interfaces
`filter` (alias `name`). Linux `ip -o addr` (line-filtered), otherwise `ifconfig -a`
(**stanza**-filtered: whole per-interface blocks). Adds the kernel's interface list.

### route
No params. Linux `ip route`, otherwise `netstat -rn`.

### env
`key` (one variable), `filter` (substring on the NAME), `show_secrets` (default false).
Values whose **name** looks secret (secret/token/passwd/password/api_key/access_key/
auth/credential/private_key/session/cookie/bearer/client_secret) are replaced with
`***REDACTED***` plus the length. `show_secrets:true` reveals them — think before you
do that, the value lands in the transcript.

### which
`name` (aliases `cmd`, `command`) **[required]**, a string or a list. Reports the PATH
hit and, when it differs, the realpath behind a symlink.

### versions
`tools` (aliases `tool`, `name`) — a list, or a comma/space separated string. With no
params it probes every installed allow-listed tool. **Allow-listed by design** (43
tools: interpreters, package managers, compilers, clangd/clang-tidy/nvcc, cmake/ninja/
make, git/gh, docker/psql/sqlite3/jq, ffmpeg/tshark/lldb, java/ruby/perl, shells,
swift). The caller may only NAME a tool — argv is fixed per tool, so this can never
become arbitrary command execution. An unknown name is a hard error listing what is
allowed.

### stat
`path` (alias `file`) **[required]**. Uses `lstat`, so it describes a **symlink
itself**: type, `filemode` + octal, owner `user:group` with numeric uid/gid, size,
link count, inode + device, mtime/atime/ctime, the link target and its realpath with an
explicit `BROKEN` marker when it dangles, entry counts for a directory, and the access
bits **for the server process** (not for you).

### hash / sha256 / md5
`path` (alias `file`) or `paths` (a list) **[required]**, `expect` (single path only →
`MATCH`/`MISMATCH` verdict), `max_mb` (default 2048, per file). `hash` also takes `algo`
(alias `algorithm`): `sha256` (default), `sha512`, `sha384`, `sha224`, `sha1`, `md5`,
`blake2b`, `blake2s`. `algo` and `algorithm` that disagree are refused, and so is either
one disagreeing with a fixed wrapper (`md5` + `algorithm:"sha256"`). The reply has no
header row, so the algorithm that ran is named on an `algo: <name>` line under the table.
Computed with `hashlib` in 1 MB chunks — identical digests on every platform, unlike
`shasum` vs `md5sum` vs `md5 -q`.

`recursive` (bool, default false) expands a directory — in `path` or any element of
`paths` — into one row per **regular** file beneath it, full path, sorted, subdirectories
descended. Symlinked files and symlinked directories are **not** followed (a count line
says how many were passed over). `max_files` (default 1000, `0` = no cap) bounds the files
an expansion adds, **shared across the whole call**; hitting it stops the walk and
appends `truncated at N files` — never a silent cut. `expect` with a directory expansion
is refused. Without `recursive`, a directory is a row saying to pass `recursive:true`.
Unreadable files and subdirectories become a note in their row instead of failing the
batch. **Gotcha:** with `expect`, a row that was skipped (directory/error) leaves the
verdict unset and prints `MISMATCH` — read the row, not just the verdict.

## Gotchas worth remembering

- **Output tables are whitespace-aligned, not pipe-delimited.** Never split on `|` when
  parsing this server's output.
- **`0` usually means "no cap"** for `limit`/`top`/`max_mb`, not "nothing".
- **Unknown params are refused** with the function's accepted list (see *How to call*) —
  read the list rather than retrying spellings.
- **The server's view is the server's**: `stat` access bits, `limits`, and `env` reflect
  the MCP server process, not the Bash shell you would have used. `env` in particular
  shows the environment the server was launched with.
- **Relative paths** resolve against the server's working directory, not a project
  fence — this server has no root sandbox. Prefer absolute paths when it matters.
  `~` is expanded.

## When NOT to use this skill

- **Anything that mutates** — writing, deleting, killing, mounting, installing. Not
  here, by contract.
- **Reading file *contents*** → the built-in `Read` tool (`offset`/`limit`), not
  `stat`/`validate`.
- **Searching contents or listing directories** → `purity_call`
  (`search_for_pattern`, `find_file`, `list_dir`).
- **Schema validation, linting, formatting** → a schema validator or linter; this
  server only answers "does it parse".
- **Build/test/clean** → `forge_call`. **Git** → `git_call`. **Debugging a process** →
  `lldb_call`.
