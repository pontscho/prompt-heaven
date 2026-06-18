#!/usr/bin/env python3
"""
Task Plan - Combined task status and batch planning for requirements.yaml.

Usage: task-plan.py /path/to/requirements.yaml [--max-score=6]

Features:
1. Display all tasks with status, size, and description
2. Show summary statistics (completed, pending, in_progress)
3. Dependency analysis with execution levels
4. Batch plan with file conflict detection
"""

import re
import sys
from typing import Dict, List, Set, Optional

# Size score mapping
SIZE_SCORES = {
	"SS": 1,
	"S": 2,
	"M": 3,
	"L": 4,
	"XL": 5,
	"XXL": 6,
}
DEFAULT_SIZE_SCORE = 3  # M

STATUS_ICONS = {
	"completed": "✅",
	"in_progress": "🚧",
	"cancel": "❌",
	"pending": "⏳",
}

class Task:
	"""Represents a single task with its metadata."""

	def __init__(self, task_id: str):
		self.task_id = task_id
		self.description = ""
		self.file_path: Optional[str] = None
		self.function_name: Optional[str] = None
		self.type = "modify"
		self.status = "pending"
		self.size = "M"
		self.dependencies: List[str] = []
		self.code_references: List[str] = []

	@property
	def size_score(self) -> int:
		return SIZE_SCORES.get(self.size.upper(), DEFAULT_SIZE_SCORE)

	def get_all_files(self) -> Set[str]:
		"""Get all files this task touches (target + references)."""
		files = set()
		if self.file_path:
			files.add(self.file_path)
		files.update(self.code_references)
		return files

	def to_dict(self) -> dict:
		return {
			"task_id": self.task_id,
			"description": self.description,
			"file_path": self.file_path,
			"function_name": self.function_name,
			"type": self.type,
			"status": self.status,
			"size": self.size,
			"size_score": self.size_score,
			"dependencies": self.dependencies,
		}


def extract_description(block: str) -> str:
	"""Extract a task description, supporting inline and YAML block scalar (|, >) forms."""
	# Block scalar: `description: |` / `>` (optional chomp/indent indicators),
	# content lives on the following more-indented lines.
	block_match = re.search(
		r'^(?P<indent>[ \t]*)description:[ \t]*[|>][+\-0-9]*[ \t]*\n'
		r'(?P<body>(?:(?P=indent)[ \t]+.*\n?|[ \t]*\n)*)',
		block, re.MULTILINE)
	if block_match:
		lines = [ln.strip() for ln in block_match.group('body').splitlines()]
		return ' '.join(ln for ln in lines if ln).strip()

	# Inline double-quoted: capture to the LAST quote, then unescape \" and \\
	m = re.search(r'description:[ \t]*"(.*)"[ \t]*$', block, re.MULTILINE)
	if m:
		return m.group(1).replace('\\"', '"').replace('\\\\', '\\').strip()
	# Inline single-quoted (YAML '' escape)
	m = re.search(r"description:[ \t]*'(.*)'[ \t]*$", block, re.MULTILINE)
	if m:
		return m.group(1).replace("''", "'").strip()
	# Bare inline (no quotes)
	m = re.search(r'description:[ \t]*(.+?)[ \t]*$', block, re.MULTILINE)
	if m:
		return m.group(1).strip()

	return ""


