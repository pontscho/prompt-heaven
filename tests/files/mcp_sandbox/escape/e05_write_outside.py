"""escape probe E5 (mcp_sandbox): create files outside the workdir.

argv: target paths. Each is created O_CREAT|O_EXCL (and removed again when
that succeeded). Prints ONE JSON line: "ok" or the errno name per target, and
the in-run control `write_cwd` -- a file in the cwd (the workdir).
"""
import errno
import json
import os
import sys


def tf_create(path):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
    os.write(fd, b"e05")
    os.close(fd)
    os.unlink(path)
    return "ok"


def main():
    out = {"write": [tf_create(p) for p in sys.argv[1:]],
           "write_cwd": tf_create("e05-cwd.txt")}
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
