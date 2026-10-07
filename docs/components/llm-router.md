---
name: llm-router
type: component
status: active
title: llm-router — an Anthropic Messages router for Claude Code
description: The stdlib-only HTTP server between Claude Code and several kinds of LLM backend -- an Anthropic Messages front that routes each request by its exact model string to a passthrough, llama.cpp, Mistral, codex or openai adapter, the last two speaking the OpenAI Responses API through one profile table; its command line and login subcommand, its 0600 JSON config and the OAuth write-back into it, the per-kind header allow-list and llama.cpp quirk rows, reasoning across kinds, the inbound and outbound security rules, how it is tested and what it declares rather than gates.
sources:
  - Scripts/llm-router.py
  - Scripts/_mcp_oauth.py
  - tests/test_llm_router.py
  - tests/files/llm_router/README.md
verified:
  commit: 2ef3ef9
  date: 2026-10-07
links:
  - 0028-route-by-model-translate-at-the-edge
  - mcp-proxy
  - 0027-the-proxy-relays-it-never-composes
  - scripts
  - tests
  - generated-regions
  - security-review-20261007-123500
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
---

# llm-router

`Scripts/llm-router.py` is one HTTP server that Claude Code talks to instead of
Anthropic. It accepts the Anthropic Messages API on two paths, lists its routes
on `GET /v1/models`, looks the
request's `model` up in its JSON config, and forwards the call to the backend
that route names — relayed as-is (`passthrough`), with llama.cpp's deviations
repaired (`llamacpp`), translated to and from Mistral's chat-completions API
(`mistral`), or translated to and from the OpenAI Responses API, on the
ChatGPT-subscription endpoint over OAuth (`codex`) or on the platform endpoint
with an API key or a Sign-in-with-ChatGPT login (`openai`)
`Scripts/llm-router.py:KIND_CLASSES`. The OAuth backends are logged in once by
the `login` subcommand, and the router keeps their refresh tokens current in
the same config file. Every answer the client sees is
Anthropic-shaped: a JSON body, an SSE stream that always closes its envelope, or
the `{"type":"error","error":{...}}` envelope `Scripts/llm-router.py:_rt_error_body`.
It is pure Python 3.9 and the standard library
([[0024-pure-python-39-and-the-stdlib]]), and it is not an MCP server.

This page is the *shape*. Why the router routes by model and translates at the
edge, why its HTTP front is a declared copy of [[mcp-proxy]]'s rather than a
canonical source, and every alternative that lost, is frozen in
[[0028-route-by-model-translate-at-the-edge]]; where it sits among the repo's
scripts is [[scripts]].

## The shape

**One validated request, one route, one adapter.** The front reads the body
once and builds an `InboundRequest` — the model, a non-empty `messages` list of
`user` / `assistant` turns (plus text-only `system` turns after the first,
which Claude Code sends mid-conversation; each kind places them its own way,
below), `stream`, `max_tokens`, `tools` checked to the
minimum the router relies on, everything else untouched
`Scripts/llm-router.py:_rt_parse_inbound`. The route is the **exact** model
string, then the config's `default`, else a 404 `not_found_error`
`Scripts/llm-router.py:_rt_route`. The backend's kind picks one adapter
instance from `Scripts/llm-router.py:ADAPTERS`; the adapter classes, with their
header tables and route options, are `Scripts/llm-router.py:KIND_CLASSES`.

**The file is eight units, and four of them are pure.** The module docstring
lists them: errors and shared helpers, config, inbound, the SSE toolkit,
adapters, the outbound transport, the HTTP front, the entry point
`Scripts/llm-router.py`. Units 2-5 reference no socket, TLS, HTTP, `select`,
clock or thread name, so every translator is sans-IO — `begin` / `feed(line)` /
`finish` / `fail` / `ping` each return bytes `Scripts/llm-router.py:Adapter` —
and the translators are tested in-process with literal input and output.

**Three endpoints: two POST, one GET.** `/v1/messages` and
`/v1/messages/count_tokens` take POST, `/v1/models` and `/v1/models/{id}` take
GET; there are no other paths `Scripts/llm-router.py:_ENDPOINTS`
`Scripts/llm-router.py:_rt_front_endpoint`. On the messages paths the only
query strings accepted are none and `beta=true`
`Scripts/llm-router.py:_RT_ALLOWED_QUERIES`, anything else is a 404; on the
models paths the query keys `limit`, `before_id`, `after_id` and `beta` are
accepted with any value and ignored, any other key is a 400
`Scripts/llm-router.py:_RT_MODELS_QUERY_KEYS`. Every other method — `OPTIONS`
and `HEAD` included, since there is no CORS — passes the same precheck and is
then a 405 with `Allow: POST` on a messages path and `Allow: GET` on a models
path `Scripts/llm-router.py:_refuse_405`.

**The model list is the config.** `GET /v1/models` answers 200 with the
Anthropic list shape — `data`, `has_more` (always `false`), `first_id`,
`last_id` (`null` when there is no route) — holding one
`{"type": "model", "id", "display_name", "created_at"}` per route key in config
order, plus the route's advertised `max_input_tokens` and `max_tokens` and a
`context_window` equal to `max_input_tokens` (each `null` when the route sets
none); `display_name` is the route's own or else the key, and `created_at` is
the router's start time in UTC to the second. The `default` route has no name
and is never listed `Scripts/llm-router.py:_rt_models_list`.
`GET /v1/models/{id}` answers the one entry whose route key equals the
percent-decoded single path segment exactly, else a 404 `not_found_error` that
does not echo the id `Scripts/llm-router.py:_get_models`. Both pass the same
precheck as a POST, read no body — a `Content-Length` above 0 is a 400,
`Transfer-Encoding` the POST framing 411 — and never contact a backend.

**The messages paths' body.** The body must carry one `Content-Type`
whose media type is `application/json` (parameters such as `charset` are
ignored; the body is always decoded as UTF-8), framed by exactly one decimal
`Content-Length` no larger than `--body-limit`; `Transfer-Encoding` is a 411
`Scripts/llm-router.py:_post`. The body must be a JSON object with finite
numbers only — `NaN`, `Infinity` or a literal that overflows to one is a 400
`Scripts/llm-router.py:_rt_loads`, and so is an integer literal longer than
`Scripts/llm-router.py:_RT_INT_LITERAL_LIMIT` digits, refused before `int()`
can spend quadratic time on it on a Python without its own digit limit
`Scripts/llm-router.py:_rt_bounded_int` — and `max_tokens` is an integer from 1 to
`Scripts/llm-router.py:_RT_MAX_TOKENS_LIMIT` (1000000).

**Response head first, then decide.** The router writes its own head only once
the upstream status (and, for a stream, its `Content-Type`) is known: a non-2xx
upstream on a stream request is a JSON error envelope, never a 200 event stream
carrying an error, and a 2xx that is not `text/event-stream` is a 502
`Scripts/llm-router.py:_relay_stream`. A non-stream answer is read whole under
`Scripts/llm-router.py:_UPSTREAM_BODY_LIMIT` and mapped by the adapter; a 429's
`Retry-After` is relayed when it is all digits
`Scripts/llm-router.py:_relay_json`. The event-stream requirement is the
adapter's `stream_policy`: the codex and `openai`-OAuth profiles accept a 2xx
with no `Content-Type`, because codex sends none on a streamed answer
`Scripts/llm-router.py:_rt_responses_profiles`.

