---
name: bitbucket
description: >-
  Read and write pull requests on a Bitbucket Server / Data Center instance from the
  terminal via the bundled stdlib-only Python CLI at scripts/bitbucket.py — list pull
  requests, read one with its reviewers and activity feed, ask whether it could merge,
  then open one, comment on it, set a review status, decline it, reopen it, or merge it.
  Server/Data Center ONLY: Bitbucket Cloud is a different product with a different REST
  API and is refused at the door rather than half-supported. Auth is a personal HTTP
  access token sent as a bearer token, always from the environment, never a flag value
  baked into a command. Use whenever a task needs a pull request opened or inspected on
  a self-hosted Bitbucket, asks what reviewers said on a PR, needs a PR's merge
  blockers explained, or mentions a Bitbucket PR id. Nothing project-specific lives in
  the script: pr-create reads its defaults from a `.claude/bitbucket.json` profile found
  by walking up from the working directory. Merging is deliberately gated behind four
  separate refusals. Deliberately does NOT delete anything, does not edit a pull
  request's metadata, and does not post inline diff comments.
---

# Bitbucket

Pull requests on a self-hosted Bitbucket, from the terminal. One script, stdlib only,
no third-party HTTP client. The read side is unrestricted; the write side is small on
purpose and the merge is gated.

## Quick start

```bash
export BITBUCKET_URL=https://bitbucket.example.com
export BITBUCKET_TOKEN=...            # profile → HTTP access tokens

python3 -B scripts/bitbucket.py version
python3 -B scripts/bitbucket.py whoami

python3 -B scripts/bitbucket.py pr-list --state OPEN
python3 -B scripts/bitbucket.py pr-get 1211
python3 -B scripts/bitbucket.py pr-activities 1211 --action COMMENTED
python3 -B scripts/bitbucket.py pr-mergeability 1211

python3 -B scripts/bitbucket.py pr-create \
	--from bugfix/STR-13654-nawl-connection-id-header \
	--title "STR-13654 fix(nawl): read the Connection-Id header from the correct offset" \
	--description-file - < body.md --dry-run

python3 -B scripts/bitbucket.py pr-merge 1211 --yes
```

## Setup

Both variables are required and both are read from the environment. The flags exist for
one-off overrides; putting a token on a command line writes it into your shell history,
so prefer the environment.

| Variable | Required | Flag | Meaning |
| --- | --- | --- | --- |
| `BITBUCKET_URL` | yes | `--url` | Base URL. A context path such as `/bitbucket` is preserved. |
| `BITBUCKET_TOKEN` | yes | `--token` | Personal HTTP access token, sent as `Authorization: Bearer`. |
| `BITBUCKET_PROJECT` | no | `--project` | Default project key. |
| `BITBUCKET_REPO` | no | `--repo` | Default repository slug. |
| `BITBUCKET_PROFILE` | no | `--profile` | Explicit profile path, overriding the walk up. |
| `bitbucket_profile` | no | `--profile` | The same input, lowercase, read second. Environment names are case-sensitive, so this is a real second way to point the profile loader at an arbitrary path. Accepting both spellings is this fleet's pattern; it is documented rather than removed. |
| `BITBUCKET_READ_ONLY` | no | — | `1`/`true`/`yes` refuses every write subcommand. |

A repository-scoped or project-scoped token works for reads but cannot open or merge a
pull request. `pr-merge` in particular needs repository write.

## Commands

### Reading

| Command | What it does |
| --- | --- |
| `version` | The instance version banner, and the cheapest proof the URL points at a Bitbucket. |
| `whoami` | Which user the token authenticates as, read off the `X-AUSERNAME` header. |
| `repo` | Repository metadata, including the numeric id other endpoints demand. |
| `pr-list` | Pull requests, filtered by state, branch, direction or author. |
| `pr-get` | One pull request in full: state, version, reviewers, description. |
| `pr-activities` | The activity feed — comments, approvals, rescopes — optionally one action only. |
| `pr-mergeability` | Would it merge, and which checks veto it. Does not write. |
| `pr-builds` | Build statuses reported against the pull request's source tip, with one verdict. |
| `pr-reviewers` | Which reviewers the repository would auto-assign for a branch pair. |

### Writing

