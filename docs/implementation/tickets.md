# Engineering ticket ledger

Total registered tickets: 142. Every ticket is owned by the numbered prompt encoded in its ID. Dependencies and verification scope are copied from the prompt/work-package definitions. Implementation and verification states reflect inspected workspace files, not planned architecture.

## PCB-00-1 - Prompt 00: — Read the documents and establish the implementation baseline

- Owner prompt: `00`.
- Dependencies: none (initial baseline).
- Implementation: `implemented` (Prompt 00 baseline deliverable completed).
- Verification: `passed` (read-only inspection and documentation consistency evidence recorded).
- Required verification scope: Prompt 00 source/inventory review and ledger consistency check.
- Evidence: Source identity and recorded/expected digest comparison: `source-manifest.json` and `reports/prompt-00.md`.
- Acceptance criteria: — Establish source identity. Record actual paths, versions and hashes; inspect differences from the pack's recorded hashes. Definition of done: the authoritative sources and any actual changes are explicit.

## PCB-00-2 - Prompt 00: — Read the documents and establish the implementation baseline

- Owner prompt: `00`.
- Dependencies: none (initial baseline).
- Implementation: `implemented` (Prompt 00 baseline deliverable completed).
- Verification: `passed` (read-only inspection and documentation consistency evidence recorded).
- Required verification scope: Prompt 00 source/inventory review and ledger consistency check.
- Evidence: Actual workspace inventory gap assessment: `requirements-matrix.md`.
- Acceptance criteria: — Build a requirement-to-code gap map. Classify each REQ-01–14 and WP-01–24 as present/partial/absent/unverified using actual files. Definition of done: no existing functionality is declared correct merely from naming or documentation.

## PCB-00-3 - Prompt 00: — Read the documents and establish the implementation baseline

- Owner prompt: `00`.
- Dependencies: none (initial baseline).
- Implementation: `implemented` (Prompt 00 baseline deliverable completed).
- Verification: `passed` (read-only inspection and documentation consistency evidence recorded).
- Required verification scope: Prompt 00 source/inventory review and ledger consistency check.
- Evidence: Copied execution contract and complete ledgers/counts: `execution-contract.md`, all matrices, `verify_prompt00.py`, and `reports/prompt-00.md`.
- Acceptance criteria: — Initialize the execution contract and ledgers specified in pack §4. Copy all normative contract/report requirements without weakening them. Register every PCB ticket in this pack and every E2E-01–43 scenario, with owner prompt and required evidence. Definition of done: later sessions can resume from repository state.

## PCB-00-4 - Prompt 00: — Read the documents and establish the implementation baseline

- Owner prompt: `00`.
- Dependencies: none (initial baseline).
- Implementation: `implemented` (Prompt 00 baseline deliverable completed).
- Verification: `passed` (read-only inspection and documentation consistency evidence recorded).
- Required verification scope: Prompt 00 source/inventory review and ledger consistency check.
- Evidence: Read-only runtime/service/config-name inspection: `prerequisites.md` and `commands.md`.
- Acceptance criteria: — Assess local development and external prerequisites: Python/Node/package managers, PostgreSQL, object storage, Docker/VM capability, model/judge endpoints, budgets, source rights and human calibration. Do not print secrets or start paid work. Definition of done: unknown/blocked prerequisites are distinguished from absent implementation.

## PCB-00-5 - Prompt 00: — Read the documents and establish the implementation baseline

- Owner prompt: `00`.
- Dependencies: none (initial baseline).
- Implementation: `implemented` (Prompt 00 baseline deliverable completed).
- Verification: `passed` (read-only inspection and documentation consistency evidence recorded).
- Required verification scope: Prompt 00 source/inventory review and ledger consistency check.
- Evidence: Sequencing and missing-source assessment: `decisions.md` and `phase-map.md`.
- Acceptance criteria: — Resolve implementation sequencing and record material discrepancies. Preserve correct existing work and unrelated changes; list refactor boundaries only where needed. Definition of done: the next prompt is concrete and compatible with the source documents.

## PCB-01-1 - Prompt 01: — Bootstrap the workspace and methodology register

- Owner prompt: `01`.
- Dependencies: Prompt 00 baseline.
- Implementation: `implemented` (Prompt 01 deliverable completed).
- Verification: `passed` (checks and evidence recorded in the Prompt 01 resume report and command registry).
- Required verification scope: package imports/builds and prohibited dependency rules in CI.
- Evidence: Python workspace packages: `uv build --all-packages` built sdists and wheels for all 10 packages; `python scripts/smoke_workspace.py` imported all 10; `python scripts/check_boundaries.py` passed. Ruff format/lint and locked mypy passed. No production benchmark behavior was added.
- Acceptance criteria: — Create or reconcile the Python/TypeScript workspace and module boundaries. Establish core/services/persistence/orchestration/runner/evaluation/scoring/publication/plugin ownership. DoD: packages build/import and prohibited dependencies are checked; no fake production behavior is added.

## PCB-01-2 - Prompt 01: — Bootstrap the workspace and methodology register

- Owner prompt: `01`.
- Dependencies: Prompt 00 baseline.
- Implementation: `implemented` (Prompt 01 deliverable completed).
- Verification: `passed` (checks and evidence recorded in the Prompt 01 resume report and command registry).
- Required verification scope: clean locked Python/Node installs, builds, runtime compatibility, and pinned development image resolution.
- Evidence: `uv.lock` and `pnpm-lock.yaml` generated; `uv sync --locked --all-packages --group dev` and `pnpm install --frozen-lockfile` passed (pnpm 12.5.1 supply-chain policy verified, 343 locked packages installed). All Python package builds and web typecheck/lint/build passed. Docker Registry index and linux/amd64 image digests are pinned and were inspected after pull; see D-01-04 and `commands.md`. PostgreSQL 17.6 and SeaweedFS 4.48 are development-only.
- Acceptance criteria: — Resolve compatible runtime/package versions and commit reproducible lockfiles plus approved development images. DoD: a clean environment can install the locked dependencies without floating latest tags or undocumented PATH assumptions.

## PCB-01-3 - Prompt 01: — Bootstrap the workspace and methodology register

- Owner prompt: `01`.
- Dependencies: Prompt 00 baseline.
- Implementation: `implemented` (Prompt 01 deliverable completed).
- Verification: `passed` (checks and evidence recorded in the Prompt 01 resume report and command registry).
- Required verification scope: invalid/missing role config rejection; format/type/test/build checks; service config parse and smoke execution.
- Evidence: `uv run ... ruff format --check`, Ruff lint, mypy, pytest (3 passed), package boundary, import/config smoke, and schema checks passed; `uv build --all-packages` built all 10 packages. Node 24.21.0/pnpm 12.5.1 frozen install, web typecheck/lint/Next build passed. `docker compose config --quiet` and `docker compose up -d --wait` passed; PostgreSQL health/readiness passed, S3 API responded on localhost:8333. See exact commands in `commands.md`.
- Acceptance criteria: — Implement validated role-specific startup configuration, development service definitions and a minimal CI pipeline for format/type/test/build checks. DoD: invalid/missing required config fails safely and the existing smoke checks actually run.

## PCB-01-4 - Prompt 01: — Bootstrap the workspace and methodology register

- Owner prompt: `01`.
- Dependencies: Prompt 00 baseline.
- Implementation: `implemented` (Prompt 01 work recorded against current files).
- Verification: `passed` (see Prompt 01 evidence and blockers).
- Required verification scope: review all four benchmark methods, native metrics, adaptations, available source revisions, private assets, and rights status.
- Evidence: `docs/methodology/` and `docs/implementation/reports/prompt-01.md`; primary method sources, native/adapted boundaries, private asset and source rights disclosures recorded.
- Acceptance criteria: — Create docs/methodology/{swebench,livecodebench,cursorbench,deepcodebench}.md and a source/terms register. Use the primary sources referenced in the architecture; record verified methods, native metrics, adaptations, unavailable private assets and unresolved rights. DoD: no invented methods or claims of reproducing private CursorBench tasks.

## PCB-02-1 - Prompt 02: — Implement canonical contracts and schemas

- Owner prompt: `02`.
- Dependencies: Prompt 01 ·.
- Implementation: `implemented` (strict versioned models, status vocabulary, typed errors, and cross-document reference validation).
- Verification: `passed` (contract unit checks and generated schema drift check).
- Required verification scope: unknown-field/coercion/reference/range rejection; E2E-01 contract slice.
- Evidence: `packages/core/src/polycodebench_core/models.py`, `errors.py`, `validation.py`, `plugins.py`; `tests/test_core_contracts.py`; `schemas/contracts/`; Prompt 02 report.
- Acceptance criteria: — Implement strict task, run, candidate, observation, artifact, scorecard, protocol and plugin contract models, statuses and typed errors. DoD: unknown fields, invalid references/ranges and forbidden coercions are rejected; schema versions are explicit.

## PCB-02-2 - Prompt 02: — Implement canonical contracts and schemas

- Owner prompt: `02`.
- Dependencies: Prompt 01 ·.
- Implementation: `implemented` (`pcb-json-v1` canonical bytes, strict duplicate-key parsing, semantic content digests, UTF-8 bundle sorting and bundle digests in both runtimes).
- Verification: `passed` (shared expected bytes/digests and invalid-input/property checks passed in Python and TypeScript).
- Required verification scope: E2E-01; safe integers, decimal strings, UTF-8, duplicate keys, reordered keys, file manifests.
- Evidence: `packages/core/src/polycodebench_core/canonical.py`, `apps/contracts/src/canonical.ts`, `tests/fixtures/contracts/`, both runtime test suites; Prompt 02 report.
- Acceptance criteria: — Implement pcb-json-v1 canonical bytes and content/bundle digests. Enforce ASCII sorted keys, safe JSON integer bounds, decimal-string money/64-bit seeds, UTF-8 rules, duplicate-key rejection and deterministic file manifests. DoD: Python and TypeScript match byte-for-byte on shared vectors.

## PCB-02-3 - Prompt 02: — Implement canonical contracts and schemas

- Owner prompt: `02`.
- Dependencies: Prompt 01 ·.
- Implementation: `implemented` (UUIDv4 IDs, UTC validation, monotonic timers, safe POSIX relative paths, unsigned 64-bit decimal seed derivation and unnormalized source-byte hashing).
- Verification: `passed` (boundary fixtures and identity checks in both runtimes).
- Required verification scope: uint64 maximum, invalid timestamp/path/seed cases, source-byte preservation.
- Evidence: `packages/core/src/polycodebench_core/identity.py`, `apps/contracts/src/canonical.ts`, `tests/test_core_contracts.py`, `apps/contracts/test/contracts.mjs`, shared fixtures.
- Acceptance criteria: — Implement IDs, UTC timestamps, monotonic-duration handling, validated relative paths and deterministic task/sample seed derivation. DoD: unsigned 64-bit seeds are stored/serialized without loss or signed overflow; source/provider bytes are not silently normalized.

## PCB-02-4 - Prompt 02: — Implement canonical contracts and schemas

- Owner prompt: `02`.
- Dependencies: Prompt 01 ·.
- Implementation: `implemented` (13 standalone JSON Schemas, OpenAPI 3.1 components and generated shared TypeScript declarations; CI checks generated drift).
- Verification: `passed` (generation/check and both runtime package build/type checks).
- Required verification scope: generated outputs match authoritative models; consumers import generated contract declarations.
- Evidence: `scripts/export_contract_schemas.py`, `schemas/contracts/`, `apps/contracts/src/generated.ts`, `apps/contracts/src/consumer-contracts.ts`, `.github/workflows/ci.yml`.
- Acceptance criteria: — Generate committed JSON Schemas and initial OpenAPI/shared-client contracts from authoritative models. DoD: schema drift is detectable in CI and consumers use the generated contracts rather than handwritten conflicting types.

## PCB-03-1 - Prompt 03: — Build persistence, identity and idempotency

- Owner prompt: `03`.
- Dependencies: Prompt 02 ·.
- Implementation: `implemented`.
- Verification: `passed` for PostgreSQL 18.4 empty-database migration, cyclic artifact/execution FK, second-upgrade idempotence, grants, and Alembic model/schema drift check; PG17.6 run is configured in CI but hosted CI has not run in this session.
- Required verification scope: E2E-02, E2E-25 foundational subcases.
- Evidence: `packages/persistence/src/polycodebench_persistence/models.py`; packaged Alembic revision and SQL role scripts; `tests/test_persistence_postgres.py`; local command/evidence in `docs/implementation/commands.md` and `docs/implementation/reports/prompt-03.md`.
- Acceptance criteria: — Add schema/migrations for tasks/configs, runs/attempts/evaluations, jobs/dependencies/executions, workers/capacity slots, artifact references, evidence/review, scorecards/releases, endpoint requests, ledgers and audit/idempotency records. DoD: empty-database creation and upgrade paths work; cyclic foreign-key creation order is handled correctly; constraints match semantics.

## PCB-03-2 - Prompt 03: — Build persistence, identity and idempotency

- Owner prompt: `03`.
- Dependencies: Prompt 02 ·.
- Implementation: `implemented`.
- Verification: `passed` for atomic run/attempt inserts, max uint64 seed persistence, immutable task-version/audit rejection, and independently stored attempt/evaluation schema; no evaluation workflow exists yet.
- Required verification scope: E2E-02, E2E-25 foundational subcases.
- Evidence: `packages/persistence/src/polycodebench_persistence/{runs.py,models.py}` and `tests/test_persistence_postgres.py`; published-release immutability is enforced by database triggers but full publication workflow is future scope.
- Acceptance criteria: — Implement service/repository transactions and immutable-record protections. DoD: attempt/evaluation separation is real, no published evidence is overwritten, and foreign keys/unique constraints enforce identities and retry boundaries.

## PCB-03-3 - Prompt 03: — Build persistence, identity and idempotency

- Owner prompt: `03`.
- Dependencies: Prompt 02 ·.
- Implementation: `implemented` for RBAC/identity foundations, non-login role groups, row-version CAS, append-only audit storage and scoped DB grants/RLS.
- Verification: `passed` for service authorization, submitter RLS own-row visibility/cross-subject insert denial, base-table denial, role assignment audit, and stale role-version rejection. No API routes or separately managed deployment login credentials exist yet; deployment must attach workload logins and supply secrets externally.
- Required verification scope: E2E-02, E2E-25 foundational subcases.
- Evidence: `packages/services/src/polycodebench_services/{identity.py,rbac.py}`; `packages/persistence/src/polycodebench_persistence/identities.py`; `packages/persistence/sql/{provision_roles.sql,grant_permissions.sql}`; PostgreSQL test evidence in `tests/test_persistence_postgres.py`.
- Acceptance criteria: — Implement administrative identity/RBAC foundations, optimistic version checks and append-only audit events. DoD: permissions are enforced in services/API boundaries, with role-specific database credentials and no secret values in records/logs.

## PCB-03-4 - Prompt 03: — Build persistence, identity and idempotency

- Owner prompt: `03`.
- Dependencies: Prompt 02 ·.
- Implementation: `implemented`.
- Verification: `passed` for simultaneous same-key requests returning one run and attempt set, changed-payload key conflict, and injected attempt-write failure rolling back run, attempts, idempotency claim and audit event.
- Required verification scope: E2E-02, E2E-25 foundational subcases.
- Evidence: `packages/services/src/polycodebench_services/runs.py`, `packages/persistence/src/polycodebench_persistence/runs.py`, and PostgreSQL integration evidence in `tests/test_persistence_postgres.py`.
- Acceptance criteria: — Implement request idempotency and transaction-safe run/attempt creation service behavior. DoD: same key and request replay one result; changed request conflicts; creation cannot leave partial attempt sets. This foundational service may be integration-tested before the scheduler exists.

## PCB-04-1 - Prompt 04: — Implement artifact storage and visibility

- Owner prompt: `04`.
- Dependencies: Prompt 03 ·.
- Implementation: `implemented (S3 provisional write, expected byte/SHA validation, independent canonical readback, conditional immutable registration, retry-safe finalizer)`.
- Verification: `passed (PostgreSQL 17.6 + SeaweedFS 4.48; changed-size, wrong-digest and truncated bytes rejected; verified retry and duplicate identity checks)`.
- Required verification scope: E2E-03, E2E-26 storage subcases.
- Evidence: packages/persistence/src/polycodebench_persistence/{object_store.py,artifacts.py,migrations/versions/a4f04c4f5a12_artifact_integrity_and_visibility.py}, tests/test_artifacts_postgres.py, docs/implementation/reports/prompt-04.md.
- Acceptance criteria: — Implement provisional upload, expected size/hash validation, independent finalization and immutable verified artifact registration. DoD: an object-store ETag is never substituted for SHA-256; altered or truncated bytes cannot become verified evidence.

## PCB-04-2 - Prompt 04: — Implement artifact storage and visibility

- Owner prompt: `04`.
- Dependencies: Prompt 03 ·.
- Implementation: `implemented (visibility-selected buckets, role-scoped upload/read service and solve DB role without artifact enumeration grant)`.
- Verification: `passed for local storage variants: hidden/public UUID probes, hidden/public same-byte separation, solve-role SELECT denial and upload-role denial. Production IAM/bucket policy remains unvalidated separately`.
- Required verification scope: E2E-03, E2E-26 storage subcases.
- Evidence: packages/services/src/polycodebench_services/artifacts.py, packages/persistence/sql/grant_permissions.sql, tests/test_artifacts_postgres.py, E2E-26 scope in e2e-matrix.md.
- Acceptance criteria: — Enforce hidden/internal/public visibility domains and role-scoped reads/writes. DoD: digest knowledge does not authorize access, private/public deduplication does not leak data, and a solve identity cannot list/read hidden assets.

## PCB-04-3 - Prompt 04: — Implement artifact storage and visibility

- Owner prompt: `04`.
- Dependencies: Prompt 03 ·.
- Implementation: `implemented (same-domain digest uniqueness, atomic byte reservations, immutable verified manifest edges, domain/cycle guards and upload replay recovery)`.
- Verification: `passed for quota refusal, within-domain duplicate retry, cross-visibility separation, interrupted provisional upload recovery and cycle/scope rejection`.
- Required verification scope: E2E-03, E2E-26 storage subcases.
- Evidence: packages/persistence/src/polycodebench_persistence/{models.py,artifacts.py,migrations/versions/a4f04c4f5a12_artifact_integrity_and_visibility.py}, tests/test_artifacts_postgres.py.
- Acceptance criteria: — Implement manifest edges, safe artifact references, byte quotas and retry-safe deduplication. DoD: authoritative database references are committed only after verification; upload/commit interruption does not create contradictory identities.

## PCB-04-4 - Prompt 04: — Implement artifact storage and visibility

- Owner prompt: `04`.
- Dependencies: Prompt 03 ·.
- Implementation: `implemented (expired provisional and unreferenced canonical-object cleanup with in-flight finalization protection, audited retention holds, strict public metadata projection from separate object, inert attachment response)`.
- Verification: `passed for quota release at upload expiry, provisional-byte retention through day 30 and cleanup after the retention interval; verified/held/published objects retained; distinct reviewer/publisher requirement, projection retry and unchanged private source key/visibility. Controlled public routes pass locally; production policy validation remains pending`.
- Required verification scope: E2E-03, E2E-26 storage subcases.
- Evidence: packages/persistence/src/polycodebench_persistence/artifacts.py, packages/services/src/polycodebench_services/{artifacts.py,artifact_publication.py}, tests/test_artifacts_postgres.py, packages/persistence/README.md.
- Acceptance criteria: — Add provisional garbage collection and allowlisted public projection/export support. DoD: referenced/published/held artifacts are retained; publication makes reviewed projection objects without changing a private object's ACL in place; downloads are inert and authorized.

Prompt 04 review follow-up (2026-09-30): upload finalization now locks the upload row before quota release; expired finalization retains staged bytes. Reviewer approval is persisted against the exact canonical projection digest in migration `b5e17f2c4096`; migration `c6a90d17f20e` requires that approval for declassification. Public artifact registration and declassification commit together, and public reads require the approval link. Six local artifact integration tests and the full 24-test suite passed; production policy and full public-route E2E remain pending.

## PCB-05-1 - Prompt 05: — Build task admission and freeze the methodology contracts

- Owner prompt: `05`.
- Dependencies: Prompt 04 ·.
- Implementation: `implemented` (`packages/services/src/polycodebench_services/task_packages.py`, `task_fixture_runner.py`; `packages/persistence/src/polycodebench_persistence/tasks.py`; `packages/services/src/polycodebench_services/tasks.py`; `scripts/pcb.py`; authored package `taskpacks/admission-smoke/`). Visible and hidden archives are independently allowlisted; task versions bind verified artifact IDs/digests and are frozen on registration.
- Verification: `passed` for importer/path/privacy and actual authored-fixture execution; PostgreSQL registration/freeze passed. Production task images and language workers remain later scope.
- Required verification scope: E2E-04, E2E-27.
- Evidence: `tests/test_task_packages.py`; `tests/test_task_admission_postgres.py` (seven PostgreSQL 17.6 checks); `tests/test_task_fixture_runner.py` (seven runner/CLI checks); `docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json` (actual Docker execution bound to the complete snapshot; report digest `sha256:2c3e44a86daad9c6e37e17c63831e2622f514b9a64cc7484fcc760fb2a4cbe64`). Final full suite: 56 passed with PostgreSQL, SeaweedFS and Docker enabled. Reference visibility, undeclared files, artifact-ID contradictions and stale snapshot evidence regressions passed.
- Acceptance criteria: — Implement task-package import and separate visible/hidden bundle construction, safe repository snapshot handling, rights/provenance metadata, output contracts and immutable task-version registration. DoD: hidden data/references cannot appear in model-visible exports or visible image build contexts.

