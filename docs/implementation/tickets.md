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
- Verification: `passed for quota release at upload expiry, provisional-byte retention through day 30 and cleanup after the retention interval; verified/held/published objects retained; distinct reviewer/publisher requirement, projection retry and unchanged private source key/visibility. Production policy validation and public routes remain pending`.
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
- Acceptance criteria: — Build frozen Rust/Cargo toolchains, offline crates and distinct regular/instrumented/performance recipes. DoD: toolchain/Cargo.lock and image digests determine evaluator identity.

## PCB-11-2 - Prompt 11: — Implement Rust support

- Owner prompt: `11`.
- Dependencies: task/sandbox contracts ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-04, E2E-15, E2E-16.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
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
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-04, E2E-15, E2E-16.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Author Rust admission fixtures and the pilot task inventory with valid/incorrect/alternative/quality-defective cases. DoD: intended faults are actually detected, valid alternative code is accepted, source/split/applicability metadata is complete.

## PCB-12-1 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-16–18; close relevant E2E-04 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement fresh grading environments, base/candidate validation, immutable acceptance overlays and expected test inventories. DoD: candidate edits or printed fake successes cannot replace authoritative acceptance; missing mandatory tests never count as passes.

## PCB-12-2 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-16–18; close relevant E2E-04 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement analyzer execution/parsing contracts and normalized observations with raw report references, tool/rule/advisory versions and explicit failure semantics. DoD: empty reports, crashes and unsupported checks cannot become perfect scores.

## PCB-12-3 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-16–18; close relevant E2E-04 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement baseline-to-candidate relations, semantic issue identity and reviewed cross-tool deduplication. DoD: one underlying issue has one composite owner; unchanged unrelated debt is visible without unjustified blame; ambiguous mappings remain reviewable.

## PCB-12-4 - Prompt 12: — Implement independent grading and normalized evidence

- Owner prompt: `12`.
- Dependencies: solve/Python/Rust/scheduler ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-16–18; close relevant E2E-04 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement weighted robustness scenarios, seeded property/fuzz evidence and applicability plans, then complete Python/Rust functional/quality fixture integration. DoD: task-hard requirements gate correctness while optional quality scenarios remain separately weighted; actual fixture results validate those distinctions.

## PCB-13-1 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-19, E2E-20.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement PerformancePlan validation, exclusive capacity/hardware matching, reference identity and output verification. DoD: candidate/reference use the same physical worker/allocation class, frozen workload/flags and equivalent runtime; instrumented builds cannot enter the speed lane.

## PCB-13-2 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-19, E2E-20.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement randomized paired order, specified warmup/iteration counts, cold/steady-state modes and whole-process-tree memory recording. DoD: every iteration and input/environment identity is preserved, with compile time separately reported.

## PCB-13-3 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-19, E2E-20.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement canaries, frozen stability thresholds, bounded block retries and first-valid-block selection. DoD: noise invalidates the affected block consistently; the fastest rerun is never cherry-picked.

## PCB-13-4 - Prompt 13: — Implement performance measurement

- Owner prompt: `13`.
- Dependencies: sandbox/evaluation ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-19, E2E-20.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement workload aggregation, ratio floors, weighted geometric means, variance and censored-timeout handling. DoD: a lower bound is not reported as an exact duration; the documented efficiency transform has golden checks and does not claim proof of Big-O.

## PCB-14-1 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-21, E2E-22.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build anonymized evidence packets and fixed, versioned rubric/panel definitions. DoD: candidate identity/rank/cost are withheld, citations must exist, judge has no execution tools, and candidate comments are untrusted data.

## PCB-14-2 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-21, E2E-22.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement three logical votes, schema validation, fixed bounded invalid-vote recovery and averaging. DoD: every delivery is retained; fewer than three valid required votes cannot produce a ready result; low scores are not discarded as retries.

## PCB-14-3 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-21, E2E-22.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement disagreement triggers, reviewer access/decisions and immutable supersession. DoD: overrides cite evidence/anchors/reason and preserve original votes; changing the panel requires a new evaluation/cohort version.

## PCB-14-4 - Prompt 14: — Implement judging, review and calibration