**One send, at most three times.** The upstream request goes out through one
`_rt_send` call inside a loop that may resend twice: once after an OAuth
backend's 401 has forced a token refresh, and once after a 400 the adapter
learned from — an unsupported sampling parameter (`openai` with an API key) or
an unsupported `reasoning_effort` (`mistral`); both 400s are read before any
byte reaches the client, so the stream path learns too, and a request gets one
learn retry whichever it is `Scripts/llm-router.py:_post`.

**One writer, one pump.** The handler thread is the only thread that writes to
the client. A streaming request adds exactly one reader thread, the upstream
pump, which reads lines into a byte-bounded queue and closes the upstream itself
`Scripts/llm-router.py:_rt_pump` `Scripts/llm-router.py:_RtByteQueue`. The
handler waits on the queue in one-second ticks and, in order, checks shutdown,
upstream silence past the backend's `idle_timeout`, the item, an `event: ping`
when nothing has been written to the client for
`Scripts/llm-router.py:_PING_INTERVAL_S`, and a client-EOF poll
`Scripts/llm-router.py:_relay_loop` `Scripts/llm-router.py:_rt_client_gone`. A
stream that fails after its head is one `event: error` and a close, never a
`message_stop`. The pump is unblocked with `shutdown(SHUT_RDWR)` on the recorded
upstream socket, never `close()`, and is joined before the connection slot is
released — the reasoning and the measurements are ADR 0028's.

**The model echo.** Translated and normalized answers (mistral, codex, openai; llamacpp's
`message_start` and non-stream body) carry the model string the client
requested, on the default route too; passthrough rewrites nothing in the
response `Scripts/llm-router.py:LlamacppRelay`
`Scripts/llm-router.py:MistralStreamTranslator`.

**Error mapping is two tables.** The status the router answers maps to its
Anthropic error type `Scripts/llm-router.py:_STATUS_TO_TYPE`; an upstream
non-2xx maps to the router's status — 401 and 403 to 502, 422 to 400,
502/503/504 to 529, anything unlisted to 502
`Scripts/llm-router.py:_UPSTREAM_STATUS`. An upstream 401 or 403 is reported as
"backend `<name>` rejected the router's credential", never as a 401 to Claude
Code, because a router 401 means only that the router token is wrong. The one
exception is an OAuth backend: its upstream 401 after the forced refresh, and a
refresh the token endpoint refuses as expired or revoked, are a 401
`authentication_error` naming the backend and the command that fixes it,
`llm-router.py login --backend <name>`, because only a fresh login can
`Scripts/llm-router.py:ResponsesAdapter` — the KD-16 deviation in
[[0028-route-by-model-translate-at-the-edge]]. The Responses error codes the
router types itself (usage and rate limits as 429, an unsupported SIWC route as
403) are `Scripts/llm-router.py:_RT_RS_ERROR_CODES`.

The plumbing it shares with the fleet is generated, not imported: one
`_mcp_logging.py` region `Scripts/llm-router.py:_configure_logging` and, at the
head of the outbound-transport unit, the OAuth protocol from
`Scripts/_mcp_oauth.py` — PKCE, the authorize URL, the code, refresh and device
grants, the token answer and the ID token, the loopback callback listener —
with its transport and clock injected by the router, so the router keeps its
one sender and its one address policy ([[0014-a-canonical-source-is-a-domain]],
[[0025-generate-do-not-import]]). `Scripts/llm-router.py` is a hand-declared
host of the generator `Scripts/amalgamate.py:DECLARED_HOSTS`
([[generated-regions]]). The address
classifier is a declared verbatim hand copy from `Scripts/_mcp_chrome.py`
`Scripts/amalgamate.py:HAND_COPY_REASONS`.

## Command line

Network settings are flags; routing and secrets are the config. There is no
`--config-json`: argv is visible in `ps`, and every config carries a secret
`Scripts/llm-router.py:_rt_parser`.

| Flag | Default | Rule |
|---|---|---|
| `--config` | required | the JSON config below |
| `--bind` | `127.0.0.1` | an IPv4 literal; IPv6 and host names are refused; a non-loopback address needs `--allow-remote` |
| `--port` | `0` (ephemeral) | `0`-`65535` |
| `--allow-remote` | off | allows a non-loopback `--bind` — a VPN interface, never a public one: `0.0.0.0` and any global address are refused even with it, while `100.64.0.0/10` (CGNAT, used by VPNs such as Tailscale) is accepted |
| `--allowed-origin` | none | repeatable; an exact `scheme://host[:port]`, http or https, no path, query or trailing slash |
| `--ready-file` | none | written `0600` and atomically after the bind: `{"port": N, "pid": P}` `Scripts/llm-router.py:_rt_write_ready_file`; removed on a clean shutdown while it still holds the router's pid `Scripts/llm-router.py:_rt_remove_ready_file` |
| `--body-limit` | 32 MiB `Scripts/llm-router.py:_ROUTER_BODY_LIMIT` | bytes, inside `Scripts/llm-router.py:_BODY_LIMIT_RANGE` (1 MiB to 256 MiB) |
| `--max-connections` | `Scripts/llm-router.py:_HTTP_CONNECTION_CAP` (32) | inside `Scripts/llm-router.py:_CONNECTION_CAP_RANGE` (1 to 1024); past it a connection gets a bare 503 and is closed |
| `--debug` | off | DEBUG logging to stderr |
| `--log-file` | none | log to a `0600` file; implies `--debug` |

The flags are validated before the config is read
`Scripts/llm-router.py:_rt_settings`. The listener is bound before anything
else starts; SIGTERM, SIGINT and SIGHUP handlers go in before the ready file is
written, so a supervisor that signals as soon as the file appears reaches the
ordered shutdown `Scripts/llm-router.py:main`. Exit 0 is a normal or
signal-initiated exit; exit 2 is a command-line, config or bind refusal, printed
as one `llm-router: <msg>` line on stderr that names the flag or field and the
rule, never the value `Scripts/llm-router.py:_rt_refuse`.

**The ordered shutdown.** A POST accepted from then on gets a 529 and a live
stream gets one `event: error` "router shutting down"; every live upstream
socket is shut down; running handlers are drained for at most
`Scripts/llm-router.py:_DRAIN_S`; then the listener closes, the ready file is
removed if it still holds this process's pid, and the log handlers are flushed
`Scripts/llm-router.py:_rt_shutdown`.

