#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["PyYAML>=6"]
# ///
"""
task-validator.py - Semantic validator for requirements.yaml (p:task-plan output).

requirements.yaml is the sole input for the p:implement / p:requirements skills.
A malformed or inconsistent plan (dangling dependency, dependency cycle, wrong
effort_breakdown, invalid enum) is otherwise only discovered mid-implementation,
expensively. This validator runs deterministically before `complete: true` is set
and catches those classes of error.

Unlike the regex-based task-*.py family ("no yaml library dependency"), a *validator*
must check YAML well-formedness itself, which regex cannot. PyYAML is therefore used
to parse, behind a clean import guard.

Phase-aware:
  complete: false -> only requirements / constraints / success_criteria are required.
  complete: true  -> the full implementation_plan + context_summary are required too.

Usage: task-validator.py [requirements.yaml] [--strict] [--quiet] [--json]

Exit codes:
  0  no ERROR (and, with --strict, no WARNING either)
  1  at least one ERROR (or, with --strict, at least one WARNING)
  2  file not readable / YAML parse error / PyYAML missing
"""

import sys

try:
	import yaml
except ImportError:
	sys.stderr.write(
		"ERROR: PyYAML is required but not installed.\n"
		"       Install it with: pip install 'PyYAML>=6'\n")
	sys.exit(2)


ERROR = "ERROR"
WARNING = "WARNING"

# Enum constants (mirrors the p:task-plan SKILL.md schema)
REQ_CATEGORIES   = {"architecture", "dependencies", "data", "security", "interface", "implementation"}
REQ_STATUS       = {"pending", "answered"}
CONSTRAINT_TYPES = {"technical", "business", "security"}
TASK_TYPES       = {"create", "modify", "delete", "test"}
TASK_STATUS      = {"pending", "completed", "cancel"}
SIZES            = {"ss", "s", "m", "l", "xl", "xxl"}
SIZE_ORDER       = ["ss", "s", "m", "l", "xl", "xxl"]  # for aggregation

TOP_LEVEL_KEYS = {
	"original_request", "goal", "complete", "requirements", "constraints",
	"success_criteria", "context_summary", "implementation_plan",
}


class Issue:
	"""A single validation finding: level + locator path + message."""

	def __init__(self, level, path, message):
		self.level = level
		self.path = path
		self.message = message

	def to_dict(self):
		return {"path": self.path, "message": self.message}


