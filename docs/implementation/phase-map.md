# PolyCodeBench implementation phase map

## 2. Phase map

| Phase | Prompts | Main deliverable | Exit gate |
|---|---|---|---|
| Phase 0 — Baseline and execution plan | 00 | Repository gap analysis, execution contract, ticket/evidence ledger | Every requirement, work package and test has a known owner/status; existing work preserved. |
| Phase 1 — Methodology and foundations | 01–05 | Reproducible workspace, schemas, database, artifact storage, task registry and admission policy | Inputs can be validated/frozen with trustworthy provenance; foundational contracts verified. |
| Phase 2 — Python/Rust harness and pilot | 06–17 | Isolated execution, durable jobs, models/tools, language plugins, quality evaluation, scoring, internal pilot | **BLOCKED** - Prompt 17 preflight defines 144 attempts; 0 dispatched, 0 completed, 0 model-failed, 144 infrastructure/preflight-blocked. Production isolation, rights/freeze, calibration, spend and resolved provider inputs remain outstanding. |
| Phase 3 - Track A | 18 | Bug-source construction, detection/adjudication, patch evaluation | **BLOCKED** - Prompt 18 implementation and E2E-32 to 34 internal acceptance passed independently; Phase 2 accepted-pilot prerequisite from Prompt 17 remains blocked, so the aggregate phase exit gate is not satisfied. |
| Phase 4 - Remaining languages | 19-23 | JavaScript, TypeScript, C, C++, Go and Java plugins, tools, images, profiles and admitted tasks | **BLOCKED** - Java admission passes 26/26; JavaScript 25/25 and C 29/29 development-sandbox admissions are current; Go admission passes 24/24 but its latest conformance artifact fails 1/18. C++ lacks current admission and TypeScript has no task pack; Python/Rust evidence is historical. E2E-15/35 remain partial. |
| Phase 5 — Remaining Track B suites | 24–28 | Repository repair, realistic tasks, self-repair, Q&A and prediction | Each family preserves its protocol/native metric and uses only applicable scoring. |
| Phase 6 — Public product | 29–32 | Public API, all seven pages, evidence views and reviewed model submission | Actual published data drives complete public workflows; private data stays restricted. |
| Phase 7 — Operational hardening | 33 | Production-shaped infrastructure, restoration, monitoring and runbooks | **BLOCKED** - Staging deploy/recovery/isolation/withdrawal rehearsals have real evidence. Prompt 33 delivered validated IaC and passed every local production-shaped rehearsal, but no staging environment exists (no authorized account/budget); `alembic check` drift (D-33-03) and a branched migration history (D-33-04) also block deployment. |
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
- Prompt 08: PARTIAL. Accountable model gateway (four adapters, endpoint governance, call/usage ledger, hierarchical budget reservations) implemented; E2E-10/12 and gateway-level E2E-11 passed on local PostgreSQL with fixture transport; local adapter verified live against Ollama. Live OpenAI-compatible, Anthropic and Google checks are blocked on provider credentials, prices and a spend budget, so those adapters are live-untested. Resume per `progress.json` and `reports/prompt-08.md`.

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
| 08 | Prompt 08 | — Implement model adapters and budget accounting | WP-08 | Model adapters, endpoint registration, usage and budget gateway | partial (hosted OpenAI-compatible/Anthropic/Google adapters live-untested; two-model live pilot remains Prompt 17) |
| 09 | Prompt 09 | — Implement single-shot and agent execution | WP-09 | Single-shot and standard agent with durable tools/checkpoints | done (fixture model and development sandbox; live model evidence Prompt 17) |
| 10 | Prompt 10 | — Implement Python support | WP-10 | Python plugin, pinned toolchain, task fixtures and profiles | done (development sandbox: 12/12 clusters executable-admission passed; quality admission pending Prompts 12–15) |
| 11 | Prompt 11 | — Implement Rust support | WP-11 | Rust plugin, pinned toolchain, task fixtures and profiles | done (development sandbox: 12/12 clusters executable-admission passed; quality admission pending Prompts 12–15) |
| 12 | Prompt 12 | — Implement independent grading and normalized evidence | WP-12 | Independent grading, analyzer evidence and robustness pipeline | done (development sandbox; production and admission gates pending) |
| 13 | Prompt 13 | — Implement performance measurement | WP-13 | Paired runtime/memory measurements and stability policy | in_progress (parallel implementation) |
| 14 | Prompt 14 | — Implement judging, review and calibration | WP-14 | Three-vote judge pipeline, reviewer flow and calibration evidence | in_progress (parallel implementation) |
| 15 | Prompt 15 | - Implement deterministic scoring and replay | WP-15 | Pure scoring, gating, language profiles and deterministic replay | done (pure scorer, gating, ownership, explanation and clean-process replay implemented and verified on fixtures; the live-pilot replay variant stays with Prompt 17 and `effective_for_scoring` stays false until calibration) |
| 16 | Prompt 16 | — Implement aggregation and release publication | WP-16 | Aggregates, intervals, immutable releases and reviewed publication | done (synthetic/internal implementation and tests; external publication is not authorized) |
| 17 | Prompt 17 | — Run and verify the real Python/Rust pilot | WP-17 | Real two-model Python/Rust pilot and internal report | blocked (preflight complete; no live dispatch authorized/possible) |
| 18 | Prompt 18 | - Implement Track A bug hunting and repair | WP-18 | Complete Track A workflow | implemented independently (internal evidence passed; Phase 3 aggregate gate blocked by Prompt 17) |
| 19 | Prompt 19 | — Add JavaScript and TypeScript support | WP-19, part 1 | Separate JavaScript and TypeScript support | partial (JS/TS source plugins and entrypoints exist; no allowlist entries, task packs or complete images) |
| 20 | Prompt 20 | — Add C support | WP-19, part 2 | C support | partial (local C plugin/plans/fixtures exist; fresh image admission unverified) |
| 21 | Prompt 21 | — Add C++ support | WP-19, part 3 | C++ support | partial (executable admission passed 27/27 checks in pinned development-sandbox images across eight synthetic variants; quality admission, curator approval and downstream scorer/replay remain pending; race/concurrency coverage remains unverified; E2E-15/35 remain partial) |
| 22 | Prompt 22 | — Add Go support | WP-19, part 4 | Go support | partial (plugin, pinned recipes, profiles, task pack, 24/24 executable admission and 18/18 development-sandbox conformance pass; Go tool identity cleanup remains) |
| 23 | Prompt 23 | - Add Java and close language coverage | WP-19, part 5 | Java support and complete language coverage audit | partial (Java implementation, pinned images and development-sandbox admission are complete; all-language conformance remains incomplete) |
| 24 | Prompt 24 | — Implement repository repair benchmark adapters | WP-20, part 1 | SWE-style/native repository repair adapter | done (E2E-36 passed at the local-fixture tier through the pinned upstream evaluator; no official dataset instance imported) |
| 25 | Prompt 25 | — Implement realistic repository tasks | WP-20, part 2 | Independently curated realistic repository tasks | not_started |
| 26 | Prompt 26 | — Implement self-repair | WP-20, part 3 | Fixed-budget self-repair protocol | not_started |
| 27 | Prompt 27 | — Implement repository understanding and factual Q&A | WP-20, part 4 | Repository Q&A and fact-based grading | not_started |
| 28 | Prompt 28 | — Implement prediction suites and close Track B coverage | WP-20, part 5 | Output/test prediction and complete suite coverage audit | done (E2E-38 prediction subcases passed at the local fixture tier; the seven-family coverage audit closes WP-20 on real entrypoints, with the repo_qa evidence pointer provisional until Prompt 27 completes) |
| 29 | Prompt 29 | Implement the complete public API and projections | WP-21 | Complete public projections/API and generated client | partial (public API/projections are implemented and exercised by Prompts 30-32; generated-client parity and full E2E-26 remain open) |
| 30 | Prompt 30 | — Build leaderboard, language and model pages | WP-22, part 1 | Leaderboard, language view and model profile | done (PCB-30-1..4 implemented; Prompt 30 E2E-39/40 browser subcases pass against an explicitly synthetic published test release; full E2E-39/40 remain partial across Prompts 31/32) |
| 31 | Prompt 31 | — Build comparison, task explorer and methodology pages | WP-22, part 2 | Comparison, task explorer and methodology pages | not_started |
| 32 | Prompt 32 | — Implement reviewed model submissions and close the public product phase | WP-22, part 3; WP-23 | Seventh page plus model-submission approval workflow | not_started |
| 33 | Prompt 33 | — Harden deployment and rehearse operations | WP-24 | Production IaC, operational drills and runbooks | blocked (PCB-33-1..4 implemented; IaC validated, local isolated restore, drills, load and security rehearsals passed; staging deployment and E2E-42/43 staging variants blocked on AWS authorization; D-33-03 migration drift and D-33-04 branched history must be resolved before any deploy) |
| 34 | Prompt 34 | — Perform the final integrated audit and repair pass | All WP-01–24 | Final integrated audit and verified repair pass | not_started |

- Prompt 23: PARTIAL. Java recipes, profiles, task fixtures and local shared-contract audit are implemented; current image identity and complete eight-language conformance remain blocked. See `reports/prompt-23.md`, `reports/language-coverage.md` and the Phase 4 gate report.
- Phase 4 aggregate gate: BLOCKED. E2E-15 and E2E-35 remain partial because C++ admission and TypeScript task-backed execution are missing, Python/Rust evidence is historical, and Go still has tool identity cleanup and downstream path evidence outstanding.
