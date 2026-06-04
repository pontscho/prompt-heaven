---
name: p:recap
description: Session recap and memory extraction for the AI Soul memory system. Reviews the full conversation and proposes memories for anything worth persisting. Use at the end of a session, when the user says /recap, or when important information was discussed that should be remembered. Trigger: /recap, session summary, emlek mentese, session vege.
---

# Session Recap

Review the entire conversation and extract everything worth remembering into the AI Soul memory system.

## Instructions

### Step 1: Create session anchor memory (ALWAYS, do this first)

Before anything else, propose a single structured **session summary** as an episodic anchor. This is the continuity record that future sessions will use to know what happened here.

Call `memory_propose` with:
- **type**: `event`
- **subject**: `"session YYYY-MM-DD HH:MM"` (use today's actual date and current time)
- **property**: short title of the main topic (e.g. `"memory system improvements"`, `"bug fix: login flow"`)
- **value**: structured summary in this exact format:
  ```
  topic: <1-2 sentences, what was the session about>
  outcomes: <bullet list of concrete outcomes: decisions made, things built, problems solved>
  open: <unresolved threads, planned next steps — or "none" if everything was wrapped up>
  ```
- **source**: `"experienced"`
- **tags**: `["session-summary"]`
- **confidence**: `0.75`
- **reason**: `"Episodic anchor for this session — enables continuity in future sessions"`

This memory is **always proposed**, even if nothing else is worth remembering. It is the minimum output of every recap.

### Step 2: Full conversation review

Read through the ENTIRE conversation from beginning to end. Do not skim. Look for:

- **Facts** about the user (environment, tools, projects, habits, constraints)
- **Preferences** (communication style, workflow choices, tech choices, design decisions)
- **Lessons** (what worked, what didn't, insights reached during problem-solving)
- **Decisions** (architectural choices, design decisions, deliberate trade-offs)
- **Events** (what was built or changed in this session)
- **Relationship context** (how the user relates to topics, what they care about)

### Step 3: Be generous, not conservative

Default behavior is too conservative — too many things are filtered out as "not important enough."

In recap mode: **if in doubt, propose it.** The operator will reject what's not needed. Better to have too many proposals than too few.

Do NOT skip:
- Small preferences revealed in passing
- Implicit assumptions the user made
- Workflow habits observed
- Technical opinions expressed casually

### Step 4: Propose memories

For each item found, call `memory_propose` with:

- **type**: `fact` / `preference` / `lesson` / `relationship` / `event`
- **subject**: Who/what this is about — **choose carefully**:
  - Personal (about the user): `"Zoltán"`
  - Project-level: `"ai-soul project"`, `"memory system"`, `"embedding server"`
  - Technical/architectural: use the component or system name, NOT `"Zoltán"`
  - Rule: if the memory is still true when a different person uses the system, the subject is the system, not the user
- **property**: The specific aspect (e.g. "prefers direct communication", "uses BGE-M3")
- **value**: The actual content — be specific, not generic
- **source**: Usually `"experienced"` (from this session) or `"told"` (user explicitly said it)
- **reason**: Why this is worth persisting
- **confidence**: 0.6–0.9 for most session memories (don't default to 1.0)
- **replaces**: If this updates an existing memory, include the old memory ID

### Step 5: Check for updates

Before proposing, quickly check if a similar memory might already exist using `memory_recall` with relevant keywords. If an update is needed, use the `replaces` field.

### Step 6: Report

After proposing, give a brief summary:
- How many memories were proposed (including the session anchor)
- The key categories (facts / preferences / lessons / decisions / session-summary)
- Any notable ones worth highlighting
- Remind the user to review pending memories: `ai-soul memory list --status pending`

## Memory type guide

| Type | Use for |
|------|---------|
| `fact` | Objective information about the user, their environment, projects |
| `preference` | Choices, styles, opinions — how they like things done |
| `lesson` | Insights from problem-solving, what works/doesn't work |
| `relationship` | How user relates to a topic, technology, concept |
| `event` | What happened in this session (built X, decided Y) |

## Examples

**Preference discovered in passing:**
> User said "inkább ne legyen emoji" while reviewing output

→ Propose: type=preference, subject="Zoltán", property="output style", value="No emojis in responses — user explicitly dislikes them"

**Lesson from problem-solving:**
> A fix worked only after reverting an earlier change

→ Propose: type=lesson, subject="debugging approach", property="revert-first strategy", value="When fixes don't work, try reverting to baseline first before adding more changes"

**Architectural decision:**
> User chose skill-based approach over Python automation for recap

→ Propose: type=event, subject="ai-soul memory system", property="recap implementation", value="Chose /recap skill as first step, Python automation planned for later"

**Technical fact about a component (NOT personal):**
> The embedding server uses BGE-M3 with 1024-dimensional dense vectors

→ Propose: type=fact, subject="embedding server", property="model", value="BGE-M3, 1024-dim dense + sparse lexical embeddings"
→ NOT: subject="Zoltán", property="uses BGE-M3" — this is system fact, not user preference

**Bug fix implemented in this session:**
> Fixed a filter bug in kg-search.ts where graph nodes were missing a status field

→ Propose: type=event, subject="kg-search.ts", property="bug fix", value="traverse filter checked node.status which doesn't exist on graph nodes — filter always returned false, related memories never populated"
→ NOT: subject="Zoltán", property="fixed bug"

## Notes

- This skill runs in the current session — it can see the full conversation
- Propose memories freely; quality control happens at approval step
- Session memories are less confident than explicit user statements — reflect this in confidence score
- If the session was purely technical with no new learnings, it's fine to propose 0 memories and say so
