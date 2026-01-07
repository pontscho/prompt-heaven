---
name: p:ctest.h
description: Expert guidance for using the custom ctest.h unit testing framework. This is a lightweight C testing framework, NOT CMake's CTest. Use when writing, running, or debugging C unit tests or integration tests with ctest.h. Activate when user mentions unit tests, CTEST/CTEST2 macros, ASSERT_* macros, test failures, or asks to write/fix/run C tests using this framework.
allowed-tools: Read, Grep, Glob, Bash, Edit, Write
---

# ctest.h Testing Framework Guide

## Overview

`ctest.h` is a lightweight C unit testing framework used in the POLUAH project. It's a single-header library located at `src/tests/ctest.h`.

**CRITICAL**: This is NOT CMake's CTest. This is a custom framework with its own macros and conventions.

## Test Structure

### Basic Test Without Data (CTEST)

Use `CTEST` when your test doesn't need shared data or setup/teardown:

```c
CTEST(suite_name, test_name)
{
	// Test implementation
	ASSERT_TRUE(1 == 1);
}
```

### Test With Shared Data (CTEST2)

Use `CTEST2` when tests need:
- Shared data structures
- Setup/teardown functions
- Resource initialization/cleanup

```c
// 1. Define the data structure
CTEST_DATA(suite_name)
{
	poluah_client_t *client;
	int socket_fd;
};

// 2. Define setup (runs before each test)
CTEST_SETUP(suite_name)
{
	data->client = poluah_client_create();
	data->socket_fd = -1;
}

// 3. Define teardown (runs after each test)
CTEST_TEARDOWN(suite_name)
{
	if (data->client != NULL) {
		poluah_client_destroy(data->client);
		data->client = NULL;
	}
	if (data->socket_fd != -1) {
		close(data->socket_fd);
	}
}

// 4. Write tests using CTEST2
CTEST2(suite_name, test_name)
{
	ASSERT_NOT_NULL(data->client);
	// Access data via 'data' pointer
}
```

**IMPORTANT**: Use CTEST2 macro if the test uses event data that was allocated during CTEST_SETUP, otherwise use CTEST.

## Assertion Macros

### Pointer Assertions
```c
ASSERT_NULL(ptr)              // ptr must be NULL
ASSERT_NOT_NULL(ptr)          // ptr must not be NULL
```

### Boolean Assertions
```c
ASSERT_TRUE(condition)        // condition must be true (non-zero)
ASSERT_FALSE(condition)       // condition must be false (zero)
```

### Integer Assertions
```c
ASSERT_EQUAL(expected, actual)           // signed integers must be equal
ASSERT_EQUAL_U(expected, actual)         // unsigned integers must be equal
ASSERT_NOT_EQUAL(expected, actual)       // signed integers must differ
ASSERT_NOT_EQUAL_U(expected, actual)     // unsigned integers must differ
ASSERT_INTERVAL(min, max, actual)        // actual must be in [min, max]
```

### String Assertions
```c
ASSERT_STR(expected, actual)             // C strings must match
ASSERT_WSTR(expected, actual)            // Wide strings must match
```

### Data Buffer Assertions
```c
ASSERT_DATA(exp_ptr, exp_size, real_ptr, real_size)  // Binary data comparison
```

### Floating Point Assertions
```c
ASSERT_DBL_NEAR(expected, actual)                    // Within 1e-4 tolerance
ASSERT_DBL_NEAR_TOL(expected, actual, tolerance)     // Custom tolerance
ASSERT_DBL_FAR(expected, actual)                     // Beyond 1e-4 tolerance
ASSERT_DBL_FAR_TOL(expected, actual, tolerance)      // Custom tolerance
```

### Unconditional Failure
```c
ASSERT_FAIL()                            // Always fails - shouldn't reach here
```

## Logging Functions

```c
CTEST_LOG(fmt, ...)    // Log informational message (blue)
CTEST_ERR(fmt, ...)    // Log error and abort test (yellow, doesn't return)
```

## Running Tests

### Build Tests First
```bash
cmake --build build
```

### Run All Tests in a Test Executable
```bash
build/src/tests/test-unit
build/src/tests/test-http-server-integration2
build/src/tests/test-websocket-server-integration2
```

### Run Specific Suite
```bash
build/src/tests/test-unit suite_name
```

### Run Specific Test
```bash
build/src/tests/test-unit suite_name:test_name
```

**Example**:
```bash
build/src/tests/test-unit websocket_server:connect_invalid_endpoint
```

## Test File Structure Template

