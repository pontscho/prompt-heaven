"""tf probe: sbx_restrict group L (L1-L8, L11) -- run INSIDE a restricted sbx.

Prints ONE JSON line naming each attempt's result: "ok" or the errno name
(never a byte of what was read). The cwd is the write scope.

argv: <known_hosts> <home> <repo CLAUDE.md> <planted executable> then the
stat-oracle pairs: <existing> <missing> under a directory NOT granted, and
<existing> <missing> directly under a metadata-granted ancestor of the scope.
"""
import ctypes
import errno
import json
import os
import shutil
import stat
import subprocess
import sys

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


def tf_read(path):
    with open(path, "rb") as fh:
        fh.read(1)


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
    # the tool's own diagnostic (a path in the write scope at most)
    lines = proc.stderr.strip().splitlines() or [""]
    return "rc %d: %s" % (proc.returncode, lines[0][:120])


def main():
    known_hosts, home, repo_file, planted = sys.argv[1:5]
    out = {"python": "ok"}
    out["known_hosts"] = tf_try(lambda: tf_read(known_hosts))
    out["home_listing"] = tf_try(lambda: os.listdir(home))
    out["etc_passwd"] = tf_try(lambda: tf_read("/etc/passwd"))
    out["repo_file"] = tf_try(lambda: tf_read(repo_file))
    for key, path in zip(("stat_existing", "stat_missing",
                          "stat_existing_child", "stat_missing_child"),
                         sys.argv[5:9]):
        out[key] = tf_try(lambda p=path: os.stat(p))
    out["exec_planted"] = tf_run([planted])
    try:
        out["root_listing"] = len(os.listdir("/"))
    except OSError as exc:
        out["root_listing"] = tf_name(exc)
    # L11: inside the write scope (the cwd)
    for name in ("tf_acl_cmd", "tf_acl_lib", "tf_uchg", "tf_copy", "tf_plain"):
        with open(name, "w") as fh:
            fh.write("x")
    os.mkdir("tf_tree")
    with open(os.path.join("tf_tree", "f"), "w") as fh:
        fh.write("x")
    out["chmod_acl"] = tf_run(["/bin/chmod", "+a", "everyone deny delete",
                               "tf_acl_cmd"])
    out["acl_set_file"] = tf_acl_set_file("tf_acl_lib")
    out["chflags_uchg"] = tf_try(lambda: os.chflags("tf_uchg",
                                                    stat.UF_IMMUTABLE))
    out["copy2"] = tf_try(lambda: shutil.copy2("tf_copy", "tf_copy2"))
    out["copytree"] = tf_try(lambda: shutil.copytree("tf_tree", "tf_tree2"))

    def plain():
        with open("tf_plain", "w") as fh:
            fh.write("y")
        os.chmod("tf_plain", 0o755)
    out["write_chmod_x"] = tf_try(plain)
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
