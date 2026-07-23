# DORA → Ready Agent 1 Crosswalk

This document maps durable DORA practices (2014–2024) and the DORA AI Capabilities Model
(2025–2026) onto Ready Agent 1's criteria taxonomy. It **never changes scoring** — it justifies
roadmap and advisory criterion design. Primary sources are the State of DevOps / Accelerate
reports (2014–2019, 2021–2025), the Gen-AI impact report, the DORA AI Capabilities Model, and
2026 agent/ROI guidance. Research notes that ground this crosswalk live as session artifacts
(`dora-findings-2014-2019`, `dora-findings-2021-2024`, `dora-findings-ai-agentic`).

**Method:** start from capabilities recurring in 3+ reports (2014–2019 table), merge with the
2021–2024 rollup, then overlay the seven AI capabilities and 2026 agent guidance. Repo-level
proxies for org constructs are marked `partial` and carry `[INFERENCE]` honesty markers — never
present a proxy as full coverage.

## State legend

| State | Meaning |
|---|---|
| `covered` | A deterministic criterion exists that measures the construct directly enough for advisory use. |
| `partial` | A prerequisite or repo-level proxy exists; a named criterion closes part of the rest. Proxies stay `partial`. |
| `gap` | No coverage; either a new criterion below closes it, or it is explicitly out of scope. |

## Durable practices

Merged from the 12-row "Cross-year durable practices" table (2014–2019) and the 2021–2024
rollup. Years include later revalidation where the rollup confirms continuity.