- Owner prompt: `14`.
- Dependencies: gateway/evidence ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-21, E2E-22.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement calibration import/evaluation/reporting and build the disjoint labeled-packet workflow specified in T §15.3. DoD: actual qualified human labels, agreement/confusion metrics and audit selection are recorded; absent labels or judge access remain blocked instead of becoming invented reviews.

## PCB-15-1 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-23, E2E-24.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement evidence completeness, pass/fail/unknown/N/A semantics and correctness gating. DoD: failed code has zero applicable quality contributions; missing required evidence cannot become either a zero failure or a perfect score.

## PCB-15-2 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-23, E2E-24.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement exact decimal composites, applicability redistribution, security penalties, quality/idiom/robustness rubrics and efficiency inputs. DoD: nominal/effective weights and item contributions explain every result; diagnostic language profiles do not double-count composite penalties.

## PCB-15-3 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-23, E2E-24.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Enforce canonical issue/evidence ownership, immutable scorecard identity and traceable contribution chains. DoD: reordered equivalent evidence yields identical output and duplicate findings cannot change a score.

## PCB-15-4 - Prompt 15: — Implement deterministic scoring and replay

- Owner prompt: `15`.
- Dependencies: typed validated evidence and applicable evaluator outputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-23, E2E-24.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement clean-process score replay and score/schema version handling through the CLI/API. DoD: archived validated evidence reproduces the same canonical scorecard without requesting another candidate or judge.

## PCB-16-1 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-28–30.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement metric definitions, fixed cohort identity, sample/task/stratum/language aggregation, failure denominators and coverage. DoD: missing languages/tasks are not silently renormalized; conditional-on-pass metrics are separately labeled; filters use a common eligible cohort.

## PCB-16-2 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-28–30.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement fixed-seed clustered/hierarchical bootstrap and paired comparisons. DoD: correlated variants remain in their cluster, uncertainty is recomputed for the actual metric, replicate/seed/method evidence exists, and unstable/insufficient samples are labeled.

## PCB-16-3 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-28–30.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement draft/validate/review/approve/publish/withdraw states and versioned corrections. DoD: approval binds exact content; a change invalidates approval; published historical results never mutate in place.

## PCB-16-4 - Prompt 16: — Implement aggregation and release publication

- Owner prompt: `16`.
- Dependencies: completed scorecards ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-28–30.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement allowlisted projections, manifest signing and atomic current-pointer updates. DoD: incomplete/unsafe projections cannot publish, pointer races conflict, and private evidence is never made public through a bulk export.

## PCB-17-1 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Complete and freeze at least 12 independent Python clusters and 12 independent Rust clusters with validated reference/faulty/alternative solutions, required quality opportunities, rights, hidden bundles and admission evidence. DoD: no placeholder tasks or unresolved required analyzers enter the pilot.

## PCB-17-2 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Freeze two distinct real model identities/configurations, one compatible protocol, three planned samples per task and a common distinct judge panel. Produce exact run plans, resolved capabilities/prices, resource needs, active budgets and exposure policy. DoD: the plan accounts for 24 × 2 × 3 = 144 attempts; outstanding credentials/authorization/human calibration are concrete blockers, not guessed values.

## PCB-17-3 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Execute the bounded pilot using existing authorization or obtain only the missing concrete authorization after preparation. DoD: all attempts have genuine provider/sandbox/evidence lineage; model failures count; infrastructure failures follow the specified retry/missingness policy; no best-answer selection.

## PCB-17-4 - Prompt 17: — Run and verify the real Python/Rust pilot

- Owner prompt: `17`.
- Dependencies: WP-05–16 integrated and required live/human inputs ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-31 and integrated earlier variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Produce the internal exploratory release/report and replay sampled plus required scorecards. DoD: dimensions, coverage, cost/latency, intervals/limitations, tool/judge versions, failure/exclusion ledger and evidence links agree with the actual runs.

## PCB-18-1 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted pilot ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-32–34.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement historical/pre-fix, disclosed-security and mutation task-source workflows with reproducible defects, provenance, immutable oracles, clean controls and rejected equivalent mutations. DoD: Python/Rust historical and injected tasks run, publicly disclosed security coverage has verified examples or a clearly blocked source requirement, and no hidden injection log enters visible assets.

