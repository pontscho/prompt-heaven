---
name: p:lldb-mcp
description: >
  Full API reference for the LLDB MCP server. Use when debugging with LLDB via MCP:
  crash analysis, core dumps, live debugging, breakpoints, stepping, memory inspection.
  The MCP server exposes two tools in tools/list: lldb_mcp_status (presence check) and
  lldb_call (universal dispatcher). All 29 LLDB functions are invoked via lldb_call.
triggers:
  - lldb
  - debug a program
  - core dump
  - crash analysis
  - set breakpoint
  - attach to process
  - step through code
  - backtrace
  - lldb_mcp
---

# LLDB MCP Server — Full API Reference
All LLDB operations go through `lldb_call(function=...,params={...})`. This keeps the tool list minimal while giving full access to the debugger.

## How to call any LLDB function

```
mcp__mcp-lldb__lldb_call(function="<function_name>",params={...parameters...})
```

**Example — start a session:**
```
mcp__mcp-lldb__lldb_call(function="lldb_start",params={})
```

**Example — load a binary:**
```
mcp__mcp-lldb__lldb_call(function="lldb_load",params={"session_id":"<uuid>","program":"/path/to/binary"})
```

When the server is unavailable, `lldb_call` without parameters will fail. Check this first.

## Session lifecycle

```
lldb_start → creates session, returns session_id
lldb_load → load executable into session
 OR
lldb_attach → attach to running process
 OR
lldb_load_core → load program + core dump

[ debug loop ]
lldb_set_breakpoint / lldb_breakpoint_list / lldb_breakpoint_delete
lldb_run / lldb_continue / lldb_step / lldb_next / lldb_finish
lldb_backtrace / lldb_print / lldb_expression / lldb_examine
lldb_info_registers / lldb_frame_info / lldb_disassemble
lldb_thread_list / lldb_thread_select
lldb_watchpoint
lldb_process_info / lldb_kill
lldb_command → raw LLDB command (escape hatch)

lldb_terminate → end session, release resources
```

Always call `lldb_terminate` when done to free the LLDB process and PTY.

## Tools

### lldb_start
Start a new LLDB session.
```json
{
"lldb_path":"lldb",// optional, default "lldb"
"working_dir":"/path/to"// optional, default cwd
}
```
Returns: `"LLDB session started. ID: <uuid>\n\nOutput:\n..."`

### lldb_load
Load an executable into a session.
```json
{
"session_id":"<uuid>",
"program":"/path/to/binary",
"arguments":["arg1","arg2"] // optional
}
```

### lldb_attach
Attach to a running process by PID.
```json
{"session_id":"<uuid>","pid":1234}
```
Timeout: 60s.

### lldb_load_core
Load a program and its core dump file.
```json
{"session_id":"<uuid>","program":"/path/to/binary","core_path":"/path/to/core"}
```
Automatically runs `bt` and returns the initial backtrace.

### lldb_command
Execute any raw LLDB command (escape hatch for unsupported operations).
```json
{"session_id":"<uuid>","command":"memory read 0x1000"}
```

### lldb_terminate
Terminate a session and release resources.
```json
{"session_id":"<uuid>"}
```

### lldb_list_sessions
List all active sessions.
```json
{}
```

### lldb_run
Run the loaded program.
```json
{"session_id":"<uuid>"}
```
Timeout: 60s (waits for breakpoint or program exit).

### lldb_continue
Continue execution from current stop. Timeout: 60s.
```json
{"session_id":"<uuid>"}
```

### lldb_step
Step into (source line or instruction).
```json
{
"session_id":"<uuid>",
"instructions":false // true = step instruction (si), false = step line (s)
}
```

### lldb_next
Step over (source line or instruction).
```json
{
"session_id":"<uuid>",
"instructions":false // true = next instruction (ni), false = next line (n)
}
```

### lldb_finish
Run until current function returns.
```json
{"session_id":"<uuid>"}
```

### lldb_kill
Kill the running process (keeps session alive).
```json
{"session_id":"<uuid>"}
```

### lldb_set_breakpoint
Set a breakpoint by function name.
```json
{
"session_id":"<uuid>",
"location":"main",// function name
"condition":"x > 5"// optional condition expression
}
```
For file:line breakpoints use `lldb_command` with `breakpoint set --file foo.c --line 42`.

### lldb_breakpoint_list
List all breakpoints.
```json
{"session_id":"<uuid>"}
```

### lldb_breakpoint_delete
Delete a breakpoint by its number.
```json
{"session_id":"<uuid>","breakpoint_id":1}
```

