#!/usr/bin/env python3
"""pack_apply -- the offline core of ClaudeCode/skills/pack/scripts/pack.py.

What needs no board server, tested first because it touches roadmap.md:

  A. APPLY     every repo event kind applied through the REAL roadmap.py in a
               mkdtemp git repo: once and then twice (roadmap.md byte-identical
               after the second), a crash re-applied, the end-of-lane rank
               expansion, the WIP / state / lane / dropped pre-checks (roadmap.py
               not called), the stderr classifier fed roadmap.py's real WIP
               refusal, close with the archive's sha, add idempotent through the
               origin, the slug fallback.
  B. EVENTS    validate_event refuses every hostile field before any argv, a
               malformed eventId is skipped and counted, never acked.
  C. TEXT      sanitise_row and the text judge: marker classes, invisible code
               points, the parity rows only the generated `_unsafe_label` knows
               (with a mutation proving they bite), the NFC clip.
  D. READERS   read_export / project_items / read_task_yaml.
  E. DIGEST    _read_sync_digest / _write_sync_digest: round trip, a symlinked
               directory, a hardlinked digest, a pre-existing temp name, a
               129-byte / non-hex / FIFO digest.
  F. GATES     the AST gate over pack.py OUTSIDE its generated region, the
               narrower region gate, and the planted control group.
  G. HYGIENE   no bytecode and no new path in the repo tree.

Sandbox discipline (the `roadmap` suite's): every git repository and roadmap
lives in a mkdtemp sandbox; while a case runs, the process environment carries
GIT_CEILING_DIRECTORIES at the sandbox's parent and no global or system git
config, and PACK_ROADMAP_PY (pack.py's TEST-ONLY override) names this repo's
roadmap.py, so pack.py's one subprocess site never reaches the live roadmap.
Stdlib only.
"""

import ast
import contextlib
import io
import os
import subprocess
import sys
import tokenize
import unicodedata

import _harness as H

NAME = "pack_apply"
TARGET = H.repo_path("ClaudeCode", "skills", "pack", "scripts", "pack.py")
ROADMAP = H.repo_path("ClaudeCode", "skills", "roadmap", "scripts", "roadmap.py")
LIVE_ROADMAP = H.repo_path("docs", "roadmap", "roadmap.md")

GA = "A. APPLY: events through the real roadmap.py"
GB = "B. EVENTS: every server field re-checked before argv"
GC = "C. TEXT: sanitise_row and the generated text judge"
GD = "D. READERS: export, items, task YAML"
GE = "E. DIGEST: the one write site"
GF = "F. GATES: AST gate, region gate, control group"
GG = "G. HYGIENE: no bytecode, no new repo path"

GIT_USER = ("-c", "user.name=pack-test", "-c", "user.email=pack@test.invalid",
            "-c", "commit.gpgsign=false")
EV = "pv_" + "A" * 22
EV2 = "pv_" + "B" * 22
# Invisible and look-alike code points, spelled by number so this file holds none.
ZWSP = chr(0x200B)
FFFD = chr(0xFFFD)
FULLWIDTH_LT = chr(0xFF1C)
COMBINING_ACUTE = chr(0x0301)
E_ACUTE = chr(0x00E9)
# "Γιορτή — ★": no code point of it folds to ASCII under NFKD.
GREEK_TITLE = "".join(chr(cp) for cp in (0x0393, 0x03B9, 0x03BF, 0x03C1, 0x03C4, 0x03AE)) + " " + chr(0x2014) + " " + chr(0x2605)


def problem_if(condition, message):
    return [message] if condition else []


def event_id(n):
    """A distinct valid event id per small integer."""
    return "pv_%022d" % n


# ---------------------------------------------------------------------------
# The sandbox: environment, git repos, roadmap.py runs
# ---------------------------------------------------------------------------

_SANDBOX = None


@contextlib.contextmanager
def isolated_env():
    """The git isolation plus PACK_ROADMAP_PY, set on THIS process's
    environment (pack.py's child inherits it) and restored afterwards."""
    keys = {"GIT_CEILING_DIRECTORIES": os.path.dirname(_SANDBOX),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PACK_ROADMAP_PY": ROADMAP,
            "PYTHONDONTWRITEBYTECODE": "1"}
    saved = dict((k, os.environ.get(k)) for k in keys)
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def inside(path):
    real = os.path.realpath(path)
    if _SANDBOX is None or not (real == _SANDBOX or real.startswith(_SANDBOX + os.sep)):
        raise AssertionError("refusing to touch %r outside the sandbox" % (path,))
    return path


def git(repo, *args):
    proc = subprocess.run(["git"] + list(GIT_USER) + list(args), cwd=inside(repo),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=30, env=H.child_env())
    return proc.returncode, proc.stdout, proc.stderr


