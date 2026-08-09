# Recording answers

All recording goes through `ra1 answer`. There is **no direct read/write/merge** of policy
files, no JSON parsing, no Make/package probing, and no report rerun from this skill: the
engine owns the policy files, the rescan, and the verification contract.

## The two policy files (explanatory only)

The engine merges exactly these two reviewable inputs:

- `.ra1/config.json` — team-owned inputs the engine acts on: `detect.project_type`,
  `detect.surfaces`, `detect.apps.<dir>`, `loop_ready`, `ci_budget_minutes`,
  `acdc.verify_command`. A recorded value makes the criterion evaluable; the engine judges
  it and may still fail it.
- `.ra1/waivers.json` — exact engine-authored waiver ids for the `capability.github` gap's
  `github.non_github_host` choice. A waived criterion is **excluded** from the gate and
  disclosed as waived — never counted as passing. There is no free-form reason, owner, or
  expiry.

Generated output lives only under ignored `.ra1/reports/`; policy files stay reviewable and
unignored.

## The exact commands

For one current gap, plan exactly one of:

```bash
python3 -I "<skill-dir>/scripts/readiness/cli.py" answer --project . \
  --gap-id <canonical-gap-id> --choice <canonical-choice-id> --format json
python3 -I "<skill-dir>/scripts/readiness/cli.py" answer --project . \
  --gap-id config.ci_budget_minutes --minutes <1..1440> --format json
```

- `--gap-id` and `--choice` (or `--minutes`) are copied verbatim from the just-returned
  canonical `gaps` payload.
- Repeated `--choice` is allowed **only** for the current multi-enum surfaces gap.
- Only one gap and one value per invocation.
- Plan mode (`no --apply`) writes nothing; it emits the plan `answer_contract` with
  `verification.status: "not_run"`.
- Only when the developer's **current turn** explicitly selects recording do you repeat the
  exact plan command with fixed `--apply`.

## The answer_contract

The apply result is one canonical contract, presented verbatim:

```json
{
  "operation": "apply",
  "gap_id": "config.loop_ready",
  "target_kind": "config",
  "target": ".ra1/config.json",
  "apply_result": {"written": true, "created": true},
  "verification": {
    "status": "passed",
    "errors": [],
    "gap_resolved": true,
    "status_changes": [{"id": "criterion.id", "from": "unknown", "to": "fail"}],
    "waived_ids": [],
    "remaining_gap_ids": [],
    "score": {"from": {}, "to": {}},
    "decision_successful": true
  }
}
```

Present the recorded write, the `status_changes` (an honest answer may turn `unknown` into
`fail` or lower the Level — that is the interview working), `waived_ids`, remaining gaps, and
the before/after score copied from the contract. Apply success means the typed answer was
durably recorded and the rescan confirmed it; it **never means** the score or Level improved.

## Never

- Never pass free-form text, paths, JSON, or general waivers as answer options.
- Never add a waiver reason, owner, expiry, or provenance suffix.
- Never recommend `gh auth login`; the `github.restore_access` choice is an external action
  you do not execute.
- Never treat a failed `answer_contract` as recorded, and never report a retained policy
  edit as success without the contract's own verification.
- Never run a full `ra1 report` as part of this skill's flow; that is a separate explicit
  `ra1-report` invocation.