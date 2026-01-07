#!/usr/bin/env bash
# Post-edit clang-format auto-formatter

# Read JSON input from stdin
INPUT_JSON=$(cat)

# Set PROJECT_ROOT from CLAUDE_PROJECT_DIR if not set
PROJECT_ROOT="${PROJECT_ROOT:-$CLAUDE_PROJECT_DIR}"

if [ -z "$PROJECT_ROOT" ]; then
	echo "[clang-format] ! PROJECT_ROOT not set" >&2
	exit 1
fi

# Parse tool name and file path from JSON using sed
TOOL_NAME=$(echo "$INPUT_JSON" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
FILE_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only for C/C++ files
if [[ ! "$FILE_PATH" =~ \.(c|cpp|h|hpp)$ ]]; then
	exit 0
fi

# Only for Edit/MultiEdit/Write tools
if [[ ! "$TOOL_NAME" =~ ^(Edit|MultiEdit|Write)$ ]]; then
	exit 0
fi

# Extract CLANG_FORMAT from CMake cache
if [ ! -f "$PROJECT_ROOT/build/CMakeCache.txt" ]; then
	echo "[clang-format] ! No CMakeCache.txt found, have to run CMake first to configure the build." >&2
	exit 1
fi

CMAKE_CLANG_FORMAT=$(grep "^CLANG_FORMAT_EXE:FILEPATH=" "$PROJECT_ROOT/build/CMakeCache.txt" | cut -d'=' -f2)
if [ -z "$CMAKE_CLANG_FORMAT" ] || [ ! -f "$CMAKE_CLANG_FORMAT" ]; then
	echo "[clang-format] ! No CLANG_FORMAT_EXE from CMake" >&2
	exit 1
fi

# Check for .clang-format config
if [ ! -f "$PROJECT_ROOT/.clang-format" ]; then
	echo "[clang-format] ! No .clang-format config found" >&2
	exit 1
fi

echo "- [clang-format] Formatting $FILE_PATH..."

# Run clang-format in-place
"$CMAKE_CLANG_FORMAT" -i --style=file "$FILE_PATH"

if [ $? -eq 0 ]; then
	echo "- [clang-format] ✓ Formatted successfully"
	exit 0
else
	echo "[clang-format] ! Failed to format file" >&2
	exit 2
fi
