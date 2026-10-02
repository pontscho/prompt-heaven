#!/usr/bin/env python3
"""mcp-webfetch's two roots: the cache directory and save_to containment (R-0054).

The server used to be registered with `--project-root ~/.claude`, and that ONE
root carried two jobs: the disk cache (<root>/.cache/webfetch) and the
containment root of `save_to`, which then landed under ~/.claude in every
project.  They are split now.  `--cache-root DIR` names the cache directory
ITSELF (entries are DIR/<sha256>.json, no suffix appended); without it the
cache is $XDG_CACHE_HOME/web-fetch when that variable is set, non-empty and
absolute, else ~/.cache/web-fetch.  save_to keeps the project root and its
rules exactly as they were -- relative joined to the root, realpath, outside
refused.

Imported in-process with stub bs4 / markdownify present for the load only (the
same pattern as tests/test_mcp_chrome.py:load_webfetch).  The network is never
reached: `_fetch_once` and `_check_host_allowed` are swapped on the module for
each row.  `main()` is driven with sys.argv patched and `handle_fetch` /
`McpServer` / `asyncio` swapped, so no fetch and no server ever starts.

HOME and XDG_CACHE_HOME are pinned into the `tempfile.mkdtemp()` workspace for
the WHOLE run (XDG_CACHE_HOME unset unless a row sets it), because the default
cache directory is a real directory in the developer's home: a row that leaves
the cache root out must exercise the default without touching the real one.
The hygiene group asserts the real default directory was not created.

Groups:
  A  the cache is the cache directory; no cache root means the XDG default
  B  save_to stays contained by the project root, never by the cache dir
  C  the dispatcher, the status reply and the tool description
  D  the CLI: --cache-root default and ~ expansion, both entry points
  X  the default's XDG resolution: set, unset, relative
  E  hygiene
"""

import contextlib
import io
import os
import sys
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "webfetch_roots"
SERVER = H.repo_path("Scripts", "mcp-webfetch.py")
STUBBED = ("bs4", "markdownify")
SWAPPED = ("_fetch_once", "_check_host_allowed", "handle_fetch", "McpServer",
           "asyncio")
URL = "https://example.test/doc"
BODY = "<html><body><p>hello roots</p></body></html>"
# The default's spelling relative to HOME, written here from the decision
# rather than read off the module, so a changed default fails a row.
DEFAULT_REL = os.path.join(".cache", "web-fetch")


class StubSoup:
    """bs4.BeautifulSoup's stand-in: no tags, no main element."""

    def __init__(self, html, _parser=None):
        self.html = html

    def __call__(self, _names):
        return []

    def select_one(self, _selector):
        return None

    def __str__(self):
        return self.html


def stub_markdownify(html, **_opts):
    return "STUB-MARKDOWN\n" + html


def load_webfetch():
    """Scripts/mcp-webfetch.py by path, stub bs4 / markdownify for the load only."""
    missing = object()
    saved = dict((n, sys.modules.get(n, missing)) for n in STUBBED)
    bs4 = types.ModuleType("bs4")
    bs4.BeautifulSoup = StubSoup
    md = types.ModuleType("markdownify")
    md.markdownify = stub_markdownify
    sys.modules["bs4"] = bs4
    sys.modules["markdownify"] = md
    try:
        return H.load_module_from_path("mcp_webfetch_roots_under_test", SERVER)
    finally:
        for name, old in saved.items():
            if old is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


@contextlib.contextmanager
def swapped(mod, **names):
    """Replace module attributes for the block; always put the originals back."""
    old = dict((n, getattr(mod, n)) for n in names)
    for name, value in names.items():
        setattr(mod, name, value)
    try:
        yield
    finally:
        for name, value in old.items():
            setattr(mod, name, value)


@contextlib.contextmanager
def env(**values):
    """Set (str) or unset (None) environment variables for the block."""
    old = dict((k, os.environ.get(k)) for k in values)
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def offline(mod, calls=None):
    """Swaps that keep handle_fetch off the network: a canned 200 per call."""
    seen = calls if calls is not None else []

    def fetch_once(profile, method, url, headers, timeout, max_bytes,
                   allow_private=False):
        seen.append(url)
        return {"status": 200, "final_url": url,
                "headers": {"Content-Type": "text/html; charset=utf-8"},
                "body": BODY, "size": len(BODY), "profile": "verified",
                "transport": "verified", "cert_verified": True}
    return swapped(mod, _fetch_once=fetch_once,
                   _check_host_allowed=lambda _url, _ap: None)


