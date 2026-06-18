#!/usr/bin/env python3
"""
Extract implementation plan from requirements.yaml in compact YAML format.
Usage: python3 task-implementation-plan.py [path_to_requirements.yaml] [task_id1] [task_id2] ...
If task_id(s) are provided, only those specific tasks will be displayed.
If no task_id is provided, the complete implementation plan will be displayed.
"""

import re
import sys
import os

def extract_single_task(tasks_content, task_id):
    """Extract a single task by task_id from the tasks section"""
    # Match task block starting with the specified task_id
    # Pattern: from "- task_id: TASK_ID" to next "- task_id:" or end
    # (task_id value may be quoted: task_id: "task-003")
    pattern = rf'''(    - task_id:\s*["']?{re.escape(task_id)}\b.*?)(?=\n    - task_id:|\Z)'''
    task_match = re.search(pattern, tasks_content, re.DOTALL)

    if task_match:
        return task_match.group(1).strip()
    return None

def extract_multiple_tasks(tasks_content, task_ids):
    """Extract multiple tasks by task_ids from the tasks section"""
    extracted_tasks = []
    not_found_tasks = []

    for task_id in task_ids:
        task = extract_single_task(tasks_content, task_id)
        if task:
            extracted_tasks.append(task)
        else:
            not_found_tasks.append(task_id)

    return extracted_tasks, not_found_tasks

def extract_implementation_plan(yaml_file, task_ids=None):
    """Extract implementation_plan, complete flag, success_criteria, and context_summary"""

    with open(yaml_file, 'r') as f:
        content = f.read()

    # Extract complete flag
    complete_match = re.search(r'^complete:\s*(.+)$', content, re.MULTILINE)
    complete = complete_match.group(1).strip() if complete_match else "false"

    # Extract context_summary section (captured patterns from planning)
    context_match = re.search(r'^context_summary:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
    context_summary = context_match.group(1).strip() if context_match else ""

    # Extract implementation_plan section
    impl_match = re.search(r'^implementation_plan:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
    implementation_plan = impl_match.group(1).strip() if impl_match else ""

    # Extract success_criteria section
    success_match = re.search(r'^success_criteria:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
    success_criteria = success_match.group(1).strip() if success_match else ""

    # If task_ids are provided, filter to show only those tasks
    if task_ids and implementation_plan:
        # Extract tasks section
        tasks_match = re.search(r'  tasks:(.*)', implementation_plan, re.DOTALL)
        if tasks_match:
            tasks_content = tasks_match.group(1)
            extracted_tasks, not_found_tasks = extract_multiple_tasks(tasks_content, task_ids)

            if not_found_tasks:
                print(f"Error: Task(s) {', '.join(repr(t) for t in not_found_tasks)} not found in requirements.yaml", file=sys.stderr)
                sys.exit(1)

            if extracted_tasks:
                # Reconstruct implementation_plan with only the selected tasks
                # Keep affected_files, new_files, reference_files sections
                non_tasks_match = re.search(r'(.*?)  tasks:', implementation_plan, re.DOTALL)
                non_tasks = non_tasks_match.group(1).strip() if non_tasks_match else ""

                # Combine all extracted tasks
                combined_tasks = "\n    ".join(extracted_tasks)
                implementation_plan = f"{non_tasks}\n  tasks:\n    {combined_tasks}" if non_tasks else f"  tasks:\n    {combined_tasks}"

    # Output compact YAML
    print(f"complete: {complete}")

    if context_summary:
        print("context_summary:")
        print(context_summary)

    if success_criteria:
        print("success_criteria:")
        print(success_criteria)

    if implementation_plan:
        print("implementation_plan:")
        print(implementation_plan)

def main():
    # Default to requirements.yaml in current directory or parent directories
    yaml_file = None
    task_ids = []

    if len(sys.argv) > 1:
        # Check if first argument is a yaml file
        if sys.argv[1].endswith('.yaml') or sys.argv[1].endswith('.yml'):
            yaml_file = sys.argv[1]
            # All remaining arguments are task_ids
            if len(sys.argv) > 2:
                task_ids = sys.argv[2:]
        else:
            # First argument is not a yaml file, so search for requirements.yaml
            # and treat all arguments as task_ids
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
            # All arguments are task_ids
            task_ids = sys.argv[1:]
    else:
        # No arguments, search for requirements.yaml
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

    extract_implementation_plan(yaml_file, task_ids if task_ids else None)

if __name__ == "__main__":
    main()
