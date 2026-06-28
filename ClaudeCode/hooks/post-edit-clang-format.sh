#!/usr/bin/env bash
# Post-edit clang-format auto-formatter
#
# Formats C/C++ files in place after an edit, using the clang-format binary
# recorded in the project's CMake cache. Any precondition that simply means
# "nothing to format here" (non-C file, no CMake build, no .clang-format) is a
# SILENT skip (exit 0) — never an error — so editing non-C / unconfigured
# projects stays quiet. A real clang-format failure is the only hard error.

# Read JSON input from stdin
INPUT_JSON=$(cat)

# Parse tool name and file path from JSON using sed
TOOL_NAME=$(echo "$INPUT_JSON" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
FILE_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only act on C/C++ files edited via Edit/MultiEdit/Write — otherwise nothing to do.
# These filters run FIRST so non-C edits never touch the precondition checks below.
[[ "$FILE_PATH" =~ \.(c|cpp|h|hpp)$ ]] || exit 0
[[ "$TOOL_NAME" =~ ^(Edit|MultiEdit|Write)$ ]] || exit 0

# Resolve the project root (Claude Code exports CLAUDE_PROJECT_DIR to hooks).
PROJECT_ROOT="${PROJECT_ROOT:-$CLAUDE_PROJECT_DIR}"
[ -n "$PROJECT_ROOT" ] || exit 0

# From here on, a missing build/config means "not a formattable project" → skip
# silently (exit 0), NOT an error. Only an actual format failure is fatal.

# Need a configured CMake build to know which clang-format to use.
[ -f "$PROJECT_ROOT/build/CMakeCache.txt" ] || exit 0

CMAKE_CLANG_FORMAT=$(grep "^CLANG_FORMAT_EXE:FILEPATH=" "$PROJECT_ROOT/build/CMakeCache.txt" | cut -d'=' -f2)
{ [ -n "$CMAKE_CLANG_FORMAT" ] && [ -f "$CMAKE_CLANG_FORMAT" ]; } || exit 0

# Need a style config to format against.
[ -f "$PROJECT_ROOT/.clang-format" ] || exit 0

echo "- [clang-format] Formatting $FILE_PATH..."

# Run clang-format in-place
if "$CMAKE_CLANG_FORMAT" -i --style=file "$FILE_PATH"; then
	echo "- [clang-format] ✓ Formatted successfully"
	exit 0
else
	echo "[clang-format] ! Failed to format file" >&2
	exit 2
fi
