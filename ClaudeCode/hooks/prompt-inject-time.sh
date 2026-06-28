#!/usr/bin/env bash
# UserPromptSubmit hook: inject the current precise wall-clock time into context.
#
# Why: the base context only carries the session-start DATE (frozen at launch),
# never the clock time, and it never updates during a long session. This refreshes
# "now" on every user prompt so time-sensitive reasoning has a fresh anchor.
#
# Contract (matches attention-reminder.py): emit ONE JSON line with
# hookSpecificOutput.additionalContext, and ALWAYS exit 0 — a non-zero exit from a
# UserPromptSubmit hook would BLOCK the prompt.

# Drain the payload on stdin so the writer never blocks on a full pipe.
cat >/dev/null 2>&1

LOCAL=$(date '+%Y-%m-%d %H:%M:%S %Z (%z)')
UTC=$(date -u '+%H:%M:%S')
EPOCH=$(date '+%s')

# additionalContext contains only digits/letters/spaces/()/+/-/| — all JSON-safe,
# so the string can be embedded without escaping.
CTX="[now] ${LOCAL} | UTC ${UTC} | epoch ${EPOCH}"

printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' "$CTX"
exit 0
