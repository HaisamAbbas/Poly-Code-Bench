# PolyCodeBench implementation phase map

## 2. Phase map

| Phase | Prompts | Main deliverable | Exit gate |
|---|---|---|---|
| Phase 0 — Baseline and execution plan | 00 | Repository gap analysis, execution contract, ticket/evidence ledger | Every requirement, work package and test has a known owner/status; existing work preserved. |
| Phase 1 — Methodology and foundations | 01–05 | Reproducible workspace, schemas, database, artifact storage, task registry and admission policy | Inputs can be validated/frozen with trustworthy provenance; foundational contracts verified. |
| Phase 2 — Python/Rust harness and pilot | 06–17 | Isolated execution, durable jobs, models/tools, language plugins, quality evaluation, scoring, internal pilot | Two real model configurations complete the specified 144-attempt exploratory pilot with evidence and replay, or exact blockers remain explicit. |
| Phase 3 — Track A | 18 | Bug-source construction, detection/adjudication, patch evaluation | Known/false/novel/duplicate findings and repair failures are handled correctly. |
| Phase 4 — Remaining languages | 19–23 | JS, TS, C, C++, Go and Java plugins and admitted tasks | Every required language passes the same extension contract without core rewrites. |
| Phase 5 — Remaining Track B suites | 24–28 | Repository repair, realistic tasks, self-repair, Q&A and prediction | Each family preserves its protocol/native metric and uses only applicable scoring. |
| Phase 6 — Public product | 29–32 | Public API, all seven pages, evidence views and reviewed model submission | Actual published data drives complete public workflows; private data stays restricted. |
| Phase 7 — Operational hardening | 33 | Production-shaped infrastructure, restoration, monitoring and runbooks | Staging deploy/recovery/isolation/withdrawal rehearsals have real evidence. |
| Phase 8 — Final integrated audit | 34 | Full requirements audit, defect fixes, release-readiness report | No unjustified completed gates; every required capability has appropriate end-to-end evidence. |

Phase-ending prompts are **00, 05, 17, 18, 23, 28, 32, 33 and 34**. They must include a phase-wide gate summary as well as their prompt report. The other prompts use the same brief report format for their own scope.

## 3. Prompt-to-deliverable map

| Prompt | Work package | Deliverable |
|---|---|---|
| 00 | Prerequisite to all | Gap map, source manifest, execution contract and complete work ledger |
| 01 | WP-01 | Workspace, dependency locks, CI, startup configuration and methodology register |
| 02 | WP-02 | Canonical types, serialization, JSON Schemas and cross-language fixtures |
| 03 | WP-03 | Database/migrations, repository services, identity, audit and idempotency |
| 04 | WP-04 | Verified artifacts, visibility enforcement and safe lifecycle management |
| 05 | WP-05 | Task packages, admission/freezing, splits and source methodology records |
| 06 | WP-06 | Development and production VM sandbox drivers |
| 07 | WP-07 | Job DAG, leases/fences, recovery, cancellation and capacity slots |
| 08 | WP-08 | Model adapters, endpoint registration, usage and budget gateway |
| 09 | WP-09 | Single-shot and standard agent with durable tools/checkpoints |
| 10 | WP-10 | Python plugin, pinned toolchain, task fixtures and profiles |
| 11 | WP-11 | Rust plugin, pinned toolchain, task fixtures and profiles |
| 12 | WP-12 | Independent grading, analyzer evidence and robustness pipeline |
| 13 | WP-13 | Paired runtime/memory measurements and stability policy |
| 14 | WP-14 | Three-vote judge pipeline, reviewer flow and calibration evidence |
| 15 | WP-15 | Pure scoring, gating, language profiles and deterministic replay |
| 16 | WP-16 | Aggregates, intervals, immutable releases and reviewed publication |
| 17 | WP-17 | Real two-model Python/Rust pilot and internal report |
| 18 | WP-18 | Complete Track A workflow |
| 19 | WP-19, part 1 | Separate JavaScript and TypeScript support |
| 20 | WP-19, part 2 | C support |
| 21 | WP-19, part 3 | C++ support |
| 22 | WP-19, part 4 | Go support |
| 23 | WP-19, part 5 | Java support and complete language coverage audit |
| 24 | WP-20, part 1 | SWE-style/native repository repair adapter |
| 25 | WP-20, part 2 | Independently curated realistic repository tasks |
| 26 | WP-20, part 3 | Fixed-budget self-repair protocol |
| 27 | WP-20, part 4 | Repository Q&A and fact-based grading |
| 28 | WP-20, part 5 | Output/test prediction and complete suite coverage audit |
| 29 | WP-21 | Complete public projections/API and generated client |
| 30 | WP-22, part 1 | Leaderboard, language view and model profile |
| 31 | WP-22, part 2 | Comparison, task explorer and methodology pages |
| 32 | WP-22, part 3; WP-23 | Seventh page plus model-submission approval workflow |
| 33 | WP-24 | Production IaC, operational drills and runbooks |
| 34 | All WP-01–24 | Final integrated audit and verified repair pass |

