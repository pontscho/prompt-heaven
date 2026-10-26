#!/usr/bin/env python3
"""Offline suite for the Jenkins job CLI (ClaudeCode/skills/jenkins-job/scripts/jenkins_job.py).

The CLI talks to a Jenkins over HTTPS only.  This suite never dials a real host:
the one place it speaks HTTP is a fake Jenkins on 127.0.0.1 with an ephemeral port,
reachable only because the CLI honours the TEST-ONLY override
JENKINS_JOB_ALLOW_INSECURE_LOOPBACK=1 for the hosts 127.0.0.1 and ::1.  Every case
that is about a refusal is checked on MEASUREMENTS -- the fake's request log -- not
on the shape of the source: a dry run sends no write, a refused clone creates
nothing, a failed config POST never prints the token.

Groups:
  A  the door: scheme, userinfo, missing env, the path and name rules
  B  reads: whoami, get-config and its masking
  C  dry runs send no write
  D  --apply: the request sequence, the patch, the token
  E  failures: duplicate, missing trigger/source, half-created, POST failure,
     cross-origin redirect, size cap, hostile text
  F  hygiene and documentation

Usage:
  python3 tests/test_jenkins_job_cli.py [--brief]
Exit code 0 iff every case passes.
"""

import contextlib
import http.server
import io
import json
import os
import re
import sys
import threading
import urllib.parse

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "jenkins_job_cli"
SKILL_DIR = H.repo_path("ClaudeCode", "skills", "jenkins-job")
TARGET = os.path.join(SKILL_DIR, "scripts", "jenkins_job.py")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
PR_TEMPLATE = os.path.join(SKILL_DIR, "templates", "discover-pr-refs.xml")

GA = "A. the door"
GB = "B. reads and masking"
GC = "C. dry runs"
GD = "D. apply"
GE = "E. failures"
GF = "F. hygiene and docs"

USER = "zoltan"
API_TOKEN = "api-token-SECRET-xyz"
SOURCE = "sl/ngs-media-server/ngs-media-server"
FOLDER = "sl/ngs-media-server"
SOURCE_TOKEN = "SOURCE-WEBHOOK-TOKEN-1234567890"
ENC_BLOB = "{AQAAABAAAAAQSECRETBLOB1234567890abcdef=}"
CRUMB = "crumb-value-123"

TEMPLATE = """<?xml version='1.1' encoding='UTF-8'?>
<org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject plugin="workflow-multibranch@1">
  <description>template</description>
  <displayName>Template</displayName>
  <triggers>
    <com.igalg.jenkins.plugins.mswt.trigger.ComputedFolderWebHookTrigger plugin="multibranch-scan-webhook-trigger@1">
      <spec></spec>
      <token>%(token)s</token>
    </com.igalg.jenkins.plugins.mswt.trigger.ComputedFolderWebHookTrigger>
  </triggers>
  <sources class="jenkins.branch.MultiBranchProject$BranchSourceList">
    <data>
      <jenkins.branch.BranchSource>
        <source class="jenkins.plugins.git.GitSCMSource" plugin="git@5">
          <id>abc</id>
          <remote>ssh://git@stash.example:7999/sl/ngs-media-server.git</remote>
          <credentialsId>sl-stash-key</credentialsId>
          <traits>
            <jenkins.plugins.git.traits.BranchDiscoveryTrait/>
          </traits>
        </source>
      </jenkins.branch.BranchSource>
    </data>
  </sources>
  <factory class="org.jenkinsci.plugins.workflow.multibranch.WorkflowBranchProjectFactory">
    <scriptPath>deploy/jenkins/Jenkinsfile</scriptPath>
  </factory>
  <password>%(blob)s</password>
  <note>carries %(blob)s inline</note>
</org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject>
""" % {"token": SOURCE_TOKEN, "blob": ENC_BLOB}

NO_TRIGGER = TEMPLATE.replace(
    "com.igalg.jenkins.plugins.mswt.trigger.ComputedFolderWebHookTrigger",
    "hudson.triggers.OtherTrigger")
NO_GIT = TEMPLATE.replace("jenkins.plugins.git.GitSCMSource", "other.Source")

