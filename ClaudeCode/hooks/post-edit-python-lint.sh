#!/usr/bin/env bash
# Post-edit Python linter hook
# Always: in-memory compile() (syntax, no deps, writes no .pyc)
# Optional: ruff check (style, imports) if available

INPUT_JSON=$(cat)

# Parse tool name and file path from JSON using sed
TOOL_NAME=$(echo "$INPUT_JSON" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
FILE_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only for Python files
if [[ ! "$FILE_PATH" =~ \.py$ ]]; then
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

echo "- [python-lint] Checking $FILE_PATH..."

# --- Step 1: Syntax check (always, no deps) ---
# compile() IN MEMORY, never `python3 -m py_compile`: py_compile writes a
# __pycache__/*.pyc next to every file this hook touches, i.e. the linter would
# litter the tree on every edit. Compiling the raw BYTES also makes the
# tokenizer honour a PEP-263 coding cookie and a UTF-8 BOM the way the
# interpreter itself does, so a valid non-UTF-8 source is not flagged.
if ! PY_OUTPUT=$(python3 -c 'import sys
p = sys.argv[1]
try:
    compile(open(p, "rb").read(), p, "exec", dont_inherit=True)
except SyntaxError as e:
    sys.exit("%s:%s:%s: %s" % (p, e.lineno, e.offset, e.msg))
except Exception as e:
    sys.exit("%s: %s: %s" % (p, type(e).__name__, e))
' "$FILE_PATH" 2>&1); then
	echo "[python-lint] ! Syntax error:" >&2
	echo "$PY_OUTPUT" >&2
	exit 2
fi

# --- Step 2: ruff (if available) ---
RUFF_BIN=""
if command -v ruff &>/dev/null; then
	RUFF_BIN="ruff"
elif python3 -m ruff --version &>/dev/null 2>&1; then
	RUFF_BIN="python3 -m ruff"
fi

if [ -n "$RUFF_BIN" ]; then
	if ! RUFF_OUTPUT=$($RUFF_BIN check "$FILE_PATH" 2>&1); then
		echo "[python-lint] ! ruff errors:" >&2
		echo "$RUFF_OUTPUT" >&2
		exit 2
	fi
	if [ -n "$RUFF_OUTPUT" ]; then
		echo "$RUFF_OUTPUT"
	fi
fi

exit 0
