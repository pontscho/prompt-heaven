#!/usr/bin/env python3
"""
Extract implementation plan from requirements.yaml in compact YAML format.

Usage:
    task-implementation-plan.py [task_id1] [task_id2] ...
    task-implementation-plan.py --doc=path/to/plan.md [task_id1] [task_id2] ...
    task-implementation-plan.py --yaml=path/to/requirements.yaml [task_id1] ...

Options:
    --doc=PATH    Path to feature implementation plan document to include in output
                  Default: ${PROJECT_ROOT}/docs/feature-implementation-plan.md
    --no-doc      Skip including the implementation plan document
    --yaml, -y    Path to requirements.yaml (default: auto-detect)

With task ids, the output is complete / context_summary / success_criteria and the
implementation_plan with its tasks list filtered to the requested tasks (each task
block verbatim). Without task ids, the whole implementation plan is printed.
A task id that is not found is an error (exit 1).
"""

import re
import sys
import os
import argparse


def find_project_root():
	"""Find project root by looking for .git directory or requirements.yaml"""
	current_dir = os.getcwd()
	for _ in range(10):
		if os.path.exists(os.path.join(current_dir, '.git')):
			return current_dir
		if os.path.exists(os.path.join(current_dir, 'requirements.yaml')):
			return current_dir
		parent = os.path.dirname(current_dir)
		if parent == current_dir:
			break
		current_dir = parent
	return os.getcwd()


def dump_doc_file(doc_path):
	"""Dump the feature implementation plan document to stdout"""
	if not os.path.exists(doc_path):
		print(f"# Warning: Implementation plan document not found: {doc_path}", file=sys.stderr)
		return False

	print("=" * 80)
	print("# FEATURE IMPLEMENTATION PLAN")
	print("=" * 80)
	with open(doc_path, 'r') as f:
		print(f.read())
	print("=" * 80)
	print()
	return True


def find_requirements_yaml():
	"""Find requirements.yaml in current or parent directories"""
	current_dir = os.getcwd()
	for _ in range(5):
		candidate = os.path.join(current_dir, 'requirements.yaml')
		if os.path.exists(candidate):
			return candidate
		parent = os.path.dirname(current_dir)
		if parent == current_dir:
			break
		current_dir = parent
	return None


def extract_single_task(tasks_content, task_id):
	"""Extract a single task block (verbatim) by task_id from the tasks section"""
	# Match task block starting with the specified task_id
	# Pattern: from "- task_id: TASK_ID" to next "- task_id:" or end
	# (task_id value may be quoted: task_id: "task-003")
	pattern = rf'''(    - task_id:\s*["']?{re.escape(task_id)}\b.*?)(?=\n    - task_id:|\Z)'''
	task_match = re.search(pattern, tasks_content, re.DOTALL)

	if task_match:
		return task_match.group(1).strip()
	return None