TRAIT_SNIPPET = ("<jenkins.plugins.git.traits.CleanBeforeCheckoutTrait>"
                 "<extension class=\"hudson.plugins.git.extensions.impl."
                 "CleanBeforeCheckout\"/></jenkins.plugins.git.traits."
                 "CleanBeforeCheckoutTrait>")


def problem_if(condition, message):
    return [message] if condition else []


# ---------------------------------------------------------------------------
# the fake Jenkins
# ---------------------------------------------------------------------------

class Fake(object):
    def __init__(self):
        self.jobs = {SOURCE: TEMPLATE.encode("utf-8")}
        self.log = []               # (method, path, query, headers, body)
        self.crumb = True
        self.newjob = {FOLDER: 200}
        self.fail = {}              # route name -> (status, body bytes)
        self.copy_transform = None
        self.redirect_to = None     # config GET redirects here when set

    def posts(self):
        return [e for e in self.log if e[0] == "POST"]

    def paths(self, method=None):
        return [(e[0], e[1]) for e in self.log if method in (None, e[0])]


def make_handler(fake):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            parsed = urllib.parse.urlparse(self.path)
            query = dict(urllib.parse.parse_qsl(parsed.query))
            headers = {k.lower(): v for k, v in self.headers.items()}
            fake.log.append((method, parsed.path, query, headers, body))
            import base64
            want = "Basic " + base64.b64encode(
                ("%s:%s" % (USER, API_TOKEN)).encode()).decode()
            if headers.get("authorization") != want:
                return self._send(401, b"no")
            parts = [urllib.parse.unquote(p)
                     for p in parsed.path.strip("/").split("/") if p]
            names, i = [], 0
            while i + 1 < len(parts) and parts[i] == "job":
                names.append(parts[i + 1])
                i += 2
            tail = "/".join(parts[i:])
            key = "/".join(names)
            base = "http://%s" % self.headers.get("Host")

            if method == "GET" and tail == "crumbIssuer/api/json":
                if not fake.crumb:
                    return self._send(404, b"nope")
                return self._send(200, json.dumps(
                    {"crumbRequestField": "Jenkins-Crumb", "crumb": CRUMB}
                ).encode(), {"Set-Cookie": "JSESSIONID=sess1; Path=/"})
            if method == "POST" and fake.crumb:
                if (headers.get("jenkins-crumb") != CRUMB
                        or "JSESSIONID=sess1" not in headers.get("cookie", "")):
                    return self._send(403, b"No valid crumb was included")
            if method == "GET" and not names and tail == "me/api/json":
                return self._send(200, json.dumps(
                    {"id": USER, "fullName": "Zoltan P"}).encode())
            if method == "GET" and tail == "newJob":
                return self._send(fake.newjob.get(key, 403), b"<html/>")
            if method == "GET" and tail == "api/json":
                return self._send(200 if key in fake.jobs else 404, b"{}")
            if method == "GET" and tail == "config.xml":
                if key == "redir" and fake.redirect_to:
                    return self._send(302, b"", {"Location": fake.redirect_to})
                if "config_get" in fake.fail:
                    st, text = fake.fail["config_get"]
                    return self._send(st, text)
                if key in fake.jobs:
                    return self._send(200, fake.jobs[key])
                return self._send(404, b"missing")
            if method == "POST" and tail == "createItem":
                if "createItem" in fake.fail:
                    st, text = fake.fail["createItem"]
                    return self._send(st, text)
                src = query.get("from", "").lstrip("/")
                if src not in fake.jobs:
                    return self._send(404, b"no source")
                newkey = (key + "/" if key else "") + query.get("name", "")
                data = fake.jobs[src]
                if fake.copy_transform:
                    data = fake.copy_transform(data)
                fake.jobs[newkey] = data
                loc = base + "".join("/job/" + urllib.parse.quote(n)
                                     for n in newkey.split("/")) + "/"
                return self._send(302, b"", {"Location": loc})
            if method == "POST" and tail == "config.xml":
                if "config_post" in fake.fail:
                    st, text = fake.fail["config_post"]
                    return self._send(st, text)
                fake.jobs[key] = body
                return self._send(200, b"")
            if method == "POST" and tail == "build":
                if "build" in fake.fail:
                    st, text = fake.fail["build"]
                    return self._send(st, text)
                return self._send(201, b"")
            return self._send(404, b"unrouted")

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


