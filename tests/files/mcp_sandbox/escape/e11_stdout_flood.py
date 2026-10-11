"""escape probe E11 (mcp_sandbox): flood stdout.

argv: <total bytes>. Writes `total` bytes to stdout in 1 MiB chunks, then
ONE JSON line on STDERR (stdout is the flood): the bytes written and "ok" or
the errno name that stopped it.
"""
import errno
import json
import sys

CHUNK = b"x" * (1024 * 1024)


def main():
    total = int(sys.argv[1])
    written = 0
    status = "ok"
    out = sys.stdout.buffer
    try:
        while written < total:
            piece = CHUNK[:min(len(CHUNK), total - written)]
            out.write(piece)
            written += len(piece)
        out.flush()
    except OSError as exc:
        status = errno.errorcode.get(exc.errno, str(exc.errno))
    sys.stderr.write(json.dumps({"written": written, "status": status},
                                sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