## PCB-18-2 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted pilot ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-32–34.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement findings parsing/span validation, causal matching, semantic duplicates and one-to-one accepted matches. DoD: file coincidence is insufficient; TP/FP/FN and localization use the specified rules; unresolved genuinely novel findings are not automatic false positives.

## PCB-18-3 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted pilot ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-32–34.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement root-cause/severity rubrics, human adjudication, ground-truth version updates and cohort-wide rematching. DoD: accepted new bugs update all affected results consistently; original decisions/votes remain auditable.

## PCB-18-4 - Prompt 18: — Implement Track A bug hunting and repair

- Owner prompt: `18`.
- Dependencies: accepted pilot ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-32–34.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement final combined-patch grading and Track A aggregates, including source/language balance and clean-control handling. DoD: good detection with bad patch retains detection but gets repair zero; no-op clean controls do not earn empty repair credit; multilingual headline follows equal-language aggregation.

## PCB-19-1 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build pinned Node/package-manager/runtime/test images with offline dependencies and locked recipes. DoD: dependency/advisory snapshots and test-runner identity are recorded and no online installation occurs during scored execution.

## PCB-19-2 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement build/test/symbol/analysis plans, ESLint, applicable security/dependency checks and strict TypeScript checks where the task requires them. DoD: JavaScript is not penalized for lacking TypeScript types; task-specific strictness passes the reference.

## PCB-19-3 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement async/error/concurrency/typing/idiom applicability and ownership mappings. DoD: floating promises and real async errors have evidence, unused concurrency opportunities are N/A, and stylistic modern syntax is not an automatic bonus.

## PCB-19-4 - Prompt 19: — Add JavaScript and TypeScript support

- Owner prompt: `19`.
- Dependencies: accepted pilot/plugin contracts ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit runnable JS/TS fixture tasks and run the shared language/evaluation conformance suite. DoD: valid, wrong, alternative-valid, security/async/type-defective and timeout fixtures produce the intended evidence through real entrypoints.

## PCB-20-1 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build pinned compiler/standard/dependency recipes and separate release/instrumented images. DoD: flags and hardware identities are frozen; sanitizer/Valgrind timing never masquerades as release performance.

## PCB-20-2 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement compilation, tests, clang-tidy/cppcheck and applicable ASan/UBSan/Valgrind plans/parsers. DoD: build errors, sanitizer findings, unsupported checks and infrastructure failures are classified distinctly.

## PCB-20-3 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement ownership/error-checking/portability/UB/memory profile mappings. DoD: baseline warning debt and task-specific warning policy are respected; blanket -Werror does not silently invalidate otherwise admitted legacy tasks.

## PCB-20-4 - Prompt 20: — Add C support

- Owner prompt: `20`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 plus instrumented/runtime cases.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit real conformance fixtures for correct/alternative code, wrong output, bounds/UB/resource defects and timeouts. DoD: instrumentation detects intended executed defects while reports acknowledge coverage limits; no blanket claim of proven memory safety.

## PCB-21-1 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement pinned task-specific C++ standard/compiler/build/test recipes with separate performance and sanitizer profiles. DoD: incompatible instrumentation combinations are rejected and reference/alternative builds use the same contract.

## PCB-21-2 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Integrate selected clang-tidy/cppcheck rules and applicable ASan/UBSan/TSan checks with normalized output. DoD: actual findings/crashes/unsupported paths retain their correct semantics; no analyzer omission silently raises scores.

## PCB-21-3 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement RAII/ownership, STL/container, move/value-semantics and modern-feature applicability with single composite ownership. DoD: nonowning raw pointers or justified legacy patterns are not automatically failures; measured copies and API choices are not blindly double-penalized.

## PCB-21-4 - Prompt 21: — Add C++ support

- Owner prompt: `21`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit runnable fixtures for valid alternatives, ownership/exception/resource/concurrency defects and timeouts. DoD: shared extension checks pass and expected evidence reaches the ordinary scorer/replay path.

## PCB-22-1 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build pinned Go/module/vendor/test and release recipes with offline execution. DoD: runtime/dependency/flag identity is reproducible and language registration is data-driven.

