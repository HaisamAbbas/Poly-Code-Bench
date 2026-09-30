# Prompt 04 completion report

## Review fixes (2026-09-30)

- Serialized finalization on the upload row before changing quota. Concurrent calls now share one committed artifact and release its byte reservation once.
- Expired finalization releases quota but leaves provisional bytes for the stated 30-day retention interval.
- Added an immutable, reviewer-written projection approval bound to the source and exact canonical payload digest. Publishing requires its ID and a distinct publisher. The database guard rejects declassification without a matching stored approval.
- Public artifact registration and declassification now commit in one database transaction. Public downloads also require a matching approval and declassification record, so raw or failed public uploads are not exposed through the service. Failed object-store writes can still leave unreferenced canonical bytes for the existing 30-day orphan collector.
- Added migrations `b5e17f2c4096` and `c6a90d17f20e`, updated grants and six artifact integration tests. Local PostgreSQL 17.6 + SeaweedFS 4.48 full suite: **24 passed**. Ruff check/format, mypy across 36 source files and Alembic schema drift check passed. Production policy and full public route checks remain pending.

Prompt 04 / Phase 1 — DONE (Prompt 04 gate; Phase 1 aggregate pending)

## 1. Implemented functionality and changed files

- Added the S3-compatible storage adapter with three distinct visibility buckets, safe internally constructed keys, conditional writes, explicit SHA-256 checksums and no ETag-as-digest behavior: `packages/persistence/src/polycodebench_persistence/object_store.py`.
- Added upload reservations, atomic byte quotas, expected size/digest finalization, independent canonical readback, immutable verified artifact registration, same-upload retry recovery, visibility-domain deduplication, manifest cycle/scope constraints, audited retention holds, post-expiry provisional cleanup and 30-day unreferenced canonical-object cleanup. In-flight uploads are excluded from orphan collection: `packages/persistence/src/polycodebench_persistence/artifacts.py`.
- Added quota/upload/retention/declassification models and migration, plus role grants for the dedicated finalizer and publisher/reviewer actions: `packages/persistence/src/polycodebench_persistence/{models.py,migrations/versions/a4f04c4f5a12_artifact_integrity_and_visibility.py,sql/provision_roles.sql,sql/grant_permissions.sql}`.
- Added role-scoped download/upload services and a strict reviewed-metadata projection writer that stores a new public object while preserving the private source: `packages/services/src/polycodebench_services/{artifacts.py,artifact_publication.py}`.
- Added real PostgreSQL + SeaweedFS integration variants and CI service setup: `tests/test_artifacts_postgres.py`, `.github/workflows/ci.yml`. Added boto3 and type stubs to `packages/persistence/pyproject.toml`, root dev dependencies and `uv.lock`.
- Updated the persistence guide and Prompt 04 ticket, requirements, E2E, decision, command, phase and progress ledgers.

## 2. Tests/commands actually run and their results

- Local PostgreSQL 17.6 migration from empty database, role provisioning/grants, repeat upgrade and `alembic check`: **PASS**, no schema drift.
- Local SeaweedFS 4.48 plus PostgreSQL integration: `uv run --locked --all-packages --group dev pytest -q -p no:cacheprovider` — **21 passed**. Repeated `tests/test_artifacts_postgres.py` on the same database — **3 passed**.
- `uv sync --locked --all-packages --group dev --offline` — **PASS**.
- Ruff format/check and mypy across 35 source files — **PASS**; package-boundary check, ten-package import/startup check and generated startup/contract schema checks — **PASS**.
- `docker compose config --quiet` — **PASS**. `uv build --all-packages --out-dir .cache/prompt04-build` — **PASS** for source distributions and wheels across all ten packages; persistence wheel contains the artifact repository, S3 adapter and migration.
- Source/ledger verification, JSON parse and `git diff --check` — **PASS**.
- Hosted GitHub Actions was not dispatched. Production object-store policy checks were not run because no production bucket, IAM principals or encryption configuration are available. Public API/page/download/export routes do not exist yet, so full E2E-26 remains pending.

## 3. Acceptance gates

- **Satisfied:** PCB-04-1 through PCB-04-4 in the local integration scope. E2E-03 passed altered-size, wrong-digest and truncated-byte rejection without a new verified artifact. E2E-26 storage variants passed role denial, hidden/public dedup separation, retry recovery, quota refusal, manifest scope/cycle rejection, projection and two-phase provisional/canonical orphan cleanup at the 30-day retention boundary; active finalizations are protected from orphan GC.
- **Pending:** Full E2E-26 across public routes/pages/downloads/exports. Production IAM/bucket policy, service identity, encryption and lifecycle validation remain unverified separately from local SeaweedFS. Stage/lease-bound one-object transfer permissions await the Prompt 07 stage-control contract. Hosted CI remains unrun.
- **Phase gate:** Phase 1 remains in progress until Prompt 05 task admission and methodology contracts are complete.

## 4. Decisions or specification discrepancies recorded

- D-04-01 maps the storage key's first segment to the encryption domain while the current artifact contract has no separate artifact-kind field.
- D-04-02 keeps uploads service-proxied until stage/lease-bound transfer identities exist; guests receive no bucket listing or broad S3 credentials.
- D-04-03 limits public export to separately reviewed allowlisted metadata projections; scorecard/release export and public routes remain future scope.
- D-04-04 records that local SeaweedFS evidence does not validate production object-store policies. No scoring or normative benchmark-method change was made.
- D-04-05 separates 24-hour upload reservation expiry/quota release from the 30-day provisional-byte retention interval.

## 5. Exact next command or numbered prompt

Next: Prompt 05 — Build task admission and freeze the methodology contracts.
