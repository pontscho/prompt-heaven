---
name: mcp-jenkins
description: >-
  Use when working with Jenkins CI via MCP: list/trigger/cancel/replay builds, job and build status,
  console and pipeline-stage logs, artifacts, queue items, JUnit test reports, multibranch job paths.
  One tool: jenkins_call. All functions invoked via the jenkins_call dispatcher.
triggers:
  - jenkins
  - jenkins_call
  - CI build
  - trigger build
  - build status
  - console log
  - build log
  - pipeline stage
  - failed build
  - test report
  - junit results
  - build artifact
  - build queue
  - replay pipeline
  - Jenkinsfile
  - multibranch job
---

# Jenkins MCP Skill — Full API Reference

Tool: `jenkins_call`
Parameters: `function` (string), `params` (object, optional)
Short aliases: `f` for `function`, `p` for `params`
Called without `function` → server status: endpoint, auth, effective project, the answer ceiling and the function list.

## How to call any function

```
mcp__mcp-jenkins__jenkins_call(function="<function_name>",params={...parameters...})
mcp__mcp-jenkins__jenkins_call(f="<function_name>",p={...parameters...})
```

**Example — status of the last build:**
```
mcp__mcp-jenkins__jenkins_call(function="get_build_status",params={"job_path":"my/job/master","build_number":"lastBuild"})
```

**NEVER reach Jenkins through Bash/curl when this tool is available.** It handles HTTP basic auth, CSRF crumbs, multibranch job-path encoding, redirect-following that preserves `Authorization`, and pagination consistently. A hand-rolled `curl` gets all five wrong.

## JOB PATHS — read this before the first call

`job_path` is **slash-separated, no `job/` segments**: `foo/bar/baz`. The server expands it to `job/foo/job/bar/job/baz` itself. A path that already contains `job/` is passed through unchanged, and leading/trailing slashes are stripped.

**For a multibranch pipeline the leaf is the BRANCH**, e.g. `sl/my-project/my-project/master`:

| path | has builds? |
|-|-|
| `sl/my-project` | no — folder |
| `sl/my-project/my-project` | no — multibranch parent |
| `sl/my-project/my-project/master` | YES — the branch job |

The parent multibranch folder has **no `lastBuild`**. Querying a build on it returns a 404 or a 30x redirect, and the reply says so plus what to do: run `list_jobs` on that path to enumerate the child branches, then re-query the leaf.

URL-encode `/` inside a branch name as `%2F` — `feature%2Ffoo` for branch `feature/foo`.

## PROJECT SCOPE

If `JENKINS_PROJECT` (or `--project`) is set, **every `job_path` is resolved RELATIVE to that project**, so the model never has to walk the parent folders. Call `status` first to see the effective project.

| you pass | resolves to |
|-|-|
| missing / empty `job_path` | the project itself |
| `foo/master` | `<project>/foo/master` |
| `<project>/foo/master` | unchanged (idempotent) |
| `/other/foo/master` | `other/foo/master` — leading `/` escapes the scope |

So `list_jobs` with no params lists the jobs inside the project.

## Reply shape and the answer ceiling

Replies are **markdown**: a `## heading`, `**label**: value` lines, whitespace-aligned tables inside a fence for row data, a fence for verbatim blobs (console logs, artifacts). Absent fields are omitted rather than printed as null.

Every function accepts **`max_answer_chars`** (int, default **24000** ≈ 6k tokens). Pass `0` to disable the ceiling when you genuinely want the whole thing.

* **Row-shaped payloads** (log lines, job lists, test cases) drop whole ROWS and close with
  `[showing rows 1-1000 of 5231; offset=1000 for more]` — indices are 1-based inclusive, so `offset=` is literally the value to pass back. Variants: `[3 rows]` (everything, from the top), `[showing rows 5-6 of 6; no rows left]`, `[no rows at offset 99 of 6]`.
* **Everything else** is cut on a line boundary with exactly one closing line:
  `[truncated: kept 23856 of 313941 chars from the head; raise max_answer_chars or narrow the query]`
* Head-biased by default. `run_and_wait` and `inspect_build` are **tail**-biased — their verdict is the last line of the document, so a cut keeps the verdict and the end of the log, and the closing line says `from the tail`.
* If the cut lands inside a fenced block, the fence is repaired so the accounting line never looks like log content.

