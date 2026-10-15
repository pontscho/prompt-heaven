#!/usr/bin/env python3
"""Scripts/fetch.py, the browser-like single-URL fetcher: offline, no network.

The script carries the generated Chrome client (Scripts/_mcp_chrome.py) and its
decoders; that part is gated by the generated_region and mcp_chrome suites.
What is judged here is fetch.py's own half: its command line, its exit-code
and one-stderr-line contract, the cache, the pacing and the retry loop.

Nothing here touches the network. The CLI rows that run a real process use a
URL the client's public-only connect policy refuses (127.0.0.1) or a cache hit,
which never reaches the client; the loop rows swap in a scripted session
(`Fetcher(session=...)`, or `_ch_session_new` for the in-process `main` rows)
and fake `sleep` / `clock` / `rand` so the schedule is read off, not waited for.

Groups:
  A  import and the command line: no import side effects, --help, usage errors
  B  a refused URL (client URL rule, connect policy): exit 1, exactly one
     `fetch.py:` stderr line, no retry sleep
  C  the cache: a pre-seeded hit with no network, --out, --meta, binary stdout,
     damaged and non-2xx entries ignored
  D  pure helpers: the backoff schedule, Retry-After, the stderr sanitizer
  E  the Fetcher loop on a scripted session: pacing, retries, rebuild, caching
  F  main() in-process: --meta on an HTTP-error exit, stdout bytes, exit codes
  H  POST (offline only): method/data/header validation, the header parser,
     the cache bypass, the scripted-session request (body, headers, cors mode,
     pacing, backoff, refusals) and the CLI usage errors
  G  hygiene
"""

import io
import json
import os
import subprocess
import sys
import time
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "fetch"
SCRIPT = H.repo_path("Scripts", "fetch.py")
SEEDED_URL = "http://127.0.0.1:1/seeded"
BINARY_BODY = b"\x00\xff\xfe<html>\xc3\x28 binary-safe\r\n\x1b[31m"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def run_cli(args, timeout=60):
    """Run fetch.py as a child; return (rc, stdout bytes, stderr text)."""
    proc = subprocess.run([sys.executable, "-B", SCRIPT] + list(args),
                          input=b"", capture_output=True, timeout=timeout,
                          env=H.child_env())
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


class Rand:
    """Deterministic stand-in for the Fetcher's random source."""

    def random(self):
        return 0.0

    def uniform(self, low, high):
        return low


class Clock:
    """A clock that only moves when the fake sleep moves it."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Headers:
    def __init__(self, mapping):
        self.map = dict((k.lower(), v) for k, v in (mapping or {}).items())

    def get(self, name, default=None):
        return self.map.get(name.lower(), default)


class StubSession:
    """A session that answers a script: an int status, a (status, headers, body) tuple or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.requests = []
        self.closed = False

    def _answer(self, url):
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, hdrs, body = item if isinstance(item, tuple) else (item, {}, b"body%d" % item)
        return types.SimpleNamespace(status_code=status, url=url + "#final",
                                     headers=Headers(hdrs), content=body)

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        self.calls.append((url, dict(headers or {}), timeout))
        return self._answer(url)

    def request(self, method, url, headers=None, body=None, timeout=None, mode="navigate",
                referer=None, on_headers=None):
        self.requests.append({"method": method, "url": url, "headers": dict(headers or {}),
                              "body": body, "timeout": timeout, "mode": mode, "referer": referer})
        return self._answer(url)

    def close(self):
        self.closed = True


def make(mod, script, **kw):
    clock = Clock()
    kw.setdefault("delay_min", 0.0)
    kw.setdefault("delay_max", 0.0)
    session = StubSession(script)
    fetcher = mod.Fetcher(session=session, sleep=clock.sleep, clock=clock,
                          rand=Rand(), **kw)
    return fetcher, session, clock


def seed(mod, directory, url, body, status=200, bytes_field=None):
    """Write a cache entry the way the documented layout says: <sha256(url)>.body + .json."""
    os.makedirs(directory, exist_ok=True)
    stem = os.path.join(directory, mod.cache_key(url))
    with open(stem + ".body", "wb") as handle:
        handle.write(body)
    meta = {"url": url, "status": status, "final_url": url + "/final",
            "content_type": "text/html", "bytes": len(body) if bytes_field is None else bytes_field,
            "fetched_at": "2020-01-02T03:04:05Z", "transport": "chrome",
            "from_cache": False, "attempts": 1}
    with open(stem + ".json", "w", encoding="utf-8") as handle:
        json.dump(meta, handle)