## PCB-22-2 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Integrate go test, gofmt checking, vet, staticcheck, gosec and applicable race-enabled runs. DoD: race/instrumented results cannot enter performance measurements; checker crashes cannot look like clean output.

## PCB-22-3 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement error/interface/stdlib/context/concurrency diagnostic and orthogonal idiom mappings. DoD: nonconcurrent tasks mark concurrency N/A and lifecycle/cancellation findings have concrete contract/evidence ownership.

## PCB-22-4 - Prompt 22: — Add Go support

- Owner prompt: `22`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit fixtures exercising valid/alternative code, ignored errors, cancellation/lifecycle faults, wrong answers and limits. DoD: scenarios detect their intended behavior under a frozen repetition policy and clean fixtures are not falsely penalized.

## PCB-23-1 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build pinned JDK/Maven-or-Gradle/JUnit recipes, offline dependencies and frozen JIT/performance policy. DoD: declared cold/steady-state modes and warmup do not adapt to favor individual candidates.

## PCB-23-2 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Integrate SpotBugs, PMD, Checkstyle, dependency analysis and task-specific security/resource/concurrency probes. DoD: analyzer coverage, failure semantics and baseline deltas use shared contracts.

## PCB-23-3 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement Java profiles and admit valid/alternative/null/resource/concurrency/security/wrong-output fixtures. DoD: streams/records/SOLID terminology do not earn automatic points; behavioral and contextual evidence determines results.

## PCB-23-4 - Prompt 23: — Add Java and close language coverage

- Owner prompt: `23`.
- Dependencies: WP-19: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-15, E2E-35 for all required languages.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Audit Python, Rust, JS, TS, C, C++, Go and Java end to end through plugin registration, task admission, solve output contracts, grading, scoring, replay and capability metadata. DoD: every required language has actual conformance evidence, separate JS/TS semantics, and no missing core path hidden by a capability label.

## PCB-24-1 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement SuiteAdapter import/validation for supported SWE-bench-style/native task records, immutable repo snapshots and patch output. DoD: source revisions/terms/protocol differences are recorded and no future fixes/hidden tests leak into solving.

## PCB-24-2 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Wrap the pinned upstream evaluator rather than loosely recreating its result from a generic test fraction. DoD: native fail-to-pass/pass-to-pass outcomes and resolution metric are preserved separately from PolyCodeBench acceptance and quality.

## PCB-24-3 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Bind cache/upstream run identity to task, candidate and evaluator digests and integrate protected grading overlays. DoD: one candidate cannot receive another candidate's cached grade and edits to visible tests cannot replace native/hidden acceptance.

## PCB-24-4 - Prompt 24: — Implement repository repair benchmark adapters

- Owner prompt: `24`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit native-compatible and deliberately adapted/ported fixtures through the actual harness. DoD: their methodology labels differ correctly; patch correctness and applicable quality evidence are traceable to the same frozen candidate.

## PCB-25-1 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: see prompt-specific verification.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement task authoring/import for developer requests spanning real files/modules, with repo conventions, allowed changes and acceptance contracts. DoD: multiple valid implementations can succeed; hidden requirements are not improvised after seeing a candidate.

## PCB-25-2 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: see prompt-specific verification.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement executable acceptance plus bounded rubric items for genuinely non-executable criteria, using existing judge/reviewer services. DoD: all criteria have frozen evidence methods and required-gate status; judgments cannot override failed mandatory tests.

## PCB-25-3 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: see prompt-specific verification.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Integrate patch/workspace artifacts and applicable quality profiles with baseline-aware evidence. DoD: repository-wide legacy debt does not become a candidate penalty and unchanged files are not scored as new code.

## PCB-25-4 - Prompt 25: — Implement realistic repository tasks

- Owner prompt: `25`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: see prompt-specific verification.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit realistic multi-file fixtures and document the Cursor-inspired methodology boundary. DoD: no claim of private CursorBench task access/exact reproduction or false statement that its approach ignores quality/efficiency.

## PCB-26-1 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-37.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement initial/repair-round state, allowed public feedback and fixed round/budget limits. DoD: every round retains its candidate, prompt, public feedback and cost; the protocol fixes when rounds stop.