class Server(object):
    def __init__(self, handler):
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_counter():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler, hits


# ---------------------------------------------------------------------------
# driving the CLI
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def environment(**extra):
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("JENKINS_"):
            del os.environ[key]
    os.environ.update(extra)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def call(mod, argv, url, loopback=True, extra_env=None, token=API_TOKEN):
    env = {"JENKINS_ENDPOINT": url, "JENKINS_USERNAME": USER,
           "JENKINS_TOKEN": token}
    if loopback:
        env["JENKINS_JOB_ALLOW_INSECURE_LOOPBACK"] = "1"
    env.update(extra_env or {})
    out, err = io.StringIO(), io.StringIO()
    with environment(**env), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        try:
            rc = mod.main(list(argv))
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def one_line(err):
    return err.strip().count("\n") == 0 and "Traceback" not in err \
        and err.strip() != ""


def clone_argv(name="ngs-webrtc", *more):
    return ["clone", SOURCE, name, "--folder", FOLDER] + list(more)


def token_lines(out):
    return [l for l in out.splitlines() if "token" in l.lower()
            and ("invoke?token=" in l or "webhook token" in l)]


def find_token(out):
    m = re.search(r"invoke\?token=([A-Za-z0-9._~%-]+)", out)
    return urllib.parse.unquote(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def group_a(suite, mod, fake, url):
    rc, out, err = call(mod, ["get-config", "a/b"], "http://jenkins.example.com",
                        extra_env=None)
    suite.record(GA, "http-non-loopback-refused-even-with-override",
                 problem_if(rc != 2 or not one_line(err)
                            or "cleartext" not in err,
                            "rc=%r err=%r" % (rc, err)))

    n = len(fake.log)
    rc, out, err = call(mod, ["get-config", "a/b"], url, loopback=False)
    suite.record(GA, "http-loopback-refused-without-override",
                 problem_if(rc != 2 or not one_line(err) or len(fake.log) != n,
                            "rc=%r err=%r requests=%d" % (rc, err,
                                                         len(fake.log) - n)))

    rc, out, err = call(mod, ["get-config", "a/b"],
                        "https://bob:hunter2@jenkins.example.com")
    suite.record(GA, "userinfo-refused-not-echoed",
                 problem_if(rc != 2 or "hunter2" in err + out
                            or not one_line(err), "rc=%r err=%r" % (rc, err)))

    out, err = io.StringIO(), io.StringIO()
    with environment(), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = mod.main(["whoami"])
    suite.record(GA, "missing-env-names-what-is-missing",
                 problem_if(rc != 2 or not one_line(err.getvalue())
                            or "JENKINS_TOKEN" not in err.getvalue(),
                            "rc=%r err=%r" % (rc, err.getvalue())))

    n = len(fake.log)
    problems = []
    for bad in ("sl/../x", "sl//x", "/sl/x", "sl/./x"):
        rc, out, err = call(mod, ["get-config", bad], url)
        if rc != 2 or not one_line(err):
            problems.append("%r -> rc=%r err=%r" % (bad, rc, err))
    suite.record(GA, "dotdot-empty-segment-refused-zero-requests",
                 problems + problem_if(len(fake.log) != n,
                                       "requests were sent"))

    n = len(fake.log)
    problems = []
    for bad in ("a/b", "..", ".x", "a b?", "x#y", ""):
        rc, out, err = call(mod, clone_argv(bad), url)
        if rc != 2 or not one_line(err):
            problems.append("%r -> rc=%r err=%r" % (bad, rc, err))
    suite.record(GA, "bad-new-name-refused-zero-requests",
                 problems + problem_if(len(fake.log) != n,
                                       "requests were sent"))

    rc, out, err = call(mod, ["get-config", SOURCE, "--raw"], url)
    suite.record(GA, "no-raw-flag-exists",
                 problem_if(rc != 2 or not one_line(err), "rc=%r" % rc))

    rc, out, err = call(mod, ["clone", SOURCE, "x", "--folder", FOLDER,
                              "--webhook-token", "abcdefghijklmnopq",
                              "--generate-webhook-token", "p"], url)
    suite.record(GA, "token-flags-are-exclusive",
                 problem_if(rc != 2 or not one_line(err), "rc=%r" % rc))

    # a real child process, its environment forced to the refused scheme so it
    # can never reach any host the shell happens to be configured for
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-I", "-B", TARGET, "get-config", "a/b"],
        capture_output=True, text=True, timeout=30,
        env=H.child_env({"JENKINS_ENDPOINT": "http://jenkins.example.com",
                         "JENKINS_USERNAME": "u", "JENKINS_TOKEN": "t"}))
    suite.record(GA, "real-process-refusal-is-one-line-no-traceback",
                 problem_if(proc.returncode != 2 or not one_line(proc.stderr)
                            or proc.stdout != "",
                            "rc=%r err=%r" % (proc.returncode, proc.stderr)))


