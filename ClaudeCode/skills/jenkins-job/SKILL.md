---
name: jenkins-job
description: >-
  Create, clone and wire Jenkins multibranch pipeline jobs from the terminal with the
  bundled stdlib-only Python CLI at scripts/jenkins_job.py. Use when a task needs a new
  Jenkins multibranch job cloned from a template job, a job's config.xml read (secrets
  masked) or replaced, branch indexing triggered, a fresh multibranch webhook token
  generated for a Bitbucket Server webhook, or a check whether the API token may create
  jobs in a folder. Every write (clone, set-config, scan) is a dry run until --apply is
  given. Auth is JENKINS_USERNAME / JENKINS_TOKEN from the environment only, over HTTPS
  only. Job creation is rare, so this is a skill plus a script, not functions in the
  mcp-jenkins server: for builds, logs and test reports use the mcp-jenkins skill
  instead. The script never deletes anything.
---

# Jenkins job

Create and wire multibranch jobs. One script, stdlib only, Python 3.9, no third-party
HTTP client. Reads need no flag; every write is a dry run until `--apply`.

For builds, build logs, queue, test reports and triggering a build, use the
`mcp-jenkins` skill (`jenkins_call`). This skill exists only for the rare operations that
change job configuration.

## Setup

All values come from the environment. There is deliberately no token flag: a token on a
command line lands in shell history.

| Variable | Required | Meaning |
| --- | --- | --- |
| `JENKINS_ENDPOINT` | yes | Base URL, **https only**; `http://` is refused. `JENKINS_URL` is read as a fallback spelling. Same name as the mcp-jenkins server uses. |
| `JENKINS_USERNAME` | yes | User name for HTTP Basic auth. |
| `JENKINS_TOKEN` | yes | API token (not the UI password). |
| `JENKINS_JOB_ALLOW_INSECURE_LOOPBACK` | no | **Test-only.** `1` accepts an `http://` endpoint if and only if its host is `127.0.0.1` or `::1`. Never set it against a real Jenkins. |

Job paths are slash-separated names, e.g. `sl/ngs-media-server/ngs-media-server`,
spelled by the script as `/job/sl/job/ngs-media-server/job/ngs-media-server` with each
segment URL-quoted. Empty segments, `.` and `..` are refused. A new job name is limited
to letters, digits, `.`, `_` and `-`.

## Commands

```bash
python3 -B scripts/jenkins_job.py whoami [--folder sl/ngs-media-server]
python3 -B scripts/jenkins_job.py get-config sl/ngs-media-server/ngs-media-server
python3 -B scripts/jenkins_job.py clone SOURCE_JOB_PATH NEW_NAME --folder FOLDER_PATH [options] [--apply]
python3 -B scripts/jenkins_job.py set-config JOB_PATH --file config.xml [--apply]
python3 -B scripts/jenkins_job.py scan JOB_PATH [--apply]
```

| Command | What it does |
| --- | --- |
| `whoami` | `GET /me/api/json`: user id and full name. With `--folder`, also whether `<folder>/newJob` loads: HTTP 200 means the user can create items there, 403 means no Job/Create on that folder. Read-only. |
| `get-config` | `GET <job>/config.xml`, printed with secrets masked: the text of any element or attribute whose name contains token, password, passphrase, privateKey, secret or apiKey, and every `{AQAA...}` Jenkins-encrypted value, become `[REDACTED]`. **There is no flag that shows them.** A body that is not well-formed XML is refused rather than printed unmasked. Comments are not shown. |
| `clone` | Copy a job, patch the copy, wire the webhook token. See below. |
| `set-config` | `POST <job>/config.xml` with the file. The file must be well-formed XML, is size-capped (1 MiB) and may not contain a DOCTYPE or ENTITY declaration. It **replaces the whole configuration**. |
| `scan` | `POST <job>/build?delay=0`, which is multibranch branch indexing ("Scan Multibranch Pipeline Now"). Reports the HTTP status. |

### Dry run and `--apply`