def rm(repo, *args):
    """roadmap.py run directly (fixture building), never through pack.py."""
    proc = subprocess.run([sys.executable, ROADMAP] + list(args), cwd=inside(repo),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=60, env=H.child_env())
    if proc.returncode != 0:
        raise AssertionError("roadmap.py %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


_REPOS = [0]


def new_repo(work, items=(), wip=None):
    """A fresh git repo with an initialised roadmap and `items`, each
    (title, horizon, state). Returns (repo, {title: id})."""
    _REPOS[0] += 1
    repo = inside(work.subdir("repo%03d" % _REPOS[0]))
    for args in (("-c", "init.defaultBranch=main", "init", "-q", repo),):
        proc = subprocess.run(["git"] + list(args), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=30, env=H.child_env())
        if proc.returncode != 0:
            raise AssertionError("git init failed: %s" % proc.stderr.strip())
    os.makedirs(os.path.join(repo, "docs"), exist_ok=True)
    rm(repo, "init")
    if wip is not None:
        rm(repo, "wip", str(wip), "--reason", "test cap")
    ids = {}
    for n, (title, horizon, state) in enumerate(items):
        out = rm(repo, "add", "--title=" + title, "--origin", "fixture %d" % n,
                 "--horizon", "inbox" if horizon == "unset" else horizon, "--state", state)
        ids[title] = out.strip().splitlines()[-1]
    rc, _o, err = git(repo, "add", "-A")
    rc2, _o, err2 = git(repo, "commit", "-q", "-m", "fixture")
    if rc or rc2:
        raise AssertionError("git commit failed: %s" % (err + err2).strip())
    return repo, ids


def roadmap_bytes(repo):
    with open(inside(os.path.join(repo, "docs", "roadmap", "roadmap.md")), "rb") as fh:
        return fh.read()


def export(repo, plain=False):
    import json
    return json.loads(rm(repo, "export") if plain else rm(repo, "export", "--open-only"))


def lane(repo, horizon):
    doc = export(repo)
    rows = [i for i in doc["items"] if i["horizon"] == horizon]
    return [i["id"] for i in sorted(rows, key=lambda i: i["rank"])]


def item(repo, item_id, plain=False):
    for row in export(repo, plain)["items"]:
        if row["id"] == item_id:
            return row
    return None


def head(repo):
    _rc, out, _err = git(repo, "rev-parse", "HEAD")
    return out.strip()


class Recorder:
    """Wraps pack.py's one subprocess site: every argv it is handed."""

    def __init__(self, mod):
        self.mod = mod
        self.real = mod._roadmap
        self.calls = []

    def __enter__(self):
        def recording(repo, argv):
            self.calls.append(list(argv))
            return self.real(repo, argv)
        self.mod._roadmap = recording
        return self

    def __exit__(self, *exc):
        self.mod._roadmap = self.real
        return False

    def writes(self):
        return [c for c in self.calls if c and c[0] != "export"]


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def apply(mod, repo, event):
    clean = mod.validate_event(event)
    if clean is None:
        return ("failed", "refused"), []
    with Recorder(mod) as rec, captured():
        outcome = mod.apply_event(repo, clean)
    return outcome, rec.calls


def twice(mod, repo, event, want):
    """Apply once and again: both outcomes `want`, roadmap.md byte-identical
    after the second, and the second run calls no writing command."""
    first, _calls = apply(mod, repo, event)
    after_first = roadmap_bytes(repo)
    second, calls = apply(mod, repo, event)
    out = []
    out += problem_if(first != want, "first apply %r, want %r" % (first, want))
    out += problem_if(second != want, "second apply %r, want %r" % (second, want))
    out += problem_if(roadmap_bytes(repo) != after_first, "roadmap.md changed on the second apply")
    out += problem_if([c for c in calls if c[0] != "export"], "the second apply ran %r" % calls)
    return out


# ---------------------------------------------------------------------------
# A. APPLY
# ---------------------------------------------------------------------------

def group_apply(suite, mod, work):
    # start
    repo, ids = new_repo(work, [("alpha", "next", "planned"), ("beta", "now", "active")])
    a = ids["alpha"]
    probs = twice(mod, repo, {"eventId": EV, "kind": "start", "itemId": a}, ("applied", None))
    row = item(repo, a)
    probs += problem_if(row["horizon"] != "now" or row["state"] != "active", "start left %r" % row)
    suite.record(GA, "start-applied-twice-identical", probs)

    # start into a full now: failed:wip, roadmap.py not called
    repo, ids = new_repo(work, [("busy", "now", "active"), ("wait", "next", "planned")], wip=1)
    before = roadmap_bytes(repo)
    outcome, calls = apply(mod, repo, {"eventId": EV, "kind": "start", "itemId": ids["wait"]})
    probs = problem_if(outcome != ("failed", "wip"), "outcome %r" % (outcome,))
    probs += problem_if([c for c in calls if c[0] != "export"], "roadmap.py was called: %r" % calls)
    probs += problem_if(roadmap_bytes(repo) != before, "roadmap.md changed")
    suite.record(GA, "start-into-full-now-wip-precheck", probs)

    # move into a full now: failed:wip, not called
    outcome, calls = apply(mod, repo, {"eventId": EV, "kind": "move", "itemId": ids["wait"], "lane": "now"})
    probs = problem_if(outcome != ("failed", "wip"), "outcome %r" % (outcome,))
    probs += problem_if([c for c in calls if c[0] != "export"], "roadmap.py was called: %r" % calls)
    probs += problem_if(roadmap_bytes(repo) != before, "roadmap.md changed")
    suite.record(GA, "move-into-full-now-wip-precheck", probs)

    # _classify on roadmap.py's REAL refusal (the pre-check bypassed)
    with captured():
        code, _out, stderr = mod._roadmap(repo, ["move", ids["wait"], "now", "--reason", "x"])
    probs = problem_if(code == 0, "roadmap.py accepted a move into a full now")
    probs += problem_if(mod._classify(code, stderr) != "wip", "classified %r from %r" % (mod._classify(code, stderr), stderr))
    probs += problem_if(mod._classify(2, "roadmap: something else\n") != "refused", "a non-WIP refusal not `refused`")
    probs += problem_if(roadmap_bytes(repo) != before, "roadmap.md changed")
    suite.record(GA, "classify-real-wip-stderr", probs, detail=["stderr: %s" % stderr.strip()[:120]])

    # rank before
    repo, ids = new_repo(work, [("r1", "next", "planned"), ("r2", "next", "planned"), ("r3", "next", "planned")])
    r1, r2, r3 = ids["r1"], ids["r2"], ids["r3"]
    probs = twice(mod, repo, {"eventId": EV, "kind": "rank", "itemId": r3, "beforeId": r1}, ("applied", None))
    probs += problem_if(lane(repo, "next") != [r3, r1, r2], "order %r" % lane(repo, "next"))
    suite.record(GA, "rank-before-applied-twice-identical", probs)

    # rank to the end of the lane: the expansion, exact sequence
    repo, ids = new_repo(work, [("e1", "later", "idea"), ("e2", "later", "idea"),
                                ("e3", "later", "idea"), ("e4", "later", "idea")])
    e1, e2, e3, e4 = (ids[k] for k in ("e1", "e2", "e3", "e4"))
    event = {"eventId": EV, "kind": "rank", "itemId": e2, "beforeId": None}
    outcome, calls = apply(mod, repo, event)
    writes = [c for c in calls if c[0] != "export"]
    probs = problem_if(outcome != ("applied", None), "outcome %r" % (outcome,))
    probs += problem_if(writes != [["rank", e3, "--before", e2], ["rank", e4, "--before", e2]],
                        "expansion ran %r" % writes)
    probs += problem_if(lane(repo, "later") != [e1, e3, e4, e2], "order %r" % lane(repo, "later"))
    after = roadmap_bytes(repo)
    again, calls2 = apply(mod, repo, event)
    probs += problem_if(again != ("applied", None) or [c for c in calls2 if c[0] != "export"],
                        "the second end-of-lane apply %r ran %r" % (again, calls2))
    probs += problem_if(roadmap_bytes(repo) != after, "roadmap.md changed on the second apply")
    suite.record(GA, "rank-end-of-lane-expansion", probs, detail=["%r" % w for w in writes])

    # crash: the first expansion command ran, the ack was lost; re-apply
    repo2, ids2 = new_repo(work, [("e1", "later", "idea"), ("e2", "later", "idea"),
                                  ("e3", "later", "idea"), ("e4", "later", "idea")])
    rm(repo2, "rank", ids2["e3"], "--before", ids2["e2"])
    outcome, calls = apply(mod, repo2, {"eventId": EV, "kind": "rank", "itemId": ids2["e2"], "beforeId": None})
    probs = problem_if(outcome != ("applied", None), "outcome %r" % (outcome,))
    probs += problem_if([c for c in calls if c[0] != "export"] != [["rank", ids2["e4"], "--before", ids2["e2"]]],
                        "the re-apply ran %r" % calls)
    probs += problem_if(roadmap_bytes(repo2) != after, "the crashed-then-re-applied roadmap.md differs from a clean apply")
    suite.record(GA, "crash-then-reapply-identical", probs)

    # rank across lanes: failed:lane
    repo, ids = new_repo(work, [("x", "next", "planned"), ("y", "later", "idea")])
    before = roadmap_bytes(repo)
    outcome, calls = apply(mod, repo, {"eventId": EV, "kind": "rank", "itemId": ids["x"], "beforeId": ids["y"]})
    probs = problem_if(outcome != ("failed", "lane"), "outcome %r" % (outcome,))
    probs += problem_if([c for c in calls if c[0] != "export"] or roadmap_bytes(repo) != before, "the repo was touched")
    suite.record(GA, "rank-cross-lane-failed-lane", probs)

    # cross-lane move without and with beforeId
    repo, ids = new_repo(work, [("m1", "next", "planned"), ("m2", "later", "idea"), ("m3", "later", "idea")])
    m1, m2, m3 = ids["m1"], ids["m2"], ids["m3"]
    probs = twice(mod, repo, {"eventId": EV, "kind": "move", "itemId": m1, "lane": "later", "beforeId": None},
                  ("applied", None))
    probs += problem_if(lane(repo, "later") != [m2, m3, m1], "order %r" % lane(repo, "later"))
    probs += twice(mod, repo, {"eventId": EV2, "kind": "move", "itemId": m1, "lane": "later", "beforeId": m2},
                   ("applied", None))
    probs += problem_if(lane(repo, "later") != [m1, m2, m3], "order %r" % lane(repo, "later"))
    repo, ids = new_repo(work, [("m1", "next", "planned"), ("m2", "later", "idea"), ("m3", "later", "idea")])
    probs += twice(mod, repo, {"eventId": EV, "kind": "move", "itemId": ids["m1"], "lane": "later",
                               "beforeId": ids["m3"]}, ("applied", None))
    probs += problem_if(lane(repo, "later") != [ids["m2"], ids["m1"], ids["m3"]], "order %r" % lane(repo, "later"))
    suite.record(GA, "move-cross-lane-with-and-without-before", probs)

    # move a planned item into inbox: failed:state; an active one across lanes: failed:state
    repo, ids = new_repo(work, [("p", "next", "planned"), ("act", "now", "active")])
    before = roadmap_bytes(repo)
    o1, c1 = apply(mod, repo, {"eventId": EV, "kind": "move", "itemId": ids["p"], "lane": "inbox"})
    o2, c2 = apply(mod, repo, {"eventId": EV2, "kind": "move", "itemId": ids["act"], "lane": "next"})
    probs = problem_if(o1 != ("failed", "state"), "planned -> inbox %r" % (o1,))
    probs += problem_if(o2 != ("failed", "state"), "active -> next %r" % (o2,))
    probs += problem_if([c for c in c1 + c2 if c[0] != "export"] or roadmap_bytes(repo) != before, "the repo was touched")
    suite.record(GA, "move-state-prechecks", probs)

    # close: live -> archived done with the full sha; twice identical
    repo, ids = new_repo(work, [("closing", "now", "active"), ("other", "next", "planned")])
    full = head(repo)
    probs = twice(mod, repo, {"eventId": EV, "kind": "close", "itemId": ids["closing"], "commit": full[:10]},
                  ("applied", full))
    row = item(repo, ids["closing"], plain=True)
    probs += problem_if(not row or row["state"] != "done" or row["closed"]["commit"] != full, "archive %r" % row)
    suite.record(GA, "close-applied-twice-full-sha", probs)

    # close with an unknown sha: failed:refused, file unchanged
    repo, ids = new_repo(work, [("c", "next", "planned")])
    before = roadmap_bytes(repo)
    outcome, _calls = apply(mod, repo, {"eventId": EV, "kind": "close", "itemId": ids["c"], "commit": "deadbeef" * 5})
    probs = problem_if(outcome != ("failed", "refused"), "outcome %r" % (outcome,))
    probs += problem_if(roadmap_bytes(repo) != before, "roadmap.md changed")
    suite.record(GA, "close-unknown-sha-refused", probs)

    # close of an item the operator already closed with ANOTHER sha: applied, archive sha wins
    repo, ids = new_repo(work, [("hand", "next", "planned")])
    full = head(repo)
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    rm(repo, "close", ids["hand"], "--commit", full)
    other = head(repo)
    outcome, _calls = apply(mod, repo, {"eventId": EV, "kind": "close", "itemId": ids["hand"], "commit": other[:12]})
    suite.record(GA, "close-already-closed-other-sha", problem_if(outcome != ("applied", full), "outcome %r, want the archive's %s" % (outcome, full)))

    # close of a dropped item: failed:dropped
    repo, ids = new_repo(work, [("gone", "next", "planned")])
    rm(repo, "close", ids["gone"], "--reason", "not needed")
    outcome, _calls = apply(mod, repo, {"eventId": EV, "kind": "close", "itemId": ids["gone"], "commit": head(repo)})
    suite.record(GA, "close-dropped-failed-dropped", problem_if(outcome != ("failed", "dropped"), "outcome %r" % (outcome,)))

    # close of a title that yields no slug: --slug r<NNNN>, archived. The
    # plan's example `Ünnep — ★` is NOT such a title (NFKD folds Ü to U, so
    # roadmap.py's slugify gives `unnep`): it closes WITHOUT --slug, and a
    # Greek title, which has no ASCII fold at all, takes the fallback.
    probs = []
    for title, want_slug in (("Ünnep — ★", None), (GREEK_TITLE, True)):
        repo, ids = new_repo(work, [(title, "next", "planned")])
        item_id = ids[title]
        full = head(repo)
        outcome, calls = apply(mod, repo, {"eventId": EV, "kind": "close", "itemId": item_id, "commit": full})
        closes = [c for c in calls if c[0] == "close"]
        argv = ["close", item_id, "--commit", full] + (["--slug", "r" + item_id[2:]] if want_slug else [])
        page = "%s-%s.md" % (item_id[2:], "r" + item_id[2:] if want_slug else "unnep")
        probs += problem_if(outcome != ("applied", full), "%r: outcome %r" % (title, outcome))
        probs += problem_if(closes != [argv], "%r: close argv %r" % (title, closes))
        probs += problem_if(not os.path.isfile(os.path.join(repo, "docs", "roadmap", "archive", page)),
                            "%r: no archive page %s" % (title, page))
    suite.record(GA, "close-slug-fallback", probs)
    probs = problem_if(mod._needs_slug("Fix the parser"), "an ASCII title asks for --slug")
    probs += problem_if(not mod._needs_slug("★ —"), "a symbol-only title does not")
    probs += problem_if(mod._needs_slug("Ünnep"), "an NFKD-foldable title asks for --slug")
    suite.record(GA, "needs-slug-mirrors-slugify", probs)

    # close without a commit on a live item: skipped, nothing run
    repo, ids = new_repo(work, [("nocommit", "next", "planned")])
    outcome, calls = apply(mod, repo, {"eventId": EV, "kind": "close", "itemId": ids["nocommit"]})
    suite.record(GA, "close-without-commit-skipped",
                 problem_if(outcome != ("skipped", None) or [c for c in calls if c[0] != "export"],
                            "outcome %r, calls %r" % (outcome, calls)))

    # add twice -> one item; a title starting with `-`
    repo, _ids = new_repo(work, [("existing", "next", "planned")])
    probs = twice(mod, repo, {"eventId": EV, "kind": "add", "title": "-dash first title"}, ("applied", None))
    doc = export(repo, plain=True)
    added = [i for i in doc["items"] if i["origin"] == "pack " + EV]
    probs += problem_if(len(added) != 1, "%d items carry the origin" % len(added))
    probs += problem_if(added and (added[0]["title"] != "-dash first title" or added[0]["horizon"] != "unset"
                                   or added[0]["state"] != "idea"), "added %r" % (added[:1],))
    suite.record(GA, "add-twice-one-item-dash-title", probs)

    # add whose origin exists only in the ARCHIVE: applied, no second item
    repo, _ids = new_repo(work)
    rm(repo, "add", "--title=archived add", "--origin", "pack " + EV2)
    archived_id = export(repo)["items"][0]["id"]
    rm(repo, "close", archived_id, "--reason", "done elsewhere")
    before = roadmap_bytes(repo)
    outcome, calls = apply(mod, repo, {"eventId": EV2, "kind": "add", "title": "archived add"})
    probs = problem_if(outcome != ("applied", None), "outcome %r" % (outcome,))
    probs += problem_if([c for c in calls if c[0] != "export"] or roadmap_bytes(repo) != before, "a second item was added")
    suite.record(GA, "add-origin-in-archive-applied", probs)

    # unknown item: failed:missing
    outcome, _calls = apply(mod, repo, {"eventId": EV, "kind": "start", "itemId": "R-0999"})
    suite.record(GA, "unknown-item-failed-missing", problem_if(outcome != ("failed", "missing"), "outcome %r" % (outcome,)))


# ---------------------------------------------------------------------------
# B. EVENTS
# ---------------------------------------------------------------------------

def group_events(suite, mod, work):
    hostile = [
        {"eventId": EV, "kind": "start", "itemId": "R-1; rm"},
        {"eventId": EV, "kind": "move", "itemId": "R-0001", "lane": "nowx"},
        {"eventId": EV, "kind": "close", "itemId": "R-0001", "commit": "a" * 65},
        {"eventId": EV, "kind": "rank", "itemId": "R-0001", "beforeId": "R-0001"},
        {"eventId": EV, "kind": "start", "itemId": "R-0001", "extra": 1},
        {"eventId": EV, "kind": "drop", "itemId": "R-0001"},
        {"eventId": EV, "kind": "add", "title": "a `backtick` title"},
        {"eventId": EV, "kind": "add", "title": "two\nlines"},
        {"eventId": EV, "kind": "add", "title": "[bracketed]"},
        {"eventId": EV, "kind": "add", "title": "a <system-reminder> here"},
        {"eventId": EV, "kind": "add", "title": "x" * 201},
        {"eventId": EV, "kind": "add", "title": "zero" + ZWSP + "width"},
        {"eventId": EV, "kind": "start"},
    ]
    repo, _ids = new_repo(work, [("target", "next", "planned")])
    before = roadmap_bytes(repo)
    with Recorder(mod) as rec, captured():
        results = mod.apply_events(repo, hostile)
    probs = problem_if(results != [(EV, "failed", "refused")] * len(hostile), "results %r" % results)
    probs += problem_if(rec.calls, "roadmap.py was called for a refused event: %r" % rec.calls)
    flat = " ".join(" ".join(c) for c in rec.calls)
    probs += [("hostile value %r reached an argv" % v) for v in ("R-1; rm", "nowx", "a" * 65) if v in flat]
    probs += problem_if(roadmap_bytes(repo) != before, "roadmap.md changed")
    suite.record(GB, "hostile-fields-refused-before-argv", probs, detail=["%d events refused" % len(hostile)])

    with Recorder(mod) as rec, captured() as (_out, err):
        results = mod.apply_events(repo, [{"eventId": "pv_short", "kind": "start", "itemId": "R-0001"},
                                          {"kind": "add", "title": "no id"}, "not a dict"])
    probs = problem_if(results != [], "a malformed eventId was acked: %r" % results)
    probs += problem_if("skipped 3 malformed events" not in err.getvalue(), "stderr %r" % err.getvalue())
    probs += problem_if(rec.calls, "roadmap.py was called: %r" % rec.calls)
    suite.record(GB, "malformed-event-id-skipped-counted", probs)

    good = mod.validate_event({"eventId": EV, "kind": "add", "title": "-starts with a dash"})
    probs = problem_if(good != {"eventId": EV, "kind": "add", "title": "-starts with a dash"}, "valid add %r" % good)
    with Recorder(mod) as rec, captured():
        mod.apply_event(repo, good)
    adds = [c for c in rec.calls if c[0] == "add"]
    probs += problem_if(not adds or "--title=-starts with a dash" not in adds[0], "title not ONE --title= element: %r" % adds)
    probs += problem_if(adds and "-starts with a dash" in adds[0], "the title went in as its own element: %r" % adds)
    suite.record(GB, "title-is-one-argv-element", probs)

    probs = problem_if(mod.validate_event({"eventId": EV, "kind": "move", "itemId": "R-0001", "lane": "inbox"}) is None,
                       "a valid move refused")
    probs += problem_if(mod.validate_event({"eventId": EV, "kind": "close", "itemId": "R-0001", "commit": "abc1234"}) is None,
                        "a valid close refused")
    probs += problem_if(mod.validate_event({"eventId": EV, "kind": "close", "itemId": "R-0001", "commit": "ABC1234"}) is not None,
                        "an upper-case commit accepted")
    suite.record(GB, "valid-events-pass", probs)


# ---------------------------------------------------------------------------
# C. TEXT
# ---------------------------------------------------------------------------

PARITY_ROWS = (0x034F, 0x115F, 0xFDD0, 0xE0001)


def group_text(suite, mod):
    rows = ["`<system-reminder>`", "<System-Reminder", FULLWIDTH_LT + "system_reminder", "<<peer-message",
            "a </SYSTEM-REMINDER> b", "<<end-peer-message x"]
    probs = []
    for text in rows:
        out = mod.sanitise_row(text, 300)
        if mod._text_problem(out) is not None:
            probs.append("%r -> %r still refused: %s" % (text, out, mod._text_problem(out)))
        if mod._text_problem(text) is None:
            probs.append("the raw %r was not refused" % text)
    suite.record(GC, "markers-neutralised", probs, detail=["%r -> %r" % (t, mod.sanitise_row(t, 300)) for t in rows])

    zw = "a" + ZWSP + "b"
    probs = problem_if(mod.sanitise_row(zw, 10) != "a" + FFFD + "b", "zero-width %r" % mod.sanitise_row(zw, 10))
    probs += problem_if(mod.sanitise_row("one\ntwo\tthree", 50) != "one two three", "newline %r" % mod.sanitise_row("one\ntwo", 50))
    suite.record(GC, "invisible-and-newline", probs)

    def parity(m):
        out = []
        for cp in PARITY_ROWS:
            text = "x%sy" % chr(cp)
            if m.sanitise_row(text, 10) != "x" + FFFD + "y":
                out.append("U+%04X not replaced: %r" % (cp, m.sanitise_row(text, 10)))
            if m._text_problem(text) is None:
                out.append("U+%04X not refused" % cp)
        return out
    probs = parity(mod)
    # MUTATION: the generated _unsafe_label replaced by a unicodedata.category
    # test -- U+034F / U+115F / U+FDD0 then pass, so the rows above go red.
    real = mod._unsafe_label
    mod._unsafe_label = lambda cp: "x" if unicodedata.category(chr(cp)) in ("Cc", "Cf", "Co", "Cs") else None
    try:
        mutated = parity(mod)
    finally:
        mod._unsafe_label = real
    missed = [cp for cp in (0x034F, 0x115F, 0xFDD0) if not any("U+%04X" % cp in p for p in mutated)]
    probs += problem_if(missed, "the category mutation was not caught for %s" % ["U+%04X" % c for c in missed])
    suite.record(GC, "parity-rows-and-mutation", probs, detail=["mutant problems: %d" % len(mutated)])

    nfd = ("e" + COMBINING_ACUTE) * 200
    out = mod.sanitise_row(nfd, 200)
    probs = problem_if(out != E_ACUTE * 200, "an NFD title at the cap was clipped: %d code points" % len(out))
    long = mod.sanitise_row("y" * 250, 200)
    probs += problem_if(len(long) != 200 or not long.endswith(" [clipped]"), "a long title %r" % long[-20:])
    suite.record(GC, "nfc-before-clip", probs)


# ---------------------------------------------------------------------------
# D. READERS
# ---------------------------------------------------------------------------

def fake_export(schema="roadmap-export/2", items=None):
    import json
    return json.dumps({"schema": schema, "items": items or [], "wip_limit": {"now": 3}})


def group_readers(suite, mod, work):
    real = mod._roadmap
    try:
        mod._roadmap = lambda repo, argv: (0, fake_export("roadmap-export/1"), "")
        with captured() as (_o, err):
            try:
                mod.read_export(work.path)
                code = 0
            except SystemExit as exc:
                code = exc.code
        probs = problem_if(code != 2, "roadmap-export/1 read with exit %r" % code)
        probs += problem_if("roadmap-export/2" not in err.getvalue(), "stderr %r" % err.getvalue())
        mod._roadmap = lambda repo, argv: (0, '{"schema": "roadmap-export/2", "schema": "x", "items": []}', "")
        with captured():
            try:
                mod.read_export(work.path)
                code = 0
            except SystemExit as exc:
                code = exc.code
        probs += problem_if(code != 2, "a duplicate key accepted (exit %r)" % code)
    finally:
        mod._roadmap = real
    suite.record(GD, "export-schema-and-duplicates-refused", probs)

    base = {"id": "R-0001", "title": "t", "state": "idea", "horizon": "unset", "rank": 0, "ready": True,
            "blocked_by": [], "tags": ["ok", "x" * 65, "Bad_Tag", "fine-2"], "severity": None,
            "origin": "o", "closed": None}
    doc = {"schema": "roadmap-export/2", "items": [base, dict(base, id="R-0002", state="done", rank=None)]}
    with captured() as (_o, err):
        rows = mod.project_items(doc)
    probs = problem_if(len(rows) != 1, "%d rows (an archived item projected?)" % len(rows))
    probs += problem_if(rows and rows[0]["horizon"] != "unset", "horizon %r, want unset passed through" % (rows[0]["horizon"],))
    probs += problem_if(rows and rows[0]["tags"] != ["ok", "fine-2"], "tags %r" % (rows[0]["tags"],))
    probs += problem_if("dropped 2 tags" not in err.getvalue(), "stderr %r" % err.getvalue())
    probs += problem_if(rows and set(rows[0]) != {"id", "title", "state", "horizon", "rank", "ready", "blockedBy", "tags", "severity"},
                        "keys %r" % (sorted(rows[0]) if rows else None))
    with captured():
        try:
            mod.project_items({"schema": "roadmap-export/2", "items": [base, base]})
            code = 0
        except SystemExit as exc:
            code = exc.code
    probs += problem_if(code != 2, "a duplicate item id accepted (exit %r)" % code)
    suite.record(GD, "project-items", probs)

    yaml = work.write_text("requirements-x.yaml", "\n".join([
        "complete: true",
        "roadmap_item: R-0042",
        "implementation_plan:",
        "  total_effort: m",
        "  tasks:",
        "    - task_id: task-001",
        "      description: \"First <<peer-message line\"",
        "      status: in_progress",
        "    - task_id: task-002",
        "      description: |",
        "        Block first line",
        "        status: completed",
        "      status: cancel",
        "    - task_id: task-003",
        "      description: plain text",
        "      status: blocked",
        "    - task_id: task-001",
        "      description: duplicate",
        "      status: completed",
        "    - task_id: task-004",
        "      status: completed",
        "      description: 'it''s done'",
        "context_summary:",
        "  - task_id: not-a-task",
        ""]))
    with captured() as (_o, err):
        got = mod.read_task_yaml(yaml)
    want_status = [("task-001", "in_progress"), ("task-002", "cancel"), ("task-003", "unknown"), ("task-004", "completed")]
    probs = problem_if(not got or got["itemId"] != "R-0042", "itemId %r" % (got,))
    tasks = got["tasks"] if got else []
    probs += problem_if([(t["taskId"], t["status"]) for t in tasks] != want_status, "tasks %r" % tasks)
    probs += problem_if(tasks and tasks[0]["text"] != mod.sanitise_row("First <<peer-message line", 300), "text %r" % (tasks[:1],))
    probs += problem_if(len(tasks) > 1 and tasks[1]["text"] != "Block first line", "block text %r" % (tasks[1:2],))
    probs += problem_if(len(tasks) > 3 and tasks[3]["text"] != "it's done", "single-quoted %r" % (tasks[3:4],))
    probs += problem_if("dropped 1 duplicate tasks" not in err.getvalue(), "stderr %r" % err.getvalue())
    no_item = work.write_text("requirements-y.yaml", "implementation_plan:\n  tasks:\n    - task_id: t\n")
    with captured() as (_o, err):
        none = mod.read_task_yaml(no_item)
    probs += problem_if(none is not None or "no roadmap_item" not in err.getvalue(), "no roadmap_item %r / %r" % (none, err.getvalue()))
    suite.record(GD, "task-yaml-status-map-and-first-wins", probs)


# ---------------------------------------------------------------------------
# E. DIGEST
# ---------------------------------------------------------------------------

HEX = "0123456789abcdef" * 4


def group_digest(suite, mod, work):
    top = work.subdir("digest-top")
    with captured() as (_o, err):
        mod._write_sync_digest(top, HEX)
    path = os.path.join(top, ".claude", "tmp", "pack", "last-sync.sha256")
    probs = problem_if(mod._read_sync_digest(top) != HEX, "round trip %r" % mod._read_sync_digest(top))
    probs += problem_if(not os.path.isfile(path) or oct(os.stat(path).st_mode & 0o777) != "0o600", "the digest file is missing or not 0600")
    probs += problem_if(oct(os.stat(os.path.join(top, ".claude", "tmp", "pack")).st_mode & 0o777) != "0o700", "pack dir not 0700")
    probs += problem_if(err.getvalue(), "stderr %r" % err.getvalue())
    probs += problem_if(sorted(os.listdir(os.path.dirname(path))) != ["last-sync.sha256"], "leftovers %r" % os.listdir(os.path.dirname(path)))
    suite.record(GE, "write-read-round-trip", probs)

    top = work.subdir("digest-symlink")
    outside = work.subdir("digest-outside")
    os.makedirs(os.path.join(top, ".claude"))
    os.symlink(outside, os.path.join(top, ".claude", "tmp"))
    with captured() as (_o, err):
        mod._write_sync_digest(top, HEX)
    probs = problem_if(os.listdir(outside), "written through the symlink: %r" % os.listdir(outside))
    probs += problem_if("digest not written" not in err.getvalue(), "stderr %r" % err.getvalue())
    probs += problem_if(mod._read_sync_digest(top) is not None, "read through the symlink")
    suite.record(GE, "symlinked-dir-refused", probs)

    top = work.subdir("digest-hardlink")
    pack_dir = os.path.join(top, ".claude", "tmp", "pack")
    os.makedirs(pack_dir)
    victim = work.write_text("digest-victim.txt", "victim bytes\n")
    os.link(victim, os.path.join(pack_dir, "last-sync.sha256"))
    with captured():
        mod._write_sync_digest(top, HEX)
    with open(victim, encoding="utf-8") as fh:
        probs = problem_if(fh.read() != "victim bytes\n", "the hardlinked outside file changed")
    probs += problem_if(mod._read_sync_digest(top) != HEX, "the digest was not replaced")
    suite.record(GE, "hardlinked-digest-swapped-not-written", probs)

    top = work.subdir("digest-temp")
    pack_dir = os.path.join(top, ".claude", "tmp", "pack")
    os.makedirs(pack_dir)
    planted = os.path.join(pack_dir, "last-sync.1.planted.tmp")
    with open(planted, "w", encoding="utf-8") as fh:
        fh.write("planted\n")
    real = mod._digest_temp_name
    mod._digest_temp_name = lambda: "last-sync.1.planted.tmp"
    try:
        with captured() as (_o, err):
            mod._write_sync_digest(top, HEX)
    finally:
        mod._digest_temp_name = real
    with open(planted, encoding="utf-8") as fh:
        probs = problem_if(fh.read() != "planted\n", "the pre-existing temp file was opened and written")
    probs += problem_if("digest not written" not in err.getvalue(), "stderr %r" % err.getvalue())
    probs += problem_if(os.path.exists(os.path.join(pack_dir, "last-sync.sha256")), "a digest was written anyway")
    suite.record(GE, "pre-existing-temp-never-opened", probs)

    probs = []
    for label, body in (("129-byte", HEX + "\n" + "0" * 64), ("non-hex", "z" * 64 + "\n"), ("upper", HEX.upper())):
        top = work.subdir("digest-" + label)
        os.makedirs(os.path.join(top, ".claude", "tmp", "pack"))
        with open(os.path.join(top, ".claude", "tmp", "pack", "last-sync.sha256"), "w", encoding="ascii") as fh:
            fh.write(body)
        probs += problem_if(mod._read_sync_digest(top) is not None, "%s digest read as %r" % (label, mod._read_sync_digest(top)))
    top = work.subdir("digest-fifo")
    os.makedirs(os.path.join(top, ".claude", "tmp", "pack"))
    os.mkfifo(os.path.join(top, ".claude", "tmp", "pack", "last-sync.sha256"))
    probs += problem_if(mod._read_sync_digest(top) is not None, "a FIFO read as a digest")
    probs += problem_if(mod._read_sync_digest(work.subdir("digest-empty")) is not None, "a missing digest read")
    suite.record(GE, "bad-digests-read-none", probs)


# ---------------------------------------------------------------------------
# F. GATES
# ---------------------------------------------------------------------------

WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"}
OS_MUTATORS = {"replace", "rename", "renames", "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs",
               "chmod", "chown", "lchown", "link", "symlink", "truncate", "ftruncate", "utime", "mkfifo",
               "mknod", "write"}
ATTR_MUTATORS = {"write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "rename", "replace",
                 "symlink_to", "hardlink_to", "chmod", "lchmod"}
DIGEST_OS_ALLOWED = {"open", "mkdir", "write", "fsync", "replace", "unlink", "close"}
REGION_NAMES = {"_parent_pid", "_find_owner", "_read_session_file", "Owner"}
BANNED_IMPORTS = {"importlib", "shutil", "tempfile", "pathlib"}
STDLIB = set(getattr(sys, "stdlib_module_names", ())) or {
    "argparse", "hashlib", "json", "os", "re", "secrets", "stat", "subprocess", "sys", "time",
    "unicodedata", "typing"}


def region_lines(source):
    """(begin, end) line numbers of the one GENERATED region, by COMMENT tokens."""
    begins, ends = [], []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            if tok.string.startswith("# BEGIN GENERATED:"):
                begins.append(tok.start[0])
            elif tok.string.startswith("# END GENERATED:"):
                ends.append(tok.start[0])
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        raise AssertionError("want exactly one region, got %r / %r" % (begins, ends))
    return begins[0], ends[0]


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else base + "." + node.attr
    return None


def _flag_leaves(node):
    """The flag names of an os.open flags expression, or None when it is not a
    `|` tree of os.O_* / _O_* names."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _flag_leaves(node.left), _flag_leaves(node.right)
        return None if left is None or right is None else left + right
    name = _dotted(node)
    if name is None:
        return None
    leaf = name.split(".")[-1]
    if (name.startswith("os.O_") and name.count(".") == 1) or (isinstance(node, ast.Name) and leaf.startswith("_O_")):
        return [leaf.lstrip("_")]
    return None


def _mode_of(call, index):
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    return call.args[index] if len(call.args) > index else None


def _bad_mode(node):
    """True when an open mode is non-literal or writes."""
    if node is None:
        return False
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return True
    return any(c in node.value for c in "wax+")


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


def gate_problems(source):
    """The AST gate over pack.py OUTSIDE its generated region."""
    begin, end = region_lines(source)
    tree = ast.parse(source)
    parents = _parents(tree)
    out, runs = [], []

    def outside(node):
        line = getattr(node, "lineno", None)
        return line is not None and not (begin <= line <= end)
    for node in ast.walk(tree):
        if not outside(node):
            continue
        where = "line %d" % node.lineno
        func = _enclosing_function(node, parents)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                if top in BANNED_IMPORTS:
                    out.append("%s: import of %s" % (where, top))
                elif top not in STDLIB:
                    out.append("%s: non-stdlib import %s" % (where, top))
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in ("subprocess", "os"):
                out.append("%s: from %s import ..." % (where, node.module))
        if isinstance(node, ast.Name) and (node.id in REGION_NAMES or node.id == "__import__"):
            out.append("%s: name %s" % (where, node.id))
        if isinstance(node, ast.Attribute) and node.attr in REGION_NAMES:
            out.append("%s: attribute %s" % (where, node.attr))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in REGION_NAMES:
            out.append("%s: string constant %r" % (where, node.value))
        if isinstance(node, ast.Attribute) and _dotted(node) == "sys.modules":
            out.append("%s: sys.modules" % where)
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            out.append("%s: __dict__" % where)
        if isinstance(node, ast.keyword) and node.arg == "shell":
            out.append("%s: shell=" % where)
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func) or ""
        if name == "subprocess.run":
            runs.append(node)
        if name in ("os.system", "os.popen") or name.startswith("os.exec") or name.startswith("os.spawn"):
            out.append("%s: %s" % (where, name))
        if name in ("globals", "vars"):
            out.append("%s: %s()" % (where, name))
        if name in ("getattr", "setattr", "delattr"):
            if len(node.args) < 2 or not (isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                out.append("%s: %s with a non-literal name" % (where, name))
        if any(isinstance(kw.arg, str) and kw.arg == "shell" for kw in node.keywords):
            out.append("%s: shell= on a call" % where)
        if func == "_write_sync_digest":
            if name.startswith("os.") and name.count(".") == 1 and name[3:] not in DIGEST_OS_ALLOWED and name[3:] in OS_MUTATORS:
                out.append("%s: %s inside _write_sync_digest" % (where, name))
            continue
        if name in ("open", "io.open", "codecs.open") and _bad_mode(_mode_of(node, 1)):
            out.append("%s: %s with a writing or non-literal mode" % (where, name))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open" and name != "os.open" \
                and name not in ("io.open", "codecs.open") and _bad_mode(_mode_of(node, 0)):
            out.append("%s: .open with a writing or non-literal mode" % where)
        if name == "os.open":
            leaves = _flag_leaves(node.args[1]) if len(node.args) > 1 else None
            if leaves is None or WRITE_FLAGS & set(leaves):
                out.append("%s: os.open with writing or non-literal flags" % where)
        if name.startswith("os.") and name[3:] in OS_MUTATORS:
            out.append("%s: %s outside _write_sync_digest" % (where, name))
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ATTR_MUTATORS:
            out.append("%s: .%s() outside _write_sync_digest" % (where, node.func.attr))
    if len(runs) != 1:
        out.append("%d subprocess.run sites outside the region, want exactly 1" % len(runs))
    for run in runs:
        first = run.args[0] if run.args else None
        if not (isinstance(first, ast.List) and first.elts and _dotted(first.elts[0]) == "sys.executable"):
            out.append("line %d: subprocess.run whose argv does not start with sys.executable" % run.lineno)
    return out


def region_gate_problems(source):
    """The narrower gate over the GENERATED region itself."""
    begin, end = region_lines(source)
    tree = ast.parse(source)
    parents = _parents(tree)
    out, runs = [], []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line is None or not (begin <= line <= end):
            continue
        where = "region line %d" % line
        if isinstance(node, ast.keyword) and node.arg == "shell":
            out.append("%s: shell=" % where)
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func) or ""
        if name == "os.open":
            leaves = _flag_leaves(node.args[1]) if len(node.args) > 1 else None
            if leaves is None or "O_RDONLY" not in leaves or WRITE_FLAGS & set(leaves):
                out.append("%s: os.open flags are not read-only" % where)
        if name == "open":
            mode = _mode_of(node, 1)
            if mode is None or _bad_mode(mode):
                out.append("%s: open without a literal read mode" % where)
        if name.startswith("os.") and name[3:] in OS_MUTATORS:
            out.append("%s: %s" % (where, name))
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ATTR_MUTATORS:
            out.append("%s: .%s()" % (where, node.func.attr))
        if name in ("os.system", "os.popen") or name.startswith("os.exec") or name.startswith("os.spawn"):
            out.append("%s: %s" % (where, name))
        if name == "subprocess.run":
            runs.append(node)
    if len(runs) != 1:
        out.append("%d subprocess.run calls in the region, want exactly 1" % len(runs))
    for run in runs:
        first = run.args[0] if run.args else None
        if _enclosing_function(run, parents) != "_parent_pid":
            out.append("region line %d: subprocess.run outside _parent_pid" % run.lineno)
        if not (isinstance(first, ast.List) and first.elts and isinstance(first.elts[0], ast.Name) and first.elts[0].id == "ps"):
            out.append("region line %d: subprocess.run argv does not start with the name ps" % run.lineno)
    return out


# Each planted copy: (label, text, where) -- `where` "end" appends outside the
# region, "region" inserts just above the END marker.
CONTROL_MUTANTS = (
    ("path-write-text", "def _m():\n    Path(\"x\").write_text(\"y\")  # MUTATION\n", "end"),
    ("os-remove", "def _m():\n    os.remove(\"x\")  # MUTATION\n", "end"),
    ("shutil-import", "import shutil  # MUTATION\n", "end"),
    ("open-append", "def _m(p):\n    return open(p, \"a\")  # MUTATION\n", "end"),
    ("second-subprocess-run", "def _m():\n    subprocess.run([sys.executable, \"-c\", \"0\"])  # MUTATION\n", "end"),
    ("globals-subscript", "def _m():\n    return globals()[\"_parent_pid\"]  # MUTATION\n", "end"),
    ("getattr-sys-modules", "def _m(n):\n    return getattr(sys.modules[__name__], n)  # MUTATION\n", "end"),
    ("getattr-literal-region-name", "def _m():\n    return getattr(sys.modules[__name__], \"_parent_pid\")  # MUTATION\n", "end"),
    ("region-os-open-wronly", "_M1 = os.open(\"x\", os.O_WRONLY)  # MUTATION\n", "region"),
    ("region-os-unlink", "_M2 = os.unlink(\"x\")  # MUTATION\n", "region"),
    ("region-second-run", "_M3 = subprocess.run([\"ps\"])  # MUTATION\n", "region"),
)


def plant(source, text, where):
    if where == "end":
        return source + "\n\n" + text
    lines = source.splitlines(keepends=True)
    _begin, end = region_lines(source)
    lines.insert(end - 1, text)
    return "".join(lines)


def group_gates(suite, work):
    with open(TARGET, encoding="utf-8") as fh:
        source = fh.read()
    probs = gate_problems(source)
    suite.record(GF, "ast-gate-outside-region", probs)
    probs = region_gate_problems(source)
    suite.record(GF, "region-gate", probs)
    probs, detail = [], []
    for label, text, where in CONTROL_MUTANTS:
        planted = work.write_text(os.path.join("control", label + ".py"), plant(source, text, where))
        with open(planted, encoding="utf-8") as fh:
            mutated = fh.read()
        found = gate_problems(mutated) + region_gate_problems(mutated)
        detail.append("%s: %s" % (label, found[0] if found else "NOT CAUGHT"))
        if not found:
            probs.append("the %s mutant passed both gates" % label)
    copy = work.write_text(os.path.join("control", "unmodified.py"), source)
    with open(copy, encoding="utf-8") as fh:
        clean = fh.read()
    probs += problem_if(gate_problems(clean) + region_gate_problems(clean), "the unmodified copy is red")
    suite.record(GF, "control-group", probs, detail=detail)


# ---------------------------------------------------------------------------
# G. HYGIENE
# ---------------------------------------------------------------------------

def group_hygiene(suite, tree_before, pyc_before, live_before):
    new_paths = sorted(H.repo_tree() - tree_before)
    suite.record(GG, "no-new-repo-paths", problem_if(new_paths, "new paths: %r" % new_paths[:5]))
    suite.record(GG, "no-bytecode", problem_if(H.pycache_snapshot() != pyc_before, "a __pycache__ entry changed"))
    live_after = H.sha256_file(LIVE_ROADMAP) if os.path.isfile(LIVE_ROADMAP) else None
    suite.record(GG, "live-roadmap-untouched", problem_if(live_after != live_before, "docs/roadmap/roadmap.md changed"))


# ---------------------------------------------------------------------------

def run(opts=None):
    global _SANDBOX
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="pack.py offline core: roadmap events through the real roadmap.py, "
                                "text hygiene, readers, the digest write site and the AST gates",
                    opts=opts, mode="grouped")
    tree_before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    live_before = H.sha256_file(LIVE_ROADMAP) if os.path.isfile(LIVE_ROADMAP) else None
    work = H.TempWorkspace("pack-apply-", keep=opts.keep)
    _SANDBOX = os.path.realpath(work.path)
    try:
        mod = H.load_module_from_path("pack_under_test", TARGET)
        with isolated_env():
            for name, body in (("apply", lambda: group_apply(suite, mod, work)),
                               ("events", lambda: group_events(suite, mod, work)),
                               ("text", lambda: group_text(suite, mod)),
                               ("readers", lambda: group_readers(suite, mod, work)),
                               ("digest", lambda: group_digest(suite, mod, work)),
                               ("gates", lambda: group_gates(suite, work))):
                try:
                    body()
                except Exception as exc:  # a crashed group is a red case, never a lost one
                    suite.record(GG, "group-%s-crashed" % name, ["%s: %s" % (exc.__class__.__name__, exc)])
    finally:
        work.cleanup()
        _SANDBOX = None
    group_hygiene(suite, tree_before, pyc_before, live_before)
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
