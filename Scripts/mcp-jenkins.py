#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""MCP-Jenkins: Pure Python Jenkins MCP server.

Single-tool dispatcher pattern: exposes one MCP tool (jenkins_call) that
routes to internal handler functions via the 'function' parameter. Port of
the TypeScript jenkins-mcp-server, with markdown replies (see below).

Requires only Python 3.9+ stdlib modules.

Output shape (cap convention v1)
--------------------------------
Replies are MARKDOWN, not JSON: a `## heading`, `**label**: value` lines,
whitespace-aligned tables inside a fence for row data, and a fence for verbatim
blobs (console logs, artifacts). Handlers still build a payload DICT — that is
the data layer the recipe functions branch on — and a per-handler renderer turns
it into markdown at the dispatcher boundary. Nothing about the data changed;
only its carrier did. `json.dumps(payload, indent=2)` cost a newline plus two
spaces of indent per field, and quotes and braces around every one of them.

Answers are capped by `max_answer_chars` (default 24000 chars, ~6k tokens),
overridable per call:
  * row-shaped payloads (log lines, job lists, test cases) drop whole ROWS and
    say where to resume: `[showing rows 1-1000 of 5231; offset=1000 for more]`
  * everything else is cut on a LINE BOUNDARY with exactly one closing line:
    `[truncated: kept <n> of <total> chars from the <head|tail>; raise
    max_answer_chars or narrow the query]`
  * head-biased by default; tail-biased where the recap sits at the bottom
    (run_and_wait / inspect_build), and the closing line says which end it kept

The full model-facing reference — every function, alias, parameter, the job-path
rule, pagination and the recipes — lives in the mcp-jenkins SKILL, not in the
tool description: the description is sent on every single request, the skill is
loaded only when a Jenkins-shaped task actually comes up.

Environment variables
---------------------
All variables can be overridden by the matching CLI flag. The lowercase
variants (`jenkins_endpoint`, ...) are also accepted as fallbacks.

  JENKINS_ENDPOINT  (required, --endpoint)
      Jenkins base URL, e.g. ``https://jenkins.example.com``. Do NOT include
      a trailing ``/job`` segment — the server appends ``/job/<name>`` itself
      when navigating into folders. A trailing slash is tolerated and stripped.

  JENKINS_USERNAME  (required, --username)
      Jenkins user name used for HTTP basic auth.

  JENKINS_TOKEN     (required, --token)
      Jenkins API token (not the UI password) used for HTTP basic auth.

  JENKINS_PROJECT   (optional, --project)
      Default project / folder prefix. When set, every ``job_path`` passed by
      the model is resolved relative to this project, so the model does not
      have to walk parent folders first. Accepted forms (all normalized to
      ``sl/foo``):
          ``sl/foo``           ``/sl/foo/``           ``job/sl/job/foo``
      Resolution rules:
          missing / empty job_path  -> project itself
          job_path starts with '/'  -> absolute path (escape hatch)
          starts with project/      -> idempotent, untouched
          otherwise                 -> prefixed with ``<project>/``
      Call ``jenkins_call`` with no function to see the effective project in
      the status output.

Server start-up refuses to launch if any of the required variables (endpoint /
username / token) is missing. JENKINS_PROJECT is optional — when unset the
server behaves exactly as before.
"""

import argparse
import asyncio
import base64
import datetime
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mcp-jenkins")


# ---------------------------------------------------------------------------
# Configuration (filled in by main())
# ---------------------------------------------------------------------------

class JenkinsConfig:
    endpoint: Optional[str] = None
    username: Optional[str] = None
    token: Optional[str] = None
    project: Optional[str] = None
    timeout: int = 30
    log_file: Optional[str] = None
    debug: bool = False
    # Records which env var (or CLI flag) supplied each value, for diagnostics.
    sources: Dict[str, str] = {}

    @classmethod
    def is_ready(cls) -> bool:
        return bool(cls.endpoint and cls.username and cls.token)

    @classmethod
    def auth_header(cls) -> str:
        raw = f"{cls.username}:{cls.token}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    @classmethod
    def masked_token(cls) -> Optional[str]:
        if not cls.token:
            return None
        token = cls.token
        if len(token) >= 8:
            return f"{token[:4]}...{token[-2:]} (len={len(token)})"
        return f"*** (len={len(token)})"


# ---------------------------------------------------------------------------
# Parameter aliases — models often use short / alternative names
# ---------------------------------------------------------------------------

PARAM_ALIASES = {
    # job path
    "job": "job_path",
    "jobPath": "job_path",
    "path": "job_path",
    "folder": "job_path",
    "folderPath": "job_path",
    "folder_path": "job_path",
    "project": "job_path",
    "project_path": "job_path",
    # build number
    "build": "build_number",
    "buildNumber": "build_number",
    "num": "build_number",
    "number": "build_number",
    # parameters
    "params": "parameters",
    "build_parameters": "parameters",
    # artifact
    "artifact": "artifact_path",
    "file": "artifact_path",
    "artifactPath": "artifact_path",
    # queue
    "url": "queue_url",
    "queueUrl": "queue_url",
    # generic
    "limit": "recent_builds_limit",
    "recentBuildsLimit": "recent_builds_limit",
    "delay": "delay_sec",
    "delaySec": "delay_sec",
    "timeout": "timeout_sec",
    "timeoutSec": "timeout_sec",
    # log pagination
    "start": "start_line",
    "startLine": "start_line",
    "max": "max_lines",
    "maxLines": "max_lines",
    # row pagination (list_jobs rows, test-report cases)
    "skip": "offset",
    "row_offset": "offset",
    "rowOffset": "offset",
    "rows": "max_rows",
    "maxRows": "max_rows",
    # answer ceiling — the reply budget, NOT a page size. `max`/`maxLines` above
    # keep pointing at max_lines: that is the log PAGE, a different question.
    "max_chars": "max_answer_chars",
    "maxChars": "max_answer_chars",
    "maxAnswerChars": "max_answer_chars",
    # stage selection
    "stageId": "stage_id",
    "stageName": "stage_name",
    # return type
    "returnType": "return_type",
    "type": "return_type",
    # list_jobs
    "name_filter": "filter",
    "nameFilter": "filter",
    "maxDepth": "max_depth",
    # test_report
    "onlyFailed": "only_failed",
    "maxCases": "max_cases",
    "includeStack": "include_stack",
    # replay
    "mainScript": "main_script",
    "script": "main_script",
    # run_and_wait
    "pollInterval": "poll_interval_sec",
    "pollIntervalSec": "poll_interval_sec",
    "logTail": "log_tail",
    "tail": "log_tail",
}


def _resolve_aliases(params: Any) -> dict:
    """Return a new dict with aliased parameter names resolved to canonical.

    Accepts a dict or a JSON-encoded object string (the wire sometimes sends
    params as a serialised string).
    """
    if params is None:
        return {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as exc:
            raise ValueError(f"'params' was a string but not valid JSON: {exc}.")
    if not isinstance(params, dict):
        raise ValueError("'params' must be an object or JSON-encoded object string.")
    resolved: dict = {}
    for key, value in params.items():
        canonical = PARAM_ALIASES.get(key, key)
        if canonical not in resolved:
            resolved[canonical] = value
    return resolved


def _bool_param(value: Any, default: bool = False) -> bool:
    """Coerce a possibly-stringy value to bool.

    The wire frequently carries booleans as strings ("false"/"0"/"no"), where a
    naive bool("false") would wrongly yield True.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


def _normalize_project(raw: Optional[str]) -> Optional[str]:
    """Normalize JENKINS_PROJECT: drop leading/trailing slashes and any embedded
    `job/` segments. So both 'sl/foo', '/sl/foo/', and 'job/sl/job/foo' all
    yield 'sl/foo'.
    """
    if not raw:
        return None
    parts = [p for p in raw.split("/") if p and p != "job"]
    return "/".join(parts) or None


def _apply_project_scope(params: dict) -> dict:
    """When JENKINS_PROJECT is configured, treat user-provided `job_path` as
    relative to the project so the model doesn't have to traverse parent
    folders. Semantics:
      - missing/empty job_path -> project itself
      - job_path starts with '/' -> absolute, escape hatch (leading '/' stripped)
      - already starts with the project prefix -> idempotent, left as-is
      - otherwise -> prefixed with '<project>/'
    """
    project = JenkinsConfig.project
    if not project:
        return params
    raw = params.get("job_path")
    if raw is None or raw == "":
        new_path = project
    elif not isinstance(raw, str):
        return params
    elif raw.startswith("/"):
        new_path = raw.lstrip("/")
    elif raw == project or raw.startswith(project + "/"):
        return params
    else:
        new_path = f"{project}/{raw}"
    result = dict(params)
    result["job_path"] = new_path
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text)


def _format_duration(duration_ms: Optional[int]) -> str:
    if not duration_ms or duration_ms <= 0:
        return "0s"
    seconds = duration_ms // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_iso(ts_ms: Optional[int]) -> Optional[str]:
    if not ts_ms:
        return None
    return datetime.datetime.utcfromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts_ms % 1000:03d}Z"


def _format_job_path(job_path: str) -> str:
    """Convert "foo/bar/baz" -> "job/foo/job/bar/job/baz". Tolerates an input
    that already contains "job/" segments. Strips leading / trailing slashes.
    """
    if "job/" in job_path:
        return job_path.strip("/")
    return "/".join(f"job/{seg}" for seg in job_path.split("/") if seg)


def _paginate_text(text: str, start_line: Optional[int], max_lines: Optional[int]) -> dict:
    lines = text.split("\n")
    total_lines = len(lines)
    start = start_line if start_line is not None else 0
    limit = max_lines if max_lines is not None else 1000

    if start_line is not None or max_lines is not None:
        sliced = lines[start:start + limit]
    else:
        sliced = lines

    has_more = total_lines > start + limit
    out = {
        "text": "\n".join(sliced),
        "totalLines": total_lines,
        "hasMore": has_more,
    }
    if has_more:
        out["nextStartLine"] = start + limit
    return out


# ---------------------------------------------------------------------------
# HTTP layer (urllib-based, sync — wrapped via run_in_executor)
# ---------------------------------------------------------------------------

class HttpResult:
    __slots__ = ("status", "headers", "body", "raw")

    def __init__(self, status: int, headers: Dict[str, str], body: Any, raw: Optional[bytes] = None):
        self.status = status
        self.headers = headers
        self.body = body
        self.raw = raw


class _AuthPreservingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow GET/HEAD redirects while preserving the Authorization header.

    The stdlib default strips Authorization on cross-host redirects, which on
    Jenkins (proxy / federated auth setups) tends to turn a harmless 302 into
    a stuck "status 302" with no body the caller can inspect.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            auth = req.get_header("Authorization")
            if auth and not new_req.has_header("Authorization"):
                new_req.add_header("Authorization", auth)
        return new_req


_opener = urllib.request.build_opener(_AuthPreservingRedirectHandler())


