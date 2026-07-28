#!/usr/bin/env bash

# Master post-edit linter
# Dispatches to appropriate linter based on file extension:
#   C/C++        - clang-tidy
#   JSON/JSONC   - jq
#   Python       - in-memory compile() (no .pyc) + ruff (optional)
#   Vue/JS/TS    - biome / oxlint / eslint (auto-detected)

INPUT_JSON=$(cat)

# --- Common parse -------------------------------------------------------------
TOOL_NAME=$(echo "$INPUT_JSON" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
FILE_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only Edit/MultiEdit/Write
if [[ ! "$TOOL_NAME" =~ ^(Edit|MultiEdit|Write)$ ]]; then
	exit 0
fi

# File must exist
if [ ! -f "$FILE_PATH" ]; then
	exit 0
fi

EXT="${FILE_PATH##*.}"

# --- C/C++: clang-tidy --------------------------------------------------------
lint_clang_tidy() {
	local PROJECT_ROOT="${PROJECT_ROOT:-$CLAUDE_PROJECT_DIR}"

	if [ -z "$PROJECT_ROOT" ]; then
		echo "[lint] clang-tidy: PROJECT_ROOT not set, skipping" >&2
		return 0
	fi

	if [ ! -f "$PROJECT_ROOT/build/CMakeCache.txt" ]; then
		echo "[lint] clang-tidy: no CMakeCache.txt (run CMake first), skipping" >&2
		return 0
	fi

	local CLANG_TIDY_BIN
	CLANG_TIDY_BIN=$(grep "^CLANG_TIDY_EXE:FILEPATH=" "$PROJECT_ROOT/build/CMakeCache.txt" | cut -d'=' -f2)
	if [ -z "$CLANG_TIDY_BIN" ] || [ ! -f "$CLANG_TIDY_BIN" ]; then
		echo "[lint] clang-tidy: CLANG_TIDY_EXE not found in CMakeCache, skipping" >&2
		return 0
	fi

	if [ ! -f "$PROJECT_ROOT/build/compile_commands.json" ]; then
		echo "[lint] clang-tidy: no compile_commands.json, skipping" >&2
		return 0
	fi

	echo "- [lint] clang-tidy: $FILE_PATH..."

	local TIDY_OUTPUT
	TIDY_OUTPUT=$("$CLANG_TIDY_BIN" "$FILE_PATH" \
		-p="$PROJECT_ROOT/build" \
		--config-file="$PROJECT_ROOT/.clang-tidy" \
		--quiet \
		--format-style=file \
		--header-filter="$PROJECT_ROOT/src/.*" 2>&1)

	local FILTERED
	FILTERED=$(echo "$TIDY_OUTPUT" \
		| grep -v "^Suppressed " \
		| grep -v "^Use -header-filter" \
		| grep -v "^[0-9]* warnings generated\.$")

	if [ -n "$FILTERED" ]; then
		echo "$TIDY_OUTPUT" >&2
		return 2
	fi

	return 0
}

# --- JSON / JSONC / JSONL / JSON5 ---------------------------------------------
lint_json() {
	if ! command -v jq &>/dev/null; then
		echo "[lint] json: jq not found, skipping" >&2
		return 0
	fi

	echo "- [lint] json: $FILE_PATH..."

	local JQ_OUTPUT STRIPPED ERRORS LINE_NUM line

	case "$EXT" in
		json)
			if ! JQ_OUTPUT=$(jq '.' "$FILE_PATH" 2>&1 >/dev/null); then
				echo "[lint] json: invalid JSON:" >&2
				echo "$JQ_OUTPUT" >&2
				return 2
			fi
			;;

		jsonc)
			STRIPPED=$(sed 's|//.*$||g' "$FILE_PATH" | sed ':a;N;$!ba;s|/\*[^*]*\*\+\([^/*][^*]*\*\+\)*/||g')
			if ! JQ_OUTPUT=$(echo "$STRIPPED" | jq '.' 2>&1 >/dev/null); then
				echo "[lint] json: invalid JSONC:" >&2
				echo "$JQ_OUTPUT" >&2
				return 2
			fi
			;;

		jsonl)
			LINE_NUM=0
			ERRORS=""
			while IFS= read -r line || [ -n "$line" ]; do
				LINE_NUM=$((LINE_NUM + 1))
				[ -z "$(echo "$line" | tr -d '[:space:]')" ] && continue
				if ! JQ_OUTPUT=$(echo "$line" | jq '.' 2>&1 >/dev/null); then
					ERRORS="${ERRORS}Line $LINE_NUM: $JQ_OUTPUT\n"
				fi
			done <"$FILE_PATH"
			if [ -n "$ERRORS" ]; then
				echo "[lint] json: invalid JSONL:" >&2
				echo -e "$ERRORS" >&2
				return 2
			fi
			;;

		json5)
			STRIPPED=$(sed 's|//.*$||g' "$FILE_PATH" | sed ':a;N;$!ba;s|/\*[^*]*\*\+\([^/*][^*]*\*\+\)*/||g')
			STRIPPED=$(echo "$STRIPPED" | sed 's/,\([[:space:]]*[}\]]\)/\1/g')
			if ! JQ_OUTPUT=$(echo "$STRIPPED" | jq '.' 2>&1 >/dev/null); then
				if command -v npx &>/dev/null; then
					local NPX_OUTPUT
					if ! NPX_OUTPUT=$(npx --yes json5 -v "$FILE_PATH" 2>&1); then
						echo "[lint] json: invalid JSON5:" >&2
						echo "$NPX_OUTPUT" >&2
						return 2
					fi
				else
					echo "[lint] json: invalid JSON5 (basic check):" >&2
					echo "$JQ_OUTPUT" >&2
					return 2
				fi
			fi
			;;
	esac

	return 0
}

