"""escape probe E26 (mcp_sandbox): open descriptors until the limit.

argv: <cap>. Opens /dev/null up to `cap` times; stops at the first failure.
Prints ONE JSON line: how many opened, why it stopped ("cap" or the errno
name) and RLIMIT_NOFILE.
"""
import errno
import json
import os
import resource
import sys


def main():
    cap = int(sys.argv[1])
    fds = []
    stopped = "cap"
    try:
        while len(fds) < cap:
            fds.append(os.open("/dev/null", os.O_RDONLY))
    except OSError as exc:
        stopped = errno.errorcode.get(exc.errno, str(exc.errno))
    for fd in fds:
        os.close(fd)
    lim = [-1 if v == resource.RLIM_INFINITY else v
           for v in resource.getrlimit(resource.RLIMIT_NOFILE)]
    print(json.dumps({"opened": len(fds), "stopped": stopped, "nofile": lim},
                     sort_keys=True))


if __name__ == "__main__":
    main()
