# Skill: ra1-verify
Purpose: enforce the repo's deterministic checks before task completion (AC/DC Verify phase; vendor-agnostic)

When to invoke: after every code modification, before ending the turn.

Required steps:
1. Run `<VERIFY_COMMAND>` covering the changed code.
2. Mandatory fixes: never mark work complete with failing lint/type/test checks.
3. Re-run after fixes to confirm resolution and no regressions.

Evidence of completion: `<VERIFY_COMMAND>` exits 0.
