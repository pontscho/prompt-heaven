# CMake Quick Reference

Fast lookup for common patterns and syntax.

**For static linking, see [p:static-linking skill](../p:static-linking/SKILL.md)** - dedicated guide with automated tools.

## Static Linking (Quick)

```cmake
# Platform-specific static linking
if(UNIX AND NOT APPLE)
	set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
	set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
elseif(APPLE)
	set(CMAKE_FIND_LIBRARY_SUFFIXES .a)  # NO -static on macOS
endif()
```

Use `build-static.py --verify` from p:static-linking for automated builds.

## Library Finding Patterns

### Pattern: zlib (Homebrew-aware)
```cmake
if(APPLE)
	find_library(ZLIB_LIBRARY NAMES z REQUIRED
		HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib)
	find_path(ZLIB_INCLUDE_DIR zlib.h
		HINTS /usr/local/opt/zlib/include /opt/homebrew/opt/zlib/include)
	set(ZLIB_LIBRARIES ${ZLIB_LIBRARY})
	set(ZLIB_INCLUDE_DIRS ${ZLIB_INCLUDE_DIR})
else()
	pkg_check_modules(ZLIB REQUIRED zlib)
endif()
```

### Pattern: libpng (with fallback)
```cmake
if(APPLE)
	find_library(PNG_LIBRARY NAMES png16 png REQUIRED
		HINTS /usr/local/opt/libpng/lib /opt/homebrew/opt/libpng/lib)
	find_path(PNG_INCLUDE_DIR png.h PATH_SUFFIXES libpng16 libpng
		HINTS /usr/local/opt/libpng/include /opt/homebrew/opt/libpng/include)
	set(PNG_LIBRARIES ${PNG_LIBRARY})
	set(PNG_INCLUDE_DIRS ${PNG_INCLUDE_DIR})
else()
	pkg_check_modules(PNG libpng16)
	if(NOT PNG_FOUND)
		pkg_check_modules(PNG libpng)
	endif()
endif()
```

### Pattern: Optional dependency
```cmake
option(ENABLE_WEBP "Enable WebP support" ON)

if(ENABLE_WEBP)
	find_library(WEBP_LIBRARY NAMES webp)
	if(WEBP_LIBRARY)
		set(WEBP_FOUND TRUE)
		add_definitions(-DHAVE_WEBP)
	else()
		message(WARNING "libwebp not found, WebP support disabled")
		set(ENABLE_WEBP OFF)
	endif()
endif()

# Later in target_link_libraries:
if(WEBP_FOUND)
	target_link_libraries(myapp PRIVATE ${WEBP_LIBRARY})
endif()
```

## Target-Based Linking

### Modern linking (GOOD ✅)
```cmake
target_link_libraries(myapp PRIVATE
	${PNG_LIBRARIES}
	${ZLIB_LIBRARIES}
)

target_include_directories(myapp PRIVATE
	${PNG_INCLUDE_DIRS}
	${ZLIB_INCLUDE_DIRS}
)

target_compile_options(myapp PRIVATE
	-Wall -Wextra -O3
)

target_compile_definitions(myapp PRIVATE
	ENABLE_FEATURE=1
)
```

### Legacy (BAD ❌)
```cmake
link_libraries(${PNG_LIBRARIES})        # Don't use
include_directories(${PNG_INCLUDE_DIRS})  # Don't use
add_compile_options(-Wall)              # Don't use
add_definitions(-DENABLE_FEATURE=1)     # Don't use
```

## Platform Detection

```cmake
if(UNIX AND NOT APPLE)
	# Linux
endif()

if(APPLE)
	# macOS
endif()

if(WIN32)
	# Windows
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
	# Linux (more specific)
endif()

if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64")
	# x86-64
endif()

if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
	# ARM64
endif()
```

## Platform-Specific Linker Flags

```cmake
# Stack size
if(UNIX AND NOT APPLE)
	set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-z,stack-size=8388608")
elseif(APPLE)
	set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-stack_size,0x800000")
elseif(WIN32)
	set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} /STACK:8388608")
endif()

# Math library (Linux only)
if(UNIX AND NOT APPLE)
	target_link_libraries(myapp PRIVATE m pthread)
endif()
```

## Compiler Flags

```cmake
# GCC/Clang
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
	target_compile_options(myapp PRIVATE
		-Wall -Wextra -Werror
		-O3 -g
		-march=native
	)
endif()

# MSVC
if(MSVC)
	target_compile_options(myapp PRIVATE
		/W4 /WX /O2
	)
endif()

# Generator expressions (advanced)
target_compile_options(myapp PRIVATE
	$<$<C_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
	$<$<C_COMPILER_ID:MSVC>:/W4>
	$<$<CONFIG:Debug>:-g3 -O0>
	$<$<CONFIG:Release>:-O3 -DNDEBUG>
)
```

## Build Types

```cmake
# Set default build type
if(NOT CMAKE_BUILD_TYPE)
	set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()

# Available types:
# - Debug: No optimization, debug symbols
# - Release: Full optimization, no debug symbols
# - RelWithDebInfo: Optimization + debug symbols
# - MinSizeRel: Optimize for size
```

## Configuration Summary