def parse_requirements_yaml(yaml_file: str) -> Dict[str, Task]:
	"""Parse tasks from requirements.yaml using regex (no yaml library dependency)."""
	with open(yaml_file, 'r') as f:
		content = f.read()

	tasks: Dict[str, Task] = {}

	# Find tasks section
	tasks_match = re.search(r'^\s*tasks:\s*$', content, re.MULTILINE)
	if not tasks_match:
		return tasks

	tasks_text = content[tasks_match.end():]

	# Split into individual task blocks
	task_blocks = re.split(r'\n\s{4}- task_id:', tasks_text)

	for i, block in enumerate(task_blocks):
		if i == 0 and 'task_id:' not in block:
			continue

		# Add back the task_id prefix for first block processing
		if i > 0:
			block = 'task_id:' + block

		# Extract task_id
		task_id_match = re.search(r'task_id:\s*(\S+)', block)
		if not task_id_match:
			continue

		task = Task(task_id_match.group(1))

		# Extract description (handles inline and block scalar |/> forms)
		task.description = extract_description(block)

		# Extract file_path
		file_match = re.search(r'file_path:\s*(\S+)', block)
		if file_match and file_match.group(1) != 'null':
			task.file_path = file_match.group(1)

		# Extract function_name
		func_match = re.search(r'function_name:\s*(\S+)', block)
		if func_match and func_match.group(1) != 'null':
			task.function_name = func_match.group(1)

		# Extract type
		type_match = re.search(r'type:\s*(\w+)', block)
		if type_match:
			task.type = type_match.group(1)

		# Extract status
		status_match = re.search(r'status:\s*(\w+)', block)
		if status_match:
			task.status = status_match.group(1)

		# Extract size
		size_match = re.search(r'\n\s+size:\s*(\w+)', block)
		if size_match:
			task.size = size_match.group(1).upper()

		# Extract dependencies — supports inline `[a, b]` and multi-line `- a\n- b` formats
		inline_deps_match = re.search(r'dependencies:\s*\[([^\]]*)\]', block)
		if inline_deps_match:
			deps_str = inline_deps_match.group(1).strip()
			task.dependencies = [d.strip() for d in deps_str.split(',') if d.strip()]
		else:
			multiline_deps_match = re.search(r'dependencies:\s*\n((?:\s+-\s+\S+\n?)+)', block)
			if multiline_deps_match:
				deps_text = multiline_deps_match.group(1)
				task.dependencies = re.findall(r'-\s+(\S+)', deps_text)

		# Extract code_references files
		refs_match = re.search(r'code_references:\s*\n((?:.*\n)*?)(?=\s+(?:api_references|test_requirements|dependencies):|\s+-\s+task_id:|\Z)', block)
		if refs_match:
			refs_text = refs_match.group(1)
			file_refs = re.findall(r'file:\s*(\S+)', refs_text)
			task.code_references = [f for f in file_refs if f != 'null']

		tasks[task.task_id] = task

	return tasks


def compute_execution_levels(tasks: Dict[str, Task]) -> List[List[str]]:
	"""
	Compute execution levels using topological sort.
	Level 1: Tasks with no dependencies
	Level N: Tasks whose dependencies are all in levels < N
	"""
	# Filter to incomplete tasks only
	incomplete = {tid: t for tid, t in tasks.items() if t.status in ('pending', 'in_progress')}

	if not incomplete:
		return []

	# Calculate in-degree for each task (only counting incomplete dependencies)
	in_degree: Dict[str, int] = {}
	for task_id, task in incomplete.items():
		# Count only incomplete dependencies
		incomplete_deps = [d for d in task.dependencies if d in incomplete]
		in_degree[task_id] = len(incomplete_deps)

	levels: List[List[str]] = []
	remaining = set(incomplete.keys())

	while remaining:
		# Find all tasks with no remaining dependencies
		current_level = [tid for tid in remaining if in_degree.get(tid, 0) == 0]

		if not current_level:
			# Circular dependency detected - break with remaining tasks
			print(f"WARNING: Circular dependency detected. Remaining tasks: {remaining}", file=sys.stderr)
			levels.append(list(remaining))
			break

		# Sort by task_id for deterministic ordering
		current_level.sort()
		levels.append(current_level)

		# Remove current level from remaining and update in-degrees
		for task_id in current_level:
			remaining.remove(task_id)
			# Decrease in-degree for tasks that depend on this one
			for other_id in remaining:
				if task_id in incomplete[other_id].dependencies:
					in_degree[other_id] -= 1

	return levels


def has_file_conflict(task_a: Task, task_b: Task) -> bool:
	"""Check if two tasks have overlapping file scopes."""
	files_a = task_a.get_all_files()
	files_b = task_b.get_all_files()
	return bool(files_a & files_b)