## Current state

- Prompt 00: DONE (baseline and ledgers established; see `reports/prompt-00.md`).
- Phase 0 aggregate gate: PASS for baseline completeness after correcting the WP registration count to 24 and verifying all ledgers; product implementation remains incomplete.
- Prompt 01: DONE (workspace, locked installs/builds, CI-equivalent checks and local services verified; see `reports/prompt-01.md`).
- Prompt 02: DONE (contract models, canonical serialization, identity helpers, generated schemas and cross-runtime contract evidence; see `reports/prompt-02.md`).
- Prompt 03: DONE for foundational PostgreSQL schema, identity, audit and idempotent run creation; full route E2Es remain pending (see `reports/prompt-03.md`).
- Prompt 04: DONE for local artifact integrity, visibility, quota, manifest, retention and reviewed-projection foundation; production object-store policies and public routes remain pending (see `reports/prompt-04.md`).
- Prompt 05: DONE for WP-05 structural/authored-fixture admission, task-set freeze and versioned pilot/methodology contracts; see `reports/prompt-05.md`.
- Phase 1 aggregate gate: PASS. The project owner approved the specified v1 pilot weights on 2026-09-30 (D-05-04). Scoring remains inactive pending human/judge calibration; production task admission, later language/evaluation variants, and source/data rights remain explicit prerequisites. No scored task set is authorized.
- Prompt 06: PARTIAL. Typed sandbox contracts, local Docker enforcement, EC2 driver/guest/bootstrap and Terraform plan implemented; live Docker adversarial subcases passed. The owner deferred production VM/IaC validation because cloud access is unavailable; the required target/principal/approved AMI/budget are not configured. Resume per `progress.json` and `reports/prompt-06.md`.
- Prompt 07: PARTIAL. Durable PostgreSQL DAG/lease/fence/recovery, worker/sandbox integration, fairness, retry, cancellation and operator control implemented. E2E-07/08 passed on local PostgreSQL/SeaweedFS; E2E-09 local Docker cancellation and completed-evidence preservation passed. Durable model-usage retention waits for Prompt 08/17; see `reports/prompt-07.md`.

## Prompt register

