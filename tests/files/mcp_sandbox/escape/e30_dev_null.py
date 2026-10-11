"""escape probe E30 (mcp_sandbox): /dev/null is usable, /dev/zero is not.

Reads and writes /dev/null, runs a child with stdin/stdout DEVNULL (opened
O_RDWR by subprocess), and -- the negative control that shows the probe sees
a denial -- reads /dev/zero, which nothing grants. Prints ONE JSON line: "ok"
or the errno name per attempt.
"""
import errno
import json
import os
import subprocess


def tf_try(fn):
    try:
        fn()
        return "ok"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


def tf_rw(path, flags):
    fd = os.open(path, flags)
    try:
        if flags & (os.O_WRONLY | os.O_RDWR):
            os.write(fd, b"e30")
        else:
            os.read(fd, 1)
    finally:
        os.close(fd)


def tf_child():
    rc = subprocess.run(["/bin/echo", "e30"], stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL).returncode
    if rc != 0:
        raise OSError(errno.EIO, "rc %d" % rc)


def main():
    out = {
        "read": tf_try(lambda: tf_rw("/dev/null", os.O_RDONLY)),
        "write": tf_try(lambda: tf_rw("/dev/null", os.O_WRONLY)),
        "rdwr": tf_try(lambda: tf_rw("/dev/null", os.O_RDWR)),
        "child": tf_try(tf_child),
        "dev_zero": tf_try(lambda: tf_rw("/dev/zero", os.O_RDONLY)),
    }
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
