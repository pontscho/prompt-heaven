#!/usr/bin/env bash
# SessionStart hook: inject host identity + session-start time into context.
#
# Why: the base context provides only platform (linux), shell, the kernel OS string,
# and CWD. It does NOT provide hostname, CPU architecture, distro name, user, CPU/RAM,
# or the time. This hook fills that gap once per session (and on resume/clear/compact).
#
# Contract (matches attention-reminder.py): emit ONE JSON line with
# hookSpecificOutput.additionalContext, and ALWAYS exit 0 — a hook must never block.

# Drain the payload on stdin so the writer never blocks on a full pipe.
cat >/dev/null 2>&1

HOSTNAME_S=$(hostname 2>/dev/null)
ARCH=$(uname -m 2>/dev/null)
KERNEL=$(uname -r 2>/dev/null)
USER_S=$(whoami 2>/dev/null)
OS_PRETTY=$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")
CPU_MODEL=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^ *//')
CPU_CORES=$(nproc 2>/dev/null)
MEM_TOTAL=$(free -h 2>/dev/null | awk '/^Mem:/{print $2}')
NOW=$(date '+%Y-%m-%d %H:%M:%S %Z (%z)')
NOW_UTC=$(date -u '+%Y-%m-%d %H:%M:%S')
TZID=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)

export HOSTNAME_S ARCH KERNEL USER_S OS_PRETTY CPU_MODEL CPU_CORES MEM_TOTAL NOW NOW_UTC TZID

# Build the JSON with python3 (always present — every MCP server is python3) so the
# multi-line, arbitrary host strings are escaped correctly.
python3 - <<'PY'
import json, os, sys
g = os.environ.get
ctx = (
	"[host-info] Host identity not present in the base context:\n"
	f"  Host : {g('HOSTNAME_S','?')} ({g('ARCH','?')})\n"
	f"  OS   : {g('OS_PRETTY','?')} (kernel {g('KERNEL','?')})\n"
	f"  User : {g('USER_S','?')}\n"
	f"  HW   : {g('CPU_CORES','?')} cores {g('CPU_MODEL','?')} / {g('MEM_TOTAL','?')} RAM\n"
	f"  Time : {g('NOW','?')} | UTC {g('NOW_UTC','?')} | TZ {g('TZID','?')}"
)
json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}, sys.stdout)
PY
exit 0
