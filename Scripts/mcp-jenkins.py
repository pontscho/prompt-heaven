#!/usr/bin/env python3
"""MCP-Jenkins: Pure Python Jenkins MCP server.

Single-tool dispatcher pattern: exposes one MCP tool (jenkins_call) that
routes to internal handler functions via the 'function' parameter. Port of
the TypeScript jenkins-mcp-server with the same JSON-shaped responses.

Requires only Python 3.9+ stdlib modules.
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


def _resolve_aliases(params: dict) -> dict:
    """Return a new dict with aliased parameter names resolved to canonical."""
    resolved: dict = {}
    for key, value in params.items():
        canonical = PARAM_ALIASES.get(key, key)
        if canonical not in resolved:
            resolved[canonical] = value
    return resolved


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
# Standard result wrappers
# ---------------------------------------------------------------------------

def _ok(payload: dict) -> dict:
    return {"__raw_text__": json.dumps(payload, indent=2)}


def _err(message: str, **extra) -> dict:
    payload = {"error": message}
    payload.update(extra)
    return {"__raw_text__": json.dumps(payload, indent=2), "__is_error__": True}


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

    builds_out: List[dict] = []
    for b in (data.get("builds") or [])[:limit]:
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
    })


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
    })


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
        })

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
        return _ok(out)

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
    return _ok(out)


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
    })


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
    wait = bool(params.get("wait", False))
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
            })
        if data.get("cancelled") is True:
            return _ok({
                "state": "cancelled",
                "queueUrl": queue_url,
                "why": data.get("why"),
            })

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
            })

        time.sleep(2)


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
        })

    content = result.body if isinstance(result.body, str) else json.dumps(result.body, indent=2)
    return _ok({
        "jobPath": job_path,
        "buildNumber": build_number,
        "artifactPath": artifact_path,
        "contentType": content_type or "text/plain",
        "contentLength": len(content),
        "content": content,
    })


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
    recursive = bool(params.get("recursive", False))
    name_filter = params.get("filter") or params.get("name_filter")
    max_depth = int(params.get("max_depth", 3))

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

    return _ok({
        "jobPath": job_path or "(root)",
        "recursive": recursive,
        "filter": name_filter,
        "count": len(jobs_out),
        "jobs": jobs_out,
    })


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
        })

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
    only_failed = bool(params.get("only_failed", False))
    max_cases = int(params.get("max_cases", 100))
    include_stack = bool(params.get("include_stack", False))

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

    cases_out: List[dict] = []
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
            cases_out.append(entry)
            if len(cases_out) >= max_cases:
                break
        if len(cases_out) >= max_cases:
            break

    return _ok({
        "jobPath": job_path,
        "buildNumber": build_number,
        "total": total,
        "passCount": pass_count,
        "failCount": fail_count,
        "skipCount": skip_count,
        "duration": data.get("duration"),
        "onlyFailed": only_failed,
        "casesReturned": len(cases_out),
        "casesTruncated": len(cases_out) >= max_cases,
        "cases": cases_out,
    })


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
    })


# ---------------------------------------------------------------------------
# Handler: run_and_wait (recipe)
# ---------------------------------------------------------------------------

def _parse_handler_payload(result: dict) -> dict:
    """Parse the JSON payload inside `__raw_text__` back into a dict."""
    raw = result.get("__raw_text__")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


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
    start = _parse_handler_payload(handle_start_build({
        "job_path": job_path,
        "parameters": parameters,
        "delay_sec": delay_sec,
    }))
    if "error" in start:
        return _ok({"phase": "start_build", "outcome": "error", "start": start})
    queue_url = start.get("queueUrl")
    if not queue_url:
        return _ok({"phase": "start_build", "outcome": "no_queue_url", "start": start})

    # --- step 2: wait for queue → build number -----------------------------
    remaining = max(1, int(deadline - time.monotonic()))
    queue = _parse_handler_payload(handle_get_queue_item({
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
        })

    build_number = queue.get("buildNumber")
    build_url = queue.get("buildUrl")

    # --- step 3: poll build status until !building --------------------------
    status: dict = {}
    timed_out = False
    while True:
        status = _parse_handler_payload(handle_get_build_status({
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
    if log_tail > 0:
        full_log = _parse_handler_payload(handle_get_build_log({
            "job_path": job_path,
            "build_number": str(build_number),
            "max_lines": 100000,
        }))
        log_text = full_log.get("log") or ""
        if log_text:
            all_lines = log_text.split("\n")
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
    if timed_out:
        out["hint"] = ("Overall timeout reached while build was still running. "
                       "Re-run with a larger timeout_sec, or use get_build_status / cancel_build manually.")
    return _ok(out)


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
        results = {key: _parse_handler_payload(fut.result()) for key, fut in futures.items()}

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
                payload = _parse_handler_payload(fut.result())
                if key == "logTail":
                    log_text = (payload or {}).get("log") or ""
                    lines = log_text.split("\n") if log_text else []
                    tail = lines[-log_tail:]
                    out["logTail"] = "\n".join(tail)
                    out["logTailLines"] = len(tail)
                    out["logTotalLines"] = len(lines)
                else:
                    out[key] = payload

    return _ok(out)


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
        "timeoutSec": JenkinsConfig.timeout,
        "debug": JenkinsConfig.debug,
        "logFile": JenkinsConfig.log_file,
        "configSources": dict(JenkinsConfig.sources),
        "functions": available,
    }

    if params.get("test") or params.get("probe"):
        out["connection"] = _probe_connection()

    return _ok(out)


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
    "get_build_status": handle_get_build_status,
    "build_status": handle_get_build_status,
    "status_build": handle_get_build_status,
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

def handle_jenkins_call(arguments: dict) -> dict:
    function = (arguments.get("function") or arguments.get("f") or "").strip()
    params_in = arguments.get("params") or arguments.get("p") or {}
    if not isinstance(params_in, dict):
        return _err(f"'params' must be an object, got {type(params_in).__name__}")
    params = _resolve_aliases(params_in)

    if not function:
        return handle_status(params)

    handler = HANDLERS.get(function)
    if not handler:
        canonical = sorted(_CANONICAL_FUNCTIONS)
        return _err(f"Unknown function: {function}. Available: {', '.join(canonical)}")

    try:
        return handler(params)
    except (ValueError, KeyError) as exc:
        return _err(str(exc))
    except urllib.error.URLError as exc:
        return _err(f"Network error: {exc}")
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        log.exception("Handler %s raised", function)
        return _err(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# MCP tool definition
# ---------------------------------------------------------------------------

JENKINS_CALL_TOOL = {
    "name": "jenkins_call",
    "description": (
        "Jenkins integration: list/trigger/cancel/replay builds, query job & build status, "
        "fetch console logs, download artifacts, fetch test reports, follow queue items.\n\n"
        "Single-tool dispatcher: pass `function` + `params` (or `f` + `p`).\n"
        "Without `function` -> returns server status and available functions.\n\n"
        "FUNCTIONS (canonical names + common aliases):\n"
        "  list_jobs         (list, ls)              - list jobs in a folder; recursive=true to walk\n"
        "  get_job_info      (job, job_info)         - params/builds for a job\n"
        "  get_build_status  (build_status)          - single build status (number or lastBuild/lastSuccessful/...)\n"
        "  get_build_log     (log, console)          - console output. mode: full | pipeline | stage\n"
        "  get_test_report   (tests, junit)          - JUnit results. only_failed=true to slim down\n"
        "  start_build       (build, trigger)        - trigger a build; optional parameters + delay_sec\n"
        "  cancel_build      (cancel, stop, abort)   - mode: stop (graceful) | term | kill\n"
        "  replay_build      (replay)                - replay a Pipeline build, optional main_script override\n"
        "  get_queue_item    (queue)                 - resolve a queue URL to a build number; wait=true to poll\n"
        "  run_and_wait      (wait_build, run_build) - RECIPE: start_build -> wait queue -> poll until done\n"
        "  inspect_build     (inspect, summary)      - RECIPE: job_info + build_status + pipeline overview\n"
        "                                              in parallel; auto-includes test report on FAILURE/UNSTABLE\n"
        "  download_artifact (artifact, download)    - download an artifact (return_type: text | base64)\n\n"
        "JOB PATHS: Use `foo/bar/baz` form (slash-separated). For multibranch pipelines the leaf is the branch, "
        "e.g. `sl/my-project/my-project/master`. The parent multibranch folder has NO lastBuild - always go down "
        "to the branch level. URL-encode '/' inside branch names as %2F.\n\n"
        "WORKFLOW RECIPES:\n"
        "  - Trigger and wait for a build:\n"
        "      Use `run_and_wait` (chained: start_build -> get_queue_item wait=true -> poll get_build_status).\n"
        "      Params: job_path, parameters?, delay_sec?, timeout_sec? (default 1800),\n"
        "              poll_interval_sec? (default 5), log_tail? (lines from end of console).\n"
        "  - Manual chain if you need finer control:\n"
        "      1) start_build              -> returns queueUrl\n"
        "      2) get_queue_item wait=true -> returns buildNumber once Jenkins picks it up\n"
        "      3) Poll get_build_status until building=false, then optionally get_build_log / get_test_report.\n"
        "  - Investigate a build (one call):\n"
        "      Use `inspect_build` - returns job_info + build_status + pipeline overview in one shot\n"
        "      (parallel HTTP). On FAILURE/UNSTABLE auto-fetches the test report. log_tail=N for log tail.\n"
        "  - Investigate a failed build (manual):\n"
        "      get_build_status (read result), get_build_log mode='pipeline' (find failed stage),\n"
        "      get_build_log mode='stage' stage_name='...' (drill into that stage),\n"
        "      get_test_report only_failed=true (failing cases).\n"
        "  - Replay with a tweaked Jenkinsfile:\n"
        "      replay_build job_path=... build_number=... main_script=<new Pipeline script>.\n\n"
        "NEVER reach Jenkins through Bash/curl when this tool is available - it handles auth, CSRF crumbs, "
        "multi-branch job-path encoding, and pagination consistently.\n\n"
        "EXAMPLES:\n"
        "  status:           function omitted, or function=\"status\"\n"
        "  list root:        function=\"list_jobs\", params={}\n"
        "  list recursive:   function=\"list_jobs\", params={\"job_path\":\"sl\",\"recursive\":true,\"filter\":\"media\"}\n"
        "  build status:     function=\"get_build_status\", params={\"job_path\":\"my/job/master\",\"build_number\":\"lastBuild\"}\n"
        "  trigger + wait:   function=\"run_and_wait\", params={\"job_path\":\"my/job/master\",\"parameters\":{\"VERSION\":\"1.2.3\"},\"log_tail\":50}\n"
        "  failing tests:    function=\"get_test_report\", params={\"job_path\":\"my/job/master\",\"only_failed\":true}\n"
        "  inspect build:    function=\"inspect_build\", params={\"job_path\":\"my/job/master\",\"build_number\":\"lastBuild\",\"log_tail\":40}\n"
        "  cancel:           function=\"cancel_build\", params={\"job_path\":\"my/job/master\",\"build_number\":42,\"mode\":\"term\"}"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "description": "Function name (status, get_job_info, get_build_status, get_build_log, "
                               "start_build, get_queue_item, download_artifact, or an alias).",
            },
            "params": {
                "type": "object",
                "description": "Function parameters (see function-specific docs).",
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
        log.info("MCP server starting, endpoint=%s, auth=%s",
                 JenkinsConfig.endpoint, "configured" if JenkinsConfig.is_ready() else "missing")
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
                response = await loop.run_in_executor(None, self._handle_message, msg)
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
                "serverInfo": {"name": "mcp-jenkins", "version": "0.1.0"},
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

        result = handle_jenkins_call(arguments)
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
    "list_jobs":          "List jobs at a folder level (recursive=true to walk; filter=regex on fullName)",
    "get_job_info":       "Fetch job metadata, parameter definitions, recent builds",
    "get_build_status":   "Get status of a build (or lastBuild/lastSuccessful/lastFailed/lastCompleted)",
    "get_build_log":      "Console log: mode=full (default) | pipeline | stage. Supports startLine/maxLines paging",
    "get_test_report":    "JUnit-style test report (only_failed=true, max_cases=N, include_stack=true)",
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
        sys.exit(0)

    parser = argparse.ArgumentParser(description="MCP-Jenkins: Pure Python Jenkins MCP server")
    parser.add_argument("--endpoint", help="Jenkins base URL (overrides JENKINS_ENDPOINT)")
    parser.add_argument("--username", help="Jenkins username (overrides JENKINS_USERNAME)")
    parser.add_argument("--token", help="Jenkins API token (overrides JENKINS_TOKEN)")
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

    JenkinsConfig.endpoint = (endpoint or "").rstrip("/")
    JenkinsConfig.username = username
    JenkinsConfig.token = token
    JenkinsConfig.timeout = max(1, int(args.timeout or 30))
    JenkinsConfig.log_file = args.log_file
    JenkinsConfig.debug = bool(args.debug or args.log_file)
    JenkinsConfig.sources = {
        "endpoint": ep_src,
        "username": user_src,
        "token": token_src,
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