`clone`, `set-config` and `scan` print the exact method, URL and a summary of what would
change, send nothing, and exit 0. They act only with `--apply`. `clone` does still *read*
in a dry run (the source config, whether the new name is free) so the dry run can tell
you the patch applies and the name is free, and so a refusal happens before anything is
created.

### `clone` options

```
clone SOURCE_JOB_PATH NEW_NAME --folder FOLDER_PATH
      [--remote URL] [--credentials-id ID] [--script-path P]
      [--display-name S] [--description S]
      [--webhook-token T | --generate-webhook-token PREFIX]
      [--trait-xml FILE ...] [--cron-spec S] [--apply]
```

With `--apply`, in this order:

1. `POST <folder>/createItem?name=NEW_NAME&mode=copy&from=/<SOURCE path>` (with crumb).
2. `GET` the new job's `config.xml`.
3. Patch it with `xml.etree`: the remote and `credentialsId` of the `GitSCMSource`, the
   factory's `scriptPath`, `displayName`, `description`, the
   `ComputedFolderWebHookTrigger`'s `<token>` (and `<spec>` with `--cron-spec`), and each
   `--trait-xml` snippet appended to the Git source's `<traits>`.
4. `POST` the patched `config.xml`.

Rules that matter:

- **The webhook token is always replaced** and never the source's. It is generated with
  Python's `secrets` as `PREFIX-<random>` (`--generate-webhook-token PREFIX`; the default
  prefix is the new job name), or taken from `--webhook-token` (16-128 characters of
  `A-Za-z0-9._~-`, refused if it equals the source's token).
- **The token is printed once**, after a successful config POST, together with the
  invoke URL the Bitbucket webhook needs:
  `<JENKINS_ENDPOINT>/multibranch-webhook-trigger/invoke?token=<token>`. It is written to
  no file and appears on no other line. If the config POST fails it is not printed. In a
  dry run no token exists yet.
- **Refusals before anything is created:** the new name already exists in the folder; the
  source has no `GitSCMSource` or no `...ComputedFolderWebHookTrigger` element to patch.
  Each is one stderr line.
- **A job created but not finished** (reading or patching or posting its config failed
  after step 1) is reported by name and URL with exit 1. The script **never deletes
  anything**; removing or fixing the half-created job is yours to do. Until step 4
  succeeds the copy still carries the source's remote and webhook token.
- A password inside a `--remote` URL is masked in everything the script prints
  (`user:password@` becomes `***@`); an `ssh://git@host` user name is shown.

## Runbook: clone a multibranch job from a template and wire a Bitbucket Server webhook

Placeholders: `<FOLDER>` the Jenkins folder path, `<TEMPLATE>` an existing multibranch job
path, `<NAME>` the new job, `<REMOTE>` the repository's git URL exactly as the Jenkins
agents reach it. No host, folder or credential id is built into the script; always pass
the remote explicitly and confirm it with the user.

1. **Can I?** `whoami --folder <FOLDER>`. HTTP 200 on `newJob` means Job/Create
   on that folder; 403 means ask a Jenkins admin before going further.
2. **Look at the template.** `get-config <TEMPLATE>` (masked).
3. **Dry-run the clone** and read the summary:
   `clone <TEMPLATE> <NAME> --folder <FOLDER> --remote <REMOTE> --generate-webhook-token <NAME>`
   Check the remote, credentials id and script path. Add `--credentials-id`,
   `--script-path`, `--display-name` as the new repository needs.
4. **Apply:** the same command plus `--apply`. Copy the printed invoke URL; it is shown once.
5. **Register the webhook on Bitbucket Server** (project SL, repository `ngs-webrtc`)
   pointing at that invoke URL. Jenkins does **not** register it: the trigger plugin only
   listens. Webhook commands for the `p:bitbucket` skill are **planned**, not available
   yet; until then create it in the Bitbucket UI.
6. **First indexing.** `scan sl/ngs-media-server/ngs-webrtc --apply`, then check the
   result with the `mcp-jenkins` skill. Whether a webhook push alone triggers indexing is
   confirmed by pushing to the repository.

## Facts about the template job (verified read-only on the live Jenkins, 2026-10-26)

The shape of one real template (a `WorkflowMultiBranchProject`); names are examples of
what was observed, not values the script assumes:

- Branch source `GitSCMSource`: remote `ssh://git@<host>:<port>/<project>/<repo>.git`,
  `credentialsId` an SSH key id. Traits: `BranchDiscoveryTrait`,
  `CloneOptionTrait` (shallow=false, noTags=false), `SubmoduleOptionTrait` (recursive,
  parentCredentials), `GitLFSPullTrait`.
- Factory `WorkflowBranchProjectFactory`, `scriptPath` `deploy/jenkins/Jenkinsfile`.
- Trigger `com.igalg.jenkins.plugins.mswt.trigger.ComputedFolderWebHookTrigger` (plugin
  multibranch-scan-webhook-trigger) with a `<token>`; periodic scan off.
- `orphanedItemStrategy`: `pruneDeadBranches=true`, `daysToKeep=7`, `numToKeep=1`.
- The Bitbucket webhook is **not** registered by Jenkins; it is created on Bitbucket
  Server and points at `<jenkins>/multibranch-webhook-trigger/invoke?token=<token>`.
- Bitbucket **build status** comes from the shared library (`notifyBitbucket`), not from
  job configuration.
- A multibranch job's own cron only triggers **branch indexing**. A nightly build must set
  its trigger inside the Jenkinsfile (`properties([pipelineTriggers([cron(...)])])`) and
  live in its **own** multibranch job with a different `scriptPath`.
- The Jenkins user needs Job/Create on the folder; `zoltan.ponekker` was verified able to
  open `<folder>/newJob`.

## PR discovery

**Option A (chosen): keep the plain Git source and add the git-plugin trait "Discover
other refs"** (`jenkins.plugins.git.traits.DiscoverOtherRefsTrait`, form fields `ref` and
`nameMapping`). Bitbucket Server publishes each pull request as a ref under
`refs/pull-requests/<id>/`, and the trait makes the indexer treat those refs as branches,
so a PR shows up as a branch job named by the mapping (`PR-<id>`).

