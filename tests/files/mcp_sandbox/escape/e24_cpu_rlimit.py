"""escape probe E24 (mcp_sandbox): the CPU limit the script runs under.

Prints ONE JSON line: RLIMIT_CPU as [soft, hard] (RLIM_INFINITY as -1).
"""
import json
import resource


def tf_lim(res):
    return [-1 if v == resource.RLIM_INFINITY else v
            for v in resource.getrlimit(res)]


def main():
    print(json.dumps({"cpu": tf_lim(resource.RLIMIT_CPU)}, sort_keys=True))


if __name__ == "__main__":
    main()
