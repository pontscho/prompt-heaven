---
name: p:cmake
description: Modern CMake best practices including cross-platform builds, static linking ( Linux full static, macOS hybrid ), dependency management with pkg-config and find_library, target-based configuration, and platform-specific patterns. Use when writing CMakeLists.txt, configuring builds, linking libraries, or setting up cross-platform C/C++ projects.
---

# CMake Best Practices

Comprehensive guide for modern CMake with platform-specific static linking, dependency management, and cross-platform builds.

## Quick Start

**Validate your CMakeLists.txt** (recommended first step):
```bash
python3 cmake-validator.py CMakeLists.txt
```
See [cmake-validator.md](./cmake-validator.md) for automatic validation of legacy patterns.

**Quick references**:
- [template.cmake](./template.cmake) - Copy-paste ready template for new projects
- [quick-reference.md](./quick-reference.md) - Fast lookup for common patterns
- [examples.md](./examples.md) - Complete real-world examples (imgcat2, CLI tools, libraries, cross-compilation)

**Modern CMake template**:
```cmake
cmake_minimum_required( VERSION 3.15 )
project( myproject VERSION 1.0.0 LANGUAGES C CXX )

# Standard settings
set( CMAKE_C_STANDARD 11 )
set( CMAKE_CXX_STANDARD 17 )
set( CMAKE_EXPORT_COMPILE_COMMANDS ON )

# Platform-specific static linking
if( UNIX AND NOT APPLE )
	# Linux: Full static linking
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )

elseif( APPLE )
	# macOS: Static third-party libs, dynamic system libs
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
endif()

# Find dependencies
find_package( PkgConfig REQUIRED )
pkg_check_modules( MYLIB REQUIRED mylib )

# Create executable
add_executable( myapp main.c )

# Modern target-based linking
target_link_libraries( myapp PRIVATE ${MYLIB_LIBRARIES} )
target_include_directories( myapp PRIVATE ${MYLIB_INCLUDE_DIRS} )
```

## Core Principles

### 1. Modern Target-Based CMake

**Always prefer target-based commands** over directory-level commands:

✅ **Good** ( Modern CMake ):
```cmake
target_link_libraries( myapp PRIVATE pthread )
target_include_directories( myapp PRIVATE ${CMAKE_SOURCE_DIR}/include )
target_compile_options( myapp PRIVATE -Wall -Wextra )
target_compile_definitions( myapp PRIVATE ENABLE_FEATURE=1 )
```

❌ **Bad** ( Legacy CMake ):
```cmake
link_libraries( pthread ) # Global, affects all targets
include_directories( ${CMAKE_SOURCE_DIR}/include )
add_compile_options( -Wall -Wextra )
add_definitions( -DENABLE_FEATURE=1 )
```

**Visibility keywords**:
- `PRIVATE`: Only this target needs it
- `PUBLIC`: This target and consumers need it
- `INTERFACE`: Only consumers need it ( header-only libs )

### 2. Static Linking (Brief)

**For complete static linking guide, see [p:static-linking skill](../p:static-linking/SKILL.md)**

Quick example for platform-specific static linking:

```cmake
# Platform-specific static linking
if( UNIX AND NOT APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )
elseif( APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )  # NO -static flag on macOS!
elseif( WIN32 )
	set( CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" )
endif()
```

**Note**: macOS requires system libraries dynamic. See **p:static-linking** skill for:
- Platform-specific configurations
- Automated build tools (`build-static.py`)
- Verification tools (`verify-static-linking.py`)
- Troubleshooting and best practices

### 3. Dependency Management

**Strategy hierarchy** ( real-life pattern ):

```cmake
# 1. Try pkg-config first ( best for static libs )
find_package( PkgConfig REQUIRED )

# 2. Platform-specific library finding
if( APPLE )
	# macOS: Explicit static library search with Homebrew paths
	find_library( ZLIB_LIBRARY NAMES z REQUIRED HINTS /usr/local/opt/zlib/lib /usr/local/lib /opt/homebrew/lib )
	find_path( ZLIB_INCLUDE_DIR zlib.h HINTS /usr/local/opt/zlib/include /usr/local/include /opt/homebrew/include )
	set( ZLIB_LIBRARIES ${ZLIB_LIBRARY} )
	set( ZLIB_INCLUDE_DIRS ${ZLIB_INCLUDE_DIR} )

else()
	# Linux/Windows: Use pkg-config
	pkg_check_modules( ZLIB REQUIRED zlib )
endif()

# 3. Fallback to find_package
if( NOT ZLIB_FOUND )
	find_package( ZLIB REQUIRED )
endif()
```

