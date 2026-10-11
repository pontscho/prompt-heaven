"""escape probe E1 (mcp_sandbox): read ~/.ssh from inside a run.

argv: <ssh dir> <a file in it>. Prints ONE JSON line: per attempt "ok" or
the errno name -- never a byte of what was read. `read_own` (this file) is
the in-run control: a read the profile grants.
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


def tf_read(path):
    with open(path, "rb") as fh:
        fh.read(1)


def main():
    ssh_dir, ssh_file = sys.argv[1:3]
    out = {
        "list_ssh": tf_try(lambda: os.listdir(ssh_dir)),
        "read_ssh_file": tf_try(lambda: tf_read(ssh_file)),
        "read_own": tf_try(lambda: tf_read(os.path.abspath(__file__))),
    }
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
