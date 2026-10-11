"""escape probe E21 (mcp_sandbox): exec what the script itself planted.

argv: [<planted binary name in the cwd>]. Executes the planted binary (when
named) with --version, writes and executes a `#!/bin/sh` script, and, as the
in-run control, executes /bin/echo (an allowed exec). Prints ONE JSON line:
"ok", "exec <errno>" or "rc N" per exec.
"""
import errno
import json
import os
import subprocess
import sys


def tf_run(argv):
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    except OSError as exc:
        return "exec " + errno.errorcode.get(exc.errno, str(exc.errno))
    return "ok" if proc.returncode == 0 else "rc %d" % proc.returncode


def main():
    out = {}
    if len(sys.argv) > 1:
        planted = os.path.abspath(sys.argv[1])
        os.chmod(planted, 0o755)
        out["planted_binary"] = tf_run([planted, "--version"])
    script = os.path.abspath("planted.sh")
    with open(script, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(script, 0o755)
    out["planted_script"] = tf_run([script])
    out["bin_echo"] = tf_run(["/bin/echo", "e21"])
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
