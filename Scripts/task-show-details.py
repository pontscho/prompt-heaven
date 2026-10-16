#!/usr/bin/env python3
"""
Display raw YAML blocks for specific tasks from requirements.yaml file.
Usage: task-show-details.py [--yaml PATH] task-001 task-002 task-003 ...
--yaml / -y / --yaml=PATH reads exactly that file (no walk-up).
"""

import re
import sys
import os

def find_task_yaml_block(content, task_id):
    """Find and extract the raw YAML block for a specific task"""

    # Find the task block (task_id value may be quoted: task_id: "task-003")
    pattern = rf'''    - task_id:\s*["']?{re.escape(task_id)}\b'''
    match = re.search(pattern, content)

    if not match:
        return None

    # Find the start position (beginning of the line with task_id)
    start_pos = match.start()

    # Find where this task ends (next task_id at same indentation level or end of tasks section)
    next_task_pattern = r'\n    - task_id:'
    next_task = re.search(next_task_pattern, content[start_pos + 10:])

    if next_task:
        end_pos = start_pos + 10 + next_task.start()
    else:
        # Look for end of tasks section
        end_markers = [r'\n\nimplementation_notes:', r'\n\nfuture_enhancements:', r'\n\nconstraints:', r'\n\nsuccess_criteria:']
        end_pos = len(content)
        for marker in end_markers:
            marker_match = re.search(marker, content[start_pos:])
            if marker_match:
                end_pos = min(end_pos, start_pos + marker_match.start())

    # Extract the raw YAML block
    yaml_block = content[start_pos:end_pos].rstrip()
    return yaml_block

def split_yaml_flag(argv):
    """Pull `--yaml PATH` / `-y PATH` / `--yaml=PATH` out of argv, at any position.

    Returns (yaml_path_or_None, remaining_args). A flag with no value exits 1
    with one stderr line.
    """
    yaml_path = None
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--yaml", "-y"):
            if i + 1 >= len(argv):
                print(f"task-show-details.py: {arg} needs a PATH", file=sys.stderr)
                sys.exit(1)
            yaml_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--yaml="):
            yaml_path = arg[len("--yaml="):]
        else:
            rest.append(arg)
        i += 1
    return yaml_path, rest


def main():
    yaml_arg, task_ids = split_yaml_flag(sys.argv[1:])

    if not task_ids:
        print("Usage: task-show-details.py [--yaml PATH] task-001 task-002 task-003 ...")
        print("\nExample:")
        print("  task-show-details.py task-001")
        print("  task-show-details.py task-001 task-002 task-003")
        print("  task-show-details.py --yaml feat/requirements.yaml task-001")
        print("\n--yaml, -y PATH: read exactly this file (no walk-up);")
        print("default: requirements.yaml in the current or a parent directory")
        sys.exit(1)

    yaml_file = None
    if yaml_arg is not None:
        # An explicit file is read as named: no walk-up, no fallback.
        yaml_file = yaml_arg
    else:
        # Find requirements.yaml in current directory or parent directories
        current_dir = os.getcwd()

        for _ in range(5):  # Check up to 5 levels up
            candidate = os.path.join(current_dir, 'requirements.yaml')
            if os.path.exists(candidate):
                yaml_file = candidate
                break
            parent = os.path.dirname(current_dir)
            if parent == current_dir:  # Reached root
                break
            current_dir = parent

        if not yaml_file:
            print("Error: requirements.yaml not found in current directory or parent directories")
            sys.exit(1)

    try:
        with open(yaml_file, 'r') as f:
            content = f.read()
    except OSError as e:
        print(f"task-show-details.py: cannot read {yaml_file}: {e.strerror or e}", file=sys.stderr)
        sys.exit(1)

    found_tasks = []
    not_found_tasks = []

    for task_id in task_ids:
        yaml_block = find_task_yaml_block(content, task_id)
        if yaml_block:
            found_tasks.append((task_id, yaml_block))
        else:
            not_found_tasks.append(task_id)

    # Display all found tasks
    for task_id, yaml_block in found_tasks:
        print(f"\n# Task: {task_id}")
        print("# " + "=" * 80)
        print(yaml_block)
        print()

    # Report not found tasks
    if not_found_tasks:
        print(f"\n# Tasks not found: {', '.join(not_found_tasks)}")

if __name__ == "__main__":
    main()
