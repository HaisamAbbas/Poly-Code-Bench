# Requirement-to-code gap matrix

Status reflects the current inspected files and recorded verification. At the original Prompt 00 baseline, the workspace had only the three source documents and no implementation; historical baseline notes do not describe the current tree. Specification prose and package names alone are not implementation evidence.

| Requirement | Obligation | Status | Existing code paths / evidence | Owner work packages | Required E2E |
|---|---|---|---|---|---|
| REQ-01 | Python/Rust pilot, followed by Python, JavaScript, TypeScript, C, C++, Rust, Go, and Java support. | absent | None observed | WP-10, WP-11, WP-19 | E2E-15, E2E-35 |
| REQ-02 | Track A: historical, disclosed-security, and injected bugs; detection, localization, explanation, severity, and patches. | absent | None observed | WP-18 | E2E-32-34 |
| REQ-03 | Track B: generation, repository repair, realistic repository tasks, self-repair, repository Q&A, execution prediction, test-output prediction. | absent | None observed | WP-09, WP-12, WP-20 | E2E-31, E2E-36-38 |
| REQ-04 | Six-dimensional code evaluation with correctness gating and task-specific applicability. | absent | None observed | WP-12-15 | E2E-17-24 |
| REQ-05 | Versioned language profiles, anti-pattern rules, and evidence ownership. | absent | None observed | WP-10, WP-11, WP-15, WP-19 | E2E-23, E2E-35 |
| REQ-06 | Single-shot and standard agent protocols; API and local-model adapters; budgets and complete available usage records. | absent | None observed | WP-08, WP-09 | E2E-10-14 |
| REQ-07 | Offline isolated execution, separate solving/grading, resource limits, protected secrets. | absent | None observed | WP-06, WP-12 | E2E-05, E2E-06, E2E-17, E2E-26 |
| REQ-08 | Durable parallel jobs; bounded infrastructure retries; no answer-shopping retries. | absent | None observed | WP-07 | E2E-07-09 |
| REQ-09 | Immutable task, config, evidence, scoring, and release versions; deterministic score replay. | partial | Strict versioned contract models and semantic document digests in `packages/core/src/polycodebench_core/{models,canonical}.py`; deterministic cross-runtime config serialization verified by E2E-01. Persistence, evidence immutability, scoring replay and releases remain unimplemented. | WP-02-04, WP-15, WP-16 | E2E-01 passed at contract level; E2E-02-03, E2E-24, E2E-30 pending |
| REQ-10 | Public/date-based/private task splits; cutoff provenance; common-cohort contamination filters. | absent | None observed | WP-05, WP-16, WP-21 | E2E-27, E2E-28 |
| REQ-11 | All seven website pages, including reviewed model-submission requests. | absent | None observed | WP-21-23 | E2E-39-41 |
| REQ-12 | Exact score explanations, intervals, coverage, versioned corrections, and public/private evidence controls. | absent | None observed | WP-15, WP-16, WP-21 | E2E-23, E2E-26, E2E-28-30 |
| REQ-13 | Native-method documentation and clearly labeled adaptations for all four referenced benchmark families. | partial | `docs/methodology/{swebench,livecodebench,cursorbench,deepcodebench}.md`; source/terms register. Rights and source revisions remain open. | WP-01, WP-05, WP-20 | E2E-36-38; method-source review |
| REQ-14 | Deployable infrastructure, observability, backup/restore, operator runbooks, and a verified end-to-end demonstration. | absent | None observed | WP-06, WP-24 | E2E-42, E2E-43 |

## Work package status

