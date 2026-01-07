#!/usr/bin/env python3
"""
Display raw YAML blocks for specific tasks from requirements.yaml file.
Usage: python3 show_task_details.py task-001 task-002 task-003 ...
"""

import re
import sys
import os

def find_task_yaml_block(content, task_id):
    """Find and extract the raw YAML block for a specific task"""

    # Find the task block
    pattern = rf'    - task_id:\s*{re.escape(task_id)}\b'
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

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 show_task_details.py task-001 task-002 task-003 ...")
        print("\nExample:")
        print("  python3 show_task_details.py task-001")
        print("  python3 show_task_details.py task-001 task-002 task-003")
        sys.exit(1)

    task_ids = sys.argv[1:]

    # Find requirements.yaml in current directory or parent directories
    yaml_file = None
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

    with open(yaml_file, 'r') as f:
        content = f.read()

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
