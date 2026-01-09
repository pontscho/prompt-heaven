#!/usr/bin/env python3
"""
Extract implementation plan from requirements.yaml in compact YAML format.
Usage: python3 get_implementation_plan.py [path_to_requirements.yaml]
"""

import re
import sys
import os

def extract_implementation_plan(yaml_file):
    """Extract only implementation_plan, complete flag, and success_criteria"""

    with open(yaml_file, 'r') as f:
        content = f.read()

    # Extract complete flag
    complete_match = re.search(r'^complete:\s*(.+)$', content, re.MULTILINE)
    complete = complete_match.group(1).strip() if complete_match else "false"

    # Extract implementation_plan section
    impl_match = re.search(r'^implementation_plan:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
    implementation_plan = impl_match.group(1).strip() if impl_match else ""

    # Extract success_criteria section
    success_match = re.search(r'^success_criteria:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
    success_criteria = success_match.group(1).strip() if success_match else ""

    # Output compact YAML
    print(f"complete: {complete}")

    if success_criteria:
        print("success_criteria:")
        print(success_criteria)

    if implementation_plan:
        print("implementation_plan:")
        print(implementation_plan)

def main():
    # Default to requirements.yaml in current directory or parent directories
    yaml_file = None

    if len(sys.argv) > 1:
        yaml_file = sys.argv[1]
    else:
        current_dir = os.getcwd()
        for _ in range(5):
            candidate = os.path.join(current_dir, 'requirements.yaml')
            if os.path.exists(candidate):
                yaml_file = candidate
                break
            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break
            current_dir = parent

    if not yaml_file or not os.path.exists(yaml_file):
        print("Error: requirements.yaml not found", file=sys.stderr)
        sys.exit(1)

    extract_implementation_plan(yaml_file)

if __name__ == "__main__":
    main()