## PCB-05-2 - Prompt 05: — Build task admission and freeze the methodology contracts

- Owner prompt: `05`.
- Dependencies: Prompt 04 ·.
- Implementation: `implemented` (`packages/core/src/polycodebench_core/tasksets.py`; TaskSet/ModelCutoffProvenance contracts; PostgreSQL cluster/split registry and task-set freeze in `packages/persistence/src/polycodebench_persistence/tasks.py`). Exposure dates are sourced from immutable task data; curation timestamps do not substitute for first-public dates.
- Verification: `passed` for local date/cutoff and cluster leakage subcases plus PostgreSQL fixture task-set freeze. Altered draft split, document and manifest artifact are rejected before membership writes; freeze rehashes the document and verifies row identity and manifest integrity.
- Required verification scope: E2E-04, E2E-27.
- Evidence: `tests/test_task_packages.py`; `tests/test_task_admission_postgres.py`; schema outputs `schemas/contracts/{TaskSet,TaskSetMember,ModelCutoffProvenance}.v1.schema.json`. Public filters/common-cohort integration remains E2E-27 pending under Prompts 16/29.
- Acceptance criteria: — Implement task-set freeze, cluster/split validation, earliest-exposure dates, model-cutoff provenance and family/stratum membership. DoD: related variants cannot leak across designated splits; curation date cannot turn an old problem into a post-cutoff problem.

## PCB-05-3 - Prompt 05: — Build task admission and freeze the methodology contracts

- Owner prompt: `05`.
- Dependencies: Prompt 04 ·.
- Implementation: `implemented` (`scripts/pcb.py` import/validate/freeze/taskset CLI and shared service/repository interface; local Docker slice uses immutable Python image, no network, readonly root, nonroot, CPU/memory/PID/time limits, and no hidden mount). REST routes and production VM driver remain later scope.
- Verification: `passed` for CLI schema validation and actual reference/faulty/alternative runs; fixture evidence records `local_fixture`. CLI freeze replays the imported bytes and rejects forged reports. All non-fixture sets remain blocked until trusted production admission exists; a caller-supplied `production_worker` label is rejected. Docker timeout cleanup passed with no container left running.
- Required verification scope: E2E-04, E2E-27.
- Evidence: `docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json`; `tests/test_task_admission_postgres.py`; `tests/test_task_fixture_runner.py`; E2E-04 authored-fixture subcase passed, full E2E remains pending Prompts 10–12/17. No production task admission is claimed.
- Acceptance criteria: — Implement admission orchestration/interfaces and the validation/freezing CLI/API. If needed, add the smallest compliant local sandbox invocation for trusted authored admission fixtures, recording it as a prerequisite slice of WP-06 for Prompt 06 to extend. Actually execute reference/faulty/alternative fixture checks; do not simulate their outcomes. DoD: unvalidated tasks cannot enter a scored task set, execution tier is recorded, and production task admission remains subject to production-worker validation.

## PCB-05-4 - Prompt 05: — Build task admission and freeze the methodology contracts

- Owner prompt: `05`.
- Dependencies: Prompt 04 ·.
- Implementation: `implemented` (versioned pilot contracts in `config/scoring/`, `config/languages/`, `config/methodology/`, `config/budgets/`, `config/evidence/`, `config/task-admission/`; methodology/source-terms records in `docs/methodology/`). Project owner approved the specified v1 weights as the frozen pilot baseline on 2026-09-30; scoring remains inactive pending human/judge calibration and other release gates. All four methodology families are registered.
- Verification: `passed` via `scripts/validate_task_contracts.py`, methodology/source review and generated-schema check. Rights and private dataset restrictions remain recorded as unresolved inputs.
- Required verification scope: E2E-04, E2E-27.
- Evidence: `scripts/validate_task_contracts.py`; `docs/methodology/source-terms-register.md`; four methodology records; 21 generated schema/OpenAPI/shared TypeScript files checked with `scripts/export_contract_schemas.py --check`.
- Acceptance criteria: — Finalize versioned pilot contracts for scoring weights, applicability/owners, language profiles, method deviations, budgets and evidence schemas from the sources. DoD: proposed calibration parameters remain labeled pilot parameters; substantive uncertainty/rights restrictions are recorded; no omitted benchmark family.

## PCB-06-1 - Prompt 06: — Implement sandbox drivers and isolation

- Owner prompt: `06`.
- Dependencies: artifact/task contracts ·.
- Implementation: `implemented` in `packages/runner/src/polycodebench_runner/{contracts.py,provider.py}`; typed create/stage/execute/snapshot/terminate/destroy operations, scoped handles, fixed argv boundaries, bounded subprocess handling and operation logs.
- Verification: `blocked` for required production-driver acceptance; provider lifecycle/unit coverage and live local Docker containment passed. Production VM/E2E-05/06 evidence awaits an authorized deployment target.
- Required verification scope: E2E-05, E2E-06.
- Evidence: `tests/test_sandbox.py`; live development-tier evidence `docs/implementation/evidence/prompt-06-local-docker.json`; production gate remains pending.
- Acceptance criteria: — Implement SandboxProvider and typed create/stage/execute/snapshot/terminate/destroy operations. DoD: lifecycle actions are scoped, idempotent where specified, logged and timeout-bounded; candidate arguments never become host-shell interpolation.

## PCB-06-2 - Prompt 06: — Implement sandbox drivers and isolation

- Owner prompt: `06`.
- Dependencies: artifact/task contracts ·.
- Implementation: `implemented` in `packages/runner/src/polycodebench_runner/provider.py`, `infra/sandbox/guest/pcb-guest-control.py`, and `config/sandbox-policies/development.v1.json`; limits, safe path handling, non-root identity, no network/socket/host namespaces, bounded resources and development-only tier enforced.
- Verification: `blocked` for full required production-driver evidence; local adversarial containment passed. See `docs/implementation/evidence/prompt-06-local-docker.json`.
- Required verification scope: E2E-05, E2E-06.
- Evidence: `tests/test_sandbox.py` and opt-in real Docker run; Terraform/AWS plan and production isolation remain unverified because the cloud target/authorization and Terraform CLI are absent.
- Acceptance criteria: — Complete LocalDockerSandboxProvider and guest resource/path/process policy. DoD: limits, safe extraction, no exposed Docker socket/host namespaces/secrets, and development-only result identity are enforced externally to candidate code.

## PCB-06-3 - Prompt 06: — Implement sandbox drivers and isolation

- Owner prompt: `06`.
- Dependencies: artifact/task contracts ·.
- Implementation: `implemented` in `packages/runner/src/polycodebench_runner/provider.py`, `infra/sandbox/aws/`, `infra/sandbox/guest/`, and `config/sandbox-policies/`; principal verification, metadata/public-IP denial, distinct lanes, no egress, stage capability and production attestation are encoded.
- Verification: `blocked` pending `terraform validate/plan` and live VM evidence. No AWS target, approved AMI, supervisor identity or explicit budget was available or provisioned.
- Required verification scope: E2E-05, E2E-06.
- Evidence: fake-control/identity/attestation tests in `tests/test_sandbox.py`; local Docker evidence does not count as EC2 production evidence. Required E2E-05/06 production variants remain unrun.
- Acceptance criteria: — Implement Ec2VmSandboxProvider and deployment configuration for disposable guests. DoD: supervisor credentials remain outside guests, no instance role/metadata access, restricted control channel, offline candidate container, stage-scoped transfer capabilities and distinct solve/grading lanes.

## PCB-06-4 - Prompt 06: — Implement sandbox drivers and isolation

- Owner prompt: `06`.
- Dependencies: artifact/task contracts ·.
- Implementation: `implemented` in `packages/runner/src/polycodebench_runner/provider.py` with expired-resource collection, destroy confirmation, snapshot/checkpoint manifest and tier-bound attestation. Production attestation cannot be requested for a development handle.
- Verification: `blocked` for production destruction verification and isolation attestation; local TTL/orphan cleanup, cancellation and destroy verification passed.
- Required verification scope: E2E-05, E2E-06.
- Evidence: `tests/test_sandbox.py`, `docs/implementation/evidence/prompt-06-local-docker.json`; live production cleanup remains blocked on authorized EC2 deployment.
- Acceptance criteria: — Implement TTL/orphan cleanup, destruction verification, checkpoint collection and isolation attestation. DoD: guest/resource cleanup is confirmed before capacity reuse; a developer cannot request a production isolation badge.

## PCB-07-1 - Prompt 07: — Implement durable jobs and recovery

- Owner prompt: `07`.
- Dependencies: persistence/artifacts/sandbox ·.
- Implementation: `implemented` in `packages/core/src/polycodebench_core/jobs.py` and `packages/persistence/src/polycodebench_persistence/jobs.py`; DAG creation is atomic and replay-safe, cycles/references are checked, only named scheduler-authored skips satisfy matching branches, and gate-pass/gate-fail jobs diverge without treating a wrong answer as infrastructure failure.
- Verification: `passed` for the tested PostgreSQL gate and skip variants; full E2E-09 remains partial for model usage evidence.
- Required verification scope: E2E-07–09.
- Evidence: `tests/test_jobs_postgres.py::test_job_dag_uses_gate_conditions_and_only_accepts_named_scheduler_skips`; E2E-07/08/09 evidence `docs/implementation/evidence/prompt-07-integration.json`.
- Review fixes: terminal failures propagate; unused branches skip explicitly; parallel completions unblock joins. Independently validated by `tests/test_scheduler_regressions.py` and the isolated full suite (124 passed, no skips).
- Acceptance criteria: — Implement the stage DAG, branch-specific skip semantics and transactional job creation/unblocking. DoD: unknown/failed prerequisites cannot be mistaken for successful quality inputs; a successfully executed wrong model answer is not an infrastructure retry.

## PCB-07-2 - Prompt 07: — Implement durable jobs and recovery

- Owner prompt: `07`.
- Dependencies: persistence/artifacts/sandbox ·.
- Implementation: `implemented` in `packages/persistence/src/polycodebench_persistence/jobs.py` and `packages/orchestration/src/polycodebench_orchestration/worker.py`; claims use short PostgreSQL transactions, slot-first `SKIP LOCKED`, database-timed 120-second leases, 30-second heartbeats, 1-second revocation polling, execution rows, fencing and same-worker/same-output replay.
- Verification: `passed` for competing claims, lease expiry, new-fence reclaim, stale commit denial and idempotent/different-output commit behavior on PostgreSQL.
- Required verification scope: E2E-07–09.
- Evidence: `tests/test_jobs_postgres.py::test_e2e07_competing_workers_fence_stale_delivery_and_idempotent_commit`; migration/drift evidence and exact command in `docs/implementation/evidence/prompt-07-integration.json`.
- Review fixes: complete outcome identity and event integrity, provisioning heartbeats, fail-closed supervision, cancellation task drainage and worker identity are covered by `tests/test_scheduler_regressions.py` and 30 cases in `tests/test_worker.py`.
- Acceptance criteria: — Implement short SQL claims, 120-second leases, 30-second heartbeats, fencing tokens and execution records according to the spec. DoD: stale workers cannot dispatch or commit authoritative results and duplicate completion is idempotent only for the same output.

## PCB-07-3 - Prompt 07: — Implement durable jobs and recovery

- Owner prompt: `07`.
- Dependencies: persistence/artifacts/sandbox ·.
- Implementation: `implemented` in `packages/persistence/src/polycodebench_persistence/jobs.py`; resource-compatible capacity slots, bounded campaign/provider concurrency, append-only delivery/event records, deterministic 10/60-second retry delays with bounded jitter, max-three deliveries, reaping and explicit guest-cleanup confirmation are persisted.
- Verification: `passed` for two-worker over-allocation prevention, campaign/provider caps, three-delivery exhaustion, slot drain/resume, expiry/reclaim and cleanup; only local Docker lifecycle tier was exercised.
- Required verification scope: E2E-07–09.
- Evidence: `tests/test_jobs_postgres.py::{test_e2e07_competing_workers_fence_stale_delivery_and_idempotent_commit,test_infrastructure_deliveries_are_visible_bounded_and_worker_can_resume,test_campaign_and_provider_fairness_cap_concurrent_leases,test_e2e09_cancelled_live_docker_tool_guest_is_destroyed}`; full workspace run and evidence path in `docs/implementation/evidence/prompt-07-integration.json`.
- Acceptance criteria: — Implement capacity-slot claims, matching resource classes, cleanup states, campaign/provider fairness and bounded retry/reaper rules. DoD: no over-allocation or premature reuse of a still-running guest; all deliveries and failure classes remain visible.

## PCB-07-4 - Prompt 07: — Implement durable jobs and recovery

- Owner prompt: `07`.
- Dependencies: persistence/artifacts/sandbox ·.
- Implementation: `implemented` in `packages/persistence/src/polycodebench_persistence/jobs.py`, `packages/orchestration/src/polycodebench_orchestration/worker.py`, and `packages/orchestration/src/polycodebench_orchestration/cli.py`; cancellation revokes before dispatch, preserves completed job/artifact records, terminates live guests, and worker drain/resume plus operator cancel/reaper entrypoints are available.
- Verification: `not_run` for the complete ticket gate; PostgreSQL/local-Docker cancellation proved no new dispatch, guest destruction, cleanup, completed-artifact preservation and no synthetic model usage. The required model-call usage-retention variant remains pending until Prompt 08/17 provides durable model calls/usage.
- Required verification scope: E2E-07–09.
- Evidence: `tests/test_jobs_postgres.py::test_e2e09_cancelled_live_docker_tool_guest_is_destroyed`; local development evidence in `docs/implementation/evidence/prompt-07-integration.json`. Full E2E-09 usage evidence is pending Prompt 08/17.
- Acceptance criteria: — Implement cancel/revoke/resume semantics and progress events. DoD: cancellation prevents new dispatch, terminates work, preserves completed evidence/usage and does not turn unrun tasks into model failures.

## PCB-08-1 - Prompt 08: — Implement model adapters and budget accounting

- Owner prompt: `08`.
- Dependencies: scheduler/persistence/artifacts ·.
- Implementation: `implemented` in `packages/core/src/polycodebench_core/model_contracts.py`, `model_planning.py` and `packages/orchestration/src/polycodebench_orchestration/gateway/adapters/` (OpenAI-compatible, Anthropic, Google, local behind `BaseAdapter`/`ModelAdapter`); capability validation covers tools, structured output, seed (policy and declared range), temperature, reasoning, context window and usage counters; unsupported controls reject unless a named cohort exception is recorded, and recorded drops are stored per call.
- Verification: `passed` for unit, provider-shaped fixture and local-socket checks plus a live local-adapter check. Not live-verified: OpenAI-compatible hosted, Anthropic and Google adapters (no credentials available); their wire behavior is verified only against documented-shape FIXTURES.
- Required verification scope: E2E-10–12.
- Evidence: `tests/test_model_gateway_units.py`, `tests/test_model_gateway_fixtures.py`, `tests/fixtures/model_gateway/*.json` (FIXTURE), `tests/test_model_gateway_review_regressions.py`, live local evidence `docs/implementation/evidence/prompt-08-live-smoke-local.json`; summary `docs/implementation/evidence/prompt-08-integration.json`.
- Acceptance criteria: — Implement OpenAI-compatible, Anthropic, Google and local endpoint adapters behind ModelAdapter. DoD: capability validation covers tools/schema/seeds/reasoning/context/usage; unsupported controls reject or create explicit cohort exceptions rather than silently disappearing.

## PCB-08-2 - Prompt 08: — Implement model adapters and budget accounting

- Owner prompt: `08`.
- Dependencies: scheduler/persistence/artifacts ·.
- Implementation: `implemented` in `packages/core/src/polycodebench_core/endpoint_policy.py`, `packages/persistence/src/polycodebench_persistence/endpoints.py`, `packages/services/src/polycodebench_services/model_endpoints.py`, `gateway/transport.py`, `gateway/secrets.py`, `gateway/endpoint_check.py` and `pcb-model`; pending/approved/rejected/revoked registrations with immutable identity, secret references only, HTTPS-allowlist or explicit internal-CIDR policy, per-connection DNS validation with address pinning, no redirects, conformance required for compatible/local approval.
- Verification: `passed` on PostgreSQL 17.6 and local loopback sockets: unreviewed, pending, revoked and unprovisioned-secret endpoints are never contacted; private, loopback, link-local/metadata, mapped, 6to4/NAT64 and rebinding answers are refused; local inference needs an internal registration.
- Required verification scope: E2E-10–12.
- Evidence: `tests/test_model_gateway_units.py` (policy, SSRF matrix, pinned transport), `tests/test_model_gateway_postgres.py::{test_endpoint_registration_approval_and_immutability,test_compatible_and_local_endpoints_need_passing_conformance,test_unapproved_or_revoked_endpoint_is_never_contacted,test_secret_value_reaches_only_the_wire_and_is_scrubbed_from_storage,test_missing_secret_provisioning_blocks_before_any_persistence}`; `docs/implementation/evidence/prompt-08-integration.json`.
- Acceptance criteria: — Implement endpoint registration/approval and secret references with network-policy validation. DoD: unreviewed public endpoints are not contacted, SSRF/private-address rules hold, and local inference endpoints require explicit internal registration.

## PCB-08-3 - Prompt 08: — Implement model adapters and budget accounting

- Owner prompt: `08`.
- Dependencies: scheduler/persistence/artifacts ·.
- Implementation: `implemented` in `packages/persistence/src/polycodebench_persistence/model_ledger.py`, `gateway/service.py`, `gateway/store.py` and migration `9d3a71c05e24`; unique call intents, per-delivery records, raw and normalized responses stored as verified artifacts before the controller is notified, provider request IDs/revisions preserved, usage and price snapshots recorded, recovery consumes stored responses and never re-samples.
- Verification: `passed` on PostgreSQL 17.6 and SeaweedFS 4.48 with fixture transport faults, including crash after raw-byte persistence, crash after settlement, definitive failure replay and changed-request conflict; live local adapter call persisted and settled.
- Required verification scope: E2E-10–12.
- Evidence: `tests/test_model_gateway_postgres.py::{test_e2e11_*,test_a_turn_is_consumed_once_even_when_a_retry_was_needed,test_late_response_for_an_ambiguous_delivery_is_evidence_not_a_second_result,test_request_and_reservation_rows_are_bound_to_one_intent_per_key}`; `docs/implementation/evidence/prompt-08-integration.json`.
- Acceptance criteria: — Implement call intents/deliveries, persisted responses and usage/pricing records. DoD: recovery consumes already-stored responses; provider request IDs/revisions and unknown usage are preserved; no response shopping after failures.

## PCB-08-4 - Prompt 08: — Implement model adapters and budget accounting

- Owner prompt: `08`.
- Dependencies: scheduler/persistence/artifacts ·.
- Implementation: `implemented` in `packages/persistence/src/polycodebench_persistence/model_ledger.py`, `packages/core/src/polycodebench_core/model_planning.py`, `gateway/throttle.py`, `gateway/plan.py` and `pcb-model`; hierarchical campaign/run/attempt reservations are atomic and root-first, turn/input/output limits are separate from money, ambiguous deliveries retain exposure, retries reserve additional cost, missing usage stays NULL, and no enforceable bound blocks a strict money cap.
- Verification: `passed` on PostgreSQL 17.6: concurrent last-allowance competition (asyncio and 16 parallel threads), token/turn limits, ambiguous timeout with retry, unknown/partial usage, append-only reconciliation including overrun, delivery cap, cancelled-scope refusal, plan/cost output and strict-cap blocking.
- Required verification scope: E2E-10–12.
- Evidence: `tests/test_model_gateway_postgres.py::{test_e2e10_*,test_e2e12_*,test_delivery_cap_bounds_retries_and_keeps_all_exposure,test_strict_cap_is_blocked_without_an_enforceable_bound,test_e2e09_usage_survives_cancellation_and_cancelled_scope_cannot_spend}`, `tests/test_model_gateway_units.py::test_cost_bound_*`; `docs/implementation/evidence/prompt-08-integration.json`.
- Acceptance criteria: — Implement atomic hierarchical cost reservations, token/turn limits, provider throttling and settlement/reconciliation. DoD: ambiguous requests retain exposure, retries reserve additional cost, missing usage is not zero, and unavailable enforceable cost bounds block strict-cap claims.

## PCB-09-1 - Prompt 09: — Implement single-shot and agent execution

