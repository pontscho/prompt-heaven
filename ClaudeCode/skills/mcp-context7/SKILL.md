---
name: mcp-context7
description: >
  Full API reference for the Context7 documentation MCP server. Use when retrieving
  up-to-date library documentation, code examples, or resolving library IDs via Context7.
  The MCP server exposes one tool in tools/list: context7_call (universal dispatcher).
  Called without 'function' returns server status. All Context7 functions are invoked via context7_call.
triggers:
  - context7
  - look up documentation
  - library documentation
  - resolve library id
  - query docs
  - context7_call
  - up-to-date docs
  - code examples for
---

# Context7 MCP Server

The MCP server (`mcp-context7.py`) exposes **one tool in `tools/list`**:
- `context7_call` — universal dispatcher for all Context7 functions; called without `function` returns server status

All Context7 operations go through `context7_call(function=..., params={...})`. This keeps the tool list minimal while giving full access to the documentation API.

## How to call any Context7 function

```
mcp__context7__context7_call(function="<function_name>",params={...parameters...})
```

**Example — resolve a library ID:**
```
mcp__context7__context7_call(function="context7_resolve_library_id",params={"query":"react hooks","library_name":"react"})
```

**Example — fetch documentation:**
```
mcp__context7__context7_call(function="context7_query_docs",params={"library_id":"/websites/react_dev","query":"useEffect cleanup"})
```

When the server is unavailable, `context7_call` (without `function`) will fail. Check this first.

## Typical workflow

```
context7_resolve_library_id → get a Context7-compatible library ID from a name
context7_query_docs → fetch documentation for that library ID
```

Skip `context7_resolve_library_id` if the user already provides a library ID in the format `/org/project` or `/org/project/version` (e.g. `/vercel/next.js/v14.3.0`).

## Tools

### context7_call (status check)
Returns server status and auth mode when called without `function`.
```json
{}
```

### context7_status
Returns server status, API base URL, and authentication mode.
```json
{}
```

### context7_resolve_library_id
Resolves a package or product name to a Context7-compatible library ID. Call this before `context7_query_docs` unless the user already provides a library ID.

```json
{"query":"The question or task you need help with — used to rank results by relevance.","library_name":"Library name to search for (e.g. \"react\", \"next.js\", \"pandas\")"}
```

Both parameters are **required**.

Returns a formatted list of matching libraries, each with:
- `Context7-compatible library ID` — use this in `context7_query_docs`
- `Title` — library name
- `Description` — short summary
- `Code Snippets` — count of available examples (higher=better coverage)
- `Source Reputation` — `High` / `Medium` / `Low` / `Unknown`
- `Benchmark Score` — quality indicator, 100 is highest
- `Versions` — available version IDs (use as `/org/project/version` format)

**Selection guidance:**
1. Prefer exact name matches
2. Prefer High or Medium source reputation
3. Prefer higher Code Snippets count and Benchmark Score
4. If the user specified a version, pick the matching entry from `Versions`
5. Do not call this more than 3 times per question — use the best result you have

### context7_query_docs
Fetches up-to-date documentation and code examples for a library.

```json
{"library_id":"/org/project","query":"Specific question or task — be detailed. E.g. 'How to set up JWT auth in Express.js'"}
```

Both parameters are **required**.

`library_id` format:
- `/org/project` — latest version
- `/org/project/version` — specific version (from `context7_resolve_library_id` results)

Returns plain text documentation with code examples.

**Query guidance:** Be specific. Good: `"useEffect cleanup for subscriptions"`. Bad: `"hooks"`. Do not call this more than 3 times per question — use the best result you have.

## Parallel call strategy — reduce model turn latency

`context7_resolve_library_id` and `context7_query_docs` are independent HTTP calls.
**Send multiple `context7_call`s in a single response** when you need docs for several libraries at once — only ONE model API round-trip is needed instead of N.

### Safe to batch (all calls are stateless read-only)

|Function|Notes|
|-|-|
|`context7_resolve_library_id`|batch multiple library lookups|
|`context7_query_docs`|batch multiple queries if library IDs are already known|
|`context7_status`||

### Must be sequential (result depends on prior call)

```
context7_resolve_library_id → context7_query_docs
```
You need the `library_id` from the first call to make the second.
Exception: if library IDs for all queries are already known, batch all `context7_query_docs` calls.

### Rule of thumb

- **Multiple unknown libraries?** Batch all `context7_resolve_library_id` calls, then batch all `context7_query_docs` calls — 2 turns total instead of 2×N
- **Library IDs already known?** Batch all `context7_query_docs` directly — 1 turn

## Common workflows

### Resolve then query (typical)
```
Turn 1: context7_resolve_library_id {query, library_name}
Turn 2: context7_query_docs {library_id, query}
```

### Multiple libraries in parallel
```
Turn 1: [BATCH] context7_resolve_library_id("react") + context7_resolve_library_id("vue")
Turn 2: [BATCH] context7_query_docs(react_id,...) + context7_query_docs(vue_id,...)
```
2 turns instead of 4.

### Library ID already known (from user or prior session)
```
Turn 1: context7_query_docs {library_id:"/vercel/next.js",query: "..."}
```

### Check server status
```
Turn 1: context7_call {} ← no function arg → returns status
```

## Error handling

|HTTP status|Meaning|
|-|-|
|429|Rate limited — get a free API key at https://context7.com/dashboard or upgrade at https://context7.com/plans|
|404|Library ID not found — use `context7_resolve_library_id` to get a valid ID|
|401|Invalid API key — must start with `ctx7sk` prefix|
|empty response|Library not finalized — try a different library ID|