**Pointing Claude Code at it.** Set `ANTHROPIC_BASE_URL` to
`http://127.0.0.1:<port>` — `127.0.0.1` rather than `localhost`, because the
listener is IPv4-only — and `ANTHROPIC_AUTH_TOKEN` to the config's
`auth_token`, which Claude Code sends as `Authorization: Bearer <token>`. On a
loopback bind the `Host` header must be `127.0.0.1:<port>` or
`localhost:<port>` `Scripts/llm-router.py:_precheck`. Leave
`ANTHROPIC_API_KEY` unset: the router token in `x-api-key` as well is refused
as a token in the wrong place. Claude Code routes by the model string it sends,
so the route keys must be those strings; `ANTHROPIC_MODEL`, the
`ANTHROPIC_DEFAULT_*_MODEL` variables and `CLAUDE_CODE_SUBAGENT_MODEL` set them,
and anything else falls to `default`. The `--help` epilog carries a worked
config, the run command and these variables, and reads the token with
`read -rs` and hands it to curl on stdin, so it never reaches an argv or the
shell history `Scripts/llm-router.py:_RT_HELP_EPILOG`.

**`login` — logging an OAuth backend in.** `llm-router.py login --config <file>
--backend <name>` logs in one backend whose profile uses OAuth (kind `codex`,
or `openai` with an `oauth` object) and writes its refresh token into that
backend's `oauth` object; it is dispatched before the server's parser, so the
server's flags are untouched `Scripts/llm-router.py:main`
`Scripts/llm-router.py:_rt_login_parser`.

| Flag | Default | Rule |
|---|---|---|
| `--config` | required | the router's config; the same 0600 rules |
| `--backend` | required | a backend of that config whose profile logs in with OAuth |
| `--device` | off | codex only: the device-code flow instead of the browser redirect |
| `--open-browser` | off | open the authorize URL in a browser (it is always printed) |
| `--timeout` | `Scripts/llm-router.py:_RT_LOGIN_TIMEOUT_S` (300 s) | seconds to wait for the login, inside `Scripts/llm-router.py:_RT_LOGIN_TIMEOUT_RANGE` (5-1800) |
| `--debug`, `--log-file` | off | as for the server |

The browser flow prints the authorize URL and waits for the redirect on a
loopback listener — codex on `127.0.0.1:1455` (plus `[::1]:1455` when it can),
`openai` on an ephemeral `127.0.0.1` port — or for the redirect URL pasted on
stdin, whichever comes first `Scripts/llm-router.py:_rt_login_browser`
`Scripts/llm-router.py:_rt_login_paste_reader`. The device flow prints a code to
enter at the provider and polls at the provider's interval
`Scripts/llm-router.py:_rt_login_device`. The `openai` login registers a
dynamic client, receives an issued `client_id` and a `host_id`, and checks the
ID token's issuer, audience, expiry and nonce and the granted scope before
anything is stored `Scripts/llm-router.py:_rt_oauth_check_answer`. `login`
exits 0 after the token is written and 2 on any refusal — a busy 1455 names
`--device` as the way out. A refused login prints its failure kind and an
allow-listed OAuth error code, never the provider's `error_description`, and a
transport failure one scrubbed line `Scripts/llm-router.py:_rt_login`.

## Config

One JSON file, read from a descriptor opened without following a symlink, which
must be a regular file owned by the effective user with no group or other
permission bits (`chmod 600`) `Scripts/llm-router.py:_rt_read_config_file`.
Duplicate keys are refused at every depth and every refusal names the JSON path
and the rule, never a value `Scripts/llm-router.py:load_config` —
[[0015-ambiguity-is-the-defect]]. An example with the three chat kinds:

```json
{
  "auth_token": "<router token: secrets.token_urlsafe(32)>",
  "backends": {
    "infernox": {"kind": "passthrough", "base_url": "http://192.168.1.20:8080", "allow_private": true,
                 "api_key": "<optional>", "auth_header": "x-api-key", "allow_cleartext_api_key": true,
                 "forward_headers": ["anthropic-version", "anthropic-beta"]},
    "llama":    {"kind": "llamacpp", "base_url": "https://llama.lan:8443", "allow_private": true,
                 "ca_file": "/Users/me/.config/llm-router/lan-ca.pem"},
    "mistral":  {"kind": "mistral", "base_url": "https://api.mistral.ai", "api_key": "<key>",
                 "connect_timeout": 10, "idle_timeout": 300}
  },
  "routes": {
    "claude-sonnet-4-5": {"backend": "mistral", "model": "mistral-large-latest",
                          "options": {"count_divisor": 4, "temperature": 0.3, "max_tokens_cap": 32768}},
    "claude-haiku-4-5":  {"backend": "llama", "model": "qwen3-coder",
                          "options": {"thinking_budget": 4096, "quirks_off": ["hoist-system"]}}
  },
  "default": {"backend": "infernox", "model": "local-model"}
}
```

The two route keys are assumptions: which model strings Claude Code actually
sends has not been measured yet (G-M3 in ADR 0028). `infernox` sends its key
over plain `http://` to a LAN address, which is why it sets
`allow_cleartext_api_key`; without it the loader refuses that backend. The
suite carries the same example with its placeholders filled in and requires
the loader to accept it (case A17) `tests/test_llm_router.py:group_a`.

The two Responses kinds, as backends and routes of the same file:

```json
"backends": {
  "codex":       {"kind": "codex", "oauth": {}},
  "openai-siwc": {"kind": "openai", "oauth": {}},
  "openai-key":  {"kind": "openai", "api_key": "<platform key>"}
},
"routes": {
  "gpt-6-astra": {"backend": "codex", "model": "gpt-6-astra",
                  "options": {"reasoning_effort": "high"}},
  "gpt-5.5":     {"backend": "openai-key", "model": "gpt-5.5", "options": {"temperature": 0.2}}
}
```

`"oauth": {}` means "not logged in yet"; `login` fills it. A codex or `openai`
backend needs no `base_url` — its profile row carries the default — and an
`openai` backend takes exactly one of `api_key` and `oauth`, which picks its
profile `Scripts/llm-router.py:_rt_cfg_profile`. The help epilogs carry
worked examples of both kinds, each loaded through `load_config`
`Scripts/llm-router.py:_RT_LOGIN_HELP_EPILOG`.

**Top level** — exactly `auth_token`, `backends`, `routes`, `default`; any
other key is refused by name `Scripts/llm-router.py:_RT_TOP_KEYS`.

- `auth_token` (required): the router's bearer token, at least
  `Scripts/llm-router.py:_TOKEN_MIN_LEN` (32) printable ASCII characters with
  no whitespace, of which at least `Scripts/llm-router.py:_TOKEN_MIN_DISTINCT`
  (8) are distinct; a refusal of a weak token names `secrets.token_urlsafe(32)`
  `Scripts/llm-router.py:_rt_cfg_token`.
- `backends` (required, non-empty): name → backend. A name matches
  `^[a-z0-9][a-z0-9_-]{0,31}$` `Scripts/llm-router.py:_RT_BACKEND_NAME_RE`.
- `routes`: model string → route. A route name is 1-256 characters with no
  control character; `default` in any case is reserved, and two names equal
  after `casefold()` are refused.
- `default`: a route used when no route name matches. At least one route or a
  default is required.

**A backend** `Scripts/llm-router.py:_rt_cfg_backend`:

| Key | Default | Rule |
|---|---|---|
| `kind` | required | `passthrough`, `llamacpp`, `mistral`, `codex` or `openai`; `anthropic` is reserved and refused `Scripts/llm-router.py:_KINDS` |
| `base_url` | required, except codex and `openai` (their profile row's) | `http` or `https`; no userinfo, query or fragment; path segments `[A-Za-z0-9._~-]` `Scripts/llm-router.py:_rt_cfg_base_url`. An `http://` URL requires `allow_private` |
| `allow_private` | `false` | admits private, ULA, CGNAT and site-local addresses, minus the metadata addresses and Teredo (see Security) |
| `allow_loopback` | `false` | admits loopback; requires `allow_private` |
| `ca_file` | none | an absolute path to a PEM CA, https only; replaces the system trust store for this backend `Scripts/llm-router.py:_rt_read_ca_file` |
| `api_key` | none | at least `Scripts/llm-router.py:_API_KEY_MIN_LEN` (16) printable ASCII characters, no whitespace, different from `auth_token`; required for `mistral`, refused for codex, and for `openai` the alternative to `oauth`. With an `http://` `base_url` it needs a loopback host (`localhost` or a loopback literal, never resolved) `Scripts/llm-router.py:_rt_is_loopback_host` or `allow_cleartext_api_key` |
| `oauth` | none | codex (required) and `openai` only: the login state, `{}` before the first `login`. Keys `Scripts/llm-router.py:_RT_OAUTH_PERSIST_KEYS` only — `refresh_token` (the token rule, at least 16 characters, different from `auth_token`), `account_id`, `expires_at`, and for `openai` the issued `client_id` and `host_id`; written by `login` and the router, never by hand in normal use `Scripts/llm-router.py:_rt_cfg_oauth` |
| `identity` | the omp row | codex only: overrides of `originator`, `user-agent` and `version` in `Scripts/llm-router.py:_RT_CODEX_IDENTITY`, each 1-256 printable ASCII characters `Scripts/llm-router.py:_rt_cfg_identity` |
| `auth_base_url` | the provider's | an OAuth profile only: where the token endpoint lives instead (tests, a proxy). The `base_url` rules, but `https` only, except an `http://` loopback host under `allow_private` and `allow_loopback`; with it the token endpoint inherits the backend's address policy and `ca_file`, without it the token endpoint is public-only with the system trust store |
| `allow_cleartext_api_key` | `false` | a JSON bool; `true` lets the `api_key` travel over a non-loopback `http://` URL in clear text — a LAN backend you accept that for. No effect without an `api_key` |
| `auth_header` | `x-api-key` with an `api_key`, else `none` | `authorization`, `x-api-key` or `none`; anything but `none` needs an `api_key`; `mistral` and `openai` with an `api_key` use `authorization` only; refused on an OAuth profile, whose credential comes from the token store |
| `forward_headers` | the kind's `DEFAULT_FORWARD` | lower-case names, each in the kind's `FORWARDABLE` (table below) |
| `connect_timeout` | `Scripts/llm-router.py:_CONNECT_TIMEOUT_S` (10 s) | resolve, connect and TLS handshake under one deadline (the resolver runs on a daemon thread joined with what remains of it; a late answer is a 504 and the thread is abandoned); `0 < x <= 3600` |
| `idle_timeout` | `Scripts/llm-router.py:_IDLE_TIMEOUT_S` (300 s) | per upstream read; `0 < x <= 3600` |

**A route** — `backend` (a configured backend), `model` (the upstream
model id, 1-256 characters, no control character), `options`, validated by
the backend kind's `ROUTE_OPTIONS`, and the optional `display_name`; an unknown
key or option is refused by name `Scripts/llm-router.py:_rt_cfg_route`.

| Key | Default | Rule |
|---|---|---|
| `backend` | required | names a configured backend |
| `model` | required | the upstream model id; a string of 1-256 characters with no control character |
| `options` | `{}` | an object, checked by the kind's validators (table below) |
| `display_name` | the route key | a string of 1-`Scripts/llm-router.py:_RT_DISPLAY_NAME_LIMIT` (256) characters with no control character; shown by `GET /v1/models` only. Allowed on `default` too, where it is unused |
| `max_input_tokens`, `max_tokens` | none | integers from `Scripts/llm-router.py:_RT_TOKEN_LIMIT_MIN` to `Scripts/llm-router.py:_RT_TOKEN_LIMIT_MAX`; advertised by `GET /v1/models` (with `context_window`), never enforced |

| Kind | Option | Rule |
|---|---|---|
| passthrough | `temperature`, `top_p` | 0-2, 0-1; replace the client's values on `/v1/messages`, also when it sent none |
| llamacpp | `thinking_budget` | integer ≥ 1; the budget the `adaptive-thinking` quirk uses (8192 when unset) |
| llamacpp | `temperature`, `top_p`, `top_k` | 0-2, 0-1, integer ≥ 1; override the client's values (`sampling-override`) |
| llamacpp | `quirks_off` | a list of quirk names from the table below |
| mistral | `count_divisor` | integer 1-16, default 4; the count_tokens estimate's divisor `Scripts/llm-router.py:_rt_ms_estimate` |
| mistral | `temperature`, `top_p` | 0-2, 0-1; override the client's values |
| mistral | `max_tokens_cap` | integer ≥ 1; the client's `max_tokens` is cut to it |
| mistral | `reasoning_effort` | one of `Scripts/llm-router.py:_RT_MS_EFFORTS` (`none` to `max`); sent as is whatever the client's thinking asks, and never remapped or learned |
| mistral | `reasoning_effort_map` | an object of at most seven pairs, each key and value one of the same efforts; rewrites the effort computed from the client's thinking before it is sent, e.g. `{"low": "high", "xhigh": "high"}` for a model that takes only some `Scripts/llm-router.py:_rt_ms_opt_effort_map` |
| codex, `openai` | `count_divisor` | as for mistral, over the translated Responses body |
| codex, `openai` | `reasoning_effort` | one of `Scripts/llm-router.py:_RT_RS_EFFORTS` (`none` to `xhigh`); wins over the effort the client's thinking budget maps to |
| codex, `openai` | `verbosity` | `low`, `medium` or `high`; sent as `text.verbosity` |
| `openai` with an `api_key` | `temperature`, `top_p`, `max_tokens_cap` | as for mistral; refused at load on the two OAuth profiles, which take no sampling |

`Scripts/llm-router.py:PassthroughAdapter` `Scripts/llm-router.py:LlamacppAdapter`
`Scripts/llm-router.py:MistralAdapter` `Scripts/llm-router.py:CodexAdapter`
`Scripts/llm-router.py:OpenaiAdapter`

## Backends

**Headers: an allow-list per kind.** The upstream request's headers are built
from the backend, never copied from the client: `Content-Type:
application/json`, `Accept` (`text/event-stream` when streaming, else
`application/json`), `User-Agent: llm-router`, then each client header whose
name is in the backend's `forward_headers`, then the credential last
`Scripts/llm-router.py:_rt_upstream_headers`. `Accept-Encoding: identity` and
`Content-Length` are added by the one sender `Scripts/llm-router.py:_rt_send`.

| Kind | `FORWARDABLE` | `DEFAULT_FORWARD` | Credential |
|---|---|---|---|
| passthrough | `anthropic-version`, `anthropic-beta` | both | per `auth_header` |
| llamacpp | `anthropic-version` | `anthropic-version` | per `auth_header` |
| mistral | (none) | (none) | `Authorization: Bearer <api_key>` |
| codex | (none) | (none) | `Authorization: Bearer <access token>` from the token store, plus `chatgpt-account-id` and the omp identity headers |
| openai | (none) | (none) | `Authorization: Bearer` the `api_key`, or the access token of an OAuth login |
| anthropic | reserved: refused by the loader | — | — |

`Scripts/llm-router.py:_NEVER_FORWARD` lists the names no table may ever hold —
`authorization`, `x-api-key`, `host`, `cookie`, `content-length`,
`transfer-encoding`, `connection`, `origin` and the router-owned `accept`,
`accept-encoding`, `content-type`, `user-agent` — plus any `proxy-*` name, and
`Scripts/llm-router.py:_rt_check_forward_tables` checks every kind's tables
against it at import, so a bad table stops the router before it binds.

**passthrough** — an Anthropic-compatible upstream such as inferNO. Only
`model` is rewritten in the request; the body goes to `<base_url>/v1/messages`
(or `/v1/messages/count_tokens`), and the stream is relayed one whole event at a
time with its bytes unchanged `Scripts/llm-router.py:PassthroughAdapter`
`Scripts/llm-router.py:EventRelay`. Lines end at CRLF, LF or a bare CR, as in a
client's SSE parser, and a stream-leading UTF-8 BOM is dropped — the one byte
the relay changes `Scripts/llm-router.py:_rt_sse_split`. An upstream `:`
comment is not relayed; the handler's ping timer is the only source of `event: ping`. An upstream
`event: error` keeps its type when it is an Anthropic one and has its message
scrubbed.

**llamacpp** — llama-server's own `/v1/messages`, with its known deviations
repaired by registry rows `Scripts/llm-router.py:LLAMACPP_REQUEST_QUIRKS`
`Scripts/llm-router.py:LLAMACPP_EVENT_QUIRKS`. Each row is `(name, upstream
ref, fn)`, every function returns a new value, is idempotent and is a no-op on
conforming input, and a route switches a row off by name with `quirks_off`, so a
suspected upstream fix can be tried without a code change. count_tokens is
forwarded after the request quirks, so the count matches what `/v1/messages`
would send `Scripts/llm-router.py:LlamacppAdapter`.

| Row | Applies to | Upstream ref (unverified) | Repair |
|---|---|---|---|
| `tool-results-first` | request | llama.cpp #29482 | in each user message, `tool_result` blocks first, each group in its order |
| `hoist-system` | request | llama.cpp #27367 | a list `system` becomes one string joined with a blank line |
| `adaptive-thinking` | request | no adaptive type | `{"type":"adaptive"}` becomes `enabled` with `min(thinking_budget or 8192, max_tokens - 1)`; dropped when `max_tokens <= 1` |
| `sampling-override` | request | llama.cpp #27893 | the route's `temperature` / `top_p` / `top_k` win over the client's |
| `tool-use-input` | stream event and non-stream `content[]` | llama.cpp #22960 | a `tool_use` block start without `input` gets `"input": {}` |
| `error-shape` | `event: error`, and any event whose data is an object with an `error` key | llama.cpp error format | `{code, message, type}` (flat or nested) becomes the Anthropic envelope, typed through the two status tables, its message scrubbed |

An event a quirk changed is re-serialized; every other event is relayed byte for
byte `Scripts/llm-router.py:LlamacppRelay`. Line splitting and the BOM are
handled as for passthrough.

**mistral** — no Anthropic endpoint, so the request is translated to
`<base_url>/v1/chat/completions` and the answer back
`Scripts/llm-router.py:MistralAdapter`. The translation builds a **new** body
from an explicit key list, so an Anthropic field it does not name is dropped by
construction; system text becomes the first message, `tool_use` blocks become
`tool_calls` with JSON-string arguments, `tool_result` blocks become `tool`
messages (an orphan one a user text), a `tool` message directly followed by a
user message gets an assistant `"Done."` bridge, and a block type Mistral cannot
take is a 400 naming the type `Scripts/llm-router.py:_rt_ms_translate`. Tool
ids are a pure function of the request: a 9-alphanumeric Mistral id becomes
`toolu_lr` + those 9 characters and maps back exactly, every other id hashes to
9 base62 characters, so a resent history gets the same ids across restarts
`Scripts/llm-router.py:_rt_ms_tool_id_map`
`Scripts/llm-router.py:_rt_ms_anthropic_id`. In the stream, text deltas go out
as they arrive while tool-call deltas are buffered — their arguments, ids and
names bounded by `Scripts/llm-router.py:_RT_BUFFERED_TOOL_LIMIT` (8 MiB) and
their number by `Scripts/llm-router.py:_RT_BUFFERED_TOOL_COUNT` (128) — and
emitted whole, in index order, at the end of the turn
`Scripts/llm-router.py:MistralStreamTranslator`. A tool's `arguments` may come
as a JSON string or an object; it must amount to a JSON object with finite
numbers, and the client gets it re-serialized, compact and ASCII-escaped; a
list or anything else is an `event: error` (in a non-stream answer, a 502).
count_tokens is never forwarded: it is a local estimate, the translated body's
UTF-8 bytes divided by `count_divisor` `Scripts/llm-router.py:_rt_ms_estimate`.
A 422 answer's detail list is flattened to field names, never values
`Scripts/llm-router.py:_rt_ms_detail_names`. A mid-conversation `system` turn
is folded into the leading system message.

*Mistral reasoning.* The effort sent as `reasoning_effort` is the route's fixed
option when it has one; otherwise the client's thinking budget is mapped
through the same bands as the Responses kinds, then through the route's
`reasoning_effort_map`, then through what the router has learned about the
model `Scripts/llm-router.py:_rt_ms_effort`; no thinking, or disabled thinking,
sends no key. Neither a `thinking` key nor a `reasoning_content` field is ever
sent, because Mistral refuses both. Mistral's thinking chunks come back as one
Anthropic thinking block whose signature is `lms1.` plus the base64 of
Mistral's own signature, the bare prefix when it sends none
`Scripts/llm-router.py:_rt_ms_signature`; stream and non-stream share one
content reader `Scripts/llm-router.py:_RtMsContent`. On replay the client's own
`lms1.` blocks become one thinking chunk ahead of the text; a foreign signature,
a `redacted_thinking` block or an undecodable signature is dropped and counted
in the `req` line's `dropped_thinking`.

*The learned effort.* Some Mistral models take only a few efforts and answer
any other with a 400 that lists the supported ones. The router parses that list
`Scripts/llm-router.py:_RT_MS_EFFORT_REFUSED_RE`, remembers it per backend and
upstream model for the life of the process
`Scripts/llm-router.py:_RouterHttpServer`, and resends once with
the nearest supported effort — never `none` while thinking was asked for, a
tie going to the higher, `none` only when it is all the model takes
`Scripts/llm-router.py:_rt_ms_effort_remap`; later requests are remapped up
front. The fixed `reasoning_effort` option is never remapped: its 400 is
relayed. One INFO line `reasoning_effort learned: ...` marks each change of a
learned set, and the `req` line's `effort=` field shows the effort sent
whenever the map or the learned set changed it
`Scripts/llm-router.py:_rt_log_summary`.

**codex and openai** — one adapter, `ResponsesAdapter`, translates to and from
the OpenAI Responses API, and every difference between the three wire dialects
is a row of `Scripts/llm-router.py:_rt_responses_profiles`, never a kind branch:

| Profile | Picked by | Endpoint | Sampling | Event-stream type required | Tools sent as |
|---|---|---|---|---|---|
| `codex` | kind codex | `https://chatgpt.com/backend-api` + `/codex/responses` | no | no | functions |
| `openai-oauth` | kind openai with `oauth` | `https://api.openai.com` + `/v1/responses` | no | no | one namespace tool, `claude_code` |
| `openai-apikey` | kind openai with `api_key` | the same | yes | yes | functions |

The codex row also sends the account header, `OpenAI-Beta:
responses=experimental` and the omp client identity — the `originator`,
`User-Agent` and `version` that client sends, measured from its release and
overridable per backend `Scripts/llm-router.py:_RT_CODEX_IDENTITY`
`Scripts/llm-router.py:ResponsesAdapter`. The request body is
built new from an explicit key list `Scripts/llm-router.py:_rt_rs_translate`:
system text becomes `instructions` (a mid-conversation `system` turn a
`developer` input item in place), `tool_use` / `tool_result` become
`function_call` / `function_call_output` items whose ids are a pure function of
the Anthropic ids, a tool's JSON-schema `pattern` holding a lookaround or a
backreference is dropped because the upstream refuses it
`Scripts/llm-router.py:_rt_rs_schema`, `store` is false, and the `prompt_cache_key` is a hash of the backend and the
client's `metadata.user_id` (else the first user text), so a resumed
conversation lands on the same upstream cache
`Scripts/llm-router.py:_rt_rs_cache_key`. The upstream is **always**
asked for a stream; a non-stream client request runs the same translator into a
message collector over the whole answer `Scripts/llm-router.py:_RtMessageCollector`.
Responses streams get their own bounds: a line up to
`Scripts/llm-router.py:_RT_RS_SSE_LINE_LIMIT` (8 MiB), an event up to
`Scripts/llm-router.py:_RT_RS_SSE_EVENT_LIMIT` (16 MiB).

*Reasoning.* The client's thinking budget picks `reasoning.effort` through
fixed bands `Scripts/llm-router.py:_RT_RS_EFFORT_BANDS` (adaptive thinking is
`medium`) unless the route fixes one, and the reasoning summary is asked for.
A reasoning item comes back as a thinking block whose text is the summary and
whose signature carries the encrypted reasoning, `lrs1.<backend>.<content>`,
or as a `redacted_thinking` block when there is no summary
`Scripts/llm-router.py:_rt_rs_signature`. On replay a signature minted for the
same backend becomes a reasoning item again; another backend's, a foreign one,
one over `Scripts/llm-router.py:_RT_RS_SIGNATURE_LIMIT` or one beyond the
`Scripts/llm-router.py:_RT_RS_REASONING_CAP`-th is dropped and counted
`Scripts/llm-router.py:_rt_rs_unsign`.

*Sampling.* The two OAuth profiles send no `temperature` or `top_p` and report
what they dropped as `sampling_dropped=` on the `req` line. `openai` with an API
key sends them, and when a model answers 400 "Unsupported parameter" for one,
the router drops it for that backend and model for the life of the process and
resends once `Scripts/llm-router.py:_rt_unsupported_sampling`.

## OAuth: the token store and the write-back

**Where the tokens live.** A logged-in backend's `oauth` object holds the
refresh token and what goes with it; the access and ID tokens are kept in
memory only and never written, so every start refreshes once
`Scripts/llm-router.py:_RT_OAUTH_PERSIST_KEYS`. There is no separate token
file and no background thread: the token store refreshes on the request that
finds the access token due `Scripts/llm-router.py:_RtTokenStore`.

**One refresh at a time.** Each OAuth backend has its own lock, taken in short
slices so a closing server is noticed; a request that cannot get it within the
backend's `connect_timeout` plus `Scripts/llm-router.py:_RT_OAUTH_IDLE_S` is a
503, and one arriving while the server shuts down a 529. A refresh that fails
for any reason but a dead login is shared with every waiter for
`Scripts/llm-router.py:_RT_OAUTH_FAIL_CACHE_S`, so a broken token endpoint costs
one POST per window, not one per request. After an upstream 401 the store
refreshes only if the current access token is still the rejected one. The
token endpoint's answer is checked before use — every token against the one
header-value rule `Scripts/llm-router.py:_rt_header_value_ok`, the `openai` ID
token and scope by `Scripts/llm-router.py:_rt_oauth_check_answer` — and an
answer that fails is a 502 whose rotated refresh token is discarded
`Scripts/llm-router.py:_RtTokenStore`. A provider's answer can never
make the next refresh due sooner than
`Scripts/_mcp_oauth.py:OAUTH_MIN_REFRESH_INTERVAL_S` from now.

**The write-back.** A rotated refresh token is written into the config before
the store swaps it in memory, by one writer that rewrites only
`backends.<name>.oauth` `Scripts/llm-router.py:_rt_config_update_oauth`: the
config's directory is checked first (owned by the user, not group- or
other-writable) and every later file operation is relative to that directory's
descriptor; a sidecar `<config>.lock` is flocked, shared by the router and
`login` `Scripts/llm-router.py:_rt_config_lock`; stale temp files of the writer's
own pattern are swept; the file is re-read, merged, re-serialized and written
to a 0600 temp file only if the result round-trips and every value outside
that object is unchanged; the hash is re-checked against a writer that took no
lock; then the temp file is renamed over the config and the directory is
synced. A refusal at any step is one WARNING with the backend name and a type
name; the token stays in memory. A config that only holds non-OAuth backends
never gets a lock file `Scripts/llm-router.py:_rt_startup_sweep`.