| Command | What it does |
| --- | --- |
| `pr-create` | Open a pull request from the profile's defaults plus the flags given. |
| `pr-comment` | Add a general comment, or a reply with `--parent`. |
| `pr-approve` | Set this token's review status: `APPROVED`, `NEEDS_WORK` or `UNAPPROVED`. |
| `pr-decline` | Decline, at the version the server currently holds. |
| `pr-reopen` | Reopen a declined pull request. |
| `pr-merge` | Merge, behind four separate refusals. See below. |

Every write subcommand takes `--dry-run`, which prints the method, URL and JSON body
that would be sent and exits 0 having sent nothing.

## The merge gate

`pr-merge` is the only call here that no later call can undo, so four refusals stand in
front of it and each one is a distinct failure mode rather than a single confirmation:

1. `BITBUCKET_READ_ONLY` — checked in `main()` before the client is constructed, so it
   fires without a single request leaving the machine.
2. `--yes` — absent by default, and checked before the pre-flight, so a merge you did
   not ask for reads nothing either.
3. The mergeability pre-flight — `GET .../merge` must answer `outcome: CLEAN`, no
   vetoes and not conflicted. Anything else refuses unless `--ignore-vetoes` is given
   as well, and the refusal quotes each veto's summary.
4. The version assertion — the pull request is re-read immediately before the write and
   the server's current version is used. `--version` does not supply a value; it asks
   the script to refuse when what you remember disagrees with what the server holds.

## What has and has not been verified

This matters more than usual here, so it is a section rather than a footnote.

Verified 2026-09-15 against Bitbucket 9.4.23 with a real token: `version`, `whoami`,
`repo`, `pr-list`, `pr-get`, `pr-activities`, `pr-mergeability`, `pr-builds`,
`pr-reviewers`, and `pr-create`, which opened a real pull request. Those call sites
carry a `# VERIFIED` marker with that date. `whoami` counts because it observes
nothing but the `X-AUSERNAME` header on the `version` response — a run that printed a
real username is that header, observed.

Not yet accepted by any server: `pr-comment`, `pr-approve`, `pr-decline`, `pr-reopen`
and `pr-merge`. Their bodies have been assembled against real pull-request data under
`--dry-run`, so the URL and payload are known good in shape; what is unproven is that
the endpoint accepts them. Those sites keep their `# UNVERIFIED` marker. Use
`--dry-run` first, and treat the first live run of each as the verification it has not
yet had. Do it on a pull request nobody needs: for `pr-merge` the verification and the
consequence are the same event, so there is no order in which you learn the endpoint
accepts the body before it has already merged the branch.

Two defects surfaced on that first live run, both worth knowing because they are the
shape of mistake this kind of script makes:

- The default-reviewers endpoint answers with a **flat array of user objects**, not the
  array of conditions its own published reference describes. A client that trusted the
  document reported "1 condition, 0 reviewers" — a silent zero, indistinguishable from
  a repository with no default reviewers configured. Only a live call could have caught
  this.
- `--description-file` was routed through the helper that takes text rather than the one
  that takes a path, so it would have published the path string as the pull request's
  description. A dry run caught this one, which is the argument for always running it.

## Profiles

Nothing project-specific lives in the script. `pr-create` reads its defaults from the
first `.claude/bitbucket.json` found by walking up from the working directory, so the
same command means different things in different repositories, deliberately.

```json
{
	"project": "SL",
	"repo": "ngs-media-server",

	"projects": {
		"SL": {
			"target": "master",
			"reviewers": ["@default"],
			"require": ["target"]
		}
	}
}
```

- `target` is the default branch a pull request opens against. A short name is expanded
  to `refs/heads/...` — the create endpoint rejects a short name with a 400 whose
  message never mentions refs.
- `reviewers` accepts usernames, and the sentinel `@default`, which asks the server
  which reviewers this specific branch pair would be given. It cannot be expanded
  earlier than that, because the answer depends on the branch pair.
- `require` is enforced locally, before any network call, and exits 2 when something it
  names is unset. It is not the server's validation; it exists so a project can insist
  on something the server is happy to omit.

Where the walk stops, stated precisely because the boundary is what bounds the blast
radius below. Both sides are resolved with `realpath` and the walk climbs only while the
**next directory up is still inside `$HOME`** — so `$HOME` itself is checked and nothing
above it ever is. Read from the other end: **from a checkout outside `$HOME`** — a CI
workspace, `/opt/work/repo`, `/tmp` — **there is no walk at all**, and only the working
directory's own profile is read. Pass `--profile` or set `BITBUCKET_PROFILE` there.

