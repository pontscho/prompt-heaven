---
name: jira
description: >-
  Read from and write to Jira from the terminal via the bundled stdlib-only Python CLI at
  scripts/jira.py — search by JQL, read an issue with its comments and changelog, list a
  project's fields, then add a comment, move an issue through a transition, log work, or
  upload a file as an attachment.
  Works against BOTH Jira Cloud and Jira Server/Data Center; the deployment is detected at
  runtime and the two search APIs are hidden behind one iterator. Auth is a Personal Access
  Token (Data Center) or an email plus API token (Cloud), always from the environment, never
  a flag value baked into a command. Use whenever a task mentions a Jira issue key such as
  STR-1234, asks what a ticket says, needs a JQL query run, or needs a comment, status change,
  worklog or attachment written back. Deliberately does NOT create, delete or field-edit
  issues.
---

# Jira

A thin, auditable Jira client: one Python file, standard library only, no MCP server and no
dependency tree. It covers reading and the four writes that are safe to hand an agent —
comment, transition, worklog, attach.

## Quick start

The script is invoked by its **absolute deployed path**; there is no `$ARGUMENTS`
substitution in this repo. `Bash(python3:*)` is already permitted, so none of these needs a
new permission grant.

```bash
J=~/.claude/skills/p/skills/jira/scripts/jira.py

python3 $J whoami                                    # liveness + auth probe — ALWAYS start here
python3 $J get STR-1234                              # issue detail
python3 $J get STR-1234 --comments --changelog       # with discussion and history
python3 $J search 'project = STR AND sprint in openSprints() AND assignee = currentUser()'
python3 $J transitions STR-1234                      # what moves are legal right now
python3 $J comment STR-1234 'Reproduced on 8.14.2.'
python3 $J transition STR-1234 'In Progress'
python3 $J worklog STR-1234 '2h 30m' --comment 'Root-caused the reconnect stall.'
```

**Every command writes Markdown to stdout** — headings and GitHub-flavoured tables, the same
shape this project's MCP servers reply in — so a result can be pasted into a document or a
comment unchanged. The one-line count goes to stderr instead, which keeps a pipeline's stdout
clean. Add `--json` for the machine-readable form, and `--dry-run` to any **write** to print
the exact method, URL and body while sending nothing.

## Setup

Credentials come from the environment. Every one has a matching flag, but **prefer the
environment**: a flag value lands in shell history and in the transcript.

| Variable | Required | Flag | Meaning |
|---|---|---|---|
| `JIRA_URL` | yes | `--url` | Base URL. May carry a context path (`https://jira.example.com/jira`) — that is handled. |
| `JIRA_TOKEN` | yes | `--token` | Data Center: a Personal Access Token. Cloud: an API token. |
| `JIRA_EMAIL` | Cloud only | `--email` | Atlassian account email. **Its presence is what selects Cloud Basic auth; its absence selects Data Center Bearer auth.** |
| `JIRA_READ_ONLY` | no | — | Truthy (`1`/`true`/`yes`) makes every write refuse with exit 2. Set it when handing the script to an autonomous agent. |

Missing configuration is refused before any network call, naming each missing variable and
its flag, with exit code 2.

`--timeout SECONDS` (default 30) is available on every command.

Data Center tokens are minted at *Profile → Personal Access Tokens* in the Jira web UI. Cloud
tokens come from <https://id.atlassian.com/manage/api-tokens>; note that Cloud tokens created
after December 2024 **expire within a year by default**, so a script that worked for months
can start failing with no code change.

**There is no option to disable TLS verification, and that is deliberate.** If an internal
Jira uses a certificate from a private CA, point the standard OpenSSL variables at that CA
(`SSL_CERT_FILE=/path/to/ca.pem`, or `SSL_CERT_DIR`) — `ssl.create_default_context()` honours
both. Turning verification off would trade a one-line configuration fix for a permanently
interceptable channel carrying a Personal Access Token.

## Commands

### Reading

| Command | What it does |
|---|---|
| `whoami` | `GET /myself`. Prints the deployment type, resolved base URL, auth mode, where each config value came from, and the authenticated user. The sanctioned way to answer "is this configured correctly". |
| `search <JQL>` | Runs a JQL query and streams the matches. `--fields`, `--limit N` (default 50, counted across pages), `--fail-empty` (exit 1 when nothing matched — for use as a gate), `--json`. |
| `get <KEY>` | Issue detail. `--fields`, `--comments`, `--changelog`, `--json`. |
| `transitions <KEY>` | The transitions legal from the issue's **current** status, with their numeric IDs. |
| `projects` | Every project you can see. |
| `fields` | Every field, with its ID and whether it is custom. `--grep TEXT` filters by name — this is how you map a display name onto its `customfield_NNNNN` ID. |

