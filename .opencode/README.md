# OpenCode Workspace

This directory contains the OpenCode agent assets for the GitLab merge request code review gate.

- `skills/` contains task instructions and reusable procedures.
- `agents/` contains agent role definitions.
- `commands/` contains user-facing slash command entrypoints.

Business repositories are mounted or checked out outside `.opencode`; review outputs should be written to `review-output/` and archived by GitLab CI.
