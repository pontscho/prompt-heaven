---
name: skills
type: subsystem
status: active
title: Skills
description: Loadable knowledge packs activated on-demand or via Skill permissions.
sources:
  - ClaudeCode/skills
verified:
  commit: 4061b15
  date: 2026-10-02
links:
  - overview
  - agents
  - tests
  - requirements-yaml
  - 0005-approve-the-wrapper-not-the-command
  - 0009-the-first-reader-is-a-cold-model
  - 0020-the-prompt-is-its-own-block
  - 0022-a-someday-maybe-is-a-roadmap-item
---

# Skills

Skills are loadable knowledge packs under `ClaudeCode/skills/`. Each skill is a
bare-named directory `<name>/` containing at minimum a `SKILL.md`; some bundle
helper scripts and reference files. The `p:` in `/p:<name>` is not part of the
directory name; the plugin prepends it at invocation time
`ClaudeCode/ARCHITECTURE.md`. A skill defines *rules and patterns* (how to
use a tool, a language convention, a workflow); the former `/p:` slash-commands
(`p:analyze`, `p:feature-plan`, `p:task-plan`, `p:implement`, `p:deep-research`,
`p:project-explore`, `p:checkpoint`, `p:spec-design`) were migrated into skills,
so skills now also carry the explicitly-invoked multi-step workflows — see
[[overview]].

## Structure

- **Minimal skill**: a single `SKILL.md` — e.g. `ClaudeCode/skills/mcp-clangd/SKILL.md`.
- **Extended skill**: `SKILL.md` + scripts + reference — e.g.
  `ClaudeCode/skills/static-linking/` ships `SKILL.md`, `README.md`,
  `build-static.py`, `verify-static-linking.py`, `example-CMakeLists.txt`.

Frontmatter is required: `name` and a `description` carrying both *what it does*
and *when to trigger*. The `name` must match the bare directory name exactly, and
the `p:` prefix is never written into it `ClaudeCode/ARCHITECTURE.md`. The
description is what Claude matches against to auto-activate the skill.

## Notable skills

