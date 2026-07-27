#!/usr/bin/env python3
"""
Static Linking Verification Tool

Verifies that binaries are properly statically linked across platforms.
Supports Linux, macOS, and Windows.

Usage:
    python3 verify-static-linking.py <binary>
    python3 verify-static-linking.py build/myapp
    python3 verify-static-linking.py --strict build/myapp
"""

import sys
import subprocess
import platform
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum


class Status(Enum):
    SUCCESS = "✓"
    WARNING = "⚠"
    ERROR = "✗"


@dataclass
class Dependency:
    name: str
    path: Optional[str] = None
    is_system: bool = False


@dataclass
class VerificationResult:
    status: Status
    platform: str
    binary: Path
    dependencies: List[Dependency]
    message: str
    details: List[str]


class StaticLinkingVerifier:
    """Verifies static linking for different platforms"""

    def __init__(self, binary_path: Path, strict: bool = False):
        self.binary = binary_path
        self.strict = strict
        self.platform = platform.system()

    def verify(self) -> VerificationResult:
        """Main verification method"""
        if not self.binary.exists():
            return VerificationResult(
                status=Status.ERROR,
                platform=self.platform,
                binary=self.binary,
                dependencies=[],
                message=f"Binary not found: {self.binary}",
                details=[]
            )

        if not self.binary.is_file():
            return VerificationResult(
                status=Status.ERROR,
                platform=self.platform,
                binary=self.binary,
                dependencies=[],
                message=f"Not a file: {self.binary}",
                details=[]
            )

        # Platform-specific verification
        if self.platform == "Linux":
            return self._verify_linux()
        elif self.platform == "Darwin":
            return self._verify_macos()
        elif self.platform == "Windows":
            return self._verify_windows()
        else:
            return VerificationResult(
                status=Status.ERROR,
                platform=self.platform,
                binary=self.binary,
                dependencies=[],
                message=f"Unsupported platform: {self.platform}",
                details=[]
            )

    def _verify_linux(self) -> VerificationResult:
        """Verify Linux binary with ldd"""
        try:
            result = subprocess.run(
                ["ldd", str(self.binary)],
                capture_output=True,
                text=True,
                timeout=10
            )

            output = result.stdout + result.stderr

            # Check if fully static
            if "not a dynamic executable" in output or "not a dynamic" in output:
                return VerificationResult(
                    status=Status.SUCCESS,
                    platform="Linux",
                    binary=self.binary,
                    dependencies=[],
                    message="Fully static binary (no dynamic dependencies)",
                    details=["Binary is completely static", "No shared libraries linked"]
                )

            # Parse dependencies
            deps = []
            for line in output.splitlines():
                line = line.strip()
                if "=>" in line:
                    parts = line.split("=>")
                    lib_name = parts[0].strip()
                    lib_path = parts[1].strip().split()[0] if len(parts[1].strip()) > 0 else None

                    # Check if system library
                    is_system = False
                    if lib_path and any(p in lib_path for p in ["/lib/", "/usr/lib/", "linux-vdso", "ld-linux"]):
                        is_system = True

                    deps.append(Dependency(
                        name=lib_name,
                        path=lib_path,
                        is_system=is_system
                    ))

            # Evaluate
            if not deps:
                return VerificationResult(
                    status=Status.SUCCESS,
                    platform="Linux",
                    binary=self.binary,
                    dependencies=[],
                    message="Fully static binary",
                    details=["No dynamic dependencies found"]
                )

            # Only system libs (linux-vdso, ld-linux)
            non_system_deps = [d for d in deps if not d.is_system]
            if not non_system_deps:
                status = Status.WARNING if self.strict else Status.SUCCESS
                return VerificationResult(
                    status=status,
                    platform="Linux",
                    binary=self.binary,
                    dependencies=deps,
                    message="Mostly static (only system libs)" if not self.strict else "Not fully static",
                    details=[
                        f"System dependencies: {len(deps)}",
                        "All dependencies are system libraries"
                    ]
                )

            # Has non-system dependencies
            return VerificationResult(
                status=Status.ERROR,
                platform="Linux",
                binary=self.binary,
                dependencies=deps,
                message=f"Dynamic binary with {len(non_system_deps)} non-system dependencies",
                details=[
                    f"Total dependencies: {len(deps)}",
                    f"Non-system dependencies: {len(non_system_deps)}"
                ]
            )

        except FileNotFoundError:
            return VerificationResult(
                status=Status.ERROR,
                platform="Linux",
                binary=self.binary,
                dependencies=[],
                message="ldd command not found",
                details=["Install binutils package"]
            )
        except Exception as e:
            return VerificationResult(
                status=Status.ERROR,
                platform="Linux",
                binary=self.binary,
                dependencies=[],
                message=f"Error running ldd: {e}",
                details=[]
            )

    def _verify_macos(self) -> VerificationResult:
        """Verify macOS binary with otool"""
        try:
            result = subprocess.run(
                ["otool", "-L", str(self.binary)],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return VerificationResult(
                    status=Status.ERROR,
                    platform="macOS",
                    binary=self.binary,
                    dependencies=[],
                    message=f"otool failed: {result.stderr}",
                    details=[]
                )

            lines = result.stdout.strip().splitlines()

            # Skip first line (binary path itself)
            if len(lines) <= 1:
                return VerificationResult(
                    status=Status.SUCCESS,
                    platform="macOS",
                    binary=self.binary,
                    dependencies=[],
                    message="No dependencies (unusual but valid)",
                    details=[]
                )

            deps = []
            for line in lines[1:]:
                lib_path = line.strip().split()[0]

                # Categorize
                is_system = (
                    lib_path.startswith("/usr/lib/") or
                    lib_path.startswith("/System/Library/")
                )

                deps.append(Dependency(
                    name=Path(lib_path).name,
                    path=lib_path,
                    is_system=is_system
                ))

            # Check if only libSystem.B.dylib
            if len(deps) == 1 and "/usr/lib/libSystem.B.dylib" in deps[0].path:
                return VerificationResult(
                    status=Status.SUCCESS,
                    platform="macOS",
                    binary=self.binary,
                    dependencies=deps,
                    message="Optimal static binary (only libSystem required by macOS)",
                    details=[
                        "Only system library: libSystem.B.dylib",
                        "Third-party libraries are statically linked"
                    ]
                )

            # Only system libraries
            non_system_deps = [d for d in deps if not d.is_system]
            if not non_system_deps:
                if len(deps) <= 3:
                    return VerificationResult(
                        status=Status.SUCCESS,
                        platform="macOS",
                        binary=self.binary,
                        dependencies=deps,
                        message="Good static binary (only system libs)",
                        details=[
                            f"System dependencies: {len(deps)}",
                            "All dependencies are system libraries"
                        ]
                    )
                else:
                    return VerificationResult(
                        status=Status.WARNING,
                        platform="macOS",
                        binary=self.binary,
                        dependencies=deps,
                        message=f"Many system dependencies ({len(deps)})",
                        details=[
                            f"System dependencies: {len(deps)}",
                            "Consider reviewing dependencies"
                        ]
                    )

            # Has non-system dependencies
            return VerificationResult(
                status=Status.ERROR,
                platform="macOS",
                binary=self.binary,
                dependencies=deps,
                message=f"Dynamic binary with {len(non_system_deps)} third-party dependencies",
                details=[
                    f"Total dependencies: {len(deps)}",
                    f"Non-system dependencies: {len(non_system_deps)}"
                ]
            )

        except FileNotFoundError:
            return VerificationResult(
                status=Status.ERROR,
                platform="macOS",
                binary=self.binary,
                dependencies=[],
                message="otool command not found",
                details=["Install Xcode Command Line Tools"]
            )
        except Exception as e:
            return VerificationResult(
                status=Status.ERROR,
                platform="macOS",
                binary=self.binary,
                dependencies=[],
                message=f"Error running otool: {e}",
                details=[]
            )

    def _verify_windows(self) -> VerificationResult:
        """Verify Windows binary with dumpbin"""
        try:
            # Try dumpbin first (Visual Studio)
            result = subprocess.run(
                ["dumpbin", "/dependents", str(self.binary)],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return VerificationResult(
                    status=Status.ERROR,
                    platform="Windows",
                    binary=self.binary,
                    dependencies=[],
                    message="Could not analyze binary (dumpbin not found)",
                    details=["Install Visual Studio or use Windows SDK"]
                )

            # Parse dependencies
            deps = []
            in_deps_section = False
            for line in result.stdout.splitlines():
                line = line.strip()
                if "Image has the following dependencies:" in line:
                    in_deps_section = True
                    continue
                if in_deps_section and line and not line.startswith("Summary"):
                    if line.endswith(".dll"):
                        is_system = any(sys_dll in line.lower() for sys_dll in [
                            "kernel32", "ntdll", "msvcrt", "ucrtbase"
                        ])
                        deps.append(Dependency(
                            name=line,
                            path=None,
                            is_system=is_system
                        ))

            # Evaluate
            non_system_deps = [d for d in deps if not d.is_system]

            if not deps:
                return VerificationResult(
                    status=Status.SUCCESS,
                    platform="Windows",
                    binary=self.binary,
                    dependencies=[],
                    message="Fully static binary (no DLL dependencies)",
                    details=["Binary is completely static"]
                )

            if not non_system_deps:
                return VerificationResult(
                    status=Status.SUCCESS,
                    platform="Windows",
                    binary=self.binary,
                    dependencies=deps,
                    message="Static binary (only system DLLs)",
                    details=[
                        f"System dependencies: {len(deps)}",
                        "All dependencies are Windows system DLLs"
                    ]
                )

            return VerificationResult(
                status=Status.ERROR,
                platform="Windows",
                binary=self.binary,
                dependencies=deps,
                message=f"Dynamic binary with {len(non_system_deps)} third-party dependencies",
                details=[
                    f"Total dependencies: {len(deps)}",
                    f"Non-system dependencies: {len(non_system_deps)}"
                ]
            )

        except FileNotFoundError:
            return VerificationResult(
                status=Status.ERROR,
                platform="Windows",
                binary=self.binary,
                dependencies=[],
                message="dumpbin command not found",
                details=["Install Visual Studio or Windows SDK"]
            )
        except Exception as e:
            return VerificationResult(
                status=Status.ERROR,
                platform="Windows",
                binary=self.binary,
                dependencies=[],
                message=f"Error running dumpbin: {e}",
                details=[]
            )


def print_result(result: VerificationResult, verbose: bool = False):
    """Pretty print verification result"""
    print(f"\n{'=' * 80}")
    print(f"Static Linking Verification - {result.platform}")
    print(f"{'=' * 80}\n")

    print(f"Binary: {result.binary}")
    print(f"Status: {result.status.value} {result.message}\n")

    if result.details:
        print("Details:")
        for detail in result.details:
            print(f"  • {detail}")
        print()

    if result.dependencies:
        print(f"Dependencies ({len(result.dependencies)}):")

        system_deps = [d for d in result.dependencies if d.is_system]
        non_system_deps = [d for d in result.dependencies if not d.is_system]

        if system_deps:
            print(f"\n  System libraries ({len(system_deps)}):")
            for dep in system_deps:
                if verbose and dep.path:
                    print(f"    • {dep.name} ({dep.path})")
                else:
                    print(f"    • {dep.name}")

        if non_system_deps:
            print(f"\n  Third-party libraries ({len(non_system_deps)}):")
            for dep in non_system_deps:
                if verbose and dep.path:
                    print(f"    • {dep.name} ({dep.path})")
                else:
                    print(f"    • {dep.name}")

    print(f"\n{'=' * 80}\n")

    # Exit code
    if result.status == Status.SUCCESS:
        return 0
    elif result.status == Status.WARNING:
        return 0  # Warning is still OK
    else:
        return 1


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify static linking of binaries across platforms"
    )
    parser.add_argument("binary", help="Path to binary to verify")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: fail on any dynamic dependencies"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output with full paths"
    )

    args = parser.parse_args()

    binary_path = Path(args.binary)
    verifier = StaticLinkingVerifier(binary_path, strict=args.strict)
    result = verifier.verify()

    exit_code = print_result(result, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