- Owner prompt: `09`.
- Dependencies: task/sandbox/model gateway ·.
- Implementation: `implemented` in `packages/core/src/polycodebench_core/solve_contracts.py` (frozen `SolveProtocol`, per-task `EffectiveProtocol`), `solve_extraction.py`, `solve_prompts.py`, `config/protocols/{single-shot-v1,standard-agent-v1}.yaml`, `packages/services/src/polycodebench_services/solve_protocols.py` and `packages/orchestration/src/polycodebench_orchestration/solve/session.py`; protocol digest and effective-protocol digest are recorded in every session, checkpoint and candidate; gateway capability validation runs before the first turn; extraction is deterministic, model-free and marks ambiguity invalid for files, patch, text, typed JSON and findings contracts.
- Verification: `passed` (unit, fixture, real PostgreSQL + object store; agent parts in a real local Docker sandbox): 29 extraction fixtures incl. ambiguous fenced blocks, duplicate keys, over-limit findings; protocol/effective-protocol identity and narrowing; model is a FIXTURE throughout.
- Required verification scope: E2E-13, E2E-14.
- Evidence: `tests/test_solve_core.py`, `tests/fixtures/solve_extraction/cases.json` (FIXTURE), `tests/test_solve_sessions.py::{test_e2e13_*,test_single_shot_extraction_failures_are_model_failures_with_evidence_kept,test_a_task_can_narrow_the_tool_set_and_the_model_gets_no_extra_tools}`; `docs/implementation/evidence/prompt-09-integration.json`.
- Acceptance criteria: — Implement single-shot extraction and standard-agent protocol selection, capability validation and family output contracts. DoD: deterministic extraction never uses another LLM to select a better candidate, and protocol identity is part of the run.

## PCB-09-2 - Prompt 09: — Implement single-shot and agent execution

- Owner prompt: `09`.
- Dependencies: task/sandbox/model gateway ·.
- Implementation: `implemented` in `packages/runner/src/polycodebench_runner/guest_helper.py` (runs only inside the guest, shipped per call in the argument vector), `guest_tools.py`, `packages/orchestration/src/polycodebench_orchestration/solve/tools.py` and the tool schemas in `solve_contracts.py`; strict schemas, caps, protected and reserved paths, symlink refusal, all-or-nothing unified-diff engine, sequential provider-order execution, per-command process-tree cleanup (subreaper) and sweeps.
- Verification: `passed` in a real local Docker sandbox (development isolation): forbidden and protected paths, invalid schemas, unknown/unavailable tools, output truncation with archived raw output, timeouts, background descendants killed with no zombies, links removed before snapshots, pathological regex bounded, helper hijack attempts ineffective.
- Required verification scope: E2E-13, E2E-14.
- Evidence: `tests/test_solve_guest_helper.py` (29, host), `tests/test_solve_guest_docker.py` (11, Docker), `tests/test_solve_sessions.py::{test_tool_errors_*,test_outputs_are_truncated_*,test_a_command_that_edits_a_protected_file_*}`; `docs/implementation/evidence/prompt-09-integration.json`.
- Acceptance criteria: — Implement list_files, read_file, search, apply_patch, run_command and run_public_tests with schemas, caps, protected paths and sequential tool-call ordering. DoD: only allowed visible inputs/feedback reach the model; shell execution remains inside the guest; command descendants are cleaned up.

## PCB-09-3 - Prompt 09: — Implement single-shot and agent execution

- Owner prompt: `09`.
- Dependencies: task/sandbox/model gateway ·.
- Implementation: `implemented` in `packages/orchestration/src/polycodebench_orchestration/solve/session.py`, `packages/core/src/polycodebench_core/solve_context.py`, `packages/persistence/src/polycodebench_persistence/solve_state.py` and migration `b2f6d4a91c73`; events plus a checkpoint (workspace archive, transcript manifest, protocol, binding digest) commit in one transaction with a sequence compare-and-swap; the latest eight turns stay in full, older tool output becomes deterministic metadata summaries, over-ceiling truncation and compaction are recorded, and a context that cannot fit is a declared budget exhaustion.
- Verification: `passed` on PostgreSQL 17.6 and SeaweedFS 4.48: a workspace paired with a different transcript or protocol is refused on restore, a stale controller cannot fork the transcript, and rebuilt budgets must equal the checkpoint's; context policy invariants (determinism, call/result pairing, latest-turn retention) unit tested.
- Required verification scope: E2E-13, E2E-14.
- Evidence: `tests/test_solve_core.py::test_*context*`, `tests/test_solve_sessions.py::{test_restore_refuses_*,test_compare_and_swap_*,test_e2e14_*}`; `docs/implementation/evidence/prompt-09-integration.json`.
- Acceptance criteria: — Implement transcript events, bounded context policy, compaction/truncation records and atomic workspace/transcript checkpoints. DoD: restoring a checkpoint never pairs one workspace revision with a different conversation state.

## PCB-09-4 - Prompt 09: — Implement single-shot and agent execution

- Owner prompt: `09`.
- Dependencies: task/sandbox/model gateway ·.
- Implementation: `implemented` in `packages/orchestration/src/polycodebench_orchestration/solve/session.py`, `executor.py`, `loader.py`, `inspection.py`, `cli.py` (`pcb-solve`) and `solve_state.freeze_candidate`; budget exhaustion on turns, tool calls, tokens, active time, gateway spending and context freezes a valid workspace or records a model failure; candidates are immutable; hidden identity is enforced as a fail-closed request guard; stored model responses are consumed once; only a started, uncommitted command is replayed, and the repeat is recorded; infrastructure interruptions raise and never become outcomes.
- Verification: `passed` on PostgreSQL + object store + local Docker: E2E-14 guest killed mid-command then resumed in a fresh guest, response recorded-but-uncommitted consumed once (single-shot and agent), exhaustion variants, terminal idempotency, lease loss before any request, worker-executor mapping (model failure completes the stage with a failed gate).
- Required verification scope: E2E-13, E2E-14.
- Evidence: `tests/test_solve_sessions.py` (19), `tests/test_solve_loader.py` (4); `docs/implementation/evidence/prompt-09-integration.json`.
- Acceptance criteria: — Implement stopping, budget exhaustion, candidate freezing and infrastructure recovery. DoD: candidate bytes are immutable, no hidden feedback reaches solving, stored provider responses are consumed once, and only an uncommitted command can be replayed under the documented policy.

## PCB-10-1 - Prompt 10: — Implement Python support

- Owner prompt: `10`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `infra/images/python/{Dockerfile,runtime.in,runtime.lock,evaluator.in,evaluator.lock}`, `scripts/build_python_images.py`, `plugins/languages/python/src/polycodebench_lang_python/identities.py`, `config/images/python-v1.json`, `config/plugins/allowlist-v1.yaml`.
- Verification: `passed` (development/local Docker tier).
- Required verification scope: E2E-04, E2E-15, relevant E2E-16.
- Evidence: `docker build --network none` for both images; the build script compares the installed set to the lock and treats drift as a hard failure. Runtime `sha256:beb3dd62e10b…`, evaluator `sha256:dd801029a063…`, base `python@sha256:44ff437bba87…` (Python 3.12.14); pytest 9.0.2, Hypothesis 6.150.2, Ruff 0.16.8, mypy 1.19.1, Bandit 1.9.2, Semgrep 1.150.0; runtime lock 8 packages, evaluator lock 80. Every package comes from the hash-verified local wheelhouse via `pip --no-index --require-hashes`; plans declare `network: none` and contain no installer, and the guest cannot reach an index. `config/images/python-v1.json` records base/image/guest/rule/lock digests, and every `ToolIdentity` is derived from it. The E2E-16 `missing_dependency` case proves an unavailable third-party module fails the candidate at import with no online install.
- Pending (not this ticket): registry publication and production-worker pinning are deployment inputs; production isolation is the owner-deferred Prompt 06 gate.
- Acceptance criteria: — Build pinned offline Python runtime/evaluator images and dependency recipes. DoD: compiler/runtime/tool/lock/rule identities are recorded and scored runs perform no online installation.

## PCB-10-2 - Prompt 10: — Implement Python support

- Owner prompt: `10`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `plugins/languages/python/src/polycodebench_lang_python/{plans,parsers,testparse,symbols,observations,profile}.py`, guest tools `pcb_syntax_check.py` / `pcb_pytest_report.py` / `pcb_context_scan.py` / `pcb_perf_driver.py` / `pcb_capture.py` / `pcb_dependency_check.py`, rule bundle `rules/{ruff.toml,pytest.ini,mypy.ini,mypy-strict.ini,bandit.yaml,semgrep-rules.yml}`, `packages/plugins-api/src/polycodebench_plugins_api/testreport.py`, `packages/evaluation/src/polycodebench_evaluation/plan_runner.py`.
- Verification: `passed`.
- Required verification scope: E2E-04, E2E-15, relevant E2E-16.
- Evidence: `docs/implementation/evidence/prompt-10-conformance.json` (14/14 cases, `passed: true`), `tests/test_python_parsers.py`, `tests/test_python_guest.py`, `tests/test_plan_runner.py`, `tests/test_python_conformance_docker.py`. Findings exits parse as findings (ruff exit 1 → 3 findings, bandit exit 1 → 2 findings). A required scan that crashes, is killed, is absent (exit 127), has a bad config (exit 2) or times out yields one `python.<tool>.scan` observation with status `missing` and zero findings; profile items fed by that scan become `missing` and no aggregate is invented. Hidden tests arrive only as `overlay` inputs; missing, skipped or xfailed required cases are never passes.
- Acceptance criteria: — Implement Python build/test/symbol/analyzer/performance plans using pytest, Hypothesis, Ruff, mypy, Bandit and the approved applicable checks. DoD: nonzero finding exits are parsed correctly; missing or crashing required checks cannot look clean.

## PCB-10-3 - Prompt 10: — Implement Python support

- Owner prompt: `10`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `config/languages/python-profile-v1.yaml`, `plugins/languages/python/src/polycodebench_lang_python/{profile.py,observations.py}`, AST context scanner `guest/pcb_context_scan.py`.
- Verification: `passed`.
- Required verification scope: E2E-04, E2E-15, relevant E2E-16.
- Evidence: `tests/test_python_guest.py`, `tests/test_python_parsers.py`, and the `profile_applicability` / `anti_pattern` conformance cases. A mutable default is a violation only when it is mutated or escapes; a defensive rebind or read-only use is recorded `benign_in_context` and never penalised, and Ruff's B006 plus the scanner merge into one canonical issue with the scanner deciding. Missing annotations count only when the task contract expects types (`required: 2 violations; none: not_applicable`). Scores derive from the task's frozen opportunity count, never from constructs found in the candidate, so there is no syntax-count bonus; violations are unique canonical issue keys capped at that count, so one issue reported by several tools is merged before counting. D-10-03 records the two idiom detectors widened after the authored fixtures proved them too narrow; all 12 pilot references were re-scanned and remain clean.
- Acceptance criteria: — Implement the specified diagnostic and orthogonal idiom profiles with applicability/ownership mappings and concrete anti-pattern fixtures. DoD: mutable defaults or type expectations are evaluated in context; no syntax-count bonus or duplicate penalty.

## PCB-10-4 - Prompt 10: — Implement Python support

