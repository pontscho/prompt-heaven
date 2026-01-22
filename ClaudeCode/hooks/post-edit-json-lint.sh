#!/usr/bin/env bash
# Post-edit JSON linter hook
# Validates JSON syntax for .json, .jsonc, .jsonl, .json5 files

INPUT_JSON=$(cat)

# Parse tool name and file path from JSON using sed
TOOL_NAME=$(echo "$INPUT_JSON" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
FILE_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only for JSON files
if [[ ! "$FILE_PATH" =~ \.(json|jsonc|jsonl|json5)$ ]]; then
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
	echo "[json-lint] ! jq not found, skipping validation" >&2
	exit 0
fi

# Get file extension
EXT="${FILE_PATH##*.}"

echo "- [json-lint] Checking $FILE_PATH..."

# Validate based on extension
case "$EXT" in
json)
	# Standard JSON - direct jq validation
	if ! JQ_OUTPUT=$(jq '.' "$FILE_PATH" 2>&1 >/dev/null); then
		echo "[json-lint] ! Invalid JSON:" >&2
		echo "$JQ_OUTPUT" >&2
		exit 2
	fi
	;;

jsonc)
	# JSON with comments - strip comments first
	# Remove // comments and /* */ block comments
	STRIPPED=$(sed 's|//.*$||g' "$FILE_PATH" | sed ':a;N;$!ba;s|/\*[^*]*\*\+\([^/*][^*]*\*\+\)*/||g')
	if ! JQ_OUTPUT=$(echo "$STRIPPED" | jq '.' 2>&1 >/dev/null); then
		echo "[json-lint] ! Invalid JSONC:" >&2
		echo "$JQ_OUTPUT" >&2
		exit 2
	fi
	;;

jsonl)
	# JSON Lines - validate each line separately
	LINE_NUM=0
	ERRORS=""
	while IFS= read -r line || [ -n "$line" ]; do
		LINE_NUM=$((LINE_NUM + 1))
		# Skip empty lines
		if [ -z "$(echo "$line" | tr -d '[:space:]')" ]; then
			continue
		fi
		if ! JQ_OUTPUT=$(echo "$line" | jq '.' 2>&1 >/dev/null); then
			ERRORS="${ERRORS}Line $LINE_NUM: $JQ_OUTPUT\n"
		fi
	done <"$FILE_PATH"

	if [ -n "$ERRORS" ]; then
		echo "[json-lint] ! Invalid JSONL:" >&2
		echo -e "$ERRORS" >&2
		exit 2
	fi
	;;

json5)
	# JSON5 - try to handle common extensions
	# Strip // and /* */ comments, then try jq
	# Note: This won't handle all JSON5 features (trailing commas, unquoted keys, etc.)
	STRIPPED=$(sed 's|//.*$||g' "$FILE_PATH" | sed ':a;N;$!ba;s|/\*[^*]*\*\+\([^/*][^*]*\*\+\)*/||g')

	# Try to remove trailing commas before } and ]
	STRIPPED=$(echo "$STRIPPED" | sed 's/,\([[:space:]]*[}\]]\)/\1/g')

	if ! JQ_OUTPUT=$(echo "$STRIPPED" | jq '.' 2>&1 >/dev/null); then
		# Check if node/npx with json5 is available as fallback
		if command -v npx &>/dev/null; then
			if ! NPX_OUTPUT=$(npx --yes json5 -v "$FILE_PATH" 2>&1); then
				echo "[json-lint] ! Invalid JSON5:" >&2
				echo "$NPX_OUTPUT" >&2
				exit 2
			fi
		else
			echo "[json-lint] ! Invalid JSON5 (basic check):" >&2
			echo "$JQ_OUTPUT" >&2
			echo "[json-lint] Note: Install json5 (npx json5) for full JSON5 support" >&2
			exit 2
		fi
	fi
	;;
esac

exit 0
