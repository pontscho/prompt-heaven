"""escape probe E19 (mcp_sandbox): which file descriptors reached the script.

os.fstat over fds 0-255 -- NOT a /dev/fd listing, which the restricted
profile denies (item-3a-g.md 3f); that listing's outcome is reported too.
Prints ONE JSON line: the open fds and "ok"/errno for the listing.
"""
import errno
import json
import os


def main():
    open_fds = []
    for fd in range(256):
        try:
            os.fstat(fd)
        except OSError:
            continue
        open_fds.append(fd)
    try:
        os.listdir("/dev/fd")
        dev_fd = "ok"
    except OSError as exc:
        dev_fd = errno.errorcode.get(exc.errno, str(exc.errno))
    print(json.dumps({"open_fds": open_fds, "dev_fd": dev_fd},
                     sort_keys=True))


if __name__ == "__main__":
    main()