```cmake
message(STATUS "")
message(STATUS "Build Configuration")
message(STATUS "===================")
message(STATUS "Platform: ${CMAKE_SYSTEM_NAME}")
message(STATUS "Processor: ${CMAKE_SYSTEM_PROCESSOR}")
message(STATUS "Compiler: ${CMAKE_C_COMPILER_ID} ${CMAKE_C_COMPILER_VERSION}")
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "")
message(STATUS "Dependencies:")
message(STATUS "  libpng: ${PNG_LIBRARIES}")
message(STATUS "  zlib:   ${ZLIB_LIBRARIES}")
message(STATUS "")
message(STATUS "Optional Features:")
message(STATUS "  WebP: ${ENABLE_WEBP}")
message(STATUS "  HEIF: ${ENABLE_HEIF}")
message(STATUS "")
```

## Testing Setup

```cmake
option(BUILD_TESTING "Build tests" ON)

if(BUILD_TESTING)
	enable_testing()

	# Create test library (no main.c)
	add_library(mylib STATIC
		src/core.c
		src/utils.c
	)

	# Main app
	add_executable(myapp
		src/main.c
	)
	target_link_libraries(myapp PRIVATE mylib)

	# Test executable
	add_executable(mytest
		tests/test_core.c
	)
	target_link_libraries(mytest PRIVATE mylib)

	# Register test
	add_test(NAME mytest COMMAND mytest)
endif()
```

## Installation

```cmake
# Simple installation
install(TARGETS myapp DESTINATION bin)

# Advanced installation (library + headers)
include(GNUInstallDirs)

install(TARGETS mylib myapp
	EXPORT mylibTargets
	LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
	ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
	RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
	PUBLIC_HEADER DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)

# Install headers
install(DIRECTORY include/
	DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
	FILES_MATCHING PATTERN "*.h"
)
```

## Cross-Compilation

**Toolchain file** (`toolchain.cmake`):
```cmake
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
```

**Usage**:
```bash
cmake -B build -DCMAKE_TOOLCHAIN_FILE=toolchain.cmake
cmake --build build
```

## Generator Expressions

```cmake
# Conditional compilation
target_compile_definitions(myapp PRIVATE
	$<$<CONFIG:Debug>:DEBUG_MODE>
	$<$<CONFIG:Release>:NDEBUG>
	$<$<PLATFORM_ID:Linux>:PLATFORM_LINUX>
	$<$<PLATFORM_ID:Darwin>:PLATFORM_MACOS>
	$<$<PLATFORM_ID:Windows>:PLATFORM_WINDOWS>
)

# Conditional flags
target_compile_options(myapp PRIVATE
	$<$<C_COMPILER_ID:GNU>:-fno-strict-aliasing>
	$<$<C_COMPILER_ID:Clang>:-Wno-deprecated>
	$<$<C_COMPILER_ID:MSVC>:/wd4996>
)

# Conditional linking
target_link_libraries(myapp PRIVATE
	$<$<PLATFORM_ID:Linux>:pthread>
	$<$<PLATFORM_ID:Windows>:ws2_32>
)
```

## Common Homebrew Paths (macOS)

```cmake
# Intel Mac: /usr/local
# Apple Silicon: /opt/homebrew

# Generic pattern:
find_library(LIB_NAME NAMES libname
	HINTS
		/usr/local/opt/package/lib
		/opt/homebrew/opt/package/lib
		/usr/local/lib
		/opt/homebrew/lib
)

find_path(LIB_INCLUDE_DIR header.h
	HINTS
		/usr/local/opt/package/include
		/opt/homebrew/opt/package/include
		/usr/local/include
		/opt/homebrew/include
)
```

## Verification Commands

**Linux**:
```bash
# Check if static
ldd ./myapp
# Expected: "not a dynamic executable"

# Check symbols
nm -D ./myapp

# Check size
ls -lh ./myapp
```

**macOS**:
```bash
# Check dependencies
otool -L ./myapp
# Expected: Only /usr/lib/libSystem.B.dylib

# Check architecture
file ./myapp

# Check symbols
nm -g ./myapp
```

**Windows**:
```powershell
# Check dependencies
dumpbin /dependents myapp.exe

# Check imports
dumpbin /imports myapp.exe
```

## Common Errors

### Error: "library not found for -lcrt0.o" (macOS)
**Cause**: Using `-static` flag on macOS

**Fix**: Remove `-static` for macOS:
```cmake
if(UNIX AND NOT APPLE)
	set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
elseif(APPLE)
	# Don't use -static on macOS
	set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
endif()
```

### Error: "Could NOT find ZLIB (missing: ZLIB_LIBRARY)"
**Cause**: `CMAKE_FIND_LIBRARY_SUFFIXES .a` set too early

**Fix**: Add Homebrew hints:
```cmake
find_library(ZLIB_LIBRARY NAMES z REQUIRED
	HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib)
```

### Error: pkg-config returns dynamic libraries
**Cause**: pkg-config defaults to dynamic libs

**Fix**: Use explicit find_library on macOS:
```cmake
if(APPLE)
	find_library(PNG_LIBRARY NAMES png16 png REQUIRED)
else()
	pkg_check_modules(PNG REQUIRED libpng16)
endif()
```

## Minimal Template

```cmake
cmake_minimum_required(VERSION 3.15)
project(myapp VERSION 1.0.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Static linking
if(UNIX AND NOT APPLE)
	set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
	set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
elseif(APPLE)
	set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
endif()

# Dependencies
find_package(PkgConfig REQUIRED)
pkg_check_modules(DEPS REQUIRED zlib libpng)

# Executable
add_executable(myapp src/main.c)

target_link_libraries(myapp PRIVATE ${DEPS_LIBRARIES})
target_include_directories(myapp PRIVATE ${DEPS_INCLUDE_DIRS})
target_compile_options(myapp PRIVATE -Wall -Wextra -O3)

install(TARGETS myapp DESTINATION bin)
```