def extract_full_implementation_plan(content):
	"""Extract complete, context_summary, implementation_plan and success_criteria"""
	# Extract complete flag
	complete_match = re.search(r'^complete:\s*(.+)$', content, re.MULTILINE)
	complete = complete_match.group(1).strip() if complete_match else "false"

	# Extract context_summary section
	context_match = re.search(r'^context_summary:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
	context_summary = context_match.group(1).strip() if context_match else ""

	# Extract implementation_plan section
	impl_match = re.search(r'^implementation_plan:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
	implementation_plan = impl_match.group(1).strip() if impl_match else ""

	# Extract success_criteria section
	success_match = re.search(r'^success_criteria:(.*?)(?=^[a-z_]+:|\Z)', content, re.MULTILINE | re.DOTALL)
	success_criteria = success_match.group(1).strip() if success_match else ""

	return {
		'complete': complete,
		'context_summary': context_summary,
		'implementation_plan': implementation_plan,
		'success_criteria': success_criteria
	}


def filter_tasks(plan_data, task_ids):
	"""Restrict plan_data['implementation_plan'] to the given tasks.

	Returns the list of task ids that were not found (empty on success).
	The non-task parts of implementation_plan (affected_files, new_files,
	reference_files, ...) are kept.
	"""
	implementation_plan = plan_data['implementation_plan']
	tasks_match = re.search(r'  tasks:(.*)', implementation_plan, re.DOTALL)
	if not tasks_match:
		return list(task_ids)

	tasks_content = tasks_match.group(1)
	extracted_tasks = []
	not_found = []
	for task_id in task_ids:
		task = extract_single_task(tasks_content, task_id)
		if task:
			extracted_tasks.append(task)
		else:
			not_found.append(task_id)

	if not_found:
		return not_found

	# Reconstruct implementation_plan with only the selected tasks
	non_tasks_match = re.search(r'(.*?)  tasks:', implementation_plan, re.DOTALL)
	non_tasks = non_tasks_match.group(1).strip() if non_tasks_match else ""
	combined_tasks = "\n    ".join(extracted_tasks)
	if non_tasks:
		plan_data['implementation_plan'] = f"{non_tasks}\n  tasks:\n    {combined_tasks}"
	else:
		plan_data['implementation_plan'] = f"  tasks:\n    {combined_tasks}"
	return []


def print_full_plan(plan_data):
	"""Print implementation plan in YAML format"""
	print(f"complete: {plan_data['complete']}")

	if plan_data['context_summary']:
		print("context_summary:")
		print(plan_data['context_summary'])

	if plan_data['success_criteria']:
		print("success_criteria:")
		print(plan_data['success_criteria'])

	if plan_data['implementation_plan']:
		print("implementation_plan:")
		print(plan_data['implementation_plan'])


def main():
	parser = argparse.ArgumentParser(
		description='Extract implementation plan from requirements.yaml',
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog='''
Examples:
  %(prog)s                                    # Full implementation plan
  %(prog)s task-001 task-002                  # Specific tasks
  %(prog)s --doc=docs/plan.md task-001        # Include doc file + task
  %(prog)s --no-doc task-001                  # Task only, no doc file
'''
	)

	parser.add_argument('task_ids', nargs='*', help='Task IDs to extract (if none, extract full plan)')
	parser.add_argument('--doc', dest='doc_path', metavar='PATH',
		help='Path to feature implementation plan document (default: ${PROJECT_ROOT}/docs/feature-implementation-plan.md)')
	parser.add_argument('--no-doc', dest='no_doc', action='store_true',
		help='Skip including the implementation plan document')
	parser.add_argument('--yaml', '-y', dest='yaml_path', metavar='PATH',
		help='Path to requirements.yaml (default: auto-detect)')

	args = parser.parse_args()

	# Find requirements.yaml
	if args.yaml_path:
		yaml_file = args.yaml_path
	else:
		yaml_file = find_requirements_yaml()

	if not yaml_file or not os.path.exists(yaml_file):
		print("Error: requirements.yaml not found", file=sys.stderr)
		sys.exit(1)

	with open(yaml_file, 'r') as f:
		content = f.read()

	plan_data = extract_full_implementation_plan(content)

	# Resolve the requested tasks before printing anything, so a bad id fails cleanly
	if args.task_ids:
		not_found = filter_tasks(plan_data, args.task_ids)
		if not_found:
			print(f"Error: Task(s) {', '.join(repr(t) for t in not_found)} not found in {yaml_file}", file=sys.stderr)
			sys.exit(1)

	# Handle --doc parameter
	if not args.no_doc:
		if args.doc_path:
			doc_path = args.doc_path
		else:
			# Default doc path
			doc_path = os.path.join(find_project_root(), 'docs', 'feature-implementation-plan.md')

		# Expand environment variables
		doc_path = os.path.expandvars(doc_path)

		if args.doc_path or os.path.exists(doc_path):
			dump_doc_file(doc_path)

	print("=" * 80)
	if args.task_ids:
		print("# TASK SPECIFICATIONS")
	else:
		print("# REQUIREMENTS SUMMARY")
	print("=" * 80)
	print_full_plan(plan_data)


if __name__ == "__main__":
	main()
