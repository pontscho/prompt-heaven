"""escape probe E2 (mcp_sandbox): read the canary store root from a run.

argv: <secret file>. The canary directory under .claude/tmp/ stands in for
the store root. Prints ONE JSON line: "ok" or the errno name per attempt and
the byte COUNT read -- never a byte of the secret itself.
"""
import errno
import json
import os
import sys


def tf_name(exc):
    return errno.errorcode.get(exc.errno, str(exc.errno))


def main():
    secret = sys.argv[1]
    out = {"size": None}
    try:
        with open(secret, "rb") as fh:
            out["size"] = len(fh.read())
        out["read_secret"] = "ok"
    except OSError as exc:
        out["read_secret"] = tf_name(exc)
    try:
        out["list_store"] = len(os.listdir(os.path.dirname(secret)))
    except OSError as exc:
        out["list_store"] = tf_name(exc)
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
