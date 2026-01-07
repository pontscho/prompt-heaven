---
name: p:static-linking
description: Guide for building and verifying statically linked binaries with CMake. Covers platform-specific static linking (Linux full static, macOS hybrid, Windows /MT), verification tools, and troubleshooting. Use when building portable binaries, creating standalone executables, or verifying static linking.
---

# Static Linking with CMake

Complete guide for building and verifying statically linked binaries across platforms using CMake.

## Quick Start

**Build with automatic static linking**:
```bash
python3 build-static.py --verify
```

**Verify existing binary**:
```bash
python3 verify-static-linking.py build/myapp
```

**Manual CMake static build**:
```bash
# Linux
cmake -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=.a \
  -DCMAKE_EXE_LINKER_FLAGS="-static"

# macOS
cmake -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=.a

cmake --build build
python3 verify-static-linking.py build/myapp
```

## What is Static Linking?

Static linking embeds library code directly into the executable at compile time, creating a standalone binary that doesn't depend on external shared libraries.

### Benefits ✅

- **Portability**: Binary works on systems without installing dependencies
- **Version independence**: No shared library version conflicts
- **Deployment simplicity**: Single file distribution
- **Performance**: Potentially faster (no dynamic linking overhead)
- **Security**: Reduced attack surface (no LD_PRELOAD hijacking)

### Drawbacks ❌

- **Binary size**: Larger executables (contains all library code)
- **Updates**: Security patches require recompiling binary
- **Memory**: Multiple static binaries don't share library memory
- **Platform limitations**: macOS doesn't support full static linking

## Platform-Specific Behavior

### Linux 🐧

**Full static linking** is possible and recommended for maximum portability.

```bash
# Verification
ldd myapp
# Expected: "not a dynamic executable"
```

**CMake configuration**:
```cmake
if(UNIX AND NOT APPLE)
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
endif()
```

### macOS 🍎

**Hybrid static linking** only - system libraries MUST be dynamic.

```bash
# Verification
otool -L myapp
# Expected: Only /usr/lib/libSystem.B.dylib
```

**Why?**: Apple prohibits statically linking system libraries (libSystem, libc, etc.). Only third-party libraries can be static.

**CMake configuration**:
```cmake
if(APPLE)
    # Only set suffix, NO -static flag
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
endif()
```

### Windows 🪟

**Static runtime linking** with `/MT` flag.

```powershell
# Verification
dumpbin /dependents myapp.exe
# Expected: Only system DLLs (kernel32, ntdll, etc.)
```

**CMake configuration**:
```cmake
if(WIN32)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()
```

## Tools

### 1. verify-static-linking.py

Cross-platform binary verification tool.

**Usage**:
```bash
# Basic verification
python3 verify-static-linking.py build/myapp

# Strict mode (fail on any dynamic deps)
python3 verify-static-linking.py --strict build/myapp

# Verbose output with full paths
python3 verify-static-linking.py -v build/myapp
```

**Features**:
- ✓ Automatic platform detection (Linux/macOS/Windows)
- ✓ Categorizes dependencies (system vs third-party)
- ✓ Color-coded output (✓/⚠/✗)
- ✓ Detailed dependency analysis
- ✓ Exit codes for CI/CD integration

**Example output (Linux)**:
```
================================================================================
Static Linking Verification - Linux
================================================================================

Binary: build/myapp
Status: ✓ Fully static binary (no dynamic dependencies)

Details:
  • Binary is completely static
  • No shared libraries linked

================================================================================
```

**Example output (macOS)**:
```
================================================================================
Static Linking Verification - macOS
================================================================================

Binary: build/myapp
Status: ✓ Optimal static binary (only libSystem required by macOS)

Details:
  • Only system library: libSystem.B.dylib
  • Third-party libraries are statically linked

Dependencies (1):

  System libraries (1):
    • libSystem.B.dylib

================================================================================
```

### 2. build-static.py

Automated CMake build helper with static linking.

**Usage**:
```bash
# Basic build
python3 build-static.py

# Clean build with verification
python3 build-static.py --clean --verify

# Custom build directory and type
python3 build-static.py --build-dir build-release --build-type Release

# Build specific target with parallel jobs
python3 build-static.py --target myapp -j 8 --verify

# Verbose output
python3 build-static.py -v --verify
```