**A login on disk wins.** When a refresh fails because the login expired or was
revoked, a different refresh token on disk — a `login` ran, or another router
rotated it — is adopted once and refreshed; a token that failed that way is
dropped and never adopted again, so the next `login` is picked up by the next
request. And a `login` that lands while a refresh of the old grant is in flight
is never overwritten: the writer sees a disk token that is neither the one the
refresh started from nor the dead one, writes nothing, and the store adopts the
disk login instead `Scripts/llm-router.py:_rt_config_disk_refresh`.

**Secrets in the scrubber.** Every token the store or `login` handles is pinned
in the scrubber under its backend before any header use or write, so error text
relayed to the client never carries a current token; superseded ones stay as
ordinary entries until the cap evicts the oldest
`Scripts/llm-router.py:_rt_scrub_pin` `Scripts/llm-router.py:_RT_SCRUB_DYNAMIC_CAP`.

## Security

The code-mode review of the Responses kinds and the OAuth client, and the five
findings it fixed, is [[security-review-20261007-123500]].

**Inbound.** The bind is an IPv4 literal, loopback by default, and any other
address needs `--allow-remote`. Each request is checked from its request line
and headers only, before any body byte is read, in this order: header defects,
the path, `Host` (on a loopback bind only), `Origin` (an absent one passes; a
present one must be named by `--allowed-origin`), the bearer — compared in
constant time — then the router token appearing anywhere other than the one
`Authorization` header (a 400, also in the query), then the query
`Scripts/llm-router.py:_precheck`. The request line and headers must arrive
within `Scripts/llm-router.py:_HTTP_PREAUTH_TIMEOUT_S` (5 s) in total while
the connection has never authenticated, and within
`Scripts/llm-router.py:_HTTP_HEADER_TIMEOUT_S` (10 s) once it has
`Scripts/llm-router.py:handle_one_request`; the body, once the checks pass,
within `Scripts/llm-router.py:_HTTP_BODY_TIMEOUT_S` (60 s) in total
`Scripts/llm-router.py:_HeaderDeadlineReader`. An `Expect: 100-continue` gets
its `100 Continue` only after the bearer and the framing checks pass, never
from the stdlib before them `Scripts/llm-router.py:handle_expect_100`. A
forwardable header sent twice, or whose value holds a CR or LF, is a 400
naming the header `Scripts/llm-router.py:_rt_parse_inbound`. Every refusal is an Anthropic
envelope with `Connection: close` `Scripts/llm-router.py:_refuse`, and the
stdlib's own refusals (400, 414, 431, 501, 505) answer fixed texts so they never
quote the request line or the method `Scripts/llm-router.py:send_error`. A 401
or 403 is logged with the status and the peer address only.

