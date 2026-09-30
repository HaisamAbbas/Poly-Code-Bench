# Prompt 03 completion report

Prompt 03 / Phase 1 — DONE (Prompt 03 gate; Phase 1 aggregate pending)

## 1. Implemented functionality and changed files

- Added 42 PostgreSQL domain tables with relational constraints for tasks/configs, model revisions/endpoints, campaigns/runs/attempts/evaluations, jobs/dependencies/executions/workers/capacity, artifacts/evidence/reviews, scoring/releases, submissions, accounting, identities, idempotency and audit. Attempt and evaluation records have separate identities and lifecycles.
- Added packaged Alembic initial schema revision, including cyclic artifact/execution foreign-key ordering, immutability/version/idempotency/release/task-set/DAG guards and public release views. Downgrade refuses destructive data loss.
- Added narrow no-login database role groups and grants/RLS policies; implemented PostgreSQL database, run/identity repositories, service RBAC, role assignment/revocation, optimistic versions, append-only audit, typed safe database errors, and atomic run/attempt creation with idempotent replay/conflict behavior.
- Added actual PostgreSQL integration fixtures and `docs/implementation/reports/prompt-03.md` evidence; reconciled ticket, E2E, requirement, phase and progress ledgers; added PostgreSQL service/migration/drift checks to CI; documented migration/bootstrap commands.
- Changed/added: `.github/workflows/ci.yml`, `pyproject.toml`, `uv.lock`, `packages/core/src/polycodebench_core/application_errors.py`, `packages/persistence/{README.md,alembic.ini,pyproject.toml,sql/*,src/polycodebench_persistence/*}`, `packages/services/src/polycodebench_services/*`, `tests/test_persistence_postgres.py`, and the Prompt 03 implementation ledgers/report.

## 2. Tests/commands actually run and their results

- Fresh database on local PostgreSQL 18.4: provisioned role groups; `uv run --locked alembic -c packages/persistence/alembic.ini upgrade head` passed; grants applied; second upgrade was a no-op; `uv run --locked alembic -c packages/persistence/alembic.ini check` reported no new operations.
- `PCB_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55434/pcb_prompt03_final2_test uv run --locked --all-packages --group dev pytest -q -p no:cacheprovider` — **18 passed**, including four PostgreSQL integration tests for concurrent same-key replay, changed-payload conflict, attempt-write failure rollback, immutable writes, row-scoped DB permissions, service RBAC, role audit and stale-version rejection. Integration database was dedicated and named with `test`.
- Ruff format/lint — passed (60 files formatted); mypy across `core`, `configuration`, `persistence`, `services` and scripts — passed (29 source files); package boundary and ten-package startup checks — passed; startup and contract schema checks — passed (15 generated outputs).
- `uv build --all-packages --out-dir .cache/prompt03-build` — source and wheel builds passed for all ten packages. Inspected the persistence wheel and confirmed it contains Alembic env/template and `5c9545180d80_initial_persistence_schema.py`.
- `python docs/implementation/verify_prompt00.py` — passed with 14 requirements, 24 work packages, 43 E2E scenarios, Prompts 00–34, 142 tickets, owners/evidence and source hashes; progress JSON parse and `git diff --check` passed.
- First Alembic drift check exposed a missing cyclic FK in the draft; migration was corrected, then verified on a new empty database. Initial offline and sandboxed builds could not fetch the pinned `uv-build==0.12.15`; the previously authorized elevated PyPI retry succeeded.
- Hosted GitHub Actions, local PostgreSQL 17.6 Docker service and HTTP/API routes were not run. Docker's Linux engine is unavailable; CI is configured for the pinned PostgreSQL 17.6 service.

## 3. Acceptance gates

- **Satisfied:** PCB-03-1 through PCB-03-4 at the foundational scope described in the tickets; empty-database migration and repeat upgrade; foreign-key cycle; schema drift; atomic run/attempt transaction; same-key replay and changed-body conflict; injected partial failure rollback; immutable row/audit enforcement; RBAC and selected database grants/RLS; optimistic role-version checks. E2E-02/E2E-25 evidence is linked with exact tested scope in `e2e-matrix.md`.
- **Pending:** E2E-02 full scheduler/API/live-run variants; E2E-25 full administrative/public roles and every API/UI route. They remain `not_run`; foundational service/database tests do not close either complete scenario. No claim is made that every permission route was tested. PG17.6 hosted CI has not been dispatched. Deployment login identities/credentials must be attached through the deployment secret manager; no credentials are stored here.
- **Phase gate:** Phase 1 remains `in_progress` pending Prompts 04 and 05. Overall product, all other E2E scenarios and production gates remain pending under their owners.

## 4. Decisions or specification discrepancies recorded

- D-03-01: initial downgrade refuses destructive deletion of benchmark provenance/evidence.
- D-03-02: database roles are no-login groups; deployment-specific logins and secrets are managed externally.
- D-03-03: create both cyclic-reference tables before adding the artifact-to-execution FK; recorded initial migration drift and corrected migration.
- D-03-04: local PostgreSQL 18.4 evidence, planned PG17.6 CI target, and route/E2E scope boundaries are explicit. No normative product or scoring discrepancy found.

## 5. Exact next command or numbered prompt

Next: Prompt 04 — Implement artifact storage and visibility.
