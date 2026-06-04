# API/Interface Analysis Command

You are performing an **API/Interface analysis**. This involves documenting public APIs, interfaces, and how external code should interact with a module or subsystem. This is for creating reference documentation.

## Your Task

1. **Identify the API**: Ask the user which API/interface to analyze (e.g., "HTTP Server API", "Memory API", "Lua bindings", specific header file, etc.)

2. **API Discovery**:
   - Identify all public functions, structs, enums, macros
   - Distinguish between public and private/internal elements
   - Find header files that define the interface
   - Understand the intended use cases

3. **Interface Analysis**:
   - **Overview**: What is this API for? What problems does it solve?
   - **Public Functions**: Document each function signature
   - **Data Types**: Document public structs, enums, types
   - **Constants/Macros**: Document relevant defines and macros
   - **Usage Patterns**: Common usage patterns and idioms
   - **Initialization/Cleanup**: Required setup and teardown
   - **Error Handling**: How errors are reported and handled
   - **Thread Safety**: Thread safety guarantees or requirements
   - **Memory Ownership**: Who owns what? Who is responsible for freeing?
   - **Examples**: Concrete usage examples
   - **Limitations**: Known limitations or constraints

4. **Generate Documentation**:
   - **Determine output location**:
     - Check if `docs/api/` exists → use it
     - Otherwise check if `docs/` exists → create `docs/api/`
     - If no `docs/` directory → ask user where to save the documentation
   - Filename format: `api-{name}.md`
   - Use a consistent, user-friendly format
   - Include code examples
   - Cross-reference related APIs

5. **Summary**: Provide a brief overview in the chat

## Output Format

```markdown
# API Reference: {API Name}

**Header**: `path/to/header.h`
**Since**: Version X.X
**Status**: Stable / Experimental / Deprecated

## Overview

Brief description of what this API provides and when to use it.

## Quick Start

```c
// Minimal example showing basic usage
#include "header.h"

int main() {
    // Example code
}
```

## Data Types

### `struct api_object`

```c
typedef struct {
    int field1;        // Description
    char *field2;      // Description
} api_object;
```

**Fields**:
- `field1`: Description of field1
- `field2`: Description of field2, ownership rules

### `enum api_status`

```c
typedef enum {
    API_SUCCESS = 0,
    API_ERROR = -1
} api_status;
```

**Values**:
- `API_SUCCESS`: Successful operation
- `API_ERROR`: Operation failed

## Constants & Macros

### `API_MAX_SIZE`

```c
#define API_MAX_SIZE 4096
```

Maximum buffer size for API operations.

## Functions

### Core Functions

#### `api_init()`

```c
int api_init(api_object *obj, const char *config);
```

**Description**: Initializes an API object with the given configuration.

**Parameters**:
- `obj`: Pointer to uninitialized object (non-NULL)
- `config`: Configuration string (can be NULL for defaults)

**Returns**:
- `0`: Success
- `-1`: Invalid parameters
- `-2`: Initialization failed

**Example**:
```c
api_object obj;
if (api_init(&obj, NULL) < 0) {
    // Handle error
}
```

**Notes**:
- Must be called before any other API functions
- Object must be cleaned up with `api_cleanup()`
- Thread-safe

**See Also**: `api_cleanup()`

---

#### `api_cleanup()`

```c
void api_cleanup(api_object *obj);
```

**Description**: Cleans up and releases resources associated with an API object.

**Parameters**:
- `obj`: Pointer to initialized object (non-NULL)

**Returns**: None

**Example**:
```c
api_cleanup(&obj);
```

**Notes**:
- Safe to call multiple times
- Object should not be used after cleanup
- Automatically called on program exit (if registered)

---

## Usage Patterns

### Pattern 1: Basic Usage

```c
api_object obj;

// Initialize
if (api_init(&obj, NULL) < 0) {
    return -1;
}

// Use the API
api_do_something(&obj);

// Cleanup
api_cleanup(&obj);
```

### Pattern 2: Error Handling

```c
int result = api_operation(&obj);
if (result < 0) {
    const char *error = api_get_error(&obj);
    fprintf(stderr, "Error: %s\n", error);
}
```

## Memory Management

- **Allocation**: How memory is allocated (malloc, custom allocator, etc.)
- **Ownership**: Who owns returned data; caller's responsibility to free
- **Lifetime**: When objects/data remain valid

## Error Handling

- Functions return negative values on error
- Use `api_get_error()` to retrieve error message
- Error strings are valid until next API call

## Thread Safety

- Functions marked 🔒 are thread-safe
- Functions marked ⚠️ require external synchronization
- Object initialization/cleanup is NOT thread-safe

## Performance Notes

- Function X is O(n) where n is...
- Caching is used for...
- Avoid calling Y in tight loops

## Examples

### Complete Example

```c
// Full working example showing typical usage
#include "api-header.h"

int main() {
    // ... complete example ...
}
```

## Migration Guide

### From Version 1.x to 2.x

- `old_function()` renamed to `new_function()`
- Parameter order changed in `api_init()`

## Limitations

- Maximum of N concurrent connections
- Does not support IPv6 (yet)
- Not suitable for...

## Related APIs

- [Memory API](api-memory.md) - Memory management functions
- [Logging API](api-logging.md) - Logging integration

## Internal Implementation Notes

*For developers working on this API*:

- Implementation in `src/path/to/impl.c`
- See `docs/analysis/subsystem-X.md` for architecture
- Key data structures documented in `analysis-module-Y.md`
```

## Important Notes

- This is USER-FACING documentation - write for API consumers
- Be comprehensive but concise
- Include practical, working examples
- Document behavior, not implementation details
- Clearly mark stability (stable/experimental/deprecated)
- Cross-reference related documentation
- If API is very large, ask if user wants specific sections only

## Example Usage

```
/analyze-api HTTP Server API
/analyze-api memory management functions
/analyze-api src/include/api.h
/analyze-api Python bindings
```

---

**Start by asking the user which API/interface to analyze (if not already clear from context).**