**Outbound: SSRF policy.** The backend's host is resolved on every connect and
**every** address it resolves to is vetted; one refused address refuses the hop
`Scripts/llm-router.py:_rt_resolve`. Without `allow_private` only public
addresses pass `Scripts/llm-router.py:_ch_address_refused`; with it, private,
ULA, CGNAT and site-local addresses pass, while the translation prefixes,
the metadata addresses that are not link-local (`fd00:ec2::254`,
`100.100.100.200`) and Teredo `2001::/32`
`Scripts/llm-router.py:_RT_PRIVATE_REFUSED_NETS`, link-local
(`169.254.169.254` included), multicast, unspecified and reserved stay refused,
and loopback needs `allow_loopback` as well
`Scripts/llm-router.py:_rt_private_policy_refused`. The resolver runs on a
daemon thread under the backend's `connect_timeout`, and the socket connects
to the vetted literals and finishes the TLS handshake under what remains of
that one deadline, reading no proxy variable
`Scripts/llm-router.py:_rt_open_socket`. A redirect, an interim 1xx and a
`Content-Encoding` other than identity are each a 502 `Scripts/llm-router.py:_rt_send`.

**Outbound: TLS.** An `https` backend gets a verifying context — the system
trust store, or only the backend's `ca_file` — with `CERT_REQUIRED`, host-name
checking and a TLS 1.2 floor asserted before a socket opens, and with ragged-EOF
suppression off, so a truncated stream is an error rather than a clean end
`Scripts/llm-router.py:_RtHttpsConnection`.