def group_b(suite, mod, fake, url):
    n = len(fake.log)
    rc, out, err = call(mod, ["whoami", "--folder", FOLDER], url)
    suite.record(GB, "whoami-identity-and-can-create",
                 problem_if(rc != 0 or USER not in out or "Zoltan P" not in out
                            or "HTTP 200" not in out
                            or any(e[0] != "GET" for e in fake.log[n:]),
                            "rc=%r out=%r" % (rc, out)))

    rc, out, err = call(mod, ["whoami", "--folder", "other/folder"], url)
    suite.record(GB, "whoami-cannot-create-403",
                 problem_if(rc != 0 or "HTTP 403" not in out
                            or "can create" not in out,
                            "rc=%r out=%r" % (rc, out)))

    rc, out, err = call(mod, ["whoami", "--folder", "x"], url,
                        token="wrong-token")
    suite.record(GB, "whoami-bad-token-one-line-exit-1",
                 problem_if(rc != 1 or not one_line(err)
                            or "wrong-token" in err + out,
                            "rc=%r err=%r" % (rc, err)))

    n = len(fake.log)
    rc, out, err = call(mod, ["get-config", SOURCE], url)
    leaked = [s for s in (SOURCE_TOKEN, ENC_BLOB, "AQAAABAAAAAQSECRET")
              if s in out + err]
    suite.record(GB, "get-config-masks-secrets",
                 problem_if(rc != 0 or leaked or "[REDACTED]" not in out
                            or "sl-stash-key" not in out
                            or "deploy/jenkins/Jenkinsfile" not in out,
                            "rc=%r leaked=%r" % (rc, leaked)))

    suite.record(GB, "get-config-needs-no-crumb",
                 problem_if(any("crumbIssuer" in e[1] for e in fake.log[n:])
                            or any(e[0] != "GET" for e in fake.log[n:]),
                            "reads fetched a crumb or wrote"))

    rc, out, err = call(mod, ["get-config", "no/such/job"], url)
    suite.record(GB, "get-config-404-one-line-exit-1",
                 problem_if(rc != 1 or not one_line(err) or "404" not in err,
                            "rc=%r err=%r" % (rc, err)))


def group_c(suite, mod, fake, url, workspace):
    n = len(fake.log)
    rc, out, err = call(mod, clone_argv(
        "dry-one", "--remote", "ssh://git@stash/sl/new.git",
        "--credentials-id", "new-key", "--script-path", "ci/Jenkinsfile",
        "--generate-webhook-token", "dry"), url)
    posts = [e for e in fake.log[n:] if e[0] != "GET"]
    suite.record(GC, "clone-dry-run-sends-no-write",
                 problem_if(rc != 0 or posts or "DRY RUN" not in out
                            or "createItem" not in out
                            or "mode=copy" not in out
                            or "ssh://git@stash/sl/new.git" not in out
                            or "invoke?token=" in out
                            or (FOLDER + "/dry-one") in fake.jobs,
                            "rc=%r posts=%r out=%r" % (rc, posts, out)))

    rc, out, err = call(mod, clone_argv(
        "dry-two", "--remote", "https://bob:pw123secret@git.example/x.git"),
        url)
    suite.record(GC, "remote-password-never-echoed-in-the-summary",
                 problem_if(rc != 0 or "pw123secret" in out + err
                            or "git.example/x.git" not in out,
                            "rc=%r out=%r" % (rc, out)))

    f = workspace.write_text("cfg.xml", TEMPLATE)
    n = len(fake.log)
    rc, out, err = call(mod, ["set-config", SOURCE, "--file", f], url)
    suite.record(GC, "set-config-dry-run-sends-nothing",
                 problem_if(rc != 0 or len(fake.log) != n
                            or "DRY RUN" not in out or "POST" not in out,
                            "rc=%r new=%r" % (rc, fake.log[n:])))

    n = len(fake.log)
    rc, out, err = call(mod, ["scan", SOURCE], url)
    suite.record(GC, "scan-dry-run-sends-nothing",
                 problem_if(rc != 0 or len(fake.log) != n
                            or "build?delay=0" not in out,
                            "rc=%r new=%r" % (rc, fake.log[n:])))