**Why this order?**
- `pkg-config` respects `--static` flags and handles transitive deps
- Explicit `find_library` with `.a` suffix forces static linking
- `find_package` is the fallback ( may return dynamic libs )

### 4. Cross-Platform Patterns

**Platform detection**:
```cmake
if( UNIX AND NOT APPLE )
	# Linux-specific
	message( STATUS "Platform: Linux" )

elseif( APPLE )
	# macOS-specific
	message( STATUS "Platform: macOS" )

elseif( WIN32 )
	# Windows-specific
	message( STATUS "Platform: Windows" )
endif()

# More specific detection
if( CMAKE_SYSTEM_NAME STREQUAL "Linux" )
	# Linux-only

elseif( CMAKE_SYSTEM_NAME STREQUAL "Darwin" )
	# macOS-only
endif()
```

**Platform-specific linker flags**:
```cmake
if( UNIX AND NOT APPLE )
	# Linux: Set stack size
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-z,stack-size=8388608" )
	# Linux: Link math library
	target_link_libraries( myapp PRIVATE m )

elseif( APPLE )
	# macOS: Set stack size
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-stack_size,0x800000" )

elseif( WIN32 )
	# Windows: Set stack size
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} /STACK:8388608" )
endif()
```

### 5. Compiler Flags

**Recommended pattern**:
```cmake
# Compiler flags
if( CMAKE_C_COMPILER_ID MATCHES "GNU|Clang" )
	add_compile_options(
		-Wall                    # All warnings
		-Wextra                  # Extra warnings
		-Werror                  # Warnings as errors
		-O3                      # Max optimization
		-g                       # Debug symbols
	 )

elseif( MSVC )
	add_compile_options(
		/W4                      # Warning level 4
		/WX                      # Warnings as errors
		/O2                      # Optimize for speed
	 )
endif()

# Target-specific flags ( preferred )
target_compile_options( myapp PRIVATE
	$<$<C_COMPILER_ID:GNU,Clang>:-Wno-error=unused-but-set-variable>
	$<$<C_COMPILER_ID:MSVC>:/wd4100>
 )
```

**Generator expressions** for conditional compilation:
```cmake
target_compile_definitions( myapp PRIVATE
	$<$<CONFIG:Debug>:DEBUG_MODE>
	$<$<CONFIG:Release>:NDEBUG>
	$<$<PLATFORM_ID:Linux>:PLATFORM_LINUX>
	$<$<PLATFORM_ID:Darwin>:PLATFORM_MACOS>
 )
```

## Instructions

When helping with CMake, follow these steps:

### Step 1: Assess the project requirements

1. **Ask about linking strategy**:
   - Do they need static or dynamic linking?
   - Is this cross-platform?
   - Are there specific libraries to link?

2. **Identify platforms**:
   - Linux only? macOS? Windows?
   - Cross-compilation needed?

3. **Check dependencies**:
   - System libraries ( pthread, m, dl )?
   - Third-party libraries ( zlib, openssl, etc. )?
   - Homebrew/vcpkg/conan package manager?

### Step 2: Set up static linking (if needed)

**For complete static linking setup, use [p:static-linking skill](../p:static-linking/SKILL.md)**

Quick pattern:
```cmake
if( UNIX AND NOT APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )
elseif( APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )  # NO -static on macOS
endif()
```

**Use p:static-linking tools**:
- `build-static.py --verify` - Automated build with verification
- `verify-static-linking.py` - Verify binaries post-build

### Step 3: Configure dependency finding

For each library:

1. **On macOS, use explicit find_library**:
```cmake
if( APPLE )
	find_library( PNG_LIBRARY NAMES png16 png REQUIRED HINTS /usr/local/opt/libpng/lib /usr/local/lib /opt/homebrew/lib )
	find_path( PNG_INCLUDE_DIR png.h HINTS /usr/local/opt/libpng/include /usr/local/include /opt/homebrew/include )
	set( PNG_LIBRARIES ${PNG_LIBRARY} )
	set( PNG_INCLUDE_DIRS ${PNG_INCLUDE_DIR} )

else()
	pkg_check_modules( PNG REQUIRED libpng16 )
endif()
```

2. **Use pkg-config for Linux**:
```cmake
pkg_check_modules( PNG REQUIRED libpng16 )
```

3. **Always provide a fallback**:
```cmake
if( NOT PNG_FOUND )
	find_package( PNG REQUIRED )
endif()
```

### Step 4: Use modern target-based linking

**Never use directory-level commands**. Always use:

```cmake
target_link_libraries( myapp PRIVATE ${PNG_LIBRARIES} )
target_include_directories( myapp PRIVATE ${PNG_INCLUDE_DIRS} )
```

**Use PRIVATE/PUBLIC/INTERFACE correctly**:
- Executable: Always `PRIVATE`
- Static library used by others: `PUBLIC` or `INTERFACE`
- Static library not used by others: `PRIVATE`

### Step 5: Add configuration summary

Always add a summary for debugging:

```cmake
message( STATUS "" )
message( STATUS "Build Configuration" )
message( STATUS "===================" )
message( STATUS "Platform: ${CMAKE_SYSTEM_NAME}" )
message( STATUS "Compiler: ${CMAKE_C_COMPILER_ID} ${CMAKE_C_COMPILER_VERSION}" )
message( STATUS "Build type: ${CMAKE_BUILD_TYPE}" )
message( STATUS "Static linking: ${STATIC_LINKING}" )
message( STATUS "" )
message( STATUS "Dependencies:" )
message( STATUS "  libpng: ${PNG_LIBRARIES}" )
message( STATUS "  zlib:   ${ZLIB_LIBRARIES}" )
message( STATUS "" )
```

### Step 6: Verify static linking

**Use p:static-linking verification tool** (cross-platform):
```bash
python3 verify-static-linking.py ./myapp
```

Or manual verification:
- **Linux**: `ldd ./myapp` → "not a dynamic executable"
- **macOS**: `otool -L ./myapp` → Only libSystem.B.dylib
- **Windows**: `dumpbin /dependents myapp.exe`

See **[p:static-linking skill](../p:static-linking/SKILL.md)** for automated workflows.

## Common Patterns

### Pattern 1: Optional Dependencies

```cmake
option( ENABLE_PNG "Enable PNG support" ON )

if( ENABLE_PNG )
	find_library( PNG_LIBRARY NAMES png16 png )
	find_path( PNG_INCLUDE_DIR png.h )
	if( PNG_LIBRARY AND PNG_INCLUDE_DIR )
		set( PNG_FOUND TRUE )
		add_definitions( -DHAVE_PNG )
		message( STATUS "PNG support: ENABLED" )

	else()
		set( ENABLE_PNG OFF )
		message( WARNING "libpng not found, PNG support disabled" )
	endif()

else()
	message( STATUS "PNG support: DISABLED" )
endif()

# Link only if found
if( PNG_FOUND )
	target_link_libraries( myapp PRIVATE ${PNG_LIBRARY} )
	target_include_directories( myapp PRIVATE ${PNG_INCLUDE_DIR} )
endif()
```

### Pattern 2: Multiple Library Variants

```cmake
# Try preferred variant first
pkg_check_modules( JPEG libjpeg-turbo )
if( NOT JPEG_FOUND )
	pkg_check_modules( JPEG libjpeg )
endif()

if( NOT JPEG_FOUND )
	# Fallback to find_package
	find_package( JPEG REQUIRED )
	set( JPEG_LIBRARIES ${JPEG_LIBRARIES} )
	set( JPEG_INCLUDE_DIRS ${JPEG_INCLUDE_DIRS} )
endif()
```

### Pattern 3: Static Library for Tests