# --- Python -------------------------------------------------------------------
lint_python() {
	echo "- [lint] python: $FILE_PATH..."

	# Step 1: syntax check (always, no external deps)
	# compile() IN MEMORY, never `python3 -m py_compile`: py_compile writes a
	# __pycache__/*.pyc next to every file this hook touches, i.e. the linter
	# would litter the tree on every edit. Compiling the raw BYTES also makes
	# the tokenizer honour a PEP-263 coding cookie and a UTF-8 BOM the way the
	# interpreter itself does, so a valid non-UTF-8 source is not flagged.
	local PY_OUTPUT
	if ! PY_OUTPUT=$(python3 -c 'import sys
p = sys.argv[1]
try:
    compile(open(p, "rb").read(), p, "exec", dont_inherit=True)
except SyntaxError as e:
    sys.exit("%s:%s:%s: %s" % (p, e.lineno, e.offset, e.msg))
except Exception as e:
    sys.exit("%s: %s: %s" % (p, type(e).__name__, e))
' "$FILE_PATH" 2>&1); then
		echo "[lint] python: syntax error:" >&2
		echo "$PY_OUTPUT" >&2
		return 2
	fi

	# Step 2: ruff (if available)
	local RUFF_BIN=""
	if command -v ruff &>/dev/null; then
		RUFF_BIN="ruff"
	elif python3 -m ruff --version &>/dev/null 2>&1; then
		RUFF_BIN="python3 -m ruff"
	fi

	if [ -n "$RUFF_BIN" ]; then
		local RUFF_OUTPUT
		if ! RUFF_OUTPUT=$($RUFF_BIN check "$FILE_PATH" 2>&1); then
			echo "[lint] python: ruff errors:" >&2
			echo "$RUFF_OUTPUT" >&2
			return 2
		fi
		[ -n "$RUFF_OUTPUT" ] && echo "$RUFF_OUTPUT"
	fi

	return 0
}

# --- Vue / JS / TS ------------------------------------------------------------
lint_vue() {
	if ! command -v jq &>/dev/null; then
		echo "[lint] vue: jq not found, cannot detect linter" >&2
		return 0
	fi

	# Walk up from file dir to find package.json
	local FILE_DIR PACKAGE_JSON dir
	FILE_DIR=$(dirname "$FILE_PATH")
	PACKAGE_JSON=""
	dir="$FILE_DIR"
	while [ "$dir" != "/" ]; do
		if [ -f "$dir/package.json" ]; then
			PACKAGE_JSON="$dir/package.json"
			break
		fi
		dir=$(dirname "$dir")
	done

	[ -z "$PACKAGE_JSON" ] && return 0

	local PROJECT_DIR
	PROJECT_DIR=$(dirname "$PACKAGE_JSON")

	has_package() {
		jq -e "(.dependencies[\"$1\"] // .devDependencies[\"$1\"]) != null" "$PACKAGE_JSON" >/dev/null 2>&1
	}

	local LINTER="" LINTER_CMD=""

	# Priority 1: Biome
	if has_package "@biomejs/biome" && { [ -f "$PROJECT_DIR/biome.json" ] || [ -f "$PROJECT_DIR/biome.jsonc" ]; }; then
		LINTER="biome"
		LINTER_CMD="npx biome check"
	fi

	# Priority 2: oxlint
	if [ -z "$LINTER" ] && has_package "oxlint"; then
		LINTER="oxlint"
		LINTER_CMD="npx oxlint"
	fi

	# Priority 3: ESLint
	if [ -z "$LINTER" ] && has_package "eslint"; then
		for cfg in eslint.config.js eslint.config.mjs eslint.config.cjs \
		           .eslintrc .eslintrc.js .eslintrc.cjs \
		           .eslintrc.json .eslintrc.yml .eslintrc.yaml; do
			if [ -f "$PROJECT_DIR/$cfg" ]; then
				LINTER="eslint"
				LINTER_CMD="npx eslint"
				break
			fi
		done
		if [ -z "$LINTER" ] && jq -e '.eslintConfig != null' "$PACKAGE_JSON" >/dev/null 2>&1; then
			LINTER="eslint"
			LINTER_CMD="npx eslint"
		fi
	fi

	[ -z "$LINTER" ] && return 0

	echo "- [lint] $LINTER: $FILE_PATH..."

	cd "$PROJECT_DIR" || return 1

	local LINT_OUTPUT
	if ! LINT_OUTPUT=$($LINTER_CMD "$FILE_PATH" 2>&1); then
		echo "[lint] $LINTER errors:" >&2
		echo "$LINT_OUTPUT" >&2
		return 2
	fi

	[ -n "$LINT_OUTPUT" ] && echo "$LINT_OUTPUT"
	return 0
}

# --- Dispatch -----------------------------------------------------------------
case "$EXT" in
	c|cpp|h|hpp)
		lint_clang_tidy
		;;

	json|jsonc|jsonl|json5)
		lint_json
		;;

	py)
		lint_python
		;;

	vue|js|jsx|ts|tsx|mjs|cjs|mts|cts)
		lint_vue
		;;

	*)
		exit 0
		;;
esac

exit $?