`search` and `get` carry **separate** default field lists, and that separation is deliberate.
`search` asks for a compact set (`summary,status,assignee,reporter,issuetype,priority,updated`)
because a description fetched for five hundred rows multiplies the payload for a column no
table renders. `get` asks for that set plus `description`, `components`, `labels`,
`resolution`, `created` and `fixVersions`. Neither defaults to whatever the server would
choose, and that part is not an optimisation: Data Center's search defaults to `*navigable`
while its get-issue defaults to `*all`, so naming the fields is the only behaviour that means
the same thing on both deployments. `--fields '*all'` opts out of either list.

### Writing

Each write honours `JIRA_READ_ONLY` and accepts `--dry-run`.

| Command | What it does |
|---|---|
| `comment <KEY> <TEXT>` | Adds a comment. `TEXT` may be `-`, meaning read the body from stdin. |
| `transition <KEY> <ID-OR-NAME>` | Moves the issue. A name is resolved to its numeric ID by reading the transition list first; if the name is unknown or ambiguous the legal transitions are printed and the command exits 1. `--comment TEXT` attaches a comment to the transition. |
| `worklog <KEY> <TIMESPENT>` | Logs work, e.g. `'3h 20m'`. `--comment TEXT`, `--started ISO`. |
| `attach <KEY> <FILE>` | Uploads a file as an attachment. `--name NAME` overrides the displayed filename; it is reduced to a basename either way, so a path cannot be smuggled into it. |

**Out of scope on purpose**: creating issues, deleting anything, and editing arbitrary
fields. Those need `editmeta` round-trips and per-field value shapes that differ between
deployments, and they are the operations where a mis-prompted agent does damage that is
tedious to undo. Use the web UI, or ask for them to be added deliberately.

## Reading the errors

Five Jira behaviours mislead people who have not met them before. The script names each one
in its error output rather than passing the raw status through.

- **A 404 does not mean "does not exist."** Jira returns 404 both for a missing issue and for
  an issue you lack permission to see — in Atlassian's own wording. The message says so.
  Before concluding a ticket was deleted, check whether the token's account has *Browse
  Projects* on that project.
- **Bad credentials often produce a 200, not a 401.** Jira sends no `WWW-Authenticate`
  challenge and permits anonymous access, so a rejected token can come back as a *successful*
  response containing only public data. The script checks the `X-AUSERNAME` response header
  and treats `anonymous` as an auth failure. This is why `whoami` is the first thing to run.
- **An HTML body means you never reached the API.** Behind an SSO proxy you get a login page
  with a 200. The script branches on `Content-Type` before parsing and reports the first part
  of the body — usually the base URL's context path is wrong.
- **A 401 carrying `X-Seraph-LoginReason: AUTHENTICATION_DENIED` is a CAPTCHA lockout**, not a
  bad token. Log in through the web UI once to clear it.
- **429 and 5xx are retried; other 4xx are not.** `Retry-After` is honoured, read
  case-insensitively because Cloud and Data Center disagree on its capitalisation.

## Exit codes

`0` success · `1` a real finding — the API refused the operation, or a transition name did not
resolve · `2` bad invocation, missing configuration, a malformed issue key, an unreachable
host, or a write attempted under `JIRA_READ_ONLY`.

## Notes

**Both deployments, one interface.** Only search differs materially: Cloud uses
`POST /rest/api/2/search/jql` with opaque `nextPageToken` paging and returns no total, while
Data Center uses `POST /rest/api/2/search` with `startAt`/`maxResults`/`total`. The script
probes `/serverInfo` once per run — and only when a search is actually requested — then hides
the split behind a single iterator. There is deliberately no cached total in the output,
because Cloud does not return one and Data Center documents its own as optional.

**Why API v2 on Cloud too.** v3's only addition over v2 is the Atlassian Document Format. v2
exists on Cloud with the identical operation set and takes plain strings, so targeting v2
everywhere removes ADF serialisation and flattening from the client entirely. If rich
formatting in a written comment ever matters, that is the moment to revisit — not before.

**Custom fields are per-instance.** Agile fields such as Sprint, Epic Link and Story Points
are ordinary custom fields whose numeric IDs differ between Jira instances, and whose display
names can be renamed. Never hard-code an ID from another project: run `fields --grep sprint`
against the instance you are talking to. Note also that a Sprint value read from a plain
issue fetch comes back as an unparseable Java `toString` blob with the real name embedded as
`name=...`; that is a Jira quirk, not a bug in the client.

**Relationship to other skills.** This skill is the *access layer* — primitives, one call
each. A multi-step workflow that happens to touch Jira (bootstrap a ticket, brainstorm, open
a branch, attach a document) is a separate skill that calls these commands; it does not
belong here.

**On the CSRF header.** `attach` is the only command sending `multipart/form-data`, and Jira
blocks every such request that arrives without `X-Atlassian-Token: no-check` — on both
deployments, and without saying that is why. The header is set automatically; it is
documented here only because a hand-rolled `curl` upload that omits it fails in a way that
reads like an authentication problem.

**Do not reach for the Atlassian Rovo MCP server as a substitute.** It is Cloud-only and
cannot see a Data Center instance at all.
