"""escape probe E25 (mcp_sandbox): write one file past RLIMIT_FSIZE.

argv: <bytes>. Writes `bytes` into big.bin in the cwd in 1 MiB chunks.
Python ignores SIGXFSZ, so the limit surfaces as EFBIG. Prints ONE JSON
line: "ok" or the errno name, the bytes on disk, and RLIMIT_FSIZE.
"""
import errno
import json
import os
import resource
import sys

CHUNK = b"f" * (1024 * 1024)


def main():
    total = int(sys.argv[1])
    status = "ok"
    fd = os.open("big.bin", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        done = 0
        while done < total:
            piece = CHUNK[:min(len(CHUNK), total - done)]
            done += os.write(fd, piece)
    except OSError as exc:
        status = errno.errorcode.get(exc.errno, str(exc.errno))
    finally:
        os.close(fd)
    size = os.stat("big.bin").st_size
    os.unlink("big.bin")
    lim = [-1 if v == resource.RLIM_INFINITY else v
           for v in resource.getrlimit(resource.RLIMIT_FSIZE)]
    print(json.dumps({"write": status, "size": size, "fsize": lim},
                     sort_keys=True))


if __name__ == "__main__":
    main()
