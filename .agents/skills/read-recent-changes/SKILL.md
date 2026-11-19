---
name: read-recent-changes
description: >-
  Use this skill BEFORE making file modifications or starting a task.
  It reads recent activity logs from .change_log.md or falls back to git log/status to get context on recent changes.
---

# Read Recent Changes Skill

This skill ensures that before modifying project files or beginning a technical task, the agent checks and understands what was recently changed in the codebase.

## When to Use

- **BEFORE** starting any editing task, bug fix, feature implementation, or file modification.
- Whenever entering a task flow where historical context of recent edits is needed.

## Execution Steps

1. **Check Change Log File (`.change_log.md`)**:
   - Check if `.change_log.md` exists at the project root (`d:/OneDrive/tools/dc_auto_bot/.change_log.md`) using `view_file`.
   - If it exists, read the last 5 to 10 entries to understand recent modifications and work context.

2. **Fallback Mechanism (If `.change_log.md` is missing or empty)**:
   - Run `git status` via `run_command` to check uncommitted changes.
   - Run `git log -n 5 --oneline` via `run_command` to inspect recent commit history.

3. **Synthesize Context**:
   - Identify recent bug fixes, refactorings, or added features.
   - Ensure proposed modifications do not conflict with or undo recent changes.
   - Proceed with the task with full contextual awareness.