## PCB-26-2 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-37.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement final candidate selection without hidden-result access. DoD: final quality evaluates the protocol-selected artifact, never the best hidden-scoring round; initial/final native correctness and cumulative cost remain distinct.

## PCB-26-3 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-37.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Integrate durable checkpoints and infrastructure retries at round boundaries. DoD: recovering infrastructure does not grant additional repair rounds or erase spent budget.

## PCB-26-4 - Prompt 26: — Implement self-repair

- Owner prompt: `26`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-37.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Add admitted self-repair fixtures and native-versus-adapted methodology records. DoD: visible feedback can drive the permitted repair, while hidden outcomes cannot cause another model call.

## PCB-27-1 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement pinned repo/question inputs, read/search-only solving and structured answers/citations. DoD: citations reference the base snapshot; editing is disabled for this protocol; retrieval context and truncation are logged.

## PCB-27-2 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement atomic-fact oracles, accepted paraphrases and fixed entailment judging with preserved native adapter aggregation. DoD: missing facts get no credit, repeated facts add no credit and empty answers have fact recall zero.

## PCB-27-3 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement citation validity, grounding and unsupported/contradicted-claim diagnostics with explicit unknown states. DoD: incomplete claim verification is not zero hallucinations; prose does not receive invented security/runtime/idiom scores.

## PCB-27-4 - Prompt 27: — Implement repository understanding and factual Q&A

- Owner prompt: `27`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-38 Q&A variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit cross-file Q&A fixtures with verifying code spans, alternative correct wording, wrong citations and contradictions. DoD: source/method records accurately describe DeepCodeBench-inspired or native compatibility and all fact evidence is versioned.

## PCB-28-1 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36–38.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement code-execution/output-prediction inputs and protocol restrictions. DoD: the evaluated model cannot run the target code when the declared task measures prediction without execution; a different tool policy is a different cohort.

## PCB-28-2 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36–38.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement test-output-prediction tasks and explicit exact_bytes/normalized_text/typed_json grading. DoD: normalization rules are frozen, parse errors are wrong answers, and judges do not rescue deterministic mismatches.

## PCB-28-3 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36–38.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Admit prediction fixtures and wire native/ported/inspired methodology plus answer-only metric definitions. DoD: generated-code dimensions stay N/A and output/error types are handled consistently through reports/API schemas.

## PCB-28-4 - Prompt 28: — Implement prediction suites and close Track B coverage

- Owner prompt: `28`.
- Dependencies: WP-20: WP-17.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-36–38.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Audit codegen, repository repair, realistic repo tasks, self-repair, repo Q&A, output prediction and test prediction through actual entrypoints. DoD: all have source records, output contracts, allowed tools/feedback, grading, missingness and reproducible evidence; close WP-20 only when every required family passes.

## PCB-29-1 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement release, leaderboard, model, language, comparison, public task, scorecard, artifact and methodology endpoints with typed metric definitions. DoD: responses come from actual published projections, never worker/hidden tables or hardcoded demo arrays.

## PCB-29-2 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement release/filter-bound pagination, stable sorting, decimal serialization, ETags/cache policy and common-cohort comparisons for 2–4 configurations. DoD: incompatible protocols/coverage return the specified typed reasons; filtered denominators and uncertainty reflect the actual cohort.

## PCB-29-3 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Complete administrative/API authorization, idempotency, optimistic concurrency, error taxonomy and publication/read access controls. DoD: permissions are enforced on every route/artifact; private identity probes cannot reveal hidden data or useful download tokens.

## PCB-29-4 - Prompt 29: — Implement the complete public API and projections

- Owner prompt: `29`.
- Dependencies: WP-16,18,19,20 complete ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-25/26/28/39 API variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Generate and validate OpenAPI/TypeScript clients and safe public response fixtures. DoD: schemas/client/server agree, and fixtures originate from real test-release generation with explicitly synthetic labels where appropriate.

## PCB-30-1 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-39/40 page variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement shared layout/navigation, release selection, shareable URL filter state, typed loading/error/empty states and accessible table/chart primitives. DoD: no frontend scoring formula or hardcoded language registry diverges from the API.

## PCB-30-2 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-39/40 page variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement leaderboard scope/mode/budget labels, sortable metrics, confidence intervals, coverage, pass rate/cost and release notices. DoD: failed/gated zero, missing, N/A and pending-review states remain visually and semantically distinct.

