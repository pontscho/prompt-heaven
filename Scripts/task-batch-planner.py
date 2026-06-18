#!/usr/bin/env python3
"""
Task Batch Planner - Dependency-aware batch optimization for task execution.

Usage: task-batch-planner.py /path/to/requirements.yaml [--max-score=4]

Algorithm:
1. Build dependency graph from tasks
2. Topological sort to create execution levels
3. Detect file conflicts within each level
4. Form batches using greedy best-fit (smallest first)

Output: Structured batch plan with execution order.
"""

import re
import sys
from collections import defaultdict
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

		task = Task(task_id_match.group(1).strip('\'"'))

		# Extract description
		desc_match = re.search(r'description:\s*["\']?([^"\'\n]+)["\']?', block)
		if desc_match:
			task.description = desc_match.group(1).strip()

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

		# Extract dependencies
		deps_match = re.search(r'dependencies:\s*\n((?:\s+-\s+\S+\n?)+)', block)
		if deps_match:
			deps_text = deps_match.group(1)
			task.dependencies = [d.strip('\'"') for d in re.findall(r'-\s+(\S+)', deps_text)]
		elif 'dependencies: []' in block:
			task.dependencies = []

		# Extract code_references files
		refs_match = re.search(r'code_references:\s*\n((?:.*\n)*?)(?=\s+(?:api_references|test_requirements|dependencies):|\s+-\s+task_id:|\Z)', block)
		if refs_match:
			refs_text = refs_match.group(1)
			file_refs = re.findall(r'file:\s*(\S+)', refs_text)
			task.code_references = [f for f in file_refs if f != 'null']

		tasks[task.task_id] = task

	return tasks


def build_dependency_graph(tasks: Dict[str, Task]) -> Dict[str, Set[str]]:
	"""Build reverse dependency graph (who blocks whom)."""
	blocks: Dict[str, Set[str]] = defaultdict(set)

	for task_id, task in tasks.items():
		for dep in task.dependencies:
			if dep in tasks:
				blocks[dep].add(task_id)

	return blocks


def compute_execution_levels(tasks: Dict[str, Task]) -> List[List[str]]:
	"""
	Compute execution levels using topological sort.
	Level 0: Tasks with no dependencies
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
			has_conflict = any(has_file_conflict(task, tasks[bid]) for bid in batch)
			if has_conflict:
				i += 1
				continue

			# Add to batch
			batch.append(task_id)
			batch_score += task.size_score
			remaining.pop(i)
			# Don't increment i since we removed an element

		batches.append(batch)

	return batches


def generate_batch_plan(yaml_file: str, max_score: int = 6):
	"""Generate and display the complete batch plan."""
	tasks = parse_requirements_yaml(yaml_file)

	if not tasks:
		print("ERROR: No tasks found in requirements.yaml", file=sys.stderr)
		sys.exit(1)

	# Compute execution levels
	levels = compute_execution_levels(tasks)

	if not levels:
		print("All tasks already completed!")
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

	print_human_readable(levels, all_batches, max_score)


def print_human_readable(levels: List[List[str]], batches: List[dict], max_score: int):
	"""Print human-readable batch plan."""
	total_tasks = sum(len(level) for level in levels)

	# Dependency analysis
	print("DEPENDENCY ANALYSIS")
	print("-" * 80)
	for level_idx, level_task_ids in enumerate(levels):
		level_num = level_idx + 1  # 1-based numbering
		task_summary = ", ".join(f"{tid}" for tid in level_task_ids)
		if level_idx == 0:
			print(f"  Level {level_num} (independent): {task_summary}")
		else:
			print(f"  Level {level_num} (after L{level_num-1}): {task_summary}")
	print()

	# Batch plan as table
	print("BATCH PLAN")
	print("-" * 80)

	# Calculate column widths
	max_tasks_width = max(
		len(" + ".join(t['task_id'] for t in b["tasks"]))
		for b in batches
	)
	max_tasks_width = max(max_tasks_width, 5)  # minimum "Tasks" header width

	# Table header
	print(f"  {'Batch':<6} | {'Tasks':<{max_tasks_width}} | {'Score':<5} | {'Note'}")
	print(f"  {'-'*6}-+-{'-'*max_tasks_width}-+-{'-'*5}-+-{'-'*30}")

	# Table rows
	for batch in batches:
		task_parts = " + ".join(t['task_id'] for t in batch["tasks"])
		score = batch["combined_score"]

		# Check for file conflicts that prevented merging
		note = ""
		if len(batch["tasks"]) == 1 and score < max_score:
			note = "file conflict / no compatible"

		print(f"  {batch['batch_number']:<6} | {task_parts:<{max_tasks_width}} | {score:<5} | {note}")

	print()
	print(f"  SUMMARY: {total_tasks} tasks in {len(batches)} batches across {len(levels)} levels")
	print()

def main():
	import argparse

	parser = argparse.ArgumentParser(description="Task Batch Planner")
	parser.add_argument("yaml_file", nargs="?", default="requirements.yaml",
						help="Path to requirements.yaml")
	parser.add_argument("--max-score", type=int, default=6,
						help="Maximum combined score per batch (default: 6)")

	args = parser.parse_args()

	generate_batch_plan(args.yaml_file, args.max_score)


if __name__ == "__main__":
	main()
