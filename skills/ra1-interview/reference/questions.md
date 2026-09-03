# Question craft

How to turn a gap into a question a busy developer answers correctly on the first try.
Every question is formed **only** from the just-emitted gap payload: `question`, `why`,
bounded `evidence`, blocker/Level counts, and the display-only `choices` labels. You never
inspect repository files to form a question, never infer a new answer, never execute an
`external_action`, never recommend `gh auth login`, and never turn user prose into a
value.

## The shape of every question

1. **The question**, in one sentence, using the gap's `question` as the spine.
2. **The stake**, from `blocked_gating` and `levels`: *"This unblocks 3 gating criteria at
   L3."* When `blocked_gating` is 0, say so: *"Nothing gating depends on this; it affects
   N advisory criteria."*
3. **The recordable choices**, using only the `choices` labels and their `effect`
   (`record` / `external_action` / `leave_unanswered`), presented as an explicit
   **record-or-leave-unanswered** decision. The `leave_unanswered` choice is always
   available; choosing it records nothing.
4. **An honest note** that recording is not credit: the engine re-scores from the recorded
   value, and the answer can expose failures or lower the Level.

Keep it four lines. A paragraph invites a paragraph back, and prose is harder to record
than a value.

## Per kind

### `detection`

The scan classified the repository with too little evidence, or found competing evidence.
Only the emitted choices may be recorded — never a free-form answer.

- For `detect.project_type.contested`, the `choice` may be repeated (multi-enum): ask which
  of the detected surfaces the project actually serves and record every one. Say plainly
  that declaring more surfaces raises the criteria count and may lower the level.
- For a per-app gap (`detect.app_type.<hash>`), the question names the app directory — the
  canonical gap id is opaque by design, so never put a path in argv.

### `config`

A value only the team can decide. Record only the emitted canonical choices (or the CI
budget integer). If a verify-command gap is emitted as `unrecordable`, say plainly that no
safe command candidate was found and that the developer must edit `.ra1/config.json`
manually — you never construct or edit a command yourself.

- For `loop_ready`, ask about reality, not aspiration: does an autonomous loop run today?
  A `true` here adds nine criteria that will mostly fail on a repo that does not run one,
  which is correct but should not be a surprise.
- For a budget-style number, name the consequence of each direction; the engine's integer
  range (1..1440 minutes) is the only accepted value.

### `capability`

A data source the scan could not reach. The only recording choice in this catalogue is
`github.non_github_host` (a disclosed exclusion of the gap's specific blocked criteria);
`github.restore_access` is an **external action** you never execute — you only ask whether
the developer will restore access, and re-scanning afterwards is a separate explicit
request. Never recommend `gh auth login`; say instead that restoring API access would let
the engine verify the controls.

- Never waive the whole capability in one stroke: read the gap's `blocked_ids` aloud, in
  full, before asking for the decision, and say that only those exact criteria are
  excluded and never counted as passing.

## Handling the answer you get

| Answer | Do |
|---|---|
| A canonical `choices` id | Plan `ra1 answer --gap-id <id> --choice <id>`; on explicit current-turn confirmation, repeat with `--apply`. |
| A value outside `choices` | Do not coerce it. Restate the accepted choices and ask again. |
| "I don't know" | Offer `leave_unanswered` as the honest default and say what happens (the gap stays open, the criteria stay `unknown`). |
| "It's complicated" / a story | Ask one more time with the exact choices; do not turn prose into a value. |
| A request to just make the score better | Decline, once, plainly: recording supplies an input the engine re-evaluates; it never improves the Level by itself. |

## What never becomes a question

- Anything the scanner already judged. A `fail` with a rationale is a finding; asking "do
  you actually have a linter?" invites a developer to argue with evidence.
- Anything outside the emitted gap payload: you may not inspect repository files, invent a
  new gap, or recommend a configuration the engine did not emit.
- Agent judgments (`judgment.*`). Those are qualitative and belong to **ra1-report**'s T4
  section.