def capture(fn, *args, **kw):
    """Call fn with stdout (a binary-capable wrapper) and stderr swapped; return (result, stdout bytes, stderr text)."""
    out_raw, err = io.BytesIO(), io.StringIO()
    out = io.TextIOWrapper(out_raw, encoding="utf-8", write_through=True)
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        try:
            result = fn(*args, **kw)
        except SystemExit as exc:
            result = exc.code
        out.flush()
        return result, out_raw.getvalue(), err.getvalue()
    finally:
        sys.stdout, sys.stderr = old


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def group_a(suite, mod_loader):
    old_argv = list(sys.argv)
    mod, out, err = capture(mod_loader)
    problems = []
    if out or err:
        problems.append("import wrote output: stdout=%r stderr=%r" % (out[:60], err[:60]))
    if sys.argv != old_argv:
        problems.append("import changed sys.argv")
    if getattr(mod, "_SHARED", None) != {}:
        problems.append("import opened a shared Fetcher: %r" % (getattr(mod, "_SHARED", None),))
    suite.record("A", "import-has-no-side-effects", problems)

    rc, out, err = run_cli(["--help"])
    problems = []
    if rc != 0:
        problems.append("--help exited %d" % rc)
    if b"usage" not in out.lower():
        problems.append("no usage text on stdout: %r" % out[:80])
    if "--cache-dir" not in out.decode("utf-8", "replace"):
        problems.append("--cache-dir missing from the help")
    suite.record("A", "help-exits-0", problems)

    for cid, args in (("no-args", []),
                      ("unknown-option", ["--no-such-option", "http://example.com/"]),
                      ("delay-max-below-min", ["--delay-min", "5", "--delay-max", "1", "http://example.com/"]),
                      ("non-numeric-retries", ["--retries", "x", "http://example.com/"]),
                      ("not-an-http-url", ["ftp://example.com/"])):
        rc, out, err = run_cli(args)
        problems = []
        if rc != 2:
            problems.append("exit %d, wanted 2 (stderr %r)" % (rc, err[:120]))
        if out:
            problems.append("a usage error wrote to stdout")
        suite.record("A", "usage-error-exit-2-" + cid, problems)


def group_b(suite, ws):
    meta = os.path.join(ws, "refused.json")
    started = time.monotonic()
    rc, out, err = run_cli(["http://127.0.0.1:1/", "--meta", meta])
    took = time.monotonic() - started
    lines = err.splitlines()
    problems = []
    if rc != 1:
        problems.append("exit %d, wanted 1" % rc)
    if out:
        problems.append("stdout not empty: %r" % out[:60])
    if len(lines) != 1 or not lines[0].startswith("fetch.py:"):
        problems.append("stderr is not exactly one `fetch.py:` line: %r" % err[:300])
    elif "refused" not in lines[0]:
        problems.append("the line does not name the refusal: %r" % lines[0])
    suite.record("B", "refused-url-one-stderr-line-exit-1", problems,
                 detail=["stderr: %s" % err.strip()[:160]])

    problems = []
    if took > 20:
        problems.append("took %.1f s: a policy refusal was retried with backoff" % took)
    if os.path.exists(meta):
        problems.append("a meta file exists although no response did")
    suite.record("B", "refusal-is-not-retried-and-has-no-meta", problems)

    # Port 1 is refused by the client's URL rules before any address is
    # resolved; a loopback address on an ordinary port reaches the connect
    # policy, which is the refusal that guards the unverified Chrome path.
    started = time.monotonic()
    rc, out, err = run_cli(["http://127.0.0.1:8081/"])
    lines = err.splitlines()
    problems = []
    if rc != 1 or out:
        problems.append("rc=%d stdout=%r" % (rc, out[:40]))
    if len(lines) != 1 or not lines[0].startswith("fetch.py:") or "policy: 127.0.0.1 resolves to 127.0.0.1" not in lines[0]:
        problems.append("stderr %r" % err[:300])
    if time.monotonic() - started > 20:
        problems.append("a policy refusal was retried with backoff")
    suite.record("B", "loopback-policy-refusal-one-line-exit-1", problems)


