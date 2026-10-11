"""escape probe E29 (mcp_sandbox): the files[] a run was given.

Runs only when the request was ACCEPTED (a `..` or absolute files[] path is
refused before any workdir exists). Prints ONE JSON line: the cwd's entries.
"""
import json
import os


def main():
    print(json.dumps({"cwd_files": sorted(os.listdir("."))}, sort_keys=True))


if __name__ == "__main__":
    main()