**Secrets.** The upstream credential belongs to the backend object and is
written only by `Scripts/llm-router.py:_rt_upstream_headers`; the client's own
`Authorization` and `x-api-key` are never forwarded (table above). Upstream
error text relayed to the client is scrubbed of every configured secret — the
value, its URL-encoded and base64 forms and, for a secret of 16 or more
characters, its first and last 8 — then cut to
`Scripts/llm-router.py:_ERROR_TEXT_WIDTH` (300) characters with every
non-printable one replaced `Scripts/llm-router.py:_rt_make_scrubber`; the
scrubbed text goes to the client only and is never logged.
`BackendSpec` and `RouterConfig` print their secrets as `<redacted>`
`Scripts/llm-router.py:BackendSpec` `Scripts/llm-router.py:RouterConfig`.

**Logging is structure only** ([[0011-a-truncated-payload-carries-the-first-cookie]]).
Every wire-derived value goes through `Scripts/llm-router.py:_log_value`; the
access line has the method and the path without its query — `/v1/models/{id}`
as the literal `/v1/models/<id>`, a path that is not one of the endpoints as
`<unrouted>`, a method with no handler as `<other>`, since each is
unauthenticated client text
`Scripts/llm-router.py:log_message`; a POST gets one DEBUG `req` summary line —
endpoint, route, kind, stream, status, upstream status, bytes in and out,
timings and how the stream ended, and when they apply `dropped_thinking=`,
`refreshed=` (an upstream 401 forced a token refresh), `sampling_dropped=` and
`effort=` `Scripts/llm-router.py:_rt_log_summary`; an
exception is logged by its type name only. Nothing the token endpoint says
reaches a log line, an exception message or a client envelope except an
allow-listed OAuth error code `Scripts/_mcp_oauth.py:OAUTH_ERROR_CODES`, and no
token, code, verifier or URL query is ever logged.

## How it is tested

The `llm_router` suite `tests/test_llm_router.py` drives the router live as a
subprocess `tests/test_llm_router.py:RouterProc` against a scripted loopback
backend peer that replays a wire fixture where a case names one
`tests/test_llm_router.py:ScriptedPeer`, and imports it in-process for the
config loader, the pure translators and a static pass. Its forge target is
`llm_router` (`python3 tests/run.py llm_router`). The groups follow the shape:
config refusals and defaults `tests/test_llm_router.py:group_a`, the front
`tests/test_llm_router.py:group_b`, outbound — SSRF policy, verified TLS
against the test CA, upstream refusals `tests/test_llm_router.py:group_c`,
passthrough `tests/test_llm_router.py:group_d`, the llama.cpp quirk rows
`tests/test_llm_router.py:group_e`, Mistral request translation
`tests/test_llm_router.py:group_f`, the Mistral stream
`tests/test_llm_router.py:group_g`, idle deadlines, the drain and a client that
hangs up, per backend kind `tests/test_llm_router.py:group_h`, secret leaks
`tests/test_llm_router.py:group_i`, static AST rules over the router
source, the declared hand copies and sandbox hygiene
`tests/test_llm_router.py:group_j_static` `tests/test_llm_router.py:group_j`
`tests/test_llm_router.py:group_j_hygiene`, and the groups for the Responses
kinds and OAuth: Anthropic-to-Responses request translation
`tests/test_llm_router.py:group_k`, the config write-back — flock, merge,
directory-relative rename `tests/test_llm_router.py:group_l`, the Responses
stream and the non-stream collector `tests/test_llm_router.py:group_m`, the
token store — single-flight refresh, the forced refresh on 401, the shared
failure, the bounded wait, a login landing mid-refresh, and the learned Mistral
effort `tests/test_llm_router.py:group_n` — and the `login` subcommand against
a scripted auth peer `tests/test_llm_router.py:group_o`. K-O run before I and
J, because I sweeps every log the run collected and J compares start-of-run
snapshots `tests/test_llm_router.py:GROUP_ORDER`. Its case count is declared
once, per group, in `tests/run.py:SUITES`, and checked against the run; the
per-group detail is the suite's row in [[tests]], and the red-first record per
group is ADR 0028's, its addendum included.

