"""escape probe E18 (mcp_sandbox): hold a run slot.

argv: <marker> <seconds>. Prints ONE JSON line {"started": true} at once,
then sleeps `seconds` (the marker only lets the test find it in ps).
"""
import json
import sys
import time


def main():
    print(json.dumps({"started": True}), flush=True)
    time.sleep(float(sys.argv[2]))


if __name__ == "__main__":
    main()
