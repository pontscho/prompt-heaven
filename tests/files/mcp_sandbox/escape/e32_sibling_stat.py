"""escape probe E32 (mcp_sandbox): stat siblings of the workdir's ancestors.

argv: <ancestor> -- <sibling>... The ancestor's stat is the in-run control
(the profile grants every ancestor's metadata as a literal); each sibling is
stat'ed and read. Prints ONE JSON line: "ok" or the errno name per attempt.
"""
import errno
import json
import os
import sys


def tf_try(fn):
    try:
        fn()
        return "ok"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


def tf_read(path):
    with open(path, "rb") as fh:
        fh.read(1)


def main():
    args = sys.argv[1:]
    cut = args.index("--")
    ancestors, siblings = args[:cut], args[cut + 1:]
    out = {
        "ancestor_stat": [tf_try(lambda p=p: os.stat(p)) for p in ancestors],
        "sibling_stat": [tf_try(lambda p=p: os.stat(p)) for p in siblings],
        "sibling_read": [tf_try(lambda p=p: tf_read(p)) for p in siblings
                         if not os.path.isdir(p)],
    }
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
