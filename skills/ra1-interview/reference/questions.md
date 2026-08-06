# Question craft

How to turn a gap into a question a busy developer answers correctly on the first try.

## The shape of every question

1. **The question**, in one sentence, using the gap's `question` as the spine.
2. **Your recommendation**, with the evidence that produced it: *"I'd say `cli` — `pyproject.toml`
   declares a `ra1` console script and there's no server dependency."*
3. **The stake**, from `blocked_gating` and `levels`: *"This unblocks 3 gating criteria at L3."*
   When `blocked_gating` is 0, say so: *"Nothing gating depends on this; it affects 9 advisory
   criteria."* A developer deserves to know a question is low-stakes.
4. **The accepted answers**, verbatim from `options`, when the gap is closed-ended.

Keep it four lines. A paragraph invites a paragraph back, and prose is harder to record than a
value.

## Per kind

### `detection`

The scan classified the repository with too little evidence, or found competing evidence.

- Lead with what you found in the repo, not with the taxonomy. *"There's a `Dockerfile` and a
  FastAPI dependency, so I'd call this a service"* beats *"is this a library, service, frontend,
  CLI, data pipeline, or infrastructure?"*
- For `detect.project_type.contested`, the answer is a **set**, not a choice: ask which of the
  detected surfaces the directory actually serves and record every one
  (`detect.surfaces: ["service", "frontend"]`). Never make the developer pick one of two true
  answers — that is what silently skipped the loser's criteria in the first place. Say plainly
  that declaring both raises the criteria count and may lower the level. If each surface really
  lives in its own directory, it is a monorepo instead: pin `detect.apps.<dir>` per directory.
- For a monorepo app gap, name the directory every time. `apps/worker` and `apps/web` get
  different answers and the developer is holding both in their head.

### `config`

A value only the team can decide.

- Propose the value you would defend, from what the repo already does. For a verify command,
  read `Makefile`, `package.json` scripts, `AGENTS.md`, and CI, then propose the one that
  actually runs lint and tests together. If none exists, say that plainly: the honest answer may
  be "we don't have one yet", which is a finding, not a config value.
- For a budget-style number, offer a defensible starting number and name the consequence of
  each direction. Never invent a number without saying it is a starting point.
- For `loop_ready`, ask about reality, not aspiration: does an autonomous loop run today? A
  `true` here adds nine criteria that will mostly fail on a repo that does not run one, which is
  correct but should not be a surprise.

### `capability`

A data source the scan could not reach.

- Ask whether access can be restored before offering a waiver. `gh auth login` is a better
  outcome than nine disclosed exclusions, and it costs the developer one command.
- If the project is not hosted on GitHub at all, that is the waiver case, and the reason should
  say where the practice actually lives (*"branch protection is enforced in GitLab"*).
- Never waive the whole capability in one stroke without naming the criteria it excludes. Read
  `blocks` aloud, in full, before asking for the decision.

## Handling the answer you get

| Answer | Do |
|---|---|
| A clean value in `options` | Record it (step 4). Move on. |
| A value outside `options` | Do not coerce it. Restate the accepted values, or map their words to one option and confirm the mapping explicitly. |
| "I don't know" | Offer your recommendation as the default and say what happens if it's wrong (a wrong pin skips or fails the wrong criteria; both are visible in the next report and reversible by editing one line). |
| "It's complicated" / a story | Extract the structural fact. Usually the answer is "two apps, not one", or "that practice lives in another system". Restate it as one sentence and confirm before recording. |
| A claim that contradicts the repo | Surface the contradiction with the file: *"you said tests run in CI, but `.github/workflows` has no test job — is CI elsewhere?"* Resolve before recording. |
| A request to just make the score better | Decline, once, plainly: pins record what the repository is, and waivers are disclosed exclusions rather than passes. Then re-ask the honest question. |

## What never becomes a question

- Anything the codebase answers (step 2 caught it).
- Anything the scanner already judged. A `fail` with a rationale is a finding; asking "do you
  actually have a linter?" invites a developer to argue with evidence.
- Agent judgments (`judgment.*`). Those are qualitative and belong to **ra1-report**'s T4
  section.
- Whether the developer would *like* a criterion to pass.
