"""escape probe E16 (mcp_sandbox), python3: the script's environment.

Prints ONE JSON line: the sorted environment KEYS -- never a value.
"""
import json
import os


def main():
    print(json.dumps({"keys": sorted(os.environ)}, sort_keys=True))


if __name__ == "__main__":
    main()
