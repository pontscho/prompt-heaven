#!/usr/bin/env python3
"""
Display task status from requirements.yaml file.
Usage: python3 show_tasks.py /path/to/requirements.yaml
"""

import re
import sys

def show_tasks(yaml_file):
    """Parse and display tasks from requirements.yaml"""

    with open(yaml_file, 'r') as f:
        content = f.read()

    # Extract tasks section
    tasks_match = re.search(r'tasks:(.*)', content, re.DOTALL)
    if not tasks_match:
        print("No tasks section found in requirements.yaml")
        return

    tasks_text = tasks_match.group(1)

    print("=" * 120)
    print(f"{'Task ID':<30} | {'Status':<15} | {'Description':<70}")
    print("=" * 120)

    current_category = None
    tasks_list = []

    # Split into lines and parse
    lines = tasks_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for category headers (CATEGORY or PHASE)
        if '# CATEGORY' in line or '# PHASE' in line or '===' in line:
            category_match = re.search(r'# ((?:CATEGORY|PHASE) \d+: [^#\n]+)', line)
            if category_match:
                current_category = category_match.group(1).strip()
                if tasks_list:  # Print previous category's tasks if any
                    print()
                print(f"\n{current_category}")
                print("-" * 120)

        # Check for task entries
        elif '    - task_id:' in line:
            task_id_match = re.search(r'task_id:\s*(\S+)', line)
            if task_id_match:
                task_id = task_id_match.group(1)

                # Look ahead for description and status
                description = None
                status = "pending"

                for j in range(i+1, min(i+10, len(lines))):
                    if 'description:' in lines[j]:
                        desc_match = re.search(r'description:\s*(.+)', lines[j])
                        if desc_match:
                            description = desc_match.group(1).strip()
                    elif 'status:' in lines[j]:
                        status_match = re.search(r'status:\s*(\w+)', lines[j])
                        if status_match:
                            status = status_match.group(1)
                    elif '    - task_id:' in lines[j]:
                        break

                if description:
                    status_icon = {
                        "completed": "✅",
                        "in_progress": "🚧",
                        "cancel": "❌",
                        "pending": "⏳"
                    }.get(status, "⏳")

                    desc_short = description[:68] + "..." if len(description) > 68 else description
                    print(f"{task_id:<30} | {status_icon} {status:<12} | {desc_short:<70}")
                    tasks_list.append((task_id, status))

        i += 1

    print("=" * 120)

    # Summary statistics
    total = len(tasks_list)
    completed = sum(1 for _, status in tasks_list if status == "completed")
    in_progress = sum(1 for _, status in tasks_list if status == "in_progress")
    pending = sum(1 for _, status in tasks_list if status == "pending")
    cancelled = sum(1 for _, status in tasks_list if status == "cancel")

    print(f"\n📊 Summary: {completed}/{total} tasks completed")
    print(f"   ✅ Completed: {completed}")
    print(f"   🚧 In Progress: {in_progress}")
    print(f"   ⏳ Pending: {pending}")
    if cancelled > 0:
        print(f"   ❌ Cancelled: {cancelled}")

    if total > 0:
        percentage = (completed / total) * 100
        print(f"   Progress: {percentage:.1f}%")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 show_tasks.py /path/to/requirements.yaml")
        sys.exit(1)

    yaml_file = sys.argv[1]
    show_tasks(yaml_file)