def form_batches(tasks: Dict[str, Task], task_ids: List[str], max_score: int = 6) -> List[List[str]]:
	"""
	Form batches from tasks at the same execution level.
	Uses greedy best-fit algorithm (smallest tasks first).
	"""
	if not task_ids:
		return []

	# Sort by size score (smallest first), then by task_id for determinism
	sorted_ids = sorted(task_ids, key=lambda tid: (tasks[tid].size_score, tid))

	batches: List[List[str]] = []
	remaining = list(sorted_ids)

	while remaining:
		# Start new batch with smallest remaining task
		batch = [remaining.pop(0)]
		batch_score = tasks[batch[0]].size_score

		# Try to add more tasks to this batch
		i = 0
		while i < len(remaining):
			task_id = remaining[i]
			task = tasks[task_id]

			# Check score constraint
			if batch_score + task.size_score > max_score:
				i += 1
				continue

			# Check file conflict with all tasks already in batch
			conflict = any(has_file_conflict(task, tasks[bid]) for bid in batch)
			if conflict:
				i += 1
				continue

			# Add to batch
			batch.append(task_id)
			batch_score += task.size_score
			remaining.pop(i)
			# Don't increment i since we removed an element

		batches.append(batch)

	return batches


def print_task_status(tasks: Dict[str, Task]):
	"""Print task status table."""
	# Sort tasks by task_id
	sorted_tasks = sorted(tasks.values(), key=lambda t: t.task_id)

	# Calculate adaptive column widths
	max_id_width = max(len(t.task_id) for t in sorted_tasks)
	max_id_width = max(max_id_width, 7)  # minimum "Task ID" header width

	max_status_width = max(len(t.status) for t in sorted_tasks) + 2  # +2 for emoji
	max_status_width = max(max_status_width, 6)  # minimum "Status" header width

	# Description width adapts to remaining space (target ~120 char total line width)
	desc_width = 120 - max_id_width - max_status_width - 20  # 20 = size + separators
	desc_width = max(desc_width, 40)  # minimum description width

	total_width = max_id_width + max_status_width + 6 + desc_width + 12  # columns + separators

	print("TASK STATUS")
	print("-" * (total_width - 2))
	print(f"  {'Task ID':<{max_id_width}} | {'Status':<{max_status_width+1}} | {'Size':<4} | {'Description':<{desc_width}}")
	print(f"  {'-'*max_id_width}-+-{'-'*(max_status_width+1)}-+-{'-'*4}-+-{'-'*desc_width}")

	for task in sorted_tasks:
		icon = STATUS_ICONS.get(task.status, "⏳")
		status_str = f"{icon} {task.status}"
		print(f"  {task.task_id:<{max_id_width}} | {status_str:<{max_status_width}} | {task.size:<4} | {task.description}")

	return total_width

def print_summary(tasks: Dict[str, Task]):
	"""Print summary statistics."""
	total = len(tasks)
	completed = sum(1 for t in tasks.values() if t.status == "completed")
	in_progress = sum(1 for t in tasks.values() if t.status == "in_progress")
	pending = sum(1 for t in tasks.values() if t.status == "pending")
	cancelled = sum(1 for t in tasks.values() if t.status == "cancel")

	# Effort breakdown
	size_counts = {"SS": 0, "S": 0, "M": 0, "L": 0, "XL": 0, "XXL": 0}
	for task in tasks.values():
		if task.size in size_counts:
			size_counts[task.size] += 1

	print()
	print(f"📊 SUMMARY: {completed}/{total} tasks completed ({(completed/total*100) if total > 0 else 0:.0f}%)")
	print(f"   ✅ Completed:   {completed}")
	print(f"   🚧 In Progress: {in_progress}")
	print(f"   ⏳ Pending:     {pending}")
	if cancelled > 0:
		print(f"   ❌ Cancelled:   {cancelled}")

	# Show effort breakdown if any sizes are defined
	if any(size_counts.values()):
		effort_parts = [f"{sz}:{cnt}" for sz, cnt in size_counts.items() if cnt > 0]
		print(f"   📏 Effort: {' | '.join(effort_parts)}")
	print()


