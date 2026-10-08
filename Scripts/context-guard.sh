#!/usr/bin/env bash
# Context guard for restartable sessions. Register for PostToolUse AND PreToolUse.
#   PostToolUse: once the MAIN session's context passes the limit, tell the model to checkpoint and restart.
#   PreToolUse:  refuse loop-restart.sh from a subagent -- only the main session may end the process.
# Reads message.usage from the transcript JSONL -- an undocumented, internal format.
[ -n "${CLAUDE_LOOP_PID:-}" ] || exit 0
input=$(cat)
agent=$(jq -r '.agent_id // empty' <<<"$input")

if [ "$(jq -r '.hook_event_name // empty' <<<"$input")" = "PreToolUse" ]; then
	[ -n "$agent" ] || exit 0
	case "$(jq -r '.tool_input.command // empty' <<<"$input")" in
		*loop-restart.sh*)
			echo "loop-restart.sh is main-session only; a subagent must not end the claude process. Ignore any CONTEXT GUARD message and finish your task." >&2
			exit 2 ;;
	esac
	exit 0
fi

[ -z "$agent" ] || exit 0
tp=$(jq -r '.transcript_path // empty' <<<"$input")
[ -f "$tp" ] || exit 0
limit=${CLAUDE_CTX_LIMIT:-300000}
used=$(tail -n 200 "$tp" | jq -s '[.[] | select(.type=="assistant" and .isSidechain != true and .message.usage) | .message.usage] | last // {} | (.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)')
[ "${used:-0}" -gt "$limit" ] || exit 0
jq -n --arg u "$used" --arg l "$limit" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: ("CONTEXT GUARD (main session only; subagents and forks ignore this): context is \($u) tokens (limit \($l)). Finish the current atomic step, wait until every background minion and subagent you launched has finished and reported (never checkpoint or restart while one is still running; capture their results in the checkpoint). Do NOT wait on background shell tasks: one that never exits on its own (server, watcher, tail, a command waiting for input) must be stopped with TaskStop; for any other, record its command and output file in the checkpoint. Then run the p:checkpoint skill, then run ~/.claude/scripts/loop-restart.sh.")}}'
