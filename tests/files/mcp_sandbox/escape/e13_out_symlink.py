"""escape probe E13 (mcp_sandbox): plant symlinks in $OUT.

argv: <secret file>. Creates out/link-abs -> the secret, out/link-rel ->
../main.py, out/link-dir -> the secret's directory, and one regular file
out/real.txt (the collection control). Prints ONE JSON line: "ok" or the
errno name per creation.
"""
import errno
import json
import os
import sys


def tf_try(fn):
    try:
        fn()
        return "ok"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


def main():
    secret = sys.argv[1]
    out_dir = os.environ.get("OUT", "out")
    os.makedirs(out_dir, exist_ok=True)
    res = {
        "link_abs": tf_try(lambda: os.symlink(
            secret, os.path.join(out_dir, "link-abs"))),
        "link_rel": tf_try(lambda: os.symlink(
            "../main.py", os.path.join(out_dir, "link-rel"))),
        "link_dir": tf_try(lambda: os.symlink(
            os.path.dirname(secret), os.path.join(out_dir, "link-dir"))),
    }
    with open(os.path.join(out_dir, "real.txt"), "w") as fh:
        fh.write("e13-real")
    print(json.dumps(res, sort_keys=True))


if __name__ == "__main__":
    main()
