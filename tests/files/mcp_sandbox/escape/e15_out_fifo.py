"""escape probe E15 (mcp_sandbox): plant a FIFO in $OUT.

A FIFO nobody writes would hang a collector that opens it blocking. Creates
out/fifo and one regular file out/x.txt (the collection control). Prints ONE
JSON line: "ok" or the errno name for the mkfifo.
"""
import errno
import json
import os


def main():
    out_dir = os.environ.get("OUT", "out")
    os.makedirs(out_dir, exist_ok=True)
    try:
        os.mkfifo(os.path.join(out_dir, "fifo"))
        res = {"mkfifo": "ok"}
    except OSError as exc:
        res = {"mkfifo": errno.errorcode.get(exc.errno, str(exc.errno))}
    with open(os.path.join(out_dir, "x.txt"), "w") as fh:
        fh.write("e15-x")
    print(json.dumps(res, sort_keys=True))


if __name__ == "__main__":
    main()