## PCB-30-3 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-39/40 page variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement language leaderboards and diagnostic profile views with opportunity counts/tool coverage. DoD: graphs do not imply evidence for untested language features or reuse inappropriate TS metrics on JS.

## PCB-30-4 - Prompt 30: — Build leaderboard, language and model pages

- Owner prompt: `30`.
- Dependencies: public API/client ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-39/40 page variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement model profiles with code-only radar, language/dimension heatmap, generation cost/latency and factual supported summaries. DoD: answer-only tasks receive no invented code dimensions; all displayed numbers link to source scores/evidence.

## PCB-31-1 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build 2–4 model comparison with compatibility feedback, common-task paired differences/intervals and configuration identities. DoD: the same visible task and release underpin code comparisons; incompatible budgets/protocols are not silently mixed.

## PCB-31-2 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement bounded escaped source/diff views, task browsing, public statement/source versions, submitted patches and tool findings. DoD: uploads are inert, large payloads are lazy-loaded, and private/held-out candidates are never accidentally exposed.

## PCB-31-3 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement metric-to-task-to-item-to-evidence drilldowns with raw/gated values, effective weights, versioned formulas and redacted-private explanations. DoD: a user can reconstruct a public score from unrounded contributions within documented rounding.

## PCB-31-4 - Prompt 31: — Build comparison, task explorer and methodology pages

- Owner prompt: `31`.
- Dependencies: first pages/API ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-26, E2E-39, E2E-40.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build frozen methodology and correction/withdrawal views, native-versus-adapted labels and explicit limitations. DoD: historical URLs retain the original release identity and clearly identify successors/withdrawals.

## PCB-32-1 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement submitter identity/ownership, validated metadata, rate limits and pending/rejected/approved request states. DoD: a public request creates no model call, VM or automatic benchmark run and cannot carry provider secret values in the public schema.

## PCB-32-2 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement reviewer/admin endpoint/capability checks, source/permission records, secret-reference setup and explicit bounded run plans. DoD: SSRF/private-address rules hold and approval binds a concrete model/config/budget, not unlimited future evaluations.

## PCB-32-3 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement authorized run creation and submitter-safe status updates after approval. DoD: existing idempotency/budget/audit controls apply; repeated approval/request cannot create duplicate spending; other users cannot read the request.

## PCB-32-4 - Prompt 32: — Implement reviewed model submissions and close the public product phase

- Owner prompt: `32`.
- Dependencies: WP-22: WP-21; WP-23: WP-08, WP-21.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-41; close E2E-25/26/39/40 variants.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Build the seventh public page and review the complete seven-page product. DoD: request/error/status states work, public/admin/submitter permissions are tested server-side, and all pages use actual release data/contracts without placeholder features.

## PCB-33-1 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-42, E2E-43.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Complete environment-separated infrastructure-as-code for network/identities, database/backups, artifacts, images, control services, workers, performance capacity, secrets, signing and public delivery. DoD: clean staging deployment is reproducible; actual identity/policy enforces environment and isolation tier, not a request string.

## PCB-33-2 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-42, E2E-43.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement validated deployment/configuration, migration/rollback/drain procedures, telemetry and required alerts. DoD: expanded schemas remain compatible, stale workers cannot commit, logs/metrics expose useful run IDs without leaking secrets/held-out content.

## PCB-33-3 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-42, E2E-43.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
- Acceptance criteria: — Implement and execute restore/orphan/outage/withdrawal/key-rotation procedures as permitted. DoD: isolated restoration verifies referential/digest integrity, replays ten stratified scorecards, rebuilds a public projection and reports measured recovery timing; resources are reclaimed.

## PCB-33-4 - Prompt 33: — Harden deployment and rehearse operations

- Owner prompt: `33`.
- Dependencies: all public-product/engine modules ·.
- Implementation: `not_started` (no application source observed).
- Verification: `not_run` (no application implementation to verify).
- Required verification scope: E2E-42, E2E-43.
- Evidence: no implementation or acceptance evidence observed in the pre-Prompt-00 workspace; future evidence path/command is not yet established.
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