### lldb_watchpoint
Set a watchpoint on a variable or address.
```json
{
"session_id":"<uuid>",
"expression":"my_var",
"watch_type":"write"// "read" | "write" | "read_write"
}
```

### lldb_backtrace
Show the call stack.
```json
{
"session_id":"<uuid>",
"full":false,// true = include local variables per frame (bt full)
"limit":10// optional frame count limit
}
```

### lldb_frame_info
Get detailed info about a specific stack frame.
```json
{
"session_id":"<uuid>",
"frame_index":0// 0 = innermost frame
}
```
Returns: frame selection output + local variables + source listing.

### lldb_print
Print the value of an expression.
```json
{"session_id":"<uuid>","expression":"my_var"}
```

### lldb_expression
Evaluate an expression in the current frame (supports side effects).
```json
{"session_id":"<uuid>","expression":"x + y"}
```
Prefer `lldb_print` for simple reads; use `lldb_expression` when you need full evaluation.

### lldb_examine
Examine raw memory.
```json
{
"session_id":"<uuid>",
"expression":"0x7fff5fbff000",
"format":"x",// "x" hex | "d" decimal | "u" unsigned | "o" octal | "t" binary | "i" instruction | "c" char | "f" float | "s" string
"count":16// number of units to display
}
```

### lldb_info_registers
Display CPU registers.
```json
{
"session_id":"<uuid>",
"register":"rax"// optional; omit for all registers
}
```

### lldb_thread_list
List all threads in the current process.
```json
{"session_id":"<uuid>"}
```

### lldb_thread_select
Select a thread and show its backtrace.
```json
{
"session_id":"<uuid>",
"thread_index":1// 1-based thread index (from thread list output)
}
```
Note: `thread_index` is LLDB's internal index, not the OS thread ID.

### lldb_process_info
Get process status and info.
```json
{"session_id":"<uuid>"}
```

### lldb_disassemble
Disassemble code at current PC or named function.
```json
{
"session_id":"<uuid>",
"location":"main",// optional function name; omit for current PC
"count":10// number of instructions
}
```

### lldb_help
Get LLDB built-in help.
```json
{
"session_id":"<uuid>",
"command":"breakpoint"// optional; omit for full help overview
}
```

## Parallel call strategy — reduce model turn latency

**Send multiple independent `lldb_call`s in a single response** (multi-tool message). The MCP server serializes them on execution, but only ONE model API round-trip is needed instead of N. This is the primary way to reduce latency.

### Safe to batch (read-only, process stopped/crashed)

These do not mutate LLDB state and can be issued together in one response:

|Function|Notes|
|-|-|
|`lldb_backtrace`||
|`lldb_frame_info`|call for multiple frame indices at once|
|`lldb_info_registers`||
|`lldb_thread_list`||
|`lldb_print`|call for multiple variables at once|
|`lldb_examine`|call for multiple addresses at once|
|`lldb_expression`|read-only expressions|
|`lldb_disassemble`||
|`lldb_breakpoint_list`||
|`lldb_process_info`||
|`lldb_list_sessions`||

### Must be sequential (state-changing or order-dependent)

These depend on previous results or mutate debugger state — always one at a time:

```
lldb_start → lldb_load | lldb_attach | lldb_load_core
 → lldb_set_breakpoint
 → lldb_run | lldb_continue | lldb_step | lldb_next | lldb_finish
 → lldb_kill | lldb_terminate
```

### Rule of thumb

- **After any stop** (breakpoint hit, crash, attach): batch all the inspection calls you need
- **Before any resume** (`continue`, `step`, `next`): finish the batch first

## Common workflows

### Crash analysis from core dump
Turn 1: lldb_start
Turn 2: lldb_load_core {program, core_path} ← returns bt automatically
Turn 3: [BATCH] lldb_frame_info(0) + lldb_info_registers + lldb_thread_list
Turn 4: lldb_terminate

3 model turns instead of 5.

### Live debug with breakpoint
Turn 1: lldb_start
Turn 2: [BATCH] lldb_load + lldb_set_breakpoint ← load and arm BP together
Turn 3: lldb_run ← state change, must be alone
Turn 4: [BATCH] lldb_backtrace + lldb_frame_info(0) + lldb_info_registers
Turn 5: lldb_step / lldb_next / lldb_finish ← each step is one turn
Turn 6: [BATCH] inspect again if needed
Turn 7: lldb_terminate

### Attach to running process
Turn 1: lldb_start
Turn 2: lldb_attach {pid: 12345}
Turn 3: [BATCH] lldb_thread_list + lldb_backtrace + lldb_info_registers
Turn 4: lldb_continue
Turn 5: lldb_terminate
