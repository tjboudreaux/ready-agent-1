---
name: ra1-interview
description: Resolve the unanswered questions in a Ready Agent 1 readiness report by interviewing the developer, then record the answers as engine inputs and re-score. Use when a readiness report lists unanswered questions or gaps, when `ra1 gaps` returns any gap, when a criterion is `unknown` because the scan could not classify the project or reach a data source, or when the user asks to answer, pin, or configure what the readiness scan could not infer.
license: MIT
compatibility: Python 3.11+; optional authenticated gh CLI for GitHub (T2) checks
metadata:
  version: 0.11.0
allowed-tools: Bash
---

# Agent Readiness Interview

A readiness scan reports `unknown` when it lacks an input, not when a repository is bad. This
skill closes that distance: it asks the developer the questions the engine cannot answer,
records each answer where the **engine** reads it, and re-scores so the change in the number
comes from re-evaluation rather than from anyone's opinion.

**The engine owns every verdict.** You collect inputs. An answer can make a criterion
evaluable or exclude it with a disclosed waiver; an answer can never mark one passing.

## Steps

`<skill-dir>` in the commands below is this skill's own directory: the absolute path your
runtime reports when it loads the skill. Substitute it. Do not write `$(dirname "$0")` — in a
shell tool call `$0` is the shell, not this file, so that form resolves against the caller's
working directory.

### 1. List the gaps

```bash
python3 -I "<skill-dir>/scripts/readiness/cli.py" gaps \
  --project . --format json
```

Each gap carries exactly `gap_id`, `kind`, `question`, `why`, `recordable`, `input_kind`
(`single_choice` | `multi_choice` | `integer` | `unrecordable`), `choices` (opaque ids with
display-only labels and effects `record` | `external_action` | `leave_unanswered`), `value`
(null, or the CI-budget integer spec), `blocked_ids`, `blocked_gating`, `levels`, and
`evidence`. The list is already ordered by leverage: the first gap blocks the gate the
reader is trying to clear. IDs and choices are engine constants; copy them into argv
verbatim — never question/evidence/user prose.

**Done when:** you have the gap list. Empty list → tell the user every input was inferable and
stop; there is nothing to interview about.

### 2. Frame the question from the gap payload only

For every gap, form the question strictly from `question`, `why`, bounded `evidence`, the
blocker/Level counts, and the display-only `choices` labels — see
[reference/questions.md](reference/questions.md). You do not inspect repository files to
derive a recommendation, and you never construct a value the engine did not emit.

**Done when:** each gap has one four-line question with its stake and its recordable choices,
or an explicit `leave_unanswered` decision.

### 3. Interview, one question at a time

Ask **one** question, wait for the answer, then ask the next. Order follows the gap list, with
one exception: ask a question whose answer changes later questions first (project type before
anything scoped to an application).

Each question states, in this order: the question, what answering it unblocks
(`blocked_gating` and `levels`), and the accepted `choices` labels verbatim, framed as an
explicit record-or-leave-unanswered decision.

Read [reference/questions.md](reference/questions.md) for the wording rules per gap kind and
for what to do with a vague, "I don't know", or contradictory answer.

**Done when:** every gap has an answer, an explicit "leave it unanswered", or a waiver
decision — and you asked no question the codebase had already answered.

### 4. Plan exactly one answer; apply only on explicit current-turn confirmation

For one current gap, plan exactly one of:

```bash
python3 -I "<skill-dir>/scripts/readiness/cli.py" answer --project . \
  --gap-id <canonical-gap-id> --choice <canonical-choice-id> --format json
python3 -I "<skill-dir>/scripts/readiness/cli.py" answer --project . \
  --gap-id config.ci_budget_minutes --minutes <1..1440> --format json
```

Repeated `--choice` is allowed only for the current multi-enum surfaces gap. IDs/choices must
be copied from the just-returned canonical payload. Only when the developer's current turn
explicitly selects recording do you repeat the exact plan command with fixed `--apply`.

The engine reacquires the live root, requires clean/known Git and a complete T0/T1 baseline,
rederives the gap and its internal `.ra1` policy mapping, performs one bounded create/merge
on `.ra1/config.json` or `.ra1/waivers.json`, rescans with the same offline scope, and emits
the canonical `answer_contract`. You never read, write, or merge policy files directly, never
pass a free-form waiver reason, owner, or expiry, and never append provenance suffixes.

**Done when:** you present the `answer_contract` verbatim, state plainly that recording is
not credit (the engine re-scores; the answer can expose failures or lower the Level), and
never call an unchanged/unresolved criterion fixed.

### 5. Report the verified outcome honestly

Present the `answer_contract`'s own verification: the recorded write, `gap_resolved`, the
`status_changes` (including `unknown → fail` — that is the interview working, not failing),
`waived_ids`, remaining gaps, and the before/after score **copied verbatim** from the
contract. Generating or storing a subsequent full report remains a separate explicit
`ra1 report` invocation, not an automatic post-answer step.

**Done when:** you have stated the before/after score from the contract, listed the status
changes, and named the remaining gaps.

## Contract (do not violate)

- **Never write a status, a score, or a pass.** The engine writes verdicts from the
  recorded input.
- **Never write, read, or merge policy files directly.** Only `ra1 answer` mutates
  `.ra1/config.json` or `.ra1/waivers.json`, and only with exact engine-authored ids.
- **Never waive what the scanner looked for and did not find.** Waivers exist only for the
  `capability.github` gap's `github.non_github_host` choice, which excludes the gap's exact
  blocked ids with no free-form reason.
- **Never execute an `external_action`** (such as `github.restore_access`); ask whether the
  developer will do it, and re-scan on a later explicit request.
- **Never pin a project type to raise a number.** Pin what the repository is. If the honest
  type makes more criteria fail, that is the correct outcome.
- **Never ask a question outside the gap payload** — you may not inspect repository files to
  derive an answer.
- **Never batch questions.** One at a time, because a batched answer to five questions is
  reliably vaguer than five answers.
- **Never claim the score improved because of an answer.** It improved because the engine
  re-evaluated, or it did not improve at all.

## Notes

- No gaps but criteria still `unknown`? Those are agent judgments (`judgment.*`), which are
  advisory and belong to the **ra1-report** skill's T4 section, not to this interview.
- To act on findings rather than inputs, hand off to **ra1-fix** (the Loadout).
- Gaps are advisory output derived after scoring. Listing them, or leaving one unanswered,
  cannot change a level.
