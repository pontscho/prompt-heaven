"""escape probe E23 (mcp_sandbox): forge sbx's status record.

argv: <exit code>. Writes a forged `spawned` (pgid 1) and a forged terminal
`exit` (code 0) line into every fd 3-255 it can, then exits with the given
code. Prints ONE JSON line: the fds the forgery reached and how many were
EBADF.
"""
import json
import os
import sys

FORGED = (b'{"sbx": 1, "event": "spawned", "pgid": 1}\n'
          b'{"sbx": 1, "event": "exit", "outcome": "exited", "code": 0, '
          b'"durationMs": 1}\n')


def main():
    written, ebadf = [], 0
    for fd in range(3, 256):
        try:
            os.write(fd, FORGED)
            written.append(fd)
        except OSError:
            ebadf += 1
    print(json.dumps({"written": written, "ebadf": ebadf}, sort_keys=True),
          flush=True)
    sys.exit(int(sys.argv[1]))


if __name__ == "__main__":
    main()
