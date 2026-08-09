# Criterion graduation policy

How an advisory (`gating: false`) criterion earns a gating slot. Graduation is deliberately
hard: a gating vote moves the Level of every repository the engine scores, so the bar is
evidence, review, and a deliberate release change — never enthusiasm.

## Quantitative eligibility (necessary, never sufficient)

`python3 -m evals.criterion_benchmark --candidate <exact-criterion-id>` computes the
deterministic eligibility artifact from the committed label corpus
(`evals/criterion_labels.json`). A criterion is **quantitatively eligible** only when all
of the following hold:

- at least **100** reviewed cases for the criterion;
- at least **30** cases with expected status `pass` and at least **30** with expected
  blocking status (`fail` or `unknown`);
- representation from at least **5** named ecosystems;
- pass precision (`true_pass / all predicted pass`) of at least **0.99**;
- exact four-status (`pass|fail|unknown|skipped`) accuracy of at least **0.95**;
- **zero** predicted `pass` for expected non-pass cases tagged `adversarial: true` or
  severity `high`/`critical`, and zero label/schema errors.

The benchmark exits `0` only when the policy passes, `1` when a structurally valid corpus
is ineligible, and `2` on an invalid corpus or usage. `python3 -m evals.criterion_benchmark
--validate evals/criterion_labels.json` validates the committed corpus without evaluating
eligibility. The benchmark **cannot mutate the registry** and **cannot prove human
review**: every artifact carries `review_status: "unverified_external"` because the
two-reviewer requirement is enforced by Git review, not by this tool.

## Label corpus rules

- Ground truth is authored **without using the check output** — write the expected status
  from the fixture, never from what the check says today.
- Every label PR requires **approval from two independent human reviewers**.
- Cases reference only committed, safe, repository-relative fixture roots under
  `evals/fixtures/corpus/`.
- Each case is exactly `{id, criterion_id, fixture, ecosystem, expected, adversarial,
  severity}` with the documented enums.

## Graduation (the deliberate part)

Eligibility is not graduation. A gating change requires all of:

1. a maintainer-authored **ADR** that cites the benchmark artifact, examines every
   remaining error in the confusion matrix, and explains user impact and applicability;
2. a **reviewed release change** that flips `gating: false` to `true` in
   `engine/readiness/criteria/registry.json`;
3. **before/after score and history fixtures** making the denominator/Level change
   explicit, and a CHANGELOG entry recording the score-contract migration.

## De-graduation

Discovery of a new high/critical or adversarial false pass after graduation requires a
reviewed patch release that returns the criterion to `gating: false` and records the
score/history migration — quantitative metrics are observed-corpus thresholds, not
statistical confidence guarantees.
