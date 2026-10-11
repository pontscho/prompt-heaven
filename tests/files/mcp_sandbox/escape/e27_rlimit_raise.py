"""escape probe E27 (mcp_sandbox): raise the limits the script runs under.

For AS, CPU, NPROC, FSIZE, NOFILE and CORE: read [soft, hard], then try to
set both to RLIM_INFINITY. Prints ONE JSON line: the limits (RLIM_INFINITY
as -1) and "ok", "ValueError" or the errno name per raise.
"""
import errno
import json
import resource

TF_LIMITS = ("AS", "CPU", "NPROC", "FSIZE", "NOFILE", "CORE")


def tf_lim(res):
    return [-1 if v == resource.RLIM_INFINITY else v
            for v in resource.getrlimit(res)]


def main():
    limits, raise_ = {}, {}
    for name in TF_LIMITS:
        res = getattr(resource, "RLIMIT_" + name)
        limits[name] = tf_lim(res)
        inf = resource.RLIM_INFINITY
        try:
            resource.setrlimit(res, (inf, inf))
            raise_[name] = "ok"
        except ValueError:
            raise_[name] = "ValueError"
        except OSError as exc:
            raise_[name] = errno.errorcode.get(exc.errno, str(exc.errno))
    print(json.dumps({"limits": limits, "raise": raise_}, sort_keys=True))


if __name__ == "__main__":
    main()
