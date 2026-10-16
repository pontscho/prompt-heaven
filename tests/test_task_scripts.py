#!/usr/bin/env python3
"""Offline suite for the task-script family's per-feature `--yaml PATH`.

Scripts/task-update.py, Scripts/task-show-details.py and
Scripts/task-implementation-plan.py all used to read a requirements.yaml they
located themselves (cwd, or a walk up from it).  A feature that keeps its own
YAML elsewhere had no way to say so, and task-implementation-plan.py had a
quieter hazard: `--yaml other.yaml` still injected the DEFAULT
docs/feature-implementation-plan.md, i.e. another feature's plan document
printed in front of this feature's tasks.

Every case runs the real script as a child process, with its cwd set to a
fixture directory that holds a DECOY requirements.yaml (and a decoy plan
document): a script that ignores --yaml and falls back to the cwd is caught by
the decoy changing or showing up, not by an exit code.

Groups:
  A  task-update.py: --yaml / -y / --yaml=, position independence, the default
     unchanged, an unreadable file as one stderr line, the old exit codes
  B  task-show-details.py: --yaml, no walk-up when it is given, the default
     walk-up unchanged
  C  task-implementation-plan.py: --yaml without --doc skips the default plan
     document and warns once, --doc injects exactly that one, --no-doc is
     silent, the default unchanged, --doc with --no-doc is a usage error
  D  the deployed copy under ClaudeCode/scripts is byte-identical
  E  hygiene: the repo tree and bytecode untouched

Usage:
  python3 tests/test_task_scripts.py
  python3 tests/test_task_scripts.py --brief
Exit code 0 iff every non-informational case passes.
"""

import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "task_scripts"
UPDATE = H.repo_path("Scripts", "task-update.py")
SHOW = H.repo_path("Scripts", "task-show-details.py")
PLAN = H.repo_path("Scripts", "task-implementation-plan.py")
PLAN_COPY = H.repo_path("ClaudeCode", "scripts", "task-implementation-plan.py")

YAML_TMPL = """complete: false
context_summary: |
  %(tag)s context
success_criteria:
  - %(tag)s criterion
implementation_plan:
  affected_files:
    - x.py
  tasks:
    - task_id: task-001
      status: pending
      title: %(tag)s first
    - task_id: task-002
      status: pending
      title: %(tag)s second
"""

DEFAULT_DOC = "DEFAULT-DOC-MARKER"
OTHER_DOC = "EXPLICIT-DOC-MARKER"


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def py(script, args, cwd):
    return H.run_process([sys.executable, script] + list(args), cwd=cwd)


def status_of(text, task_id):
    """The status value of one task block in a fixture YAML."""
    block = text.split("task_id: %s" % task_id, 1)[1]
    return block.split("status:", 1)[1].split("\n", 1)[0].strip()


def lines(text):
    return [ln for ln in text.splitlines() if ln.strip()]


def fixture(ws):
    """cwd holds a DECOY requirements.yaml + decoy plan doc; feat/ holds the
    feature's own YAML and plan document."""
    ws.write_text("requirements.yaml", YAML_TMPL % {"tag": "DECOY"})
    ws.write_text("docs/feature-implementation-plan.md", DEFAULT_DOC + "\n")
    ws.write_text("feat/requirements.yaml", YAML_TMPL % {"tag": "FEAT"})
    ws.write_text("feat/plan.md", OTHER_DOC + "\n")
    return {
        "cwd": ws.path,
        "decoy": ws.join("requirements.yaml"),
        "feat": ws.join("feat", "requirements.yaml"),
        "doc": ws.join("feat", "plan.md"),
        "missing": ws.join("feat", "nope.yaml"),
    }


def reset(fx):
    with open(fx["decoy"], "w", encoding="utf-8") as fh:
        fh.write(YAML_TMPL % {"tag": "DECOY"})
    with open(fx["feat"], "w", encoding="utf-8") as fh:
        fh.write(YAML_TMPL % {"tag": "FEAT"})


def one_line_error(rc, out, err):
    """The refusal contract: exit 1, exactly one stderr line, no traceback."""
    p = []
    if rc != 1:
        p.append("rc %r, want 1" % rc)
    if len(lines(err)) != 1:
        p.append("stderr is %d lines, want 1: %r" % (len(lines(err)), err[:200]))
    if "Traceback" in out or "Traceback" in err:
        p.append("a traceback reached the caller")
    return p