**Two knobs, two questions.** `max_lines` / `max_rows` / `max_cases` size the PAGE you asked for. `max_answer_chars` is the reply BUDGET, the last-resort net. Narrow the page first; raise the budget only when you really need the volume.

## Functions

13 canonical functions. Aliases in parentheses are accepted for `function`.

### `status` (`config`, `info`)
Effective configuration and the function list. No required params.

* `test` (alias `probe`) — bool. Also `GET /api/json` on the endpoint to verify reachability and auth; adds a `connection` section with Jenkins version, mode, nodeName, numExecutors, or the failure reason.

Use it whenever a job path unexpectedly 404s: it shows the endpoint and the effective project the paths are being resolved against.

### `list_jobs` (`list`, `ls`)
Jobs at a folder level. Nothing is required — no `job_path` means the project root (or the Jenkins root when no project is configured).

* `job_path` — folder to list.
* `recursive` — bool, default `false`. Walk into child folders.
* `filter` (aliases `name_filter`, `nameFilter`) — case-insensitive **regex**, matched against `fullName`. An invalid regex is a parameter error, not a silent no-op.
* `max_depth` (alias `maxDepth`) — int, default `3`. Only meaningful with `recursive`.
* `max_rows` (aliases `rows`, `maxRows`) — int, default `200`. Row window; `0` means no window (then only `max_answer_chars` bounds the reply).
* `offset` (aliases `skip`, `row_offset`, `rowOffset`) — int, default `0`. Row window start; the value the row line hands back.

Columns: `fullName`, `kind` (`job` / `folder`), `buildable`, `color`. **Not printed, by derivation:** the per-job URL is `<endpoint>/job/<a>/job/<b>/` for the same `fullName`, and the Java `_class` collapses to the `kind` column — folders are `Folder`, `OrganizationFolder` and `WorkflowMultiBranchProject`.

`fullName` is the identity to pass back as `job_path`.

> `limit` is **not** an alias for `max_rows` here — it maps to `recent_builds_limit`. Use `max_rows`.

### `get_job_info` (`job_info`, `job`)
Job metadata, parameter definitions, recent builds.

* `job_path` — **required**.
* `recent_builds_limit` (aliases `limit`, `recentBuildsLimit`) — int, default `5`.

Returns name/fullName/url/buildable/description, a `parameters` table (name, type, default, choices, description — this is how you learn what `start_build` accepts), and a `recent builds (N of M)` table. A build URL is `<job url><number>/`.

### `get_build_status` (`build_status`, `status_build`, `get_build`)
One build.

* `job_path` — **required**.
* `build_number` (aliases `build`, `buildNumber`, `num`, `number`) — a number or a Jenkins keyword: `lastBuild` (default), `lastSuccessfulBuild`, `lastFailedBuild`, `lastCompletedBuild`, `lastStableBuild`.

Returns result (`SUCCESS` / `FAILURE` / `UNSTABLE` / `ABORTED` / `BUILDING` / `UNKNOWN`), building, timestamp, duration + durationMs, estimatedDuration, url, description, the `causes` (who or what triggered it) and the SCM `changes`.

A 30x here almost always means `job_path` is a folder or multibranch parent — go down to the branch.

### `get_build_log` (`build_log`, `log`, `console`)
Console output, pipeline overview, or one stage's log — three modes, three different payloads.

* `job_path` — **required**.
* `build_number` — default `lastBuild`.
* `mode` — `full` (default) | `pipeline` | `stage`.
* `stage_id` (alias `stageId`) / `stage_name` (alias `stageName`) — for `mode="stage"`; the name is resolved case-insensitively against the pipeline description, and an unknown name returns the list of available stages.
* `start_line` (aliases `start`, `startLine`, **`offset`**) — int, 0-based, default `0`.
* `max_lines` (aliases `max`, `maxLines`) — int, default `1000`.

`mode="full"` → the fenced console text plus the row line.
`mode="pipeline"` → the stage table (id, stage, status, duration) plus a `stage errors` block naming the failing stage — this is how you find WHICH stage broke before pulling any log.
`mode="stage"` → that stage's log. Five strategies are tried in order (stage `wfapi/log`, child flow nodes, the descriptor's `_links.log.href`, flow-graph traversal, and finally slicing `consoleText` between stage markers), so it still returns something on older Pipeline plugin versions.

