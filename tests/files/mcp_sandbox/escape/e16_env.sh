# escape probe E16 (mcp_sandbox), bash: the environment a CHILD of the
# script sees (/usr/bin/env), so bash's own exports (PWD, SHLVL, _) count.
# Prints ONE JSON line: the KEYS only -- never a value. Absolute paths only:
# a bare-name lookup needs metadata the restricted profile may not grant.
keys=$(/usr/bin/env | /usr/bin/cut -d= -f1)
out=""
for k in $keys; do
    out="$out${out:+, }\"$k\""
done
printf '{"keys": [%s]}\n' "$out"
