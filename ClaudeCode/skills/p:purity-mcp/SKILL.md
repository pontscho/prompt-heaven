---
name: p:purity-mcp
description: >
  NEVER use the built-in `Search`, `Grep`, or `Edit` tools. They are deprecated for this project.
  ALWAYS use `purity_call` ONLY. If you attempt to use a built-in tool, I will consider it a failure.
  MANDATORY — mcp-purity file operations: create_text_file, list_dir, find_file, replace_content, delete_lines, replace_lines, insert_at_line, search_for_pattern. Use when writing, searching, listing, or editing files.
  Trigger:
    - Creating or editing files.
    - Listing directories, searching for files or patterns.
    - When the user mentions purity, mcp-purity, or purity_call.
    - GLOB_TOOL_NAME = mcp__mcp-purity__purity_call with function "find_file"
    - GREP_TOOL_NAME = mcp__mcp-purity__purity_call with function "search_for_pattern"
    - READ_TOOL_NAME = mcp__mcp-purity__purity_call with function "read_file"
---

## Overview

MCP-Purity is a file operations MCP server. It exposes a single tool `purity_call` that dispatches file handler functions via the `function` parameter. **MCP tool name**: `mcp__mcp-purity__purity_call`

## Quick Start

```json
{"f":"","p":{}}
```
Returns server status and list of available functions.

## Available Functions

### 1. `read_file` — Read a file

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`relative_path`|string|yes|—|Relative path to file|
|`start_line`|int|no|1|1-based first line index|
|`end_line`|int|no|null|1-based last line (inclusive); null = read to end|
|`max_answer_chars`|int|no|-1|Character limit; -1 = unlimited|

```json
{"f":"read_file","p":{"relative_path":"src/main.py","start_line":1,"end_line":50}}
```

### 2. `create_text_file` — Create or overwrite a file

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|Relative path to file|
|`content`|string|yes|Full content to write|

Creates parent directories automatically. **Destructive** — overwrites existing files.

```json
{"f":"create_text_file","p":{"relative_path":"src/utils.py","content":"def add(a, b):\n    return a + b\n"}}
```

### 3. `list_dir` — List directory contents

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`relative_path`|string|no|"."|Directory to list|
|`recursive`|bool|no|false|Scan subdirectories|
|`skip_ignored_files`|bool|no|false|Skip gitignored files|
|`max_answer_chars`|int|no|-1|Character limit|

```json
{"f":"list_dir","p":{"relative_path":"src","recursive":true,"skip_ignored_files":true}}
```

### 4. `find_file` — Find files by wildcard pattern

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`file_mask`|string|yes|—|Filename pattern with `*` or `?` wildcards|
|`relative_path`|string|no|"."|Directory subtree to search|

```json
{"f":"find_file","p":{"file_mask":"*.test.ts","relative_path":"src"}}
```

### 5. `replace_content` — Replace content in a file

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`relative_path`|string|yes|—|File to modify|
|`needle`|string|yes|—|String or regex pattern to find|
|`repl`|string|yes|—|Replacement string|
|`mode`|string|yes|—|`"literal"` or `"regex"`|
|`allow_multiple_occurrences`|bool|no|false|Allow multiple replacements|

Regex mode uses standard Python `re.sub()` backreferences: `\1`, `\2`, `\g<name>`.

```json
{"f":"replace_content","p":{"relative_path":"config.py","needle":"DEBUG = True","repl":"DEBUG = False","mode":"literal"}}
```

### 6. `delete_lines` — Delete a range of lines

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|File to modify|
|`start_line`|int|yes|1-based first line to delete|
|`end_line`|int|yes|1-based last line to delete (inclusive)|

```json
{"f":"delete_lines","p":{"relative_path":"src/app.py","start_line":10,"end_line":15}}
```

### 7. `replace_lines` — Replace a range of lines

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|File to modify|
|`start_line`|int|yes|1-based first line to replace|
|`end_line`|int|yes|1-based last line to replace (inclusive)|
|`content`|string|yes|New content to insert|

```json
{"f":"replace_lines","p":{"relative_path":"src/app.py","start_line":5,"end_line":7,"content":"    return new_value\n"}}
```

### 8. `insert_at_line` — Insert content at a line

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|File to modify|
|`line`|int|yes|1-based line index; new content is inserted before this line|
|`content`|string|yes|Content to insert|

Existing content at `line` shifts down. Does not replace.

```json
{"f":"insert_at_line","p":{"relative_path":"src/main.py","line":1,"content":"# Auto-generated\n"}}
```

### 9. `search_for_pattern` — Regex search across files

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`substring_pattern`|string|yes|—|Regex pattern to search for|
|`context_lines_before`|int|no|0|Context lines before match|
|`context_lines_after`|int|no|0|Context lines after match|
|`paths_include_glob`|string|no|""|Glob to include files|
|`paths_exclude_glob`|string|no|""|Glob to exclude files|
|`relative_path`|string|no|""|Restrict to subdirectory|
|`max_answer_chars`|int|no|-1|Character limit|

```json
{"f":"search_for_pattern","p":{"substring_pattern":"TODO|FIXME","context_lines_after":1,"paths_include_glob":"**/*.py"}}
```

## Parameter aliases

All handlers accept the aliases below as a convenience — callers can use
the shorter / more familiar form and the server folds them onto the
canonical names from the tables above before the handler runs. The
canonical name is what error messages reference.

### Global aliases (apply to every function)

| Alias | Canonical |
|-|-|
| `path`, `file_path`, `file`, `root` | `relative_path` |
| `pattern` | `substring_pattern` |
| `search`, `find`, `old_string`, `old` | `needle` |
| `replacement`, `replace`, `replace_with`, `new_string`, `new` | `repl` |
| `line_start`, `start` | `start_line` |
| `line_end`, `end` | `end_line` |
| `include` | `paths_include_glob` |
| `exclude` | `paths_exclude_glob` |
| `glob` | `paths_include_glob` *(except in `list_dir`, where `glob` is also accepted natively as a synonym)* |

### Function-specific aliases (override globals for the named function)

| Function | Alias | Canonical |
|-|-|-|
| `replace_content`   | `old_content` | `needle`  |
| `replace_content`   | `new_content` | `repl`    |
| `create_text_file`  | `new_content` | `content` |
| `replace_lines`     | `new_content` | `content` |
| `insert_at_line`    | `new_content` | `content` |

### Function-name aliases

| Alias | Canonical |
|-|-|
| `ls`             | `list_dir`           |
| `glob`           | `find_file`          |
| `grep`, `search` | `search_for_pattern` |

### Unknown-parameter hint

When a handler raises an error (e.g. a missing required parameter), the
error message is augmented with a list of any caller-supplied keys that
are not known to the canonical function. Example:

```
Missing required parameter: needle | Unknown params for 'replace_content': foo_bar. Accepted: allow_multiple_occurrences, mode, needle, relative_path, repl.
```

Use the hint to spot typos or wrong-alias choices in the next call.

## Error Handling

All errors return `{"error":"message"}` in the tool response with `isError: true`. Common errors:
- Missing required parameters
- Path escapes project root (sandbox violation)
- File/directory not found
- Invalid regex pattern
- Multiple occurrences when `allow_multiple_occurrences` is false

## Security

All paths are sandboxed under `--project-root`. Symlinks are resolved before validation. Attempts to escape the project root via `..` or absolute paths are rejected.
