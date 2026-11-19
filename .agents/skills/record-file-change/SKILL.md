---
name: record-file-change
description: >-
  Use this skill immediately after modifying, creating, or deleting files in the project.
  It logs a brief summary of the changes with timestamp into the project change log file (.change_log.md).
---

# Record File Change Skill

This skill provides guidelines and instructions for recording file modifications into `.change_log.md` immediately after completing any file edit, creation, or deletion.

## When to Use

- **Immediately after** calling file modification tools (e.g. `write_to_file`, `replace_file_content`, `multi_replace_file_content`) or deleting files.
- Record concise, meaningful summaries so future task sessions or collaborators understand what was changed.

## Execution Steps

1. **Check Target Log File**:
   - Target log file: `.change_log.md` at the project root (`d:/OneDrive/tools/dc_auto_bot/.change_log.md`).
   - If `.change_log.md` does not exist, create it using `write_to_file` with the initial header:
     ```markdown
     # Project Change Log
     ```

2. **Format Entry**:
   Append a new entry using `replace_file_content` or `write_to_file` in the following format:

   ```markdown
   ## [YYYY-MM-DD HH:mm:ss] - `<file_path>`
   - **Action**: Created / Modified / Deleted
   - **Summary**: Concise description of what was changed and why.
   - **Key Changes**:
     - Detail 1
     - Detail 2
   ```

3. **Rules & Best Practices**:
   - Keep entries concise and factual.
   - Focus on key logical changes and intent rather than dumping entire diffs.