def roots(ws, label):
    """(project root, cache dir): two disjoint realpath-ed siblings."""
    proj = os.path.realpath(ws.subdir(os.path.join(label, "project")))
    cache = os.path.realpath(ws.subdir(os.path.join(label, "cache")))
    return proj, cache


def entries(cache_dir):
    """The *.json cache entries directly in `cache_dir`, sorted."""
    if not os.path.isdir(cache_dir):
        return []
    return sorted(n for n in os.listdir(cache_dir) if n.endswith(".json"))


def json_under(root):
    """Every *.json anywhere under `root`, relative: the project must stay clean."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        found += [os.path.relpath(os.path.join(dirpath, f), root)
                  for f in files if f.endswith(".json")]
    return sorted(found)


def text_of(reply):
    if not isinstance(reply, dict):
        return repr(reply)
    return reply.get("__raw_text__") or reply.get("error") or repr(reply)


def call(fn):
    """fn() or the exception it raised, as ("ok", value) / ("raised", repr)."""
    try:
        return "ok", fn()
    except BaseException as exc:  # SystemExit from argparse included
        return "raised", "%s: %s" % (type(exc).__name__, exc)


def pinned_default():
    """The default the pinned HOME (XDG_CACHE_HOME unset) must resolve to."""
    return os.path.realpath(os.path.join(os.environ["HOME"], DEFAULT_REL))


# ---------------------------------------------------------------------------
# Group A -- the cache is the cache directory
# ---------------------------------------------------------------------------

def group_a(suite, wf, ws):
    proj, cache = roots(ws, "a1")
    with offline(wf):
        kind, got = call(lambda: wf.handle_fetch({"url": URL}, proj,
                                                 cache_root=cache))
    problems = []
    if kind != "ok" or "error" in got:
        problems.append("handle_fetch with cache_root failed: %s"
                        % (got if kind != "ok" else text_of(got))[:200])
    if len(entries(cache)) != 1:
        problems.append("expected ONE entry directly in the cache dir %s, "
                        "found %s" % (cache, entries(cache)))
    nested = [p for p in json_under(cache) if os.sep in p]
    if nested:
        problems.append("entries below a suffix of the cache dir: %s" % nested)
    if json_under(proj):
        problems.append("the project root got cache entries: %s"
                        % json_under(proj))
    suite.record("A", "cache-written-directly-in-cache-dir", problems,
                 detail=["project: %s" % proj, "cache  : %s" % cache,
                         "cache entries: %s" % json_under(cache),
                         "project json: %s" % json_under(proj)])

    calls = []
    with offline(wf, calls):
        kind, got = call(lambda: wf.handle_fetch({"url": URL}, proj,
                                                 cache_root=cache))
    text = got if kind != "ok" else text_of(got)
    problems = []
    if kind != "ok" or "error" in got:
        problems.append("second call failed: %s" % text[:200])
    elif calls:
        problems.append("the second call fetched again (%d fetch) instead of "
                        "reading the cache dir" % len(calls))
    suite.record("A", "cache-read-back-from-cache-dir", problems,
                 detail=["fetches on the second call: %d" % len(calls),
                         "reply head: %s" % text[:120]])

    proj, _cache = roots(ws, "a3")
    default = pinned_default()
    with offline(wf):
        kind, got = call(lambda: wf.handle_fetch({"url": URL}, proj))
    problems = []
    if kind != "ok" or "error" in got:
        problems.append("handle_fetch(params, root) failed: %s"
                        % (got if kind != "ok" else text_of(got))[:200])
    if len(entries(default)) != 1:
        problems.append("with no cache root the entry belongs in the default "
                        "%s, found %s" % (default, entries(default)))
    if json_under(proj):
        problems.append("the project root got cache entries: %s"
                        % json_under(proj))
    suite.record("A", "no-cache-root-means-xdg-default-not-project", problems,
                 detail=["HOME pinned, XDG_CACHE_HOME unset: default %s"
                         % default,
                         "project json: %s" % json_under(proj)])


# ---------------------------------------------------------------------------
# Group B -- save_to is contained by the project root, not the cache dir
# ---------------------------------------------------------------------------

def group_b(suite, wf, ws):
    proj, cache = roots(ws, "b1")
    rel = os.path.join("out", "doc.html")
    with offline(wf):
        kind, got = call(lambda: wf.handle_fetch(
            {"url": URL, "save_to": rel, "output": "html"}, proj,
            cache_root=cache))
    want = os.path.join(proj, rel)
    stray = os.path.join(cache, rel)
    problems = []
    if kind != "ok" or "error" in got:
        problems.append("the save failed: %s"
                        % (got if kind != "ok" else text_of(got))[:200])
    if not os.path.isfile(want):
        problems.append("%s was not written" % want)
    else:
        with open(want, encoding="utf-8") as fh:
            if fh.read() != BODY:
                problems.append("%s is not the body byte-exact" % want)
    if os.path.exists(stray):
        problems.append("save_to landed under the CACHE dir: %s" % stray)
    suite.record("B", "save-to-relative-lands-in-project-root", problems,
                 detail=["save_to=%s" % rel, "expected: %s" % want,
                         "must not exist: %s" % stray])

    proj, cache = roots(ws, "b2")
    target = os.path.join(cache, "steal.html")
    calls = []
    with offline(wf, calls):
        kind, got = call(lambda: wf.handle_fetch(
            {"url": URL, "save_to": target, "output": "html"}, proj,
            cache_root=cache))
    text = got if kind != "ok" else text_of(got)
    problems = []
    if kind != "ok":
        problems.append("raised instead of refusing: %s" % text[:200])
    elif "error" not in got:
        problems.append("a save_to inside the cache dir but outside the "
                        "project root was ACCEPTED")
    elif "outside the project root" not in text:
        problems.append("refused, but not by containment: %s" % text[:200])
    if os.path.exists(target):
        problems.append("%s was written" % target)
    if calls:
        problems.append("the refusal came after a fetch (%d), not before"
                        % len(calls))
    suite.record("B", "save-to-into-cache-dir-refused", problems,
                 detail=["save_to=%s" % target,
                         "the cache dir is no second containment root",
                         "reply: %s" % text[:200]])

    rel_escape = os.path.join("..", "cache", "steal2.html")
    with offline(wf):
        kind, got = call(lambda: wf.handle_fetch(
            {"url": URL, "save_to": rel_escape, "output": "html"}, proj,
            cache_root=cache))
    text = got if kind != "ok" else text_of(got)
    problems = []
    if kind != "ok" or "error" not in got:
        problems.append("save_to=%s was not refused: %s"
                        % (rel_escape, text[:200]))
    if os.path.exists(os.path.join(cache, "steal2.html")):
        problems.append("the relative escape wrote into the cache dir")
    suite.record("B", "save-to-relative-escape-to-cache-dir-refused",
                 problems, detail=["save_to=%s" % rel_escape,
                                   "reply: %s" % text[:200]])


# ---------------------------------------------------------------------------
# Group C -- the dispatcher, the status reply, the tool description
# ---------------------------------------------------------------------------

def group_c(suite, wf, ws):
    proj, cache = roots(ws, "c1")
    with offline(wf):
        kind, got = call(lambda: wf.handle_webfetch_call(
            {"function": "fetch", "params": {"url": URL}}, proj, cache))
    problems = []
    if kind != "ok" or "error" in got:
        problems.append("handle_webfetch_call with a cache dir failed: %s"
                        % (got if kind != "ok" else text_of(got))[:200])
    if len(entries(cache)) != 1 or json_under(proj):
        problems.append("the dispatcher did not thread the cache dir: "
                        "cache %s, project %s" % (entries(cache),
                                                  json_under(proj)))
    suite.record("C", "dispatcher-threads-cache-dir", problems,
                 detail=["cache entries: %s" % entries(cache),
                         "project json: %s" % json_under(proj)])

    kind, got = call(lambda: wf.handle_webfetch_call({}, proj, cache))
    text = got if kind != "ok" else text_of(got)
    problems = []
    if kind != "ok":
        problems.append("status call raised: %s" % text[:200])
    else:
        if "Cache dir: %s\n" % cache not in text:
            problems.append("status does not report Cache dir: %s" % cache)
        if "project: %s" % proj not in text:
            problems.append("status no longer names the project root %s"
                            % proj)
    suite.record("C", "status-reply-shows-cache-dir", problems,
                 detail=["reply: %s" % text[:300]], text=text)

    kind, got = call(lambda: wf.handle_webfetch_call({}, proj))
    text = got if kind != "ok" else text_of(got)
    want = "Cache dir: %s\n" % pinned_default()
    suite.record("C", "status-reply-default-cache-is-xdg-default",
                 [] if kind == "ok" and want in text else
                 ["status without a cache root does not say %r: %s"
                  % (want, text[:200])],
                 detail=["HOME pinned, XDG_CACHE_HOME unset"])

    desc = wf.WEBFETCH_CALL_TOOL["description"]
    line = next((p for p in desc.split("\n\n") if p.startswith("Cache:")),
                "<no Cache: paragraph>")
    problems = []
    for need in ("--cache-root", "$XDG_CACHE_HOME/web-fetch",
                 "~/.cache/web-fetch"):
        if need not in line:
            problems.append("the Cache paragraph does not say %r" % need)
    if ".cache/webfetch" in desc or "<project_root>" in line:
        problems.append("the description still places the cache under a "
                        "root's .cache/webfetch")
    suite.record("C", "description-names-cache-dir-and-default", problems,
                 detail=["Cache paragraph: %s" % line[:260]], text=line)


# ---------------------------------------------------------------------------
# Group D -- the CLI
# ---------------------------------------------------------------------------

def run_main(wf, argv):
    """wf.main() with argv patched; (outcome, handle_fetch calls, servers)."""
    fetches = []
    servers = []

    def handle_fetch(params, project_root, cache_root=None):
        fetches.append((project_root, cache_root))
        return {"__raw_text__": "stub"}

    class Server:
        def __init__(self, project_root, cache_root=None):
            servers.append((project_root, cache_root))

        def run(self):
            return None

    fake_asyncio = types.SimpleNamespace(run=lambda _coro: None)
    old_argv = sys.argv
    sys.argv = ["mcp-webfetch.py"] + list(argv)
    out = io.StringIO()
    try:
        with swapped(wf, handle_fetch=handle_fetch, McpServer=Server,
                     asyncio=fake_asyncio), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            kind, got = call(wf.main)
    finally:
        sys.argv = old_argv
    return (kind, got, out.getvalue()), fetches, servers


def group_d(suite, wf, ws):
    proj, _cache = roots(ws, "d1")
    default = pinned_default()
    outcome, fetches, _servers = run_main(
        wf, ["--test", URL, "--project-root", proj])
    problems = []
    if outcome[0] != "ok":
        problems.append("main --test raised: %s" % outcome[1])
    if len(fetches) != 1:
        problems.append("handle_fetch called %d times" % len(fetches))
    elif fetches[0][1] != default:
        problems.append("without --cache-root the cache dir is %r, not the "
                        "default %r" % (fetches[0][1], default))
    suite.record("D", "cli-test-no-cache-root-is-xdg-default",
                 problems, detail=["handle_fetch got: %s" % fetches,
                                   "output: %s" % outcome[2][:200]])

    want = os.path.join(os.path.realpath(os.environ["HOME"]), "shared")
    outcome, fetches, _servers = run_main(
        wf, ["--test", URL, "--project-root", proj,
             "--cache-root", "~/shared"])
    problems = []
    if outcome[0] != "ok":
        problems.append("main --test --cache-root raised: %s" % outcome[1])
    if len(fetches) != 1:
        problems.append("handle_fetch called %d times; output %s"
                        % (len(fetches), outcome[2][:200]))
    else:
        if fetches[0][1] != want:
            problems.append("--cache-root ~/shared reached handle_fetch as %r,"
                            " not %r" % (fetches[0][1], want))
        if fetches[0][0] != proj:
            problems.append("the project root moved: %r" % (fetches[0][0],))
    suite.record("D", "cli-test-cache-root-tilde-expanded", problems,
                 detail=["expected cache dir: %s" % want,
                         "handle_fetch got: %s" % fetches])

    outcome, _fetches, servers = run_main(
        wf, ["--project-root", proj, "--cache-root", "~/shared"])
    problems = []
    if outcome[0] != "ok":
        problems.append("server-mode main raised: %s" % outcome[1])
    if len(servers) != 1:
        problems.append("McpServer constructed %d times" % len(servers))
    elif servers[0][1] is None:
        problems.append("McpServer was not handed the cache root")
    suite.record("D", "cli-server-hands-cache-root-to-mcpserver", problems,
                 detail=["McpServer got: %s" % servers])

    kind, srv = call(lambda: wf.McpServer(proj, "~/shared"))
    kind2, srv2 = call(lambda: wf.McpServer(proj))
    problems = []
    if kind != "ok":
        problems.append("McpServer(root, cache_root) raised: %s" % srv)
    elif getattr(srv, "cache_root", None) != want:
        problems.append("McpServer cache_root is %r, not %r"
                        % (getattr(srv, "cache_root", None), want))
    if kind2 != "ok":
        problems.append("McpServer(root) raised: %s" % srv2)
    elif getattr(srv2, "cache_root", None) != default:
        problems.append("McpServer(root).cache_root is %r, not the default "
                        "%r" % (getattr(srv2, "cache_root", None), default))
    suite.record("D", "mcpserver-cache-root-expanded-and-defaulted", problems,
                 detail=["expected: %s / %s" % (want, default)])


# ---------------------------------------------------------------------------
# Group X -- the default's XDG resolution
# ---------------------------------------------------------------------------

def group_x(suite, wf, ws):
    proj, _cache = roots(ws, "x")
    home = os.path.realpath(os.environ["HOME"])
    xdg = os.path.realpath(ws.subdir(os.path.join("x", "xdg")))
    cases = (
        ("xdg-cache-home-set-is-used", xdg,
         os.path.join(xdg, "web-fetch"),
         "XDG_CACHE_HOME absolute -> $XDG_CACHE_HOME/web-fetch"),
        ("xdg-cache-home-unset-or-empty-means-home-cache", None,
         os.path.join(home, DEFAULT_REL),
         "XDG_CACHE_HOME unset (and, same row, empty) -> <HOME>/.cache/web-fetch"),
        ("xdg-cache-home-relative-is-ignored", "relative/xdg",
         os.path.join(home, DEFAULT_REL),
         "XDG_CACHE_HOME relative -> ignored, <HOME>/.cache/web-fetch"),
    )
    for cid, value, want, why in cases:
        values = [value] if value is not None else [None, ""]
        problems, seen = [], []
        for v in values:
            with env(XDG_CACHE_HOME=v):
                kind, srv = call(lambda: wf.McpServer(proj))
            got = getattr(srv, "cache_root", srv) if kind == "ok" else srv
            seen.append((v, got))
            if got != want:
                problems.append("XDG_CACHE_HOME=%r -> %r, wanted %r"
                                % (v, got, want))
        suite.record("X", cid, problems,
                     detail=[why, "HOME=%s" % home, "seen: %s" % seen])


# ---------------------------------------------------------------------------
# Group E -- hygiene
# ---------------------------------------------------------------------------

def group_e(suite, wf, originals, real_default, existed, before, pyc_before):
    moved = [n for n in SWAPPED if getattr(wf, n) is not originals[n]]
    suite.record("E", "swapped-names-restored",
                 [] if not moved else ["not restored: %s" % moved],
                 detail=["`is` identity on %s" % ", ".join(SWAPPED)])
    stubs = [n for n in STUBBED
             if getattr(sys.modules.get(n), "BeautifulSoup", None) is StubSoup
             or getattr(sys.modules.get(n), "markdownify", None)
             is stub_markdownify]
    suite.record("E", "stub-modules-gone-from-sys-modules",
                 [] if not stubs else ["stubs left in sys.modules: %s" % stubs])
    created = not existed and os.path.exists(real_default)
    suite.record("E", "real-default-cache-dir-untouched",
                 [] if not created else
                 ["this run CREATED the real cache dir %s" % real_default],
                 detail=["real default (environment before pinning): %s"
                         % real_default,
                         "existed before the run: %s" % existed])
    after = H.repo_tree()
    new = sorted(after - before)
    suite.record("E", "no-new-repo-paths",
                 [] if not new else
                 ["this suite wrote into the repo tree: %s" % new[:12]],
                 detail=["%d path(s) before, %d after" % (len(before),
                                                          len(after))])
    # A delta, not the absolute form: `tests/test_purity_lsp.py:_pyc_problems`
    # is where zero is asserted outright.
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after
                     if pyc_before.get(p) != pyc_after[p])
    suite.record("E", "no-new-bytecode",
                 [] if not changed else
                 ["this run wrote or touched bytecode: %s" % changed[:6]],
                 detail=["%d .pyc before, %d after" % (len(pyc_before),
                                                        len(pyc_after))])


# ---------------------------------------------------------------------------

def run(opts=None):
    """Load mcp-webfetch, drive both roots offline, return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="mcp-webfetch: the cache is the --cache-root dir "
                          "(default XDG), save_to stays contained by the "
                          "project root",
                    opts=opts, mode="stream", group_width=3, cid_width=48)

    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    wf = load_webfetch()
    originals = dict((n, getattr(wf, n)) for n in SWAPPED)
    # The real default, computed from the UNPINNED environment, so the hygiene
    # row can prove no row reached it.
    real_default = wf._default_cache_root()
    existed = os.path.exists(real_default)

    with H.TempWorkspace("ph-webfetch-roots-", keep=opts.keep) as ws:
        with env(HOME=ws.subdir("home"), XDG_CACHE_HOME=None):
            suite.note("      server  : %s" % SERVER)
            suite.note("      fixture : %s" % ws.path)
            group_a(suite, wf, ws)
            group_b(suite, wf, ws)
            group_c(suite, wf, ws)
            group_d(suite, wf, ws)
            group_x(suite, wf, ws)

    group_e(suite, wf, originals, real_default, existed, before, pyc_before)
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
