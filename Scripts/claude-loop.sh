#!/usr/bin/env bash
# Run claude in a restart loop: each round resumes from .claude/tmp/checkpoint.md (checkpoint.py nexts).
# A round ends in a restart ONLY when the session itself ran loop-restart.sh (which touches the flag);
# a normal exit (/exit, Ctrl-C) ends the loop.
#
# Usage (from the repo root): claude-loop.sh [--entity NAME] [claude flags...]
# With no flags it uses the Safranek launch line. Env: CLAUDE_CTX_LIMIT (default 250000, read by context-guard.sh).
set -u

# Optional first argument `--entity NAME`: the checkpoint extension of one work stream. The loop then
# resumes from .claude/tmp/checkpoint-NAME.md (staging session-block-NAME.md) and uses its own restart flag,
# so several loops in one repo do not touch each other. Without it the shared checkpoint.md is used.
ENTITY=""
if [ "${1:-}" = "--entity" ]; then
	ENTITY="${2:-}"
	case "$ENTITY" in
		""|*[!a-z0-9-]*) echo "claude-loop: --entity needs a name of [a-z0-9-]" >&2; exit 1 ;;
	esac
	shift 2
fi

CKPT=".claude/tmp/checkpoint${ENTITY:+-$ENTITY}.md"
if [ ! -f "$CKPT" ]; then
	echo "claude-loop: no $CKPT here -- run it from the repo root (and the entity file must exist)" >&2
	exit 1
fi

export CLAUDE_LOOP_PID=$$
export CLAUDE_LOOP_FLAG="$PWD/.claude/tmp/loop-restart${ENTITY:+-$ENTITY}"

if [ $# -eq 0 ]; then
	set -- --model opus \
		--settings '{"skillOverrides":{"code-review":"off"}}' \
		--disallowed-tools EnterPlanMode ExitPlanMode \
		--system-prompt-file "$HOME/.claude/safranek/system-prompt.assembled.txt"
fi

if [ -z "$ENTITY" ]; then
	PROMPT='Restartable session (claude-loop). First run `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py nexts` and resume the ai-soul work from the newest session: its STATE, NEXT and ACTIVATION block are authoritative. Continue the development along NEXT. When a CONTEXT GUARD message appears: finish the current atomic step (no half-applied edits), invoke the p:checkpoint skill, then run `~/.claude/scripts/loop-restart.sh` as your very last action. Never run that script for any other reason. If blocked on an operator decision, ask with AskUserQuestion and wait.'
else
	PROMPT='Restartable session (claude-loop, entity '"$ENTITY"'). Your checkpoint file is `'"$CKPT"'`; the shared .claude/tmp/checkpoint.md is a frozen archive. First run `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py nexts --file '"$CKPT"'` and resume the work of the '"$ENTITY"' stream from its newest session: its STATE, NEXT and ACTIVATION block are authoritative. Continue the development along NEXT. When a CONTEXT GUARD message appears: finish the current atomic step (no half-applied edits), invoke the p:checkpoint skill and write ONLY to `'"$CKPT"'` (checkpoint.py prepend --file '"$CKPT"' --block-file .claude/tmp/session-block-'"$ENTITY"'.md), then run `~/.claude/scripts/loop-restart.sh` as your very last action. Never run that script for any other reason. If blocked on an operator decision, ask with AskUserQuestion and wait.'
fi

round=0
while :; do
	round=$((round + 1))
	rm -f "$CLAUDE_LOOP_FLAG"
	echo "claude-loop: round $round (context limit ${CLAUDE_CTX_LIMIT:-250000})" >&2
	safranek "$@" -- "$PROMPT"
	stty sane 2>/dev/null
	[ -f "$CLAUDE_LOOP_FLAG" ] || break
	rm -f "$CLAUDE_LOOP_FLAG"
	sleep 2
done
echo "claude-loop: stopped after round $round" >&2
