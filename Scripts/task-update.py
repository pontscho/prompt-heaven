#!/usr/bin/env python3
"""
Mass update task status in requirements.yaml file.
Usage: task-update.py [--yaml PATH] <status> <task1> <task2> ...
Example: task-update.py completed task-001 task-002 task-003
         task-update.py --yaml feat/requirements.yaml completed task-001
--yaml / -y / --yaml=PATH may appear anywhere; default is ./requirements.yaml.
"""

import re
import sys

def update_task_status(yaml_file, status, task_ids):
    """Update status for multiple tasks in requirements.yaml"""

    # Validate status
    valid_statuses = ['pending', 'completed', 'in_progress', 'cancel']
    if status not in valid_statuses:
        print(f"Error: Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}")
        sys.exit(1)

    # Read file
    with open(yaml_file, 'r') as f:
        lines = f.readlines()

    # Track updates
    updated = []
    not_found = []

    for task_id in task_ids:
        task_found = False

        # Find the task and update its status line
        for i in range(len(lines)):
            # Look for task_id line (value may be quoted: task_id: "task-003")
            if re.match(rf'''^\s*-?\s*task_id:\s*["']?{re.escape(task_id)}["']?\s*$''', lines[i]):
                task_found = True

                # Find the status line, scanning to the next task / top-level key.
                # (A fixed N-line window misses status: when a long `description: |`
                #  block scalar precedes it; the break conditions below bound the scan.)
                for j in range(i + 1, len(lines)):
                    # Check if we've hit the next task or end of tasks section
                    if re.match(r'^\s*-?\s*task_id:', lines[j]) or re.match(r'^\w+:', lines[j]):
                        break

                    # Found status line, update it
                    if re.match(r'^\s+status:\s*\w+\s*$', lines[j]):
                        indent = re.match(r'^(\s+)status:', lines[j]).group(1)
                        lines[j] = f"{indent}status: {status}\n"
                        updated.append(task_id)
                        break
                break

        if not task_found:
            not_found.append(task_id)

    # Write back
    if updated:
        with open(yaml_file, 'w') as f:
            f.writelines(lines)

        print(f"✅ Updated {len(updated)} task(s) to status '{status}':")
        for task_id in updated:
            print(f"   - {task_id}")

    if not_found:
        print(f"\n⚠️  Warning: {len(not_found)} task(s) not found:")
        for task_id in not_found:
            print(f"   - {task_id}")
        sys.exit(1)

    if updated:
        print(f"\n📝 File updated: {yaml_file}")

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
                print(f"task-update.py: {arg} needs a PATH", file=sys.stderr)
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


if __name__ == "__main__":
    yaml_file, args = split_yaml_flag(sys.argv[1:])

    if len(args) < 2:
        print("Usage: task-update.py [--yaml PATH] <status> <task1> <task2> ...")
        print("Example: task-update.py completed task-001 task-002")
        print("         task-update.py --yaml feat/requirements.yaml completed task-001")
        print("Valid statuses: pending, completed, in_progress")
        print("--yaml, -y PATH: the requirements file to update (default: ./requirements.yaml)")
        sys.exit(1)

    status = args[0]
    task_ids = args[1:]

    if yaml_file is None:
        yaml_file = "requirements.yaml"
    try:
        update_task_status(yaml_file, status, task_ids)
    except OSError as e:
        print(f"task-update.py: cannot access {yaml_file}: {e.strerror or e}", file=sys.stderr)
        sys.exit(1)
