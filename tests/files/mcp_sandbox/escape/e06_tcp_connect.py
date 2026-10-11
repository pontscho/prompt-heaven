"""escape probe E6 (mcp_sandbox): reach a loopback listener over TCP / UDP.

argv: <host> <tcp port> <udp port>. The ONLY network this probe touches is
the test's own loopback listeners. Prints ONE JSON line: "ok", "timeout" or
the errno name per attempt.
"""
import errno
import json
import socket
import sys


def tf_name(exc):
    if isinstance(exc, socket.timeout):
        return "timeout"
    return errno.errorcode.get(exc.errno, str(exc.errno))


def tf_tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"e06-tcp")
        return "ok"
    except OSError as exc:
        return tf_name(exc)


def tf_udp(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"e06-udp", (host, port))
        return "ok"
    except OSError as exc:
        return tf_name(exc)


def main():
    host, tcp_port, udp_port = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    out = {"tcp_connect": tf_tcp(host, tcp_port),
           "udp_send": tf_udp(host, udp_port)}
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