def group_a(suite, fx):
    for cid, args in (
        ("update-yaml-long", ["--yaml", fx["feat"], "completed", "task-001"]),
        ("update-yaml-short", ["-y", fx["feat"], "completed", "task-001"]),
        ("update-yaml-equals", ["--yaml=" + fx["feat"], "completed", "task-001"]),
        ("update-yaml-last", ["completed", "task-001", "--yaml", fx["feat"]]),
        ("update-yaml-between", ["completed", "--yaml", fx["feat"], "task-001"]),
    ):
        reset(fx)
        rc, out, err = py(UPDATE, args, fx["cwd"])
        p = []
        if rc != 0:
            p.append("rc %r: %s" % (rc, err[:200]))
        if status_of(read(fx["feat"]), "task-001") != "completed":
            p.append("the named file was not updated")
        if status_of(read(fx["feat"]), "task-002") != "pending":
            p.append("a task that was not named changed")
        if read(fx["decoy"]) != YAML_TMPL % {"tag": "DECOY"}:
            p.append("the decoy ./requirements.yaml was touched")
        suite.record("A", cid, p)

    reset(fx)
    rc, out, err = py(UPDATE, ["completed", "task-001"], fx["cwd"])
    p = []
    if rc != 0:
        p.append("rc %r: %s" % (rc, err[:200]))
    if status_of(read(fx["decoy"]), "task-001") != "completed":
        p.append("the default (./requirements.yaml) was not updated")
    if read(fx["feat"]) != YAML_TMPL % {"tag": "FEAT"}:
        p.append("a file nobody named was touched")
    suite.record("A", "update-default-unchanged", p)

    reset(fx)
    rc, out, err = py(UPDATE, ["--yaml", fx["missing"], "completed", "task-001"],
                      fx["cwd"])
    suite.record("A", "update-missing-yaml-one-line",
                 one_line_error(rc, out, err))

    reset(fx)
    rc, out, err = py(UPDATE, ["--yaml", fx["missing"], "completed", "task-001"],
                      fx["cwd"])
    p = []
    if read(fx["decoy"]) != YAML_TMPL % {"tag": "DECOY"}:
        p.append("a missing --yaml file fell back to ./requirements.yaml")
    suite.record("A", "update-missing-yaml-no-fallback", p)

    rc, out, err = py(UPDATE, ["--yaml"], fx["cwd"])
    suite.record("A", "update-yaml-without-value",
                 [] if rc != 0 and "Traceback" not in err + out
                 else ["rc %r, traceback=%r" % (rc, "Traceback" in err + out)])

    reset(fx)
    rc, out, err = py(UPDATE, ["bogus", "task-001", "--yaml", fx["feat"]],
                      fx["cwd"])
    p = []
    if rc != 1 or "Invalid status" not in out:
        p.append("an invalid status is no longer rc 1 + message: %r %r" % (rc, out[:120]))
    suite.record("A", "update-invalid-status-unchanged", p)

    reset(fx)
    rc, out, err = py(UPDATE, ["completed", "task-999", "--yaml", fx["feat"]],
                      fx["cwd"])
    p = []
    if rc != 1 or "task-999" not in out:
        p.append("an unknown task is no longer rc 1 naming it: %r %r" % (rc, out[:120]))
    suite.record("A", "update-unknown-task-unchanged", p)

    rc, out, err = py(UPDATE, ["completed"], fx["cwd"])
    suite.record("A", "update-usage-unchanged",
                 [] if rc == 1 and "Usage" in out
                 else ["rc %r out %r" % (rc, out[:120])])


def group_b(suite, fx):
    rc, out, err = py(SHOW, ["--yaml", fx["feat"], "task-001"], fx["cwd"])
    p = []
    if rc != 0:
        p.append("rc %r: %s" % (rc, err[:200]))
    if "FEAT first" not in out:
        p.append("the named file's task is not shown")
    if "DECOY" in out:
        p.append("the cwd decoy leaked into the output")
    suite.record("B", "show-yaml-long", p)

    rc, out, err = py(SHOW, ["task-002", "-y", fx["feat"]], fx["cwd"])
    p = []
    if rc != 0 or "FEAT second" not in out or "DECOY" in out:
        p.append("-y after the id: rc %r out %r" % (rc, out[:160]))
    suite.record("B", "show-yaml-short-last", p)

    rc, out, err = py(SHOW, ["--yaml=" + fx["feat"], "task-001"], fx["cwd"])
    p = []
    if rc != 0 or "FEAT first" not in out or "DECOY" in out:
        p.append("--yaml=PATH: rc %r out %r" % (rc, out[:160]))
    suite.record("B", "show-yaml-equals", p)

    rc, out, err = py(SHOW, ["--yaml", fx["missing"], "task-001"], fx["cwd"])
    p = one_line_error(rc, out, err)
    if "DECOY" in out:
        p.append("a missing --yaml file walked up to ./requirements.yaml")
    suite.record("B", "show-missing-yaml-no-walk-up", p)

    rc, out, err = py(SHOW, ["task-001"], fx["cwd"])
    p = []
    if rc != 0 or "DECOY first" not in out:
        p.append("default no longer reads ./requirements.yaml: rc %r" % rc)
    suite.record("B", "show-default-unchanged", p)

    sub = os.path.join(fx["cwd"], "feat", "sub")
    os.makedirs(sub, exist_ok=True)
    rc, out, err = py(SHOW, ["task-001"], sub)
    p = []
    if rc != 0 or "FEAT first" not in out:
        p.append("the default walk-up from a subdirectory broke: rc %r out %r"
                 % (rc, out[:120]))
    suite.record("B", "show-default-walk-up-unchanged", p)


