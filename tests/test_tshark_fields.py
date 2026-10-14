#!/usr/bin/env python3
"""mcp-tshark analyze: the `fields` parameter is a comma-separated string OR a list.

Models send a JSON array for `fields` as often as the documented comma-separated
string.  The handler used to call `.split(",")` on whatever it was given, so a
list died with `AttributeError: 'list' object has no attribute 'split'` -- an
exception that is neither a ValueError nor a FileNotFoundError, so it escaped
`handle_analyze`'s refusal envelope and surfaced as an internal error.  The list
is now joined into the string form, and a value that is neither a str nor a
list is refused with a ValueError (which `handle_analyze` turns into an
`{"error": ...}` reply).

Imported in-process; no tshark and no capture is needed.  `_get_tshark` and
`_run_tshark` are swapped on the module for each row: the stub records the argv
it is handed and answers with an empty successful run, so what is asserted is
exactly the `-e` arguments the handler built.  The "capture" is an empty file in
a `mkdtemp()` workspace, because the handler checks `os.path.isfile` first.

Groups:
  A  list, string and refused values
  E  hygiene
"""

import os
import sys
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "tshark_fields"
SERVER = H.repo_path("Scripts", "mcp-tshark.py")
SWAPPED = ("_get_tshark", "_run_tshark")
WANTED = ["ip.src", "ip.dst", "tcp.port"]
STUB_PATH = "/stub/tshark"


class Recorder:
    """_run_tshark's stand-in: remember the argv, answer an empty success."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def e_args(argv):
    """The values that follow each `-e` in an argv."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-e"]


def drive(ts, pcap, fields):
    """Call the real _run_analyze with the stubs; return (argv or None, exc or None)."""
    rec = Recorder()
    old = dict((n, getattr(ts, n)) for n in SWAPPED)
    ts._get_tshark = lambda: STUB_PATH
    ts._run_tshark = rec
    try:
        try:
            ts._run_analyze({"file": pcap, "fields": fields},
                            os.path.dirname(pcap))
        except Exception as exc:  # noqa: BLE001 -- the exception IS the answer
            return None, exc
    finally:
        for name, value in old.items():
            setattr(ts, name, value)
    return (rec.calls[0] if rec.calls else None), None


def group_a(suite, ts, pcap):
    comma = ",".join(WANTED)
    list_argv, list_exc = drive(ts, pcap, list(WANTED))
    str_argv, str_exc = drive(ts, pcap, comma)

    problems = []
    if list_exc is not None:
        problems.append("a list raised %s: %s" % (type(list_exc).__name__,
                                                  list_exc))
    elif e_args(list_argv) != WANTED:
        problems.append("list gave -e args %r, wanted %r"
                        % (e_args(list_argv), WANTED))
    suite.record("A", "list-yields-the-field-args", problems,
                 detail=["fields=%r" % (WANTED,)])

    problems = []
    if str_exc is not None:
        problems.append("a string raised %s: %s" % (type(str_exc).__name__,
                                                    str_exc))
    elif e_args(str_argv) != WANTED:
        problems.append("string gave -e args %r, wanted %r"
                        % (e_args(str_argv), WANTED))
    suite.record("A", "string-still-yields-the-field-args", problems,
                 detail=["fields=%r" % comma])

    problems = []
    if list_argv is None or str_argv is None:
        problems.append("one of the two calls never reached tshark")
    elif list_argv != str_argv:
        problems.append("list argv %r != string argv %r"
                        % (list_argv, str_argv))
    suite.record("A", "list-and-string-build-the-same-argv", problems)

    for cid, value in (("int", 7), ("dict", {"a": 1})):
        argv, exc = drive(ts, pcap, value)
        problems = []
        if exc is None:
            problems.append("fields=%r was accepted (argv %r)" % (value, argv))
        elif not isinstance(exc, ValueError):
            problems.append("fields=%r raised %s (%s), wanted ValueError"
                            % (value, type(exc).__name__, exc))
        elif "fields" not in str(exc):
            problems.append("the refusal does not name the parameter: %s"
                            % exc)
        suite.record("A", "%s-is-a-valueerror-refusal" % cid, problems,
                     detail=["fields=%r" % (value,)])

    # The envelope: handle_analyze turns the ValueError into an error reply.
    old = dict((n, getattr(ts, n)) for n in SWAPPED)
    ts._get_tshark = lambda: STUB_PATH
    ts._run_tshark = Recorder()
    try:
        try:
            reply = ts.handle_analyze({"file": pcap, "fields": 7},
                                      os.path.dirname(pcap))
            raised = None
        except Exception as exc:  # noqa: BLE001
            reply, raised = None, exc
    finally:
        for name, value in old.items():
            setattr(ts, name, value)
    problems = []
    if raised is not None:
        problems.append("handle_analyze let %s escape" % type(raised).__name__)
    elif "error" not in reply:
        problems.append("no error reply: %r" % (reply,))
    suite.record("A", "int-is-an-error-reply-not-a-crash", problems)

    # Negative control: the pre-fix expression really does die on a list, so
    # the rows above fail against the old code rather than passing vacuously.
    try:
        list(WANTED).split(",")
        problems = ["a list has a .split -- the control is void"]
    except AttributeError:
        problems = []
    suite.record("A", "control-the-old-split-dies-on-a-list", problems,
                 detail=["the pre-fix `custom_fields.split(\",\")`"])


def group_e(suite, ts, originals, before, pyc_before):
    moved = [n for n in SWAPPED if getattr(ts, n) is not originals[n]]
    suite.record("E", "swapped-names-restored",
                 [] if not moved else ["not restored: %s" % moved])
    new = sorted(H.repo_tree() - before)
    suite.record("E", "no-new-repo-paths",
                 [] if not new else
                 ["this suite wrote into the repo tree: %s" % new[:12]])
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after
                     if pyc_before.get(p) != pyc_after[p])
    suite.record("E", "no-new-bytecode",
                 [] if not changed else
                 ["this run wrote or touched bytecode: %s" % changed[:6]])


def run(opts=None):
    """Load mcp-tshark, drive analyze's `fields` offline, return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="mcp-tshark analyze: fields as a list or a "
                          "comma-separated string, anything else a ValueError",
                    opts=opts, mode="stream", group_width=3, cid_width=44)
    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    ts = H.load_module_from_path("mcp_tshark_fields_under_test", SERVER)
    originals = dict((n, getattr(ts, n)) for n in SWAPPED)

    with H.TempWorkspace("ph-tshark-fields-", keep=opts.keep) as ws:
        pcap = os.path.join(ws.path, "empty.pcap")
        with open(pcap, "wb"):
            pass
        suite.note("      server  : %s" % SERVER)
        suite.note("      fixture : %s" % ws.path)
        group_a(suite, ts, pcap)

    group_e(suite, ts, originals, before, pyc_before)
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