| Capability | Years supported | Citations | RA1 criteria ids | State |
|---|---|---|---|---|
| Continuous delivery as an integrated system | 2014–2019, 2021–2024 | [2014](https://dora.dev/research/2014/2014-state-of-devops-report.pdf); [2015](https://dora.dev/research/2015/2015-state-of-devops-report.pdf); [2016](https://dora.dev/research/2016/2016-state-of-devops-report.pdf); [2017](https://dora.dev/research/2017/2017-state-of-devops-report.pdf); [2018](https://dora.dev/research/2018/dora-report/2018-dora-accelerate-state-of-devops-report.pdf); [2019](https://dora.dev/research/2019/dora-report/2019-dora-accelerate-state-of-devops-report.pdf); [2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf); [2022](https://dora.dev/research/2022/dora-report/2022-dora-accelerate-state-of-devops-report.pdf); [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf); [2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf) | `build.ci_present`, `build.ci_runs_tests`, `build.release_automation`, `testing.unit_tests_exist`, `testing.integration_tests_exist` | `covered` |
| Comprehensive version control / production artifacts as code | 2014–2019, 2021–2024 | same primary PDFs above; [version control capability](https://dora.dev/capabilities/version-control/) | `build.vcs_cli`, `build.deps_pinned`, `build.agent_config_versioned` | `partial`[^vc] |
| Automated testing and fast feedback | 2014–2019, 2021–2024 | same; [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf) | `testing.unit_tests_exist`, `testing.integration_tests_exist`, `testing.coverage_threshold`, `build.ci_runs_tests`, `build.ci_duration_budget` | `covered` |
| Continuous integration and trunk-based / short-lived branches | 2014–2019, 2021–2023 | same; trunk nuance in [2022](https://dora.dev/research/2022/dora-report/2022-dora-accelerate-state-of-devops-report.pdf) | `build.ci_present`, `build.ci_runs_tests`, `build.integration_frequency` | `partial`[^trunk] |
| Lean flow: small batches, WIP visibility, and customer feedback | 2015–2018, 2021–2024 | [2015](https://dora.dev/research/2015/2015-state-of-devops-report.pdf)–[2018](https://dora.dev/research/2018/dora-report/2018-dora-accelerate-state-of-devops-report.pdf); [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf); [2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf); [small batches](https://dora.dev/capabilities/working-in-small-batches/) | `build.small_batches`, `build.integration_frequency`, `judgment.user_feedback_loop` | `partial`[^batches] |
| Deployment / configuration automation | 2015–2019, 2021–2022 | [2015](https://dora.dev/research/2015/2015-state-of-devops-report.pdf)–[2019](https://dora.dev/research/2019/dora-report/2019-dora-accelerate-state-of-devops-report.pdf); [2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf); [2022](https://dora.dev/research/2022/dora-report/2022-dora-accelerate-state-of-devops-report.pdf) | `build.release_automation`, `build.ci_present`, `build.single_command_setup` | `covered` |
| Monitoring → observability used for detection, learning, and decisions | 2014, 2015, 2018–2019, 2021–2024 | [2014](https://dora.dev/research/2014/2014-state-of-devops-report.pdf); [2018](https://dora.dev/research/2018/dora-report/2018-dora-accelerate-state-of-devops-report.pdf); [2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf)–[2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf) | `observability.structured_logging`, `observability.tracing`, `observability.metrics`, `observability.alerting_rules`, `observability.slo_definitions`, `observability.incident_learning` | `partial`[^obs] |
| Testable, deployable, loosely coupled architecture and teams | 2015, 2017–2019, 2021–2023 | [2015](https://dora.dev/research/2015/2015-state-of-devops-report.pdf); [2017](https://dora.dev/research/2017/2017-state-of-devops-report.pdf)–[2019](https://dora.dev/research/2019/dora-report/2019-dora-accelerate-state-of-devops-report.pdf); [2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf)–[2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf) | `docs.architecture_doc`, `judgment.code_modularization`, `judgment.service_flow_doc_quality` | `partial` |
| Integrating security early into daily delivery | 2016–2018, 2021–2022 | [2016](https://dora.dev/research/2016/2016-state-of-devops-report.pdf)–[2018](https://dora.dev/research/2018/dora-report/2018-dora-accelerate-state-of-devops-report.pdf); [2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf); [2022](https://dora.dev/research/2022/dora-report/2022-dora-accelerate-state-of-devops-report.pdf) | `security.automated_security_review`, `security.secret_scanning`, `security.dependency_update_automation`, `security.codeowners` | `covered` |
| Lightweight, automated, peer-based change approval | 2017–2019, 2023 | [2017](https://dora.dev/research/2017/2017-state-of-devops-report.pdf)–[2019](https://dora.dev/research/2019/dora-report/2019-dora-accelerate-state-of-devops-report.pdf); [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf) (review latency) | `security.branch_protection`, `taskdisc.pr_templates`, `taskdisc.review_latency` | `partial`[^review] |
| Generative, learning-oriented, high-trust culture | 2014–2019, 2021–2024 | same six 2014–2019 PDFs; [2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf)–[2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf) | `observability.incident_learning` (docs proxy only) | `partial` / out of scope for culture itself[^culture] |
| Leadership that enables autonomy, investment, and learning | 2015, 2017–2018, 2024 | [2015](https://dora.dev/research/2015/2015-state-of-devops-report.pdf); [2017](https://dora.dev/research/2017/2017-state-of-devops-report.pdf); [2018](https://dora.dev/research/2018/dora-report/2018-dora-accelerate-state-of-devops-report.pdf); [2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf) | — | `gap` (out of scope) |

[^vc]: `build.vcs_cli` proves VCS availability; `build.agent_config_versioned` is a **repo proxy** for versioning prompts/agent configs ([INFERENCE] from AI capability #4 / 2026 guidance). Comprehensive artifact versioning (IaC, DB scripts, recovery) is not fully scored.
[^trunk]: Integration cadence (`build.integration_frequency`) is a cadence proxy, not full trunk-topology measurement. DORA's stable recommendation is small changes + automated checks, not branch topology alone ([2022](https://dora.dev/research/2022/dora-report/2022-dora-accelerate-state-of-devops-report.pdf)).
[^batches]: `build.small_batches` uses a **median LOC churn heuristic** (≤400 changed lines over ≤50 non-merge commits). DORA defines small batches by integration frequency/releasability, not LOC — this is an RA1 heuristic proxy `[INFERENCE]`. Squash-merge workflows can inflate per-commit churn.
[^obs]: Existing observability criteria cover config+wiring. `observability.slo_definitions` and `observability.incident_learning` are **repo proxies** for reliability contracts and learning-from-failure documentation — not runtime SLO compliance or culture. `[INFERENCE]`
[^review]: `taskdisc.review_latency` measures median first-review latency via GitHub API (≤48h over ≤20 merged PRs). Bot reviews may flatter latency while advisory.
[^culture]: Fold note: 2014–2019 "generative culture" and 2021–2024 "generative culture and human systems" are one capability family. Repo evidence is limited to incident-learning docs (`partial`); culture/psychological safety remain out of scope.

**2021–2024 rollup folds (named, not silent):**

- *Documentation quality (amplifier)* — not a separate durable-row in the 2014–2019 3+ table; added from 2021–2024 as durable multiplier. RA1: `docs.readme`, `docs.agents_md`, `docs.doc_freshness`, `docs.architecture_doc`, judgments. State: `partial` (presence/freshness, not full quality).
- *Reliability / operational performance* — folded into the monitoring→observability row via SLO + incident-learning proxies (not the 2019 disaster-recovery-testing finding).
- *User centricity* — folded into lean-flow customer-feedback cell via `judgment.user_feedback_loop`; also listed under AI capability #6.
- *Continuous improvement / experimentation* — covered in part by `product.experiment_config`, `product.feature_flags`; transformation-loop evidence remains a `gap` (out of scope for this change).
- *Flexible infrastructure / cloud characteristics* — out of scope (not repo-observable as NIST-style capability).

## AI Capabilities Model crosswalk

Seven capabilities from the DORA AI Capabilities Model
([model](https://cloud.google.com/blog/products/ai-machine-learning/introducing-doras-inaugural-ai-capabilities-model);
[report](https://dora.dev/ai/capabilities-model/report/)). Mapping rows marked `[INFERENCE]`
follow the Taxonomy Mapping section of the AI/agentic findings note.

| Capability | Citations | RA1 criteria ids | State |
|---|---|---|---|
| 1. Clear and communicated AI stance | [stance](https://dora.dev/capabilities/clear-and-communicated-ai-stance/); [gen-AI report](https://dora.dev/ai/gen-ai-report/) | `docs.ai_stance` | `partial` `[INFERENCE]` — file/heading presence + tool/permission signal; not org socialization |
| 2. Healthy data ecosystems | [data ecosystems](https://dora.dev/capabilities/healthy-data-ecosystems/) | — | `gap` (out of scope — org-level data product) |
| 3. AI-accessible internal data | [AI-accessible data](https://dora.dev/capabilities/ai-accessible-internal-data/) | `docs.machine_context`, `docs.agents_md` | `partial` `[INFERENCE]` — MCP/`llms.txt` wiring beyond AGENTS.md |
| 4. Strong version control practices | [version control](https://dora.dev/capabilities/version-control/) | `build.vcs_cli`, `build.agent_config_versioned` | `partial` `[INFERENCE]` — agent config/prompt history ≥2 commits |
| 5. Working in small batches | [small batches](https://dora.dev/capabilities/working-in-small-batches/); [2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf) | `build.small_batches`, `build.integration_frequency` | `partial` `[INFERENCE]` — LOC/cadence proxies |
| 6. User-centric focus | [user-centric](https://dora.dev/capabilities/user-centric-focus/); [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf); [2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf) | `product.analytics_instrumentation`, `product.feature_flags`, `product.experiment_config`, `judgment.user_feedback_loop` | `partial` — T4 judgment for feedback→prioritization |
| 7. Quality internal platforms | [platform engineering](https://dora.dev/capabilities/platform-engineering/); [2025 report](https://dora.dev/research/2025/dora-report/) | `devenv.devcontainer`, `build.single_command_setup`, `devenv.env_template` | `partial` — local enablement only; platform staffing/DevEx out of scope |

**2026 agent guidance extras** ([balancing AI tensions](https://dora.dev/insights/balancing-ai-tensions/);
[tokenmaxxing](https://dora.dev/insights/finding-balance-in-the-era-of-tokenmaxxing/)):

| Guidance | RA1 criteria ids | State |
|---|---|---|
| Least-privilege agent access (RAG/MCP credentials) | `security.agent_permissions` | `partial` `[INFERENCE]` — shared deny/restrictive-allow config; not runtime enforcement |
| Versioned prompts / agent configs | `build.agent_config_versioned` | `partial` |
| Small generated changes + mandatory tests | `build.small_batches`, `testing.*`, `build.ci_runs_tests` | `partial` |
| Context-aware review agents | — | `gap` (not repo-deterministic in this change) |
| Human ownership of AI output | — | `gap` / judgment territory; not a new criterion here |
| Agent runaway budget / circuit breakers | — | `gap` (out of scope — not reliably repo-observable as shared policy yet) |
| Usage ≠ readiness (co-authorship, tokens, invocations) | `build.agentic_development` deliberately **excluded** from readiness claims | adoption evidence only |

## Out of scope (not repo-observable)

- Generative culture / Westrum typology — survey construct; incident docs are only a documentation proxy.
- Transformational leadership — org/people construct.
- Psychological safety — survey construct (extends culture line).
- AI trust levels — survey/attitude construct ([trust in AI](https://dora.dev/insights/trust-in-ai/)).
- Org-level healthy data ecosystems — data-product ownership and catalogs.
- Platform team staffing / DevEx NPS — org staffing and product metrics.
- Agent runaway budget / circuit breakers — not reliably a shared repo policy artifact yet.

## Reversals & cautions

1. **Documentation direct-effect instability.** 2021: docs predicted better SDO; 2022: direct delivery association anomalous/negative while multiplier remained; 2023: no direct delivery effect, strong multiplier; 2024: amplifies user signals / AI raised perceived doc quality. Treat direct delivery impact as context-sensitive; multiplier as durable. ([2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf); [2022 deep dive](https://cloud.google.com/blog/products/devops-sre/deep-dive-into-2022-state-of-devops-report-on-documentation); [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf); [2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf))
2. **Throughput / stability decoupling.** Earlier reports emphasized they move together; 2024 modeled separate factors and showed AI/platform adoption can improve productivity while delivery outcomes decline. ([2024](https://dora.dev/research/2024/dora-report/2024-dora-accelerate-state-of-devops-report.pdf); [2025](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report) — throughput turned positive, stability stayed negative)
3. **Trunk-topology nuance.** Strong elite association in 2021; experience-dependent pain in 2022; mediated through CD in 2023. Stable core: small changes, automated checks, never leave trunk broken — not branch topology alone. ([2021](https://dora.dev/research/2021/dora-report/2021-dora-accelerate-state-of-devops-report.pdf); [2022](https://dora.dev/research/2022/dora-report/2022-dora-accelerate-state-of-devops-report.pdf); [2023](https://dora.dev/research/2023/dora-report/2023-dora-accelerate-state-of-devops-report.pdf))
4. **Tokenmaxxing warning.** Token consumption, co-authorship counts, accepted suggestions, and invocation volume are input/vanity metrics vulnerable to Goodhart's Law — never score them as readiness or productivity. ([tokenmaxxing](https://dora.dev/insights/finding-balance-in-the-era-of-tokenmaxxing/); [2025 report](https://dora.dev/research/2025/dora-report/))

## Note on `build.agentic_development`

`build.agentic_development` (L5, advisory) detects agent co-authorship trailers in recent git
history. It is **adoption evidence only** and is deliberately excluded from readiness claims in
this crosswalk. Do not conflate it with AI stance, permissions, context wiring, or small-batch
safeguards.

## New advisory criteria in 0.6.0 (this change)

| id | Closes |
|---|---|
| `build.small_batches` | Lean flow / AI #5 — LOC churn proxy (`partial`) |
| `build.integration_frequency` | CI/trunk cadence proxy (`partial`) |
| `taskdisc.review_latency` | Fast peer review / change approval (`partial`) |
| `observability.slo_definitions` | Reliability contracts (`partial`) |
| `observability.incident_learning` | Learning-from-failure docs proxy (`partial`) |
| `docs.ai_stance` | AI capability #1 (`partial`) |
| `security.agent_permissions` | 2026 least-privilege agent config (`partial`) |
| `docs.machine_context` | AI capability #3 (`partial`) |
| `build.agent_config_versioned` | AI #4 + versioned agent configs (`partial`) |
| `judgment.user_feedback_loop` | AI #6 / user centricity (T4 forever) |

Every former `gap` row above either names one of these ids or an out-of-scope reason. Every
proxy criterion is `partial`, not `covered`.