def print_batch_plan(tasks: Dict[str, Task], levels: List[List[str]], max_score: int, table_width: int):
	"""Print dependency analysis and batch plan."""
	if not levels:
		print("All tasks already completed - no batch plan needed.")
		return

	# Generate batches for each level
	all_batches: List[dict] = []
	batch_number = 1

	for level_idx, level_task_ids in enumerate(levels):
		level_batches = form_batches(tasks, level_task_ids, max_score)

		for batch_tasks in level_batches:
			batch_info = {
				"batch_number": batch_number,
				"level": level_idx,
				"tasks": [tasks[tid].to_dict() for tid in batch_tasks],
				"combined_score": sum(tasks[tid].size_score for tid in batch_tasks),
				"task_ids": batch_tasks,
			}
			all_batches.append(batch_info)
			batch_number += 1

	total_incomplete = sum(len(level) for level in levels)

	# Dependency analysis
	print("DEPENDENCY ANALYSIS")
	print("-" * (table_width - 2))
	for level_idx, level_task_ids in enumerate(levels):
		level_num = level_idx + 1  # 1-based numbering
		task_summary = ", ".join(level_task_ids)
		if level_idx == 0:
			print(f"  Level {level_num} (independent): {task_summary}")
		else:
			print(f"  Level {level_num} (after L{level_num-1}): {task_summary}")
	print()

	# Batch plan as table
	print("BATCH PLAN")
	print("-" * (table_width - 2))

	# Calculate column widths
	max_tasks_width = max(
		len(" + ".join(t['task_id'] for t in b["tasks"]))
		for b in all_batches
	)
	max_tasks_width = max(max_tasks_width, 5)  # minimum "Tasks" header width

	# Note column width adapts to fill remaining space
	note_width = table_width - max_tasks_width - 24  # 24 = batch + score + separators
	note_width = max(note_width, 20)  # minimum note width

	# Table header
	print(f"  {'Batch':<6} | {'Tasks':<{max_tasks_width}} | {'Score':<5} | {'Note':<{note_width}}")
	print(f"  {'-'*6}-+-{'-'*max_tasks_width}-+-{'-'*5}-+-{'-'*note_width}")

	# Table rows
	for batch in all_batches:
		task_parts = " + ".join(t['task_id'] for t in batch["tasks"])
		score = batch["combined_score"]

		# Check for file conflicts that prevented merging
		note = ""
		if len(batch["tasks"]) == 1 and score < max_score:
			note = "file conflict / no compatible"

		print(f"  {batch['batch_number']:<6} | {task_parts:<{max_tasks_width}} | {score:<5} | {note:<{note_width}}")

	print()
	print(f"📊 TOTAL: {total_incomplete} tasks in {len(all_batches)} batches across {len(levels)} levels")
	print()

def main():
	import argparse

	parser = argparse.ArgumentParser(description="Task Plan - Status and Batch Planning")
	parser.add_argument("yaml_file", nargs="?", default="requirements.yaml",
						help="Path to requirements.yaml")
	parser.add_argument("--max-score", type=int, default=6,
						help="Maximum combined score per batch (default: 6)")
	parser.add_argument("--full", action="store_true",
						help="Show full output (reserved for future use)")

	args = parser.parse_args()

	# Parse tasks
	tasks = parse_requirements_yaml(args.yaml_file)

	if not tasks:
		print("ERROR: No tasks found in requirements.yaml", file=sys.stderr)
		sys.exit(1)

	# Print task status (returns table width for consistent formatting)
	table_width = print_task_status(tasks)

	# Print summary
	print_summary(tasks)

	# Compute execution levels and print batch plan
	levels = compute_execution_levels(tasks)
	print_batch_plan(tasks, levels, args.max_score, table_width)

if __name__ == "__main__":
	main()