**Features**:
- ✓ Platform-specific CMake flags automatically applied
- ✓ Parallel builds (auto-detects CPU count)
- ✓ Clean build option
- ✓ Integrated verification
- ✓ Progress indicators

**What it does**:
1. Detects your platform
2. Runs CMake with appropriate static linking flags
3. Builds the project with optimal parallelism
4. (Optional) Verifies all built binaries

## CMake Patterns

### Complete Static Linking Template

```cmake
cmake_minimum_required(VERSION 3.15)
project(myapp VERSION 1.0.0 LANGUAGES C CXX)

# ==============================================================================
# Static Linking Configuration
# ==============================================================================

# Force static library preference
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Build shared libraries" FORCE)

# Platform-specific static linking
if(UNIX AND NOT APPLE)
    # Linux: Full static
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
    message(STATUS "Linux: Full static linking enabled")

elseif(APPLE)
    # macOS: Hybrid static (third-party libs only)
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
    message(STATUS "macOS: Hybrid static linking enabled")
    message(STATUS "  - Third-party libraries: static")
    message(STATUS "  - System libraries: dynamic (required)")

elseif(WIN32)
    # Windows: Static runtime
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    message(STATUS "Windows: Static runtime (/MT) enabled")
endif()

# ==============================================================================
# Dependencies
# ==============================================================================

find_package(PkgConfig REQUIRED)

# Example: zlib
if(APPLE)
    # macOS: Explicit static library search
    find_library(ZLIB_LIBRARY NAMES z REQUIRED
        HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib)
    set(ZLIB_LIBRARIES ${ZLIB_LIBRARY})
else()
    pkg_check_modules(ZLIB REQUIRED zlib)
endif()

# ==============================================================================
# Executable
# ==============================================================================

add_executable(myapp src/main.c)

target_link_libraries(myapp PRIVATE ${ZLIB_LIBRARIES})

# Platform-specific system libraries
if(UNIX AND NOT APPLE)
    target_link_libraries(myapp PRIVATE m pthread)
endif()
```

### Verifying Static Linking in CMakeLists.txt

Add automatic verification as a post-build step:

```cmake
# Find verification script
find_program(VERIFY_STATIC
    NAMES verify-static-linking.py
    HINTS
        ${CMAKE_SOURCE_DIR}/scripts
        ${CMAKE_SOURCE_DIR}/../p:static-linking
        ~/.claude/skills/p:static-linking
)

if(VERIFY_STATIC)
    add_custom_command(TARGET myapp POST_BUILD
        COMMAND ${Python3_EXECUTABLE} ${VERIFY_STATIC} $<TARGET_FILE:myapp>
        COMMENT "Verifying static linking of $<TARGET_NAME:myapp>"
        VERBATIM
    )
else()
    message(WARNING "verify-static-linking.py not found, skipping verification")
endif()
```

## Common Issues and Solutions

### Problem: "library not found for -lcrt0.o" (macOS)

**Cause**: Using `-static` flag on macOS

**Solution**: Remove `-static` flag for macOS builds

```cmake
# ❌ WRONG
if(APPLE)
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
endif()

# ✅ CORRECT
if(APPLE)
    # Only set library suffix, no -static flag
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
endif()
```

### Problem: Binary still has dynamic dependencies

**Cause**: Libraries not available as static archives (`.a`)

**Diagnose**:
```bash
# Check what CMake found
grep -r "FOUND" build/CMakeCache.txt

# Check available library formats
ls /usr/local/lib/libpng*
# If only .so/.dylib files exist, no static version available
```

**Solution**: Install static development packages

```bash
# Debian/Ubuntu
sudo apt install libpng-dev zlib1g-dev

# Fedora/RHEL
sudo dnf install libpng-static zlib-static

# macOS (Homebrew)
brew install libpng zlib
```

### Problem: Linking fails with "undefined reference"

**Cause**: Missing transitive dependencies or wrong link order

**Solution**: Add all required libraries explicitly

```cmake
# Find transitive dependencies
pkg_check_modules(PNG REQUIRED libpng16)

# Link with all dependencies
target_link_libraries(myapp PRIVATE
    ${PNG_LIBRARIES}
    ${PNG_STATIC_LIBRARIES}  # Include static-specific deps
)

# Ensure proper link order (Linux)
if(UNIX AND NOT APPLE)
    target_link_libraries(myapp PRIVATE m pthread)  # Add at end
endif()
```

