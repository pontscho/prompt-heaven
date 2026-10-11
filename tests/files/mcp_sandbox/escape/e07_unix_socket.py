"""escape probe E7 (mcp_sandbox): connect to a unix-domain socket.

argv: <socket path> (the test's own listener). Prints ONE JSON line: "ok" or
the errno name for the connect, and for a bind of a new socket in the cwd.
"""
import errno
import json
import os
import socket
import sys


def tf_name(exc):
    if isinstance(exc, socket.timeout):
        return "timeout"
    return errno.errorcode.get(exc.errno, str(exc.errno))


def main():
    out = {}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(sys.argv[1])
            sock.sendall(b"e07")
        out["unix_connect"] = "ok"
    except OSError as exc:
        out["unix_connect"] = tf_name(exc)
    path = "e07.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(path)
        out["unix_bind_cwd"] = "ok"
    except OSError as exc:
        out["unix_bind_cwd"] = tf_name(exc)
    finally:
        if os.path.lexists(path):
            os.unlink(path)
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
