#!/usr/bin/env python3
"""
Static Build Helper for CMake Projects

Automates building CMake projects with static linking configurations
for different platforms.

Usage:
    python3 build-static.py
    python3 build-static.py --build-dir build-static
    python3 build-static.py --verify
    python3 build-static.py --clean --verify
"""

import sys
import subprocess
import platform
import shutil
from pathlib import Path
from typing import Optional, List


class StaticBuilder:
    """Helper for building CMake projects with static linking"""

    def __init__(
        self,
        source_dir: Path,
        build_dir: Path,
        build_type: str = "Release",
        verbose: bool = False
    ):
        self.source_dir = source_dir
        self.build_dir = build_dir
        self.build_type = build_type
        self.verbose = verbose
        self.platform = platform.system()

    def clean(self):
        """Clean build directory"""
        if self.build_dir.exists():
            print(f"🧹 Cleaning {self.build_dir}...")
            shutil.rmtree(self.build_dir)
            print("✓ Build directory cleaned\n")

    def configure(self) -> bool:
        """Run CMake configuration"""
        print(f"⚙️  Configuring CMake project for {self.platform}...")
        print(f"   Source: {self.source_dir}")
        print(f"   Build: {self.build_dir}")
        print(f"   Type: {self.build_type}\n")

        # CMake arguments
        cmake_args = [
            "cmake",
            "-S", str(self.source_dir),
            "-B", str(self.build_dir),
            f"-DCMAKE_BUILD_TYPE={self.build_type}",
        ]

        # Platform-specific flags
        if self.platform == "Linux":
            cmake_args.extend([
                "-DCMAKE_FIND_LIBRARY_SUFFIXES=.a",
                "-DCMAKE_EXE_LINKER_FLAGS=-static",
            ])
            print("🐧 Linux: Configuring for full static linking")

        elif self.platform == "Darwin":
            cmake_args.extend([
                "-DCMAKE_FIND_LIBRARY_SUFFIXES=.a",
            ])
            print("🍎 macOS: Configuring for hybrid static linking (third-party libs static)")

        elif self.platform == "Windows":
            cmake_args.extend([
                "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            ])
            print("🪟 Windows: Configuring for static runtime (/MT)")

        if self.verbose:
            cmake_args.append("--debug-output")

        print()

        try:
            result = subprocess.run(
                cmake_args,
                check=True,
                capture_output=not self.verbose,
                text=True
            )

            if not self.verbose and result.stdout:
                # Print summary
                for line in result.stdout.splitlines():
                    if any(keyword in line for keyword in ["Build", "Compiler", "Platform", "Found", "enabled", "disabled"]):
                        print(f"  {line}")

            print("\n✓ Configuration successful\n")
            return True

        except subprocess.CalledProcessError as e:
            print(f"\n✗ Configuration failed!")
            if e.stderr:
                print(f"\nError output:\n{e.stderr}")
            return False
        except FileNotFoundError:
            print("✗ CMake not found. Please install CMake first.")
            return False

    def build(self, target: Optional[str] = None, jobs: Optional[int] = None) -> bool:
        """Run CMake build"""
        print("🔨 Building project...")

        cmake_args = [
            "cmake",
            "--build", str(self.build_dir),
            "--config", self.build_type,
        ]

        if target:
            cmake_args.extend(["--target", target])

        if jobs:
            cmake_args.extend(["--parallel", str(jobs)])
        else:
            # Auto-detect CPU count
            try:
                import os
                cpu_count = os.cpu_count() or 1
                cmake_args.extend(["--parallel", str(cpu_count)])
                print(f"   Using {cpu_count} parallel jobs")
            except:
                pass

        if self.verbose:
            cmake_args.append("--verbose")

        print()

        try:
            subprocess.run(
                cmake_args,
                check=True,
                capture_output=not self.verbose
            )

            print("\n✓ Build successful\n")
            return True

        except subprocess.CalledProcessError as e:
            print(f"\n✗ Build failed!")
            if e.stderr:
                print(f"\nError output:\n{e.stderr.decode()}")
            return False

    def find_binaries(self) -> List[Path]:
        """Find built binaries in build directory"""
        binaries = []

        # Common binary locations
        search_patterns = [
            "**/*",  # All files
        ]

        for pattern in search_patterns:
            for file in self.build_dir.glob(pattern):
                if file.is_file() and file.stat().st_mode & 0o111:  # Executable bit
                    # Skip certain files
                    if any(skip in file.name.lower() for skip in ["cmake", "test", ".so", ".dylib", ".a"]):
                        continue
                    # Skip if in CMakeFiles directory
                    if "CMakeFiles" in str(file):
                        continue
                    binaries.append(file)

        return binaries

    def verify_binaries(self) -> bool:
        """Verify all built binaries"""
        print("🔍 Finding binaries to verify...\n")

        binaries = self.find_binaries()

        if not binaries:
            print("⚠ No binaries found in build directory")
            return False

        print(f"Found {len(binaries)} binary(ies):\n")

        # Import verifier
        verifier_script = Path(__file__).parent / "verify-static-linking.py"
        if not verifier_script.exists():
            print(f"✗ Verifier script not found: {verifier_script}")
            return False

        all_passed = True

        for binary in binaries:
            print(f"Verifying: {binary.name}")
            try:
                result = subprocess.run(
                    [sys.executable, str(verifier_script), str(binary)],
                    capture_output=True,
                    text=True
                )

                # Print output
                print(result.stdout)

                if result.returncode != 0:
                    all_passed = False

            except Exception as e:
                print(f"✗ Error verifying {binary}: {e}\n")
                all_passed = False

        return all_passed


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build CMake projects with static linking"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path.cwd(),
        help="Source directory (default: current directory)"
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path("build"),
        help="Build directory (default: ./build)"
    )
    parser.add_argument(
        "--build-type",
        choices=["Debug", "Release", "RelWithDebInfo", "MinSizeRel"],
        default="Release",
        help="CMake build type (default: Release)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean build directory before building"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify static linking after build"
    )
    parser.add_argument(
        "--target",
        help="Specific target to build"
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        help="Number of parallel build jobs"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    # Resolve paths
    source_dir = args.source_dir.resolve()
    build_dir = args.build_dir.resolve()

    # Check source directory
    if not source_dir.exists():
        print(f"✗ Source directory not found: {source_dir}")
        sys.exit(1)

    cmake_file = source_dir / "CMakeLists.txt"
    if not cmake_file.exists():
        print(f"✗ CMakeLists.txt not found in {source_dir}")
        sys.exit(1)

    # Create builder
    builder = StaticBuilder(
        source_dir=source_dir,
        build_dir=build_dir,
        build_type=args.build_type,
        verbose=args.verbose
    )

    print(f"\n{'=' * 80}")
    print("Static Build Helper")
    print(f"{'=' * 80}\n")

    # Clean if requested
    if args.clean:
        builder.clean()

    # Configure
    if not builder.configure():
        print("\n❌ Configuration failed")
        sys.exit(1)

    # Build
    if not builder.build(target=args.target, jobs=args.jobs):
        print("\n❌ Build failed")
        sys.exit(1)

    # Verify if requested
    if args.verify:
        if not builder.verify_binaries():
            print("\n⚠ Some binaries failed verification")
            sys.exit(1)
        else:
            print("\n✅ All binaries verified successfully")

    print(f"\n{'=' * 80}")
    print("✅ Build completed successfully")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
