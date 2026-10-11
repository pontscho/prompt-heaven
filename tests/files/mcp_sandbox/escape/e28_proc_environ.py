"""escape probe E28 (mcp_sandbox): read another process's environment.

argv: <runner pid> <key name>. Linux: /proc/<pid>/environ and
/proc/self/environ. darwin (no /proc): sysctl KERN_PROCARGS2 of the pid --
argv AND environment of a same-uid process. Prints ONE JSON line: "ok" or the
errno name per read and whether `<key name>=` was seen -- never a value.
"""
import ctypes
import errno
import json
import os
import sys

TF_CTL_KERN = 1
TF_KERN_PROCARGS2 = 49


def tf_name(err):
    return errno.errorcode.get(err, str(err))


def tf_procargs(pid):
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(TF_CTL_KERN, TF_KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return tf_name(ctypes.get_errno()), b""
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return tf_name(ctypes.get_errno()), b""
    return "ok", buf.raw[:size.value]


def tf_proc(path):
    try:
        with open(path, "rb") as fh:
            return "ok", fh.read()
    except OSError as exc:
        return tf_name(exc.errno), b""


def main():
    pid, key = int(sys.argv[1]), sys.argv[2].encode() + b"="
    if sys.platform == "darwin":
        other, data = tf_procargs(pid)
        own, _ = tf_procargs(os.getpid())
    else:
        other, data = tf_proc("/proc/%d/environ" % pid)
        own, _ = tf_proc("/proc/self/environ")
    print(json.dumps({"other": other, "own": own, "key_seen": key in data},
                     sort_keys=True))


if __name__ == "__main__":
    main()