def group_d(suite, mod, fake, url, workspace):
    snippet = workspace.write_text("trait.xml", TRAIT_SNIPPET)
    n = len(fake.log)
    rc, out, err = call(mod, clone_argv(
        "ngs-webrtc", "--remote", "ssh://git@stash/sl/ngs-webrtc.git",
        "--credentials-id", "new-key", "--script-path", "ci/Jenkinsfile",
        "--display-name", "NGS WebRTC", "--description", "wired",
        "--trait-xml", snippet, "--generate-webhook-token", "ngs-webrtc",
        "--apply"), url)
    new = fake.log[n:]
    posts = [e for e in new if e[0] == "POST"]
    stored = fake.jobs.get(FOLDER + "/ngs-webrtc", b"").decode("utf-8")

    problems = []
    if rc != 0:
        problems.append("rc=%r err=%r" % (rc, err))
    if [p[1].rsplit("/", 1)[-1] for p in posts] != ["createItem",
                                                      "config.xml"]:
        problems.append("POST order: %r" % [p[1] for p in posts])
    elif (posts[0][2] != {"name": "ngs-webrtc", "mode": "copy",
                          "from": "/" + SOURCE}
          or "/job/sl/job/ngs-media-server/createItem" != posts[0][1]):
        problems.append("createItem request: %r %r" % (posts[0][1],
                                                       posts[0][2]))
    suite.record(GD, "apply-sends-createitem-copy-then-config", problems)

    problems = []
    for post in posts:
        if post[3].get("jenkins-crumb") != CRUMB:
            problems.append("no crumb on %s" % post[1])
        if "JSESSIONID=sess1" not in post[3].get("cookie", ""):
            problems.append("no session cookie on %s" % post[1])
    suite.record(GD, "writes-carry-crumb-and-session-cookie", problems)

    want = ["ssh://git@stash/sl/ngs-webrtc.git", "<credentialsId>new-key<",
            "<scriptPath>ci/Jenkinsfile<", "<displayName>NGS WebRTC<",
            "<description>wired<", "CleanBeforeCheckoutTrait",
            "BranchDiscoveryTrait"]
    missing = [w for w in want if w not in stored]
    suite.record(GD, "patch-applied-and-untouched-parts-kept",
                 missing + problem_if("ssh://git@stash.example" in stored,
                                      "old remote survived"))

    token = find_token(out)
    suite.record(GD, "token-new-not-the-sources-and-stored",
                 problem_if(not token or token == SOURCE_TOKEN
                            or SOURCE_TOKEN in stored
                            or ("<token>%s</token>" % token) not in stored
                            or not token.startswith("ngs-webrtc-")
                            or len(token) < 30,
                            "token=%r" % token))

    suite.record(GD, "token-printed-once-with-invoke-url-never-stderr",
                 problem_if(len(token_lines(out)) != 2
                            or (url + "/multibranch-webhook-trigger/invoke"
                                "?token=" + (token or "?")) not in out
                            or (token or "?") in err,
                            "lines=%r err=%r" % (token_lines(out), err)))

    other = [str(p) for p in os.listdir(workspace.path)
             if token and token in open(os.path.join(workspace.path, p),
                                        errors="replace").read()]
    suite.record(GD, "token-written-to-no-file", other)

    rc, out, err = call(mod, clone_argv(
        "ngs-pr", "--trait-xml", PR_TEMPLATE, "--generate-webhook-token",
        "ngs-pr", "--apply"), url)
    stored = fake.jobs.get(FOLDER + "/ngs-pr", b"").decode("utf-8")
    m = re.search(r"<traits>(.*?)</traits>", stored, re.S)
    inside = m.group(1) if m else ""
    suite.record(GD, "pr-refs-template-lands-inside-traits",
                 problem_if(rc != 0 or not m
                            or "DiscoverOtherRefsTrait" not in inside
                            or "<ref>pull-requests/*/from</ref>" not in inside
                            or "<nameMapping>PR-@{1}</nameMapping>"
                            not in inside
                            or "BranchDiscoveryTrait" not in inside,
                            "rc=%r err=%r" % (rc, err)))

    rc2, out2, err2 = call(mod, clone_argv(
        "ngs-two", "--generate-webhook-token", "ngs-webrtc", "--apply"), url)
    suite.record(GD, "tokens-differ-between-runs",
                 problem_if(rc2 != 0 or not find_token(out2)
                            or find_token(out2) == token,
                            "rc=%r" % rc2))

    rc, out, err = call(mod, clone_argv(
        "ngs-explicit", "--webhook-token", "explicit-token-0123456789",
        "--apply"), url)
    stored = fake.jobs.get(FOLDER + "/ngs-explicit", b"").decode()
    suite.record(GD, "explicit-token-honoured",
                 problem_if(rc != 0 or find_token(out)
                            != "explicit-token-0123456789"
                            or "<token>explicit-token-0123456789</token>"
                            not in stored, "rc=%r" % rc))

    n = len(fake.log)
    rc, out, err = call(mod, clone_argv(
        "ngs-same", "--webhook-token", SOURCE_TOKEN, "--apply"), url)
    suite.record(GD, "explicit-token-equal-to-source-refused-zero-writes",
                 problem_if(rc != 2 or not one_line(err)
                            or [e for e in fake.log[n:] if e[0] == "POST"],
                            "rc=%r err=%r" % (rc, err)))

    fake.crumb = False
    rc, out, err = call(mod, clone_argv("ngs-nocrumb", "--apply"), url)
    posts = [e for e in fake.log if e[1].endswith("/job/ngs-nocrumb/config.xml")
             and e[0] == "POST"]
    fake.crumb = True
    suite.record(GD, "crumb-issuer-disabled-404-tolerated",
                 problem_if(rc != 0 or not posts
                            or "jenkins-crumb" in posts[0][3],
                            "rc=%r err=%r" % (rc, err)))

    fresh = workspace.write_text("fresh.xml", TEMPLATE.replace(
        "template", "fresh"))
    n = len(fake.log)
    rc, out, err = call(mod, ["set-config", SOURCE, "--file", fresh,
                              "--apply"], url)
    posts = [e for e in fake.log[n:] if e[0] == "POST"]
    suite.record(GD, "set-config-apply-posts-the-file-bytes",
                 problem_if(rc != 0 or len(posts) != 1
                            or posts[0][4] != TEMPLATE.replace(
                                "template", "fresh").encode()
                            or posts[0][3].get("jenkins-crumb") != CRUMB,
                            "rc=%r posts=%d" % (rc, len(posts))))

    bad = {"malformed.xml": "<a><b></a>",
           "doctype.xml": "<!DOCTYPE a [<!ENTITY x 'y'>]><a>&x;</a>",
           "huge.xml": "<a>" + "x" * (1024 * 1024 + 10) + "</a>"}
    problems = []
    n = len(fake.log)
    for fname, text in bad.items():
        path = workspace.write_text(fname, text)
        rc, out, err = call(mod, ["set-config", SOURCE, "--file", path,
                                  "--apply"], url)
        if rc != 2 or not one_line(err):
            problems.append("%s rc=%r err=%r" % (fname, rc, err))
    suite.record(GD, "set-config-refuses-malformed-doctype-oversize",
                 problems + problem_if(len(fake.log) != n,
                                       "requests were sent"))

    n = len(fake.log)
    rc, out, err = call(mod, ["scan", SOURCE, "--apply"], url)
    posts = [e for e in fake.log[n:] if e[0] == "POST"]
    suite.record(GD, "scan-apply-posts-build-delay-0-and-reports-status",
                 problem_if(rc != 0 or len(posts) != 1
                            or posts[0][1] != "/job/sl/job/ngs-media-server"
                            "/job/ngs-media-server/build"
                            or posts[0][2] != {"delay": "0"}
                            or "201" not in out,
                            "rc=%r out=%r" % (rc, out)))

    fake.fail["build"] = (403, b"<html>forbidden</html>")
    rc, out, err = call(mod, ["scan", SOURCE, "--apply"], url)
    del fake.fail["build"]
    suite.record(GD, "scan-failure-one-line-exit-1",
                 problem_if(rc != 1 or not one_line(err) or "403" not in err,
                            "rc=%r err=%r" % (rc, err)))


