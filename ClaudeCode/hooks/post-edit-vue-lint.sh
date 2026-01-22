#!/usr/bin/env bash
# Post-edit Vue/JS/TS linter hook
# Auto-detects linter from package.json: ESLint, Biome, oxlint

INPUT_JSON=$(cat)

# Parse tool name and file path from JSON using sed
TOOL_NAME=$(echo "$INPUT_JSON" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
FILE_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only for Vue/JS/TS files
if [[ ! "$FILE_PATH" =~ \.(vue|js|jsx|ts|tsx|mjs|cjs|mts|cts)$ ]]; then
	exit 0
fi

# Only for Edit/MultiEdit/Write tools
if [[ ! "$TOOL_NAME" =~ ^(Edit|MultiEdit|Write)$ ]]; then
	exit 0
fi

# Check if file exists
if [ ! -f "$FILE_PATH" ]; then
	exit 0
fi

# Check if jq is available
if ! command -v jq &>/dev/null; then
	echo "[vue-lint] ! jq not found, cannot detect linter" >&2
	exit 0
fi

# Find package.json - walk up from file directory
find_package_json() {
	local dir="$1"
	while [ "$dir" != "/" ]; do
		if [ -f "$dir/package.json" ]; then
			echo "$dir/package.json"
			return 0
		fi
		dir=$(dirname "$dir")
	done
	return 1
}

FILE_DIR=$(dirname "$FILE_PATH")
PACKAGE_JSON=$(find_package_json "$FILE_DIR")

if [ -z "$PACKAGE_JSON" ]; then
	# No package.json found, skip silently
	exit 0
fi

PROJECT_DIR=$(dirname "$PACKAGE_JSON")

# Check if a package exists in dependencies or devDependencies
has_package() {
	local pkg="$1"
	jq -e "(.dependencies[\"$pkg\"] // .devDependencies[\"$pkg\"]) != null" "$PACKAGE_JSON" >/dev/null 2>&1
}

# Detect available linter
LINTER=""
LINTER_CMD=""

# Priority 1: Biome (fast, modern)
if has_package "@biomejs/biome"; then
	if [ -f "$PROJECT_DIR/biome.json" ] || [ -f "$PROJECT_DIR/biome.jsonc" ]; then
		LINTER="biome"
		LINTER_CMD="npx biome check"
	fi
fi

# Priority 2: oxlint (very fast)
if [ -z "$LINTER" ] && has_package "oxlint"; then
	LINTER="oxlint"
	LINTER_CMD="npx oxlint"
fi

# Priority 3: ESLint (most common)
if [ -z "$LINTER" ] && has_package "eslint"; then
	# Check for ESLint config (flat or legacy)
	if [ -f "$PROJECT_DIR/eslint.config.js" ] || \
	   [ -f "$PROJECT_DIR/eslint.config.mjs" ] || \
	   [ -f "$PROJECT_DIR/eslint.config.cjs" ] || \
	   [ -f "$PROJECT_DIR/.eslintrc" ] || \
	   [ -f "$PROJECT_DIR/.eslintrc.js" ] || \
	   [ -f "$PROJECT_DIR/.eslintrc.cjs" ] || \
	   [ -f "$PROJECT_DIR/.eslintrc.json" ] || \
	   [ -f "$PROJECT_DIR/.eslintrc.yml" ] || \
	   [ -f "$PROJECT_DIR/.eslintrc.yaml" ] || \
	   jq -e '.eslintConfig != null' "$PACKAGE_JSON" >/dev/null 2>&1; then
		LINTER="eslint"
		LINTER_CMD="npx eslint"
	fi
fi

# No linter detected
if [ -z "$LINTER" ]; then
	# Silent exit - no linter configured for this project
	exit 0
fi

echo "- [vue-lint] Checking $FILE_PATH with $LINTER..."

# Run linter from project directory
cd "$PROJECT_DIR" || exit 1

# Execute linter
if ! LINT_OUTPUT=$($LINTER_CMD "$FILE_PATH" 2>&1); then
	echo "[vue-lint] ! $LINTER errors:" >&2
	echo "$LINT_OUTPUT" >&2
	exit 2
fi

# Show output if there's any (warnings)
if [ -n "$LINT_OUTPUT" ]; then
	echo "$LINT_OUTPUT"
fi

exit 0
