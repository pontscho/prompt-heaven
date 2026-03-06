---
name: p:clang-tidy
description: Run clang-tidy static analysis on C/C++ source files after editing. Use when you need to verify code quality, check for warnings, or validate changes before committing. Trigger on /p:clang-tidy <file> [file2...] or after editing C/C++ (files with .c, .cpp, .h, .hpp extensions) files. Supports multiple files in a single call.
---

# clang-tidy

Run clang-tidy static analysis on C/C++ source files. **Supports multiple files in a single invocation.**

## Quick start

```bash
# Single file
/p:clang-tidy src/io-buffer.c

# Multiple files (PREFERRED for batch operations)
/p:clang-tidy src/io-buffer.c src/tools.c src/bitreader.c
```

## Requirements

- **clang-tidy** must be installed
- **Compilation database** must exist at `${PROJECT_ROOT}/build/compile_commands.json`
- Build configured with: `cmake -B build -S . -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`

## Instructions

1. **Validate input**: Ensure at least one source file path is provided as argument
2. **Resolve paths**: Convert relative paths to absolute if needed (for each file)
3. **Check files exist**: Verify all source files exist
4. **Run clang-tidy** (single call for ALL files):
   ```bash
   clang-tidy -p=${PROJECT_ROOT}/build --config-file=${PROJECT_ROOT}/.clang-tidy --quiet --format-style=file --header-filter="${PROJECT_ROOT}/src/.*" <file1> [file2] [file3...]
   ```
   **IMPORTANT: Pass ALL files to clang-tidy in ONE call. DO NOT run clang-tidy multiple times for each file!**
5. **Report results**:
   - Clean: "No issues found in <file(s)>"
   - Issues: Display warnings/errors with line numbers and suggest fixes

## Examples

```bash
# Single file
/p:clang-tidy src//io-buffer.c

# Multiple files (PREFERRED - single clang-tidy invocation)
/p:clang-tidy src/io-buffer.c src/bitreader.c src/endian.c

# Header file
/p:clang-tidy src/tools.h

# Mixed source and header files
/p:clang-tidy src/io-buffer.c src/io-buffer.h

# Nested paths
/p:clang-tidy src/ngs-stream-proxy/client/client-classic.c src/ngs-stream-proxy/client/client-ops-librtmp.c
```

## Command parameters

|Flag|Purpose|
|-|-|
|`-p=${PROJECT_ROOT}/build`|Compilation database location|
|`--config-file=${PROJECT_ROOT}/.clang-tidy`|Project-specific config|
|`--quiet`|Suppress unnecessary output|
|`--format-style=file`|Use .clang-format from project|
|`--header-filter="${PROJECT_ROOT}/src/.*"`|Only check project headers|

## Error handling

**No source file provided:**
```
Error: At least one source file path required.
Usage: /p:clang-tidy <file1> [file2] [file3...]
```

**File not found:**
```
Error: File not found: <path>
(Check all files before running clang-tidy)
```

**Compilation database not found:**
```
Error: Compilation database not found at ${PROJECT_ROOT}/build/compile_commands.json
Run: cmake -B build -S . -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

## Best practices

- Run AFTER editing C/C++ files
- **When checking multiple files, pass ALL files in ONE call** - do not run clang-tidy separately for each file
- Fix warnings before committing
- Some warnings may be false positives per project conventions (check CLAUDE.md)
- For batch checking all project files: `make -C build clang_tidy`
