"""escape probe E9 (mcp_sandbox): spin forever with a child in the group.

argv: <marker> (only so the test finds both processes in ps), optionally
`--once` to print the result line and exit instead of spinning (the outside
control). Prints ONE JSON line {"started": true, "child": <pid>} first, then
the parent spins on the CPU and the child sleeps -- the run's timeout must
kill the whole group.
"""
import json
import os
import sys
import time


def main():
    pid = os.fork()
    if pid == 0:
        time.sleep(300)
        os._exit(0)
    print(json.dumps({"started": True, "child": pid}, sort_keys=True),
          flush=True)
    if "--once" in sys.argv[2:]:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        return
    while True:
        pass


if __name__ == "__main__":
    main()