def group_c(suite, mod, ws):
    cache = os.path.join(ws, "cache")
    seed(mod, cache, SEEDED_URL, BINARY_BODY)
    out_path = os.path.join(ws, "deep", "dir", "out.bin")
    meta_path = os.path.join(ws, "deep", "meta.json")
    common = ["--cache-dir", cache, "--delay-min", "0", "--delay-max", "0"]
    rc, out, err = run_cli([SEEDED_URL, "--out", out_path, "--meta", meta_path] + common)
    problems = []
    if rc != 0:
        problems.append("exit %d, stderr %r" % (rc, err[:200]))
    if out or err:
        problems.append("--out run wrote stdout/stderr: %r %r" % (out[:40], err[:80]))
    try:
        with open(out_path, "rb") as handle:
            if handle.read() != BINARY_BODY:
                problems.append("--out bytes differ from the cached body")
    except OSError as exc:
        problems.append("--out missing: %s" % exc)
    suite.record("C", "cache-hit-writes-out-without-network", problems)

    problems = []
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        want = {"status": 200, "final_url": SEEDED_URL + "/final", "content_type": "text/html",
                "bytes": len(BINARY_BODY), "from_cache": True, "attempts": 0,
                "transport": "chrome", "fetched_at": "2020-01-02T03:04:05Z"}
        for key, value in want.items():
            if meta.get(key) != value:
                problems.append("meta[%s] = %r, wanted %r" % (key, meta.get(key), value))
        missing = [k for k in ("status", "final_url", "content_type", "bytes", "fetched_at",
                               "transport", "from_cache", "attempts") if k not in meta]
        if missing:
            problems.append("meta lacks %s" % missing)
    except (OSError, ValueError) as exc:
        problems.append("meta unreadable: %s" % exc)
    suite.record("C", "meta-file-fields", problems)

    started = time.monotonic()
    rc, out, err = run_cli([SEEDED_URL] + common)
    problems = []
    if rc != 0 or out != BINARY_BODY or err:
        problems.append("rc=%d stdout=%r stderr=%r" % (rc, out[:60], err[:80]))
    if time.monotonic() - started > 15:
        problems.append("a cache hit took over 15 s")
    suite.record("C", "cache-hit-stdout-is-binary-safe", problems)

    for cid, body, status, bytes_field in (
            ("size-mismatch", b"abc", 200, 99),
            ("non-2xx-entry", b"abc", 404, 3)):
        directory = os.path.join(ws, "cache-" + cid)
        seed(mod, directory, SEEDED_URL, body, status=status, bytes_field=bytes_field)
        rc, out, err = run_cli([SEEDED_URL, "--cache-dir", directory, "--delay-min", "0",
                                "--delay-max", "0", "--retries", "0"])
        problems = []
        if rc != 1 or out:
            problems.append("the entry was served: rc=%d stdout=%r" % (rc, out[:40]))
        if not err.startswith("fetch.py:"):
            problems.append("no fetch.py: line on a miss: %r" % err[:100])
        suite.record("C", "ignored-entry-" + cid, problems)

    # Library round trip: tuple shape, and a 2xx answer is written atomically.
    fetcher, session, clock = make(mod, [(200, {"Content-Type": "text/html; charset=utf-8"}, b"<p>x</p>")],
                                   cache_dir=os.path.join(ws, "cache-write"))
    first = fetcher.fetch("http://example.test/a")
    stems = sorted(os.listdir(os.path.join(ws, "cache-write")))
    second = fetcher.fetch("http://example.test/a")
    problems = []
    if first != (200, "http://example.test/a#final", b"<p>x</p>"):
        problems.append("first = %r" % (first,))
    if second != (200, "http://example.test/a#final", b"<p>x</p>"):
        problems.append("second = %r" % (second,))
    if len(session.calls) != 1:
        problems.append("%d network calls, wanted 1" % len(session.calls))
    if len(stems) != 2 or not (stems[0].endswith(".body") and stems[1].endswith(".json")):
        problems.append("cache files %r, wanted one .body and one .json" % stems)
    if clock.sleeps:
        problems.append("a cache hit slept: %r" % clock.sleeps)
    suite.record("C", "miss-stores-then-hit-skips-network", problems)

    fetcher, session, clock = make(mod, [404], cache_dir=os.path.join(ws, "cache-404"))
    fetcher.fetch("http://example.test/missing")
    problems = [] if not os.path.exists(os.path.join(ws, "cache-404")) else \
        ["a 404 was cached: %r" % os.listdir(os.path.join(ws, "cache-404"))]
    suite.record("C", "only-2xx-is-cached", problems)


def group_d(suite, mod):
    schedule = [mod.backoff_seconds(k, 0.0) for k in range(4)]
    suite.record("D", "backoff-schedule-5-10-20-40",
                 [] if schedule == [5.0, 10.0, 20.0, 40.0] else ["got %r" % schedule],
                 detail=["backoff_seconds(0..3, jitter 0) = %r" % schedule])
    problems = []
    for k in range(4):
        low, high = mod.backoff_seconds(k, 0.0), mod.backoff_seconds(k, 1.0)
        mid = mod.backoff_seconds(k, 0.5)
        if not (low < mid < high <= low * 1.25 + 1e-9):
            problems.append("k=%d jitter range %r..%r (mid %r)" % (k, low, high, mid))
    suite.record("D", "backoff-jitter-adds-up-to-a-quarter", problems)
    problems = []
    if mod.backoff_seconds(50, 1.0) != mod.BACKOFF_CAP_SECONDS:
        problems.append("k=50 gave %r, wanted the cap" % mod.backoff_seconds(50, 1.0))
    if mod.backoff_seconds(0, 9.0) != mod.backoff_seconds(0, 1.0) or mod.backoff_seconds(0, -3) != 5.0:
        problems.append("jitter is not clamped to [0, 1]")
    suite.record("D", "backoff-is-capped-and-jitter-clamped", problems)

    rows = (("5", 5), (" 30 ", 30), ("0", 0), ("120", 120), ("121", 120), ("99999", 120),
            ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("-1", None), ("1.5", None),
            ("", None), (None, None), ("٣", None), ("5 6", None), ("1" * 30, None))
    problems = []
    for value, want in rows:
        got = mod.retry_after_seconds(value)
        if got != want:
            problems.append("retry_after_seconds(%r) = %r, wanted %r" % (value, got, want))
    suite.record("D", "retry-after-seconds-form-only-capped-at-120", problems)

    dirty = "bad\x1b[31m\nline\r\x00 ‮​end"
    cleaned = mod.clean_text(dirty)
    problems = []
    if any(ord(ch) < 32 or ch in "\x7f‮​" for ch in cleaned):
        problems.append("control or format characters survived: %r" % cleaned)
    if "\n" in cleaned or "\r" in cleaned:
        problems.append("a newline survived: %r" % cleaned)
    if len(mod.clean_text("x" * 5000, 100)) != 100:
        problems.append("the length bound is not applied")
    suite.record("D", "clean-text-one-printable-line", problems,
                 detail=["clean_text -> %r" % cleaned])


