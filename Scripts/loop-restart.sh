#!/usr/bin/env bash
# Called by the model: terminate this claude process so whatever launched it can restart it.
# CLAUDE_LOOP_PID = pid of the process that started claude (a loop, or an interactive shell).
set -eu
: "${CLAUDE_LOOP_PID:?not running under claude-loop}"
p=$PPID
while [ "$p" -gt 1 ]; do
	pp=$(ps -o ppid= -p "$p" | tr -d ' ')
	if [ "$pp" = "$CLAUDE_LOOP_PID" ]; then
		if [ -n "${CLAUDE_LOOP_FLAG:-}" ]; then
			touch "$CLAUDE_LOOP_FLAG"
		fi
		kill -TERM "$p"
		exit 0
	fi
	p=$pp
done
echo "no claude process found under loop pid $CLAUDE_LOOP_PID" >&2
exit 1
