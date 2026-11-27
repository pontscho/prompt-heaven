#!/usr/bin/env bash
# Context guard for restartable sessions. Register for PostToolUse (matcher *) AND PreToolUse (matcher *).
#   PostToolUse: once the MAIN session's context passes the limit, tell the model to checkpoint and restart.
#   PreToolUse:  refuse loop-restart.sh from a subagent -- only the main session may end the process.
#                Past the HARD limit (CLAUDE_CTX_HARD, default limit + 50000) refuse every main-session tool
#                call except the ones a checkpoint and a restart need.
# The user lifts both tiers by creating "$CLAUDE_LOOP_FLAG.continue" (claude-loop.sh removes it at each round start).
# Reads message.usage from the transcript JSONL -- an undocumented, internal format.
[ -n "${CLAUDE_LOOP_PID:-}" ] || exit 0
input=$(cat)
agent=$(jq -r '.agent_id // empty' <<<"$input")
event=$(jq -r '.hook_event_name // empty' <<<"$input")
limit=${CLAUDE_CTX_LIMIT:-250000}
hard=${CLAUDE_CTX_HARD:-$((limit + 50000))}
cont=""
[ -z "${CLAUDE_LOOP_FLAG:-}" ] || cont="$CLAUDE_LOOP_FLAG.continue"

# Current main-session context size from the transcript tail; 0 when unknown.
ctx_used() {
	local tp
	tp=$(jq -r '.transcript_path // empty' <<<"$input")
	[ -f "$tp" ] || { echo 0; return; }
	tail -n 200 "$tp" | jq -s '[.[] | select(.type=="assistant" and .isSidechain != true and .message.usage) | .message.usage] | last // {} | (.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)'
}

if [ "$event" = "PreToolUse" ]; then
	if [ -n "$agent" ]; then
		case "$(jq -r '.tool_input.command // empty' <<<"$input")" in
			*loop-restart.sh*)
				echo "loop-restart.sh is main-session only; a subagent must not end the claude process. Ignore any CONTEXT GUARD message and finish your task." >&2
				exit 2 ;;
		esac
		exit 0
	fi
	[ -z "$cont" ] || [ ! -e "$cont" ] || exit 0
	used=$(ctx_used)
	[ "${used:-0}" -gt "$hard" ] || exit 0
	tool=$(jq -r '.tool_name // empty' <<<"$input")
	case "$tool" in
		Skill)
			case "$(jq -r '.tool_input.skill // empty' <<<"$input")" in *checkpoint*) exit 0 ;; esac ;;
		Read|AskUserQuestion|TaskStop|TaskOutput|TodoWrite|ListAgents|SendMessage|mcp__mcp-git__*|mcp__mcp-inspect__*|mcp__ai-soul__*|mcp__messagebox__*)
			exit 0 ;;
		Write)
			case "$(jq -r '.tool_input.file_path // empty' <<<"$input")" in */.claude/tmp/*) exit 0 ;; esac ;;
		mcp__mcp-purity__purity_call)
			case "$(jq -r '.tool_input.function // .tool_input.f // empty' <<<"$input")" in
				create_text_file|find_file|list_dir|search_for_pattern|read_file) exit 0 ;;
			esac ;;
		Bash)
			case "$(jq -r '.tool_input.command // empty' <<<"$input")" in
				*checkpoint.py*|*roadmap.py*|*loop-restart.sh*) exit 0 ;;
			esac ;;
	esac
	echo "CONTEXT GUARD: context is $used tokens, past the hard limit $hard. $tool is refused. Allowed now: the p:checkpoint skill and its commands (checkpoint.py, mcp-git, create_text_file under .claude/tmp), TaskStop, AskUserQuestion, then ~/.claude/scripts/loop-restart.sh. Do the checkpoint sequence; do not retry this call. Only the user can lift this limit." >&2
	exit 2
fi

[ -z "$agent" ] || exit 0
[ -z "$cont" ] || [ ! -e "$cont" ] || exit 0
used=$(ctx_used)
[ "${used:-0}" -gt "$limit" ] || exit 0
over=$((used - limit))
jq -n --arg u "$used" --arg l "$limit" --arg o "$over" --arg h "$hard" --arg c "$cont" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: ("CONTEXT GUARD: STOP. This is a mandatory order from the harness, not advice, and it outranks the task you were working on. (Main session only; subagents and forks ignore it.)\nContext is \($u) tokens, limit \($l), \($o) over. This message repeats after every tool call until you obey it. At \($h) tokens every tool call except the checkpoint and restart ones is refused.\nDo not start anything new: no further search, edit, build, subtask or minion, and do not continue the user request. Your only remaining work is this sequence, in order:\n1. Finish the step already in flight so no edit is left half-applied. Nothing beyond that.\n2. Wait until every background minion and subagent you launched has finished and reported (never checkpoint or restart while one is still running), and put their results in the checkpoint. Do NOT wait on background shell tasks: one that never exits on its own (server, watcher, tail, a command waiting for input) must be stopped with TaskStop; for any other, record its command and output file in the checkpoint.\n3. Run the p:checkpoint skill.\n4. Run ~/.claude/scripts/loop-restart.sh as your very last action.\nKeeping on with the previous work instead, even when it looks nearly done, is the failure: past the limit the context degrades, and the restart is what carries the work forward.\nOnly the user can lift this. If the user has told you in this conversation, in so many words, to keep going past the limit, create the file \($c) (any content, one tool call) and carry on; it also silences this message and the hard limit until the next round. Never create it on your own judgment, and if you are unsure whether the user said so, ask with AskUserQuestion.")}}'