def group_e(suite, mod):
    # Pacing: nothing before the first request, a random gap before each later one.
    fetcher, session, clock = make(mod, [200, 200], delay_min=3.0, delay_max=3.0)
    fetcher.fetch("http://example.test/1")
    first_sleeps = list(clock.sleeps)
    fetcher.fetch("http://example.test/2")
    problems = []
    if first_sleeps:
        problems.append("slept before the first request: %r" % first_sleeps)
    if clock.sleeps != [3.0]:
        problems.append("between-request sleeps %r, wanted [3.0]" % clock.sleeps)
    suite.record("E", "no-delay-before-first-request-then-gap", problems)

    # 503 -> 200: one backoff of 5 s (jitter 0), attempts 2, the Accept-Language header only.
    fetcher, session, clock = make(mod, [503, 200], retries=3)
    result = fetcher.fetch_full("http://example.test/a")
    problems = []
    if result["status"] != 200 or result["attempts"] != 2:
        problems.append("status/attempts = %r/%r" % (result["status"], result["attempts"]))
    if clock.sleeps != [5.0]:
        problems.append("sleeps %r, wanted [5.0]" % clock.sleeps)
    sent = set(k.lower() for _u, h, _t in session.calls for k in h)
    if sent - {"accept-language"}:
        problems.append("headers beyond Accept-Language were passed: %r" % sorted(sent))
    suite.record("E", "retry-503-then-success-with-backoff", problems)

    # Retry-After wins over a smaller backoff, is capped at 120, and a date is ignored.
    for cid, header, want in (("retry-after-floor", "30", 30.0), ("retry-after-cap", "9999", 120.0),
                              ("retry-after-date-ignored", "Wed, 21 Oct 2026 07:28:00 GMT", 5.0)):
        fetcher, session, clock = make(mod, [(429, {"Retry-After": header}, b""), 200])
        fetcher.fetch("http://example.test/a")
        suite.record("E", cid, [] if clock.sleeps == [want] else
                     ["sleeps %r, wanted [%r]" % (clock.sleeps, want)])

    # Exhaustion: the last HTTP status comes back, with the doubling schedule.
    fetcher, session, clock = make(mod, [403, 403, 403], retries=2)
    status, final_url, body = fetcher.fetch("http://example.test/b")
    problems = []
    if status != 403 or len(session.calls) != 3:
        problems.append("status %r after %d calls, wanted 403 after 3" % (status, len(session.calls)))
    if clock.sleeps != [5.0, 10.0]:
        problems.append("sleeps %r, wanted [5.0, 10.0]" % clock.sleeps)
    suite.record("E", "exhausted-retries-return-last-status", problems)

    fetcher, session, clock = make(mod, [404, 200])
    status = fetcher.fetch("http://example.test/c")[0]
    suite.record("E", "other-4xx-is-not-retried",
                 [] if status == 404 and len(session.calls) == 1 and not clock.sleeps else
                 ["status %r, %d calls, sleeps %r" % (status, len(session.calls), clock.sleeps)])

    # Transport errors: retried, the session rebuilt only after two in a row.
    boom = lambda: mod.ChromeClientError("tls: connection reset")  # noqa: E731
    fetcher, session, clock = make(mod, [boom(), 200], retries=3)
    fetcher.fetch("http://example.test/d")
    problems = []
    if len(session.calls) != 2 or session.closed:
        problems.append("one failure rebuilt the session (calls %d, closed %r)" % (len(session.calls), session.closed))
    suite.record("E", "one-transport-error-keeps-the-session", problems)

    fetcher, first, clock = make(mod, [boom(), OSError("reset"), 200], retries=3)
    second = StubSession([200])
    fetcher._new_session = lambda: second
    fetcher.fetch("http://example.test/e")
    problems = []
    if len(first.calls) != 2 or len(second.calls) != 1:
        problems.append("calls first/second = %d/%d, wanted 2/1" % (len(first.calls), len(second.calls)))
    if clock.sleeps != [5.0, 10.0]:
        problems.append("sleeps %r, wanted [5.0, 10.0]" % clock.sleeps)
    suite.record("E", "two-consecutive-transport-errors-rebuild-the-session", problems)

    fetcher, session, clock = make(mod, [boom(), 503, boom(), 200], retries=4)
    third = StubSession([200])
    fetcher._new_session = lambda: third
    fetcher.fetch("http://example.test/f")
    suite.record("E", "an-answer-resets-the-failure-count",
                 [] if not third.calls and len(session.calls) == 4 else
                 ["rebuilt although the failures were not consecutive"])

    fetcher, session, clock = make(mod, [boom(), boom()], retries=1)
    try:
        fetcher.fetch("http://example.test/g")
        problems = ["no FetchError after the retries"]
    except mod.FetchError as exc:
        problems = [] if "tls: connection reset" in str(exc) and "2 attempts" in str(exc) else \
            ["FetchError text %r" % str(exc)]
    suite.record("E", "transport-exhaustion-raises-fetch-error", problems)

    fetcher, session, clock = make(mod, [mod.ChromeClientError("policy: 127.0.0.1 resolves to 127.0.0.1 (loopback)"), 200])
    try:
        fetcher.fetch("http://example.test/h")
        problems = ["no FetchError for a policy refusal"]
    except mod.FetchError:
        problems = [] if len(session.calls) == 1 and not clock.sleeps else \
            ["retried a refusal: %d calls, sleeps %r" % (len(session.calls), clock.sleeps)]
    suite.record("E", "policy-refusal-is-never-retried", problems)

    fetcher, session, clock = make(mod, [mod.ChromeBodyTooLarge(100), 200])
    try:
        fetcher.fetch("http://example.test/i")
        problems = ["no FetchError for an over-size body"]
    except mod.FetchError:
        problems = [] if len(session.calls) == 1 else ["retried an over-size body"]
    suite.record("E", "oversize-body-is-never-retried", problems)

    problems = []
    for bad in ("ftp://x/", "", None, 7):
        try:
            make(mod, [200])[0].fetch(bad)
            problems.append("accepted %r" % (bad,))
        except mod.FetchError:
            pass
    for kwargs in ({"retries": -1}, {"delay_min": -1}, {"delay_min": 5, "delay_max": 1},
                   {"timeout": 0}, {"max_bytes": 0}, {"transport": "curl"}):
        try:
            mod.Fetcher(**kwargs)
            problems.append("Fetcher accepted %r" % (kwargs,))
        except ValueError:
            pass
    suite.record("E", "bad-input-is-refused", problems)