class Validator:
	"""Collects issues from a parsed requirements.yaml document."""

	def __init__(self, doc):
		self.doc = doc
		self.issues = []
		self.complete = isinstance(doc, dict) and doc.get("complete") is True

	# -- issue helpers -------------------------------------------------

	def error(self, path, message):
		self.issues.append(Issue(ERROR, path, message))

	def warn(self, path, message):
		self.issues.append(Issue(WARNING, path, message))

	@staticmethod
	def _is_nonempty_str(value):
		return isinstance(value, str) and value.strip() != ""

	# -- entry point ---------------------------------------------------

	def validate(self):
		if not isinstance(self.doc, dict):
			self.error("(root)", "top-level document must be a mapping/object")
			return self.issues
		self.check_top_level()
		self.check_requirements()
		self.check_constraints()
		if self.complete:
			self.check_context_summary()
			self.check_implementation_plan()
		return self.issues

	# -- top level -----------------------------------------------------

	def check_top_level(self):
		d = self.doc
		if not self._is_nonempty_str(d.get("original_request")):
			self.error("original_request", "must be a non-empty string")
		if not self._is_nonempty_str(d.get("goal")):
			self.error("goal", "must be a non-empty string")
		if not isinstance(d.get("complete"), bool):
			self.error("complete", "must be a boolean")
		if not isinstance(d.get("requirements"), list):
			self.error("requirements", "must be a list")
		if not isinstance(d.get("constraints"), list):
			self.error("constraints", "must be a list")
		sc = d.get("success_criteria")
		if not isinstance(sc, list) or len(sc) == 0:
			self.error("success_criteria", "must be a non-empty list")
		else:
			for i, item in enumerate(sc):
				if not self._is_nonempty_str(item):
					self.error(f"success_criteria[{i}]", "must be a non-empty string")
		for key in d:
			if key not in TOP_LEVEL_KEYS:
				self.warn(key, "unknown top-level key (typo?)")

	# -- requirements --------------------------------------------------

	def check_requirements(self):
		reqs = self.doc.get("requirements")
		if not isinstance(reqs, list):
			return
		has_pending = False
		for i, req in enumerate(reqs):
			base = f"requirements[{i}]"
			if not isinstance(req, dict):
				self.error(base, "must be a mapping")
				continue
			if req.get("category") not in REQ_CATEGORIES:
				self.error(f"{base}.category", f"must be one of {sorted(REQ_CATEGORIES)}")
			if not self._is_nonempty_str(req.get("question")):
				self.error(f"{base}.question", "must be a non-empty string")
			status = req.get("status")
			if status not in REQ_STATUS:
				self.error(f"{base}.status", f"must be one of {sorted(REQ_STATUS)}")
			if status == "answered" and not self._is_nonempty_str(req.get("answer")):
				self.error(f"{base}.answer", "must be present and non-empty when status is 'answered'")
			if status == "pending":
				has_pending = True
			for opt in ("details", "options"):
				if opt in req and not isinstance(req[opt], list):
					self.warn(f"{base}.{opt}", "should be a list")
		if self.complete and has_pending:
			self.warn("requirements", "complete:true but at least one requirement has status:pending")

	# -- constraints ---------------------------------------------------

	def check_constraints(self):
		cons = self.doc.get("constraints")
		if not isinstance(cons, list):
			return
		for i, con in enumerate(cons):
			base = f"constraints[{i}]"
			if not isinstance(con, dict):
				self.error(base, "must be a mapping")
				continue
			if con.get("type") not in CONSTRAINT_TYPES:
				self.error(f"{base}.type", f"must be one of {sorted(CONSTRAINT_TYPES)}")
			if not self._is_nonempty_str(con.get("description")):
				self.error(f"{base}.description", "must be a non-empty string")
			if not self._is_nonempty_str(con.get("impact")):
				self.error(f"{base}.impact", "must be a non-empty string")

	# -- context_summary (complete:true) -------------------------------

	def check_context_summary(self):
		cs = self.doc.get("context_summary")
		if not isinstance(cs, dict):
			self.error("context_summary", "must be present (mapping) when complete:true")
			return
		if "key_patterns" in cs and not isinstance(cs["key_patterns"], list):
			self.warn("context_summary.key_patterns", "should be a list")

	# -- implementation_plan (complete:true) ---------------------------

	def check_implementation_plan(self):
		plan = self.doc.get("implementation_plan")
		if not isinstance(plan, dict):
			self.error("implementation_plan", "must be present (mapping) when complete:true")
			return
		if plan.get("total_effort") not in SIZES:
			self.error("implementation_plan.total_effort", f"must be one of {SIZE_ORDER}")
		self._check_effort_breakdown(plan)
		for key in ("affected_files", "new_files", "reference_files"):
			val = plan.get(key)
			if not isinstance(val, list):
				self.error(f"implementation_plan.{key}", "must be a list of strings")
			else:
				for i, item in enumerate(val):
					if not self._is_nonempty_str(item):
						self.error(f"implementation_plan.{key}[{i}]", "must be a non-empty string")
		tasks = plan.get("tasks")
		if not isinstance(tasks, list) or len(tasks) == 0:
			self.error("implementation_plan.tasks", "must be a non-empty list")
			return
		self._check_tasks(tasks, plan)

	def _check_effort_breakdown(self, plan):
		eb = plan.get("effort_breakdown")
		if not isinstance(eb, dict):
			self.error("implementation_plan.effort_breakdown", "must be a mapping of size -> count")
			return
		for key, value in eb.items():
			loc = f"implementation_plan.effort_breakdown.{key}"
			if key not in SIZES:
				self.error(loc, f"unknown size key; must be one of {SIZE_ORDER}")
			if isinstance(value, bool) or not isinstance(value, int):
				self.error(loc, "value must be an integer")

	# -- tasks ---------------------------------------------------------

	def _check_tasks(self, tasks, plan):
		ids = {}                            # task_id -> [indices]
		actual_sizes = {s: 0 for s in SIZES}
		cancelled = set()
		dep_map = {}                        # task_id -> [string deps]

		for idx, task in enumerate(tasks):
			base = f"implementation_plan.tasks[{idx}]"
			if not isinstance(task, dict):
				self.error(base, "must be a mapping")
				continue
			tid = task.get("task_id")
			if self._is_nonempty_str(tid):
				loc = f"{base} ({tid})"
				ids.setdefault(tid, []).append(idx)
			else:
				loc = base
				self.error(f"{base}.task_id", "must be a non-empty string")

			self._check_one_task(task, loc, actual_sizes)

			if self._is_nonempty_str(tid):
				raw_deps = task.get("dependencies")
				dep_map[tid] = [d for d in raw_deps if isinstance(d, str)] if isinstance(raw_deps, list) else []
				if task.get("status") == "cancel":
					cancelled.add(tid)

		self._check_duplicate_ids(ids)
		self._check_dependency_graph(dep_map, set(ids.keys()), cancelled)
		self._check_effort_consistency(plan, actual_sizes)

	def _check_one_task(self, task, loc, actual_sizes):
		if not self._is_nonempty_str(task.get("description")):
			self.error(f"{loc}.description", "must be a non-empty string")

		fp = task.get("file_path")
		if not self._is_nonempty_str(fp):
			self.error(f"{loc}.file_path", "must be a non-empty string")
		elif not fp.startswith("/"):
			self.warn(f"{loc}.file_path", "should be an absolute path")

		fn = task.get("function_name")
		if fn is not None and not isinstance(fn, str):
			self.error(f"{loc}.function_name", "must be a string or null")

		if task.get("type") not in TASK_TYPES:
			self.error(f"{loc}.type", f"must be one of {sorted(TASK_TYPES)}")

		if "status" in task and task.get("status") not in TASK_STATUS:
			self.error(f"{loc}.status", f"must be one of {sorted(TASK_STATUS)}")

		size = task.get("size")
		if size not in SIZES:
			self.error(f"{loc}.size", f"must be one of {SIZE_ORDER}")
		else:
			actual_sizes[size] += 1
			if size == "xxl":
				self.warn(f"{loc}.size", "xxl task should be broken down into smaller tasks")

		if not self._is_nonempty_str(task.get("implementation_details")):
			self.error(f"{loc}.implementation_details", "must be a non-empty string")

		self._check_code_references(task, loc)

		api = task.get("api_references")
		if api is not None and not isinstance(api, list):
			self.warn(f"{loc}.api_references", "should be a list of strings")

		if not self._is_nonempty_str(task.get("test_requirements")):
			self.warn(f"{loc}.test_requirements", "should be a non-empty string")

		deps = task.get("dependencies")
		if deps is not None and not isinstance(deps, list):
			self.error(f"{loc}.dependencies", "must be a list of task_ids")

	def _check_code_references(self, task, loc):
		refs = task.get("code_references")
		if refs is None:
			return
		if not isinstance(refs, list):
			self.error(f"{loc}.code_references", "must be a list")
			return
		for i, ref in enumerate(refs):
			rloc = f"{loc}.code_references[{i}]"
			if not isinstance(ref, dict):
				self.error(rloc, "must be a mapping")
				continue
			if not self._is_nonempty_str(ref.get("file")):
				self.error(f"{rloc}.file", "must be a non-empty string")

	# -- semantic / graph checks ---------------------------------------

	def _check_duplicate_ids(self, ids):
		for tid, idxs in ids.items():
			if len(idxs) > 1:
				locs = ", ".join(f"tasks[{i}]" for i in idxs)
				self.error(f"implementation_plan.tasks ({tid})", f"duplicate task_id at {locs}")

	def _check_dependency_graph(self, dep_map, known_ids, cancelled):
		for tid, deps in dep_map.items():
			loc = f"implementation_plan.tasks ({tid}).dependencies"
			for dep in deps:
				if dep == tid:
					self.error(loc, f"task depends on itself ({dep})")
				elif dep not in known_ids:
					self.error(loc, f"depends on unknown task_id '{dep}'")
				elif dep in cancelled:
					self.warn(loc, f"depends on cancelled task '{dep}'")
		self._check_cycles(dep_map, known_ids)

	def _check_cycles(self, dep_map, known_ids):
		"""Kahn topological sort over the known-task subgraph; leftovers are in cycles."""
		in_degree = {tid: 0 for tid in known_ids}
		adj = {tid: [] for tid in known_ids}
		for tid in known_ids:
			for dep in dep_map.get(tid, []):
				# self-loops are reported as self-dependency; skip here to avoid a duplicate report
				if dep in known_ids and dep != tid:
					adj[dep].append(tid)  # dep must finish before tid
					in_degree[tid] += 1
		queue = [tid for tid in known_ids if in_degree[tid] == 0]
		visited = 0
		while queue:
			node = queue.pop()
			visited += 1
			for nxt in adj[node]:
				in_degree[nxt] -= 1
				if in_degree[nxt] == 0:
					queue.append(nxt)
		if visited != len(known_ids):
			in_cycle = sorted(tid for tid in known_ids if in_degree[tid] > 0)
			self.error("implementation_plan.tasks", f"circular dependency among tasks: {in_cycle}")

	def _check_effort_consistency(self, plan, actual_sizes):
		eb = plan.get("effort_breakdown")
		if isinstance(eb, dict):
			for size in SIZE_ORDER:
				declared = eb.get(size, 0)
				if isinstance(declared, bool) or not isinstance(declared, int):
					continue  # already flagged as an ERROR
				if declared != actual_sizes[size]:
					self.warn(
						f"implementation_plan.effort_breakdown.{size}",
						f"declared {declared} but found {actual_sizes[size]} task(s) of size '{size}'")

		declared_total = plan.get("total_effort")
		expected = self._expected_total_effort(actual_sizes)
		if declared_total in SIZES and expected is not None and declared_total != expected:
			self.warn(
				"implementation_plan.total_effort",
				f"declared '{declared_total}' but aggregation rule suggests '{expected}' "
				f"(largest task size + complexity multiplier)")

	@staticmethod
	def _expected_total_effort(actual_sizes):
		"""Apply the SKILL.md aggregation rule: largest task size + task-count multiplier."""
		largest_idx = -1
		for i, size in enumerate(SIZE_ORDER):
			if actual_sizes[size] > 0:
				largest_idx = i
		if largest_idx < 0:
			return None
		task_count = sum(actual_sizes.values())
		if task_count <= 3:
			bump = 0
		elif task_count <= 7:
			bump = 1
		else:
			bump = 2  # 8-12: +2; 13+ should be split, cap the bump here
		return SIZE_ORDER[min(largest_idx + bump, len(SIZE_ORDER) - 1)]


