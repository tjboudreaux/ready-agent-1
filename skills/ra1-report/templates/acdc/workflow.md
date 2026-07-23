# AC/DC — Agent Centric Development Cycle (vendor-agnostic)
# Agent workflow directive (MUST FOLLOW). Merge into AGENTS.md or your harness's instruction file.

## GUIDE phase — before generating code
1. Read the repo's agent briefing (AGENTS.md) and architecture docs (<GUIDE_DOCS>).
2. Locate existing implementations and conventions before writing new code; never introduce a second convention beside an existing one.
3. Before adding or updating a third-party dependency, check it against the repo's dependency policy (pinned lockfile, update automation).

## VERIFY phase — after generating code
No code is complete until it passes the repo's deterministic checks. After every modification, and before ending your turn, you MUST:
1. Run the repo's verify command: `<VERIFY_COMMAND>`
2. Fix everything it reports. You are prohibited from completing a task while checks fail.
3. Re-run `<VERIFY_COMMAND>` after fixes to confirm resolution and no regressions.

## SOLVE phase — when verification fails
1. Fix each finding at the source; never suppress, skip, or special-case to silence a check.
2. For repo-readiness gaps, run `ra1 report --project .` and remediate with `ra1 fix --project . --latest` (safe scaffolds, local branch, never pushed).
3. Re-run the VERIFY phase until clean.