| Work package | Deliverable | Status | Inspected implementation evidence | Acceptance evidence required |
|---|---|---|---|---|
| WP-01 | Workspace, lockfiles, formatting/type checks, CI, config loader, dependency-boundary checks, method-source register | present | `uv.lock` and `pnpm-lock.yaml` generated; locked Python/Node installs, Python package builds, mypy, frontend typecheck/lint/build, Python tests/config/boundary checks, and local Compose service startup passed. Methodology/source register is present; rights remain open for source/task admission. | Prompt 01 local CI passed; source rights remain tracked per benchmark/task admission. |
| WP-02 | Core types, JSON Schemas, canonical serialization, IDs/digests, status/error vocabularies, Python/TS golden fixtures | present | Strict Pydantic contracts, `pcb-json-v1` serializers in Python/TypeScript, identity/path/seed helpers, 13 generated JSON Schemas, generated OpenAPI components and TypeScript types, shared valid/invalid vectors; see Prompt 02 report. | Python and TypeScript contract fixtures/property checks; generated schema drift check; package type/build checks. |
| WP-03 | SQL models/migrations, repositories, transactions, RBAC foundations, audit/idempotency records | absent | None observed; no application source exists in workspace | E2E-02, E2E-25; migration upgrade from prior fixture. |
| WP-04 | Artifact upload/finalize/read service, digest verification, visibility roles, provisional cleanup | absent | None observed; no application source exists in workspace | E2E-03, E2E-26. |
| WP-05 | Task import/admission/freeze, split/cluster registry, rights/provenance, method records, task-set CLI/API | absent | None observed; no application source exists in workspace | E2E-04, E2E-27; reference/faulty/alternative fixtures. |
| WP-06 | Sandbox interface, development driver, production VM driver, guest/bootstrap policy, resource and TTL enforcement | absent | None observed; no application source exists in workspace | E2E-05, E2E-06; real VM lifecycle in staging. |
| WP-07 | Durable stage DAG, SQL leases/fences, events, retry taxonomy, reaper, cancellation and capacity accounting | absent | None observed; no application source exists in workspace | E2E-07, E2E-08, E2E-09. |
| WP-08 | Model adapters, capabilities, endpoint approval, call/usage ledger, monetary/token reservations, provider throttling | absent | None observed; no application source exists in workspace | E2E-10, E2E-11, E2E-12. |
| WP-09 | Single-shot and standard agent, bounded tools, context/checkpoint policy, extraction and candidate freeze | absent | None observed; no application source exists in workspace | E2E-13, E2E-14; source/trace integrity. |
| WP-10 | Python toolchain/plugin, admitted task fixtures, Ruff/mypy/Bandit and relevant test integrations | absent | None observed; no application source exists in workspace | E2E-15; full language conformance. |
| WP-11 | Rust toolchain/plugin, admitted fixtures, clippy/audit/Miri capability handling and test integration | absent | None observed; no application source exists in workspace | E2E-15, E2E-16; full language conformance. |
| WP-12 | Fresh grading environments, mandatory test inventory, analyzer parsing, baseline deltas, canonical issue mapping, robustness | absent | None observed; no application source exists in workspace | E2E-16, E2E-17, E2E-18. |
| WP-13 | Exclusive performance lane, paired iterations, canaries, runtime/memory evidence, censored timing policy | absent | None observed; no application source exists in workspace | E2E-19, E2E-20. |
| WP-14 | Judge packets/votes, fixed panel, invalid-vote handling, disagreement/reviewer API, calibration report | absent | None observed; no application source exists in workspace | E2E-21, E2E-22. |
| WP-15 | Pure scorer, applicability/gating, profile/owner configuration, exact score breakdown and deterministic replay | absent | None observed; no application source exists in workspace | E2E-23, E2E-24; all golden calculations. |
| WP-16 | Common-cohort aggregation, bootstrap, release lifecycle, projection validation/signing and atomic pointer | absent | None observed; no application source exists in workspace | E2E-28, E2E-29, E2E-30. |
| WP-17 | Live Python/Rust pilot with two real model configs, fixed protocol, internal report and full provenance export | absent | None observed; no application source exists in workspace | E2E-31; pilot completion report, calibration limitations disclosed. |
| WP-18 | Track A source builders, ground truth/matching/adjudication, detection/explanation/severity, repair and clean controls | absent | None observed; no application source exists in workspace | E2E-32, E2E-33, E2E-34. |
| WP-19 | JS, TS, C, C++, Go, Java plugins, tools, images, profiles, admitted task packs | absent | None observed; no application source exists in workspace | E2E-15, E2E-35 for every added language. |
| WP-20 | Remaining Track B adapters: native/inspired repo tasks, self-repair, Q&A and prediction; method docs | absent | None observed; no application source exists in workspace | E2E-36, E2E-37, E2E-38. |
| WP-21 | Complete public read projections/API, comparison/filter queries, evidence endpoints, generated TypeScript client | absent | None observed; no application source exists in workspace | E2E-26, E2E-28, E2E-39. |
| WP-22 | All seven frontend routes/components, charts/intervals, public code diff, accessibility, responsive performance | absent | None observed; no application source exists in workspace | E2E-39, E2E-40. |
| WP-23 | Model-submission workflow, verified request identity, endpoint review, quota-bound run approval/status | absent | None observed; no application source exists in workspace | E2E-41. |
| WP-24 | Production IaC completion, monitoring/runbooks, restore/load/security rehearsals, release demonstration and docs | absent | None observed; no application source exists in workspace | E2E-42, E2E-43; product-complete gate. |
