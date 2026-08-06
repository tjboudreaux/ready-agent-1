# Recording answers

Where each answer goes, and the rules that keep a recorded answer from becoming a fake pass.

## `.agents/readiness/config.json` — inputs the engine acts on

One object, read by the engine on every run. Create it if absent; **merge** if present.

```json
{
  "detect": {
    "project_type": "service",
    "apps": {
      "apps/web": "frontend",
      "apps/worker": "service"
    }
  },
  "loop_ready": false,
  "ci_budget_minutes": 15,
  "acdc": {
    "verify_command": "make check"
  }
}
```

| Gap `answer.path` | Accepted values |
|---|---|
| `detect.project_type` | `library`, `service`, `frontend`, `cli`, `data`, `infra` |
| `detect.apps.<dir>` | same enum, per application directory |
| `loop_ready` | `true` / `false` (boolean, not a string) |
| `ci_budget_minutes` | integer minutes |
| `acdc.verify_command` | string; must resolve to a real target or script |

Rules:

- **Read the file before writing it.** Preserve every key you did not come to change,
  including `judgments` and any `acdc` keys already present.
- **Types matter.** `"loop_ready": "true"` is a string and the engine treats it as not opted
  in. Booleans unquoted, integers unquoted.
- **An invalid enum value is worse than no value.** The engine ignores an unrecognized
  `project_type` and records that it ignored it, so a typo silently changes nothing.
- **A monorepo ignores a root pin.** For `is_monorepo` detection, pin `detect.apps.<dir>`;
  a root `project_type` is recorded as ignored.
- **Verify the verify command.** Before writing `acdc.verify_command`, confirm the target
  exists (`make -n <target>`, or the script key in `package.json`). Writing a command that does
  not resolve turns a pass into a fail on the next run.

## `.agents/readiness/waivers.json` — disclosed exclusions

Only for gaps where `waivable` is true.

**The file is a JSON array of objects, not an object keyed by id.** The engine iterates it and
reads `id` off each entry, so a `{"criterion.id": {...}}` shape raises an error and takes the
whole scan down with it.

```json
[
  {
    "id": "security.branch_protection",
    "reason": "Enforced in GitLab; this mirror is read-only. — asked by ra1-interview, answered by @tjboudreaux, 2026-08-06",
    "expires": "2027-02-01"
  }
]
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Criterion id, verbatim from the gap's `blocks` list. An entry without it is skipped. |
| `reason` | yes in practice | Shown in the report as `Waived: <reason>`. Absent, the row reads `Waived:` and tells a later reader nothing. |
| `expires` | no | ISO date. Recorded for the next human; the engine only enforces it when a caller supplies the current time, which the CLI does not do today. Treat it as a documented review date, not an automatic re-activation. |

Rules:

- **One array entry per criterion**, its `id` taken verbatim from the gap's `blocks` list.
- **The reason is the developer's, in their words.** Add the provenance suffix
  (`— asked by ra1-interview, answered by <who>, <ISO date>`) so a later reader knows the
  exclusion was deliberate, who authorized it, and when it should be revisited.
- **One waiver per criterion, never a wildcard.** If eight criteria are excluded, the developer
  agreed to eight, and each reason should be true of that criterion.
- **A waived criterion leaves the gate denominator.** It is disclosed in the report as waived,
  never counted as passing. Say this out loud before writing the file; a developer who thinks a
  waiver is a pass has been misled.
- **Never waive a `fail`.** The scanner looked and found nothing. That is a finding for
  **ra1-fix**, not a fact about another system.

## After writing

Re-run the report (SKILL.md step 5). Then confirm, from the engine's own output, that:

- the criteria you expected to become evaluable are no longer `unknown`;
- every criterion you waived reports `waived` with your reason attached;
- the gap you answered is gone from `ra1 gaps`.

A gap that persists after an answer means the value did not land: wrong path, wrong type, or a
JSON parse error that the engine absorbed silently by treating the config as empty. Check the
file parses (`python3 -m json.tool`) before assuming the engine ignored a valid answer.