| Skill | Purpose |
|-------|---------|
| `p:mcp-clangd` / `p:mcp-luals` / `p:mcp-cuda` | LSP code-intelligence routing for C/C++, Lua, CUDA |
| `p:mcp-purity` | File ops (search/glob/edit) routing |
| `p:mcp-forge` | `project-forge.yaml` build orchestration |
| `p:writer-skill` / `p:writer-agent` | Authoring new skills and agents |
| `p:wiki` | This documentation-wiki engine ([[wiki-engine]]) |
| `p:recap` | Session recap into the AI Soul memory system |
| `p:feature-plan` / `p:task-plan` / `p:implement` | Migrated `/p:` workflow chain: plan -> `requirements.yaml` -> execute |
| `p:code-review` / `p:branch-review` | Multi-lens code review (finder/verifier minion fan-out). Its plan lived in the fixed plan slot and was overwritten by the next feature; the last version survives only in git, as the plan file at 1446acb~1. After the report, each finding the user will not fix now is offered as one `ROADMAP CANDIDATE:` block with a `user:` origin; the skills only propose and never write the roadmap `ClaudeCode/skills/_lib/code-review-lenses.md` |
| `p:sandbox-run` | Sandboxed CLI runner: the bundled `sbx` helper contains a command under macOS Seatbelt / Linux bwrap — default-deny writes, no network, secret read+write denial, fail-closed on any other platform; on Linux also a fresh read-only `/dev`, private PID and IPC namespaces and a new session with no controlling terminal, plus an opt-in `--seccomp` x86_64 syscall allowlist `ClaudeCode/skills/sandbox-run/scripts/sbx` ([[0005-approve-the-wrapper-not-the-command]]) — paired with the grant-only `sbx-gate.py` PreToolUse(Bash) gate that auto-allows a clean, in-project, network-free invocation |
| `p:checkpoint` | Session handoff into `.claude/tmp/checkpoint.md`: an append-only stack of session blocks under a frozen mission tail, written whole in English whatever language the conversation is in, with a script-generated line-range table of contents that makes one block readable by offset — [[0009-the-first-reader-is-a-cold-model]]. Each session's activation prompt is its own `## ACTIVATION S<NNN>` block directly below that session, with its own `A<NNN>` TOC row `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:toc_rows`, so a resume is one command: `nexts` prints MISSION + the newest SESSION + its ACTIVATION block `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_nexts`, and `activate` prints the prompt paste-ready with the `>` markers stripped, which is where the skill's chat reply takes it from instead of retyping it `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_activate`. `prepend` refuses a segment that does not pair every session with its own prompt block `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:check_segment_shape`; older files that carry the prompt as a `### ACTIVATION` subsection stay readable through a read-only fallback `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:legacy_activation`. When the project has a `docs/roadmap/roadmap.md`, Step 7 offers the new block's deferred `THREADS` (not kept limits, not next-session work) as `ROADMAP CANDIDATE:` blocks in its chat reply, always with a `user:` origin, and never runs `roadmap.py` itself `ClaudeCode/skills/checkpoint/SKILL.md` |
| `p:roadmap` | Known-but-unscheduled work as wiki pages: docs/roadmap/roadmap.md (type `roadmap`) holds the live items in horizon lanes under a WIP cap on `now`, and each closed item is archived as its own immutable `roadmap-item` page, counted in `INDEX.md` rather than listed. Both have ONE writer, the bundled stdlib `roadmap.py`, which renders the whole file and guards it with an optimistic lock; untrusted text reaches it only through staged files, never a shell line. `adopt` has `p:minion-explorer` harvest candidates, the user approve them and the script write them `ClaudeCode/skills/roadmap/SKILL.md`. `adopt` is not the only route in: any other producer that meets deferred work (`p:minion-bug-hunter`'s diagnosis-only verdict, `p:checkpoint`'s Step 7, and `p:code-review` / `p:branch-review` after their report; the fragment itself is the list) emits a `ROADMAP CANDIDATE:` block per the shared fragment and never runs `roadmap.py` itself; the main context shows it and, only on approval, stages and adds it as adopt does `ClaudeCode/skills/_lib/roadmap-proposal.md`. A second, read-only script, `board.py`, renders one `roadmap-export/2` export as a kanban board (columns by horizon, closed items by state, reverse `blocks` edges computed from `blocked_by`): unpadded markdown tables escaped with the ADR 0016 vocabulary `ClaudeCode/skills/roadmap/scripts/board.py:render_table`, or one static HTML page that loads a pinned Vue build from unpkg.com, so viewing it needs network — [[0022-a-someday-maybe-is-a-roadmap-item]] |
| `p:jira` | Jira from the CLI via the bundled `jira.py`: Cloud and Server/DC behind one client with the deployment probed rather than guessed, several distinct pagers, `JIRA_READ_ONLY` and `--dry-run` as refusals, and a `.claude/jira.json` profile found by a walk that stops at `$HOME` with both sides resolved through `realpath` — driven fully offline with the transport injected ([[tests]]) |
| `p:bitbucket` | Bitbucket Server/DC from the CLI via the bundled `bitbucket.py`: refusals at the door (an `http://` base URL that would hand the bearer PAT to the network in cleartext, and a userinfo base URL), same-origin-only redirects, and `BITBUCKET_READ_ONLY` at both layers. Its profile lookup is byte-identical to `p:jira`'s on purpose, gated on identity so the eventual unification stays mechanical ([[tests]]) |
| `p:requirements` | Reads and batch-updates task status in `requirements.yaml` through the `task-*.py` helpers. The helpers do **not** agree on how they locate that file — see [[requirements-yaml]] for the divergence and for why the file is a single overwritable slot |

The MCP-routing skills (`p:mcp-*`) all forbid built-in tool fallback, mirroring
the mandate documented in [[overview]].
