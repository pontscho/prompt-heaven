# Static Linking Skill

Build and verify statically linked binaries with CMake across platforms.

## Quick Start

```bash
# 1. Copy example CMakeLists.txt to your project
cp example-CMakeLists.txt your-project/CMakeLists.txt

# 2. Build with static linking
cd your-project
python3 ../p:static-linking/build-static.py --verify

# 3. Verify the binary
python3 ../p:static-linking/verify-static-linking.py build/myapp
```

## Files

- **SKILL.md** - Complete guide and documentation
- **verify-static-linking.py** - Cross-platform verification tool
- **build-static.py** - Automated CMake build helper
- **example-CMakeLists.txt** - Production-ready template

## Tools Usage

### verify-static-linking.py

Verify that binaries are properly statically linked.

```bash
# Basic usage
python3 verify-static-linking.py build/myapp

# Strict mode (fail on any dynamic deps)
python3 verify-static-linking.py --strict build/myapp

# Verbose output
python3 verify-static-linking.py -v build/myapp
```

**Exit codes:**
- 0: Success (fully static or platform-appropriate)
- 1: Error (has non-system dynamic dependencies)

### build-static.py

Build CMake projects with static linking automatically.

```bash
# Basic build
python3 build-static.py

# Clean build with verification
python3 build-static.py --clean --verify

# Custom build directory
python3 build-static.py --build-dir build-release

# Parallel build
python3 build-static.py -j 8
```

## Platform Support

| Platform | Static Linking Type | Verification Command |
|----------|-------------------|---------------------|
| Linux    | Full static       | `ldd` should show "not a dynamic executable" |
| macOS    | Hybrid (third-party only) | `otool -L` should show only libSystem.B.dylib |
| Windows  | Static runtime (/MT) | `dumpbin` should show only system DLLs |

## Examples

### Manual Build (Linux)

```bash
cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=.a \
  -DCMAKE_EXE_LINKER_FLAGS="-static"
cmake --build build
python3 verify-static-linking.py build/myapp
```

### Manual Build (macOS)

```bash
cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=.a
cmake --build build
python3 verify-static-linking.py build/myapp
```

### CI/CD (GitHub Actions)

```yaml
- name: Build static binary
  run: python3 build-static.py --clean --build-type Release

- name: Verify static linking
  run: python3 verify-static-linking.py build/myapp

- name: Test portability
  run: docker run --rm -v $(pwd)/build:/app alpine:latest /app/myapp --version
```

## Integration with p:cmake

This skill works together with **p:cmake** skill:

```bash
# 1. Validate CMake patterns
python3 ~/.claude/skills/p:cmake/cmake-validator.py CMakeLists.txt

# 2. Build with static linking
python3 ~/.claude/skills/p:static-linking/build-static.py --verify
```

## Documentation

See **SKILL.md** for complete documentation including:
- Platform-specific configurations
- Detailed tool usage
- Common issues and solutions
- Best practices
- CI/CD integration
