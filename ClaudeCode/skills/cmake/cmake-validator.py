#!/usr/bin/env python3
"""
CMakeLists.txt Validator - Detect legacy CMake patterns

This script checks CMakeLists.txt files for legacy patterns and suggests
modern target-based alternatives following CMake best practices.

Usage:
    python3 cmake-validator.py [file_or_directory]
    python3 cmake-validator.py CMakeLists.txt
    python3 cmake-validator.py src/
"""

import sys
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple
from enum import Enum


class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Issue:
    severity: Severity
    line_num: int
    line: str
    pattern: str
    message: str
    suggestion: str


class CMakeValidator:
    """Validates CMakeLists.txt for legacy patterns"""

    # Legacy patterns to detect
    PATTERNS = {
        # Global include directories (should be target-based)
        r'^\s*include_directories\s*\(': {
            'name': 'include_directories()',
            'severity': Severity.WARNING,
            'message': 'Using global include_directories() - affects all targets',
            'suggestion': 'Use target_include_directories(target PRIVATE/PUBLIC ...)',
            'exceptions': []
        },

        # Global link libraries (should be target-based)
        r'^\s*link_libraries\s*\(': {
            'name': 'link_libraries()',
            'severity': Severity.ERROR,
            'message': 'Using global link_libraries() - affects all targets',
            'suggestion': 'Use target_link_libraries(target PRIVATE/PUBLIC ...)',
            'exceptions': []
        },

        # Global definitions (should be target-based)
        r'^\s*add_definitions\s*\(': {
            'name': 'add_definitions()',
            'severity': Severity.WARNING,
            'message': 'Using global add_definitions() - affects all targets',
            'suggestion': 'Use target_compile_definitions(target PRIVATE/PUBLIC ...)',
            'exceptions': []
        },

        # Global compile options without target
        r'^\s*add_compile_options\s*\(': {
            'name': 'add_compile_options()',
            'severity': Severity.INFO,
            'message': 'Using global add_compile_options() - consider target-specific flags',
            'suggestion': 'Use target_compile_options(target PRIVATE/PUBLIC ...) for target-specific flags',
            'exceptions': ['early in file', 'before first target']
        },

        # Using CMAKE_C_FLAGS directly (legacy)
        r'^\s*set\s*\(\s*CMAKE_C_FLAGS': {
            'name': 'CMAKE_C_FLAGS',
            'severity': Severity.INFO,
            'message': 'Directly setting CMAKE_C_FLAGS - consider target-specific flags',
            'suggestion': 'Use target_compile_options() or add_compile_options() instead',
            'exceptions': []
        },

        # Missing PRIVATE/PUBLIC/INTERFACE in target commands
        r'^\s*target_(?:link_libraries|include_directories|compile_options|compile_definitions)\s*\(\s*\w+\s+\$\{': {
            'name': 'Missing visibility keyword',
            'severity': Severity.WARNING,
            'message': 'target_* command may be missing PRIVATE/PUBLIC/INTERFACE keyword',
            'suggestion': 'Add PRIVATE, PUBLIC, or INTERFACE visibility keyword',
            'exceptions': []
        },

        # Using -static flag on macOS (will fail)
        r'-static.*APPLE': {
            'name': '-static on macOS',
            'severity': Severity.ERROR,
            'message': 'Using -static flag on macOS will fail (libSystem must be dynamic)',
            'suggestion': 'Use CMAKE_FIND_LIBRARY_SUFFIXES .a instead of -static on macOS',
            'exceptions': []
        },

        # Not using REQUIRED for critical dependencies
        r'find_package\s*\(\s*\w+\s*\)(?!\s*(?:REQUIRED|#))': {
            'name': 'find_package without REQUIRED',
            'severity': Severity.INFO,
            'message': 'find_package() without REQUIRED or explicit handling',
            'suggestion': 'Add REQUIRED or check ${PACKAGE}_FOUND explicitly',
            'exceptions': ['optional']
        },
    }

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.issues: List[Issue] = []

    def validate_file(self, filepath: Path) -> List[Issue]:
        """Validate a single CMakeLists.txt file"""
        self.issues = []

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error reading {filepath}: {e}", file=sys.stderr)
            return []

        for line_num, line in enumerate(lines, start=1):
            # Skip comments
            if line.strip().startswith('#'):
                continue

            self._check_line(line_num, line)

        return self.issues

    def _check_line(self, line_num: int, line: str):
        """Check a single line for legacy patterns"""
        for pattern_str, info in self.PATTERNS.items():
            if re.search(pattern_str, line):
                # Check exceptions
                skip = False
                for exception in info['exceptions']:
                    if exception.lower() in line.lower():
                        skip = True
                        break

                if not skip:
                    issue = Issue(
                        severity=info['severity'],
                        line_num=line_num,
                        line=line.strip(),
                        pattern=info['name'],
                        message=info['message'],
                        suggestion=info['suggestion']
                    )
                    self.issues.append(issue)

    def print_report(self, filepath: Path, issues: List[Issue]):
        """Print validation report"""
        if not issues:
            print(f"✓ {filepath}: No issues found")
            return

        print(f"\n{'=' * 80}")
        print(f"File: {filepath}")
        print(f"{'=' * 80}\n")

        # Group by severity
        errors = [i for i in issues if i.severity == Severity.ERROR]
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        infos = [i for i in issues if i.severity == Severity.INFO]

        for issue_list, label in [(errors, "ERRORS"), (warnings, "WARNINGS"), (infos, "INFO")]:
            if not issue_list:
                continue

            print(f"{label}:")
            print("-" * 80)

            for issue in issue_list:
                print(f"\nLine {issue.line_num}: {issue.pattern}")
                print(f"  {issue.line}")
                print(f"  ⚠ {issue.message}")
                print(f"  ✓ {issue.suggestion}")

        print(f"\n{'=' * 80}")
        print(f"Summary: {len(errors)} errors, {len(warnings)} warnings, {len(infos)} info")
        print(f"{'=' * 80}\n")


