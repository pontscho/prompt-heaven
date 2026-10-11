"""tf probe: sbx_restrict L12, the restricted Seatbelt depth cap (M0:9,
.claude/tmp/m0/item-depth.md sections 2-4) -- run INSIDE a restricted sbx.

argv: <write scope> (its canonical spelling: the cap is anchored on it).
Builds a `d/` chain to level 100 under the write scope, then tries level 101
by absolute path, *at-relative (dir_fd=) and chdir-relative; a regular file
inside the level-100 directory; an os.rename of a level-1 directory whose
DESTINATION is at level 101; and a control chain `c/` at levels 1-10.
Prints ONE JSON line, per form "ok" or the errno name. Nothing is removed:
the caller removes the chains as the owner. Unsandboxed every form is "ok".
"""
import errno
import json
import os
import sys

TF_LEVELS = 100


def tf_name(exc):
    return errno.errorcode.get(exc.errno, str(exc.errno))


def tf_try(fn):
    try:
        fn()
        return "ok"
    except OSError as exc:
        return tf_name(exc)


def tf_chain(root, name, levels):
    """mkdir root/name/name/... `levels` deep: (deepest made, path, error)."""
    path = root
    for level in range(1, levels + 1):
        nxt = os.path.join(path, name)
        try:
            os.mkdir(nxt)
        except OSError as exc:
            return level - 1, path, tf_name(exc)
        path = nxt
    return levels, path, None


def main():
    root = sys.argv[1]
    out = {}
    made, deep, err = tf_chain(root, "d", TF_LEVELS)
    out["level100"] = "ok" if err is None else "%s at %d" % (err, made + 1)
    out["deepest"] = made
    out["abs101"] = tf_try(lambda: os.mkdir(os.path.join(deep, "a")))
    fd = os.open(deep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        out["at101"] = tf_try(lambda: os.mkdir("t", dir_fd=fd))
    finally:
        os.close(fd)

    def file_in_100():
        os.close(os.open(os.path.join(deep, "f"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    out["file_in_100"] = tf_try(file_in_100)
    mover = os.path.join(root, "m")
    os.mkdir(mover)
    out["rename_to_101"] = tf_try(
        lambda: os.rename(mover, os.path.join(deep, "m")))
    made_c, _path, err_c = tf_chain(root, "c", 10)
    out["control_1_10"] = "ok" if err_c is None else "%s at %d" % (
        err_c, made_c + 1)
    os.chdir(deep)
    out["chdir101"] = tf_try(lambda: os.mkdir("r"))
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
