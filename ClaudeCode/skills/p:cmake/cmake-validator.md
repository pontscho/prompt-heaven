# CMake Validator

Automatic validator for detecting legacy CMake patterns in `CMakeLists.txt` and `.cmake` files.

## Purpose

This validator helps enforce modern CMake best practices by detecting legacy patterns and suggesting target-based alternatives.

## Usage

```bash
# Validate a single file
python3 cmake-validator.py CMakeLists.txt
python3 cmake-validator.py template.cmake

# Validate all CMake files in a directory (recursive)
python3 cmake-validator.py /path/to/project

# Validate current directory
python3 cmake-validator.py .
```

## What It Detects

### 🔴 **ERRORS** (Critical issues that will cause problems)

| Pattern | Issue | Modern Alternative |
|---------|-------|-------------------|
| `link_libraries()` | Global linking affects all targets | `target_link_libraries(target PRIVATE ...)` |
| `-static` on macOS | Will fail with crt0.o error | `CMAKE_FIND_LIBRARY_SUFFIXES .a` instead |

### 🟡 **WARNINGS** (Should be fixed for best practices)

| Pattern | Issue | Modern Alternative |
|---------|-------|-------------------|
| `include_directories()` | Global includes affect all targets | `target_include_directories(target PRIVATE ...)` |
| `add_definitions()` | Global definitions affect all targets | `target_compile_definitions(target PRIVATE ...)` |
| Missing `PRIVATE/PUBLIC` | Unclear visibility scope | Add `PRIVATE`, `PUBLIC`, or `INTERFACE` |

### ℹ️ **INFO** (Consider improving)

| Pattern | Issue | Modern Alternative |
|---------|-------|-------------------|
| `add_compile_options()` | May be too global | `target_compile_options()` for target-specific flags |
| `CMAKE_C_FLAGS` directly | Legacy flag setting | Use `target_compile_options()` or `add_compile_options()` |
| `find_package()` without `REQUIRED` | No explicit error handling | Add `REQUIRED` or check `${PACKAGE}_FOUND` |

## Output Format

```
================================================================================
File: CMakeLists.txt
================================================================================

ERRORS:
--------------------------------------------------------------------------------

Line 42: link_libraries()
  link_libraries(pthread)
  ⚠ Using global link_libraries() - affects all targets
  ✓ Use target_link_libraries(target PRIVATE/PUBLIC ...)

WARNINGS:
--------------------------------------------------------------------------------

Line 35: include_directories()
  include_directories(${CMAKE_SOURCE_DIR}/include)
  ⚠ Using global include_directories() - affects all targets
  ✓ Use target_include_directories(target PRIVATE/PUBLIC ...)

================================================================================
Summary: 1 errors, 1 warnings, 0 info
================================================================================
```

## Exit Codes

- `0`: No issues or only info/warnings
- `1`: Critical errors found (blocks build or causes failures)

## Examples

### Before (Legacy Pattern ❌)

```cmake
# Global affects all targets
include_directories(${CMAKE_SOURCE_DIR}/include)
link_libraries(pthread)
add_definitions(-DENABLE_FEATURE)

add_executable(myapp src/main.c)
```

### After (Modern Pattern ✅)

```cmake
add_executable(myapp src/main.c)

# Target-specific, clear visibility
target_include_directories(myapp PRIVATE ${CMAKE_SOURCE_DIR}/include)
target_link_libraries(myapp PRIVATE pthread)
target_compile_definitions(myapp PRIVATE ENABLE_FEATURE)
```

## Integration

### Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

files=$(git diff --cached --name-only --diff-filter=ACM | grep -E 'CMakeLists\.txt|\.cmake$')

if [ -n "$files" ]; then
    for file in $files; do
        python3 ~/.claude/skills/p:cmake/cmake-validator.py "$file"
        if [ $? -ne 0 ]; then
            echo "❌ CMake validation failed for $file"
            exit 1
        fi
    done
fi
```

### CI/CD (GitHub Actions)

```yaml
- name: Validate CMake files
  run: |
    pip install --no-deps ~/.claude/skills/p:cmake/cmake-validator.py
    find . -name "CMakeLists.txt" -o -name "*.cmake" | xargs python3 cmake-validator.py
```

## Why Target-Based Commands?

**Global commands** (`include_directories`, `link_libraries`, `add_definitions`):
- Affect ALL targets in the current directory and subdirectories
- Make dependencies unclear
- Cause unintended side effects
- Hard to debug

**Target-based commands** (`target_*`):
- Only affect specified target
- Clear, explicit dependencies
- Better modularity
- Easier to maintain

## Configuration

You can customize patterns by editing the `PATTERNS` dictionary in `cmake-validator.py`:

```python
PATTERNS = {
    r'pattern_regex': {
        'name': 'Pattern Name',
        'severity': Severity.WARNING,  # ERROR, WARNING, or INFO
        'message': 'What is wrong',
        'suggestion': 'How to fix it',
        'exceptions': ['keywords', 'to', 'skip']
    }
}
```

## Related Documentation

- [SKILL.md](./SKILL.md) - Full CMake best practices guide
- [quick-reference.md](./quick-reference.md) - Quick lookup for common patterns
- [examples.md](./examples.md) - Real-world CMake examples
- [template.cmake](./template.cmake) - Starter template for new projects

## Support

For issues or feature requests, update the validator script or open an issue in the project repository.
