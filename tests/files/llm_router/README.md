# tests/files/llm_router — backend wire fixtures for the router suite

**Fixtures, not real code, and never a secret.** These files are what a
scripted loopback peer replays to `Scripts/llm-router.py` in
`tests/test_llm_router.py`: group E (llama.cpp request and event quirks), group
F (Anthropic -> chat-completions translation, the 422 rewrite) and group G
(chat-completions SSE -> Anthropic SSE translation). Nothing imports them; a
case reads one by name and hands its lines to `ScriptedPeer`.

## Provenance — captured or synthetic

Step 1 of `docs/feature-implementation-plan.md` defines two captures: **M4**
(the user's llama-server: one streamed `/v1/messages` with a tool call, one
error, one count_tokens) and **M5** (Mistral `/v1/chat/completions`: plain
text, one tool call, two parallel tool calls, a 422 for an Anthropic-only
field). Both are **user-gated and have not run yet**. Until they do, every
fixture here is hand-written from the documented and community-reported wire
shapes, and a case that rests on one is **defensive, not observed**: it pins
the behaviour the router promises against the shape we expect, not against a
shape a real backend was seen to send.

| file | provenance | read by |
|---|---|---|
| `tf_llamacpp_stream_tool.sse` | synthetic (pending M4) | E10, E13: streamed tool call, `tool_use` start without `input`, both block stops at the end |
| `tf_llamacpp_error.json` | synthetic (pending M4) | E8 non-stream: HTTP 400, `{"error":{"code","message","type"}}` |
| `tf_llamacpp_stream_error.sse` | synthetic (pending M4) | E8 mid-stream: `event: error` with the flat `{"code","message","type"}` shape |
| `tf_mistral_stream_text.sse` | synthetic (pending M5) | G1 (defensive, not observed), G15-G17 live: OpenAI-shaped `chat.completion.chunk` lines -- a role chunk with empty content, three text chunks (one non-ASCII), a `finish_reason: "stop"` chunk carrying `usage`, then `data: [DONE]` |
| `tf_mistral_stream_tool.sse` | synthetic (pending M5) | G2 (defensive, not observed), G15 live over verified TLS: one tool call keyed by `index` 0 whose 9-alnum `id` and `name` arrive on the first fragment and whose `arguments` arrive in two fragments, `finish_reason: "tool_calls"` with `usage`, `[DONE]` |
| `tf_mistral_stream_parallel.sse` | synthetic (pending M5) | G3 (defensive, not observed): two tool calls (`index` 0 and 1) whose argument fragments INTERLEAVE (0, 1, 0, 1), `finish_reason: "tool_calls"` with `usage`, `[DONE]` |
| `tf_mistral_error_extra_inputs.json` | synthetic (pending M5) | F11 (defensive, not observed): the body of an HTTP 422, `{"object":"error","message":{"detail":[...]}}`, one `extra_forbidden` "Extra inputs are not permitted" entry per Anthropic-only key (`metadata`, `thinking`, `top_k`, a nested `cache_control`); F11 stuffs every key it names and asserts none reaches the translated body |

The three stream fixtures carry no Mistral-specific key beyond the OpenAI chunk
shape: the A-7 quirks (an unknown `p` key, `delta.content` as a list of typed
parts, `usage` on an earlier chunk or on a trailing chunk with empty `choices`,
a mid-stream error chunk) are written
inline in group G (G5-G7, G10), not into a fixture, so each fixture-backed case
fails for one reason only. M5 (e)-(g) decides whether the shapes hold.

Group F also prints "defensive, not observed" on F3, F4 and F5: they rest on
no fixture but on the three A-7 Mistral rules M5 (e)-(g) is to confirm (the
`name` on a tool message, the bridge message between a tool and a user
message, the 9-character tool id); gate G-M5 decides whether the label stays.

When M4 or M5 runs, replace the row's provenance with
`captured (M4|M5, <date>, <build or API version>)` and drop the "defensive,
not observed" label from the cases that rest on it — unless the capture
refuted the rule, in which case the case keeps the label (gate G-M5). Update
this table whenever a task adds, replaces or removes a fixture; it is finished
in Step 14.

## No key, ever

**No key value is ever written to a file here** — not a Mistral key, not a
passthrough key, not the router token, not even a truncated or encoded form.
A captured fixture is reduced before it is kept: ids that could identify the
account are replaced and only the shapes stay. Two sweeps hold the line:

- Step 1's `capture.py --sweep` searches this directory for the **exact** key
  value and its KD-9 forms (URL-encoded, base64, first/last 8 characters)
  before a capture is kept; only the hit count is recorded.
- Case **J5** keeps sweeping this directory on every run for the key-prefix
  pattern and for the suite's `TF_*` sentinels. This README carries none.

## The `tf` prefix

Every fixture file name starts with `tf_` (see `tests/files/README.md`, "The
`tf` prefix rule"): a repo-wide search for a real symbol or file must never
match this directory. Keep the prefix when adding a file.