def group_c(suite, fx):
    rc, out, err = py(PLAN, ["--yaml", fx["feat"], "task-001"], fx["cwd"])
    p = []
    if rc != 0:
        p.append("rc %r: %s" % (rc, err[:200]))
    if DEFAULT_DOC in out:
        p.append("the default plan document was injected for an explicit --yaml")
    if "FEAT first" not in out:
        p.append("the named file's task is missing")
    suite.record("C", "plan-yaml-no-default-doc", p)

    p = []
    errl = lines(err)
    if len(errl) != 1:
        p.append("want exactly one stderr line, got %d: %r" % (len(errl), err[:240]))
    elif not ("--doc" in errl[0] and "--no-doc" in errl[0] and "skipped" in errl[0]):
        p.append("the warning must say the doc was skipped and name --doc and "
                 "--no-doc: %r" % errl[0])
    suite.record("C", "plan-yaml-warns-once", p)

    rc, out, err = py(PLAN, ["-y", fx["feat"]], fx["cwd"])
    p = []
    if rc != 0 or DEFAULT_DOC in out or "skipped" not in err:
        p.append("-y without a task id: rc %r doc-injected=%r warned=%r"
                 % (rc, DEFAULT_DOC in out, "skipped" in err))
    suite.record("C", "plan-yaml-short-full-plan", p)

    rc, out, err = py(PLAN, ["--yaml", fx["feat"], "--doc", fx["doc"], "task-001"],
                      fx["cwd"])
    p = []
    if rc != 0:
        p.append("rc %r: %s" % (rc, err[:200]))
    if OTHER_DOC not in out:
        p.append("the explicit --doc was not injected")
    if DEFAULT_DOC in out:
        p.append("the default document was injected beside the explicit one")
    if err.strip():
        p.append("an unneeded warning: %r" % err[:200])
    suite.record("C", "plan-yaml-with-doc", p)

    rc, out, err = py(PLAN, ["--yaml", fx["feat"], "--no-doc", "task-001"],
                      fx["cwd"])
    p = []
    if rc != 0 or DEFAULT_DOC in out or OTHER_DOC in out:
        p.append("--no-doc still printed a document: rc %r" % rc)
    if err.strip():
        p.append("--no-doc must be silent: %r" % err[:200])
    suite.record("C", "plan-yaml-no-doc-silent", p)

    rc, out, err = py(PLAN, ["task-001"], fx["cwd"])
    p = []
    if rc != 0:
        p.append("rc %r: %s" % (rc, err[:200]))
    if DEFAULT_DOC not in out:
        p.append("default behaviour changed: the default document is not injected")
    if "DECOY first" not in out:
        p.append("default behaviour changed: ./requirements.yaml not read")
    if err.strip():
        p.append("default behaviour grew a warning: %r" % err[:200])
    suite.record("C", "plan-default-unchanged", p)

    rc, out, err = py(PLAN, ["--doc", fx["doc"], "task-001"], fx["cwd"])
    p = []
    if rc != 0 or OTHER_DOC not in out or DEFAULT_DOC in out or err.strip():
        p.append("--doc alone: rc %r out-has-doc=%r err=%r"
                 % (rc, OTHER_DOC in out, err[:120]))
    suite.record("C", "plan-doc-only-unchanged", p)

    rc, out, err = py(PLAN, ["--yaml", fx["missing"], "task-001"], fx["cwd"])
    p = one_line_error(rc, out, err)
    suite.record("C", "plan-missing-yaml-one-line", p)

    rc, out, err = py(PLAN, ["--yaml", fx["feat"], "--doc", fx["doc"], "--no-doc",
                             "task-001"], fx["cwd"])
    p = []
    if rc != 2:
        p.append("--doc with --no-doc must be an argparse usage error (exit 2): "
                 "rc %r" % rc)
    if out:
        p.append("nothing may reach stdout: %r" % out[:120])
    if "not allowed with" not in err:
        p.append("stderr must be the argparse mutual-exclusion error: %r" % err[:200])
    suite.record("C", "plan-doc-no-doc-exclusive", p)


def group_d(suite):
    a, b = H.sha256_file(PLAN), H.sha256_file(PLAN_COPY)
    suite.record("D", "deployed-copy-identical",
                 [] if a == b else ["Scripts/ and ClaudeCode/scripts/ differ"])


def group_e(suite, before, pyc_before):
    new = sorted(H.repo_tree() - before)
    suite.record("E", "no-new-repo-paths",
                 [] if not new else
                 ["this suite wrote into the repo tree: %s" % new[:12]])
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after if pyc_before.get(p) != pyc_after[p])
    suite.record("E", "no-new-bytecode",
                 [] if not changed else
                 ["this run wrote or touched bytecode: %s" % changed[:6]])


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="task-update / task-show-details / "
                          "task-implementation-plan: an explicit --yaml PATH",
                    opts=opts, mode="stream", group_width=3, cid_width=40)
    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    with H.TempWorkspace("ph-task-scripts-", keep=opts.keep) as ws:
        fx = fixture(ws)
        group_a(suite, fx)
        reset(fx)
        group_b(suite, fx)
        group_c(suite, fx)
    group_d(suite)
    group_e(suite, before, pyc_before)
    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
