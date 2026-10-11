#!/bin/bash
# tf probe: sbx_restrict group L (L9) -- run INSIDE a restricted sbx as
# `/bin/bash probe_exec.sh`. Prints ONE JSON line: a subshell, /bin/sh, and a
# background job killed by `kill $!` (the (target same-sandbox) signal rule,
# M0:2). Absolute paths only: the clean env may carry no PATH.
tf_sub=$( (echo sub) )
tf_sh=$(/bin/sh -c 'echo sh')
/bin/sleep 30 </dev/null >/dev/null 2>&1 &
tf_pid=$!
kill "$tf_pid" 2>/dev/null
wait "$tf_pid" 2>/dev/null
tf_bg=$?
printf '{"subshell": "%s", "sh": "%s", "bg_kill_status": %d}\n' \
    "$tf_sub" "$tf_sh" "$tf_bg"