def group_f(suite, mod, ws):
    original = mod._ch_session_new
    try:
        # An HTTP error with a response: exit 1, one line, meta still written.
        script = [(404, {"Content-Type": "text/html"}, b"<h1>gone</h1>")]
        mod._ch_session_new = lambda **kw: StubSession(script)
        meta_path = os.path.join(ws, "err", "meta.json")
        rc, out, err = capture(mod.main, ["http://example.test/gone", "--meta", meta_path,
                                          "--delay-min", "0", "--delay-max", "0", "--retries", "0"])
        problems = []
        if rc != 1:
            problems.append("exit %r, wanted 1" % (rc,))
        if out:
            problems.append("a 404 body reached stdout: %r" % out[:40])
        lines = err.splitlines()
        if len(lines) != 1 or not lines[0].startswith("fetch.py: HTTP 404"):
            problems.append("stderr %r" % err[:200])
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            if meta.get("status") != 404 or meta.get("from_cache") is not False or meta.get("attempts") != 1:
                problems.append("meta %r" % (meta,))
            if meta.get("content_type") != "text/html" or meta.get("bytes") != 13:
                problems.append("meta type/bytes %r" % (meta,))
            if not str(meta.get("fetched_at", "")).endswith("Z"):
                problems.append("fetched_at is not UTC ISO 8601: %r" % meta.get("fetched_at"))
        except (OSError, ValueError) as exc:
            problems.append("meta not written on an HTTP-error exit: %s" % exc)
        suite.record("F", "http-error-exit-1-with-meta", problems)

        # A 2xx: body bytes on stdout, exit 0, the sanitized one-line contract for hostile reasons.
        mod._ch_session_new = lambda **kw: StubSession([(200, {}, BINARY_BODY)])
        rc, out, err = capture(mod.main, ["http://example.test/ok", "--delay-min", "0", "--delay-max", "0"])
        problems = []
        if rc != 0 or out != BINARY_BODY or err:
            problems.append("rc=%r stdout=%r stderr=%r" % (rc, out[:40], err[:80]))
        suite.record("F", "success-writes-body-bytes-to-stdout", problems)

        # Transport failure with a hostile error text: still one clean line.
        hostile = mod.ChromeClientError("tls: boom\x1b[2J\nfetch.py: forged line")
        mod._ch_session_new = lambda **kw: StubSession([hostile])
        rc, out, err = capture(mod.main, ["http://example.test/x", "--delay-min", "0",
                                          "--delay-max", "0", "--retries", "0"])
        problems = []
        if rc != 1:
            problems.append("exit %r, wanted 1" % (rc,))
        if len(err.splitlines()) != 1 or "\x1b" in err or not err.startswith("fetch.py:"):
            problems.append("stderr is not one clean line: %r" % err)
        suite.record("F", "hostile-error-text-is-one-clean-line", problems)

        # --out and --verified selection.
        seen = {}

        def factory(**kw):
            seen.update(kw)
            return StubSession([(200, {}, b"abc")])
        mod._ch_session_new = factory
        out_path = os.path.join(ws, "o", "p", "q.html")
        rc, out, err = capture(mod.main, ["http://example.test/v", "--out", out_path, "--verified",
                                          "--timeout", "7", "--max-bytes", "1000",
                                          "--delay-min", "0", "--delay-max", "0"])
        problems = []
        if rc != 0 or out:
            problems.append("rc=%r stdout=%r" % (rc, out[:40]))
        if seen.get("transport") != "verified":
            problems.append("--verified built transport=%r" % seen.get("transport"))
        if seen.get("max_bytes") != 1000 or seen.get("timeout") != 7:
            problems.append("session kwargs %r" % (seen,))
        if seen.get("connect_policy") is not mod._ch_public_only_policy:
            problems.append("the connect policy is not _ch_public_only_policy")
        for forbidden in ("tls12_fallback", "allow_downgrade", "rand", "log"):
            if forbidden in seen:
                problems.append("session kwarg %s passed" % forbidden)
        try:
            with open(out_path, "rb") as handle:
                if handle.read() != b"abc":
                    problems.append("--out content differs")
        except OSError as exc:
            problems.append("--out not written: %s" % exc)
        leftovers = [n for n in os.listdir(os.path.dirname(out_path)) if n.startswith(".fetch-")]
        if leftovers:
            problems.append("temp files left behind: %r" % leftovers)
        suite.record("F", "out-verified-and-session-kwargs", problems)

        seen.clear()
        capture(mod.main, ["http://example.test/default", "--delay-min", "0", "--delay-max", "0"])
        suite.record("F", "default-transport-is-chrome",
                     [] if seen.get("transport") == "chrome" and seen.get("max_bytes") == 16 * 1024 * 1024
                     else ["session kwargs %r" % (seen,)])
    finally:
        mod._ch_session_new = original


