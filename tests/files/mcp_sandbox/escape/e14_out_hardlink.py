"""escape probe E14 (mcp_sandbox): plant hard links in $OUT.

argv: <secret file>. Hard-links a workdir file into out/hl.txt (st_nlink 2),
tries to hard-link the secret into out/hl-secret, and writes one regular
file out/single.txt (the collection control). Prints ONE JSON line: "ok" or
the errno name per link.
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


def main():
    secret = sys.argv[1]
    out_dir = os.environ.get("OUT", "out")
    os.makedirs(out_dir, exist_ok=True)
    with open("h.txt", "w") as fh:
        fh.write("e14-linked")
    res = {
        "link_wd": tf_try(lambda: os.link("h.txt",
                                          os.path.join(out_dir, "hl.txt"))),
        "link_secret": tf_try(lambda: os.link(
            secret, os.path.join(out_dir, "hl-secret"))),
    }
    with open(os.path.join(out_dir, "single.txt"), "w") as fh:
        fh.write("e14-single")
    print(json.dumps(res, sort_keys=True))


if __name__ == "__main__":
    main()
