---
name: p:c-cpp-guidelines
description: General C and C++ coding best practices including memory management (malloc/free, RAII, smart pointers), code style (tab indentation, snake_case, const correctness, early returns), Doxygen documentation with @ parameters, performance optimization, and code quality verification. Use when writing or editing C/C++ source files (.c, .cpp, .h, .hpp), implementing memory allocation, documenting functions/structures, or reviewing code quality.
---

### 0. Code Implementation Guidelines

Follow these rules when you write code:

- Use early returns whenever possible to make the code more readable
- When ID is part of a variable the expected naming is 'id' (e.g. motivation_id)
- Prefer to add the const keyword to function signatures
- Prefer to add the const keyword to function parameters
- Use tab for indentation, DON'T USE SPACE for it
- Keep code style of the source code in current file
- Always use extern keyword for public functions in header files

### 1. Code Documentation Guidelines
- Use the following format for documenting classes and functions: Doxygen style
- Always use @ for doxygen parameters
- For function documentation use /**
- For structure field documentation use /*<
- For member variables add one line description after the member variable definition

### 2. **Memory Management (C/C++)**

#### General Principles
- Watch for memory leaks (malloc/free, new/delete matching)
- Prevent buffer overflows and underruns
- Avoid dangling pointers and use-after-free issues
- Be mindful of stack overflow potential
- Use RAII for resource cleanup (C++)
- Choose smart pointers appropriately (C++11+: unique_ptr, shared_ptr, weak_ptr)

#### C Memory Management
- **Standard allocation functions**: `malloc()`, `calloc()`, `realloc()`, `free()`
- **String functions**: `strdup()`, `strndup()`
- **Memory operations**: `memset()`, `memcpy()`, `memmove()`, `memcmp()`, `memchr()`
- Always match allocation with deallocation:
  ```c
  char *buf = malloc(size);
  // use buf
  free(buf);
  buf = NULL;  // Good practice
  ```
- Use `calloc()` for zero-initialized memory:
  ```c
  int *array = calloc(count, sizeof(int));  // Zero-initialized
  ```
- Check allocation return values (unless using custom error handling):
  ```c
  void *ptr = malloc(size);
  if (ptr == NULL) {
      // Handle allocation failure
      return ERROR;
  }
  ```

#### C++ Memory Management
- Prefer RAII and smart pointers over raw pointers:
  ```cpp
  std::unique_ptr<MyClass> obj = std::make_unique<MyClass>();
  std::shared_ptr<Data> data = std::make_shared<Data>();
  ```
- Use `new`/`delete` matching when raw pointers are needed:
  ```cpp
  MyClass *obj = new MyClass();
  delete obj;
  ```
- Use `new[]`/`delete[]` for arrays:
  ```cpp
  int *array = new int[size];
  delete[] array;
  ```

#### Best Practices
- **Initialize variables** on declaration:
  ```c
  char *str = NULL;
  int count = 0;
  MyStruct *ptr = NULL;
  ```
- **NULL pointer checking**:
  - Use `if (ptr)` to check if pointer is non-NULL
  - Use `if (ptr == NULL)` to check if pointer is NULL
- **Design for NULL safety**: Functions should handle NULL pointers gracefully where appropriate
- **Set pointers to NULL after freeing**:
  ```c
  free(ptr);
  ptr = NULL;  // Prevents use-after-free
  ```

### 3. **Performance & Efficiency**
- Algorithm complexity analysis
- Avoid unnecessary computation, copies, heap allocations and temporary objects
- Move semantics usage (C++11+)
- Cache-friendly memory patterns
- Loop optimizations

### 4. **Code Style & Standards**
- Tab indentation (no spaces)
- Consistent naming conventions (_id suffix for IDs)
- Const correctness (parameters and methods)
- Early returns usage
- DRY principle adherence
- File structure and organization
- Variable naming conventions (snake_case)
- Prefer modern compilers with good standards support (GCC, Clang, MSVC)
- Avoid using assert() in production code, use proper error handling instead

## 5. Coding guidelines
- Follow the source code style of the current file
- Use // for single line comments and /** */ for multi-line comments
- Use snake_case for variable names and function names
- Use lowercase for hexadecimal numbers (e.g., 0x1a2b instead of 0x1A2B)
- Use thread-safe functions (e.g., `strerror_r()` instead of `strerror()`)

