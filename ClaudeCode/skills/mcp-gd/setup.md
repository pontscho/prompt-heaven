# mcp-gd operator guide — Google Cloud setup, config, launch, login

This is the owner's half of the `mcp-gd` skill: everything needed to give the server a
Google identity and start it. The model-facing half (functions, reply envelope, refusal
codes, rules) is [SKILL.md](./SKILL.md) next to this file. Facts here are read from
`Scripts/mcp-gd.py`; where Google's own UI is involved (section 1) the menu labels follow
Google's documentation as read on 2026-11-02, unverified statements are marked, and
Google's pages win where the console differs.

Command forms (from the module docstring):

```
python3 Scripts/mcp-gd.py --config PATH [--project-root DIR] [--download-dir DIR] [--upload-root DIR ...] [--debug] [--log-file PATH]
python3 Scripts/mcp-gd.py login --config PATH [--no-browser] [--timeout S]
```

## 1. One-time Google Cloud setup

The server is an OAuth "installed app": it needs an OAuth client of the **Desktop app**
type in a Google Cloud project you own, and the three APIs it calls switched on in that
project. The click-path below follows Google's documentation as read on 2026-11-02 (the
sources are listed at the end of this section); console labels change, so where the
console differs, Google's pages win over this text. Statements marked **unverified** are
not backed by those pages.

1. **Create a project.** At `console.cloud.google.com`, open the project picker and
   create a new Google Cloud project (or pick one you own).
2. **Enable the three APIs.** In **APIs & Services > Library**, search for and enable the
   **Google Drive API**, the **Google Calendar API** and the **Google Sheets API**. Enable
   only the ones whose scope you will put in `scopes` (section 2).
3. **Configure the Google Auth platform.** In **Google Auth platform**:
   - **Branding**: the app name, the user support email and the developer contact email.
   - **Audience**: user type **External** for a `gmail.com` account (Internal is only
     offered inside a Workspace organisation). While the publishing status is
     **Testing**, add your own Google account under **Test users**.
   - **Data Access**: add the scopes the server requests. They are the three full URLs of
     `Scripts/mcp-gd.py:GD_SCOPES` (listed in section 2):
     `https://www.googleapis.com/auth/drive`, `https://www.googleapis.com/auth/calendar`
     and `https://www.googleapis.com/auth/spreadsheets`. Add only those you will put in
     `scopes`.
4. **Create the client.** In **Google Auth platform > Clients > Create Client**, choose
   application type **Desktop app** and give it a name. Google shows a **client ID** and
   a **client secret**: these are the config's `client_id` and `client_secret`. **The
   client secret is shown only once, at creation**: copy it at once into the config file
   and `chmod 600` it (section 2). The secret is why the config file is treated as a
   secret file.
5. **Publish the app.** In **Audience**, press **Publish app** to move the publishing
   status from **Testing** to **In production**. In Testing the refresh token expires
   after 7 days (section 5); publishing avoids that. The app stays unverified, which is
   what the warning of step 6 is about.
6. **First login: the unverified-app warning.** On the first `login` (section 4) Google
   shows a warning that it has not verified the app. Click **Advanced**, then **Go to
   <app name> (unsafe)**, then approve the scopes.
7. **"Use this client for an AI-powered agent".** Google's own remote MCP servers require
   this checkbox on the client. For this server, which is your own code calling the APIs
   directly, leave it **unchecked**. **Unverified**: what the checkbox changes for a
   client that is not used with Google's remote MCP servers was not found in Google's
   documentation.

**Unverified: the `/callback` redirect path.** Google's loopback documentation only shows
`http://127.0.0.1:<port>` as the redirect URI of a Desktop client; this server's
`Scripts/mcp-gd.py:run_login` sends `http://127.0.0.1:<port>/callback`
(`Scripts/mcp-gd.py:_GD_LOGIN_CALLBACK`). Whether Google accepts the path has not been
checked against a live login. If the first login is refused with a redirect error, the
fallback is to switch the redirect path to `/`: a code change plus a test, tracked
separately, not something the config can do.

