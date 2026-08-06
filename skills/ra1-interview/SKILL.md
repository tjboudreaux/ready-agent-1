---
name: ra1-interview
description: Resolve the unanswered questions in a Ready Agent 1 readiness report by interviewing the developer, then record the answers as engine inputs and re-score. Use when a readiness report lists unanswered questions or gaps, when `ra1 gaps` returns any gap, when a criterion is `unknown` because the scan could not classify the project or reach a data source, or when the user asks to answer, pin, or configure what the readiness scan could not infer.
license: MIT
compatibility: Python 3.11+; optional authenticated gh CLI for GitHub (T2) checks
metadata:
  version: 0.10.0
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Agent Readiness Interview

A readiness scan reports `unknown` when it lacks an input, not when a repository is bad. This
skill closes that distance: it asks the developer the questions the engine cannot answer,
records each answer where the **engine** reads it, and re-scores so the change in the number
comes from re-evaluation rather than from anyone's opinion.

**The engine owns every verdict.** You collect inputs. An answer can make a criterion
evaluable or exclude it with a disclosed waiver; an answer can never mark one passing.

## Steps

### 1. List the gaps

```bash
python3 "$(dirname "$0")/scripts/readiness/cli.py" gaps \
  --project <repo-path> --format json
```

Each gap carries `question`, `why`, `answer` (the file and dotted path that records it),
`options`, `evidence` (what the scan already saw), `blocks`, `blocked_gating`, `levels`, and
`waivable`. The list is already ordered by leverage: the first gap blocks the gate the reader
is trying to clear.

**Done when:** you have the gap list. Empty list → tell the user every input was inferable and
stop; there is nothing to interview about.

### 2. Answer from the codebase before asking

For every gap, try to answer it yourself first. Read the repo: manifests, entrypoints,
Dockerfiles, CI workflows, `Makefile`, scripts, `AGENTS.md`. A question you can settle with
`Read` and `Glob` is not a question worth a developer's turn.

Carry your finding into the question as a recommendation, with the evidence that produced it.
Never ask a bare question when you hold a defensible answer.

**Done when:** each gap has either a recommended answer plus its evidence, or an explicit note
that the repository is genuinely silent on it.

### 3. Interview, one question at a time

Ask **one** question, wait for the answer, then ask the next. Order follows the gap list, with
one exception: ask a question whose answer changes later questions first (project type before
anything scoped to an application).

Each question states, in this order: the question, your recommended answer and why, what
answering it unblocks (`blocked_gating` and `levels`), and the accepted `options` verbatim.

Read [reference/questions.md](reference/questions.md) for the wording rules per gap kind and
for what to do with a vague, "I don't know", or contradictory answer.

**Done when:** every gap has an answer, an explicit "leave it unanswered", or a waiver
decision — and you asked no question the codebase had already answered.

### 4. Record answers where the engine reads them

Write each answer to the file and dotted path the gap named. Merge into existing config; never
clobber a file you did not read first. Read
[reference/recording-answers.md](reference/recording-answers.md) for the exact schemas,
the merge rules, and the provenance comment format.

Two paths, and the difference is the integrity of the score:

| Answer supplies | Goes to | Effect on the score |
|---|---|---|
| An input the engine can act on (project type, verify command, CI budget, loop opt-in) | `.agents/readiness/config.json` | The criterion becomes evaluable; the engine judges it and may still fail it |
| A fact whose evidence lives outside this repository (only for `waivable` gaps) | `.agents/readiness/waivers.json` | The criterion is **excluded** from the gate and disclosed as waived — never counted as passing |

**Done when:** every recorded answer is valid JSON, every waiver carries the developer's own
reason, and no file lost pre-existing keys.

### 5. Re-score and report the delta honestly

Re-run the report so the engine re-evaluates with the new inputs:

```bash
python3 "$(dirname "$0")/scripts/readiness/cli.py" report \
  --project <repo-path> --format json,markdown --store-history \
  --out <repo-path>/.agents/readiness
```

Report: the level before and after, which criteria changed status and why, the gaps that
remain, and — separately from any improvement — every criterion that is now **waived rather
than passing**, with its reason. If pinning a type turned an `unknown` into a `fail`, say so
plainly; that is the interview working, not failing.

**Done when:** you have stated the new level from the engine's own `score` block, listed the
status changes, and named the remaining gaps.

## Contract (do not violate)

- **Never write a status, a score, or a pass.** You write config and waivers; the engine
  writes verdicts.
- **Never waive what the scanner looked for and did not find.** A missing linter config is a
  finding with a fix, not a fact about the outside world. Waive only `waivable` gaps, where the
  evidence genuinely lives in a system the scan cannot read.
- **Never accept a waiver without the developer's own reason** in their words, and never write
  one they did not agree to.
- **Never pin a project type to raise a number.** Pin what the repository is. If the honest
  type makes more criteria fail, that is the correct outcome.
- **Never ask a question the codebase answers.** Explore first (step 2).
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