### Problem: Binary size too large

**Cause**: All library code embedded + debug symbols

**Solution**: Strip binary and optimize

```cmake
# Enable link-time optimization
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION TRUE)

# Strip symbols in release builds
if(CMAKE_BUILD_TYPE STREQUAL "Release")
    add_custom_command(TARGET myapp POST_BUILD
        COMMAND strip $<TARGET_FILE:myapp>
        COMMENT "Stripping symbols from $<TARGET_NAME:myapp>"
    )
endif()
```

```bash
# Manual stripping
strip --strip-all build/myapp

# Compare sizes
ls -lh build/myapp
```

## Verification Workflows

### Development Workflow

```bash
# 1. Build with static linking
python3 build-static.py --clean

# 2. Verify specific binary
python3 verify-static-linking.py build/myapp

# 3. Test binary portability
scp build/myapp remote-machine:/tmp/
ssh remote-machine /tmp/myapp --version
```

### CI/CD Integration

**GitHub Actions**:
```yaml
- name: Build static binary
  run: python3 build-static.py --build-type Release

- name: Verify static linking
  run: python3 verify-static-linking.py build/myapp

- name: Test portability
  run: |
    # Run on minimal container
    docker run --rm -v $(pwd)/build:/app alpine:latest /app/myapp --version
```

**GitLab CI**:
```yaml
build:static:
  script:
    - python3 build-static.py --clean --verify
  artifacts:
    paths:
      - build/myapp
```

### Pre-release Checklist

```bash
# 1. Clean build
python3 build-static.py --clean --build-type Release

# 2. Strict verification
python3 verify-static-linking.py --strict build/myapp

# 3. Check binary size
ls -lh build/myapp

# 4. Test on fresh system
docker run --rm -v $(pwd)/build:/app alpine:latest /app/myapp --version

# 5. Verify symbols stripped
file build/myapp
nm build/myapp | head  # Should be minimal

# 6. Test basic functionality
./build/myapp --help
./build/myapp --version
```

## Best Practices

### DO ✅

1. **Use platform-specific flags** - Linux full static, macOS hybrid, Windows /MT
2. **Verify after every build** - Catch dynamic dependencies early
3. **Test on minimal systems** - Docker, VMs without dev packages
4. **Strip release binaries** - Reduce size, remove debug symbols
5. **Document limitations** - Especially macOS hybrid approach
6. **Use automation tools** - `build-static.py` and `verify-static-linking.py`
7. **Check transitive deps** - Use `pkg-config --static`

### DON'T ❌

1. **Don't use `-static` on macOS** - Will fail with crt0.o error
2. **Don't assume full static on all platforms** - macOS requires system libs dynamic
3. **Don't skip verification** - Manual inspection misses issues
4. **Don't forget platform testing** - Build on target platforms
5. **Don't link unnecessary libraries** - Increases binary size
6. **Don't ignore warnings** - "cannot find -l..." means missing static lib

## Integration with p:cmake Skill

This skill complements **p:cmake** skill. Use together:

1. **p:cmake** - For general CMake project setup and best practices
2. **p:static-linking** - For static linking specifics and verification

**Example workflow**:
```bash
# 1. Create project with p:cmake template
cp ~/.claude/skills/p:cmake/template.cmake CMakeLists.txt

# 2. Build with static linking
python3 ~/.claude/skills/p:static-linking/build-static.py --verify

# 3. Validate CMake patterns
python3 ~/.claude/skills/p:cmake/cmake-validator.py CMakeLists.txt
```

## Summary

When building statically linked binaries:

1. **Know your platform**: Linux full static, macOS hybrid, Windows /MT
2. **Use the tools**: `build-static.py` and `verify-static-linking.py`
3. **Verify always**: Don't trust, verify with tools
4. **Test portability**: Run on minimal systems
5. **Understand limitations**: macOS requires system libs dynamic
6. **Automate**: Integrate verification in CI/CD

**Goal**: Create portable, standalone binaries that work without external dependencies.

## Additional Resources

- **[verify-static-linking.py](./verify-static-linking.py)** - Cross-platform verification tool
- **[build-static.py](./build-static.py)** - Automated CMake build helper
- **[p:cmake skill](../p:cmake/SKILL.md)** - General CMake best practices
- **[CMake Documentation](https://cmake.org/cmake/help/latest/)** - Official CMake docs