Ref choice:

- `pull-requests/*/from` (the shipped snippet) is the PR's source-branch head: it exists
  as long as the PR does and builds exactly what the author pushed.
- `pull-requests/*/merge` is the merge result with the target branch: it builds the
  integrated state, but exists only while the PR is mergeable, so conflicted PRs get no job.

Apply it with the shipped snippet (`clone` never adds it on its own):

```
clone <TEMPLATE> <NAME> --folder <FOLDER> --remote <REMOTE> \
      --trait-xml ClaudeCode/skills/jenkins-job/templates/discover-pr-refs.xml
```

`templates/discover-pr-refs.xml` is appended to the Git source's `<traits>`; copy it and
change the `ref` to `pull-requests/*/merge` for the merge variant.

Status: XML shape follows git-plugin convention, not yet observed on a live job: after
the first apply, open the job's configure page and confirm the behaviour shows with the
expected values, and check that a PR appears as a PR-<id> branch job after indexing.

Option B, the Bitbucket (cloudbees-bitbucket-branch-source) source, discovers PRs natively
but was not chosen for now: it needs a replaced `<sources>` block and a username/token
credential instead of the SSH key.

## Errors and exit codes

Every failure is one line on stderr, never a traceback. Text that came from the server is
untrusted: control characters are stripped and it is truncated.

- `0` the command did what it said, dry runs included.
- `1` the server answered and the answer is bad news (401/403/404, a failed POST, a job
  created but not finished).
- `2` bad invocation or configuration, an unreachable host, or a refused write.

The script sends no request to another host: redirects are never followed, and a
cross-origin redirect is reported, not obeyed. Responses over 4 MiB are refused.

## Not verified

- `clone --apply`, `set-config --apply` and `scan --apply` have been exercised only
  against the test suite's fake Jenkins (`tests/test_jenkins_job_cli.py`), not a live
  server. `whoami` and `get-config` shapes follow the standard Jenkins endpoints.
- Whether a freshly copied multibranch job starts an indexing run before the patched
  config is posted (it would then index the source's remote) has not been observed.
- `--cron-spec` sets the `<spec>` of the webhook trigger element; that this produces
  periodic indexing has not been verified.