FORM = {"Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"}
POST_URL = "http://example.test/post"


def group_h(suite, mod, ws):
    # Validation: nothing reaches the session on bad input.
    problems = []
    for cid, kwargs, exc in (("method PUT", {"method": "PUT"}, ValueError),
                             ("method lowercase", {"method": "post"}, ValueError),
                             ("method None", {"method": None}, ValueError),
                             ("str data", {"method": "POST", "data": "a=1"}, TypeError),
                             ("bytearray data", {"method": "POST", "data": bytearray(b"a")}, TypeError),
                             ("data with GET", {"data": b"a=1"}, ValueError),
                             ("headers list", {"headers": [("A", "b")]}, TypeError)):
        fetcher, session, clock = make(mod, [200])
        try:
            fetcher.fetch(POST_URL, **kwargs)
            problems.append("%s accepted" % cid)
        except exc:
            pass
        if session.calls or session.requests:
            problems.append("%s reached the session" % cid)
    suite.record("H", "method-data-headers-validation", problems)

    rows = (("X-A: b", ("X-A", "b")), ("  X-A :  b c  ", ("X-A", "b c")), ("X-A:b:c", ("X-A", "b:c")),
            ("X-A:", ("X-A", "")))
    problems = []
    for text, want in rows:
        if mod.parse_header(text) != want:
            problems.append("parse_header(%r) = %r, wanted %r" % (text, mod.parse_header(text), want))
    for bad in ("nocolon", ": value", "   : v", ""):
        try:
            mod.parse_header(bad)
            problems.append("parse_header accepted %r" % bad)
        except ValueError:
            pass
    suite.record("H", "parse-header-helper", problems)

    # A pre-seeded entry for the very URL must not be served to a POST, and a POST writes none.
    directory = os.path.join(ws, "cache-post")
    seed(mod, directory, POST_URL, b"SEEDED")
    before = sorted(os.listdir(directory))
    fetcher, session, clock = make(mod, [(200, {}, b"live")], cache_dir=directory)
    result = fetcher.fetch_full(POST_URL, method="POST", data=b"a=1")
    other = os.path.join(ws, "cache-post-fresh")
    fetcher2, session2, _c = make(mod, [(200, {}, b"live")])
    fetcher2.fetch_full("http://example.test/p2", method="POST", data=b"a=1", cache_dir=other)
    problems = []
    if result["body"] != b"live" or result["from_cache"] or len(session.requests) != 1:
        problems.append("the seeded entry was served: %r / %d requests" % (result["body"], len(session.requests)))
    if sorted(os.listdir(directory)) != before:
        problems.append("a POST touched the cache directory")
    if os.path.exists(other):
        problems.append("a per-call cache_dir created a cache for a POST")
    suite.record("H", "post-never-reads-or-writes-the-cache", problems)

    # The request the session sees.
    fetcher, session, clock = make(mod, [(201, {"Content-Type": "text/html"}, b"<p>ok</p>")])
    body = b"a=1&b=\xff"
    result = fetcher.fetch_full(POST_URL, method="POST", data=body, headers=dict(FORM))
    problems = []
    if len(session.requests) != 1 or session.calls:
        problems.append("%d request(), %d get() calls" % (len(session.requests), len(session.calls)))
    else:
        req = session.requests[0]
        want_headers = dict(FORM, **{"Accept-Language": mod.ACCEPT_LANGUAGE})
        if req["method"] != "POST" or req["url"] != POST_URL or req["body"] != body:
            problems.append("method/url/body %r %r %r" % (req["method"], req["url"], req["body"]))
        if req["headers"] != want_headers:
            problems.append("headers %r, wanted %r" % (req["headers"], want_headers))
        if req["mode"] != "cors" or req["referer"] is not None:
            problems.append("mode/referer %r/%r" % (req["mode"], req["referer"]))
    if result["method"] != "POST" or result["status"] != 201 or result["attempts"] != 1:
        problems.append("result %r" % dict((k, v) for k, v in result.items() if k != "body"))
    suite.record("H", "post-reaches-the-session-with-body-headers-cors", problems)

    # No data: an empty POST body; a caller Referer is lifted into referer=, Accept-Language overridable.
    fetcher, session, clock = make(mod, [200])
    fetcher.fetch(POST_URL, method="POST",
                  headers={"referer": "http://example.test/page", "accept-language": "en", "Accept": "*/*"})
    problems = []
    req = session.requests[0] if session.requests else {}
    if req.get("body") != b"":
        problems.append("body %r, wanted b''" % (req.get("body"),))
    if req.get("referer") != "http://example.test/page":
        problems.append("referer %r" % (req.get("referer"),))
    if req.get("headers") != {"Accept-Language": "en", "Accept": "*/*"}:
        problems.append("headers %r" % (req.get("headers"),))
    suite.record("H", "post-referer-lifted-accept-language-overridden", problems)

    # Pacing counts a POST, the one session (cookie jar) serves GET and POST, backoff repeats the body.
    fetcher, session, clock = make(mod, [200, 503, 200, 200], delay_min=3.0, delay_max=3.0, retries=3)
    fetcher.fetch("http://example.test/1")
    fetcher.fetch(POST_URL, method="POST", data=b"x=1")
    fetcher.fetch("http://example.test/3")
    problems = []
    if clock.sleeps != [3.0, 3.0, 5.0, 3.0] and clock.sleeps != [3.0, 5.0, 3.0]:
        problems.append("sleeps %r" % clock.sleeps)
    if len(session.requests) != 2 or len(session.calls) != 2 or session.closed:
        problems.append("one session did not serve all: %d request(), %d get(), closed %r"
                        % (len(session.requests), len(session.calls), session.closed))
    elif [r["body"] for r in session.requests] != [b"x=1", b"x=1"]:
        problems.append("the retry did not resend the body: %r" % [r["body"] for r in session.requests])
    suite.record("H", "post-pacing-session-and-backoff-as-for-get", problems,
                 detail=["sleeps %r" % clock.sleeps])

    fetcher, session, clock = make(mod, [mod.ChromeClientError("tls: connection reset"), 200], retries=3)
    result = fetcher.fetch_full(POST_URL, method="POST", data=b"x=1")
    problems = []
    if result["attempts"] != 2 or len(session.requests) != 2 or clock.sleeps != [5.0]:
        problems.append("attempts %r, %d requests, sleeps %r" % (result["attempts"], len(session.requests), clock.sleeps))
    suite.record("H", "post-transport-error-is-retried", problems)

    # A header refusal by the client is a FetchError with its message and no retry.
    fetcher, session, clock = make(mod, [mod.ChromeClientError("headers: user-agent is fixed by the Chrome profile"), 200])
    try:
        fetcher.fetch(POST_URL, method="POST", data=b"x", headers={"User-Agent": "x"})
        problems = ["no FetchError"]
    except mod.FetchError as exc:
        problems = [] if "headers: user-agent is fixed by the Chrome profile" in str(exc) and \
            len(session.requests) == 1 and not clock.sleeps else \
            ["text %r, %d requests, sleeps %r" % (str(exc), len(session.requests), clock.sleeps)]
    suite.record("H", "post-client-header-refusal-is-not-retried", problems)

    # A hostile Referer value is refused before any request, with the client's message.
    fetcher, session, clock = make(mod, [200])
    try:
        fetcher.fetch(POST_URL, method="POST", data=b"x", headers={"Referer": "http://a/\r\nX: y"})
        problems = ["no FetchError"]
    except mod.FetchError as exc:
        problems = [] if "headers:" in str(exc) and "\n" not in str(exc) and not session.requests else \
            ["text %r, %d requests" % (str(exc), len(session.requests))]
    suite.record("H", "hostile-referer-refused-before-the-session", problems)

    fetcher, session, clock = make(mod, [200])
    result = fetcher.fetch_full("http://example.test/g")
    problems = []
    if result["method"] != "GET" or session.requests:
        problems.append("method %r, %d request() calls" % (result["method"], len(session.requests)))
    if session.calls[0][1] != {"Accept-Language": mod.ACCEPT_LANGUAGE}:
        problems.append("GET headers changed: %r" % (session.calls[0][1],))
    suite.record("H", "get-is-unchanged-and-meta-says-get", problems)

    session = StubSession([(200, {}, b"lib")])
    got = mod.fetch(POST_URL, method="POST", data=b"x=1", headers=dict(FORM), session=session,
                    delay_min=0, delay_max=0)
    suite.record("H", "library-fetch-keeps-the-tuple-shape",
                 [] if got == (200, POST_URL + "#final", b"lib") and session.requests[0]["method"] == "POST"
                 else ["got %r" % (got,)])

    # CLI usage errors: exit 2 before any network.
    data_file = os.path.join(ws, "post.bin")
    with open(data_file, "wb") as handle:
        handle.write(b"a=1")
    for cid, args, want in (
            ("data-file-without-post", ["--data-file", data_file, "http://example.test/"], None),
            ("malformed-header", ["--header", "nocolon", "http://example.test/"], None),
            ("cache-dir-with-post", ["--method", "POST", "--cache-dir", os.path.join(ws, "c"),
                                     "http://example.test/"], None),
            ("unreadable-data-file", ["--method", "POST", "--data-file", os.path.join(ws, "nope.bin"),
                                      "http://example.test/"], "cannot read --data-file")):
        rc, out, err = run_cli(args)
        problems = []
        if rc != 2:
            problems.append("exit %d, wanted 2 (stderr %r)" % (rc, err[:120]))
        if out:
            problems.append("a usage error wrote to stdout")
        if want and (len(err.splitlines()) != 1 or not err.startswith("fetch.py: ") or want not in err):
            problems.append("stderr %r" % err[:200])
        suite.record("H", "cli-exit-2-" + cid, problems)

    # main() in-process: --method POST, --data-file, repeated --header, meta carries the method.
    original = mod._ch_session_new
    try:
        stub = StubSession([(200, {"Content-Type": "application/json"}, b"{}")])
        mod._ch_session_new = lambda **kw: stub
        meta_path = os.path.join(ws, "post-meta.json")
        rc, out, err = capture(mod.main, [POST_URL, "--method", "POST", "--data-file", data_file,
                                          "--header", "Content-Type: application/x-www-form-urlencoded",
                                          "--header", "x-requested-with:XMLHttpRequest",
                                          "--meta", meta_path, "--delay-min", "0", "--delay-max", "0"])
        problems = []
        if rc != 0 or out != b"{}" or err:
            problems.append("rc=%r stdout=%r stderr=%r" % (rc, out[:40], err[:80]))
        req = stub.requests[0] if stub.requests else {}
        if req.get("method") != "POST" or req.get("body") != b"a=1" or req.get("mode") != "cors":
            problems.append("request %r" % (req,))
        if req.get("headers", {}).get("Content-Type") != "application/x-www-form-urlencoded" or \
                req.get("headers", {}).get("x-requested-with") != "XMLHttpRequest":
            problems.append("headers %r" % (req.get("headers"),))
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            if meta.get("method") != "POST":
                problems.append("meta method %r" % (meta.get("method"),))
        except (OSError, ValueError) as exc:
            problems.append("meta unreadable: %s" % exc)
        suite.record("H", "main-post-data-file-headers-and-meta", problems)
    finally:
        mod._ch_session_new = original


def group_g(suite, before, pyc_before):
    new = sorted(H.repo_tree() - before)
    suite.record("G", "no-new-repo-paths",
                 [] if not new else ["this suite wrote into the repo tree: %s" % new[:12]])
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after if pyc_before.get(p) != pyc_after[p])
    suite.record("G", "no-new-bytecode",
                 [] if not changed else ["this run wrote or touched bytecode: %s" % changed[:6]])


def run(opts=None):
    """Load fetch.py, drive the CLI and the fetch loop offline, return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="fetch.py: CLI contract, cache, pacing and retry loop "
                          "(offline)",
                    opts=opts, mode="stream", group_width=3, cid_width=52)
    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    suite.note("      script  : %s" % SCRIPT)

    def loader():
        return H.load_module_from_path("fetch_under_test", SCRIPT)

    with H.TempWorkspace("ph-fetch-", keep=opts.keep) as ws:
        suite.note("      fixture : %s" % ws.path)
        group_a(suite, loader)
        mod = loader()
        group_b(suite, ws.path)
        group_c(suite, mod, ws.path)
        group_d(suite, mod)
        group_e(suite, mod)
        group_f(suite, mod, ws.path)
        group_h(suite, mod, ws.path)
    group_g(suite, before, pyc_before)
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
