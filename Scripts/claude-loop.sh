#!/usr/bin/env bash
# Run claude in a restart loop: each round resumes from .claude/tmp/checkpoint.md (checkpoint.py nexts).
# A round ends in a restart ONLY when the session itself ran loop-restart.sh (which touches the flag);
# a normal exit (/exit, Ctrl-C) ends the loop.
#
# Usage (from the repo root): claude-loop.sh [claude flags...]
# With no flags it uses the Safranek launch line. Env: CLAUDE_CTX_LIMIT (default 300000, read by context-guard.sh).
set -u

if [ ! -f .claude/tmp/checkpoint.md ]; then
	echo "claude-loop: no .claude/tmp/checkpoint.md here -- run it from the repo root" >&2
	exit 1
fi

export CLAUDE_LOOP_PID=$$
export CLAUDE_LOOP_FLAG="$PWD/.claude/tmp/loop-restart"

if [ $# -eq 0 ]; then
	set -- --model opus \
		--settings '{"skillOverrides":{"code-review":"off"}}' \
		--disallowed-tools EnterPlanMode ExitPlanMode \
		--system-prompt-file "$HOME/.claude/safranek/system-prompt.assembled.txt"
fi

PROMPT='Restartable session (claude-loop). First run `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py nexts` and resume the ai-soul work from the newest session: its STATE, NEXT and ACTIVATION block are authoritative. Continue the development along NEXT. When a CONTEXT GUARD message appears: finish the current atomic step (no half-applied edits), invoke the p:checkpoint skill, then run `~/.claude/scripts/loop-restart.sh` as your very last action. Never run that script for any other reason. If blocked on an operator decision, ask with AskUserQuestion and wait.'

round=0
while :; do
	round=$((round + 1))
	rm -f "$CLAUDE_LOOP_FLAG"
	echo "claude-loop: round $round (context limit ${CLAUDE_CTX_LIMIT:-300000})" >&2
	safranek "$@" -- "$PROMPT"
	stty sane 2>/dev/null
	[ -f "$CLAUDE_LOOP_FLAG" ] || break
	rm -f "$CLAUDE_LOOP_FLAG"
	sleep 2
done
echo "claude-loop: stopped after round $round" >&2