```c
/**
 * @file module-name-test.c
 * @brief Unit tests for module-name
 */

#include <string.h>
#include <errno.h>

#include "../ctest.h"
#include "../helpers.h"
#include "module/module-name.h"

//
// Test suite data structure
//

CTEST_DATA(module_name)
{
	module_t *instance;
	char *buffer;
};

//
// Setup function - called before each test
//

CTEST_SETUP(module_name)
{
	data->instance = NULL;
	data->buffer = NULL;
}

//
// Teardown function - called after each test
//

CTEST_TEARDOWN(module_name)
{
	if (data->instance != NULL) {
		module_destroy(data->instance);
		data->instance = NULL;
	}
	if (data->buffer != NULL) {
		poluah_free(data->buffer);
		data->buffer = NULL;
	}
}

//
// 1. Creation and Destruction Tests
//

CTEST2(module_name, create_success)
{
	data->instance = module_create();
	ASSERT_NOT_NULL(data->instance);
}

CTEST2(module_name, create_with_null_config)
{
	data->instance = module_create_with_config(NULL);
	ASSERT_NULL(data->instance);
}

//
// 2. Functionality Tests
//

CTEST2(module_name, process_valid_data)
{
	data->instance = module_create();
	ASSERT_NOT_NULL(data->instance);

	int result = module_process(data->instance, "test data");
	ASSERT_EQUAL(0, result);
}

// Simple test without shared data
CTEST(module_name, utility_function)
{
	int result = module_utility_function(42);
	ASSERT_EQUAL(84, result);
}
```

## Common Patterns and Best Practices

### 1. Test Independence
Each test must be independent and not rely on the state of other tests.

### 2. Resource Cleanup
Always clean up resources in CTEST_TEARDOWN to prevent memory leaks:
```c
CTEST_TEARDOWN(suite_name)
{
	if (data->resource != NULL) {
		cleanup_resource(data->resource);
		data->resource = NULL;
	}
}
```

### 3. NULL Safety
Always check for NULL before cleanup:
```c
if (data->client != NULL) {
	poluah_client_destroy(data->client);
	data->client = NULL;
}
```

### 4. Test Naming
- Use descriptive test names: `create_with_invalid_url` not `test1`
- Group tests by functionality with comments
- Suite name should match module name (use snake_case)

### 5. Assertion Order
Put expected value first, actual value second:
```c
ASSERT_EQUAL(expected_value, actual_value);  // CORRECT
ASSERT_EQUAL(actual_value, expected_value);  // WRONG
```

### 6. Skip Tests
Use `CTEST_SKIP` or `CTEST2_SKIP` to skip tests:
```c
CTEST_SKIP(suite_name, test_name)
{
	// This test will be skipped
}
```

## Integration Tests vs Unit Tests

### Unit Tests
- Test individual functions in isolation
- Mock external dependencies
- Fast execution
- Located in `src/tests/client/`, `src/tests/lua/`

### Integration Tests
- Test component interactions
- Use real servers/clients
- May require network/timeouts
- Located in `src/tests/integration/`

## Debugging Test Failures

### 1. Run Single Test
```bash
build/src/tests/test-unit suite_name:failing_test
```

### 2. Add Logging
```c
CTEST_LOG("Variable value: %d", variable);
```

### 3. Check Test Output
Test failures show:
- File and line number
- Expected vs actual values
- Custom error messages

### 4. Verify Setup/Teardown
Ensure resources are properly initialized in CTEST_SETUP

### 5. Check for Memory Issues
Run with valgrind:
```bash
valgrind build/src/tests/test-unit suite_name:test_name
```

## Common Mistakes to Avoid

1. **Using CTEST instead of CTEST2**: If you use shared data, you MUST use CTEST2
2. **Forgetting teardown**: Always clean up resources
3. **Tests depend on order**: Each test must be independent
4. **Not checking NULL**: Always validate pointers before use
5. **Wrong assertion order**: Expected value comes first
6. **Using assert()**: NEVER use assert() - use ASSERT_* macros
7. **Confusing with CMake CTest**: This is NOT CMake's CTest - different tool entirely

## CMakeLists.txt Integration

Tests are defined in `src/tests/CMakeLists.txt`:

```cmake
add_executable(test-unit
	unit/module1-test.c
	unit/module2-test.c
	main.c
)

target_link_libraries(test-unit
	poluah
	${CMAKE_THREAD_LIBS_INIT}
)
```

The main.c file should include:
```c
#define CTEST_MAIN
#include "ctest.h"

int main(int argc, const char *argv[])
{
	return ctest_main(argc, argv);
}
```

## Quick Reference

| Task | Command/Macro |
|------|---------------|
| Define test without data | `CTEST(suite, name)` |
| Define test with data | `CTEST2(suite, name)` |
| Define shared data | `CTEST_DATA(suite)` |
| Define setup | `CTEST_SETUP(suite)` |
| Define teardown | `CTEST_TEARDOWN(suite)` |
| Run all tests | `build/src/tests/test-unit` |
| Run suite | `build/src/tests/test-unit suite` |
| Run single test | `build/src/tests/test-unit suite:test` |
| Log message | `CTEST_LOG(fmt, ...)` |
| Fail with message | `CTEST_ERR(fmt, ...)` |

## When to Use This Skill

Activate this skill when:
- Writing new C unit tests or integration tests
- Debugging test failures
- Running tests with specific suite/test names
- User asks about CTEST, CTEST2, ASSERT_* macros
- User asks "how do I test..." in C code
- Reviewing test code for correctness
- Setting up test infrastructure
- Understanding test output or failures

This skill ensures proper usage of the ctest.h framework following POLUAH project conventions.
