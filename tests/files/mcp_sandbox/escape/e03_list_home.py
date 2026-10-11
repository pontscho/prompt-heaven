"""escape probe E3 (mcp_sandbox): list the real home directory from a run.

argv: <home>. Prints ONE JSON line: the entry COUNT or the errno name for the
home listing and, as the in-run control, for the cwd (the workdir).
"""
import errno
import json
import os
import sys


def tf_count(path):
    try:
        return len(os.listdir(path))
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


def main():
    out = {"list_home": tf_count(sys.argv[1]), "list_cwd": tf_count(".")}
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