# -- output ------------------------------------------------------------

PHASE_LABELS = {
	"complete-plan": "complete plan",
	"requirement-gathering": "requirement-gathering",
}


def render_human(issues, phase, quiet):
	errors = [i for i in issues if i.level == ERROR]
	warnings = [i for i in issues if i.level == WARNING]
	lines = []
	if not quiet:
		if errors:
			lines.append("❌ ERRORS")
			for i in errors:
				lines.append(f"  {i.level:<7} {i.path}  — {i.message}")
			lines.append("")
		if warnings:
			lines.append("⚠️  WARNINGS")
			for i in warnings:
				lines.append(f"  {i.level:<7} {i.path}  — {i.message}")
			lines.append("")
	lines.append(f"Phase: {PHASE_LABELS.get(phase, phase)}")
	lines.append(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s)")
	return "\n".join(lines)


def render_json(issues, phase):
	import json
	return json.dumps({
		"phase": phase,
		"errors": [i.to_dict() for i in issues if i.level == ERROR],
		"warnings": [i.to_dict() for i in issues if i.level == WARNING],
	}, indent=2)


def main():
	import argparse

	parser = argparse.ArgumentParser(description="Validate a requirements.yaml task plan.")
	parser.add_argument("yaml_file", nargs="?", default="requirements.yaml",
						help="Path to requirements.yaml (default: requirements.yaml)")
	parser.add_argument("--strict", action="store_true",
						help="Treat warnings as failures in the exit code")
	parser.add_argument("--quiet", action="store_true",
						help="Print only the phase + summary lines")
	parser.add_argument("--json", action="store_true", dest="as_json",
						help="Emit machine-readable JSON for agent/CI consumption")
	args = parser.parse_args()

	try:
		with open(args.yaml_file, "r") as f:
			doc = yaml.safe_load(f)
	except FileNotFoundError:
		sys.stderr.write(f"ERROR: file not found: {args.yaml_file}\n")
		sys.exit(2)
	except OSError as e:
		sys.stderr.write(f"ERROR: cannot read {args.yaml_file}: {e}\n")
		sys.exit(2)
	except yaml.YAMLError as e:
		sys.stderr.write(f"ERROR: YAML parse error in {args.yaml_file}: {e}\n")
		sys.exit(2)

	if doc is None:
		sys.stderr.write(f"ERROR: {args.yaml_file} is empty\n")
		sys.exit(2)

	validator = Validator(doc)
	issues = validator.validate()
	phase = "complete-plan" if validator.complete else "requirement-gathering"

	if args.as_json:
		print(render_json(issues, phase))
	else:
		print(render_human(issues, phase, args.quiet))

	has_error = any(i.level == ERROR for i in issues)
	has_warning = any(i.level == WARNING for i in issues)
	if has_error or (args.strict and has_warning):
		sys.exit(1)
	sys.exit(0)


if __name__ == "__main__":
	main()