| Prompt | Owner | Title | Work package | Deliverable | Status |
|---|---|---|---|---|---|
| 00 | Prompt 00 | — Read the documents and establish the implementation baseline | Prerequisite to all | Gap map, source manifest, execution contract and complete work ledger | done |
| 01 | Prompt 01 | — Bootstrap the workspace and methodology register | WP-01 | Workspace, dependency locks, CI, startup configuration and methodology register | done |
| 02 | Prompt 02 | — Implement canonical contracts and schemas | WP-02 | Canonical types, serialization, JSON Schemas and cross-language fixtures | done |
| 03 | Prompt 03 | — Build persistence, identity and idempotency | WP-03 | Database/migrations, repository services, identity, audit and idempotency | done (foundation); complete route scenarios remain pending |
| 04 | Prompt 04 | — Implement artifact storage and visibility | WP-04 | Verified artifacts, visibility enforcement and safe lifecycle management | done (local storage scope); production policy validation and public routes remain pending |
| 05 | Prompt 05 | — Build task admission and freeze the methodology contracts | WP-05 | Task packages, admission/freezing, splits and source methodology records | done (foundation; full E2E variants pending) |
| 06 | Prompt 06 | — Implement sandbox drivers and isolation | WP-06 | Development and production VM sandbox drivers | partial (local/driver implementation complete; production evidence blocked) |
| 07 | Prompt 07 | — Implement durable jobs and recovery | WP-07 | Job DAG, leases/fences, recovery, cancellation and capacity slots | partial (E2E-09 usage-retention variant pending Prompt 08/17) |
| 08 | Prompt 08 | — Implement model adapters and budget accounting | WP-08 | Model adapters, endpoint registration, usage and budget gateway | not_started |
| 09 | Prompt 09 | — Implement single-shot and agent execution | WP-09 | Single-shot and standard agent with durable tools/checkpoints | not_started |
| 10 | Prompt 10 | — Implement Python support | WP-10 | Python plugin, pinned toolchain, task fixtures and profiles | not_started |
| 11 | Prompt 11 | — Implement Rust support | WP-11 | Rust plugin, pinned toolchain, task fixtures and profiles | not_started |
| 12 | Prompt 12 | — Implement independent grading and normalized evidence | WP-12 | Independent grading, analyzer evidence and robustness pipeline | not_started |
| 13 | Prompt 13 | — Implement performance measurement | WP-13 | Paired runtime/memory measurements and stability policy | not_started |
| 14 | Prompt 14 | — Implement judging, review and calibration | WP-14 | Three-vote judge pipeline, reviewer flow and calibration evidence | not_started |
| 15 | Prompt 15 | — Implement deterministic scoring and replay | WP-15 | Pure scoring, gating, language profiles and deterministic replay | not_started |
| 16 | Prompt 16 | — Implement aggregation and release publication | WP-16 | Aggregates, intervals, immutable releases and reviewed publication | not_started |
| 17 | Prompt 17 | — Run and verify the real Python/Rust pilot | WP-17 | Real two-model Python/Rust pilot and internal report | not_started |
| 18 | Prompt 18 | — Implement Track A bug hunting and repair | WP-18 | Complete Track A workflow | not_started |
| 19 | Prompt 19 | — Add JavaScript and TypeScript support | WP-19, part 1 | Separate JavaScript and TypeScript support | not_started |
| 20 | Prompt 20 | — Add C support | WP-19, part 2 | C support | not_started |
| 21 | Prompt 21 | — Add C++ support | WP-19, part 3 | C++ support | not_started |
| 22 | Prompt 22 | — Add Go support | WP-19, part 4 | Go support | not_started |
| 23 | Prompt 23 | — Add Java and close language coverage | WP-19, part 5 | Java support and complete language coverage audit | not_started |
| 24 | Prompt 24 | — Implement repository repair benchmark adapters | WP-20, part 1 | SWE-style/native repository repair adapter | not_started |
| 25 | Prompt 25 | — Implement realistic repository tasks | WP-20, part 2 | Independently curated realistic repository tasks | not_started |
| 26 | Prompt 26 | — Implement self-repair | WP-20, part 3 | Fixed-budget self-repair protocol | not_started |
| 27 | Prompt 27 | — Implement repository understanding and factual Q&A | WP-20, part 4 | Repository Q&A and fact-based grading | not_started |
| 28 | Prompt 28 | — Implement prediction suites and close Track B coverage | WP-20, part 5 | Output/test prediction and complete suite coverage audit | not_started |
| 29 | Prompt 29 | — Implement the complete public API and projections | WP-21 | Complete public projections/API and generated client | not_started |
| 30 | Prompt 30 | — Build leaderboard, language and model pages | WP-22, part 1 | Leaderboard, language view and model profile | not_started |
| 31 | Prompt 31 | — Build comparison, task explorer and methodology pages | WP-22, part 2 | Comparison, task explorer and methodology pages | not_started |
| 32 | Prompt 32 | — Implement reviewed model submissions and close the public product phase | WP-22, part 3; WP-23 | Seventh page plus model-submission approval workflow | not_started |
| 33 | Prompt 33 | — Harden deployment and rehearse operations | WP-24 | Production IaC, operational drills and runbooks | not_started |
| 34 | Prompt 34 | — Perform the final integrated audit and repair pass | All WP-01–24 | Final integrated audit and verified repair pass | not_started |
