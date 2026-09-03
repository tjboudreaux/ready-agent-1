# AGENTS.md

Briefing for AI agents working in this repo. Keep it high-signal (≤150 lines).

## Build & Test
<!-- Install deps, build, and run tests — the exact commands. -->

## Architecture
<!-- Key components/services and how they fit together. -->

## Conventions & Patterns
<!-- Naming, error handling, where things go, gotchas. -->

## Security
<!-- Secrets handling, what must never be touched or logged. -->

## Git Workflow
<!-- Branch naming, PR expectations, review requirements. -->

## Concurrent Work
- Use a separate worktree or task branch for each concurrent agent task, and own your files.
- Coordinate with other agents before touching the same or overlapping files.
- Re-read files before editing and preserve unexpected user or concurrent changes; never overwrite them silently.
- After merging or integrating, run the full test suite to verify the combined state.
