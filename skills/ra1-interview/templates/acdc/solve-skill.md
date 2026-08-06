# Skill: ra1-solve
Purpose: resolve findings after a failed verification (AC/DC Solve phase; vendor-agnostic)

When to invoke: `<VERIFY_COMMAND>` fails, or a readiness report lists failing criteria.

Required steps:
1. Fix each finding at the source, guided by the reporting tool's rationale.
2. For readiness gaps: `ra1 report --project .`, then `ra1 fix --project . --latest --apply` (local branch; review before pushing).
3. Re-run `<VERIFY_COMMAND>` to confirm resolution.

Evidence of completion: the verify command exits 0 and no blocking findings remain.