def find_cmake_files(path: Path) -> List[Path]:
    """Find all CMakeLists.txt and .cmake files in path"""
    if path.is_file():
        # Accept CMakeLists.txt or any .cmake file
        if path.name == "CMakeLists.txt" or path.suffix == ".cmake":
            return [path]
        return []

    if path.is_dir():
        # Find both CMakeLists.txt and *.cmake files
        cmake_files = list(path.rglob("CMakeLists.txt"))
        cmake_files.extend(path.rglob("*.cmake"))
        return sorted(set(cmake_files))  # Remove duplicates

    return []


def main():
    if len(sys.argv) < 2:
        # Default to current directory
        search_path = Path.cwd()
    else:
        search_path = Path(sys.argv[1])

    if not search_path.exists():
        print(f"Error: Path '{search_path}' does not exist", file=sys.stderr)
        sys.exit(1)

    # Find all CMakeLists.txt and .cmake files
    cmake_files = find_cmake_files(search_path)

    if not cmake_files:
        print(f"No CMakeLists.txt or .cmake files found in {search_path}")
        sys.exit(0)

    print(f"Validating {len(cmake_files)} CMake file(s)...\n")

    validator = CMakeValidator()
    total_issues = 0
    total_errors = 0

    for filepath in cmake_files:
        issues = validator.validate_file(filepath)
        total_issues += len(issues)
        total_errors += len([i for i in issues if i.severity == Severity.ERROR])

        validator.print_report(filepath, issues)

    # Exit with error code if critical issues found
    if total_errors > 0:
        print(f"\n❌ Validation failed: {total_errors} critical error(s) found")
        sys.exit(1)
    elif total_issues > 0:
        print(f"\n⚠ Validation passed with {total_issues} warning(s)")
        sys.exit(0)
    else:
        print("\n✓ All files validated successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
