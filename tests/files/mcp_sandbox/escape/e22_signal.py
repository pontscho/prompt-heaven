"""escape probe E22 (mcp_sandbox): signal processes outside the script.

argv: <runner pid> <sibling pid> (either may be 0 = skip). Sends SIGWINCH
(default action: ignore -- harmless if it lands) to the runner, to this
process's parent (sbx's supervising parent) and to a concurrent sibling
run's target; then SIGTERMs its OWN background job, which must work.
Prints ONE JSON line: "ok" or the errno name per target, and the background
job's wait status.
"""
import errno
import json
import os
import signal
import subprocess
import sys


def tf_kill(pid, sig):
    if pid <= 0:
        return "skipped"
    try:
        os.kill(pid, sig)
        return "ok"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


def main():
    runner, sibling = int(sys.argv[1]), int(sys.argv[2])
    out = {
        "runner": tf_kill(runner, signal.SIGWINCH),
        "parent": tf_kill(os.getppid(), signal.SIGWINCH),
        "sibling": tf_kill(sibling, signal.SIGWINCH),
    }
    job = subprocess.Popen(["/bin/sleep", "30"], stdin=subprocess.DEVNULL)
    out["own_job"] = tf_kill(job.pid, signal.SIGTERM)
    if out["own_job"] != "ok":
        job.kill()
    out["own_job_status"] = job.wait()
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