def group_e(suite, mod, fake, url, workspace):
    n = len(fake.log)
    rc, out, err = call(mod, clone_argv("ngs-webrtc", "--apply"), url)
    suite.record(GE, "duplicate-name-refused-zero-writes",
                 problem_if(rc != 2 or not one_line(err)
                            or "already exists" not in err
                            or [e for e in fake.log[n:] if e[0] == "POST"],
                            "rc=%r err=%r" % (rc, err)))

    fake.jobs["t/no-trigger"] = NO_TRIGGER.encode()
    n = len(fake.log)
    rc, out, err = call(mod, ["clone", "t/no-trigger", "x1", "--folder", "t",
                              "--apply"], url)
    suite.record(GE, "missing-trigger-refused-before-anything-is-created",
                 problem_if(rc != 2 or not one_line(err)
                            or "trigger" not in err
                            or [e for e in fake.log[n:] if e[0] == "POST"],
                            "rc=%r err=%r" % (rc, err)))

    fake.jobs["t/no-git"] = NO_GIT.encode()
    n = len(fake.log)
    rc, out, err = call(mod, ["clone", "t/no-git", "x2", "--folder", "t",
                              "--remote", "ssh://h/r.git", "--apply"], url)
    suite.record(GE, "missing-git-source-refused-before-anything-is-created",
                 problem_if(rc != 2 or not one_line(err)
                            or "GitSCMSource" not in err
                            or [e for e in fake.log[n:] if e[0] == "POST"],
                            "rc=%r err=%r" % (rc, err)))

    # the copy comes back WITHOUT a trigger: half-created, reported by name.
    fake.copy_transform = lambda data: NO_TRIGGER.encode()
    n = len(fake.log)
    rc, out, err = call(mod, clone_argv("half", "--apply"), url)
    fake.copy_transform = None
    new = fake.log[n:]
    suite.record(GE, "half-created-job-reported-by-name-nothing-deleted",
                 problem_if(rc != 1 or not one_line(err) or "'half'" not in err
                            or "nothing was deleted" not in err
                            or [e for e in new if e[0] == "DELETE"]
                            or [e for e in new if e[0] == "POST"
                                and e[1].endswith("/config.xml")]
                            or "invoke?token=" in out,
                            "rc=%r err=%r" % (rc, err)))

    fake.fail["config_post"] = (500, b"<html>kaboom</html>")
    n = len(fake.log)
    rc, out, err = call(mod, clone_argv("cfgfail", "--apply"), url)
    del fake.fail["config_post"]
    suite.record(GE, "config-post-failure-reported-and-token-not-printed",
                 problem_if(rc != 1 or not one_line(err) or "'cfgfail'" not in err
                            or "500" not in err or "invoke?token=" in out
                            or [e for e in fake.log[n:] if e[0] == "DELETE"],
                            "rc=%r err=%r out=%r" % (rc, err, out)))

    fake.fail["createItem"] = (403, b"<html>no create perm</html>")
    n = len(fake.log)
    rc, out, err = call(mod, clone_argv("denied", "--apply"), url)
    del fake.fail["createItem"]
    suite.record(GE, "createitem-403-one-line-no-config-post",
                 problem_if(rc != 1 or not one_line(err) or "403" not in err
                            or [e for e in fake.log[n:] if e[0] == "POST"
                                and e[1].endswith("/config.xml")],
                            "rc=%r err=%r" % (rc, err)))

    handler, hits = make_counter()
    other = Server(handler)
    try:
        fake.redirect_to = other.url + "/steal"
        fake.jobs["redir"] = b"<a/>"
        rc, out, err = call(mod, ["get-config", "redir"], url)
    finally:
        fake.redirect_to = None
        other.close()
    suite.record(GE, "cross-origin-redirect-not-followed-no-credential-leak",
                 problem_if(rc != 1 or not one_line(err) or hits
                            or "redirect" not in err,
                            "rc=%r hits=%r err=%r" % (rc, hits, err)))

    saved = mod.MAX_RESPONSE_BYTES
    mod.MAX_RESPONSE_BYTES = 200
    try:
        rc, out, err = call(mod, ["get-config", SOURCE], url)
    finally:
        mod.MAX_RESPONSE_BYTES = saved
    suite.record(GE, "response-size-cap-refuses",
                 problem_if(rc != 1 or not one_line(err) or "larger" not in err
                            or out != "", "rc=%r err=%r" % (rc, err)))

    fake.fail["config_get"] = (500, b"boom\x1b[31m red\nsecond line\r\nthird")
    rc, out, err = call(mod, ["get-config", SOURCE], url)
    del fake.fail["config_get"]
    suite.record(GE, "hostile-server-text-sanitised-to-one-line",
                 problem_if(rc != 1 or not one_line(err) or "\x1b" in err
                            or "\r" in err, "rc=%r err=%r" % (rc, err)))

    n = len(fake.log)
    rc, out, err = call(mod, ["clone", "no/such/source", "zz", "--folder",
                              FOLDER, "--apply"], url)
    suite.record(GE, "missing-source-one-line-no-writes",
                 problem_if(rc != 1 or not one_line(err)
                            or [e for e in fake.log[n:] if e[0] == "POST"],
                            "rc=%r err=%r" % (rc, err)))


