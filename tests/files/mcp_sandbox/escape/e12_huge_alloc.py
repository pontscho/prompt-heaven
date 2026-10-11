"""escape probe E12 (mcp_sandbox): reserve a huge amount of memory.

argv: sizes in bytes. Each is an anonymous mmap (address space, never
touched -- so an unlimited outside run costs no RAM). Prints ONE JSON line:
"ok" or the errno / exception name per size.
"""
import errno
import json
import mmap
import sys


def tf_map(size):
    try:
        m = mmap.mmap(-1, size)
    except MemoryError:
        return "MemoryError"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
    m.close()
    return "ok"


def main():
    out = {"map": [tf_map(int(a)) for a in sys.argv[1:]]}
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