`offset` is accepted as a synonym of `start_line` precisely because the row line ends in `offset=<n> for more`.

### `get_test_report` (`test_report`, `tests`, `junit`)
JUnit / xUnit results published by the build.

* `job_path` — **required**.
* `build_number` — default `lastBuild`.
* `only_failed` (alias `onlyFailed`) — bool, default `false`. Keeps `FAILED` and `REGRESSION` cases only — the fastest way to slim a 5000-case report to the 3 that matter.
* `max_cases` (alias `maxCases`) — int, default `100`. Row window; `0` means no window.
* `offset` (aliases `skip`, `row_offset`, `rowOffset`) — int, default `0`. Row window start.
* `include_stack` (alias `includeStack`) — bool, default `false`. Adds `errorStackTrace` to the failure detail.

Returns `**tests**: <total> (pass N, fail N, skip N)` for the whole report, a case table, and a `failure detail` block with `errorDetails` (plus the stack trace when asked). The row line counts the MATCHED cases, which differs from the report total as soon as `only_failed` is on.

A 404 means no report was published — junit/xunit did not run, or this is not a test job.

### `start_build` (`build`, `trigger`)
Trigger a build. Honors CSRF crumbs automatically.

* `job_path` — **required**.
* `parameters` (aliases `params`, `build_parameters`) — string-keyed object. Present → `buildWithParameters`, absent → `build`. Values are stringified.
* `delay_sec` (aliases `delay`, `delaySec`) — int, quiet period.