def group_f(suite, before, pyc_before, workspace):
    after = H.repo_tree()
    new = sorted(after - before)
    suite.record(GF, "no-new-repo-paths",
                 problem_if(new, "this suite wrote into the repo: %s"
                            % new[:10]))
    pyc_after = H.pycache_snapshot()
    suite.record(GF, "pycache-zero",
                 problem_if(pyc_after and set(pyc_after) - set(pyc_before),
                            "this run wrote bytecode"))

    try:
        with open(SKILL_MD, encoding="utf-8") as fp:
            doc = fp.read()
    except OSError as exc:
        doc = ""
        suite.record(GF, "skill-md-exists", ["cannot read SKILL.md: %s" % exc])
    else:
        suite.record(GF, "skill-md-exists", [])
    tokens = ["name: jenkins-job", "JENKINS_ENDPOINT", "JENKINS_USERNAME",
              "JENKINS_TOKEN", "JENKINS_JOB_ALLOW_INSECURE_LOOPBACK",
              "whoami", "get-config", "clone", "set-config", "scan",
              "--apply", "PR discovery",
              "multibranch-webhook-trigger/invoke", "notifyBitbucket"]
    suite.record(GF, "skill-md-documents-every-subcommand-and-variable",
                 [t for t in tokens if t not in doc])
    suite.record(GF, "skill-md-references-the-pr-refs-template",
                 problem_if("templates/discover-pr-refs.xml" not in doc
                            or "not yet observed on a live job" not in doc
                            or not os.path.isfile(PR_TEMPLATE),
                            "SKILL.md or the template file is missing"))


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="Jenkins job CLI offline, against a fake Jenkins on "
                          "loopback: the https-only door and the path/name "
                          "rules, reads with secret masking, dry runs that "
                          "send no write, --apply's request sequence with "
                          "crumb and session, a fresh webhook token printed "
                          "once, and the failure paths -- duplicate, missing "
                          "trigger, half-created, POST failure, cross-origin "
                          "redirect, size cap, hostile text",
                    opts=opts, mode="grouped")
    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()

    if not os.path.isfile(TARGET):
        suite.record(GA, "target-exists", ["missing: %s" % TARGET])
        suite.print_summary()
        return suite

    mod = H.load_module_from_path("jenkins_job_under_test", TARGET)
    fake = Fake()
    server = Server(make_handler(fake))
    try:
        with H.TempWorkspace("ph-jenkins-job-", keep=opts.keep) as workspace:
            group_a(suite, mod, fake, server.url)
            group_b(suite, mod, fake, server.url)
            group_c(suite, mod, fake, server.url, workspace)
            group_d(suite, mod, fake, server.url, workspace)
            group_e(suite, mod, fake, server.url, workspace)
            group_f(suite, before, pyc_before, workspace.path)
    finally:
        server.close()
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
