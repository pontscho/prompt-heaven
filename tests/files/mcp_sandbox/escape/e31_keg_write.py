"""escape probe E31 (mcp_sandbox): write into the interpreter's own keg.

argv: [<dir>... -- <file>...]; with no argv the targets are the running
interpreter's: the directory of its realpath and of its stdlib (create a
file), and the realpath and os.py (open for append -- nothing is written).
A created file is removed again. Prints ONE JSON line: the targets and "ok"
or the errno name per attempt, plus the in-run control `create_cwd`.
"""
import errno
import json
import os
import sys


def tf_name(exc):
    return errno.errorcode.get(exc.errno, str(exc.errno))


def tf_create(directory):
    path = os.path.join(directory, ".e31-%d" % os.getpid())
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        return tf_name(exc)
    os.close(fd)
    os.unlink(path)
    return "ok"


def tf_append(path):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError as exc:
        return tf_name(exc)
    os.close(fd)
    return "ok"


def main():
    args = sys.argv[1:]
    if args:
        cut = args.index("--") if "--" in args else len(args)
        dirs, files = args[:cut], args[cut + 1:]
    else:
        real = os.path.realpath(sys.executable)
        stdlib = os.path.dirname(os.path.realpath(os.__file__))
        dirs = [os.path.dirname(real), stdlib]
        files = [real, os.path.realpath(os.__file__)]
    out = {
        "dirs": dirs, "files": files,
        "create": [tf_create(d) for d in dirs],
        "append": [tf_append(f) for f in files],
        "create_cwd": tf_create("."),
    }
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