Returns the `queueUrl` (Jenkins' `Location` header). That is **not** a build number: feed it to `get_queue_item`, or use `run_and_wait` instead of doing this by hand.

Call `get_job_info` first if you do not know the parameter names — a wrong parameter name is accepted silently by Jenkins and simply ignored.

### `cancel_build` (`cancel`, `stop`, `abort`)
* `job_path` — **required**.
* `build_number` — **required** (no `lastBuild` default here, on purpose: cancelling the wrong build is not recoverable).
* `mode` — `stop` (graceful, default) | `term` (terminate) | `kill` (force).

Escalate in that order. Jenkins answers 302 on success, which is reported as success.

### `replay_build` (`replay`)
Re-run a Pipeline build, optionally with a modified script.

* `job_path` — **required**.
* `build_number` — **required**.
* `main_script` (aliases `script`, `mainScript`) — the replacement Pipeline script. Omit to replay the original unchanged.

Returns a `queueUrl` like `start_build`. Requires the Pipeline plugin and Replay permission; 403/404 usually means the build is not a pipeline or the user lacks that permission.

### `get_queue_item` (`queue`, `queue_item`)
Resolve a queue URL to a build number.

* `queue_url` (aliases `url`, `queueUrl`) — **required**, as returned by `start_build` / `replay_build`.
* `wait` — bool, default `false`. Poll (every 2s) until the item starts.
* `timeout_sec` (aliases `timeout`, `timeoutSec`) — int, default `60`, clamped to 1..600.

States: `started` (with `buildNumber` + `buildUrl`), `cancelled`, `pending` (with `why`, `blocked`, `stuck`, `inQuietPeriod`). A 404 usually means the item was already picked up and aged out of the queue cache — query `lastBuild` on the job instead.

### `run_and_wait` (`wait_build`, `build_wait`, `run_build`)
**RECIPE.** `start_build` → `get_queue_item` (wait) → poll `get_build_status` until `building=false`.

* `job_path` — **required**.
* `parameters` — as `start_build`.
* `delay_sec` — as `start_build`.
* `timeout_sec` — int, default `1800` (overall budget).
* `poll_interval_sec` (aliases `pollInterval`, `pollIntervalSec`) — int, default `5`, minimum 1.
* `log_tail` (aliases `logTail`, `tail`) — int, default `0`. Include the last N console lines.

The document ends with `**result**: <result> — phase <completed|timeout>`. Earlier phases report where they stopped (`start_build` / `queue`) and embed that step's own reply. **Tail-biased** — a truncated reply keeps the verdict and the end of the log.

### `inspect_build` (`inspect`, `summary`, `summarize_build`, `overview`)
**RECIPE.** One call for the whole investigation: `get_job_info` + `get_build_status` + `get_build_log mode="pipeline"`, fetched in PARALLEL, then the test report and log tail as needed.

* `job_path` — **required**.
* `build_number` — default `lastBuild`.
* `include_tests` — `auto` (default) | `true` | `false`. `auto` fetches the failed-cases-only test report when the build is `FAILURE` or `UNSTABLE`.
* `log_tail` — int, default `0`. Last N console lines.
* `recent_builds_limit` — int, default `5`.

Sections: job → build → pipeline → test report → log tail → `**verdict**: <result> in <duration>`. **Tail-biased**, same reason. This is the right first call for "why did the build fail" — it replaces four round trips.

### `download_artifact` (`artifact`, `download`)
* `job_path` — **required**.
* `artifact_path` (aliases `artifact`, `file`, `artifactPath`) — **required**, relative to the build's artifact root.
* `build_number` — default `lastSuccessful`, which is resolved to a real number first (via the job's `lastSuccessfulBuild`).
* `return_type` (aliases `returnType`, `type`) — `text` (default) | `base64` for binaries.

Returns contentType, contentLength, `encoding` for base64, and the content in a fence. Binaries are large: set `max_answer_chars` deliberately, and note that a base64 blob is ONE line, so it is the one payload where the cut cannot land on a line boundary.

## Function alias table

| canonical | aliases |
|-|-|
| `status` | `config`, `info` |
| `list_jobs` | `list`, `ls` |
| `get_job_info` | `job_info`, `job` |
| `get_build_status` | `build_status`, `status_build`, `get_build` |
| `get_build_log` | `build_log`, `log`, `console` |
| `get_test_report` | `test_report`, `tests`, `junit` |
| `start_build` | `build`, `trigger` |
| `cancel_build` | `cancel`, `stop`, `abort` |
| `replay_build` | `replay` |
| `get_queue_item` | `queue`, `queue_item` |
| `run_and_wait` | `wait_build`, `build_wait`, `run_build` |
| `inspect_build` | `inspect`, `summary`, `summarize_build`, `overview` |
| `download_artifact` | `artifact`, `download` |

An unknown `function` returns the canonical list, so a wrong guess costs one cheap round trip.

## Parameter alias table

Aliases are resolved globally before dispatch, so any of these work anywhere.

| canonical | aliases |
|-|-|
| `job_path` | `job`, `jobPath`, `path`, `folder`, `folderPath`, `folder_path`, `project`, `project_path` |
| `build_number` | `build`, `buildNumber`, `num`, `number` |
| `parameters` | `params`, `build_parameters` |
| `artifact_path` | `artifact`, `file`, `artifactPath` |
| `queue_url` | `url`, `queueUrl` |
| `recent_builds_limit` | `limit`, `recentBuildsLimit` |
| `delay_sec` | `delay`, `delaySec` |
| `timeout_sec` | `timeout`, `timeoutSec` |
| `start_line` | `start`, `startLine`, `offset` (in `get_build_log`) |
| `max_lines` | `max`, `maxLines` |
| `max_rows` | `rows`, `maxRows` |
| `offset` | `skip`, `row_offset`, `rowOffset` |
| `max_answer_chars` | `max_chars`, `maxChars`, `maxAnswerChars` |
| `stage_id` / `stage_name` | `stageId` / `stageName` |
| `return_type` | `returnType`, `type` |
| `filter` | `name_filter`, `nameFilter` |
| `max_depth` | `maxDepth` |
| `only_failed` / `max_cases` / `include_stack` | `onlyFailed` / `maxCases` / `includeStack` |
| `main_script` | `script`, `mainScript` |
| `poll_interval_sec` | `pollInterval`, `pollIntervalSec` |
| `log_tail` | `logTail`, `tail` |

Note that `project` is an alias for **`job_path`**, not for the configured project scope.

## Workflow recipes

**Trigger and wait** — one call:
```
{"function":"run_and_wait","params":{"job_path":"my/job/master","parameters":{"VERSION":"1.2.3"},"log_tail":50}}
```
Manual chain, when you need finer control:
1. `start_build` → `queueUrl`
2. `get_queue_item` with `wait: true` → `buildNumber` once Jenkins picks it up
3. poll `get_build_status` until `building` is false, then `get_build_log` / `get_test_report`

**Investigate a failed build** — one call:
```
{"function":"inspect_build","params":{"job_path":"my/job/master","build_number":"lastBuild","log_tail":40}}
```
Manual drill-down, when you want to control the volume:
1. `get_build_status` — read `result`
2. `get_build_log` with `mode: "pipeline"` — find the failed stage
3. `get_build_log` with `mode: "stage"` and `stage_name` — that stage's log only, instead of a 300k-char console
4. `get_test_report` with `only_failed: true` — the failing cases

**Page a long console log:**
```
{"function":"get_build_log","params":{"job_path":"my/job/master","build_number":42,"max_lines":500}}
→ [showing rows 1-500 of 5231; offset=500 for more]
{"function":"get_build_log","params":{"job_path":"my/job/master","build_number":42,"offset":500,"max_lines":500}}
```

**Find the buildable leaf of a multibranch job:**
```
{"function":"list_jobs","params":{"job_path":"sl/my-project","recursive":true,"filter":"master"}}
```

**Replay with a tweaked Jenkinsfile:**
```
{"function":"replay_build","params":{"job_path":"my/job/master","build_number":42,"main_script":"<new Pipeline script>"}}
```

## Parallel call strategy

The read-only functions are independent — batch them in one response instead of chaining round-trips.

Safe to batch: `status`, `list_jobs`, `get_job_info`, `get_build_status`, `get_build_log`, `get_test_report`, `download_artifact`.

Do NOT batch anything ordered: `start_build` → `get_queue_item` → `get_build_status`, or a `mode="pipeline"` call whose stage name the next `mode="stage"` call needs. `inspect_build` already parallelises its own sub-queries, so do not hand-roll that bundle.

## Configuration

Environment variables, each overridable by the matching CLI flag. Lowercase spellings (`jenkins_endpoint`, …) are accepted as fallbacks.

| variable | flag | required | meaning |
|-|-|-|-|
| `JENKINS_ENDPOINT` | `--endpoint` | yes | base URL, e.g. `https://jenkins.example.com`. **No** trailing `/job` segment; a trailing slash is stripped |
| `JENKINS_USERNAME` | `--username` | yes | user name for HTTP basic auth |
| `JENKINS_TOKEN` | `--token` | yes | API **token**, not the UI password |
| `JENKINS_PROJECT` | `--project` | no | default project/folder prefix; see PROJECT SCOPE. `sl/foo`, `/sl/foo/` and `job/sl/job/foo` all normalise to `sl/foo` |
| — | `--timeout` | no | HTTP timeout in seconds, default 30 |
| — | `--debug` / `--log-file` | no | debug logging to stderr / to a file |

The server refuses to start when endpoint, username or token is missing. `JENKINS_PROJECT` is optional. Run the server with `--list` for the function list on the command line.

## Examples

```jsonc
// status, plus a live auth/network probe
{"function":"status","params":{"test":true}}

// list the project root, then walk one folder
{"function":"list_jobs","params":{}}
{"function":"list_jobs","params":{"job_path":"sl","recursive":true,"filter":"media"}}

// the last build of a multibranch branch job
{"function":"get_build_status","params":{"job_path":"my/job/master","build_number":"lastBuild"}}

// what parameters does this job take?
{"function":"get_job_info","params":{"job_path":"my/job/master"}}

// trigger with parameters and wait for the verdict
{"function":"run_and_wait","params":{"job_path":"my/job/master","parameters":{"VERSION":"1.2.3"},"log_tail":50}}

// one-call failure investigation
{"function":"inspect_build","params":{"job_path":"my/job/master","build_number":"lastBuild","log_tail":40}}

// just the failing tests, with stack traces
{"function":"get_test_report","params":{"job_path":"my/job/master","only_failed":true,"include_stack":true}}

// one stage's log instead of the whole console
{"function":"get_build_log","params":{"job_path":"my/job/master","build_number":42,"mode":"stage","stage_name":"Build"}}

// abort a stuck build, escalating
{"function":"cancel_build","params":{"job_path":"my/job/master","build_number":42,"mode":"term"}}

// a text artifact from the last successful build
{"function":"download_artifact","params":{"job_path":"my/job/master","artifact_path":"build/report.txt"}}

// a big artifact, ceiling raised deliberately
{"function":"download_artifact","params":{"job_path":"my/job/master","artifact_path":"dist/app.tar.gz","return_type":"base64","max_answer_chars":200000}}
```
