---
name: p:purity-mcp
description: >
  MANDATORY — load this skill BEFORE any purity_call invocation or you WILL fail with wrong parameter names.
  The ONLY correct API reference for mcp-purity file operations: read_file, create_text_file, list_dir, find_file, replace_content, delete_lines, replace_lines, insert_at_line, search_for_pattern. Use when reading, writing, searching, listing, or editing files via mcp-purity MCP server.
  Trigger: purity_call, mcp-purity, file operations in purity-managed projects."
  - Reading, creating, editing, listing, searching files in a purity-managed project
  - Using purity_call tool or mcp__mcp-purity__purity_call
  - When the user mentions purity, mcp-purity, or purity_call
---

## Overview

MCP-Purity is a file operations MCP server. It exposes a single tool `purity_call` that dispatches file handler functions via the `function` parameter. **MCP tool name**: `mcp__mcp-purity__purity_call`

## Quick Start

```json
{"function":"","params":{}}
```
Returns server status and list of available functions.

## Available Functions

### 1. `read_file` — Read a file

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`relative_path`|string|yes|—|Relative path to file|
|`start_line`|int|no|0|0-based first line index|
|`end_line`|int|no|null|0-based last line (inclusive); null = read to end|
|`max_answer_chars`|int|no|-1|Character limit; -1 = unlimited|

```json
{"function":"read_file","params":{"relative_path":"src/main.py","start_line":0,"end_line":49}}
```

### 2. `create_text_file` — Create or overwrite a file

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|Relative path to file|
|`content`|string|yes|Full content to write|

Creates parent directories automatically. **Destructive** — overwrites existing files.

```json
{"function":"create_text_file","params":{"relative_path":"src/utils.py","content":"def add(a, b):\n    return a + b\n"}}
```

### 3. `list_dir` — List directory contents

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`relative_path`|string|no|"."|Directory to list|
|`recursive`|bool|no|false|Scan subdirectories|
|`skip_ignored_files`|bool|no|false|Skip gitignored files|
|`max_answer_chars`|int|no|-1|Character limit|

```json
{"function":"list_dir","params":{"relative_path":"src","recursive":true,"skip_ignored_files":true}}
```

### 4. `find_file` — Find files by wildcard pattern

|Param|Type|Required|Default|Description|
|-|-|-|-|-|
|`file_mask`|string|yes|—|Filename pattern with `*` or `?` wildcards|
|`relative_path`|string|no|"."|Directory subtree to search|

```json
{"function":"find_file","params":{"file_mask":"*.test.ts","relative_path":"src"}}
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
{"function":"replace_content","params":{"relative_path":"config.py","needle":"DEBUG = True","repl":"DEBUG = False","mode":"literal"}}
```

### 6. `delete_lines` — Delete a range of lines

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|File to modify|
|`start_line`|int|yes|0-based first line to delete|
|`end_line`|int|yes|0-based last line to delete (inclusive)|

```json
{"function":"delete_lines","params":{"relative_path":"src/app.py","start_line":10,"end_line":15}}
```

### 7. `replace_lines` — Replace a range of lines

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|File to modify|
|`start_line`|int|yes|0-based first line to replace|
|`end_line`|int|yes|0-based last line to replace (inclusive)|
|`content`|string|yes|New content to insert|

```json
{"function":"replace_lines","params":{"relative_path":"src/app.py","start_line":5,"end_line":7,"content":"    return new_value\n"}}
```

### 8. `insert_at_line` — Insert content at a line

|Param|Type|Required|Description|
|-|-|-|-|
|`relative_path`|string|yes|File to modify|
|`line`|int|yes|0-based line index to insert at|
|`content`|string|yes|Content to insert|

Existing content at `line` shifts down. Does not replace.

```json
{"function":"insert_at_line","params":{"relative_path":"src/main.py","line":0,"content":"# Auto-generated\n"}}
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
{"function":"search_for_pattern","params":{"substring_pattern":"TODO|FIXME","context_lines_after":1,"paths_include_glob":"**/*.py"}}
```

## Error Handling

All errors return `{"error":"message"}` in the tool response with `isError: true`. Common errors:
- Missing required parameters
- Path escapes project root (sandbox violation)
- File/directory not found
- Invalid regex pattern
- Multiple occurrences when `allow_multiple_occurrences` is false

## Security

All paths are sandboxed under `--project-root`. Symlinks are resolved before validation. Attempts to escape the project root via `..` or absolute paths are rejected.
