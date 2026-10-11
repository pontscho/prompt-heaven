# escape probe E34 (mcp_sandbox), bash: exec setuid-root binaries.
# Each runs through /bin/sh -c with stdin /dev/null (M0:10, item-3k.md);
# /usr/bin/true (NOT setuid) is the control exec. Prints ONE JSON line: per
# binary sh's exit code and whether stderr said "Operation not permitted" --
# never the stderr text itself.
tf_try() {
    local err rc eperm=false
    err=$(/bin/sh -c "$1" </dev/null 2>&1 >/dev/null)
    rc=$?
    case "$err" in
        *"Operation not permitted"*) eperm=true ;;
    esac
    printf '"%s": {"rc": %d, "eperm": %s}' "$2" "$rc" "$eperm"
}
printf '{%s, %s, %s, %s}\n' \
    "$(tf_try '/usr/bin/crontab -l' crontab)" \
    "$(tf_try '/usr/bin/at -l' at)" \
    "$(tf_try '/usr/bin/sudo -n true' sudo)" \
    "$(tf_try '/usr/bin/true' true)"