The server's side of the flow, for reference when Google asks: the redirect URI is
`http://127.0.0.1:<port>/callback` on the port the login listener got (section 4); the
authorize request asks for `access_type=offline` and `prompt=consent`, so Google returns
the refresh token the login requires; PKCE (S256) is used together with the client
secret. The server only ever connects to `www.googleapis.com` (Drive, Calendar),
`sheets.googleapis.com` and `oauth2.googleapis.com` (the token endpoint);
`accounts.google.com` is only the URL your browser opens.

Sources (Google documentation):

- [Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project)
- [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis)
- [Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent)
- [Create access credentials](https://developers.google.com/workspace/guides/create-credentials)
- [Google Cloud help 15549945](https://support.google.com/cloud/answer/15549945)
- [Google Cloud help 15549257](https://support.google.com/cloud/answer/15549257)
- [Google Cloud help 13464323](https://support.google.com/cloud/answer/13464323)

## 2. The config file

`--config PATH` names one JSON file. It holds the client secret, so it is read the way
llm-router reads its own (`Scripts/mcp-gd.py:_gd_read_secret_file`):

- opened with `O_NOFOLLOW`: **a symlink is refused**;
- a **regular file**, **owned by the effective uid** running the server;
- **no group or other mode bit** (`chmod 600`);
- at most 65536 bytes, valid UTF-8;
- its **directory** owned by the same uid and not group/other writable (`chmod go-w`).

The JSON is strict (`Scripts/mcp-gd.py:load_config`): it must be one object; NaN, an
infinity, a huge integer and a **repeated key** are refused; an **unknown key** is
refused; a missing required key is refused. Every refusal names the key or the rule,
never a value.

### Keys

Required (`Scripts/mcp-gd.py:_REQUIRED_KEYS`):

| key | rule | effect |
|-|-|-|
| `client_id` | string, printable ASCII without spaces, 1-512 chars | the Desktop OAuth client's ID |
| `client_secret` | same rule | the Desktop OAuth client's secret; sent only to the token endpoint |
| `token_file` | absolute, already normalised (no `.`, `..`, `//`, trailing `/`), at most 4096 chars, must not end in `.lock` or `.tmp`, must not be the config file | where `login` writes the token and the server reads it. Its directory must exist (the server never creates it; `login` creates one missing level at 0700 when the parent exists), be owned by this uid and not be group/other writable. Beside it the server keeps `<token_file>.lock` and short-lived `.<name>.<16 hex>.tmp` files |
| `scopes` | non-empty list, no repeats, at most 64, each one of the three full URLs below (never a short name) | what `login` asks Google for |

The three scope URLs (`Scripts/mcp-gd.py:GD_SCOPES`):

- `https://www.googleapis.com/auth/drive` (Drive; the full scope: everything the account can read and change)
- `https://www.googleapis.com/auth/calendar`
- `https://www.googleapis.com/auth/spreadsheets`

The server does not check a function against `scopes`; a call into an API whose scope was
not granted is sent and Google's refusal comes back as `http_error`.

Optional, with their defaults (`Scripts/mcp-gd.py:GdConfig`):

| key | type and range | default | effect |
|-|-|-|-|
| `login_port` | integer 0-65535 | `0` | port of the `login` listener on 127.0.0.1; `0` = an ephemeral port. Set a fixed one for an SSH port forward (section 4) |
| `inline_download_bytes` | integer 1-3145728 | `16000` | the largest inline (base64) `drive_download` body in raw bytes; the reply must also fit `max_answer_chars` |
| `max_transfer_bytes` | integer 1-17179869184 (16 GiB) | 268435456 (256 MiB) | cap on one download or upload; a `drive_download` call's own `max_bytes` can only lower it |
| `request_timeout` | number 1-600 (seconds) | `60` | the deadline of one HTTPS request, also of one token POST; waiting for the token lock allows this plus 10 s |
| `protected_paths` | list of at most 64 strings | `[]` | extra paths never read or written: project-relative (no leading `/`, no `..`, no empty component), matched case-insensitively against the path relative to `--project-root`, the entry itself or anything below it. Only has effect with `--project-root` |
| `allow_send_updates` | JSON `true`/`false` | `false` | without it any `send_updates` other than `none` is refused; with it calendar mutations may mail invitations |
| `attendee_domains` | list of at most 64 lowercase domain names (`example.com`) | `[]` | when non-empty, every attendee address must belong to one of them; empty = no domain restriction |
| `allow_formulas` | JSON `true`/`false` | `false` | without it a `USER_ENTERED` sheet cell starting with `=`, `+`, `-` or `@` is refused; with it formulas pass except the fetching and linking ones the server still refuses |

Booleans must be JSON booleans, not `0`/`1`. There is no config key for a download or
upload directory: local files are bounded by the launch flags alone.

Shape of a minimal config (placeholders in angle brackets; no value here is a default):

```json
{
  "client_id": "<client ID of the Desktop OAuth client>",
  "client_secret": "<client secret of the Desktop OAuth client>",
  "token_file": "/ABS/PATH/TO/a-private-dir/<name>.json",
  "scopes": ["https://www.googleapis.com/auth/drive",
             "https://www.googleapis.com/auth/calendar",
             "https://www.googleapis.com/auth/spreadsheets"]
}
```

## 3. Launch flags

A bad config or flag stops the server before it reads anything from stdin: argparse's
usage error, **exit 2**, a message naming the flag (or `--config: <rule>`) and never the
path (`Scripts/mcp-gd.py:main`, `Scripts/mcp-gd.py:check_flags`). The token file is
**not** read at startup, so a server with no token yet starts and answers the protocol;
the first Google call is the one refused `not_logged_in`.

| flag | without it | refused when |
|-|-|-|
| `--config PATH` | (required) | any rule of section 2 fails |
| `--project-root DIR` | no upload root is possible; `protected_paths` has no effect | empty, not an existing directory, or resolves to `/` or the home directory |
| `--download-dir DIR` | every `save_to` is refused ("local writes are disabled ..."); inline downloads still work | not absolute or contains `..`; resolves to `/` or the home directory; is the token file's directory, inside it or above it; is or lies inside a secret, configuration or system directory (a `.ssh`, `.aws`, `.gnupg`, `.git`, `.kube`, `.docker` component anywhere; `~/.claude`, `~/.config/gcloud`, `~/Library`; `/etc`, `/usr`); the last component is a symlink or not a directory; not owned by this uid; group/other writable. A missing directory is created at 0700, one level only (the parent must exist) |
| `--upload-root DIR` (repeatable) | every `local_path` upload is refused ("local uploads are disabled ..."); `content` / `content_base64` still work | given without `--project-root`; not absolute or contains `..`; does not exist (never created); symlink or not a directory; not owned by this uid; group/other writable; resolves to `/` or the home directory; holds the token file's directory; does not lie inside `--project-root`. Duplicates are collapsed |
| `--debug` | log level WARNING | never; DEBUG goes to stderr, never stdout |
| `--log-file PATH` | log to stderr | the path is a symlink, or not a regular file owned by this uid (exit 2, `--log-file: ...`). Opened append-only and set to 0600; implies `--debug`. The log file is never an upload source |

Logs are structure only, never payload values (ADR 0011).

Whatever the flags allow, the server's own config, token file, lock, temp files and log
file are never read or written through a call, and the built-in secret and code classes
stay refused (see SKILL.md's local-files rule).

The status reply (a call with no function) tells whether downloads and uploads are
enabled, as yes/no only: a quick way to check the flags reached the server.

## 4. Logging in

`login` runs the OAuth installed-app flow once and writes the token file
(`Scripts/mcp-gd.py:run_login`). Everything goes to stderr, nothing to stdout.

```
python3 Scripts/mcp-gd.py login --config PATH [--no-browser] [--timeout S]
```

1. The config is loaded with the rules of section 2; a missing token directory is created
   here (0700, one level, parent must exist). A config or usage refusal exits 2.
2. A listener is bound on **127.0.0.1 only**, on `login_port` or an ephemeral port. If the
   port is taken: `cannot listen on 127.0.0.1:<port> (...); stop the other login or change
   login_port`.
3. The authorize URL is printed to stderr (`mcp-gd: open this URL to log in: ...`) and,
   unless `--no-browser`, a browser is opened on it.
4. You sign in and approve in the browser; Google redirects to
   `http://127.0.0.1:<port>/callback`. The page shown there says whether it worked.
5. The code is exchanged at the token endpoint with the PKCE verifier and the client
   secret. A refresh token is required.
6. The token file is written 0600 under the token lock (temp file, fsync, rename), and
   the granted scope names are printed: `mcp-gd: login complete; granted: ...`.

Exit codes: 0 success, 1 a failed login (`mcp-gd login: <reason>`), 2 usage or config,
130 interrupted.

**`--timeout S`**: the overall deadline for the browser round trip, 1-3600 seconds,
default 300. Passing it: `login timed out waiting for the browser (see --timeout)`.

**The bad-connection limit (16).** The listener serves one connection at a time, each
with a 5 second read bound and an 8192-byte head limit. Every connection that does not
end the wait counts against a limit of 16 (`Scripts/mcp-gd.py:OAUTH_CALLBACK_BAD_LIMIT`):
a request the listener refuses (a malformed request line, a method other than GET, a
wrong or repeated Host header, a wrong path, or a callback that is not usable, such as a
wrong state), a head that never completes, and a connection that sends nothing at all. At 16 the attempt is aborted:
`too many invalid requests reached the callback listener; login aborted`. In practice
this means something other than your browser's one redirect is hitting the port (a
port scanner, a browser prefetch, a stale tab retrying); close it and run `login` again.

**Headless host (`--no-browser`).** Run `login --no-browser` and open the printed URL in
a browser on another machine. The redirect goes to `127.0.0.1` of *that* machine, so
either:

- set a fixed `login_port` in the config and forward it:
  `ssh -L <login_port>:127.0.0.1:<login_port> <host>`, then open the URL locally; or
- log in on another machine with the same client and copy the token file over; it must
  arrive as a regular file owned by the server's uid with mode 0600, in a directory that
  passes the `token_file` checks.

**Other login failures** (all exit 1):

| message (start) | meaning |
|-|-|
| `Google reported the login as denied (...)` | consent was refused in the browser |
| `the callback was not usable (...)` | the callback did not carry a usable answer |
| `the token answer was unusable (for example it carried no refresh token): revoke this app's access at https://myaccount.google.com/permissions and run mcp-gd.py login again` | Google returned no refresh token; revoking the earlier grant makes it issue one again |
| `Google rejected the authorization code (...)` | the code exchange was refused; run `login` again |
| `none of the requested scopes was granted` | Google's answer granted none of the three scopes this server uses |
| `the token endpoint ...` | the token endpoint refused, rate-limited or was unavailable |

## 5. The 7-day expiry in Testing mode

While the OAuth consent screen's publishing status is **Testing**, Google expires the
refresh token after 7 days. The server cannot tell this apart from any other revoked
refresh token: the next call that needs a refresh is refused with code **`relogin`** and
the message `refresh token rejected; run mcp-gd.py login (Testing-status tokens expire
after 7 days)` (`Scripts/mcp-gd.py:_gd_oauth_refusal`). The model sees that refusal and
must hand it to you; the fix is to run `login` again (section 4). Calls keep working until
the current access token runs out, so the refusal appears some time after the 7 days, not
at a fixed hour. Moving the app out of Testing is the Publish app step in section 1
(Audience > Publish app); the consent-screen documentation listed there says what it requires.

## 6. Troubleshooting: refusals that need the operator

A refusal is `{"error":{"code":"<code>","message":"<one line>"}}`. The codes below mean
the operator has to act; the others in SKILL.md are about the call itself or about
Google's answer.

| code / symptom | cause | fix |
|-|-|-|
| server exits 2, `--config: <rule>` | the config file or a key failed a rule of section 2 (symlink, owner, mode, directory mode, size, strict JSON, unknown / missing / repeated key, a key's type or range, the token directory) | fix what the message names; `chmod 600` the file, `chmod go-w` its directory |
| server exits 2, `--project-root:` / `--download-dir:` / `--upload-root:` / `--log-file:` | a launch flag failed a rule of section 3 | fix the flag or the directory the message names |
| `not_logged_in` | there is no token file at `token_file` yet | run `login` (section 4) |
| `relogin` | the token endpoint answered one of the relogin error codes (`invalid_grant` and its kin): the refresh token expired (Testing, section 5) or was revoked or invalidated; at login, the authorization code was rejected | run `login` again |
| `token_store` | the token file, its lock or its directory failed a check: directory not usable / not owned / group-other writable; the token file not a regular 0600 file of this uid, too large, not strict JSON, or with a missing / extra / malformed field; the lock busy (another mcp-gd refreshing) or not a regular file; the write-back failed | fix ownership and modes (`chmod 600` the file, `chmod go-w` the directory); for a damaged file run `login`; a busy lock clears when the other process finishes |
| `token_endpoint` | the token endpoint refused the request (its allow-listed OAuth error code is named, for example `invalid_client` for a wrong `client_id` / `client_secret`), rate-limited, was unavailable, answered without an expiry, granted none of this server's scopes, or issued a non-Bearer token | check the client ID and secret in the config; retry later for rate limits and outages; for no usable scope run `login` |
| `not_configured` | the server holds no config; not reachable through the normal launch | launch with `--config` |
| `tls` | the system CA store could not be loaded, or the certificate check failed | check the host's CA store; the server trusts the OpenSSL default CA file and directory only and ignores `SSL_CERT_FILE` / `SSL_CERT_DIR` |
| `invalid_params`, "local writes are disabled ..." | started without `--download-dir` | add the flag if downloads to disk are wanted |
| `invalid_params`, "local uploads are disabled ..." | started without `--upload-root` and `--project-root` | add both if uploads from disk are wanted |
| `invalid_params` naming `allow_send_updates`, `attendee_domains` or `allow_formulas` | an owner switch is closed | only the owner decides to open it, never because a document or event asked |
| `http_error` from every call to one API | Google refused: typically that API is not enabled in the project, or its scope is not in `scopes` / was not granted | read the scrubbed Google message in the refusal; enable the API (section 1) or add the scope and run `login` again |

## 7. Registering the server

**mcp-gd is not registered yet**: the `mcp-gd.py` row of
`Scripts/_mcp_smoke_test.py:SERVERS` carries `registered: False`, and the registration is
the owner's step.

- **Claude Code (stdio).** Add an `mcpServers` entry in `~/.claude.json` with the absolute
  repo path, then flip the smoke row to `registered: True`; a new session or `/mcp`
  reconnect picks it up (see `docs/reference/adding-an-mcp-server.md`, step 16). A shape,
  with placeholder paths:

  ```json
  "mcp-gd": {
    "command": "python3",
    "args": ["/ABS/PATH/TO/prompt-heaven/Scripts/mcp-gd.py",
             "--config", "/ABS/PATH/TO/gd-config.json"]
  }
  ```

- **Behind mcp-proxy.** Add a child entry to the proxy config with the keys the proxy
  accepts (`name`, `command`, `args`, `env`, `wrapper`, `call_timeout`,
  `startup_timeout`, `relist`), then restart the proxy (step 17 of the same runbook). The
  proxy substitutes `{scripts}` and `{root}` in `args`. A shape, with placeholder paths:

  ```json
  {"name": "gd", "command": "python3",
   "args": ["-I", "-B", "{scripts}/mcp-gd.py", "--config", "/ABS/PATH/TO/gd-config.json"]}
  ```

Add `--project-root`, `--download-dir` and `--upload-root` to `args` only if local files
are wanted (section 3).