## 5.1. Code Quality Verification

### Static Analysis Tools
- **clang-tidy**: Modern C/C++ linter and static analyzer
  ```bash
  # With compilation database
  clang-tidy -p build <file_path>

  # Without compilation database
  clang-tidy <file_path> -- -I./include

  # Auto-fix issues
  clang-tidy -fix <file_path>
  ```
- **cppcheck**: Additional static analysis tool
  ```bash
  cppcheck --enable=all --inconclusive <file_path>
  ```
- **Address Sanitizer (ASan)**: Runtime memory error detection
  ```bash
  # Compile with: -fsanitize=address -g
  ```
- **Valgrind**: Memory leak detection (Linux)
  ```bash
  valgrind --leak-check=full ./program
  ```

### Best Practices
- Run static analysis tools regularly during development
- Fix warnings before committing code
- Use sanitizers during testing
- Consider integrating tools into CI/CD pipeline

## 6. Lua C API
- lua_pushlightuserdata() can push NULL values
- When push variable to Lua stack for variable names use camelCase

## 7. Header Template
```c
/**
 * @file module_name.h
 * @brief Brief description of the module
 */

#pragma once

#ifndef __MODULE_NAME_H_
#define __MODULE_NAME_H_

#include <stdint.h>
#include <stdbool.h>

// Type definitions
typedef struct module_name_s module_name_t;

// Function declarations
/**
 * Brief description of the function.
 *
 * @param config Configuration string or structure
 *
 * @return Pointer to the created module instance, or NULL on error
 */
extern module_name_t* module_name_create(const char* config);

#endif // __MODULE_NAME_H_
```

## 7. Module Template
```c
/**
 * @file module_name.c
 * @brief Brief description of the module
 */

#include <stdint.h>
#include <stdbool.h>

// Type definitions
typedef struct module_name_s module_name_t;

// Function declarations
/**
 * Brief description of the function.
 *
 * @param config Configuration string or structure
 *
 * @return Pointer to the created module instance, or NULL on error
 */
module_name_t *module_name_create(const char *config)
{
	// Implementation of the function
	return NULL; // Placeholder return value
}
```

## 8. Common Patterns

### Create/Destroy Pattern
```c
// Header (.h)
typedef struct object_t object_t;
extern object_t *object_create(void);
extern void object_destroy(object_t *obj);

// Implementation (.c)
struct object_t {
	char *name;
	int value;
};

object_t *object_create(void)
{
	object_t *obj = calloc(1, sizeof(object_t));
	if (obj == NULL)
		return NULL;

	obj->name = strdup("default");
	obj->value = 0;
	return obj;
}

void object_destroy(object_t *obj)
{
	if (obj == NULL)
		return;

	free(obj->name);
	free(obj);
}
```

### Error Handling Pattern
```c
int function_with_error_handling(void)
{
	// Early return for error cases
	if (error_condition)
		return -1;

	if (another_error)
		return -2;

	// Success path
	return 0;
}
```

### Resource Management (RAII in C++)
```cpp
class Resource {
public:
	Resource() { /* acquire resource */ }
	~Resource() { /* release resource */ }

	// Disable copy, enable move
	Resource(const Resource&) = delete;
	Resource& operator=(const Resource&) = delete;
	Resource(Resource&&) = default;
	Resource& operator=(Resource&&) = default;
};
```

## Summary

This skill provides general C/C++ coding best practices applicable to any project:
- **Memory management**: Standard libc/C++ memory operations
- **Code style**: Tab indentation, snake_case, const correctness
- **Documentation**: Doxygen with @ parameters
- **Quality**: Static analysis tools (clang-tidy, cppcheck, sanitizers)
- **Templates**: Standard header and module patterns

For project-specific APIs and conventions, refer to project documentation.
