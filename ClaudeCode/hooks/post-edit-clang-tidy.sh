#!/usr/bin/env bash
# Post-edit clang-tidy check

# Read JSON input from stdin
INPUT_JSON=$(cat)

# Set PROJECT_ROOT from CLAUDE_PROJECT_DIR if not set
PROJECT_ROOT="${PROJECT_ROOT:-$CLAUDE_PROJECT_DIR}"

if [ -z "$PROJECT_ROOT" ]; then
	echo "[clang-tidy] ! PROJECT_ROOT not set" >&2
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

# Extract CLANG_TIDY from CMake cache
if [ ! -f "$PROJECT_ROOT/build/CMakeCache.txt" ]; then
	echo "[clang-tidy] ! No CMakeCache.txt found, have to run CMake first to configure the build." >&2
	exit 1
fi

CMAKE_CLANG_TIDY=$(grep "^CLANG_TIDY_EXE:FILEPATH=" "$PROJECT_ROOT/build/CMakeCache.txt" | cut -d'=' -f2)
if [ -z "$CMAKE_CLANG_TIDY" ] || [ ! -f "$CMAKE_CLANG_TIDY" ]; then
	echo "[clang-tidy] ! No CLANG_TIDY_EXE from CMake" >&2
	exit 1
fi

# Check for compile_commands.json
if [ ! -f "${PROJECT_ROOT}/build/compile_commands.json" ]; then
	echo "[clang-tidy] ! No compile_commands.json found" >&2
	exit 1
fi

echo "- [clang-tidy] Checking $FILE_PATH..."

# clang-tidy run - capture output
TIDY_OUTPUT=$("$CMAKE_CLANG_TIDY" "$FILE_PATH" \
	-p="$PROJECT_ROOT/build" \
	--config-file="$PROJECT_ROOT/.clang-tidy" \
	--quiet \
	--format-style=file \
	--header-filter="$PROJECT_ROOT/src/.*" 2>&1)

# Filter out suppressed warnings info and metadata lines
FILTERED_OUTPUT=$(echo "$TIDY_OUTPUT" | grep -v "^Suppressed " | grep -v "^Use -header-filter" | grep -v "^[0-9]* warnings generated\.$")

# Show output (max 30 lines) if there are actual warnings
if [ -n "$FILTERED_OUTPUT" ]; then
	echo "$TIDY_OUTPUT" >&2
	# Non-empty filtered output means real warnings/errors found - block with exit code 2
	exit 2
fi

# No issues found (or only suppressed warnings)
exit 0