```cmake
# Library without main()
add_library( mylib STATIC
	src/core.c
	src/utils.c
 )

target_include_directories( mylib PUBLIC
	${CMAKE_SOURCE_DIR}/include
 )

target_link_libraries( mylib PUBLIC
	${ZLIB_LIBRARIES}
	${PNG_LIBRARIES}
 )

# Executable with main()
add_executable( myapp
	src/main.c
 )

target_link_libraries( myapp PRIVATE mylib )

# Test executable
add_executable( mytest
	tests/test_core.c
 )

target_link_libraries( mytest PRIVATE mylib )
```

### Pattern 4: Cross-Compilation

```cmake
# Set toolchain before project()
set( CMAKE_SYSTEM_NAME Linux )
set( CMAKE_SYSTEM_PROCESSOR aarch64 )

set( CMAKE_C_COMPILER aarch64-linux-gnu-gcc )
set( CMAKE_CXX_COMPILER aarch64-linux-gnu-g++ )

# Find libraries for target system
set( CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu )
set( CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER )
set( CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY )
set( CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY )

project( myproject VERSION 1.0.0 LANGUAGES C CXX )
```

## Best Practices

### DO ✅

1. **Use target-based commands** ( target_link_libraries, target_include_directories )
2. **Platform-specific static linking** ( Linux full static, macOS hybrid )
3. **Explicit library search on macOS** with Homebrew paths
4. **Always use PRIVATE/PUBLIC/INTERFACE** visibility keywords
5. **Add configuration summary** messages for debugging
6. **Provide fallback** for find_library/pkg_check_modules
7. **Use generator expressions** for conditional configuration
8. **Set CMAKE_EXPORT_COMPILE_COMMANDS ON** for IDE support
9. **Document platform limitations** ( e.g., macOS static linking )
10. **Verify static linking** with ldd/otool after build

### DON'T ❌

1. **Don't use directory-level commands** ( link_libraries, include_directories )
2. **Don't use `-static` flag on macOS** ( will fail with crt0.o error )
3. **Don't rely only on pkg-config** on macOS ( may return dynamic libs )
4. **Don't use `CMAKE_FIND_LIBRARY_SUFFIXES .a`** before all find_library calls
5. **Don't use `${VAR}_LDFLAGS`** from pkg-config ( use `${VAR}_LIBRARIES` )
6. **Don't hardcode paths** - use HINTS/PATHS in find_library
7. **Don't skip fallback** - always provide find_package fallback
8. **Don't forget platform detection** - check APPLE/UNIX/WIN32
9. **Don't use global flags** - use target-specific options
10. **Don't assume full static works** on all platforms

## Troubleshooting

### Common CMake Issues

**Problem: Library not found**
- Check `CMakeCache.txt` for what CMake found
- Ensure development packages installed
- Add HINTS with correct paths (Homebrew: `/usr/local/opt`, `/opt/homebrew/opt`)

**Problem: Wrong library version linked**
- Use explicit `find_library` with `REQUIRED`
- Check `CMakeCache.txt` and clear cache if needed

**Problem: Homebrew libraries not found (macOS)**
```cmake
find_library( ZLIB_LIBRARY NAMES z REQUIRED
	HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib
)
```

### Static Linking Issues

**For static linking troubleshooting, see [p:static-linking skill](../p:static-linking/SKILL.md)**:
- macOS crt0.o error
- Dynamic dependencies remaining
- Linking order issues
- Platform-specific verification

## Examples

**For more complete, production-ready examples, see [examples.md](./examples.md)**:
- Full imgcat2-style multi-format image tool (complex real-world project)
- Simple static CLI tool
- Cross-platform library with optional features
- Cross-compilation for ARM/embedded
- Conditional features with auto-detection

### Example 1: Cross-platform CLI tool with static linking

