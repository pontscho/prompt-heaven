"""escape probe E4 (mcp_sandbox): stat paths outside every grant.

argv: <existing path> <missing path>. Prints ONE JSON line: "ok" or the
errno name per stat/lstat, and the size only when the stat succeeded (that
is the oracle the row denies).
"""
import errno
import json
import os
import sys


def tf_stat(fn, path):
    try:
        st = fn(path)
        return "ok", st.st_size
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno)), None


def main():
    existing, missing = sys.argv[1:3]
    out = {}
    out["stat_existing"], out["size_existing"] = tf_stat(os.stat, existing)
    out["lstat_existing"], _ = tf_stat(os.lstat, existing)
    out["stat_missing"], _ = tf_stat(os.stat, missing)
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
