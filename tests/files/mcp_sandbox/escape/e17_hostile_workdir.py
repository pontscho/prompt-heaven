"""escape probe E17 (mcp_sandbox): make the workdir hard to delete.

argv: <mode> [extra]. Every mode works in the cwd (the workdir) and prints
ONE JSON line, "ok" or the errno name per attempt:
  ok        print and exit 0
  exit N    exit N
  sleep S   sleep S seconds (the run's timeout ends it)
  chmod000  a nested tree whose files and directories are all mode 000
  uchg      chflags uchg on a file and on a directory (os.chflags and
            /usr/bin/chflags)
  acl       "everyone deny delete" on a file and a subdirectory, through
            /bin/chmod +a and through acl_set_file (darwin)
"""
import ctypes
import errno
import json
import os
import stat
import subprocess
import sys
import time

# "everyone deny delete" in acl_from_text's form (item-3l.md)
TF_ACE = (b"!#acl 1\ngroup:ABCDEFAB-CDEF-ABCD-EFAB-CDEF0000000C:"
          b"everyone:12:deny:delete\n")
TF_ACL_TYPE_EXTENDED = 0x100


def tf_name(exc):
    return errno.errorcode.get(exc.errno, str(exc.errno))


def tf_try(fn):
    try:
        fn()
        return "ok"
    except OSError as exc:
        return tf_name(exc)


def tf_write(path, data="x"):
    with open(path, "w") as fh:
        fh.write(data)


def tf_run(argv):
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, universal_newlines=True)
    except OSError as exc:
        return "exec " + tf_name(exc)
    if proc.returncode == 0:
        return "ok"
    if "Operation not permitted" in proc.stderr:
        return "EPERM"
    lines = proc.stderr.strip().splitlines() or [""]
    return "rc %d: %s" % (proc.returncode, lines[0][:120])


def tf_acl_set_file(path):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_from_text.restype = ctypes.c_void_p
    libc.acl_from_text.argtypes = [ctypes.c_char_p]
    libc.acl_set_file.restype = ctypes.c_int
    libc.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                  ctypes.c_void_p]
    acl = libc.acl_from_text(TF_ACE)
    if not acl:
        return "acl_from_text failed"
    if libc.acl_set_file(path.encode(), TF_ACL_TYPE_EXTENDED, acl) == 0:
        return "ok"
    err = ctypes.get_errno()
    return errno.errorcode.get(err, str(err))


def tf_chmod000():
    os.makedirs(os.path.join("t", "a", "b", "c"))
    dirs = ["t", "t/a", "t/a/b", "t/a/b/c"]
    for d in dirs:
        tf_write(os.path.join(d, "f"))
    for d in dirs:
        os.chmod(os.path.join(d, "f"), 0)
    for d in reversed(dirs):
        os.chmod(d, 0)
    return {"chmod000": "ok"}


def tf_uchg():
    os.makedirs(os.path.join("u", "d"))
    tf_write(os.path.join("u", "f"))
    tf_write(os.path.join("u", "d", "g"))
    tf_write(os.path.join("u", "cmd"))
    return {
        "uchg_file": tf_try(lambda: os.chflags(os.path.join("u", "f"),
                                               stat.UF_IMMUTABLE)),
        "uchg_dir": tf_try(lambda: os.chflags(os.path.join("u", "d"),
                                              stat.UF_IMMUTABLE)),
        "chflags_cmd": tf_run(["/usr/bin/chflags", "uchg",
                               os.path.join("u", "cmd")]),
    }


def tf_acl():
    os.makedirs(os.path.join("a", "d"))
    tf_write(os.path.join("a", "f"))
    tf_write(os.path.join("a", "g"))
    os.makedirs(os.path.join("a", "e"))
    return {
        "chmod_acl_file": tf_run(["/bin/chmod", "+a", "everyone deny delete",
                                  os.path.join("a", "f")]),
        "chmod_acl_dir": tf_run(["/bin/chmod", "+a", "everyone deny delete",
                                 os.path.join("a", "d")]),
        "acl_set_file": tf_acl_set_file(os.path.join("a", "g")),
        "acl_set_dir": tf_acl_set_file(os.path.join("a", "e")),
    }


def main():
    mode = sys.argv[1]
    if mode == "exit":
        print(json.dumps({"mode": "exit"}))
        sys.exit(int(sys.argv[2]))
    if mode == "sleep":
        print(json.dumps({"mode": "sleep"}), flush=True)
        time.sleep(float(sys.argv[2]))
        return
    out = {"mode": mode}
    if mode == "chmod000":
        out.update(tf_chmod000())
    elif mode == "uchg":
        out.update(tf_uchg())
    elif mode == "acl":
        out.update(tf_acl())
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