```cmake
cmake_minimum_required( VERSION 3.15 )
project( mytool VERSION 1.0.0 LANGUAGES C )

set( CMAKE_C_STANDARD 11 )
set( CMAKE_C_STANDARD_REQUIRED ON )
set( CMAKE_EXPORT_COMPILE_COMMANDS ON )

# Platform-specific static linking
if( UNIX AND NOT APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )

elseif( APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
endif()

set( BUILD_SHARED_LIBS OFF CACHE BOOL "Build shared libraries" FORCE )

# Find dependencies
find_package( PkgConfig REQUIRED )

# zlib
if( APPLE )
	find_library( ZLIB_LIBRARY NAMES z REQUIRED HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib )
	find_path( ZLIB_INCLUDE_DIR zlib.h HINTS /usr/local/opt/zlib/include /opt/homebrew/opt/zlib/include )
	set( ZLIB_LIBRARIES ${ZLIB_LIBRARY} )
	set( ZLIB_INCLUDE_DIRS ${ZLIB_INCLUDE_DIR} )

else()
	pkg_check_modules( ZLIB REQUIRED zlib )
endif()

# libpng
if( APPLE )
	find_library( PNG_LIBRARY NAMES png16 png REQUIRED HINTS /usr/local/opt/libpng/lib /opt/homebrew/opt/libpng/lib )
	find_path( PNG_INCLUDE_DIR png.h HINTS /usr/local/opt/libpng/include /opt/homebrew/opt/libpng/include )
	set( PNG_LIBRARIES ${PNG_LIBRARY} )
	set( PNG_INCLUDE_DIRS ${PNG_INCLUDE_DIR} )

else()
	pkg_check_modules( PNG REQUIRED libpng16 )
endif()

# Create executable
add_executable( mytool
	src/main.c
	src/image.c
	src/utils.c
 )

# Modern target-based linking
target_link_libraries( mytool PRIVATE
	${PNG_LIBRARIES}
	${ZLIB_LIBRARIES}
 )

target_include_directories( mytool PRIVATE
	${CMAKE_SOURCE_DIR}/include
	${PNG_INCLUDE_DIRS}
	${ZLIB_INCLUDE_DIRS}
 )

# Platform-specific libs
if( UNIX AND NOT APPLE )
	target_link_libraries( mytool PRIVATE m pthread )
endif()

# Compiler flags
target_compile_options( mytool PRIVATE
	$<$<C_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Werror -O3>
	$<$<C_COMPILER_ID:MSVC>:/W4 /WX /O2>
 )

# Configuration summary
message( STATUS "" )
message( STATUS "Build Configuration" )
message( STATUS "===================" )
message( STATUS "Platform: ${CMAKE_SYSTEM_NAME}" )
message( STATUS "Compiler: ${CMAKE_C_COMPILER_ID}" )
message( STATUS "Build type: ${CMAKE_BUILD_TYPE}" )
message( STATUS "" )
message( STATUS "Dependencies:" )
message( STATUS "  libpng: ${PNG_LIBRARIES}" )
message( STATUS "  zlib:   ${ZLIB_LIBRARIES}" )
message( STATUS "" )

# Install
install( TARGETS mytool DESTINATION bin )
```

**Usage**:
```bash
# Configure
cmake -B build -DCMAKE_BUILD_TYPE=Release

# Build
cmake --build build

# Verify static linking
# Linux:
ldd build/mytool

# macOS:
otool -L build/mytool
# Should only show: /usr/lib/libSystem.B.dylib
```

### Example 2: Library with optional features