def _do_request(method: str,
                url: str,
                *,
                params: Optional[Dict[str, str]] = None,
                data: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None,
                response_type: str = "json",
                follow_redirects: bool = True,
                timeout: Optional[int] = None) -> HttpResult:
    """Blocking HTTP request. response_type is 'json', 'text' or 'bytes'.

    `follow_redirects=False` returns the raw 30x response so the caller can
    inspect the Location header. Never raises on non-2xx.
    """
    if params:
        encoded = urllib.parse.urlencode(params, doseq=True)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{encoded}"

    req_headers = {"Authorization": JenkinsConfig.auth_header()}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    log.debug("HTTP %s %s (redirects=%s)", method, url, follow_redirects)

    opener = _opener if follow_redirects else urllib.request.build_opener(
        _NoRedirectHandler()
    )

    try:
        with opener.open(req, timeout=timeout or JenkinsConfig.timeout) as resp:
            return _read_response(resp, response_type)
    except urllib.error.HTTPError as exc:
        return _read_response(exc, response_type)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects — return the 30x response intact."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then yields the original 30x response


def _read_response(resp, response_type: str) -> HttpResult:
    status = getattr(resp, "status", None) or resp.getcode()
    raw = resp.read() if hasattr(resp, "read") else b""
    headers = {k: v for k, v in (resp.headers.items() if resp.headers else [])}

    if response_type == "bytes":
        return HttpResult(status, headers, None, raw)

    text = raw.decode("utf-8", errors="replace") if raw else ""

    if response_type == "text":
        return HttpResult(status, headers, text, raw)

    # response_type == "json"
    if not text:
        return HttpResult(status, headers, {}, raw)
    try:
        return HttpResult(status, headers, json.loads(text), raw)
    except json.JSONDecodeError:
        return HttpResult(status, headers, text, raw)


def _get_crumb() -> Optional[Tuple[str, str]]:
    """Fetch a CSRF crumb. Returns (field_name, value) or None if disabled."""
    url = f"{JenkinsConfig.endpoint}/crumbIssuer/api/json"
    try:
        result = _do_request("GET", url)
    except Exception as exc:
        log.debug("Crumb fetch error: %s", exc)
        return None
    if result.status != 200 or not isinstance(result.body, dict):
        return None
    field = result.body.get("crumbRequestField")
    value = result.body.get("crumb")
    if field and value:
        return field, value
    return None


# ---------------------------------------------------------------------------
# Markdown helpers (fleet idiom — cf. mcp-git.py / mcp-inspect.py)
# ---------------------------------------------------------------------------

def _md_fence(content: str, lang: str = "") -> str:
    """Fence `content`, widening the fence past any backtick run inside it."""
    max_run = 0
    for run in re.findall(r"`+", content):
        max_run = max(max_run, len(run))
    fence = "`" * max(3, max_run + 1)
    return f"{fence}{lang}\n{content}\n{fence}"


def _md_head(title: str, level: int = 2) -> str:
    return f"{'#' * max(1, min(6, level))} {title}"


def _md_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        # Rare: a Jenkins error body or a stage error arrives as a nested
        # object. One compact line keeps the field on a single markdown row.
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _md_fields(payload: dict, keys, labels: Optional[Dict[str, str]] = None) -> List[str]:
    """`**key**: value` lines, for the keys that actually carry a value.

    None / "" / [] are DROPPED rather than printed: the old JSON spent
    `"description": null,` on every absent field, and the reader learns exactly
    the same thing from the line not being there.
    """
    labels = labels or {}
    out: List[str] = []
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or value == "" or value == []:
            continue
        out.append(f"**{labels.get(key, key)}**: {_md_scalar(value)}")
    return out