**Deadlines in milliseconds.** A case whose observable is one of the router's
own deadlines — the ping interval, the relay tick, the pre-auth, header and
body bounds, the OAuth idle timeout and the shared-failure window — runs
against an in-process router of a privately loaded module with that constant
patched to milliseconds, the handler's class-level `timeout` included, since it
is bound when the class is defined; the same code path, waited out in
milliseconds `tests/test_llm_router.py:FastRouter`
`tests/test_llm_router.py:HFastRig`. Its log goes at DEBUG to a 0600 file
that I1 sweeps like a subprocess's stderr. A case that proves a real signal or
a real process exit stays a subprocess at the production values. The suite's
measured runtime and the forge timeout derived from it are in the `llm_router`
target's description `project-forge.yaml`.

The OAuth source has a suite of its own, `mcp_oauth`
`tests/test_mcp_oauth.py`: PKCE and state, the authorize URL, the request
builders, the token answer with every refusal typed, the JWT and ID-token
checks, the callback parse, the device flow, the callback listener over real
loopback sockets with a fake clock, and an AST contract over the source.

**The fixtures are synthetic.** Every `tf_*` file under `tests/files/llm_router/`
was hand-written: the llama.cpp and Mistral ones from the documented wire
shapes, because the user-gated captures M4 (a real llama-server) and M5 (a real
Mistral account) have not run, and the `tf_responses_*.sse` ones from the
Responses event contract; the cases resting on them print "defensive, not
observed", and no fixture holds a key value `tests/files/llm_router/README.md`.
The live checkpoints against codex and the platform API are recorded in ADR
0028's addendum, not in fixtures.

The router also sits in two fleet gates: the 3.9 API scope of the dependency
gate `tests/test_py_deps.py:NEW_39_SCOPE`, and the generator's declared-host
walk, which `python3 Scripts/amalgamate.py --check` covers
`Scripts/amalgamate.py:DECLARED_HOSTS`. Because the generator's hand-copy
census is INFO-grade, the router's own suite adds the FAIL-grade gates on its
three hand copies (group J).

## Declared limits

The module docstring lists the ones a user meets first `Scripts/llm-router.py`.
The list below is a summary and is not complete: the full list, with the
reasoning and severity of each item, is the "Declared limits" section of
[[0028-route-by-model-translate-at-the-edge]], and that ADR is the place to
read before relying on one of them. In brief:

- **Cleartext bearer under `--allow-remote`** (CWE-319). There is no inbound
  TLS, so a non-loopback bind belongs on a VPN interface, never a public one;
  `Host` is not checked on such a bind, and the bearer is the boundary.
- **No throttling of failed authentication** (CWE-307). Repeated 401s are not
  rate-limited; accepted because the token is at least 32 printable ASCII
  characters with at least 8 distinct, and the listener is loopback or VPN
  only. Each 401 or 403 writes one WARNING line, so the log grows with an
  unauthenticated peer's connection rate (CWE-779).
- **The pre-auth header bound is a mitigation** (CWE-770). A peer that
  reconnects as soon as it is cut can still keep every `--max-connections`
  slot busy, at one reconnect per slot every 5 s.
- **Authenticated worst-case memory** (CWE-770). Per connection, roughly the
  body, its translated copy and the larger of a full pump queue
  (`Scripts/llm-router.py:_RT_QUEUE_BYTES`, 16 MiB) and a non-stream upstream
  answer with its decoded text (2 ×
  `Scripts/llm-router.py:_UPSTREAM_BODY_LIMIT`, 64 MiB), plus the parsed
  objects, which are several times the bytes; times `--max-connections`. At the
  defaults the bytes alone are about 6 GiB. Lower `--max-connections` or
  `--body-limit` to shrink it.
- **A resolver that misses the connect deadline leaves its thread running**
  until the OS resolver gives up; under a stalled resolver such threads pile
  up, one per timed-out request.
- **The ready-file removal is best effort:** another writer can replace the
  file between the pid check and the unlink, and nothing is removed on SIGKILL
  or a crash.
- **Passthrough trusts the backend's bytes** within the line and event bounds.
- **`SSL_CERT_FILE` / `SSL_CERT_DIR` are honoured** for a backend with no
  `ca_file`; one with a `ca_file` is immune.
- **A silent wait before the first byte.** Nothing reaches Claude Code until the
  upstream status is known, and the relay kinds hold pings until the upstream's
  first event.
- **The scrubber matches fixed encodings only;** a secret encoded at an offset
  inside a larger string, or split across lines, is not caught.
- **The Mistral token count is an estimate,** and Mistral tool calls appear
  whole at the end of the turn, never streamed.
- **Passthrough request bodies are equal after parsing, not byte for byte**
  (the body is re-serialized after the model rewrite).
- **No metrics endpoint;** the DEBUG `req` line is the only per-request record,
  and only a POST gets one.
- **`GET /v1/models` does not paginate.** `limit`, `before_id` and `after_id`
  are accepted and ignored: the whole list comes back with `has_more` false,
  and every entry's `created_at` is the router's start time, not a model's.
- **The shutdown drain does not cover a non-stream relay** in flight at SIGTERM.
- **The llama.cpp quirk references are unverified;** the issue numbers came
  from the feature brief.
- **The codex kind impersonates another client.** It sends the omp client's
  identity to the ChatGPT backend, a terms-of-service risk the user accepted;
  the identity was measured from one omp release and moves with every release.
- **No ID-token signature check, and no runtime discovery.** The `openai` ID
  token's claims are checked, its signature is not; the provider rows are
  static.
- **Token persistence has windows.** A crash between the provider's rotation
  and the write, or a refresh that times out after the provider rotated, needs
  a re-login; so can a refresh answer whose check fails, since its rotated token
  is discarded. A hand edit landing between the writer's re-check and its
  rename can be lost; the flock serializes only the router and `login`.
  Non-POSIX systems are unsupported. An interpreter without the
  directory-relative file operations gets no write-back at all.
- **The refresh lock is held across the token POST and the write**, so a waiter
  can give up with 503 while the holder is still legitimately writing, and a
  token POST in flight at shutdown can outlive the drain.
- **The scrubber keeps a bounded number of superseded tokens**
  (`Scripts/llm-router.py:_RT_SCRUB_DYNAMIC_CAP`, 64); current ones are pinned
  and never evicted.
- **The reasoning signature is plain, not MAC'd,** and bound to the backend
  name: renaming a backend loses reasoning continuity.
- **Route `max_input_tokens` / `max_tokens` are advertised only,** never
  enforced, and whether Claude Code reads them is unmeasured.
- **Effort is learned on mistral only;** `reasoning_effort_map` is a mistral
  option, and the Responses kinds learn no effort. Learned sampling and effort
  sets live as long as the process and are never written down.
- **A non-stream answer's `dropped_thinking` is not on the `req` line,** and the
  refresh made before an access token expires leaves no trace on it.
- **An OAuth access token has no cleartext opt-in of its own:** under
  `allow_private` it goes to a non-loopback `http://` `base_url` without the
  `allow_cleartext_api_key` an `api_key` needs.
- **The SIWC login is unverified live.** The namespace tool shape and the
  `openai` OAuth flow rest on the provider's documentation and on fakes; the
  live checkpoint for it has not run.
