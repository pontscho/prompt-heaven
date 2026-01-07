#!/usr/bin/env python3
"""
Mass update task status in requirements.yaml file.
Usage: python3 update_tasks.py <status> <task1> <task2> ...
Example: python3 update_tasks.py completed task-001 task-002 task-003
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
            # Look for task_id line
            if re.match(rf'^\s*-?\s*task_id:\s*{re.escape(task_id)}\s*$', lines[i]):
                task_found = True

                # Find the status line within the next 10 lines
                for j in range(i + 1, min(i + 11, len(lines))):
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

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 update_tasks.py <status> <task1> <task2> ...")
        print("Example: python3 update_tasks.py completed task-001 task-002")
        print("Valid statuses: pending, completed, in_progress")
        sys.exit(1)

    status = sys.argv[1]
    task_ids = sys.argv[2:]

    yaml_file = "requirements.yaml"
    update_task_status(yaml_file, status, task_ids)