This used to be a single `==` between two paths normalised with different strength
(`os.getcwd()` returns the resolved physical path, `$HOME` is whatever the variable
literally says), which bound on neither side — not from outside `$HOME`, where it could
never match, and not from inside it on any machine whose `$HOME` is a symlinked spelling,
which on macOS is the default one. It was repaired in this script and in the jira skill's
in one change, because the two copies are byte-identical on purpose and a test gates that.
One residue is left and declared: `realpath` resolves symlinks but does not canonicalise
**case**, so a case-distinct `$HOME` on a case-insensitive volume stops the walk at the
working directory — fewer files trusted, which is the safe direction, and `--profile` is
the way past it.

The blast radius is wider than `pr-create`. The profile supplies the project and the
repository for **every scoped subcommand**, so a planted profile selects the
repository that `pr-approve`, `pr-decline`, `pr-comment` and `pr-merge` act in. The
pull request id stays caller-supplied, so a planted profile does not fabricate an
operation — it re-aims the one you asked for, at a pull request of that number in a
repository you did not name. Two things bound it: `--project` and `--repo` beat the
profile, as do `BITBUCKET_PROJECT` and `BITBUCKET_REPO`; and the walk takes the
nearest profile, so one inside your own tree wins over anything further up.

## Reading the errors

- **401 does not mean what you think.** This API answers both "who are you" and "you
  may not do that" with 401. A 401 on a write is far more often a missing repository
  permission than a bad token.
- **403 is narrow here.** It means a licensed-user limit, a self-degraded permission,
  or a per-endpoint feature flag such as auto-merge being off — not the general
  "forbidden" of most REST services.
- **409 is overloaded.** A stale version, a merge conflict, a vetoed merge check, a
  pull request that is not open and an archived repository all arrive as 409. The
  server's message is the only discriminator, so it is printed unedited.
- **A 404 may be a permission.** The API does not distinguish "no such repository" from
  "you cannot see this repository".
- **`anonymous` means the token was ignored.** Every response carries `X-AUSERNAME`;
  when it reads `anonymous` the script stops rather than reporting an empty list as if
  it were an answer.

## Exit codes

`0` the command did what it said, `1` the server answered and the answer is bad news,
`2` bad invocation, bad configuration, an unreachable host, or a refused write.

## Notes

- **Server/Data Center only.** A `bitbucket.org` host is refused immediately. Cloud is a
  different product with a different resource model; branching on it inside one script
  would mean two half-tested clients instead of one working one.
- **Paging follows `nextPageStart`.** Item identifiers between pages are not guaranteed
  contiguous, so `start + size` can skip or repeat. The server's own `limit` is what a
  sweep believes, never the value that was requested.
- **Author filtering is not a first-class parameter.** `--author` is applied as the
  numbered participant filter this API uses instead. A caller expecting `author=` on the
  raw endpoint gets a silently unfiltered list.
- **`pr-approve` uses the participant endpoint.** `POST .../approve` has been deprecated
  since Bitbucket 4.2, and its replacement then deprecated its own `version` query
  parameter in favour of `lastReviewedCommit`. Two deprecation layers on one feature is
  why older recipes for this go wrong.
- **Only un-anchored comments.** An inline comment needs a diff anchor with both commit
  hashes, a line number and a line type; assembling one from CLI flags is how a comment
  lands on the wrong line of the wrong file.
- **Default reviewers live under a different REST root.** `/rest/default-reviewers/1.0`,
  not `/rest/api/1.0`. Assuming otherwise earns a 404 that reads like a missing repo.
- **Build status lives under a third root**, `/rest/build-status/1.0/commits/{sha}`. The
  `/rest/api/1.0/.../commits/{sha}/builds` subresource exists on 9.4.23 but demands a
  `key` — it fetches one named build rather than listing them.
- **`pr-builds` is keyed by commit, not by pull request.** Bitbucket stores a status
  against a sha; the pull request only displays whatever sits on its source tip. So the
  command reads the tip first, and a stale answer means the branch moved rather than
  that the build disappeared. It exits 1 when the verdict is `FAILED`, so it is usable
  as a gate. A mixed set reports its unhappiest member: a red build beside a green one
  is not green.
- **`pr-mergeability` is not a build check.** A vetoed merge can mean a failed build, a
  missing approval or a branch permission, and it does not distinguish them. Ask
  `pr-builds` when the question is about the build.
- **The API version segment is not the product version.** `/rest/api/1.0` has been
  stable since the Stash era, unrelated to the 9.x or 10.x number the instance reports.
