---
name: p:implementation-plan
description: Extract implementation plan from requirements.yaml in compact token-efficient YAML format. Use when you need to read implementation_plan for executing tasks without loading the entire requirements.yaml file.
allowed-tools: Bash
---

# Implementation Plan Extractor Skill

## Trigger Conditions

Use this skill when:
- You need to read the implementation_plan from requirements.yaml
- You want to execute tasks from the plan (e.g., `/p:implement` command)
- You need token-efficient access to task definitions
- You want to avoid loading the full requirements.yaml with all original_request, requirements questions, etc.

## Skill Purpose

Extracts only the essential implementation data from requirements.yaml:
- `complete` flag
- `success_criteria`
- `p:implementation_plan` (affected_files, new_files, reference_files, api_references, tasks)

Output is compact YAML format with no extra text or formatting.

## Instructions

When this skill is activated:

1. **Run the extraction script**
   ```bash
   python3 ~/.claude/skills/p:implementation-plan/get_implementation_plan.py [path_to_yaml]
   ```
   - If path is omitted, searches for requirements.yaml in current/parent directories
   - Script outputs only YAML, no extra text

2. **Display the output directly**
   - Show the script output as-is
   - Do NOT add explanations, summaries, or formatting
   - Do NOT process or interpret the YAML
   - Just raw YAML output

## Example Usage

**User:** "Show me the implementation plan"
**Response:** Run the script and display raw YAML output only.

**User:** "What tasks are in the plan?"
**Response:** Run the script and display raw YAML output only. User can read task details from the YAML.

## Important Notes

- Output is ONLY the YAML, nothing else
- No markdown code blocks, no explanations, no summaries
- Token-efficient: excludes original_request, requirements, constraints from output
- Use this instead of reading full requirements.yaml when you only need implementation details
