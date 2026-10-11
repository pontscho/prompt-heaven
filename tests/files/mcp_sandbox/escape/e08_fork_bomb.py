"""escape probe E8 (mcp_sandbox): fork until the kernel says no.

argv: <cap>. Forks children that sleep, up to `cap`; stops at the first fork
failure. Every child is then SIGKILLed and reaped by the probe itself. Prints
ONE JSON line: how many forks succeeded and why it stopped ("cap" or the
errno name).
"""
import errno
import json
import os
import signal
import sys
import time


def main():
    cap = int(sys.argv[1])
    children = []
    stopped = "cap"
    while len(children) < cap:
        try:
            pid = os.fork()
        except OSError as exc:
            stopped = errno.errorcode.get(exc.errno, str(exc.errno))
            break
        if pid == 0:
            time.sleep(30)
            os._exit(0)
        children.append(pid)
    for pid in children:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for pid in children:
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
    print(json.dumps({"forked": len(children), "stopped": stopped},
                     sort_keys=True))


if __name__ == "__main__":
    main()
