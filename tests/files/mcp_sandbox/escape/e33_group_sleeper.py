"""escape probe E33 (mcp_sandbox): a two-process group that outlives sbx.

argv: <marker> <seconds>. Forks one child (same process group) and both
sleep `seconds`. Prints ONE JSON line {"started": true, "child": <pid>}
first; the test then SIGKILLs sbx, and the runner must kill this group.
"""
import json
import os
import sys
import time


def main():
    seconds = float(sys.argv[2])
    pid = os.fork()
    if pid == 0:
        time.sleep(seconds)
        os._exit(0)
    print(json.dumps({"started": True, "child": pid}, sort_keys=True),
          flush=True)
    time.sleep(seconds)
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


if __name__ == "__main__":
    main()
