"""escape probe E20 (mcp_sandbox): look for a controlling terminal.

Opens /dev/tty (a controlling tty is what TIOCSTI injection needs) and asks
isatty() of fds 0-2. Prints ONE JSON line: "ok" or the errno name for the
open, and the three isatty answers.
"""
import errno
import json
import os


def main():
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        os.close(fd)
        dev_tty = "ok"
    except OSError as exc:
        dev_tty = errno.errorcode.get(exc.errno, str(exc.errno))
    print(json.dumps({"dev_tty": dev_tty,
                      "isatty": [os.isatty(fd) for fd in (0, 1, 2)]},
                     sort_keys=True))


if __name__ == "__main__":
    main()