- Owner prompt: `10`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented` (executable admission). `packages/evaluation/src/polycodebench_evaluation/suite_admission.py`, `packages/plugins-api/src/polycodebench_plugins_api/admission.py`, `scripts/{python_task_tool.py,python_admit_all.py,python_pilot_inventory.py,record_python_tool_fixtures.py}`, public fixture `plugins/languages/python/fixtures/top-words/`, 12 authored pilot clusters under `.protected/taskpacks/python-pilot/`, committed inventory `taskpacks/python-pilot/inventory.yaml`.
- Verification: `passed` for executable admission on all 12 clusters; **quality admission remains `pending`** (gates listed below).
- Required verification scope: E2E-04, E2E-15, relevant E2E-16.
- Evidence: `.protected/reports/*.json` — 12/12 packages pass, 21–24 checks each, every variant executed in the pinned images. Each reference passes its full declared inventory across 5 identical repetitions; every declared faulty variant fails exactly its declared cases; the alternative-valid variant passes; the quality-defective variant passes the functional gate while analyzers report its intended families; timeout variants are stopped and recorded as candidate failures (not harness errors); required analyzers complete on the reference; the performance workload smoke run executes. Exposure/rights records exist for all 12 clusters, and the committed inventory carries identities and digests only — hidden bundles, references, variants and tests remain in git-ignored `.protected/`.
- Pending (deliberately not claimed): generic evaluator stage integration (Prompt 12), performance baseline/canary and paired measurement (13), judge anchors and human calibration (14), deterministic scoring replay (15), production execution tier (Prompt 06, owner-deferred), curator approval and task freeze, owner rights confirmation, hidden-lane object-store registration at freeze. Every report records `quality_admission: pending` and the inventory records `fully_admitted: 0`, `frozen: 0`; no pilot task is frozen.
- Acceptance criteria: — Create independently authored Python admission fixtures and a pilot task inventory covering valid, wrong, alternative-valid, quality-defective and timeout cases. DoD: real reference/faulty/alternative executions validate the task oracles; admission artifacts and exposure/rights records exist.

## PCB-11-1 - Prompt 11: — Implement Rust support

- Owner prompt: `11`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `infra/images/rust/{Dockerfile,recipes.yaml}`, `scripts/fetch_rust_components.py`, `scripts/build_rust_images.py`, `plugins/languages/rust/src/polycodebench_lang_rust/{identities.py,locks.py}`, `config/images/rust-v1.json`, `config/images/rust-components.json`, allowlist entry in `config/plugins/allowlist-v1.yaml`.
- Verification: `passed` (development/local Docker tier).
- Required verification scope: E2E-04, E2E-15, E2E-16.
- Evidence: `tests/test_rust_locks.py` (8 tests), the recorded identity file, and the images themselves. Base `rust@sha256:540c902e99c3…` (rustc/cargo 1.83.0). Three genuinely distinct recipes built with `--network none`: **runtime** `sha256:3ed8de65c130…` (rustc+cargo only), **evaluator** `sha256:b32f71363ddd…` (+ clippy 0.1.83, rustfmt 1.8.0, Miri 0.1.0 on pinned nightly-2026-09-30), **performance** `sha256:18290037bf91…` (baked release profile: opt-level 3, codegen-units 1, debug-assertions/overflow-checks off, panic=abort). Each recipe is probed for all five tools and records `absent` for tools it does not ship, so the record states the difference rather than omitting it.
- Distinctness is enforced, not asserted: `require_distinct()` fails the build unless the analyzers actually run in the evaluator image, are actually absent from the other two, and the three digests differ. The gate caught a real defect — an earlier build produced three differently-tagged images with identical contents.
- Offline crates: `rustup` deletes a component payload once unpacked, so there is nothing file-level to vendor and the wheelhouse analogue is a prebuilt components image (the only step that uses a network). Miri's sysroot additionally resolves real crates from crates.io, so 1262 crate files are vendored and `CARGO_HOME` source replacement is baked in, making a scored run fully offline.
- `Cargo.lock` identity (DoD): `locks.py` parses the lock, canonicalises the *resolution* (name/version/checksum, sorted) and rejects a lock that does not actually pin it — a registry package without cargo's checksum raises `LockError` rather than yielding an identity that could drift between runs. The digest flows into `ToolIdentity.lock_digest`, so image digest + toolchain + lock together determine the identity.
- Pending (not this ticket): the plugin entry point `polycodebench_lang_rust.plugin:RustLanguagePlugin` is a forward reference until PCB-11-2; plans, parsers, profile and fixtures are PCB-11-2/3/4.
- Rebuilt in PCB-11-2 (digests changed; see D-11-10..D-11-12): runtime `sha256:a3f88da16577…`, evaluator `sha256:5aa0fac65e0b…`, performance `sha256:de4293082733…`. The digests quoted above are the first build.
- Acceptance criteria: — Build frozen Rust/Cargo toolchains, offline crates and distinct regular/instrumented/performance recipes. DoD: toolchain/Cargo.lock and image digests determine evaluator identity.

## PCB-11-2 - Prompt 11: — Implement Rust support

- Owner prompt: `11`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `plugins/languages/rust/src/polycodebench_lang_rust/{plugin,plans,parsers,testparse,symbols,taskspec,guestmods}.py`, guest scripts `guest/{pcb_rust_run,pcb_lock_audit}.py` (+ libtest qualification in `pcb_rust_test_report.py`), `rules/advisories/snapshot.json`, fixture task `plugins/languages/rust/fixtures/top-words/`, `scripts/{rust_task_tool,record_rust_tool_fixtures}.py`. Shared-contract change: `executable_workspace` on `SandboxSpec`/`ResourcePolicy` (default `False`) honoured by the Docker driver and `PlanRunner`. Images rebuilt (Dockerfile + `scripts/build_rust_images.py`).
- Verification: `passed` (development/local Docker tier; opt-in live tests need `PCB_TEST_DOCKER=1`).
- Required verification scope: E2E-04, E2E-15, E2E-16.
- Evidence: `tests/test_rust_plugin.py` (20, offline plans/validation/symbols/inputs), `tests/test_rust_parsers.py` (25, replaying **real** recordings of 10 scenarios under `tests/fixtures/rust_tool_output/`), `tests/test_rust_guest.py` (12), `tests/test_rust_profile.py` (22), `tests/test_rust_docker.py` (3 live: reference builds, passes both groups and is clean under clippy/context/Miri; the faulty candidate fails exactly `ties_break_alphabetically`; the hung candidate is a `candidate_timeout` naming the in-flight case; a non-executable workspace never yields a pass). Prompt 10 plan-runner/plugin/suite-admission tests still pass (65).
- Plans: build (`cargo build --message-format=json`), one test plan per oracle group (`cargo test --test <file> -- --test-threads=1`, hidden tests as digest-less `overlay`, crate scaffold as digest-checked `config`, candidate files as `candidate`), analysis plans for Clippy (selected lints from `rules/clippy.toml`, token lints deliberately off), the context scanner, Miri (only when the task's `miri` is `required`/`optional`; pinned nightly; baked sysroot) and a dependency audit (only when the task declares an inventory), plus a performance plan when a workload is declared. Every plan is a typed argv through `pcb_rust_run.py`, which enforces the deadline inside the guest, keeps partial output and deletes `target/`.
- Test evidence: libtest output is parsed from the merged cargo stream (case ids are qualified by the test binary, `behaviour::ordering::counts_never_increase_down_the_list`); a `test x ... ` line with no outcome names the case in flight at a timeout or a fatal signal; compile errors are split into candidate (candidate paths or API-mismatch codes) and harness (confined to hidden test files); a missing runner record, summary or case is a harness failure/incomplete, never a pass.
- Miri DoD: **clean** = measured scan with 0 findings and a libtest `test result` line; **candidate UB** = measured scan + `rust.miri.candidate-ub` finding located in the candidate; **unsupported** = scan `not_applicable` (real transcript: `can't call foreign function getppid`). Only a task that *requires* Miri has dependent items marked `missing` by an unsupported/missing scan; an `optional` or `unsupported` task is scored on the remaining evidence. Exit status is never the verdict.
- Defects found and fixed on the way: (1) the Rust images contained **no Python**, but the sandbox provider drives every guest with `python -I -B -S` (keep-alive, staging, snapshot) — no Rust plan could have run; (2) the workspace tmpfs is `noexec`, so compiled test binaries could not execute; (3) Miri rebuilt its sysroot at run time in a read-only/noexec sandbox; (4) a Miri run with exit 0 and unparsable output was read as clean; (5) PCB-11-1's `slug()` turned dots into hyphens (fixed under PCB-11-3). See D-11-10..D-11-15.
- Pending (not this ticket): admission fixtures and the 12-cluster pilot inventory (PCB-11-4), including making `SuiteAdmission` language-neutral (it still assumes `.py` candidates and `python.<tool>.scan` names); the dependency audit fails closed because **no advisory database is vendored** (building one needs a network fetch, an owner action); performance parsing belongs to Prompt 13; Clippy's `rules/clippy.toml` names two lints this Clippy does not know (`suspicious_map_or_else`, `manual_is_multiple_of`; they only produce ignored rustc warnings, and editing the rules would change the recorded rule-bundle digest).
- Acceptance criteria: — Implement build/test/symbol/plans, selected clippy rules, dependency audit and compatible Miri handling. DoD: Miri unsupported operations are distinct from a clean scan and from candidate UB; only applicable supported tasks require it.

## PCB-11-3 - Prompt 11: — Implement Rust support

- Owner prompt: `11`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `config/languages/rust-profile-v1.yaml` (rule mappings, applicability rules, ownership), `plugins/languages/rust/src/polycodebench_lang_rust/profile.py` (`RustProfile`: load, `key_for`, `normalize`, `evaluate`), exported from the package; `observations.py` fixed (see below).
- Verification: `passed` (unit tests against the real context scanner; `effective_for_scoring: false`, pilot parameters unvalidated).
- Required verification scope: E2E-04, E2E-15, E2E-16.
- Evidence: `tests/test_rust_profile.py` (22 tests; with `test_rust_guest.py`/`test_rust_locks.py`, 38 pass). Weights are read from `profiles-v1.yaml` (diagnostic: ownership_borrowing 25, unsafe_soundness 20, result_option 20, iterators_traits 15, concurrency 10, clippy 10; idioms: 35/30/25/10) and cross-checked, never re-typed. Context verdicts in the tests come from the real `pcb_rust_scan` run on Rust source. Two mutations of the token rule were confirmed to fail the suite.
- How the DoD is met: `clone`, `unwrap`/`expect` and `unsafe` are never findings by themselves. A lint that fires on the bare token (`rust.clippy.{unwrap-used,expect-used,clone-on-copy,implicit-clone,unsafe-code,undocumented-unsafe-blocks}`, `context_evaluator: context-required`) counts only when the scanner confirms a violation at the same site; benign or hint verdicts and the absence of scanner evidence demote it to `needs_review`/`not_applicable`. `evaluate()` enforces the same rule if a caller skips `normalize()`. Precise lints (`redundant-clone`, `question-mark`, Miri UB) are measured facts and are not cancelled by the scanner's heuristic "benign" verdict. Unjustified `unsafe` is a `hint` for review/judge evidence, never a penalty. Required behaviour decides applicability: ownership, unsafe, Result/Option, iterator and concurrency rules count only when the task froze an opportunity for that item; otherwise the item is `not_applicable`, never a perfect score. One canonical issue is counted once whatever tools report it; violations beyond the opportunity count floor the score at 0.
- Evidence ownership: each issue has one composite owner (Miri UB → robustness, `cargo audit` advisories → security, others none); advisories feed the composite only and add no diagnostic violation.
- Miri states: clean (measured scan), candidate UB (measured finding) and unsupported (`not_applicable` scan) are distinct. A task that *requires* Miri and gets an unsupported or missing scan has its dependent items `missing` with different reasons ("unsupported" vs "incomplete"); a task for which Miri is not required is scored on the remaining evidence.
- Defect fixed in PCB-11-1 code: `observations.slug()` turned dots into hyphens, so `rust.clippy.redundant-clone` became `rust-clippy-redundant-clone` and could never match a rule mapping; it now keeps `._-` like the Python plugin. `COLUMN_KEYED_FAMILIES` is emptied because the scanner reports column 1 and a column in the key would stop clippy and scanner findings for one site from merging (two same-family defects on one line count once, the conservative direction).
- Pending (not this ticket): parsers that turn tool output into these observations and `RustLanguagePlugin` (PCB-11-2); admission fixtures and pilot inventory (PCB-11-4). `rustfmt` is deliberately unmapped (evidence only; not in the configured Rust tool list). The untracked `taskspec.py` from another session declares item names (`correctness`, `unsafe_discipline`, …) that do not match `profiles-v1.yaml`; it should be aligned in PCB-11-2/4.
- Acceptance criteria: — Implement Rust diagnostic/orthogonal idiom profiles and evidence ownership. DoD: clone/unwrap/unsafe tokens are not automatically violations; required behavior and actual contextual evidence determine findings.

## PCB-11-4 - Prompt 11: — Implement Rust support

- Owner prompt: `11`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `implemented`. `plugins/languages/rust/fixtures/top-words/` (public conformance fixture), twelve protected pilot packages under git-ignored `.protected/taskpacks/rust-pilot/`, `taskpacks/rust-pilot/inventory.yaml` (identities and digests only), `packages/evaluation/src/polycodebench_evaluation/suite_admission.py` made language-neutral (plugin-supplied `overlay_prefix`, `candidate_suffixes`, `trusted_inputs`; `variant_files` honours the output contract), `scripts/{rust_task_tool,rust_admit_all,rust_pilot_inventory,rust_conformance,rust_quick_check,record_rust_tool_fixtures}.py`, `docs/rust-plugin.md`.
- Verification: `passed` for executable admission (development/local Docker tier); **quality admission remains `pending`**.
- Required verification scope: E2E-04, E2E-15, E2E-16.
- Evidence: all 12 packages re-admitted by me through the supervisor after the authoring agents' own runs (`.protected/reports/rust-*.json`, 22–24 checks each, every check PASS; the agents' reports are kept in `.protected/reports-authoring/`). Each reference passes its declared inventory across 5 identical repetitions; each of the 41 faulty variants fails its declared cases and no others are declared; the 12 alternatives pass; every quality-defective variant passes the functional gate while Clippy/context/Miri report its declared families (`clone-redundant`, `index-loop`, `unwrap-unguarded`, plus `undefined-behaviour` for the unsafe ring buffer, found only by Miri); the 12 timeout variants are stopped and recorded as candidate failures naming the in-flight case. Required scans (Clippy, context; Miri for the unsafe cluster) are `measured` on every reference; Miri is also `measured` on every reference whose task runs it. Conformance: `docs/implementation/evidence/prompt-11-conformance.json`, 16/16 cases across all 7 categories with real execution. Pilot inventory: `tests/test_rust_pilot_inventory.py` (5), `tests/test_rust_docker.py` (3 live).
- The 12 clusters (295 hidden cases): ring-buffer-unsafe (Miri required, `unsafe_soundness`), lru-cache, token-bucket, text-wrap, csv-records, interval-merge, path-sandbox (security), bounded-channel (concurrency), expr-evaluator, pipeline-traits, semver-ranges, sliding-window-stats. Dimensions: correctness/code_quality/idiomatic/robustness on all 12, security on 1. Each was authored as an original problem (rights records say so; `owner_confirmation: pending`); none was translated from the Python pilot.
- DoD: *intended faults are actually detected* (every declared faulty/defective/timeout variant is executed and must show exactly what it declares); *valid alternative code is accepted* (12 independent algorithms pass the full inventory); *source/split/applicability metadata is complete* (each inventory entry carries source kind/revision/rights/licence, visible/hidden split counts and visible paths, dimensions, opportunity counts, Miri applicability and required analyzers; the dataset-split assignment is explicitly `unassigned (curator decision at freeze)` rather than invented).
- Defects found while doing it: the pilot authors found `discover_cases`/the symbol sanitiser treating `//` inside a string literal as a comment (fixed with one left-to-right literal tokenizer, regression test added); admission only records `failing_cases` for required groups (a faulty variant must not declare a quality-only case; documented in D-11-19); `integer_division` is enabled in the Clippy selection, so a reference using integer `/` is flagged.
- Pending (deliberately not claimed): generic evaluator stage integration (Prompt 12), performance baseline and paired measurement (13; no Rust pilot task declares a workload, so efficiency is not an applicable dimension), judge anchors and calibration (14), deterministic scoring replay (15), production execution tier (Prompt 06, owner-deferred), curator approval and task freeze, owner rights confirmation, hidden-lane object-store registration. `inventory.yaml` records `fully_admitted: 0`, `frozen: 0`; no pilot task is frozen. The dependency audit has no real advisory snapshot (D-11-15).
- Acceptance criteria: — Author Rust admission fixtures and the pilot task inventory with valid/incorrect/alternative/quality-defective cases. DoD: intended faults are actually detected, valid alternative code is accepted, source/split/applicability metadata is complete.

## PCB-12-1 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `implemented` (PCB-12-1 integrated; see evidence).
- Verification: `passed` (offline unit tests, live local-Docker evaluator runs, plugin conformance; development tier).
- Required verification scope: E2E-17, E2E-04 (evaluator-worker variant, Python and Rust).
- Evidence: `packages/evaluation/src/polycodebench_evaluation/evaluator.py` (candidate digest/task-binding and allowed-path validation, a fresh grading guest per plan through `PlanRunner`, overlay/config pools a candidate cannot join, inventory reconciliation from the frozen oracle), `evidence.py` (`EvaluationEvidence`), `tests/test_evaluator.py`, `tests/test_evaluator_docker.py::test_e2e_17_candidate_edits_tests_or_prints_fake_success`. Evidence: `docs/implementation/evidence/prompt-12-e2e-17-disallowed.json` (gate fail, `disallowed_paths`, no analyzer run), `prompt-12-e2e-17-fake-success.json` (candidate printed an 'ALL TESTS PASSED' banner; gate fail with failing inventory cases and `quality_work_gated_off`), `prompt-12-eval-python.json`, `prompt-12-eval-rust.json`. Development sandbox (local Docker) only.
- Acceptance criteria: — Implement fresh grading environments, base/candidate validation, immutable acceptance overlays and expected test inventories. DoD: candidate edits or printed fake successes cannot replace authoritative acceptance; missing mandatory tests never count as passes.

## PCB-12-2 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `implemented` (PCB-12-2 integrated; see evidence).
- Verification: `passed` (offline unit tests, live local-Docker evaluator runs, plugin conformance; development tier).
- Required verification scope: E2E-16 (Python and Rust), E2E-18.
- Evidence: `evaluator.py::_analysis_side`/`_analyzer_evidence` supervise every analyzer plan and record `ToolRecord` (name, version, image/lock/rule-bundle/advisory digests, parser version, scope), the plan status from the exit contract, raw output digests and the scan observation; an unmeasured required analyzer adds `required_scan_not_measured` plus a review item; `native_metrics` stays separate from the gate. Evidence: `prompt-12-python-conformance.json` (14/14), `prompt-12-rust-conformance.json` (16/16, Miri unsupported distinct from clean), `prompt-12-e2e-18.json`, `prompt-12-eval-python.json`, `prompt-12-eval-rust.json`.
- Acceptance criteria: — Implement analyzer execution/parsing contracts and normalized observations with raw report references, tool/rule/advisory versions and explicit failure semantics. DoD: empty reports, crashes and unsupported checks cannot become perfect scores.

## PCB-12-3 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `implemented` (PCB-12-3 integrated; see evidence).
- Verification: `passed` (offline unit tests, live local-Docker evaluator runs, plugin conformance; development tier).
- Required verification scope: E2E-18.
- Evidence: `evaluator.py::_relations` (introduced / worsened / unchanged_in_scope / unchanged_out_of_scope / resolved / unknown against the baseline observations, D-12-03, D-12-04) and `_issue_entries` (one composite owner from the reviewed rule mapping, `counted_once`, every reporting tool named). Evidence: `prompt-12-e2e-18.json` shows four security issues each appearing once with both bandit and semgrep and one `security` owner, all `unchanged_out_of_scope`; `tests/test_evaluator.py` covers introduced/worsened/resolved/ambiguous mapping offline.
- Acceptance criteria: — Implement baseline-to-candidate relations, semantic issue identity and reviewed cross-tool deduplication. DoD: one underlying issue has one composite owner; unchanged unrelated debt is visible without unjustified blame; ambiguous mappings remain reviewable.

## PCB-12-4 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `implemented` (PCB-12-4 integrated; see evidence).
- Verification: `passed` (offline unit tests, live local-Docker evaluator runs, plugin conformance; development tier).
- Required verification scope: E2E-04 (evaluator-worker variant), E2E-16, E2E-18.
- Evidence: Weighted `ScenarioEvidence` read from the task oracle (full credit or zero; `robustness_score_bp=None` if any repetition is incomplete; hard-acceptance scenarios fail the correctness gate), `PropertyEvidence` lifted from the harness record (D-12-07), applicability through the plugins' `ProfileResult`. `tests/test_evaluator_docker.py` runs the Python and Rust `top-words` fixtures end to end (gate pass, scenario credit 10000, property evidence present, profile complete) plus the E2E-17/18 cases. Evidence: `prompt-12-eval-python.json`, `prompt-12-eval-rust.json`, `prompt-12-e2e-18.json`. The 24 protected pilot clusters were not re-admitted here (Prompt 10/11 evidence stands).
- Acceptance criteria: — Implement weighted robustness scenarios, seeded property/fuzz evidence and applicability plans, then complete Python/Rust functional/quality fixture integration. DoD: task-hard requirements gate correctness while optional quality scenarios remain separately weighted; actual fixture results validate those distinctions.

## PCB-13-1 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `implemented` (`packages/evaluation/src/polycodebench_evaluation/{performance.py,perfcontracts.py,efficiency.py}`, `PlanRunner.reserved_guest`).
- Verification: `passed` at development tier — `tests/test_performance_plan.py` and `tests/test_efficiency.py` (16 tests, re-run green) plus `tests/test_performance_docker.py` in the pinned Python image. Hardware gate `blocked_shared_ci`: the dedicated/homogeneous-machine requirement is unmet on this host.
- Required verification scope: E2E-19, plus the dedicated/homogeneous-hardware gate which is recorded as blocked on this host.
- Evidence: `tests/test_performance_plan.py`, `tests/test_performance_docker.py`, `docs/implementation/evidence/prompt-13-{e2e-19-paired,e2e-20-invalid-block,wrong-but-fast}.json`. Corrected 2026-10-03: these entries previously read `in_progress`/`not_run`, which was a Prompt 17 bookkeeping artifact — that prompt declined to audit another prompt's gate, and it was recorded here as if the gate had never run. Prompt 13's own report and its four Docker evidence artifacts show the gate did run. The hardware gate stays blocked and E2E-19/20 remain `not_run` overall.
- Acceptance criteria: — Implement PerformancePlan validation, exclusive capacity/hardware matching, reference identity and output verification. DoD: candidate/reference use the same physical worker/allocation class, frozen workload/flags and equivalent runtime; instrumented builds cannot enter the speed lane.

## PCB-13-2 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `implemented` (`packages/evaluation/src/polycodebench_evaluation/performance.py`; randomized pair order derived from `(plan_seed, workload_seed, iteration)`, per-iteration retention, separate build timings).
- Verification: `passed` at development tier — `tests/test_performance_docker.py` with real containers: one exclusive reservation for the whole window, guest-reported hardware identity, all three declared scales measured, 24 retained iterations, separate build timings. Hardware gate `blocked_shared_ci` as in PCB-13-1.
- Required verification scope: E2E-19.
- Evidence: `tests/test_performance_docker.py`, `docs/implementation/evidence/prompt-13-e2e-19-paired.json`. Corrected 2026-10-03 from `in_progress`/`not_run`; see the PCB-13-1 note.
- Acceptance criteria: — Implement randomized paired order, specified warmup/iteration counts, cold/steady-state modes and whole-process-tree memory recording. DoD: every iteration and input/environment identity is preserved, with compile time separately reported.

## PCB-13-3 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `implemented` (`packages/evaluation/src/polycodebench_evaluation/{performance.py,efficiency.py}`; canary on the trusted reference at the largest declared scale, frozen thresholds, at most two retained blocks, first-valid-block selection).
- Verification: `passed` at development tier — `tests/test_efficiency.py` (9 golden/property tests) and `tests/test_performance_docker.py`: a faster block invalidated by its canary was rejected in favour of the earlier, slower valid block, proving no cherry-picking; a starved frozen baseline invalidates every block with `score: null`. Hardware gate `blocked_shared_ci`.
- Required verification scope: E2E-20.
- Evidence: `tests/test_efficiency.py`, `tests/test_performance_docker.py`, `docs/implementation/evidence/prompt-13-e2e-20-invalid-block.json`. Corrected 2026-10-03 from `in_progress`/`not_run`; see the PCB-13-1 note.
- Acceptance criteria: — Implement canaries, frozen stability thresholds, bounded block retries and first-valid-block selection. DoD: noise invalidates the affected block consistently; the fastest rerun is never cherry-picked.

## PCB-13-4 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `implemented` (`packages/evaluation/src/polycodebench_evaluation/efficiency.py`; symmetric positive floors, median/MAD/relative-MAD, weighted geometric mean over basis-point weights, the documented `f(r,b)` transform, `E = 0.70·f(r_time,4) + 0.30·f(r_memory,2)`, censored-timeout bounds).
- Verification: `passed` — `tests/test_efficiency.py` hand-evaluated golden values: ratio 2 / memory 1.5 → `61.666667`, ratio 1 / memory 1 → `100.000000`, ratio 1 / memory 2 → `70.000000`, a 5 s timeout against a 1 ms reference → lower bound 5000 → `30.000000`, a 2 ms timeout → `insufficient_information` with no score, a missing component → `EfficiencyError`. A censored bound is never reported as an exact duration.
- Required verification scope: E2E-19, E2E-20.
- Evidence: `tests/test_efficiency.py`, `tests/test_performance_plan.py`, `docs/implementation/evidence/prompt-13-wrong-but-fast.json` (a wrong-but-fast candidate is recorded `rejected_output` with `invalid_iterations` and no score). Corrected 2026-10-03 from `in_progress`/`not_run`; see the PCB-13-1 note.
- Acceptance criteria: — Implement workload aggregation, ratio floors, weighted geometric means, variance and censored-timeout handling. DoD: a lower bound is not reported as an exact duration; the documented efficiency transform has golden checks and does not claim proof of Big-O.

## PCB-14-1 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `implemented` (anonymized evidence packets, a frozen rubric/panel pair and a strict vote contract; `packages/core/src/polycodebench_core/judge_contracts.py`, `judge_prompts.py`, `packages/evaluation/src/polycodebench_evaluation/judge_inputs.py`, `config/judging/{rubric,panel}-v1.yaml`).
- Verification: `passed` for every locally verifiable DoD (offline contract suite plus PostgreSQL/SeaweedFS evidence); live judge access is blocked, see PCB-14-4.
- Required verification scope: E2E-21, E2E-22.
- Evidence: `tests/test_judging_core.py` (56 offline tests incl. 16 adversarial fixtures and 11 regressions from the independent review), `tests/test_judge_inputs.py` (7, built from a real `EvaluationEvidence` manifest), `docs/implementation/evidence/prompt-14-e2e-21.json`; the packet type has no identity/provider/rank/cost field, `assert_no_identity_leak` rejects a withheld value copied into evidence text, `load_judge_protocol()` refuses any protocol with tools, and comment anchors (`cmt-`) are never inside an item's evidence scope while instruction attempts are detected and recorded.
- Acceptance criteria: — Build anonymized evidence packets and fixed, versioned rubric/panel definitions. DoD: candidate identity/rank/cost are withheld, citations must exist, judge has no execution tools, and candidate comments are untrusted data.

## PCB-14-2 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `implemented` (`packages/orchestration/src/polycodebench_orchestration/judge/runner.py` and `packages/services/src/polycodebench_services/judging.py`: three logical votes, schema validation, at most two replacement deliveries per vote, exact-decimal averaging).
- Verification: `passed` on real PostgreSQL 17.6, SeaweedFS 4.48 and the real model gateway with fixture judge responses (E2E-21, E2E-22 plus recovery-bound and zero-score cases).
- Required verification scope: E2E-21, E2E-22.
- Evidence: `tests/test_judging_postgres.py` (10 tests) and `docs/implementation/evidence/prompt-14-e2e-21.json`/`prompt-14-e2e-22.json`; migration `b9e04c7a1f38` adds `judge_delivery` so every delivery is retained, `judge_item_result.mean_score` is NULL for an incomplete panel, votes of 0.000000 are stored without a replacement delivery, and `(1 + 0.5 + 1) / 3` is recorded as exactly `0.833333`.
- Acceptance criteria: — Implement three logical votes, schema validation, fixed bounded invalid-vote recovery and averaging. DoD: every delivery is retained; fewer than three valid required votes cannot produce a ready result; low scores are not discarded as retries.

## PCB-14-3 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `implemented` (disagreement triggers, permission-checked reviewer access, validated decisions and immutable supersession; `packages/services/src/polycodebench_services/judging.py`, `packages/persistence/src/polycodebench_persistence/judging.py`, `pcb-judge show|adjudicate|review-queue`).
- Verification: `passed` for triggers, adjudication validation, supersession and cohort versioning (offline plus a stored reviewer override against PostgreSQL); a live human reviewer is an external input, see PCB-14-4.
- Required verification scope: E2E-21, E2E-22.
- Evidence: `tests/test_judging_core.py::test_anchor_spread_and_conflicting_facts_trigger_review`, `::test_vote_citing_only_a_candidate_comment_is_recorded_as_unsupported`, `::test_adjudication_supersedes_an_item_score_without_deleting_votes`, `::test_panel_change_requires_a_new_cohort_version`, `tests/test_judging_postgres.py::test_reviewer_override_appends_a_result_and_preserves_the_votes`; an override requires a declared anchor, existing packet anchors, a substantive reason and the retained vote indexes, and a second result row is appended while the three votes and the earlier result stay.
- Acceptance criteria: — Implement disagreement triggers, reviewer access/decisions and immutable supersession. DoD: overrides cite evidence/anchors/reason and preserve original votes; changing the panel requires a new evaluation/cohort version.

## PCB-14-4 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `implemented` for the workflow (seeded disjoint selection, label import with qualification checks, agreement/confusion/bias reporting, seeded 10% audit selection, blocked reporting when inputs are absent; `packages/core/src/polycodebench_core/judge_calibration.py`, `packages/services/src/polycodebench_services/judging_calibration.py`, `config/judging/calibration-v1.yaml`, `pcb-judge calibration`). The *inputs* are absent: no approved judge endpoint, no qualified human reviewer and no labels exist in this workspace.
- Verification: `blocked` for the human-evidence gate; the metrics, validation and blocked paths are `passed` offline and the label/audit records persist on real PostgreSQL.
- Required verification scope: E2E-21, E2E-22, plus the T §15.3 calibration gate.
- Evidence: `docs/implementation/evidence/prompt-14-calibration.json` (`status: blocked`, `exact_agreement_bp: null`, `promotion_target_met: null`, named `missing_inputs`), `tests/test_judging_core.py` (selection, blocked and metric-arithmetic cases with FIXTURE labels), `tests/test_judging_postgres.py::test_calibration_labels_are_persisted_and_stored_judge_results_are_replayable`. **Required external inputs:** an approved judge endpoint and model configuration distinct from both pilot candidates, a registered reviewer roster, and at least 30 disjoint labelled packets per pilot language including adversarial comments and stylistic alternatives.
- Acceptance criteria: — Implement calibration import/evaluation/reporting and build the disjoint labeled-packet workflow specified in T §15.3. DoD: actual qualified human labels, agreement/confusion metrics and audit selection are recorded; absent labels or judge access remain blocked instead of becoming invented reviews.

## PCB-15-1 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `implemented` — `ValidatedEvidenceManifest` carries the gate verdict, required-analyzer completeness records, canonical issues, rubric items and the efficiency measurement; `score_evaluation` validates evidence completeness first, then applies pass/fail/unknown/N/A semantics and the correctness gate.
- Verification: `passed`.
- Required verification scope: E2E-23, E2E-24.
- Evidence: `docs/implementation/evidence/prompt-15-scoring.json`; `pytest tests/test_scoring_golden.py tests/test_scoring_properties.py -q -p no:cacheprovider` (33 passed).
- Acceptance criteria: — Implement evidence completeness, pass/fail/unknown/N/A semantics and correctness gating. DoD: failed code has zero applicable quality contributions; missing required evidence cannot become either a zero failure or a perfect score.
- Notes: A failed gate zeroes every item including correctness, because the composite is `30g + g × quality`. Missing required evidence, a missing analyzer, an unresolved item or an unadjudicated high-impact claim yields `status=needs_review` with `total_score=null`; a failed correctness gate yields the publishable `0.000000`.

## PCB-15-2 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `implemented` — exact `Fraction` effective weights with a largest-remainder integer presentation, the documented security penalty table, the §13.4 efficiency transform re-derived from the measured ratios, weighted code-quality/idiom/robustness rubrics and a full explanation document.
- Verification: `passed`.
- Required verification scope: E2E-23, E2E-24.
- Evidence: `docs/implementation/evidence/prompt-15-scoring.json`; `pytest tests/test_scoring_golden.py tests/test_scoring_policy.py -q -p no:cacheprovider` (28 passed).
- Acceptance criteria: — Implement exact decimal composites, applicability redistribution, security penalties, quality/idiom/robustness rubrics and efficiency inputs. DoD: nominal/effective weights and item contributions explain every result; diagnostic language profiles do not double-count composite penalties.
- Notes: The composite is computed from exact rational weights; the integer basis points on `ScoreItem` are the rounded presentation, so both are recorded and the exact one is what sums to the total. Diagnostic profile items carry `composite_weight_bp: Literal[0]`, so they cannot contribute by construction.

## PCB-15-3 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `implemented` — `EvidenceOwnership` resolves each canonical family to one composite owner, collapses duplicate issue keys, refuses contradictory owners and undeclared distinct consequences; the manifest digest is order-insensitive; `scorecard_id` is derived from the score's own content.
- Verification: `passed`.
- Required verification scope: E2E-23, E2E-24.
- Evidence: `docs/implementation/evidence/prompt-15-scoring.json`; `pytest tests/test_scoring_properties.py tests/test_scoring_replay.py -q -p no:cacheprovider` (28 passed).
- Acceptance criteria: — Enforce canonical issue/evidence ownership, immutable scorecard identity and traceable contribution chains. DoD: reordered equivalent evidence yields identical output and duplicate findings cannot change a score.
- Notes: A duplicate report is retained as evidence, so it moves the scorecard digest while leaving `score_identity` and every contribution unchanged. That is the honest split between "the evidence changed" and "the score did not".

## PCB-15-4 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `implemented` — `replay_scorecard`/`replay_outcome`, a file-boundary loader, the `pcb-score score|replay` CLI and explicit score-schema and policy-digest version handling.
- Verification: `passed`.
- Required verification scope: E2E-23, E2E-24.
- Evidence: `docs/implementation/evidence/prompt-15-scoring.json`; `pytest tests/test_scoring_replay.py -q -p no:cacheprovider` (9 passed); `uv run --locked --offline --package polycodebench-scoring pcb-score replay ... --archived-outcome ...` returned `{"replayed": true}`.
- Acceptance criteria: — Implement clean-process score replay and score/schema version handling through the CLI/API. DoD: archived validated evidence reproduces the same canonical scorecard without requesting another candidate or judge.
- Notes: Replay ran in a fresh interpreter with a runtime import tripwire over provider clients, network stacks and higher layers, so "no provider calls and no task execution" is verified rather than asserted. A changed policy digest is refused as a policy change rather than silently rescored.

## PCB-16-1 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `implemented` (`packages/publication/src/polycodebench_publication/aggregation.py`, `reporting.py`).
- Verification: `passed` (synthetic/internal aggregation, missing-coverage, common-cohort and report tests; see `docs/implementation/reports/prompt-16.md`).
- Required verification scope: E2E-28–30.
- Evidence: `tests/test_publication_aggregation.py`, `tests/test_publication_reporting.py`; evidence is synthetic/internal and is not a model benchmark result.
- Acceptance criteria: — Implement metric definitions, fixed cohort identity, sample/task/stratum/language aggregation, failure denominators and coverage. DoD: missing languages/tasks are not silently renormalized; conditional-on-pass metrics are separately labeled; filters use a common eligible cohort.

## PCB-16-2 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `implemented` (`packages/publication/src/polycodebench_publication/aggregation.py`).
- Verification: `passed` (fixed-seed clustered bootstrap, paired comparison, sparse coverage and replay tests; see `docs/implementation/reports/prompt-16.md`).
- Required verification scope: E2E-28–30.
- Evidence: `tests/test_uncertainty.py`, `tests/test_publication_aggregation.py`; synthetic/internal fixtures only.
- Acceptance criteria: — Implement fixed-seed clustered/hierarchical bootstrap and paired comparisons. DoD: correlated variants remain in their cluster, uncertainty is recomputed for the actual metric, replicate/seed/method evidence exists, and unstable/insufficient samples are labeled.

## PCB-16-3 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `implemented` (`packages/publication/src/polycodebench_publication/releases.py`).
- Verification: `passed` (approval invalidation, correction immutability and pointer concurrency acceptance tests; see `docs/implementation/reports/prompt-16.md`).
- Required verification scope: E2E-28–30.
- Evidence: `tests/test_publication_releases.py`, `tests/test_releases.py`; local SQLite synthetic release fixtures.
- Acceptance criteria: — Implement draft/validate/review/approve/publish/withdraw states and versioned corrections. DoD: approval binds exact content; a change invalidates approval; published historical results never mutate in place.

## PCB-16-4 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `implemented` (`packages/publication/src/polycodebench_publication/releases.py`, `cli.py`, `scripts/export_publication_schemas.py`).
- Verification: `passed` (safe projection, signature verification, pointer race and schema export checks; see `docs/implementation/reports/prompt-16.md`).
- Required verification scope: E2E-28–30.
- Evidence: `tests/test_publication_releases.py`, `tests/test_releases.py`; `python scripts/export_publication_schemas.py --check`. No external publication performed.
- Acceptance criteria: — Implement allowlisted projections, manifest signing and atomic current-pointer updates. DoD: incomplete/unsafe projections cannot publish, pointer races conflict, and private evidence is never made public through a bulk export.

## PCB-17-1 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `in_progress` (12 Python and 12 Rust clusters have protected bundles and executable-admission evidence; quality admission, owner rights, curator freeze and hidden-lane registration remain pending).
- Verification: `blocked` (no fully admitted/frozen task set; see `docs/implementation/plans/prompt-17-run-plan.json`).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: refreshed `taskpacks/{python,rust}-pilot/inventory.yaml`; each reports 12/12 executable-admitted, 0 fully admitted, 0 frozen. Protected bundle digests are listed in the bounded plan.
- Acceptance criteria: — Complete and freeze at least 12 independent Python clusters and 12 independent Rust clusters with validated reference/faulty/alternative solutions, required quality opportunities, rights, hidden bundles and admission evidence. DoD: no placeholder tasks or unresolved required analyzers enter the pilot.

## PCB-17-2 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `in_progress` (pre-registered 144-attempt single-shot plan; exact provider models, endpoint capabilities, prices, active budget, judge panel and launch route unresolved).
- Verification: `blocked` (run plan cannot resolve actual model configurations or authorized cost bounds).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: `docs/implementation/plans/prompt-17-run-plan.json`; protocol-only `pcb-model plan` is available after resolved model configs, but there is no runnable run-start CLI or HTTP route in this checkout.
- Acceptance criteria: — Freeze two distinct real model identities/configurations, one compatible protocol, three planned samples per task and a common distinct judge panel. Produce exact run plans, resolved capabilities/prices, resource needs, active budgets and exposure policy. DoD: the plan accounts for 24 × 2 × 3 = 144 attempts; outstanding credentials/authorization/human calibration are concrete blockers, not guessed values.

## PCB-17-3 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `not_started` (no provider attempt was dispatched).
- Verification: `blocked` (provider, spend, production worker and authenticated run-start path are not ready).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: zero provider deliveries; all 144 planned logical attempts held before dispatch. No live output was substituted.
- Acceptance criteria: — Execute the bounded pilot using existing authorization or obtain only the missing concrete authorization after preparation. DoD: all attempts have genuine provider/sandbox/evidence lineage; model failures count; infrastructure failures follow the specified retry/missingness policy; no best-answer selection.

## PCB-17-4 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `not_started` (no actual scorecards or pilot report can be generated before dispatch and scoring approval).
- Verification: `blocked` (actual-run evidence is absent by design while preflight gates fail).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: preflight only; no benchmark result, aggregate release, or replay is claimed. See `docs/implementation/reports/prompt-17.md`.
- Acceptance criteria: — Produce the internal exploratory release/report and replay sampled plus required scorecards. DoD: dimensions, coverage, cost/latency, intervals/limitations, tool/judge versions, failure/exclusion ledger and evidence links agree with the actual runs.

## PCB-18-1 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted Prompt 17 pilot remains blocked; independent Prompt 18 implementation was explicitly authorized and does not waive the phase prerequisite.
- Implementation: implemented (typed historical/disclosed/authored/injected/mutation provenance, reproducible Python/Rust internal source builders, hidden-log/oracle visibility gates, clean-control binding, and rejection of unproven/equivalent mutations).
- Verification: passed for the authorized authored-internal scope; public disclosed-security admission remains explicitly blocked by the recorded source requirement.
- Required verification scope: E2E-32–34.
- Evidence: `packages/evaluation/src/polycodebench_evaluation/track_a.py`; `tests/test_track_a.py`; `tests/test_track_a_docker.py::test_authored_pre_fix_injected_and_reference_repair_run_in_python_and_rust`; `docs/implementation/evidence/prompt-18-source-reproduction.json`; `config/track-a-public-security-source-requirement.json`.
- Acceptance criteria: — Implement historical/pre-fix, disclosed-security and mutation task-source workflows with reproducible defects, provenance, immutable oracles, clean controls and rejected equivalent mutations. DoD: Python/Rust historical and injected tasks run, publicly disclosed security coverage has verified examples or a clearly blocked source requirement, and no hidden injection log enters visible assets.
## PCB-18-2 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted Prompt 17 pilot remains blocked; independent Prompt 18 implementation was explicitly authorized and does not waive the phase prerequisite.
- Implementation: implemented (base-digest-bound structured finding parsing/span validation, duplicate handling, reviewer-only causal proposals, fixed-point explanation evidence, and deterministic one-to-one maximum-weight matching).
- Verification: passed (E2E-32 semantics and pending novel findings covered by focused tests).
- Required verification scope: E2E-32–34.
- Evidence: `packages/evaluation/src/polycodebench_evaluation/track_a.py`; `tests/test_track_a.py::test_e2e_32_duplicate_tp_does_not_change_tp_fp_fn_or_micro_scores`; `docs/implementation/evidence/prompt-18-e2e-32.json`.
- Acceptance criteria: — Implement findings parsing/span validation, causal matching, semantic duplicates and one-to-one accepted matches. DoD: file coincidence is insufficient; TP/FP/FN and localization use the specified rules; unresolved genuinely novel findings are not automatic false positives.
## PCB-18-3 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted Prompt 17 pilot remains blocked; independent Prompt 18 implementation was explicitly authorized and does not waive the phase prerequisite.
- Implementation: implemented (append-only hash-chained reviewer events, immutable ground-truth revisions, reviewer/rationale/evidence-bound accepted edges, and complete-cohort rematching).
- Verification: passed (E2E-33 confirms novel findings stay pending, successor oracle digest changes, every affected evaluation rematches, and an incomplete rematch is rejected).
- Required verification scope: E2E-32–34.
- Evidence: `packages/evaluation/src/polycodebench_evaluation/track_a.py`; `tests/test_track_a.py::test_e2e_33_oracle_revision_requires_cohort_wide_rematching`; `docs/implementation/evidence/prompt-18-e2e-33.json`.
- Acceptance criteria: — Implement root-cause/severity rubrics, human adjudication, ground-truth version updates and cohort-wide rematching. DoD: accepted new bugs update all affected results consistently; original decisions/votes remain auditable.
## PCB-18-4 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted Prompt 17 pilot remains blocked; independent Prompt 18 implementation was explicitly authorized and does not waive the phase prerequisite.
- Implementation: implemented (fresh-base allowlisted combined-patch application through the existing patch helper, independent Evaluator execution, failed repair score zero with detection retained, no clean-control repair bonus, explicit model-failure zero credit, missing-attempt coverage loss, and fixed source/language-balanced aggregation).
- Verification: passed for internal acceptance scope (E2E-34 semantic fixture and actual development-sandbox evaluation of an authored incorrect final patch).
- Required verification scope: E2E-32–34.
- Evidence: `packages/evaluation/src/polycodebench_evaluation/track_a.py`; `tests/test_track_a.py::test_e2e_34_failed_repair_is_separate_and_clean_control_has_no_repair_score`, `::test_model_failure_counts_as_zero_credit_while_other_entry_pending_is_ignored`; `tests/test_track_a_docker.py::test_e2e_34_combined_patch_uses_fresh_candidate_and_independent_evaluator`; `docs/implementation/evidence/prompt-18-e2e-34.json`.
- Acceptance criteria: — Implement final combined-patch grading and Track A aggregates, including source/language balance and clean-control handling. DoD: good detection with bad patch retains detection but gets repair zero; no-op clean controls do not earn empty repair credit; multilingual headline follows equal-language aggregation.
## PCB-19-1 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `implemented` (`config/images/javascript-v1.json`, `config/images/typescript-v1.json`, `scripts/build_js_images.py`, `scripts/fetch_js_components.py`, `plugins/languages/javascript/{pyproject.toml,src/polycodebench_lang_javascript/{identities.py,locks.py}}`, `rules/advisories/snapshot.json`; both plugins registered in `config/plugins/allowlist-v1.yaml` with three distinct image digests each).
- Verification: `partial`: the saved JavaScript development-sandbox admission passes 25/25 checks (2026-10-03); TypeScript has a distinct toolchain/image identity but no task pack. The offline-install DoD is not established for both language paths.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `tests/test_language_extension_audit.py`, `config/plugins/allowlist-v1.yaml`, `docs/implementation/reports/language-coverage.md`, and `docs/implementation/evidence/prompt-19-js-admission.json`. JavaScript has one admitted fixture pack (25/25); TypeScript retains distinct profiles/images but no task manifest. The original blanket no-task-manifest note was corrected 2026-10-05.
- Acceptance criteria: — Build pinned Node/package-manager/runtime/test images with offline dependencies and locked recipes. DoD: dependency/advisory snapshots and test-runner identity are recorded and no online installation occurs during scored execution.

## PCB-19-2 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `implemented` (`plugins/languages/javascript/src/polycodebench_lang_javascript/{plans.py,parsers.py,symbols.py,observations.py,taskspec.py}`, guest tools `pcb_js_run.py`, `pcb_vitest_report.py`, `pcb_js_scan.py`, `pcb_npm_audit.py`, `pcb_js_capture.py`, rules `tsconfig.base.json` / `tsconfig.strict.json`, `config/languages/{javascript,typescript}-profile-v1.yaml`).
- Verification: `partial`: the admitted JavaScript pack exercises its pinned build/test and analyzer plans; TypeScript has no executed candidate/reference path. Task-specific strictness behavior is not demonstrated across both languages.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `tests/test_language_extension_audit.py`, `docs/implementation/reports/language-coverage.md` (records the JavaScript evaluator image as `tsc: absent` and TypeScript as `7.0.2` — the separation is real). Corrected 2026-10-03; see the PCB-19-1 note on the stale original text.
- Acceptance criteria: — Implement build/test/symbol/analysis plans, ESLint, applicable security/dependency checks and strict TypeScript checks where the task requires them. DoD: JavaScript is not penalized for lacking TypeScript types; task-specific strictness passes the reference.

## PCB-19-3 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `implemented` (`config/languages/javascript-profile-v1.yaml`, `config/languages/typescript-profile-v1.yaml` with distinct `kind` values `javascript_profile` / `typescript_profile`, applicability and ownership mappings; `observations.py`).
- Verification: `partial`: separate JS/TS profile semantics are asserted and one JavaScript pack is admitted, but the saved task evidence does not cover every async/error/concurrency/type-safety rule; TypeScript has no fixture pack.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `tests/test_language_extension_audit.py`, `config/languages/javascript-profile-v1.yaml`, `config/languages/typescript-profile-v1.yaml`. Corrected 2026-10-03; see the PCB-19-1 note.
- Acceptance criteria: — Implement async/error/concurrency/typing/idiom applicability and ownership mappings. DoD: floating promises and real async errors have evidence, unused concurrency opportunities are N/A, and stylistic modern syntax is not an automatic bonus.

## PCB-19-4 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `implemented` for JavaScript (`plugins/languages/javascript/fixtures/top-words/`: task statement, `package.json`, a lock that genuinely pins, a 16-case oracle, 12 acceptance + 5 quality-only Vitest cases, quality plan, exposure-rights record and six authored variants; plus `scripts/js_task_tool.py` with seal/validate/admit). TypeScript still has no package of its own.
- Verification: `passed` for JavaScript executable admission — real Docker in the pinned evaluator image, 25/25 checks, `docs/implementation/evidence/prompt-19-js-admission.json` (`executable_admission_passed: true`, report `sha256:c10b77ad603bcd5a4f5605578eb212ca0cc7e1bc73680ce5b3a0575cb73e89e9`), run 2026-10-03. Reference passes five gates identically across repetitions, the faulty variant fails exactly its declared case, the alternative passes, quality-defective passes the functional gate while reporting `hardcoded-credential`, the timeout variant fails as a candidate timeout, and command-injection reports `command-injection`. **Quality admission remains `pending`.**
- Required verification scope: E2E-15, E2E-35.
- Evidence: `docs/implementation/evidence/prompt-19-js-admission.json`; `scripts/js_task_tool.py`. Eight latent defects surfaced only because a task pack finally exercised the language: every JS plan failed to construct (`npm_config_*` keys violate the environment-key contract); the build plan used unstaged paths; `pcb_js_capture.py` was passed an unsupported `--name`; build and ESLint were wrapped by a helper that writes no supervisor record both parsers require; three analyzer reports were written but never declared as outputs; the vitest report was never converted to the shared jsonl contract; the wrapper used `sys.executable`, which is the bundled interpreter that cannot start (`GLIBC_2.38 not found` - the same latent defect still present in the Rust images, so D-11-10 is not actually fixed); the vitest wrapper carried a competing internal deadline that turned a candidate hang into an infrastructure error; and `candidate_suffixes` is a `@property` on the JS plugin while `SuiteAdmission` read it with a bare `getattr`, so the property object was compared with `str.endswith` and no candidate path ever matched. TypeScript task packs, curator approval and owner rights confirmation remain open.
- Acceptance criteria: — Admit runnable JS/TS fixture tasks and run the shared language/evaluation conformance suite. DoD: valid, wrong, alternative-valid, security/async/type-defective and timeout fixtures produce the intended evidence through real entrypoints.

## PCB-20-1 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `implemented` (pinned recipes and four distinct images - runtime, evaluator, instrumented, performance - rebuilt 2026-10-03; `ImageIdentities.require_release_recipe` refuses a measurement when the recorded performance image ever declared instrumentation).
- Verification: `passed` — image identities re-recorded in `config/images/c-v1.json`, the guest/rules digest re-derived and the allowlist rewritten by `scripts/build_c_images.py`; the instrumented lane reports ASan+UBSan while runtime/evaluator/performance report `instrumentation: none`.
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: `config/images/c-v1.json`, `docs/implementation/evidence/prompt-20-c-admission.json`. Corrected 2026-10-03: this entry previously said "fresh image build/admission was not run"; both have now run.
- Acceptance criteria: — Build pinned compiler/standard/dependency recipes and separate release/instrumented images. DoD: flags and hardware identities are frozen; sanitizer/Valgrind timing never masquerades as release performance.

## PCB-20-2 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `implemented` (pinned recipes and four distinct images - runtime, evaluator, instrumented, performance - rebuilt 2026-10-03; `ImageIdentities.require_release_recipe` refuses a measurement when the recorded performance image ever declared instrumentation).
- Verification: `passed` — image identities re-recorded in `config/images/c-v1.json`, the guest/rules digest re-derived and the allowlist rewritten by `scripts/build_c_images.py`; the instrumented lane reports ASan+UBSan while runtime/evaluator/performance report `instrumentation: none`.
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: `config/images/c-v1.json`, `docs/implementation/evidence/prompt-20-c-admission.json`. Corrected 2026-10-03: this entry previously said "fresh image build/admission was not run"; both have now run.
- Acceptance criteria: — Implement compilation, tests, clang-tidy/cppcheck and applicable ASan/UBSan/Valgrind plans/parsers. DoD: build errors, sanitizer findings, unsupported checks and infrastructure failures are classified distinctly.

## PCB-20-3 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `implemented` (pinned recipes and four distinct images - runtime, evaluator, instrumented, performance - rebuilt 2026-10-03; `ImageIdentities.require_release_recipe` refuses a measurement when the recorded performance image ever declared instrumentation).
- Verification: `passed` — image identities re-recorded in `config/images/c-v1.json`, the guest/rules digest re-derived and the allowlist rewritten by `scripts/build_c_images.py`; the instrumented lane reports ASan+UBSan while runtime/evaluator/performance report `instrumentation: none`.
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: `config/images/c-v1.json`, `docs/implementation/evidence/prompt-20-c-admission.json`. Corrected 2026-10-03: this entry previously said "fresh image build/admission was not run"; both have now run.
- Acceptance criteria: — Implement ownership/error-checking/portability/UB/memory profile mappings. DoD: baseline warning debt and task-specific warning policy are respected; blanket -Werror does not silently invalidate otherwise admitted legacy tasks.

## PCB-20-4 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `implemented` (C plugin, typed build/test/analysis/performance plans, four pinned images, profile and the `top-words` fixture with nine authored variants).
- Verification: `passed` for executable admission — real Docker in the pinned images, 29/29 checks, `docs/implementation/evidence/prompt-20-c-admission.json` (`executable_admission_passed: true`, report `sha256:bd0cef3b6966b583a76bb010306b5bad44945197eaf16a9cce107ce63d345d0b`), re-run green on 2026-10-03. **Quality admission remains `pending`.**
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: `docs/implementation/evidence/prompt-20-c-admission.json`; `tests/fixtures/c_tool_output/` re-recorded from real toolchain output. Three defects were fixed to reach this, all found by running the real thing: (1) `normalize_rule` never rewrote clang-tidy's `module-check` ids to the profile's `module.check` spelling, so every reviewed mapping missed and canonical families fell back to the raw check tail - one unbounded copy filed under two keys across tools; (2) `workload_smoke` hardcoded Python's `out/perf.json`, a document no C plan can produce, making the check unsatisfiable for every non-Python language; (3) the C performance iteration only *built* its workload and never executed it, and its `--run-arg` values were emitted as one flag instead of one flag per value. Pending gates are listed in the report: generic evaluator stage (Prompt 12), performance baseline/canary (13), judge anchors (14), scoring replay (15), production worker (Prompt 06), curator approval/freeze and owner rights confirmation.
- Acceptance criteria: — Admit real conformance fixtures for correct/alternative code, wrong output, bounds/UB/resource defects and timeouts. DoD: instrumentation detects intended executed defects while reports acknowledge coverage limits; no blanket claim of proven memory safety.

## PCB-21-1 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (pinned lock `config/languages/cpp-toolchain-v1.json` declares compilers, `c++17`/`c++20` standards, four build profiles and the incompatible-instrumentation list; every compile lane resolves flags through the single `cxxflags()` choke point; the sanitizer lane uses `lock.profile_for()` and the release lane `lock.release_profile()`).
- Verification: passed (local). `tests/test_cpp_locks.py` and `tests/test_cpp_plugin.py` (128 passed) cover incompatible-pair rejection, release-profile refusal and `address`+`thread` refusal. Release/performance separation was additionally checked directly: the performance plan's serialized argv contains no `-fsanitize` token.
- Required verification scope: E2E-15, E2E-35 (Docker image build/admission not run in this prompt).
- Evidence: `docs/implementation/reports/prompt-21.md`; `docs/implementation/evidence/prompt-21-cpp.json`.
- Acceptance criteria: — Implement pinned task-specific C++ standard/compiler/build/test recipes with separate performance and sanitizer profiles. DoD: incompatible instrumentation combinations are rejected and reference/alternative builds use the same contract.

## PCB-21-2 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented. Closed the DoD hole where a clang-tidy run that printed nothing this parser could read was reported as `findings=0`, i.e. `MEASURED`, scoring every clang-tidy-fed item full marks: `parsers._clang_tidy` now reads both captured streams and raises when a successful run produced no readable diagnostics, which `_guard` converts to `MISSING`. Also anchored the clang diagnostic regex (`testparse.py`), added `re.MULTILINE` to the UBSan report pattern so a report found mid-stream is no longer dropped, and corrected the UBSan path group so it cannot swallow preceding text.
- Verification: passed (local). `tests/test_cpp_profile.py::test_an_analyzer_that_printed_nothing_is_missing_not_clean` and `::test_clang_tidy_findings_are_found_whichever_stream_carries_them` were confirmed to fail against the pre-fix parser (`measured` instead of `missing`; zero findings on a stdout-only diagnostic) and to pass after it.
- Required verification scope: E2E-15, E2E-35 (Docker image build/admission not run in this prompt).
- Evidence: `docs/implementation/reports/prompt-21.md`; `docs/implementation/evidence/prompt-21-cpp.json`.
- Acceptance criteria: — Integrate selected clang-tidy/cppcheck rules and applicable ASan/UBSan/TSan checks with normalized output. DoD: actual findings/crashes/unsupported paths retain their correct semantics; no analyzer omission silently raises scores.

## PCB-21-3 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (no defect found in this prompt). Ownership, copy/move and modern-feature applicability are enforced through equivalence families in `config/languages/cpp-profile-v1.yaml`, so one construct reported by several tools collapses to one scored issue: clang-tidy's copy lints, cppcheck's `passedbyvalue` and the context scanner's `redundant-container-copy` all resolve to the `value-copy` family, and a leak seen by ASan shares `manual-ownership` with the scanner's `raw-owning-pointer`. `CppProfile.normalize` additionally drops duplicate reports of one `issue_key` and demotes token-only lints to `needs_review` so they cost nothing.
- Verification: passed (local). `tests/test_cpp_profile.py` asserts a non-owning `const T*`/`const char*` produces no finding at all, that a duplicated report of one rule at one site cannot change a score, and that several benign non-owning-pointer findings leave the score at 10000.
- Required verification scope: E2E-15, E2E-35 (Docker image build/admission not run in this prompt).
- Evidence: `docs/implementation/reports/prompt-21.md`; `docs/implementation/evidence/prompt-21-cpp.json`.
- Acceptance criteria: — Implement RAII/ownership, STL/container, move/value-semantics and modern-feature applicability with single composite ownership. DoD: nonowning raw pointers or justified legacy patterns are not automatically failures; measured copies and API choices are not blindly double-penalized.

## PCB-21-4 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: repaired the C++ build/output declarations, sanitizer and context evidence parsing, abnormal candidate-exit classification, compatible sanitizer lanes, fixture visibility, and typed performance smoke. The task now has eight authored synthetic variants; TSan is not required because the local runtime cannot initialize.
- Verification: passed (development sandbox). Executable admission passed 27/27 checks; five reference repetitions matched; clang-tidy, cppcheck, context and ASan measured the reference and applicable defects; performance smoke passed. Quality admission remains pending.
- Required verification scope: E2E-15 and E2E-35 remain partial; quality admission, curator/owner approval and downstream scoring/replay are pending.
- Evidence: `docs/implementation/reports/prompt-21.md`; `docs/implementation/evidence/prompt-21-cpp-admission-followup.json` (27/27, `development_sandbox`). No live benchmark result is claimed.
- Acceptance criteria: — Admit runnable fixtures for valid alternatives, ownership/exception/resource/concurrency defects and timeouts. DoD: shared extension checks pass and expected evidence reaches the ordinary scorer/replay path.

## PCB-22-1 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (pinned golang 1.26.8 base with distinct runtime/evaluator/performance recipes, an offline components build, a per-recipe instrumentation declaration, and a build step that refreshes the allowlist).
- Verification: passed (images rebuilt and re-probed from the real artifacts; recorded digests match the built images and the allowlist entry; manifest resealed and validates).
- Required verification scope: E2E-15, E2E-35.
- Note: the Go fixtures are pinned to LF via a new `.gitattributes`; under `core.autocrlf` they arrived as CRLF and `gofmt` (a scored required analyzer) charged the reference solution a formatting finding. See D-22-13.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Acceptance criteria: — Build pinned Go/module/vendor/test and release recipes with offline execution. DoD: runtime/dependency/flag identity is reproducible and language registration is data-driven.

## PCB-22-2 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (go test, gofmt, go vet, staticcheck, gosec and race-enabled runs; the race lane is refused for the measurement recipe and the measurement recipe is refused instrumented plans).
- Verification: passed (all five required scans measured for every variant in the executable admission; 102 unit/contract tests; the staticcheck/gosec clean-versus-absent rule is pinned by two regression tests, one of them mutation-checked).
- Required verification scope: E2E-15, E2E-35.
- Note: the Go fixtures are pinned to LF via a new `.gitattributes`; under `core.autocrlf` they arrived as CRLF and `gofmt` (a scored required analyzer) charged the reference solution a formatting finding. See D-22-13.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Acceptance criteria: — Integrate go test, gofmt checking, vet, staticcheck, gosec and applicable race-enabled runs. DoD: race/instrumented results cannot enter performance measurements; checker crashes cannot look like clean output.

## PCB-22-3 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (error/interface/stdlib/context/concurrency diagnostic families plus orthogonal idiom items, gated on frozen task opportunities).
- Verification: passed (a nonconcurrent task reports its concurrency items not_applicable rather than charged, and every lifecycle/cancellation check resolves to a concrete robustness owner).
- Required verification scope: E2E-15, E2E-35.
- Note: the Go fixtures are pinned to LF via a new `.gitattributes`; under `core.autocrlf` they arrived as CRLF and `gofmt` (a scored required analyzer) charged the reference solution a formatting finding. See D-22-13.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Acceptance criteria: — Implement error/interface/stdlib/context/concurrency diagnostic and orthogonal idiom mappings. DoD: nonconcurrent tasks mark concurrency N/A and lifecycle/cancellation findings have concrete contract/evidence ownership.

## PCB-22-4 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (six fixture variants: reference, alternative-heaps, faulty-ties, quality-defective, race-defective and timeout-case).
- Verification: executable admission passed (reference 5/5 repetitions; valid alternative; intended faulty-ties and timeout failures; quality-defective gate passes and shows declared families). Latest saved conformance separately fails 1/18 on the benign-sample candidate gate (`gate=fail score=10000 measured=[]`); do not mark conformance complete until fixed and rerun.
- Required verification scope: E2E-15, E2E-35.
- Note: the Go fixtures are pinned to LF via a new `.gitattributes`; under `core.autocrlf` they arrived as CRLF and `gofmt` (a scored required analyzer) charged the reference solution a formatting finding. See D-22-13.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Acceptance criteria: — Admit fixtures exercising valid/alternative code, ignored errors, cancellation/lifecycle faults, wrong answers and limits. DoD: scenarios detect their intended behavior under a frozen repetition policy and clean fixtures are not falsely penalized.

## PCB-23-1 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (offline-seeded Temurin/Maven/JUnit recipes, isolated evaluator analyzers, and immutable cold/steady-state JVM policy).
- Verification: passed for the Java recipes and executable task admission. Identities are in `config/images/java-v1.json`; the current 26-check development-sandbox report is `docs/implementation/evidence/prompt-23-java-admission.json`.
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: See `scripts/build_java_images.py --check` and `docs/implementation/reports/language-coverage.md`.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Required verification scope: E2E-15, E2E-35.
- Evidence: `docs/implementation/evidence/prompt-22-go-admission.json`, `docs/implementation/evidence/prompt-22-go-conformance.json`, `tests/test_go_plugin.py`, `tests/test_go_guest.py`, `tests/test_go_profile.py`, `tests/test_go_locks.py`, `tests/test_go_docker.py` and `docs/implementation/reports/prompt-22.md`.
- Acceptance criteria: — Build pinned JDK/Maven-or-Gradle/JUnit recipes, offline dependencies and frozen JIT/performance policy. DoD: declared cold/steady-state modes and warmup do not adapt to favor individual candidates.

## PCB-23-2 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (SpotBugs, PMD, Checkstyle, locked dependency/advisory audit, and contextual resource/concurrency/security probes use shared plan and observation contracts).
- Verification: passed for five measured reference scans, analyzer failure/absence semantics, resource/security/concurrency findings, and the task's frozen dependency lock in the built evaluator image. Shared baseline-delta resolution remains the scoring contract; the fixture has no public score claim.
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: See Java plugin tests and `docs/implementation/reports/language-coverage.md`.
- Acceptance criteria: — Integrate SpotBugs, PMD, Checkstyle, dependency analysis and task-specific security/resource/concurrency probes. DoD: analyzer coverage, failure semantics and baseline deltas use shared contracts.

## PCB-23-3 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: implemented (Java profile and eight reference/faulty/alternative/null/resource/concurrency/security/timeout fixtures; stream/record/SOLID presence alone cannot earn points).
- Verification: passed for the 26-check executable admission: reference 5/5 stable passes, valid alternative passes, wrong-output/null/timeout fail as candidates, and both resource/concurrency quality-only probes reject their defective variants.
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: See `tests/test_java_taskspec.py`, `tests/test_java_plugin.py`, `tests/test_java_guest.py` and `docs/implementation/evidence/prompt-23-language-audit.json`.
- Acceptance criteria: — Implement Java profiles and admit valid/alternative/null/resource/concurrency/security/wrong-output fixtures. DoD: streams/records/SOLID terminology do not earn automatic points; behavioral and contextual evidence determines results.

## PCB-23-4 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: partial (all eight plugin/image identities are audited, JS and TS semantics are distinct, and an allowlist writer that dropped languages plus a C++ analyzer identity defect were fixed).
- Verification: partial (JavaScript 25/25, C 29/29, C++ 27/27, Go 24/24 with corrected conformance 18/18, and Java 26/26 have current development-sandbox executable admissions; TypeScript has no task pack; quality admission and complete E2E-15/E2E-35 paths remain partial).
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: See `tests/test_language_extension_audit.py`, `docs/implementation/reports/language-coverage.md`, and `docs/implementation/evidence/prompt-23-language-audit.json`.
- Acceptance criteria: — Audit Python, Rust, JS, TS, C, C++, Go and Java end to end through plugin registration, task admission, solve output contracts, grading, scoring, replay and capability metadata. DoD: every required language has actual conformance evidence, separate JS/TS semantics, and no missing core path hidden by a capability label.

## PCB-24-1 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`plugins/suites/swebench/src/polycodebench_suites_swebench/{records,adapter}.py`): `MethodologyRecord` with the `native`/`inspired`/`adapted` vocabulary, `SourceManifest` pinned to a dataset revision and a provenance class, `NativeTaskInstance`/`NativeTestSpec`/`NativeTaskDraft` records, an immutable repository snapshot reader, and `patch_output` bound to the frozen task and candidate digest.
- Verification: `passed` (local-fixture tier).
- Required verification scope: E2E-36.
- Evidence: `plugins/suites/swebench/tests/test_e2e36_native_repo_repair.py` (23 tests, PASS at both tiers: 22 passed + 1 Docker-gated skip, and 23 passed with `PCB_TEST_DOCKER=1`); `docs/implementation/evidence/prompt-24-e2e36.json`; source revision, protocol differences and the public label are recorded on the methodology record and refused when absent (D-24-01).
- Acceptance criteria: — Implement SuiteAdapter import/validation for supported SWE-bench-style/native task records, immutable repo snapshots and patch output. DoD: source revisions/terms/protocol differences are recorded and no future fixes/hidden tests leak into solving.

## PCB-24-2 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`plugins/suites/swebench/src/polycodebench_suites_swebench/grading.py`): the native metric comes from a call to the pinned upstream `swebench.harness.grading.get_eval_report` at revision `5.0.2`; `native_metrics` exports that result and never recomputes a local fraction, and a missing or mismatched upstream revision raises rather than degrading.
- Verification: `passed` (local-fixture tier, upstream evaluator 5.0.2).
- Required verification scope: E2E-36.
- Evidence: `test_the_native_metric_comes_from_the_pinned_upstream_evaluator` and `test_the_native_metric_is_reported_separately_from_the_polycodebench_gate`; `docs/implementation/evidence/prompt-24-e2e36.json` records `RESOLVED_FULL`/`RESOLVED_NO` per candidate with gate and quality evidence flagged separate.
- Acceptance criteria: — Wrap the pinned upstream evaluator rather than loosely recreating its result from a generic test fraction. DoD: native fail-to-pass/pass-to-pass outcomes and resolution metric are preserved separately from PolyCodeBench acceptance and quality.

## PCB-24-3 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`plugins/suites/swebench/src/polycodebench_suites_swebench/overlay.py`): `run_identity` binds task, candidate and evaluator digests and the cache is keyed strictly by it; the candidate patch is applied before the hidden test patch, so a graded-test edit is overwritten; `check_instance_leakage` refuses a bundle exposing a fail-to-pass test, the gold patch or the test patch.
- Verification: `passed` (local-fixture tier).
- Required verification scope: E2E-36.
- Evidence: `test_a_cached_grade_is_only_returned_for_the_same_candidate`, `test_a_reimported_task_cannot_reuse_the_stale_grade`, `test_the_evaluator_digest_is_part_of_the_run_identity`, `test_editing_a_visible_test_does_not_replace_native_acceptance`, `test_a_candidate_patch_outside_the_allowlist_is_refused`; every candidate produced a distinct run identity and only a repeat of the same candidate was a cache hit.
- Acceptance criteria: — Bind cache/upstream run identity to task, candidate and evaluator digests and integrate protected grading overlays. DoD: one candidate cannot receive another candidate's cached grade and edits to visible tests cannot replace native/hidden acceptance.

## PCB-24-4 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`plugins/suites/swebench/tests/{native_compatible_instance,adapted_port_instance}.py`): an authored native-record fixture (`pcb-native-compatible-calc`, labelled `inspired`) and a deliberately ported Rust fixture (`pcb-adapted-calc-rs`, labelled `adapted` with two declared protocol deviations), both admitted through the adapter and graded by the pinned upstream evaluator.
- Verification: `passed` (local-fixture tier; the Rust port is compiled and executed in the pinned Rust image, so this is development-tier evidence, not a production-worker run).
- Required verification scope: E2E-36.
- Evidence: `docs/implementation/evidence/prompt-24-e2e36.json`: the two labels differ and each record validates; reference and alternative patches resolve `RESOLVED_FULL`, the faulty partial fix and the no-op resolve `RESOLVED_NO`, and every outcome is traceable to one frozen candidate digest. No official dataset instance is imported, so no `native` label is claimed anywhere.
- Acceptance criteria: — Admit native-compatible and deliberately adapted/ported fixtures through the actual harness. DoD: their methodology labels differ correctly; patch correctness and applicable quality evidence are traceable to the same frozen candidate.

## PCB-25-1 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 25 deliverable completed).
- Verification: `passed` (authoring/import contract tests and admitted-pack matrix evidence recorded).
- Required verification scope: Prompt 25 acceptance: repo-task authoring/import tests, sealed acceptance contract, full variant matrix at local_fixture tier.
- Evidence: `packages/services/src/polycodebench_services/repo_tasks.py` (authoring contracts, `seal_acceptance_contract`/`verify_acceptance_contract`, `import_repo_task_package`); packs `taskpacks/repo-tasks/{ini-interpolate,history-group}` with `repo-task.yaml` acceptance contracts; tests `tests/test_repo_tasks.py` (11 passed, including alternative-variant admission, contract-drift refusals and bounded rubric checks); admission evidence `docs/implementation/evidence/prompt-25-admission-{ini-interpolate,history-group}.json`.
- Acceptance criteria: — Implement task authoring/import for developer requests spanning real files/modules, with repo conventions, allowed changes and acceptance contracts. DoD: multiple valid implementations can succeed; hidden requirements are not improvised after seeing a candidate.

## PCB-25-2 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 25 deliverable completed).
- Verification: `passed` (gate-precedence and judge-seam regression cases recorded; judge evidence is fixture-class, live judge endpoints remain unprovisioned).
- Required verification scope: Prompt 25 acceptance: executable acceptance plus bounded rubric items through the judge services; gate precedence over judgments.
- Evidence: `packages/evaluation/src/polycodebench_evaluation/repo_task_grading.py` (`executable_gate`/`combine_gate` read no judge data), `packages/scoring/src/polycodebench_scoring/judge_evidence.py` (ItemOutcome→RubricItemEvidence seam), `packages/evaluation/src/polycodebench_evaluation/judge_inputs.py` (`judge_packet_input_from`); tests `tests/test_repo_task_grading.py::test_functionally_failing_patch_is_zero_even_with_perfect_judgments` and `::test_judge_backed_gate_criterion_can_fail_but_not_pass_by_override` (passed); matrix evidence `docs/implementation/evidence/prompt-25-matrix-*.json` (fixture_judge_votes class).
- Acceptance criteria: — Implement executable acceptance plus bounded rubric items for genuinely non-executable criteria, using existing judge/reviewer services. DoD: all criteria have frozen evidence methods and required-gate status; judgments cannot override failed mandatory tests.

## PCB-25-3 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 25 deliverable completed).
- Verification: `passed` (baseline-aware evidence cases recorded for both artifact kinds).
- Required verification scope: Prompt 25 acceptance: patch and workspace artifacts, baseline-aware convention evidence, legacy-debt and unchanged-file scoping.
- Evidence: `packages/evaluation/src/polycodebench_evaluation/repo_task_conventions.py` and shared `evaluator.baseline_relations`; tests `tests/test_repo_task_grading.py::test_legacy_debt_is_context_and_unchanged_files_are_never_new_code`, `::test_patch_and_workspace_artifacts_grade_identically`, `::test_patch_that_touches_protected_or_unknown_paths_is_rejected` (passed); matrix rows show `cfgkit/legacy_report.py` findings as `unchanged_out_of_scope`, `penalized=false`, `in_new_code=false` in `docs/implementation/evidence/prompt-25-matrix-ini-interpolate.json`.
- Acceptance criteria: — Integrate patch/workspace artifacts and applicable quality profiles with baseline-aware evidence. DoD: repository-wide legacy debt does not become a candidate penalty and unchanged files are not scored as new code.

## PCB-25-4 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 25 deliverable completed).
- Verification: `passed` (two realistic multi-file fixtures admitted through the harness; methodology boundary documented and asserted).
- Required verification scope: Prompt 25 acceptance: admission of the full variant matrix and methodology-boundary documentation checks.
- Evidence: `polycodebench_evaluation.repo_task_admission` (five stable reference repetitions, alternative accepted, faulty rejected, quality-defective functionally passing and detected); `docs/implementation/repo-task-method.md` and `docs/methodology/cursorbench.md`; tests `tests/test_repo_task_admission.py` (8 passed, including label enforcement and reproduction-claim refusals in `tests/test_repo_tasks.py`).
- Acceptance criteria: — Admit realistic multi-file fixtures and document the Cursor-inspired methodology boundary. DoD: no claim of private CursorBench task access/exact reproduction or false statement that its approach ignores quality/efficiency.

## PCB-26-1 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 26 deliverable completed).
- Verification: `passed` (round-state, feedback-isolation and budget tests recorded).
- Required verification scope: E2E-37.
- Evidence: `packages/core/src/polycodebench_core/repair_contracts.py` (RepairProtocol/RepairLimits/RepairRound/RepairRun/RoundTicket, public-only RepairFeedback with HiddenFeedbackRejected, RepairSpend) and `repair_prompts.py` (policy pcb-repair-v1); tests `tests/test_repair_contracts.py` 13 passed, including `test_every_round_retains_candidate_prompt_feedback_and_cost` and `test_the_protocol_fixes_when_rounds_stop`; durable round records in `docs/implementation/evidence/prompt-26-e2e-37.json`.
- Acceptance criteria: — Implement initial/repair-round state, allowed public feedback and fixed round/budget limits. DoD: every round retains its candidate, prompt, public feedback and cost; the protocol fixes when rounds stop.

## PCB-26-2 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 26 deliverable completed).
- Verification: `passed` (protocol selection without hidden-result access; initial/final outcomes distinct from cumulative cost).
- Required verification scope: E2E-37.
- Evidence: `select_final`/`freeze_selection`/`metrics_of` in `repair_contracts.py` (selection is a pure function of the frozen rule and public results only; RepairMetrics records initial/final native correctness separately from cumulative tokens/cost); tests `tests/test_repair_contracts.py::test_final_selection_is_protocol_rule_not_hidden_best_of`, `tests/test_repair_session.py::test_e2e37_only_public_feedback_reaches_the_model` (hidden 0/2 initial vs 2/2 final on the protocol-selected artifact).
- Acceptance criteria: — Implement final candidate selection without hidden-result access. DoD: final quality evaluates the protocol-selected artifact, never the best hidden-scoring round; initial/final native correctness and cumulative cost remain distinct.

## PCB-26-3 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 26 deliverable completed).
- Verification: `passed` (round-boundary checkpoint, redelivery and restart cases on real PostgreSQL).
- Required verification scope: E2E-37.
- Evidence: `packages/persistence/src/polycodebench_persistence/repair_state.py` (run-row round-boundary checkpoint under CAS, immutable repair_round/repair_delivery rows via migration `e5f6a7b8c9d0_repair_runs_and_rounds`), `solve_state.py` candidate revisions; tests `tests/test_repair_state_postgres.py` 5 passed (stale-frontier conflict, redelivery adds delivery and spend without a round, DB trigger keeps rounds immutable) and `tests/test_repair_session.py::test_e2e37_restart_during_a_repair_round_grants_no_extra_round` (restart consumes the persisted response once, no extra round, spend preserved).
- Acceptance criteria: — Integrate durable checkpoints and infrastructure retries at round boundaries. DoD: recovering infrastructure does not grant additional repair rounds or erase spent budget.

## PCB-26-4 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 26 deliverable completed).
- Verification: `passed` (fixture matrix independently re-run; methodology records asserted).
- Required verification scope: E2E-37.
- Evidence: admitted pack `taskpacks/self-repair/py-listsort-v1` (family self_repair, methodology_label adapted; initial scaffold 2/3 public + 0/2 hidden with visible feedback; reference/alternative 5/5; faulty fails hidden-stability; quality_defective 5/5); `docs/implementation/self-repair-method.md` (native-versus-adapted record, LiveCodeBench self-repair scenario as native reference) and `docs/methodology/livecodebench.md`; tests `tests/test_repair_session.py::test_e2e37_hidden_outcomes_cannot_cause_another_model_call` prove hidden outcomes cannot cause another model call.
- Acceptance criteria: — Add admitted self-repair fixtures and native-versus-adapted methodology records. DoD: visible feedback can drive the permitted repair, while hidden outcomes cannot cause another model call.

## PCB-27-1 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 27 deliverable completed).
- Verification: `passed` (pinned inputs, read/search-only protocol, answer envelope and retrieval logging tests recorded).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: `packages/core/src/polycodebench_core/qa_contracts.py` (QaTaskInputs pinning base digest and snapshot paths; `validate_qa_protocol_tools` refusing every mutating tool; `parse_qa_answer` frozen envelope with claims quoting the answer and citations carrying the pinned digest; `RetrievalRecord`/`retrieval_records` logging context and truncation), `qa_prompts.py` (policy pcb-qa-v1), `config/protocols/repo-qa-v1.yaml` (list_files/read_file/search only); tests `tests/test_qa_contracts.py::{test_qa_protocols_are_read_search_only,test_retrieval_context_and_truncation_are_logged,test_question_inputs_pin_the_snapshot,test_citations_reference_the_base_snapshot_only}` (passed).
- Acceptance criteria: — Implement pinned repo/question inputs, read/search-only solving and structured answers/citations. DoD: citations reference the base snapshot; editing is disabled for this protocol; retrieval context and truncation are logged.

## PCB-27-2 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 27 deliverable completed).
- Verification: `passed` (entailment judging through the real judge services; recall formula and native aggregation tests recorded).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: `qa_contracts.py` (versioned `QaOracle`/`AtomicFact` with weights and accepted paraphrases; `fact_credits` and `weighted_fact_recall` per Technical Spec 17.3; `native_presence_aggregation` preserved separately) and `packages/evaluation/src/polycodebench_evaluation/qa_grading.py` (`entailment_packet_input`, `entailment_votes` converting real `JudgementResult`s); rubric/panel `config/judging/qa-entailment-{rubric,panel}-v1.yaml`; tests `tests/test_qa_grading.py` (10 passed, incl. `::test_e2e38_missing_facts_zero_repetition_no_credit_and_empty_recall_zero` and `::test_e2e38_native_aggregation_is_preserved_separately_from_entailment`).
- Acceptance criteria: — Implement atomic-fact oracles, accepted paraphrases and fixed entailment judging with preserved native adapter aggregation. DoD: missing facts get no credit, repeated facts add no credit and empty answers have fact recall zero.

## PCB-27-3 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 27 deliverable completed).
- Verification: `passed` (citation, grounding and claim diagnostics with explicit unknown states recorded).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: `qa_contracts.py` (`validate_citations`, frozen `claim-extraction-v1`, deterministic `contradiction-v1`, `claim_precision`/`grounding_rate`/`rate` keeping unknown as None) and `qa_grading.py` (`grade_qa` producing `QaMetrics` with `grounding_rate`, `unsupported_claims`/`contradicted_claims` and `code_dimensions: not_applicable`; `incomplete_verifications` naming the missing judgments); tests `tests/test_qa_grading.py::{test_e2e38_incomplete_verification_is_unknown_not_zero,test_e2e38_wrong_citations_are_invalid_and_grounding_unsupported_diagnostics_follow}` and `tests/test_qa_fixtures.py::test_six_code_dimensions_stay_not_applicable` (passed).
- Acceptance criteria: — Implement citation validity, grounding and unsupported/contradicted-claim diagnostics with explicit unknown states. DoD: incomplete claim verification is not zero hallucinations; prose does not receive invented security/runtime/idiom scores.

## PCB-27-4 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (Prompt 27 deliverable completed).
- Verification: `passed` (cross-file fixture matrix re-run through the real contracts; method records asserted).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: admitted pack `taskpacks/qa/py-configkit-qa-v1` (family repo_qa, `inspired`; cross-file facts verified in `cfgkit/loader.py` and `cfgkit/interpolate.py`; versioned `hidden/oracle.json` with base-digest-tied verifying spans; variants: reference and paraphrased alternative both 100.000000 recall, wrong-citation all-citations-flagged with grounding 0, contradiction credited zero and counted, empty recall zero with undefined precision, repeated equals reference); `docs/implementation/qa-method.md` (DeepCodeBench-inspired boundary, native-versus-inspired table, explicit non-claims) and `docs/methodology/deepcodebench.md`; tests `tests/test_qa_fixtures.py` (8 passed).
- Acceptance criteria: — Admit cross-file Q&A fixtures with verifying code spans, alternative correct wording, wrong citations and contradictions. DoD: source/method records accurately describe DeepCodeBench-inspired or native compatibility and all fact evidence is versioned.

## PCB-28-1 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`plugins/suites/swebench/src/polycodebench_suites_swebench/prediction.py`: `validate_prediction_tools` refuses every execution tool by name, and `check_protocol_constraints` refuses test/hidden feedback and non-disabled network for both prediction families; `config/protocols/prediction-v1.yaml` is the frozen cohort with zero tools and zero tool calls, and `packages/core/src/polycodebench_core/prediction_prompts.py` renders its deterministic messages).
- Verification: `passed` (local fixture tier).
- Required verification scope: E2E-36–38.
- Evidence: `plugins/suites/swebench/tests/test_e2e38_prediction.py` (`test_a_prediction_protocol_cannot_carry_an_execution_tool`, `test_a_different_tool_policy_is_a_different_cohort`, `test_the_prediction_protocol_carries_no_tool_at_all`) and `evidence/prompt-28-e2e38.json` (`protocol_restriction`). The oracle records whether it was execution-derived; the model still cannot run anything.
- Acceptance criteria: — Implement code-execution/output-prediction inputs and protocol restrictions. DoD: the evaluated model cannot run the target code when the declared task measures prediction without execution; a different tool policy is a different cohort.

## PCB-28-2 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`prediction.py` plus `prediction_grading.grade_prediction_task`): frozen `NormalizationRules` for `exact_bytes`/`normalized_text`/`typed_json`, one-pass `parse_submission`, and a total comparison whose verdict carries the deciding rule. A parse error is a wrong answer; the grade report has no judge field, so a mismatch cannot be rescued.
- Verification: `passed` (local fixture tier; 16 graded cases).
- Required verification scope: E2E-36–38.
- Evidence: `test_exact_bytes_compares_characters_not_whitespace`, `test_normalized_text_applies_only_its_declared_rules`, `test_typed_json_ignores_declared_key_order_and_honours_tolerance`, `test_a_parse_error_is_a_wrong_answer`, `test_judge_votes_cannot_rescue_a_mismatch`; `evidence/prompt-28-e2e38.json` records every verdict with its candidate and oracle digest.
- Acceptance criteria: — Implement test-output-prediction tasks and explicit exact_bytes/normalized_text/typed_json grading. DoD: normalization rules are frozen, parse errors are wrong answers, and judges do not rescue deterministic mismatches.

## PCB-28-3 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (two admitted packs under `taskpacks/prediction/`, both `adapted` per the deviations register and `docs/methodology/livecodebench.md`; both import through `TaskPackageImporter`. `prediction_metric_definitions` publishes the answer-only metric with an empty code-dimension list, so the six generated-code dimensions are absent from a prediction report rather than zero).
- Verification: `passed` (both packs import; every graded case deterministic).
- Required verification scope: E2E-36–38.
- Evidence: `test_prediction_metrics_declare_no_code_dimensions`, `test_answer_only_metrics_are_absent_not_zero`; pack digests and the frozen protocol are in `evidence/prompt-28-e2e38.json` (`fixtures`).
- Acceptance criteria: — Admit prediction fixtures and wire native/ported/inspired methodology plus answer-only metric definitions. DoD: generated-code dimensions stay N/A and output/error types are handled consistently through reports/API schemas.

## PCB-28-4 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `implemented` (`prediction_grading.audit_track_b` plus `docs/implementation/reports/suite-coverage.md`): the audit walks all seven required Track B families through their real grader entrypoints, refuses to build a verdict that omits a family, and fails a family whose pack or evidence path is absent from the tree. WP-20 is closed by that run: `wp20_closed: true` with `failed_families: []`.
- Verification: `passed` (audit verdict machine-produced; prior prompt evidence reused as recorded, not re-run).
- Required verification scope: E2E-36–38.
- Evidence: `evidence/prompt-28-e2e38.json` (`track_b_coverage`), `docs/implementation/reports/suite-coverage.md` (per-family entrypoints, packs, evidence and scope limits). Prior applicable evidence preserved: E2E-36 (`prompt-24-e2e36.json`), repo-task admissions (`prompt-25-*.json`), E2E-37 (`prompt-26-e2e-37.json`).
- Acceptance criteria: — Audit codegen, repository repair, realistic repo tasks, self-repair, repo Q&A, output prediction and test prediction through actual entrypoints. DoD: all have source records, output contracts, allowed tools/feedback, grading, missingness and reproducible evidence; close WP-20 only when every required family passes.

## PCB-29-1 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `implemented` (release listing/current metadata, leaderboard, model/language, compare, public task, scorecard, evidence and methodology routes read published release projections in `packages/api/src/polycodebench_api/public_routes.py` and `packages/publication/src/polycodebench_publication/`).
- Verification: `partial` (routes and projections are exercised by Prompt 30-32 API/browser suites; a standalone full Prompt 29 route matrix was not recorded).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: `docs/implementation/reports/prompt-29.md`, `tests/test_public_api_prompt30.py`, `tests/test_public_api_prompt31.py`, `tests/test_public_api_prompt32.py`, `tests/test_public_api_projections.py`, and `tests/test_publication_releases.py`.
- Acceptance criteria: — Implement release, leaderboard, model, language, comparison, public task, scorecard, artifact and methodology endpoints with typed metric definitions. DoD: responses come from actual published projections, never worker/hidden tables or hardcoded demo arrays.

## PCB-29-2 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `implemented` (release-bound filters, pagination, stable sorting, decimal responses, ETags/cache policy, comparison compatibility and common-task pairing are implemented; comparison scope is explicit for release aggregates versus the filtered paired-task intersection).
- Verification: `partial` (focused API and browser coverage exists in Prompt 30-32; complete E2E-28 cohort variants are not recorded as a fresh Prompt 29 run).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: `docs/implementation/reports/prompt-29.md`, `docs/implementation/reports/prompt-31.md`, `tests/test_public_api_prompt31.py`, `tests/test_public_api_projections.py`, and `tests/test_publication_releases.py`.
- Acceptance criteria: — Implement release/filter-bound pagination, stable sorting, decimal serialization, ETags/cache policy and common-cohort comparisons for 2–4 configurations. DoD: incompatible protocols/coverage return the specified typed reasons; filtered denominators and uncertainty reflect the actual cohort.

## PCB-29-3 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `implemented` (public/private route access controls and privacy projections are implemented; held-out references are redacted and released evidence is allowlisted by published content).
- Verification: `partial` (local PostgreSQL ownership/persistence integration passes; production artifact-bucket/IAM denial remains unverified).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: `docs/implementation/reports/prompt-29.md`, `docs/implementation/reports/prompt-31.md`, `docs/implementation/reports/prompt-32.md`, and their public API/browser tests.
- Acceptance criteria: — Complete administrative/API authorization, idempotency, optimistic concurrency, error taxonomy and publication/read access controls. DoD: permissions are enforced on every route/artifact; private identity probes cannot reveal hidden data or useful download tokens.

## PCB-29-4 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `implemented` (the web DTOs and API operations are generated from the checked-in FastAPI OpenAPI snapshot).
- Verification: client/server parity and local release-scoped binary artifact routes pass (`pnpm api:types:check`, runtime OpenAPI snapshot, E2E-26 artifact evidence); production IAM/bucket-policy denial remains open.
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: `docs/implementation/reports/prompt-29.md`, `apps/web/src/lib/public-api.ts`, public API schemas and `tests/test_public_api_projections.py`.
- Acceptance criteria: — Generate and validate OpenAPI/TypeScript clients and safe public response fixtures. DoD: schemas/client/server agree, and fixtures originate from real test-release generation with explicitly synthetic labels where appropriate.

## PCB-30-1 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `implemented` (shared responsive layout/navigation, release selector, URL-persisted release/language/sort state, typed API resource states and accessible table/chart components).
- Verification: `passed` for Prompt 30 page variants; full E2E-39/40 remain partial across later prompts.
- Required verification scope: Prompt 30 browser subcases of E2E-39/40; cross-page comparison/task/load variants remain with Prompts 31/32.
- Evidence: `docs/implementation/reports/prompt-30.md`; `docs/implementation/evidence/prompt-30/browser-results.json`, `leaderboard-desktop.png`, `leaderboard-mobile.png`. API metric definitions and language choices are consumed from release responses.
- Acceptance criteria: — Implement shared layout/navigation, release selection, shareable URL filter state, typed loading/error/empty states and accessible table/chart primitives. DoD: no frontend scoring formula or hardcoded language registry diverges from the API.

## PCB-30-2 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `implemented` (leaderboard displays release scope, run mode/budget, sortable API metrics, confidence intervals, coverage, pass rate, cost/latency and release limitations/notices).
- Verification: `passed` for Prompt 30 page variants; full E2E-39/40 remain partial across later prompts.
- Required verification scope: Prompt 30 missingness, interval, source-link and release-notice subcases of E2E-39/40.
- Evidence: `docs/implementation/reports/prompt-30.md`; `docs/implementation/evidence/prompt-30/browser-results.json`, `leaderboard-desktop.png`, `model-code-profile.png`. Gated zero, N/A, missing and pending review render as separate labeled states.
- Acceptance criteria: — Implement leaderboard scope/mode/budget labels, sortable metrics, confidence intervals, coverage, pass rate/cost and release notices. DoD: failed/gated zero, missing, N/A and pending-review states remain visually and semantically distinct.

## PCB-30-3 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `implemented` (language-specific leaderboard and per-configuration diagnostics, opportunity counts, declared tool coverage and tested-dimension charts).
- Verification: `passed` for Prompt 30 page variants; full E2E-39/40 remain partial across later prompts.
- Required verification scope: Prompt 30 language filter/profile and language-to-dimension evidence subcases of E2E-39/40.
- Evidence: `docs/implementation/reports/prompt-30.md`; `docs/implementation/evidence/prompt-30/browser-results.json`, `javascript-profile.png`. JavaScript uses its own release dimensions; untested dimension cells say “Not tested.”
- Acceptance criteria: — Implement language leaderboards and diagnostic profile views with opportunity counts/tool coverage. DoD: graphs do not imply evidence for untested language features or reuse inappropriate TS metrics on JS.

## PCB-30-4 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `implemented` (model profile with code-only measured-dimension radar, language/dimension heatmap, source-linked cost/latency/coverage and measured-data-only factual summary).
- Verification: `passed` for Prompt 30 page variants; full E2E-39/40 remain partial across later prompts.
- Required verification scope: Prompt 30 model-profile, source evidence and answer-only subcases of E2E-39/40.
- Evidence: `docs/implementation/reports/prompt-30.md`; `docs/implementation/evidence/prompt-30/browser-results.json`, `model-code-profile.png`, `model-answer-only.png`. Answer-only models show no code radar or invented code dimensions.
- Acceptance criteria: — Implement model profiles with code-only radar, language/dimension heatmap, generation cost/latency and factual supported summaries. DoD: answer-only tasks receive no invented code dimensions; all displayed numbers link to source scores/evidence.

## PCB-31-1 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `implemented` (`apps/web/src/app/compare/page.tsx`; API compatibility checks and exact task/card pairing in `packages/publication/src/polycodebench_publication/projections_query.py`). Requests accept two to four distinct released configurations, reject absent/mixed protocol and budget identities, and pair only task ID/version-matched public scorecards.
- Verification: `passed` for the compatible A/C same-release three-task comparison, API-to-browser decimal equality, exact scorecard identities, and A/B protocol/budget incompatibility with no numeric comparison rows.
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: `docs/implementation/reports/prompt-31.md`; `docs/implementation/evidence/prompt-31/browser-results.json`, `comparison-desktop.png`, `comparison-mobile.png`; `tests/test_public_api_prompt31.py`.
- Acceptance criteria: — Build 2–4 model comparison with compatibility feedback, common-task paired differences/intervals and configuration identities. DoD: the same visible task and release underpin code comparisons; incompatible budgets/protocols are not silently mixed.

## PCB-31-2 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `implemented` (`apps/web/src/app/tasks/`, bounded same-origin task-content handler, `LazyTaskContent`; source/diff text is escaped as inert React text, source and diff fields are each capped at 64 KiB, total task content is capped at 512,000 UTF-8 bytes, and summaries are paginated at 50 rows).
- Verification: `passed` for lazy fetch after activation, keyboard activation, inert `<script>` diff text, public JSON export, pagination, and generic not-found/private-reference probes. E2E-26's public task/export/privacy subcases pass; artifact-storage downloads and production IAM checks remain outside this slice.
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: `docs/implementation/reports/prompt-31.md`; `docs/implementation/evidence/prompt-31/browser-results.json`, `task-detail-public-evidence.png`; `tests/test_public_api_prompt31.py`.
- Acceptance criteria: — Implement bounded escaped source/diff views, task browsing, public statement/source versions, submitted patches and tool findings. DoD: uploads are inert, large payloads are lazy-loaded, and private/held-out candidates are never accidentally exposed.

## PCB-31-3 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `implemented` (`apps/web/src/app/scorecards/[scorecardId]/page.tsx`; released decimal strings, gate/missingness states, contribution weights/arithmetic, formula and metric definitions, and task/item/evidence links are shown from the API; private evidence references are redacted per task by the projection query).
- Verification: `passed` for source-value equality, gated/N/A/missing/pending-review distinction, public evidence links and redacted-reference counts.
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: `docs/implementation/reports/prompt-31.md`; `docs/implementation/evidence/prompt-31/browser-results.json`, `scorecard-contribution-chain.png`; `tests/test_public_api_prompt31.py`.
- Acceptance criteria: — Implement metric-to-task-to-item-to-evidence drilldowns with raw/gated values, effective weights, versioned formulas and redacted-private explanations. DoD: a user can reconstruct a public score from unrounded contributions within documented rounding.

## PCB-31-4 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `implemented` (`apps/web/src/app/methodology/[version]/page.tsx`; methodology requests are pinned to the selected release/version; correction history, native/adapted labels, limitations, withdrawal notice and successor links remain attached to historical release identity).
- Verification: `passed` for pinned-version mismatch rejection, withdrawn predecessor/successor display and methodology content from the original release.
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: `docs/implementation/reports/prompt-31.md`; `docs/implementation/evidence/prompt-31/browser-results.json`, `frozen-methodology.png`, `withdrawal-and-successor.png`; `tests/test_public_api_prompt31.py`.
- Acceptance criteria: — Build frozen methodology and correction/withdrawal views, native-versus-adapted labels and explicit limitations. DoD: historical URLs retain the original release identity and clearly identify successors/withdrawals.

## PCB-32-1 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `implemented` (`packages/api/src/polycodebench_api/submissions.py` validates metadata-only requests, binds verified account identity to request ownership, rejects extra secret/run-plan fields, rate-limits to five requests per hour by default, and preserves idempotent pending records; public create returns 201 and performs no run, endpoint contact, VM or model call).
- Verification: `passed` at local API/browser fixture tier; owner-only status, mismatched email, unauthenticated, rate-limit, secret-schema and pending/no-network cases pass in `tests/test_public_api_prompt32.py` and `apps/web/tests/e2e/prompt32.spec.ts`.
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: `packages/api/src/polycodebench_api/submission_routes.py`, `submissions.py`, `tests/test_public_api_prompt32.py` (5 tests), and `docs/implementation/evidence/prompt-32/browser-results.json`. No live model call or spend was made.
- Acceptance criteria: — Implement submitter identity/ownership, validated metadata, rate limits and pending/rejected/approved request states. DoD: a public request creates no model call, VM or automatic benchmark run and cannot carry provider secret values in the public schema.

## PCB-32-2 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `implemented` (review requires reviewer/admin RBAC plus MFA; endpoint registration stores a secret reference only; source permission review and approval pin an approved endpoint, concrete model/run configuration and finite budget; endpoint policy denies private/mixed DNS targets; approved requests are capped at 500 attempts).
- Verification: `passed` at local API/service and PostgreSQL integration tiers. `tests/test_public_api_prompt32.py` covers role/MFA checks, malicious unapproved endpoint non-contact, private/mixed-address denial and bounded plans; `tests/test_public_api_submissions_postgres.py::test_approved_submission_recovers_one_bounded_postgres_run` registers and approves a synthetic endpoint through the API under the restricted database login. No provider endpoint is contacted.
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: `packages/services/src/polycodebench_services/{rbac.py,model_endpoints.py,runs.py}`, `packages/api/src/polycodebench_api/{submission_routes.py,app.py,postgres_submissions.py}`, `packages/persistence/src/polycodebench_persistence/{endpoints.py,runs.py}`, `packages/persistence/sql/{provision_roles.sql,grant_permissions.sql}`, `scripts/local_stack.py`, and the PostgreSQL integration test. Hosted OIDC registration and secret-store provisioning remain Prompt 33 deployment inputs.
- Acceptance criteria: — Implement reviewer/admin endpoint/capability checks, source/permission records, secret-reference setup and explicit bounded run plans. DoD: SSRF/private-address rules hold and approval binds a concrete model/config/budget, not unlimited future evaluations.

## PCB-32-3 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `implemented` (approval is version-checked and idempotent, pins a digest of the exact endpoint/rights/run plan, creates one bounded authorized run through the existing run service, then exposes only safe owner status; run creation retains its existing budget/idempotency/audit path).
- Verification: `passed` for synthetic API and PostgreSQL lifecycle: one bounded queued run is created; an injected failure after run creation recovers through the run idempotency record; repeated approval returns the same run; exact cost/token caps and one durable audit transition are verified; foreign owners receive generic 404. An unapproved malicious URL receives no contact or run in the companion API tests.
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: `packages/api/src/polycodebench_api/{postgres_submissions.py,submission_routes.py}`, migration `a20c4e619d32_reviewed_model_submissions.py`, `packages/persistence/src/polycodebench_persistence/{runs.py,endpoints.py}`, role grants in `packages/persistence/sql/`, and `tests/test_public_api_submissions_postgres.py`. The local PostgreSQL 17.6 migration/bootstrap and approval integration passed; no live provider or spend was used.
- Acceptance criteria: — Implement authorized run creation and submitter-safe status updates after approval. DoD: existing idempotency/budget/audit controls apply; repeated approval/request cannot create duplicate spending; other users cannot read the request.

## PCB-32-4 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `implemented` (the seventh submission page uses the published release context and typed API states, authenticates through generic OIDC with PKCE and a verified email, keeps its session in an HttpOnly cookie, never exposes a bearer token to browser code or accepts provider credentials, explains review/budget boundaries and supports request, error and owner-status states; no admin dashboard was added).
- Verification: `passed` for the two Prompt 32 browser cases and full Prompt 30/31 regressions: 9 browser cases passed across 375px/1440px/1600px layouts, keyboard interaction, release-backed navigation, status/privacy/error, task evidence and lazy payload states.
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: `apps/web/src/app/model-submissions/`, same-origin bounded BFF routes in `apps/web/src/app/api/model-submissions/`, `apps/web/src/components/model-submission-form.tsx`, `apps/web/tests/e2e/prompt32.spec.ts`, and `docs/implementation/evidence/prompt-32/`. Development release fixtures are explicitly marked synthetic.
- Acceptance criteria: — Build the seventh public page and review the complete seven-page product. DoD: request/error/status states work, public/admin/submitter permissions are tested server-side, and all pages use actual release data/contracts without placeholder features.

## PCB-33-1 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `implemented` - environment-separated Terraform in `infra/terraform/` (modules network, keys, identity, database, backup, artifacts, registry, control_services, workers (wraps `infra/sandbox/aws`), performance, public_delivery, telemetry, stack; roots `environments/{staging,production}` with `allowed_account_ids` guard, separate state and AWS Budgets cap); environment manifests `config/environments/*.yaml`; identity-derived authority `polycodebench_core.deployment` + `pcb-ops identity verify` container entrypoint; IAM tags/permissions boundary, KMS and bucket policies enforce environment and role.
- Verification: `blocked` - local: `terraform fmt -check` and `terraform validate` PASS for both roots (hashicorp/terraform:1.13, AWS provider 6.36.0); Trivy IaC scan 3 findings fixed, 2 accepted with justification; `pcb-ops env validate` PASS (no shared resources); identity refusal tests PASS. Clean staging deployment NOT run: no authorized AWS account, region, budget, operator principals, AMI or image digests (staging-execution-plan.md).
- Required verification scope: E2E-42, E2E-43.
- Evidence: docs/implementation/evidence/prompt-33/env-validate.json, docs/implementation/evidence/prompt-33/doctor-staging.json, docs/implementation/evidence/prompt-33/trivy-iac-scan.json; tests/test_operations_deployment.py.
- Sandbox/identity reconciliation follow-up: deployed manifests require distinct guest lane subnets, launch templates and lane/control security groups. `pcb-ops env reconcile` now compares operator roles, database/signing/cursor secret ARNs and namespaces, VPC CIDR, region, approved guest AMI/type and those sandbox identities to Terraform outputs; secret ARNs must belong to the manifest account and region. `Ec2VmSandboxProvider` refuses shared lane launch templates. Verified with `tests/test_operations_deployment.py` (28 passed), `tests/test_sandbox.py` (12 passed, opt-in Docker case skipped), strict mypy/Ruff, and read-only staging/production Terraform validation. Evidence: `docs/implementation/evidence/prompt-33/sandbox-manifest-reconciliation-2026-10-06.json`.
- AWS solve-worker follow-up: fixed IAM-role versus STS assumed-role ARN matching; added the deployed-manifest- and `solve-supervisor`-gated EC2 worker assembly; provisioned distinct reference-only guest-control SSH secrets per supervisor role with exact IAM reads and manifest/Terraform reconciliation; guest-control key material is read into a short-lived temporary file and removed. `tests/test_sandbox.py`, `tests/test_worker_runtime.py`, `tests/test_operations_deployment.py`: 46 passed, one opt-in Docker case skipped; Ruff and strict mypy passed. Evidence: `docs/implementation/evidence/prompt-33/aws-solve-worker-assembly-2026-10-06.json`.
- Worker operations follow-up: added an STS-rechecking `ec2-run` path and idempotent `ec2-register` path, isolated ops and solve-worker images, exact worker-config IAM access, partition-aware S3 endpoint/bucket injection with manifest reconciliation, and an aggregate manifest capacity cap. Dispatch/setup remain disabled in staging/production examples; both images default to development-only configuration. 49 focused tests pass (one opt-in Docker case skipped), both Terraform roots validate, and both images build/run locally. No AWS calls, registration, launch, push or model calls were made. Evidence: `docs/implementation/evidence/prompt-33/aws-worker-cli-2026-10-06.json`. Actual deployment inputs and evaluation/judge/scorer/publisher processors remain open.
- Registration audit follow-up: `PostgresJobRepository.register_worker` can bind creation to the verified actor and transactionally records an auditable digest and bounded worker identity/slot details. The focused PostgreSQL/SeaweedFS test passed against the dedicated local test database; no cloud calls were made. Evidence: `docs/implementation/evidence/prompt-33/worker-registration-audit-2026-10-06.json`.
- Exact-claim follow-up (2026-10-07): `PostgresJobRepository.claim` accepts optional exact job, approved-run, and stage filters; local one-shot execution requires a solve-job UUID or owner-visible approved run ID, and local/AWS solve loops filter to the solve stage. Job/run misses cannot fall back to another queued job; run-scoped watch stays within the run, while watch rejects `--job-id`. Verified with 45 worker/CLI tests, three PostgreSQL claim-filter cases, one approved-submission run-scope integration case, strict mypy/Ruff, and a fresh read-only/offline solve-worker container build/smoke. No dispatch or model call occurred. Evidence: `docs/implementation/evidence/prompt-33/worker-exact-claim-2026-10-07.json`. Clean staging deployment remains unverified.
- API/CDN readiness follow-up (2026-10-07): `/readyz` checks DB/catalog dependencies and is wired to Docker/ALB health; CloudFront routes an explicit actual-public-API allowlist and disables caching for artifact requests while forwarding only release/token. Four focused tests pass; staging/production Terraform format and validation pass on Terraform 1.13.5/AWS 6.36.0; the API image builds and its health probe passes. Live local PostgreSQL readiness returned 200 with two public releases. No AWS API, plan, apply or model call. Evidence: `docs/implementation/evidence/prompt-33/public-api-readiness-routing-2026-10-07.json`.
- Acceptance criteria: — Complete environment-separated infrastructure-as-code for network/identities, database/backups, artifacts, images, control services, workers, performance capacity, secrets, signing and public delivery. DoD: clean staging deployment is reproducible; actual identity/policy enforces environment and isolation tier, not a request string.

## PCB-33-2 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `implemented` - `pcb-ops` (packages/operations): doctor, env validate/reconcile, migrate check/rehearse/upgrade (expand-only gate), workers drain, orphans sweep, artifacts collect-garbage; structured redacting JSON logs with correlation IDs and bounded metric catalog (`polycodebench_core.telemetry`) wired into the worker and `pcb-scheduler`; canonical alert rules `infra/observability/prometheus/alerts.yaml` (+ promtool tests), Grafana dashboard, AMP/CloudTrail alarms in IaC.
- Verification: `partial` - drain/stale-commit drill PASS on PostgreSQL; promtool check+test PASS (11 rules); telemetry redaction tests PASS; migration rehearsal: empty->head and previous->head PASS on the recorded committed tree; `alembic heads` has one head. Current-tree local PostgreSQL verification on 2026-10-07: `alembic check` reports no new upgrade operations at `d4f082b91c33 (head)`, and `pcb-ops migrate check` reports no violations. A fresh current-tree DB rehearsal and staging telemetry/alert delivery remain unverified.
- Required verification scope: E2E-42, E2E-43.
- Evidence: docs/implementation/evidence/prompt-33/migration-rehearsal.json, docs/implementation/evidence/prompt-33/migrate-check-working-tree.json, docs/implementation/evidence/prompt-33/migration-current-tree-check-2026-10-07.json; tests/test_operations_postgres.py, tests/test_operations_telemetry.py.
- Acceptance criteria: — Implement validated deployment/configuration, migration/rollback/drain procedures, telemetry and required alerts. DoD: expanded schemas remain compatible, stale workers cannot commit, logs/metrics expose useful run IDs without leaking secrets/held-out content.

## PCB-33-3 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `implemented` - `pcb-ops backup create`, `restore rehearse` (isolated local-docker target), `restore verify` (any restored target, used after AWS PITR), `keys rotate/revoke/verify` (publication keyring with retained keys), orphan sweep (local + EC2 ops-reaper), drill runner scripts/ops_drills.py, seed scripts/seed_ops_rehearsal.py.
- Verification: `blocked` - local variants PASS: isolated restore (113 FKs 0 orphans, 50/50 digests, 10 stratified scorecards replayed, projection rebuilt and signature-verified, measured 41.8 s and 58.8 s, resources reclaimed) with a passing negative control; orphan, withdrawal, key-rotation, drain and outage drills PASS. Staging (AWS PITR, live EC2 orphan, live outage) blocked on authorization.
- Required verification scope: E2E-42, E2E-43.
- Evidence: docs/implementation/evidence/prompt-33/e2e-42-local-restore.json, docs/implementation/evidence/prompt-33/e2e-43-local-drills.json, docs/implementation/evidence/prompt-33/orphan-dry-run-local-default.json; tests/test_operations_recovery_docker.py.
- Acceptance criteria: — Implement and execute restore/orphan/outage/withdrawal/key-rotation procedures as permitted. DoD: isolated restoration verifies referential/digest integrity, replays ten stratified scorecards, rebuilds a public projection and reports measured recovery timing; resources are reclaimed.

## PCB-33-4 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `implemented` - docs/operations: README, 12 runbooks (all T 22.7 procedures plus deployment/migration/drain), retention-and-rights-policy.md, staging-execution-plan.md (inputs, exact commands, budget), rehearsal-report-2026-10.md (restore, drills, load, security).
- Verification: `blocked` - every runbook labels commands [V-local]/[V-test] (executed) or [S] (staging, not executed); `pcb-ops alerts check` PASS (every alert has a runbook); local load rehearsal measured p95 443 ms uncached origin (target 300 ms cached p95 remains a target); staging load/security rehearsals blocked.
- Required verification scope: E2E-42, E2E-43.
- Evidence: docs/implementation/evidence/prompt-33/load-rehearsal-public-api.json; docs/operations/rehearsal-report-2026-10.md.
- Acceptance criteria: — Complete operator runbooks, retention/rights policies and documented load/security rehearsals. DoD: each runbook has exact verified commands, authorized role, expected state/result and recovery verification; unmeasured SLOs are targets, not achievements.

## PCB-34-1 - Prompt 34: — Perform the final integrated audit and repair pass

- Owner prompt: `34`.
- Dependencies: WP-01: —.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-01–43 with required variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Reconcile the current repository against both source documents, all REQ-01–14, WP-01–24 and PCB tickets. Read actual code/config/entrypoints and inspect changed source hashes. DoD: every requirement has a concrete implementation path and appropriate verification; outdated reports or placeholder-only features are identified.

## PCB-34-2 - Prompt 34: — Perform the final integrated audit and repair pass

- Owner prompt: `34`.
- Dependencies: WP-01: —.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-01–43 with required variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Verify the complete E2E-01–43 matrix and its language/family/route/environment variants. Inspect raw evidence, commands, dates/revisions and artifact identities; rerun missing/stale/risk-affected checks. DoD: no fixture masquerades as a live provider/VM test and no skipped/blocked case is counted passed. Reuse valid expensive evidence explicitly; do not automatically repeat the whole paid campaign.

## PCB-34-3 - Prompt 34: — Perform the final integrated audit and repair pass

- Owner prompt: `34`.
- Dependencies: WP-01: —.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-01–43 with required variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Trace representative complete journeys through the real entrypoints: task import/admission/freeze → run planning/budget → model/agent → frozen candidate → fresh grading → evidence/judging → score/replay → release review → public API/website. Cover both protocols, all eight language identities, every Track B family and Track A detection/repair. DoD: integrations, visibility and identity remain intact across boundaries, with synthetic journey fixtures clearly labeled where used and the actual 144-attempt pilot separately evidenced.

## PCB-34-4 - Prompt 34: — Perform the final integrated audit and repair pass

- Owner prompt: `34`.
- Dependencies: WP-01: —.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-01–43 with required variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Exercise decisive failure paths: wrong-fast code, bogus findings, novel bugs, private artifact probes, test tampering, analyzer/judge failure, ambiguous billing, stale leases, cancellation, missing coverage and approval-invalidating changes. Fix defects at their cause and rerun impacted checks. DoD: failures produce correct outcomes without score inflation, unauthorized actions or discarded evidence.

## PCB-34-5 - Prompt 34: — Perform the final integrated audit and repair pass

- Owner prompt: `34`.
- Dependencies: WP-01: —.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-01–43 with required variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Audit readiness and produce the final report bundle. DoD: native-method claims, calibration, held-out handling, source rights, real costs/unknowns, CI/variance, restore evidence and every website page agree with actual artifacts; remaining limitations are concrete and not hidden by “MVP done.”