def _md_table(header: List[str], rows: List[List[str]]) -> str:
    """Whitespace-aligned rows; the last column is left unpadded so a long
    trailing cell (a URL, a description) cannot blow up the table width."""
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def fmt(cells: List[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            out.append(cell if i == len(cells) - 1 else cell.ljust(widths[i]))
        return "  ".join(out).rstrip()

    return "\n".join([fmt(header)] + [fmt(r) for r in rows])


def _md_join(blocks) -> str:
    """Join blocks with one blank line, dropping the empty ones."""
    return "\n\n".join(b for b in blocks if b)


# ---------------------------------------------------------------------------
# Answer ceiling — cap convention v1
# ---------------------------------------------------------------------------

# 24000 chars is ~6k tokens at the usual ~4 chars/token: a reply ONE call may
# spend, not a reply that eats the session. Jenkins is the fleet's worst
# offender by accident — a console log is routinely 10^5 lines and an artifact
# has no bound at all — and until now the only backstop was Claude Code's own
# spill-to-file: it GENERATES the whole payload, writes it to disk, and then
# costs an extra round trip to read back the part the model actually wanted.
# Capping here means the oversized half is never built, and the caller is told
# in one line how to ask for the rest.
DEFAULT_MAX_ANSWER_CHARS = 24000


def _max_answer_chars(params: dict) -> int:
    """The per-call ceiling. <= 0 disables it — an explicit "give me all of it"."""
    try:
        return int(params.get("max_answer_chars", DEFAULT_MAX_ANSWER_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ANSWER_CHARS


def _int_param(value: Any, default: int) -> int:
    """Coerce a wire value to int, falling back instead of raising."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rows_note(start: int, shown: int, total: int) -> str:
    """Row accounting for a row-shaped payload; goes on its LAST line.

    Display indices are 1-based inclusive, which makes the 1-based last row
    equal to the 0-based offset of the next one — so `offset=` is literally the
    value to pass back. Only emitted with `offset=` when rows really remain: a
    resume hint the handler would ignore is worse than no hint, and every
    function that can emit this line accepts `offset` (get_build_log takes it as
    a synonym of start_line, for exactly that reason).
    """
    last = start + shown
    if shown <= 0 and start > 0:
        # Offset past the end. `rows {start+1}-{last}` would print an INVERTED
        # range here (`rows 100-99 of 10`), which reads as corruption; the plain
        # statement is the only honest shape.
        return f"[no rows at offset {start} of {total}]"
    if last < total:
        return (f"[showing rows {start + 1}-{last} of {total}; "
                f"offset={last} for more]")
    if start > 0:
        return f"[showing rows {start + 1}-{last} of {total}; no rows left]"
    return f"[{total} rows]"


def _row_window(rows: list, offset: int, limit: int) -> Tuple[list, str]:
    """(window, accounting line) for a row-shaped payload.

    Row truncation instead of character truncation wherever the payload IS
    rows: cutting a table at char 24000 loses the row boundary and the caller
    cannot resume, while dropping whole rows keeps both.
    """
    total = len(rows)
    start = max(0, offset)
    window = rows[start:start + limit] if limit > 0 else rows[start:]
    return window, _rows_note(start, len(window), total)


_FENCE_LINE_RE = re.compile(r"^(`{3,})", re.M)


def _balance_fences(body: str, keep_tail: bool) -> str:
    """Close (or re-open) a fenced block the cut landed inside.

    A truncated reply that stops mid-fence leaves the accounting line looking
    like log content, and every reader after that has to guess where the block
    ended. An odd number of fence lines means exactly one is missing: at the
    bottom when the head was kept, at the top when the tail was.
    """
    fences = _FENCE_LINE_RE.findall(body)
    if len(fences) % 2 == 0:
        return body
    if keep_tail:
        return "%s\n%s" % (fences[0], body)
    return "%s\n%s" % (body.rstrip("\n"), fences[-1])


def _cap_text(text: str, max_chars: int, bias: str = "head") -> str:
    """Cut `text` to `max_chars` on a LINE BOUNDARY, with ONE closing line.

    Head-biased by default. `bias="tail"` keeps the END, for the two payloads
    whose recap sits at the bottom (run_and_wait / inspect_build put the verdict
    last precisely so that a cut keeps it). The closing line always names the
    end it kept and is always the payload's last line, so the caller never has
    to guess which half survived. A `file:line` anchor is never halved because
    the cut lands on a newline — the single exception is a payload that has no
    newline to cut at (a base64 artifact is one long line), where the boundary
    does not exist and the hard cut is the honest answer.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)
    keep_tail = bias == "tail"

    def marker(kept: int) -> str:
        if keep_tail:
            return (f"\n[truncated: kept {kept} of {total} chars from the tail; "
                    f"raise max_answer_chars or narrow the query]")
        return (f"\n[truncated: kept {kept} of {total} chars from the head; "
                f"raise max_answer_chars or narrow the query]")

    # marker(total) is the longest the line can ever get (kept <= total), so
    # reserving that much cannot overshoot once the real count is known. The
    # repair fence is reserved the same way: it can only ever be one of the
    # fence tokens already present, so the widest one plus its newline bounds it
    # and the whole reply stays inside the ceiling.
    fences = _FENCE_LINE_RE.findall(text)
    keep = max_chars - len(marker(total))
    if fences:
        keep -= max(len(f) for f in fences) + 1
    if keep <= 0:
        # The ceiling is smaller than the accounting line itself. The line still
        # wins: a payload with no accounting is worse than no payload.
        return marker(0).lstrip("\n")
    if keep_tail:
        cut = text.find("\n", total - keep)
        body = text[cut + 1:] if 0 <= cut < total - 1 else text[total - keep:]
    else:
        cut = text.rfind("\n", 0, keep + 1)
        body = text[:cut] if cut > 0 else text[:keep]
    kept = len(body)
    # The accounting reports the payload chars that survived, so the repair
    # fence is added after the count is taken.
    return _balance_fences(body, keep_tail) + marker(kept)


# ---------------------------------------------------------------------------
# Handler results: DATA (payload) + VIEW (renderer)
# ---------------------------------------------------------------------------
#
# A handler returns its payload dict AND the renderer that turns that payload
# into markdown. Keeping the two apart is what lets the recipe handlers
# (run_and_wait / inspect_build) branch on real values and embed their
# sub-calls' rendered sections in one document — the old code re-parsed its own
# `json.dumps` output to get the values back, which is exactly the round trip
# markdown would otherwise have made impossible.

def _ok(payload: dict, render: Optional[Callable[..., str]] = None,
        bias: str = "head") -> dict:
    """A successful handler result. `render` may be omitted for payloads that
    carry an `error` key — those are rendered by `_render_error` regardless."""
    return {"__payload__": payload, "__render__": render, "__bias__": bias}


def _err(message: str, **extra) -> dict:
    payload = {"error": message}
    payload.update(extra)
    return {"__payload__": payload, "__is_error__": True}


def _payload(result: dict) -> dict:
    """The data a handler produced — for the caller that is another handler."""
    return result.get("__payload__") or {}


def _render(result: dict, level: int = 2) -> str:
    """Markdown for one handler result.

    An `error` key wins over the registered renderer: every handler funnels its
    non-2xx paths through the same payload shape, so ONE error view covers all
    of them instead of thirteen near-identical ones.
    """
    payload = _payload(result)
    if "error" in payload:
        return _render_error(payload, level)
    render = result.get("__render__")
    if render is None:
        return _render_generic(payload, level)
    return render(payload, level)


# `hint` and `message` are prose for the reader, not fields: they go last, in
# italics, so the machine-readable part of an error stays scannable.
_ERROR_PROSE_KEYS = ("message", "hint")


def _render_error(payload: dict, level: int = 2) -> str:
    blocks = [_md_head("error: %s" % payload.get("error"), level)]
    keys = [k for k in payload
            if k != "error" and k not in _ERROR_PROSE_KEYS]
    blocks.append("\n".join(_md_fields(payload, keys)))
    prose = [f"_{k}: {payload[k]}_" for k in _ERROR_PROSE_KEYS if payload.get(k)]
    blocks.append("\n".join(prose))
    return _md_join(blocks)


def _render_generic(payload: dict, level: int = 2) -> str:
    """Safety net for a payload with no registered renderer."""
    return "\n".join(_md_fields(payload, list(payload.keys()))) or "_(empty)_"


def _require_config() -> Optional[dict]:
    if not JenkinsConfig.is_ready():
        return _err(
            "Jenkins access not available",
            message="Missing Jenkins environment variables (JENKINS_ENDPOINT / JENKINS_USERNAME / JENKINS_TOKEN)",
        )
    return None


# ---------------------------------------------------------------------------
# Handler: get_job_info
# ---------------------------------------------------------------------------

def handle_get_job_info(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    limit = int(params.get("recent_builds_limit", 5))

    api_path = _format_job_path(job_path)
    tree = ("name,fullName,url,buildable,description,"
            "property[parameterDefinitions[name,type,description,"
            "defaultParameterValue[value],choices]],"
            "builds[number,url,result,timestamp,duration,building]")
    url = f"{JenkinsConfig.endpoint}/{api_path}/api/json?tree={urllib.parse.quote(tree, safe='')}"

    result = _do_request("GET", url)

    if result.status == 404:
        return _ok({
            "error": "Job not found",
            "status": 404,
            "jobPath": job_path,
            "url": url,
        })
    if result.status != 200:
        body = result.body if isinstance(result.body, (dict, list)) else (
            (result.body or "")[:500] if isinstance(result.body, str) else result.body
        )
        return _ok({
            "error": "Failed to fetch job info",
            "status": result.status,
            "body": body,
        })

    data = result.body if isinstance(result.body, dict) else {}

    parameters: List[dict] = []
    for prop in data.get("property") or []:
        for defn in prop.get("parameterDefinitions") or []:
            entry = {
                "name": defn.get("name"),
                "type": defn.get("type"),
            }
            if defn.get("description"):
                entry["description"] = defn["description"]
            dpv = defn.get("defaultParameterValue") or {}
            if "value" in dpv:
                entry["defaultValue"] = dpv["value"]
            if isinstance(defn.get("choices"), list):
                entry["choices"] = defn["choices"]
            parameters.append(entry)

    all_builds = data.get("builds") or []
    builds_out: List[dict] = []
    for b in all_builds[:limit]:
        builds_out.append({
            "number": b.get("number"),
            "url": b.get("url"),
            "result": b.get("result") or ("BUILDING" if b.get("building") else "UNKNOWN"),
            "building": bool(b.get("building")),
            "timestamp": _format_iso(b.get("timestamp")),
            "durationMs": b.get("duration"),
        })

    return _ok({
        "jobPath": job_path,
        "name": data.get("name"),
        "fullName": data.get("fullName"),
        "url": data.get("url"),
        "buildable": data.get("buildable"),
        "description": data.get("description") or None,
        "parameters": parameters,
        "recentBuilds": builds_out,
        # How many the API offered, so the reader can tell "the job has 5
        # builds" from "you asked for 5 of 137".
        "recentBuildsTotal": len(all_builds),
    }, _render_job_info)


def _render_job_info(p: dict, level: int = 2) -> str:
    blocks = [_md_head("job %s" % p.get("jobPath"), level),
              "\n".join(_md_fields(p, ("fullName", "url", "buildable",
                                       "description")))]

    defs = p.get("parameters") or []
    if defs:
        rows = [[str(d.get("name") or ""),
                 str(d.get("type") or ""),
                 _md_scalar(d.get("defaultValue", "")),
                 ", ".join(_md_scalar(c) for c in (d.get("choices") or [])),
                 str(d.get("description") or "")] for d in defs]
        blocks += [_md_head("parameters (%d)" % len(rows), level + 1),
                   _md_fence(_md_table(
                       ["name", "type", "default", "choices", "description"],
                       rows))]

    builds = p.get("recentBuilds") or []
    if builds:
        rows = [["#%s" % b.get("number"),
                 str(b.get("result") or ""),
                 "yes" if b.get("building") else "no",
                 _format_duration(b.get("durationMs") or 0),
                 str(b.get("timestamp") or "")] for b in builds]
        # No url column: a build URL is `<job url><number>/`, so printing it per
        # row would repeat the job url the header already carries.
        blocks += [_md_head("recent builds (%d of %d)"
                            % (len(rows), p.get("recentBuildsTotal") or len(rows)),
                            level + 1),
                   _md_fence(_md_table(
                       ["build", "result", "building", "duration", "timestamp"],
                       rows))]
    return _md_join(blocks)


# ---------------------------------------------------------------------------
# Handler: get_build_status
# ---------------------------------------------------------------------------

_BUILD_TREE = ("number,result,timestamp,duration,url,displayName,description,"
               "building,estimatedDuration,changeSets[items[msg,author[fullName],date]],"
               "actions[causes[shortDescription,userId,userName]]")


def handle_get_build_status(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    build_number = str(params.get("build_number") or "lastBuild")

    api_path = _format_job_path(job_path)
    url = (f"{JenkinsConfig.endpoint}/{api_path}/{build_number}/api/json"
           f"?tree={urllib.parse.quote(_BUILD_TREE, safe='')}")

    result = _do_request("GET", url, follow_redirects=False)

    if result.status == 404:
        return _ok({
            "error": "Build not found",
            "status": 404,
            "message": f"Build '{build_number}' was not found for job '{job_path}'",
            "hint": ("If `job_path` points to a folder or multibranch parent, it has no builds of its own. "
                     "Use list_jobs to find the buildable child (typically a branch like 'master')."),
        })
    if result.status in (301, 302, 303, 307, 308):
        location = result.headers.get("Location") or result.headers.get("location") or ""
        return _ok({
            "error": f"Got {result.status} redirect",
            "status": result.status,
            "location": location,
            "jobPath": job_path,
            "hint": ("Jenkins redirected the build-status query, which usually means `job_path` is a folder or "
                     "multibranch parent (no buildable lastBuild). Run list_jobs on this path to enumerate the "
                     "child branches/jobs, then re-query with the leaf path."),
        })
    if result.status != 200:
        return _ok({
            "error": "Failed to get build status",
            "status": result.status,
            "message": _extract_message(result.body),
        })

    data = result.body if isinstance(result.body, dict) else {}

    changes: List[dict] = []
    for cs in data.get("changeSets") or []:
        for item in cs.get("items") or []:
            changes.append({
                "message": item.get("msg"),
                "author": (item.get("author") or {}).get("fullName"),
                "date": _format_iso_str(item.get("date")),
            })

    causes: List[dict] = []
    for action in data.get("actions") or []:
        for cause in action.get("causes") or []:
            entry = {
                "description": cause.get("shortDescription"),
                "userId": cause.get("userId"),
                "userName": cause.get("userName"),
            }
            if entry["description"]:
                causes.append(entry)

    return _ok({
        "jobPath": job_path,
        "buildNumber": data.get("number"),
        "displayName": data.get("displayName"),
        "result": data.get("result") or ("BUILDING" if data.get("building") else "UNKNOWN"),
        "building": data.get("building"),
        "timestamp": _format_iso(data.get("timestamp")),
        "duration": _format_duration(data.get("duration") or 0),
        "durationMs": data.get("duration"),
        "estimatedDuration": data.get("estimatedDuration"),
        "url": data.get("url"),
        "description": data.get("description") or None,
        "causes": causes,
        "changes": changes,
    }, _render_build_status)


def _render_build_status(p: dict, level: int = 2) -> str:
    blocks = [_md_head("build %s #%s" % (p.get("jobPath"), p.get("buildNumber")),
                       level),
              "\n".join(_md_fields(p, ("result", "building", "displayName",
                                       "timestamp", "duration", "durationMs",
                                       "estimatedDuration", "url",
                                       "description")))]

    causes = p.get("causes") or []
    if causes:
        lines = []
        for c in causes:
            who = c.get("userId") or c.get("userName")
            lines.append("- %s%s" % (c.get("description"),
                                     " (%s)" % who if who else ""))
        blocks += [_md_head("causes (%d)" % len(causes), level + 1),
                   "\n".join(lines)]

    changes = p.get("changes") or []
    if changes:
        blocks += [_md_head("changes (%d)" % len(changes), level + 1),
                   "\n".join("- %s — %s (%s)"
                             % (c.get("message"), c.get("author") or "?",
                                c.get("date") or "?") for c in changes)]
    return _md_join(blocks)


def _format_iso_str(date_str: Optional[str]) -> Optional[str]:
    """Jenkins changeSet item.date is often already an ISO string."""
    if not date_str:
        return None
    try:
        # Already ISO
        return date_str
    except Exception:
        return str(date_str)


def _extract_message(body: Any) -> str:
    if isinstance(body, dict):
        return body.get("message") or json.dumps(body)[:500]
    if isinstance(body, str):
        return body[:500]
    return str(body)[:500]


# ---------------------------------------------------------------------------
# Handler: get_build_log (full / pipeline / stage)
# ---------------------------------------------------------------------------

def handle_get_build_log(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    build_number = str(params.get("build_number") or "lastBuild")
    mode = params.get("mode") or "full"
    if mode not in ("full", "pipeline", "stage"):
        raise ValueError("mode must be 'full', 'pipeline' or 'stage'")
    stage_id = params.get("stage_id")
    stage_name = params.get("stage_name")
    start_line = params.get("start_line")
    if start_line is None:
        # The row-accounting line this handler emits ends in `offset=<n> for
        # more`, so `offset` HAS to land somewhere: accepted here as a synonym
        # of start_line rather than in PARAM_ALIASES, because `offset` means the
        # row window in list_jobs / get_test_report and must not be rewritten
        # globally. Without this the resume hint would name a parameter the log
        # handler ignores — a hint that silently does nothing.
        start_line = params.get("offset")
    max_lines = params.get("max_lines", 1000)

    api_path = _format_job_path(job_path)
    base_url = f"{JenkinsConfig.endpoint}/{api_path}/{build_number}"

    # --- pipeline overview --------------------------------------------------
    if mode == "pipeline":
        result = _do_request("GET", f"{base_url}/wfapi/describe")
        if result.status == 404:
            return _ok({"error": "Not found", "status": 404,
                        "message": f"Build '{build_number}' has no pipeline data for job '{job_path}'"})
        if result.status != 200 or not isinstance(result.body, dict):
            return _ok({"error": "Failed to get pipeline overview",
                        "status": result.status,
                        "message": _extract_message(result.body)})
        pipeline = result.body
        stages_out: List[dict] = []
        for stage in pipeline.get("stages") or []:
            entry = {
                "id": stage.get("id"),
                "name": stage.get("name"),
                "status": stage.get("status"),
                "duration": _format_duration(stage.get("durationMillis") or 0),
                "durationMs": stage.get("durationMillis"),
            }
            if stage.get("error"):
                entry["error"] = stage["error"]
            stages_out.append(entry)
        return _ok({
            "jobPath": job_path,
            "buildNumber": pipeline.get("id") or build_number,
            "name": pipeline.get("name"),
            "status": pipeline.get("status"),
            "duration": _format_duration(pipeline.get("durationMillis") or 0),
            "stages": stages_out,
        }, _render_pipeline)

    # --- stage mode ---------------------------------------------------------
    if mode == "stage":
        resolved_id = stage_id
        resolved_name = stage_name

        if not resolved_id and stage_name:
            r = _do_request("GET", f"{base_url}/wfapi/describe")
            if r.status != 200 or not isinstance(r.body, dict):
                return _ok({"error": "Failed to resolve stage",
                            "status": r.status, "message": _extract_message(r.body)})
            stages = r.body.get("stages") or []
            match = next((s for s in stages
                          if (s.get("name") or "").lower() == stage_name.lower()), None)
            if not match:
                avail = ", ".join(s.get("name") or "" for s in stages)
                return _ok({"error": "Stage not found",
                            "message": f"Stage '{stage_name}' not found. Available stages: {avail}"})
            resolved_id = match.get("id")
            resolved_name = match.get("name")

        if not resolved_id:
            return _ok({"error": "Missing parameter",
                        "message": "Provide 'stage_id' or 'stage_name' when using mode 'stage'"})

        log_text = _get_stage_log(base_url, resolved_id)
        paged = _paginate_text(log_text, start_line, max_lines)

        out = {
            "jobPath": job_path,
            "buildNumber": build_number,
            "stageId": resolved_id,
            "totalLines": paged["totalLines"],
            "returnedLines": len(paged["text"].split("\n")),
            "log": paged["text"],
        }
        if resolved_name:
            out["stageName"] = resolved_name
        if start_line is not None:
            out["startLine"] = start_line
        if paged["hasMore"]:
            out["hasMore"] = True
            out["nextStartLine"] = paged.get("nextStartLine")
        return _ok(out, _render_build_log)

    # --- full console log ---------------------------------------------------
    r = _do_request("GET", f"{base_url}/consoleText", response_type="text")
    if r.status == 404:
        return _ok({"error": "Not found", "status": 404,
                    "message": f"Build '{build_number}' was not found for job '{job_path}'"})
    if r.status != 200:
        return _ok({"error": "Failed to get build log",
                    "status": r.status, "message": _extract_message(r.body)})

    paged = _paginate_text(r.body or "", start_line, max_lines)
    out = {
        "jobPath": job_path,
        "buildNumber": build_number,
        "totalLines": paged["totalLines"],
        "returnedLines": len(paged["text"].split("\n")),
        "log": paged["text"],
    }
    if start_line is not None:
        out["startLine"] = start_line
    if paged["hasMore"]:
        out["hasMore"] = True
        out["nextStartLine"] = paged.get("nextStartLine")
    return _ok(out, _render_build_log)


def _render_pipeline(p: dict, level: int = 2) -> str:
    blocks = [_md_head("pipeline %s #%s" % (p.get("jobPath"), p.get("buildNumber")),
                       level),
              "\n".join(_md_fields(p, ("name", "status", "duration")))]

    stages = p.get("stages") or []
    if stages:
        rows = [[str(s.get("id") or ""), str(s.get("name") or ""),
                 str(s.get("status") or ""), str(s.get("duration") or "")]
                for s in stages]
        blocks += [_md_head("stages (%d)" % len(rows), level + 1),
                   _md_fence(_md_table(["id", "stage", "status", "duration"],
                                       rows))]
    failed = [s for s in stages if s.get("error")]
    if failed:
        blocks += [_md_head("stage errors (%d)" % len(failed), level + 1),
                   "\n".join("- `%s` (id %s): %s"
                             % (s.get("name"), s.get("id"),
                                _stage_error(s["error"])) for s in failed)]
    return _md_join(blocks)


def _stage_error(error: Any) -> str:
    """wfapi hands back `{message, type}`; the message is the finding and the
    exception class is the footnote. Anything else falls back to the scalar."""
    if isinstance(error, dict) and error.get("message"):
        kind = error.get("type")
        return "%s%s" % (error["message"], " (%s)" % kind if kind else "")
    return _md_scalar(error)


def _render_build_log(p: dict, level: int = 2) -> str:
    """Console / stage log. The line accounting is the payload's LAST line and
    carries what `totalLines` + `hasMore` + `nextStartLine` used to say."""
    if p.get("stageId"):
        title = "stage log %s #%s — %s (id %s)" % (
            p.get("jobPath"), p.get("buildNumber"),
            p.get("stageName") or "?", p.get("stageId"))
    else:
        title = "console %s #%s" % (p.get("jobPath"), p.get("buildNumber"))

    log_text = p.get("log") or ""
    start = _int_param(p.get("startLine", 0), 0)
    shown = p.get("returnedLines") if log_text else 0
    total = p.get("totalLines") or 0
    body = _md_fence(log_text, "log") if log_text else "_(empty log)_"
    return _md_join([_md_head(title, level), body,
                     _rows_note(start, _int_param(shown, 0), _int_param(total, 0))])


def _get_stage_log(base_url: str, stage_node_id: str) -> str:
    """Try multiple strategies to get the log for a pipeline stage."""
    # 1. wfapi/log on the stage itself
    try:
        r = _do_request("GET", f"{base_url}/execution/node/{stage_node_id}/wfapi/log")
        if r.status == 200 and isinstance(r.body, dict):
            text = _strip_html(r.body.get("text") or "").strip()
            if text:
                return text
    except Exception:
        pass

    # 2. walk child flow nodes
    try:
        r = _do_request("GET", f"{base_url}/execution/node/{stage_node_id}/wfapi/describe")
        if r.status == 200 and isinstance(r.body, dict):
            parts: List[str] = []
            for node in r.body.get("stageFlowNodes") or []:
                try:
                    nr = _do_request("GET", f"{base_url}/execution/node/{node['id']}/wfapi/log")
                    if nr.status == 200 and isinstance(nr.body, dict):
                        t = _strip_html(nr.body.get("text") or "").strip()
                        if t:
                            parts.append(t)
                except Exception:
                    continue
            if parts:
                return "\n".join(parts)
    except Exception:
        pass

    # 3. _links.log.href on descriptor
    try:
        r = _do_request("GET", f"{base_url}/execution/node/{stage_node_id}/wfapi/describe")
        if r.status == 200 and isinstance(r.body, dict):
            href = (((r.body.get("_links") or {}).get("log") or {}).get("href"))
            if href:
                abs_url = href if href.startswith("http") else f"{JenkinsConfig.endpoint}{href}"
                lr = _do_request("GET", abs_url)
                if lr.status == 200 and isinstance(lr.body, dict):
                    text = _strip_html(lr.body.get("text") or "").strip()
                    if text:
                        return text
    except Exception:
        pass

    # 4. flow graph traversal
    try:
        tree = "actions[nodes[id,displayName,parentIds,actions[_class,annotatedText,text]]]"
        url = f"{base_url}/api/json?tree={urllib.parse.quote(tree, safe='')}"
        r = _do_request("GET", url)
        if r.status == 200 and isinstance(r.body, dict):
            flow_action = None
            for a in r.body.get("actions") or []:
                if (a.get("_class") == "org.jenkinsci.plugins.workflow.job.views.FlowGraphAction"
                        or a.get("nodes")):
                    flow_action = a
                    break
            if flow_action and flow_action.get("nodes"):
                nodes = flow_action["nodes"]
                stage_node = next((n for n in nodes if n.get("id") == stage_node_id), None)
                if stage_node:
                    descendants = {stage_node_id}
                    changed = True
                    while changed:
                        changed = False
                        for n in nodes:
                            if n.get("id") in descendants:
                                continue
                            for pid in n.get("parentIds") or []:
                                if pid in descendants:
                                    descendants.add(n["id"])
                                    changed = True
                                    break
                    parts: List[str] = []
                    for n in nodes:
                        if n.get("id") not in descendants:
                            continue
                        for action in n.get("actions") or []:
                            if action.get("annotatedText") or action.get("text"):
                                parts.append(_strip_html(action.get("annotatedText") or action.get("text") or ""))
                                break
                    if parts:
                        return "\n".join(parts)
    except Exception:
        pass

    # 5. last resort — slice consoleText between stage markers
    try:
        r = _do_request("GET", f"{base_url}/consoleText", response_type="text")
        if r.status != 200:
            return ""
        full = r.body or ""
        wf = _do_request("GET", f"{base_url}/wfapi/describe")
        if wf.status != 200 or not isinstance(wf.body, dict):
            return ""
        stages = wf.body.get("stages") or []
        idx = next((i for i, s in enumerate(stages) if s.get("id") == stage_node_id), -1)
        if idx < 0:
            return ""
        lines = full.split("\n")
        stage_name = stages[idx].get("name") or ""
        next_name = stages[idx + 1].get("name") if idx + 1 < len(stages) else None
        start_i = -1
        end_i = len(lines)
        for i, line in enumerate(lines):
            if start_i < 0 and stage_name and stage_name in line:
                start_i = i
            elif start_i >= 0 and next_name and next_name in line:
                end_i = i
                break
        if start_i >= 0:
            return "\n".join(lines[start_i:end_i])
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------------
# Handler: start_build
# ---------------------------------------------------------------------------

def handle_start_build(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    parameters = params.get("parameters") or {}
    if parameters and not isinstance(parameters, dict):
        raise ValueError("'parameters' must be a string-keyed object")
    delay_sec = params.get("delay_sec")

    api_path = _format_job_path(job_path)
    base_url = f"{JenkinsConfig.endpoint}/{api_path}"

    crumb = _get_crumb()
    headers: Dict[str, str] = {}
    if crumb:
        headers[crumb[0]] = crumb[1]

    query: Dict[str, str] = {}
    if isinstance(delay_sec, (int, float)):
        query["delay"] = f"{int(delay_sec)}sec"

    if parameters:
        endpoint = f"{base_url}/buildWithParameters"
        for k, v in parameters.items():
            query[str(k)] = str(v)
    else:
        endpoint = f"{base_url}/build"

    result = _do_request("POST", endpoint, params=query, headers=headers, data=b"")

    if result.status not in (200, 201):
        body = result.body if isinstance(result.body, (dict, list)) else (
            (result.body or "")[:500] if isinstance(result.body, str) else result.body
        )
        return _ok({
            "error": "Failed to start build",
            "status": result.status,
            "jobPath": job_path,
            "endpoint": endpoint,
            "parameters": parameters or None,
            "body": body,
        })

    queue_url = result.headers.get("Location") or result.headers.get("location")
    return _ok({
        "jobPath": job_path,
        "jobUrl": base_url,
        "queueUrl": queue_url,
        "parameters": parameters or None,
        "delaySec": int(delay_sec) if isinstance(delay_sec, (int, float)) else 0,
        "message": "Build queued successfully",
        "hint": ("Use get_queue_item with the queueUrl to find the assigned build number once Jenkins picks it up."
                 if queue_url else
                 "Jenkins did not return a queue URL — you may need to poll the job's lastBuild via get_build_status."),
    }, _render_start_build)


def _render_params(parameters: Any) -> Optional[str]:
    """`k=v, k=v` — a build's parameters are short scalars, so one line beats a
    table and beats a nested object."""
    if not isinstance(parameters, dict) or not parameters:
        return None
    return ", ".join("%s=%s" % (k, _md_scalar(v))
                     for k, v in parameters.items())


def _render_start_build(p: dict, level: int = 2) -> str:
    fields = _md_fields(p, ("queueUrl", "jobUrl", "delaySec"))
    rendered = _render_params(p.get("parameters"))
    if rendered:
        fields.insert(0, "**parameters**: %s" % rendered)
    return _md_join([_md_head("%s — %s" % (p.get("jobPath"),
                                           p.get("message") or "build queued"),
                              level),
                     "\n".join(fields),
                     "_hint: %s_" % p["hint"] if p.get("hint") else ""])


# ---------------------------------------------------------------------------
# Handler: get_queue_item
# ---------------------------------------------------------------------------

def handle_get_queue_item(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    queue_url = params.get("queue_url")
    if not queue_url:
        raise ValueError("Missing required parameter: queue_url")
    wait = _bool_param(params.get("wait", False))
    timeout_sec = int(params.get("timeout_sec", 60))
    if timeout_sec < 1:
        timeout_sec = 1
    if timeout_sec > 600:
        timeout_sec = 600

    api_url = queue_url.rstrip("/") + "/api/json"
    deadline = time.monotonic() + timeout_sec

    while True:
        result = _do_request("GET", api_url)
        if result.status == 404:
            return _ok({
                "error": "Queue item not found",
                "status": 404,
                "queueUrl": queue_url,
                "hint": ("The queue item may have already been picked up and aged out of Jenkins' queue cache. "
                         "Try get_build_status with build_number='lastBuild' on the job."),
            })
        if result.status != 200:
            body = result.body if isinstance(result.body, (dict, list)) else (
                (result.body or "")[:500] if isinstance(result.body, str) else result.body
            )
            return _ok({
                "error": "Failed to query queue item",
                "status": result.status,
                "queueUrl": queue_url,
                "body": body,
            })

        data = result.body if isinstance(result.body, dict) else {}
        executable = data.get("executable") or {}
        if isinstance(executable.get("number"), int):
            return _ok({
                "state": "started",
                "queueUrl": queue_url,
                "buildNumber": executable["number"],
                "buildUrl": executable.get("url"),
            }, _render_queue_item)
        if data.get("cancelled") is True:
            return _ok({
                "state": "cancelled",
                "queueUrl": queue_url,
                "why": data.get("why"),
            }, _render_queue_item)

        if not wait or time.monotonic() >= deadline:
            return _ok({
                "state": "pending",
                "queueUrl": queue_url,
                "why": data.get("why"),
                "blocked": bool(data.get("blocked")),
                "stuck": bool(data.get("stuck")),
                "inQuietPeriod": bool(data.get("inQuietPeriod")),
                "hint": (f"Timed out after {timeout_sec}s. Build still in queue. "
                         "Re-run with a larger timeout_sec or check Jenkins UI for queue blockers."
                         if wait else
                         "Build is still in the queue. Re-run with wait=true to block until it starts."),
            }, _render_queue_item)

        time.sleep(2)


def _render_queue_item(p: dict, level: int = 2) -> str:
    return _md_join([
        _md_head("queue item — %s" % p.get("state"), level),
        "\n".join(_md_fields(p, ("buildNumber", "buildUrl", "queueUrl", "why",
                                 "blocked", "stuck", "inQuietPeriod"))),
        "_hint: %s_" % p["hint"] if p.get("hint") else "",
    ])


# ---------------------------------------------------------------------------
# Handler: download_artifact
# ---------------------------------------------------------------------------

def handle_download_artifact(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    artifact_path = params.get("artifact_path")
    if not artifact_path:
        raise ValueError("Missing required parameter: artifact_path")
    build_number = str(params.get("build_number") or "lastSuccessful")
    return_type = params.get("return_type") or "text"
    if return_type not in ("text", "base64"):
        raise ValueError("return_type must be 'text' or 'base64'")

    api_path = _format_job_path(job_path)

    if build_number == "lastSuccessful":
        info = _do_request("GET", f"{JenkinsConfig.endpoint}/{api_path}/api/json")
        if info.status != 200 or not isinstance(info.body, dict):
            return _ok({"error": "Failed to get last successful build",
                        "status": info.status, "message": _extract_message(info.body)})
        last = info.body.get("lastSuccessfulBuild")
        if not last or "number" not in last:
            return _ok({"error": "No successful builds found",
                        "message": "The job has no successful builds"})
        build_number = str(last["number"])

    artifact_url = f"{JenkinsConfig.endpoint}/{api_path}/{build_number}/artifact/{artifact_path}"
    rt = "bytes" if return_type == "base64" else "text"
    result = _do_request("GET", artifact_url, response_type=rt)

    if result.status == 404:
        return _ok({
            "error": "Artifact not found",
            "status": 404,
            "message": f"The artifact at path '{artifact_path}' was not found in build {build_number}",
        })
    if result.status != 200:
        return _ok({
            "error": "Failed to download artifact",
            "status": result.status,
            "message": _extract_message(result.body),
        })

    content_type = result.headers.get("Content-Type") or result.headers.get("content-type")

    if return_type == "base64":
        content_bytes = result.raw or b""
        content = base64.b64encode(content_bytes).decode("ascii")
        return _ok({
            "jobPath": job_path,
            "buildNumber": build_number,
            "artifactPath": artifact_path,
            "contentType": content_type or "application/octet-stream",
            "contentLength": len(content),
            "encoding": "base64",
            "content": content,
        }, _render_artifact)

    content = result.body if isinstance(result.body, str) else json.dumps(result.body, indent=2)
    return _ok({
        "jobPath": job_path,
        "buildNumber": build_number,
        "artifactPath": artifact_path,
        "contentType": content_type or "text/plain",
        "contentLength": len(content),
        "content": content,
    }, _render_artifact)


def _render_artifact(p: dict, level: int = 2) -> str:
    """All five envelope keys survive: jobPath / buildNumber / artifactPath move
    into the heading, contentType / contentLength / encoding stay fields, and the
    content itself goes in a fence instead of a JSON string with \\n escapes."""
    content = p.get("content") or ""
    return _md_join([
        _md_head("artifact %s #%s — %s" % (p.get("jobPath"), p.get("buildNumber"),
                                           p.get("artifactPath")), level),
        "\n".join(_md_fields(p, ("contentType", "contentLength", "encoding"))),
        _md_fence(content) if content else "_(empty artifact)_",
    ])


# ---------------------------------------------------------------------------
# Handler: list_jobs
# ---------------------------------------------------------------------------

_FOLDER_CLASSES = {
    "com.cloudbees.hudson.plugins.folder.Folder",
    "jenkins.branch.OrganizationFolder",
    "org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject",
}


def handle_list_jobs(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path") or ""
    recursive = _bool_param(params.get("recursive", False))
    name_filter = params.get("filter") or params.get("name_filter")
    max_depth = int(params.get("max_depth", 3))
    # A job list is ROWS, so it pages by row rather than being cut mid-table by
    # the character ceiling. 200 is a whole folder in one call at typical
    # Jenkins folder sizes; a recursive walk of a big instance is what the
    # window is for, and the closing line hands back the offset to resume at.
    max_rows = _int_param(params.get("max_rows", 200), 200)
    offset = max(0, _int_param(params.get("offset", 0), 0))

    name_re = None
    if name_filter:
        try:
            name_re = re.compile(name_filter, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid filter regex: {exc}")

    tree = "jobs[name,fullName,url,color,buildable,_class]"

    def _fetch(path: str) -> Tuple[int, list]:
        if path:
            base = f"{JenkinsConfig.endpoint}/{_format_job_path(path)}"
        else:
            base = JenkinsConfig.endpoint
        url = f"{base}/api/json?tree={urllib.parse.quote(tree, safe='')}"
        r = _do_request("GET", url)
        if r.status != 200 or not isinstance(r.body, dict):
            return r.status, []
        return 200, r.body.get("jobs") or []

    def _walk(path: str, depth: int) -> List[dict]:
        status, jobs = _fetch(path)
        if status != 200:
            return []
        out: List[dict] = []
        for j in jobs:
            entry = {
                "name": j.get("name"),
                "fullName": j.get("fullName") or (f"{path}/{j.get('name')}" if path else j.get("name")),
                "url": j.get("url"),
                "buildable": j.get("buildable"),
                "color": j.get("color"),
                "class": j.get("_class"),
                "isFolder": j.get("_class") in _FOLDER_CLASSES,
            }
            if not name_re or name_re.search(entry["fullName"] or ""):
                out.append(entry)
            if recursive and entry["isFolder"] and depth < max_depth:
                child_path = entry["fullName"] or (f"{path}/{j.get('name')}" if path else j.get("name"))
                out.extend(_walk(child_path, depth + 1))
        return out

    status, top = _fetch(job_path)
    if status == 404:
        return _ok({"error": "Path not found", "status": 404, "jobPath": job_path or "(root)"})
    if status != 200:
        return _ok({"error": "Failed to list jobs", "status": status, "jobPath": job_path or "(root)"})

    jobs_out = _walk(job_path, 0)
    window, rows_note = _row_window(jobs_out, offset, max_rows)

    return _ok({
        "jobPath": job_path or "(root)",
        "recursive": recursive,
        "filter": name_filter,
        "count": len(jobs_out),
        "offset": offset,
        "jobs": window,
        "rowsNote": rows_note,
    }, _render_list_jobs)


def _render_list_jobs(p: dict, level: int = 2) -> str:
    """`fullName` is the identity a caller passes back as `job_path`, so it is
    the first column. `url` and `class` are not printed per row: the url is
    `<endpoint>/job/<a>/job/<b>/` for the same fullName, and the only part of
    the Java class name a caller acts on is folder-vs-job, which is the `kind`
    column. Both derivations are spelled out in the mcp-jenkins skill."""
    jobs = p.get("jobs") or []
    fields = _md_fields(p, ("recursive", "filter"))
    blocks = [_md_head("jobs in %s" % p.get("jobPath"), level), "\n".join(fields)]
    if jobs:
        rows = [[str(j.get("fullName") or j.get("name") or ""),
                 "folder" if j.get("isFolder") else "job",
                 _md_scalar(j.get("buildable")),
                 str(j.get("color") or "")] for j in jobs]
        blocks.append(_md_fence(_md_table(
            ["fullName", "kind", "buildable", "color"], rows)))
    else:
        blocks.append("_(no jobs)_")
    blocks.append(p.get("rowsNote") or "")
    return _md_join(blocks)


# ---------------------------------------------------------------------------
# Handler: cancel_build
# ---------------------------------------------------------------------------

def handle_cancel_build(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    build_number = params.get("build_number")
    if build_number is None or str(build_number).strip() == "":
        raise ValueError("Missing required parameter: build_number")
    mode = params.get("mode") or "stop"
    if mode not in ("stop", "term", "kill"):
        raise ValueError("mode must be 'stop' (graceful), 'term' (terminate) or 'kill' (force)")

    api_path = _format_job_path(job_path)
    url = f"{JenkinsConfig.endpoint}/{api_path}/{build_number}/{mode}"

    crumb = _get_crumb()
    headers: Dict[str, str] = {}
    if crumb:
        headers[crumb[0]] = crumb[1]

    result = _do_request("POST", url, headers=headers, data=b"")

    # Jenkins typically returns 302 on success (redirects to the build page)
    if result.status in (200, 201, 302):
        return _ok({
            "jobPath": job_path,
            "buildNumber": build_number,
            "mode": mode,
            "status": result.status,
            "message": f"Cancel request ('{mode}') sent successfully",
        }, _render_cancel_build)

    body = result.body if isinstance(result.body, (dict, list)) else (
        (result.body or "")[:500] if isinstance(result.body, str) else result.body
    )
    return _ok({
        "error": "Failed to cancel build",
        "status": result.status,
        "jobPath": job_path,
        "buildNumber": build_number,
        "mode": mode,
        "body": body,
    })


def _render_cancel_build(p: dict, level: int = 2) -> str:
    return _md_join([
        _md_head("cancel %s #%s — %s" % (p.get("jobPath"), p.get("buildNumber"),
                                         p.get("message") or "sent"), level),
        "\n".join(_md_fields(p, ("mode", "status"))),
    ])


# ---------------------------------------------------------------------------
# Handler: get_test_report
# ---------------------------------------------------------------------------

def handle_get_test_report(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    build_number = str(params.get("build_number") or "lastBuild")
    only_failed = _bool_param(params.get("only_failed", False))
    max_cases = _int_param(params.get("max_cases", 100), 100)
    include_stack = _bool_param(params.get("include_stack", False))
    # `offset` exists so the row-accounting line can honestly say "offset=N for
    # more": a resume hint the handler ignored would be worse than none.
    offset = max(0, _int_param(params.get("offset", 0), 0))

    api_path = _format_job_path(job_path)
    url = f"{JenkinsConfig.endpoint}/{api_path}/{build_number}/testReport/api/json"

    result = _do_request("GET", url)

    if result.status == 404:
        return _ok({
            "error": "No test report",
            "status": 404,
            "jobPath": job_path,
            "buildNumber": build_number,
            "message": "The build has no published test report (junit/xunit may not have run, or this isn't a test job).",
        })
    if result.status != 200 or not isinstance(result.body, dict):
        return _ok({
            "error": "Failed to fetch test report",
            "status": result.status,
            "message": _extract_message(result.body),
        })

    data = result.body
    fail_count = data.get("failCount", 0)
    pass_count = data.get("passCount", 0)
    skip_count = data.get("skipCount", 0)
    total = pass_count + fail_count + skip_count

    # Every matching case is projected first, then windowed: the window has to
    # know the true match count to say "of <total>", and stopping the walk at
    # max_cases would make that number a guess.
    matched: List[dict] = []
    for suite in data.get("suites") or []:
        suite_name = suite.get("name")
        for case in suite.get("cases") or []:
            status = case.get("status") or ""
            failed = status in ("FAILED", "REGRESSION")
            if only_failed and not failed:
                continue
            entry = {
                "suite": suite_name,
                "className": case.get("className"),
                "name": case.get("name"),
                "status": status,
                "durationSec": case.get("duration"),
            }
            if failed:
                if case.get("errorDetails"):
                    entry["errorDetails"] = case["errorDetails"]
                if include_stack and case.get("errorStackTrace"):
                    entry["errorStackTrace"] = case["errorStackTrace"]
            matched.append(entry)

    cases_out, rows_note = _row_window(matched, offset, max_cases)

    return _ok({
        "jobPath": job_path,
        "buildNumber": build_number,
        "total": total,
        "passCount": pass_count,
        "failCount": fail_count,
        "skipCount": skip_count,
        "duration": data.get("duration"),
        "onlyFailed": only_failed,
        "offset": offset,
        "casesMatched": len(matched),
        "casesReturned": len(cases_out),
        "casesTruncated": offset + len(cases_out) < len(matched),
        "cases": cases_out,
        "rowsNote": rows_note,
    }, _render_test_report)


def _render_test_report(p: dict, level: int = 2) -> str:
    # `tests` is the whole report; the row note below counts the MATCHED cases,
    # which is a different number as soon as only_failed is on.
    counts = "%s (pass %s, fail %s, skip %s)" % (
        p.get("total"), p.get("passCount"), p.get("failCount"), p.get("skipCount"))
    fields = ["**tests**: %s" % counts]
    fields += _md_fields(p, ("duration", "onlyFailed"))
    blocks = [_md_head("test report %s #%s" % (p.get("jobPath"),
                                               p.get("buildNumber")), level),
              "\n".join(fields)]

    cases = p.get("cases") or []
    if cases:
        rows = [[str(c.get("status") or ""),
                 "%s.%s" % (c.get("className") or "?", c.get("name") or "?"),
                 _md_scalar(c.get("durationSec", "")),
                 str(c.get("suite") or "")] for c in cases]
        blocks.append(_md_fence(_md_table(
            ["status", "case", "sec", "suite"], rows)))
    else:
        blocks.append("_(no matching cases)_")

    # Failure detail is the reason anyone calls this, so it gets its own block
    # rather than a table cell that would be cut at the column width.
    failures = [c for c in cases if c.get("errorDetails") or c.get("errorStackTrace")]
    if failures:
        detail = []
        for c in failures:
            detail.append("- `%s.%s`" % (c.get("className") or "?",
                                         c.get("name") or "?"))
            if c.get("errorDetails"):
                detail.append("  %s" % str(c["errorDetails"]).replace("\n", "\n  "))
            if c.get("errorStackTrace"):
                detail.append(_md_fence(str(c["errorStackTrace"])))
        blocks += [_md_head("failure detail (%d)" % len(failures), level + 1),
                   "\n".join(detail)]

    blocks.append(p.get("rowsNote") or "")
    return _md_join(blocks)


# ---------------------------------------------------------------------------
# Handler: replay_build
# ---------------------------------------------------------------------------

def handle_replay_build(params: dict) -> dict:
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    build_number = params.get("build_number")
    if build_number is None or str(build_number).strip() == "":
        raise ValueError("Missing required parameter: build_number")
    main_script = params.get("main_script")

    api_path = _format_job_path(job_path)
    url = f"{JenkinsConfig.endpoint}/{api_path}/{build_number}/replay/run"

    crumb = _get_crumb()
    headers: Dict[str, str] = {}
    if crumb:
        headers[crumb[0]] = crumb[1]

    if main_script is not None:
        form = {"mainScript": main_script, "Jenkinsfile": "", "Script": ""}
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        data = b""

    result = _do_request("POST", url, headers=headers, data=data)

    if result.status not in (200, 201, 302):
        body = result.body if isinstance(result.body, (dict, list)) else (
            (result.body or "")[:500] if isinstance(result.body, str) else result.body
        )
        return _ok({
            "error": "Failed to replay build",
            "status": result.status,
            "jobPath": job_path,
            "buildNumber": build_number,
            "body": body,
            "hint": "Replay requires the Jenkins Pipeline plugin and a Pipeline build. 403/404 usually means the build isn't a pipeline or the user lacks Replay permission.",
        })

    queue_url = result.headers.get("Location") or result.headers.get("location")
    return _ok({
        "jobPath": job_path,
        "buildNumber": build_number,
        "queueUrl": queue_url,
        "scriptOverridden": main_script is not None,
        "message": "Replay queued successfully",
        "hint": "Use get_queue_item with the queueUrl (or run_and_wait) to follow the replay run.",
    }, _render_replay_build)


def _render_replay_build(p: dict, level: int = 2) -> str:
    return _md_join([
        _md_head("replay %s #%s — %s" % (p.get("jobPath"), p.get("buildNumber"),
                                         p.get("message") or "queued"), level),
        "\n".join(_md_fields(p, ("queueUrl", "scriptOverridden"))),
        "_hint: %s_" % p["hint"] if p.get("hint") else "",
    ])


# ---------------------------------------------------------------------------
# Handler: run_and_wait (recipe)
# ---------------------------------------------------------------------------

def _render_section(payload: dict, render: Callable[..., str],
                    level: int = 3) -> str:
    """Render a sub-payload a recipe embedded, error-shape aware.

    The recipes keep their sub-results as PAYLOADS (pure data, so the branching
    below reads real values), and hand them back to the same renderer the
    standalone call would have used — one level deeper, so the section headings
    nest instead of colliding.
    """
    if not payload:
        return ""
    if "error" in payload:
        return _render_error(payload, level)
    return render(payload, level)


def handle_run_and_wait(params: dict) -> dict:
    """Chained workflow: start_build -> wait queue -> poll status until !building."""
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    parameters = params.get("parameters") or {}
    delay_sec = params.get("delay_sec")
    overall_timeout = int(params.get("timeout_sec", 1800))
    poll_interval = max(1, int(params.get("poll_interval_sec", 5)))
    log_tail = int(params.get("log_tail", 0))

    deadline = time.monotonic() + overall_timeout

    # --- step 1: start_build ------------------------------------------------
    start = _payload(handle_start_build({
        "job_path": job_path,
        "parameters": parameters,
        "delay_sec": delay_sec,
    }))
    if "error" in start:
        return _ok({"phase": "start_build", "outcome": "error", "start": start},
                   _render_run_and_wait)
    queue_url = start.get("queueUrl")
    if not queue_url:
        return _ok({"phase": "start_build", "outcome": "no_queue_url", "start": start},
                   _render_run_and_wait)

    # --- step 2: wait for queue → build number -----------------------------
    remaining = max(1, int(deadline - time.monotonic()))
    queue = _payload(handle_get_queue_item({
        "queue_url": queue_url,
        "wait": True,
        "timeout_sec": min(600, remaining),
    }))
    state = queue.get("state")
    if state != "started":
        return _ok({
            "phase": "queue",
            "outcome": state or "unknown",
            "queueUrl": queue_url,
            "queue": queue,
        }, _render_run_and_wait)

    build_number = queue.get("buildNumber")
    build_url = queue.get("buildUrl")

    # --- step 3: poll build status until !building --------------------------
    status: dict = {}
    timed_out = False
    while True:
        status = _payload(handle_get_build_status({
            "job_path": job_path,
            "build_number": str(build_number),
        }))
        if "error" not in status and not status.get("building"):
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(poll_interval)

    # --- step 4: optional log tail ------------------------------------------
    tail_lines: Optional[List[str]] = None
    log_total = 0
    if log_tail > 0:
        full_log = _payload(handle_get_build_log({
            "job_path": job_path,
            "build_number": str(build_number),
            "max_lines": 100000,
        }))
        log_text = full_log.get("log") or ""
        if log_text:
            all_lines = log_text.split("\n")
            log_total = len(all_lines)
            tail_lines = all_lines[-log_tail:]

    out = {
        "phase": "timeout" if timed_out else "completed",
        "jobPath": job_path,
        "buildNumber": build_number,
        "buildUrl": build_url,
        "result": status.get("result"),
        "building": status.get("building"),
        "duration": status.get("duration"),
        "durationMs": status.get("durationMs"),
        "queueUrl": queue_url,
        "elapsedSec": int(overall_timeout - max(0, deadline - time.monotonic())),
    }
    if tail_lines is not None:
        out["logTail"] = "\n".join(tail_lines)
        out["logTailLines"] = len(tail_lines)
        out["logTotalLines"] = log_total
    if timed_out:
        out["hint"] = ("Overall timeout reached while build was still running. "
                       "Re-run with a larger timeout_sec, or use get_build_status / cancel_build manually.")
    # tail-biased: the recap is the LAST line of this document, so a reply that
    # has to be cut keeps the verdict and the end of the log — the two things
    # the caller waited for — and loses the queue bookkeeping at the top.
    return _ok(out, _render_run_and_wait, bias="tail")


def _md_log_tail(p: dict, level: int) -> str:
    """`### log tail (N of M lines)` + the fenced tail, or "" when absent."""
    tail = p.get("logTail")
    if not tail:
        return ""
    shown = p.get("logTailLines") or len(tail.split("\n"))
    total = p.get("logTotalLines") or shown
    return _md_join([_md_head("log tail (%s of %s lines)" % (shown, total), level),
                     _md_fence(tail, "log")])


def _render_run_and_wait(p: dict, level: int = 2) -> str:
    title = "run_and_wait %s" % (p.get("jobPath") or "")
    if p.get("buildNumber"):
        title += " #%s" % p["buildNumber"]
    blocks = [_md_head(title.strip(), level)]

    phase = p.get("phase")
    if phase in ("start_build", "queue"):
        # Never got a build: say which step stopped it and show that step's own
        # reply verbatim instead of paraphrasing it.
        blocks.append("\n".join(_md_fields(p, ("phase", "outcome", "queueUrl"))))
        if p.get("start"):
            blocks.append(_render_section(p["start"], _render_start_build, level + 1))
        if p.get("queue"):
            blocks.append(_render_section(p["queue"], _render_queue_item, level + 1))
        return _md_join(blocks)

    blocks.append("\n".join(_md_fields(p, ("buildUrl", "queueUrl", "elapsedSec"))))
    blocks.append(_md_log_tail(p, level + 1))
    if p.get("hint"):
        blocks.append("_hint: %s_" % p["hint"])
    blocks.append("**result**: %s — phase %s%s" % (
        p.get("result") or "UNKNOWN", phase,
        ", duration %s" % p["duration"] if p.get("duration") else ""))
    return _md_join(blocks)


# ---------------------------------------------------------------------------
# Handler: inspect_build (recipe — parallel bundle)
# ---------------------------------------------------------------------------

def handle_inspect_build(params: dict) -> dict:
    """Bundle the common investigation chain: job info + build status +
    pipeline overview, plus optional test report (auto-included on failed
    builds) and console log tail. Runs the sub-queries in parallel.
    """
    missing = _require_config()
    if missing is not None:
        return missing

    job_path = params.get("job_path")
    if not job_path:
        raise ValueError("Missing required parameter: job_path")
    build_number = str(params.get("build_number") or "lastBuild")
    log_tail = int(params.get("log_tail", 0))
    include_tests = params.get("include_tests", "auto")  # auto | true | false
    recent_limit = int(params.get("recent_builds_limit", 5))

    tasks = {
        "job": lambda: handle_get_job_info({"job_path": job_path,
                                            "recent_builds_limit": recent_limit}),
        "build": lambda: handle_get_build_status({"job_path": job_path,
                                                  "build_number": build_number}),
        "pipeline": lambda: handle_get_build_log({"job_path": job_path,
                                                  "build_number": build_number,
                                                  "mode": "pipeline"}),
    }

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {key: pool.submit(fn) for key, fn in tasks.items()}
        results = {key: _payload(fut.result()) for key, fut in futures.items()}

    out = {
        "jobPath": job_path,
        "buildNumber": build_number,
        "job": results["job"],
        "build": results["build"],
        "pipeline": results["pipeline"],
    }

    build_result = (results["build"] or {}).get("result")
    want_tests = (include_tests is True
                  or include_tests == "true"
                  or (include_tests in ("auto", None) and build_result in ("FAILURE", "UNSTABLE")))

    follow_up: Dict[str, Callable[[], dict]] = {}
    if want_tests:
        follow_up["testReport"] = lambda: handle_get_test_report({
            "job_path": job_path,
            "build_number": build_number,
            "only_failed": True,
        })
    if log_tail > 0:
        follow_up["logTail"] = lambda: handle_get_build_log({
            "job_path": job_path,
            "build_number": build_number,
            "max_lines": 100000,
        })

    if follow_up:
        with ThreadPoolExecutor(max_workers=len(follow_up)) as pool:
            futures = {key: pool.submit(fn) for key, fn in follow_up.items()}
            for key, fut in futures.items():
                payload = _payload(fut.result())
                if key == "logTail":
                    log_text = (payload or {}).get("log") or ""
                    lines = log_text.split("\n") if log_text else []
                    tail = lines[-log_tail:]
                    out["logTail"] = "\n".join(tail)
                    out["logTailLines"] = len(tail)
                    out["logTotalLines"] = len(lines)
                else:
                    out[key] = payload

    # tail-biased for the same reason as run_and_wait: the verdict closes the
    # document, and the log tail immediately above it is what a failed build is
    # read for. The job metadata at the top is the cheapest thing to lose.
    return _ok(out, _render_inspect_build, bias="tail")


def _render_inspect_build(p: dict, level: int = 2) -> str:
    build = p.get("build") or {}
    blocks = [_md_head("inspect_build %s #%s" % (p.get("jobPath"),
                                                 p.get("buildNumber")), level),
              _render_section(p.get("job") or {}, _render_job_info, level + 1),
              _render_section(build, _render_build_status, level + 1),
              _render_section(p.get("pipeline") or {}, _render_pipeline, level + 1),
              _render_section(p.get("testReport") or {}, _render_test_report,
                              level + 1),
              _md_log_tail(p, level + 1)]

    if "error" in build:
        verdict = "unknown — build status unavailable"
    else:
        verdict = "%s%s" % (build.get("result") or "UNKNOWN",
                            " in %s" % build["duration"] if build.get("duration") else "")
    blocks.append("**verdict**: %s" % verdict)
    return _md_join(blocks)


# ---------------------------------------------------------------------------
# Status / handler registry
# ---------------------------------------------------------------------------

def handle_status(params: dict) -> dict:
    """Effective configuration + available functions. Pass `test:true` to probe
    the Jenkins endpoint with a GET /api/json — handy to verify auth/network.
    """
    available = sorted({name for name in HANDLERS.keys() if name not in _ALIAS_TARGETS})

    out: dict = {
        "server": "mcp-jenkins",
        "version": "0.1.0",
        "endpoint": JenkinsConfig.endpoint,
        "username": JenkinsConfig.username,
        "tokenPreview": JenkinsConfig.masked_token(),
        "tokenConfigured": bool(JenkinsConfig.token),
        "auth": "configured" if JenkinsConfig.is_ready() else "missing",
        "project": JenkinsConfig.project,
        "projectScope": (
            f"All job_path params are relative to '{JenkinsConfig.project}'. "
            "Prefix with '/' to use an absolute path."
            if JenkinsConfig.project else "disabled"
        ),
        "timeoutSec": JenkinsConfig.timeout,
        "maxAnswerChars": DEFAULT_MAX_ANSWER_CHARS,
        "debug": JenkinsConfig.debug,
        "logFile": JenkinsConfig.log_file,
        "configSources": dict(JenkinsConfig.sources),
        "functions": available,
    }

    if params.get("test") or params.get("probe"):
        out["connection"] = _probe_connection()

    return _ok(out, _render_status)


def _render_status(p: dict, level: int = 2) -> str:
    fields = _md_fields(p, ("endpoint", "username", "tokenPreview", "auth",
                            "project", "projectScope", "timeoutSec",
                            "maxAnswerChars", "debug", "logFile"))
    sources = p.get("configSources") or {}
    if sources:
        fields.append("**configSources**: " + ", ".join(
            "%s=%s" % kv for kv in sorted(sources.items())))
    funcs = p.get("functions") or []
    fields.append("**functions** (%d): %s" % (len(funcs), ", ".join(funcs)))
    # The tool description is ~200 chars now, so this reply is where a model
    # that called with no function learns where the real reference lives.
    fields.append("**reference**: the mcp-jenkins skill — parameters per "
                  "function, the job-path rule, project scope, pagination, "
                  "the answer ceiling and the workflow recipes")

    blocks = [_md_head("%s %s" % (p.get("server"), p.get("version")), level),
              "\n".join(fields)]
    conn = p.get("connection")
    if conn:
        blocks += [_md_head("connection", level + 1),
                   "\n".join(_md_fields(conn, list(conn.keys())))]
    return _md_join(blocks)


def _probe_connection() -> dict:
    """GET /api/json on the Jenkins endpoint to verify reachability + auth."""
    if not JenkinsConfig.is_ready():
        return {"reachable": False, "reason": "auth not configured"}
    url = f"{JenkinsConfig.endpoint}/api/json?tree=mode,nodeName,quietingDown,useSecurity,numExecutors"
    try:
        r = _do_request("GET", url, timeout=10)
    except Exception as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}
    info: dict = {"status": r.status, "reachable": r.status == 200}
    jenkins_version = r.headers.get("X-Jenkins")
    if jenkins_version:
        info["jenkinsVersion"] = jenkins_version
    if r.status == 200 and isinstance(r.body, dict):
        info["mode"] = r.body.get("mode")
        info["nodeName"] = r.body.get("nodeName")
        info["quietingDown"] = r.body.get("quietingDown")
        info["useSecurity"] = r.body.get("useSecurity")
        info["numExecutors"] = r.body.get("numExecutors")
    elif r.status == 401 or r.status == 403:
        info["hint"] = "Auth rejected by Jenkins. Verify JENKINS_USERNAME / JENKINS_TOKEN."
    elif r.status >= 400:
        info["body"] = _extract_message(r.body)
    return info


HANDLERS: Dict[str, Callable[[dict], dict]] = {
    "status": handle_status,
    "config": handle_status,
    "info": handle_status,
    "get_job_info": handle_get_job_info,
    "job_info": handle_get_job_info,
    "job": handle_get_job_info,
    "list_builds": handle_get_job_info,
    "get_build_status": handle_get_build_status,
    "build_status": handle_get_build_status,
    "status_build": handle_get_build_status,
    "get_build": handle_get_build_status,
    "get_build_log": handle_get_build_log,
    "build_log": handle_get_build_log,
    "log": handle_get_build_log,
    "console": handle_get_build_log,
    "start_build": handle_start_build,
    "build": handle_start_build,
    "trigger": handle_start_build,
    "get_queue_item": handle_get_queue_item,
    "queue": handle_get_queue_item,
    "queue_item": handle_get_queue_item,
    "download_artifact": handle_download_artifact,
    "artifact": handle_download_artifact,
    "download": handle_download_artifact,
    "list_jobs": handle_list_jobs,
    "list": handle_list_jobs,
    "ls": handle_list_jobs,
    "cancel_build": handle_cancel_build,
    "cancel": handle_cancel_build,
    "stop": handle_cancel_build,
    "abort": handle_cancel_build,
    "get_test_report": handle_get_test_report,
    "test_report": handle_get_test_report,
    "tests": handle_get_test_report,
    "junit": handle_get_test_report,
    "replay_build": handle_replay_build,
    "replay": handle_replay_build,
    "run_and_wait": handle_run_and_wait,
    "wait_build": handle_run_and_wait,
    "build_wait": handle_run_and_wait,
    "run_build": handle_run_and_wait,
    "inspect_build": handle_inspect_build,
    "inspect": handle_inspect_build,
    "summary": handle_inspect_build,
    "summarize_build": handle_inspect_build,
    "overview": handle_inspect_build,
}

# canonical name -> aliases (for status listing)
_CANONICAL_FUNCTIONS = {
    "status", "get_job_info", "get_build_status", "get_build_log",
    "start_build", "get_queue_item", "download_artifact",
    "list_jobs", "cancel_build", "get_test_report", "replay_build", "run_and_wait",
    "inspect_build",
}
_ALIAS_TARGETS = set(HANDLERS.keys()) - _CANONICAL_FUNCTIONS


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _finish(result: dict, params: dict) -> dict:
    """Render one handler result and apply the answer ceiling — ONCE, here.

    Rendering and capping live at the dispatcher boundary rather than in the
    handlers so that a recipe's embedded sections are capped as one document,
    not thirteen times over, and so that every function obeys the same ceiling
    without each one having to remember to.
    """
    text = _cap_text(_render(result), _max_answer_chars(params),
                     bias=result.get("__bias__") or "head")
    return {"__raw_text__": text, "__is_error__": bool(result.get("__is_error__"))}


def handle_jenkins_call(arguments: dict) -> dict:
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    params_in = arguments.get("params") or arguments.get("p") or {}
    try:
        params = _resolve_aliases(params_in)
    except ValueError as exc:
        return _finish(_err(str(exc)), {})
    params = _apply_project_scope(params)

    if not function:
        return _finish(handle_status(params), params)

    handler = HANDLERS.get(function)
    if not handler:
        canonical = sorted(_CANONICAL_FUNCTIONS)
        return _finish(
            _err(f"Unknown function: {function}. Available: {', '.join(canonical)}"),
            params)

    try:
        return _finish(handler(params), params)
    except (ValueError, KeyError) as exc:
        return _finish(_err(str(exc)), params)
    except urllib.error.URLError as exc:
        return _finish(_err(f"Network error: {exc}"), params)
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        log.exception("Handler %s raised", function)
        return _finish(_err(f"{type(exc).__name__}: {exc}"), params)


# ---------------------------------------------------------------------------
# MCP tool definition
# ---------------------------------------------------------------------------

# The description is sent on EVERY request, whether or not anything Jenkins-
# shaped comes up; the skill is loaded only when it does. What stays here is the
# trigger surface (Jenkins / CI / build / job / console log / artifact / queue /
# test report) plus the two facts a model needs before it can ask anything else:
# how to get the function list, and where the full reference lives. The 4805
# characters this used to spend — the function catalogue, the job-path rule,
# project scope, pagination and the recipes — all moved to the skill, in fuller
# form than they fitted here.
JENKINS_CALL_TOOL = {
    "name": "jenkins_call",
    "description": (
        "Jenkins CI: jobs, builds, console logs, artifacts, queue, test reports; "
        "trigger/cancel/replay builds. No 'function' -> status + function list. "
        "Full reference: the mcp-jenkins skill."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "description": "Function name (e.g. get_build_log, start_build). Alias: 'f'",
            },
            "params": {
                "type": "object",
                "description": "Function parameters (see the mcp-jenkins skill). Alias: 'p'",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# MCP server (stdio, JSON-RPC 2.0)
# ---------------------------------------------------------------------------

class McpServer:
    """Minimal MCP server over stdio. Mirrors mcp-purity's loop."""

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, endpoint=%s, auth=%s, project=%s",
                 JenkinsConfig.endpoint,
                 "configured" if JenkinsConfig.is_ready() else "missing",
                 JenkinsConfig.project or "(none)")
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("Invalid JSON: %s", exc)
                    continue

                log.debug("<- %s", json.dumps(msg)[:200])
                try:
                    response = await loop.run_in_executor(None, self._handle_message, msg)
                except Exception as exc:
                    log.exception("Unhandled exception while handling message")
                    response = self._error(
                        msg.get("id"), -32603,
                        f"Internal error: {type(exc).__name__}: {exc}",
                    )
                if response is not None:
                    out = json.dumps(response)
                    log.debug("-> %s", out[:200])
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()
        finally:
            log.info("MCP server shutting down")

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if msg_id is None:
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mcp-jenkins", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": [JENKINS_CALL_TOOL]})

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if tool_name != "jenkins_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                return self._result(msg_id, {
                    "content": [{"type": "text", "text":
                        f"'arguments' was a string but not valid JSON: {exc}."}],
                    "isError": True,
                })
        if not isinstance(arguments, dict):
            return self._result(msg_id, {
                "content": [{"type": "text", "text":
                    f"'arguments' must be an object; got {type(arguments).__name__}."}],
                "isError": True,
            })

        try:
            result = handle_jenkins_call(arguments)
        except Exception as exc:
            log.exception("Unhandled exception in handle_jenkins_call")
            result = {"__is_error__": True, "error": f"Internal server error: {type(exc).__name__}: {exc}"}
        is_error = bool(result.get("__is_error__"))
        text = result.get("__raw_text__") or result.get("error", "")

        return self._result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

HANDLER_DESCRIPTIONS = {
    "status":             "Server status + listed functions (called when no function is supplied)",
    "list_jobs":          "List jobs at a folder level (recursive=true to walk; filter=regex on fullName; max_rows/offset)",
    "get_job_info":       "Fetch job metadata, parameter definitions, recent builds",
    "get_build_status":   "Get status of a build (or lastBuild/lastSuccessful/lastFailed/lastCompleted)",
    "get_build_log":      "Console log: mode=full (default) | pipeline | stage. Pages by start_line (alias offset) / max_lines",
    "get_test_report":    "JUnit-style test report (only_failed=true, max_cases=N, offset=N, include_stack=true)",
    "start_build":        "Trigger a build (optional `parameters` + `delay_sec`). Honors CSRF crumbs",
    "cancel_build":       "Stop a running build (mode: stop=graceful | term | kill)",
    "replay_build":       "Replay a Pipeline build (optional main_script override)",
    "get_queue_item":     "Resolve a queue URL to a build number (set wait=true to poll)",
    "run_and_wait":       "RECIPE: start_build -> wait queue -> poll until !building. log_tail=N for tail",
    "inspect_build":      "RECIPE: job_info + build_status + pipeline (parallel). Auto-adds test report on failure",
    "download_artifact":  "Download an artifact (return_type='text' or 'base64'; supports lastSuccessful)",
}


def _env_first(*names: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (value, env_name_that_provided_it) — or (None, None)."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v, n
    return None, None


def _pick(cli_value: Optional[str], cli_flag: str, *env_names: str) -> Tuple[Optional[str], str]:
    """CLI flag overrides env. Returns (value, source description)."""
    if cli_value:
        return cli_value, cli_flag
    val, env = _env_first(*env_names)
    if val:
        return val, f"env:{env}"
    return None, "<unset>"


def main() -> None:
    if "--list" in sys.argv:
        print("mcp-jenkins - available functions:\n")
        for name in ("status", "list_jobs", "get_job_info", "get_build_status", "get_build_log",
                     "get_test_report", "start_build", "cancel_build", "replay_build",
                     "get_queue_item", "run_and_wait", "inspect_build", "download_artifact"):
            print(f"  {name:20s} {HANDLER_DESCRIPTIONS.get(name, '')}")
        aliases = sorted(_ALIAS_TARGETS)
        if aliases:
            print("\nAliases: " + ", ".join(aliases))
        print(f"\nEvery reply is markdown, capped at max_answer_chars "
              f"(default {DEFAULT_MAX_ANSWER_CHARS}).")
        print("Full model-facing reference: the mcp-jenkins skill.")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="MCP-Jenkins: Pure Python Jenkins MCP server")
    parser.add_argument("--endpoint", help="Jenkins base URL (overrides JENKINS_ENDPOINT)")
    parser.add_argument("--username", help="Jenkins username (overrides JENKINS_USERNAME)")
    parser.add_argument("--token", help="Jenkins API token (overrides JENKINS_TOKEN)")
    parser.add_argument(
        "--project",
        help=("Default project/folder prefix (overrides JENKINS_PROJECT). When set, "
              "user-supplied job_path values are treated as relative to this project; "
              "prefix a job_path with '/' to use an absolute path."),
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds (default: 30)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    # Accepted (and ignored) for config-compat with other mcp-* servers
    parser.add_argument("--project-root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    endpoint, ep_src = _pick(args.endpoint, "--endpoint", "JENKINS_ENDPOINT", "jenkins_endpoint")
    username, user_src = _pick(args.username, "--username", "JENKINS_USERNAME", "jenkins_username")
    token, token_src = _pick(args.token, "--token", "JENKINS_TOKEN", "jenkins_token")
    project_raw, proj_src = _pick(args.project, "--project", "JENKINS_PROJECT", "jenkins_project")

    JenkinsConfig.endpoint = (endpoint or "").rstrip("/")
    JenkinsConfig.username = username
    JenkinsConfig.token = token
    JenkinsConfig.project = _normalize_project(project_raw)
    JenkinsConfig.timeout = max(1, int(args.timeout or 30))
    JenkinsConfig.log_file = args.log_file
    JenkinsConfig.debug = bool(args.debug or args.log_file)
    JenkinsConfig.sources = {
        "endpoint": ep_src,
        "username": user_src,
        "token": token_src,
        "project": proj_src,
        "timeout": "--timeout" if args.timeout != 30 else "default",
    }

    level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
    log_handlers: list = []
    if args.log_file:
        log_handlers.append(logging.FileHandler(args.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    if not JenkinsConfig.is_ready():
        missing = []
        if not JenkinsConfig.endpoint:
            missing.append("JENKINS_ENDPOINT (or --endpoint)")
        if not JenkinsConfig.username:
            missing.append("JENKINS_USERNAME (or --username)")
        if not JenkinsConfig.token:
            missing.append("JENKINS_TOKEN (or --token)")
        print(
            "mcp-jenkins: refusing to start — missing required configuration:\n  - "
            + "\n  - ".join(missing)
            + "\n\nSet these as environment variables or pass via CLI flags. "
              "See `mcp-jenkins.py --help`.",
            file=sys.stderr,
        )
        sys.exit(2)

    server = McpServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