```cmake
cmake_minimum_required( VERSION 3.15 )
project( mylib VERSION 2.0.0 LANGUAGES C )

# Build options
option( ENABLE_PNG "Enable PNG support" ON )
option( ENABLE_JPEG "Enable JPEG support" ON )
option( BUILD_SHARED_LIBS "Build shared libraries" OFF )
option( BUILD_TESTING "Build tests" ON )

# Static linking
if( UNIX AND NOT APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )

elseif( APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
endif()

find_package( PkgConfig REQUIRED )

# zlib ( required )
if( APPLE )
	find_library( ZLIB_LIBRARY NAMES z REQUIRED HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib )
	set( ZLIB_LIBRARIES ${ZLIB_LIBRARY} )

else()
	pkg_check_modules( ZLIB REQUIRED zlib )
endif()

# PNG ( optional )
if( ENABLE_PNG )
	if( APPLE )
		find_library( PNG_LIBRARY NAMES png16 png HINTS /usr/local/opt/libpng/lib /opt/homebrew/opt/libpng/lib )
		if( PNG_LIBRARY )
			set( PNG_FOUND TRUE )
			set( PNG_LIBRARIES ${PNG_LIBRARY} )
		endif()

	else()
		pkg_check_modules( PNG libpng16 )
	endif()

	if( PNG_FOUND )
		add_definitions( -DHAVE_PNG )
		message( STATUS "PNG support: ENABLED" )

	else()
		message( WARNING "libpng not found, PNG support disabled" )
		set( ENABLE_PNG OFF )
	endif()
endif()

# JPEG ( optional )
if( ENABLE_JPEG )
	if( APPLE )
		find_library( JPEG_LIBRARY NAMES jpeg HINTS /usr/local/opt/jpeg/lib /opt/homebrew/opt/jpeg/lib )
		if( JPEG_LIBRARY )
			set( JPEG_FOUND TRUE )
			set( JPEG_LIBRARIES ${JPEG_LIBRARY} )
		endif()

	else()
		pkg_check_modules( JPEG libjpeg )
	endif()

	if( JPEG_FOUND )
		add_definitions( -DHAVE_JPEG )
		message( STATUS "JPEG support: ENABLED" )

	else()
		message( WARNING "libjpeg not found, JPEG support disabled" )
		set( ENABLE_JPEG OFF )
	endif()
endif()

# Library
add_library( mylib
	src/core.c
	src/utils.c
 )

target_link_libraries( mylib PUBLIC
	${ZLIB_LIBRARIES}
 )

if( PNG_FOUND )
	target_link_libraries( mylib PUBLIC ${PNG_LIBRARIES} )
endif()

if( JPEG_FOUND )
	target_link_libraries( mylib PUBLIC ${JPEG_LIBRARIES} )
endif()

target_include_directories( mylib PUBLIC
	$<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/include>
	$<INSTALL_INTERFACE:include>
 )

# Executable
add_executable( myapp src/main.c )
target_link_libraries( myapp PRIVATE mylib )

# Tests
if( BUILD_TESTING )
	enable_testing()
	add_executable( mytest tests/test.c )
	target_link_libraries( mytest PRIVATE mylib )
	add_test( NAME mytest COMMAND mytest )
endif()
```

## Verification

After creating/modifying CMakeLists.txt:

**0. Validate with cmake-validator** ( recommended ):
```bash
python3 cmake-validator.py CMakeLists.txt
```
This checks for legacy patterns and suggests modern alternatives. See [cmake-validator.md](./cmake-validator.md).

Then always:

1. **Clean build**:
```bash
rm -rf build
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

2. **Verify static linking** (if applicable):
```bash
# Use p:static-linking tool (cross-platform)
python3 verify-static-linking.py build/myapp
```
See **[p:static-linking skill](../p:static-linking/SKILL.md)** for detailed verification workflows.

3. **Test functionality**:
```bash
./build/myapp --version
./build/myapp --help
```

## Additional Resources

- **[cmake-validator.py](./cmake-validator.py)** - Automatic validator for detecting legacy patterns
- **[cmake-validator.md](./cmake-validator.md)** - Validator documentation and usage guide
- **[template.cmake](./template.cmake)** - Production-ready template for new projects
- **[quick-reference.md](./quick-reference.md)** - Quick lookup for common patterns and boilerplate
- **[examples.md](./examples.md)** - Complete real-world project examples
- **[p:static-linking skill](../p:static-linking/SKILL.md)** - Specialized guide for building and verifying static binaries

## Summary

When working with CMake:

1. **Validate first**: Run `cmake-validator.py` to catch legacy patterns
2. **Always use modern target-based commands**
3. **Explicit library finding on macOS** with Homebrew paths
4. **Fallback strategy**: pkg-config → find_library → find_package
5. **Use generator expressions** for conditional config
6. **Add configuration summary** for debugging
7. **For static linking**: Use **[p:static-linking skill](../p:static-linking/SKILL.md)** tools and guides

The goal is **portable, maintainable, cross-platform builds** with modern CMake practices.
