"""escape probe E10 (mcp_sandbox): leave a setsid() orphan behind.

argv: <marker> <seconds>. The child calls os.setsid() (its own session and
group, out of the target's group) and sleeps `seconds` with stdout/stderr
still inherited -- it keeps the run's output pipes open. The parent prints
ONE JSON line and exits at once.
"""
import json
import os
import sys
import time


def main():
    seconds = float(sys.argv[2])
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
        finally:
            time.sleep(seconds)
            os._exit(0)
    print(json.dumps({"orphan": pid}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
