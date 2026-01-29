#!/usr/bin/env python3
"""
Extract implementation plan from requirements.yaml in compact YAML format.

Usage:
    task-implementation-plan.py [task_id1] [task_id2] ...
    task-implementation-plan.py --doc=path/to/plan.md [task_id1] [task_id2] ...

Options:
    --doc=PATH    Path to feature implementation plan document to include in output
                  Default: ${PROJECT_ROOT}/docs/feature-implementation-plan.md
    --no-doc      Skip including the implementation plan document
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


def extract_task_data(content, task_id):
	"""Extract data for a specific task from requirements.yaml"""
	# Find the task block by task_id
	# Pattern: look for task with matching id in the tasks list
	task_pattern = rf'- id:\s*{re.escape(task_id)}\s*\n(.*?)(?=\n\s*- id:|\n[a-z_]+:|\Z)'
	task_match = re.search(task_pattern, content, re.DOTALL)

	if not task_match:
		return None

	task_block = task_match.group(0)

	# Extract fields from task block
	result = {'id': task_id}

	# Extract simple fields
	for field in ['name', 'status', 'priority', 'phase']:
		match = re.search(rf'^\s*{field}:\s*(.+)$', task_block, re.MULTILINE)
		if match:
			result[field] = match.group(1).strip().strip('"\'')

	# Extract implementation_details (multiline)
	impl_match = re.search(r'implementation_details:\s*\|\s*\n(.*?)(?=\n\s*[a-z_]+:|\Z)', task_block, re.DOTALL)
	if impl_match:
		result['implementation_details'] = impl_match.group(1).rstrip()

	# Extract test_requirements (multiline)
	test_match = re.search(r'test_requirements:\s*\|\s*\n(.*?)(?=\n\s*[a-z_]+:|\Z)', task_block, re.DOTALL)
	if test_match:
		result['test_requirements'] = test_match.group(1).rstrip()

	# Extract code_references (list)
	refs_match = re.search(r'code_references:\s*\n((?:\s*-\s*.+\n?)+)', task_block)
	if refs_match:
		refs_text = refs_match.group(1)
		refs = re.findall(r'-\s*["\']?([^"\']+)["\']?', refs_text)
		result['code_references'] = [r.strip() for r in refs]

	# Extract dependencies (list)
	deps_match = re.search(r'dependencies:\s*\n((?:\s*-\s*.+\n?)+)', task_block)
	if deps_match:
		deps_text = deps_match.group(1)
		deps = re.findall(r'-\s*["\']?([^"\']+)["\']?', deps_text)
		result['dependencies'] = [d.strip() for d in deps]

	# Extract target_files (list)
	files_match = re.search(r'target_files:\s*\n((?:\s*-\s*.+\n?)+)', task_block)
	if files_match:
		files_text = files_match.group(1)
		files = re.findall(r'-\s*["\']?([^"\']+)["\']?', files_text)
		result['target_files'] = [f.strip() for f in files]

	return result


def extract_full_implementation_plan(content):
	"""Extract the full implementation_plan section (legacy mode)"""
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


def print_task_data(task_data):
	"""Print task data in YAML-like format"""
	print(f"- id: {task_data['id']}")
	for field in ['name', 'status', 'priority', 'phase']:
		if field in task_data:
			print(f"  {field}: {task_data[field]}")

	if 'dependencies' in task_data and task_data['dependencies']:
		print("  dependencies:")
		for dep in task_data['dependencies']:
			print(f"    - {dep}")

	if 'target_files' in task_data and task_data['target_files']:
		print("  target_files:")
		for f in task_data['target_files']:
			print(f"    - {f}")

	if 'code_references' in task_data and task_data['code_references']:
		print("  code_references:")
		for ref in task_data['code_references']:
			print(f"    - {ref}")

	if 'implementation_details' in task_data:
		print("  implementation_details: |")
		for line in task_data['implementation_details'].split('\n'):
			print(f"    {line}")

	if 'test_requirements' in task_data:
		print("  test_requirements: |")
		for line in task_data['test_requirements'].split('\n'):
			print(f"    {line}")


def print_full_plan(plan_data):
	"""Print full implementation plan in YAML format"""
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

	# Find project root for default paths
	project_root = find_project_root()

	# Handle --doc parameter
	if not args.no_doc:
		if args.doc_path:
			doc_path = args.doc_path
		else:
			# Default doc path
			doc_path = os.path.join(project_root, 'docs', 'feature-implementation-plan.md')

		# Expand environment variables
		doc_path = os.path.expandvars(doc_path)

		if os.path.exists(doc_path):
			dump_doc_file(doc_path)

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

	# Extract and print task data
	if args.task_ids:
		# Extract specific tasks
		print("=" * 80)
		print("# TASK SPECIFICATIONS")
		print("=" * 80)
		print("tasks:")

		not_found = []
		for task_id in args.task_ids:
			task_data = extract_task_data(content, task_id)
			if task_data:
				print_task_data(task_data)
				print()
			else:
				not_found.append(task_id)

		if not_found:
			print(f"# Warning: Tasks not found: {', '.join(not_found)}", file=sys.stderr)
			if len(not_found) == len(args.task_ids):
				sys.exit(1)
	else:
		# Extract full implementation plan (legacy mode)
		print("=" * 80)
		print("# REQUIREMENTS SUMMARY")
		print("=" * 80)
		plan_data = extract_full_implementation_plan(content)
		print_full_plan(plan_data)


if __name__ == "__main__":
	main()
