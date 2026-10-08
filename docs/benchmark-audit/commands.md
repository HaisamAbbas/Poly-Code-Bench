# Benchmark audit command and evidence registry

Date: 2026-10-08 (Asia/Karachi). A command is marked passed only if it ran.

## Prompt83 baseline

| Command | Result | Scope |
|---|---|---|
| `uv run --offline --locked ruff check packages/core/src/polycodebench_core packages/persistence/src/polycodebench_persistence packages/services/src/polycodebench_services packages/api/src/polycodebench_api packages/orchestration/src/polycodebench_orchestration tests/test_core_contracts.py tests/test_suite_admission.py tests/test_repo_task_admission.py` | Passed: All checks passed | Existing backend and focused contract/admission paths, before audit changes. |
| `uv run --offline --locked pytest -q tests/test_core_contracts.py tests/test_suite_admission.py tests/test_repo_task_admission.py` | Passed: 37 passed in 14.69s | Fixture/unit baseline only; no live DB/source/model/human evidence. |

## Inspected project commands

- Workspace/tool configuration: `pyproject.toml`, `package.json`, `uv.lock`.
- Python lint: Ruff. Frontend lint/typecheck scripts are in root `package.json`.
- Migration entrypoint: `packages/persistence/src/polycodebench_persistence/migrations/env.py`; requires PostgreSQL via `PCB_MIGRATION_DATABASE_URL`.
- Database-backed queue/admission coverage includes `tests/test_jobs_postgres.py` and `tests/test_task_admission_postgres.py`; those were not included in this baseline.
- No benchmark audit command or audit API schema exists yet.

## Prompt84 metadata checks and scoped implementation checks

| Command/check | Result | Interpretation |
|---|---|---|
| `git ls-remote https://github.com/openai/human-eval.git HEAD` | `6d43fb980f9fee3c892a914eda09951f772ad10d` | Public repository HEAD metadata only. |
| `git ls-remote https://github.com/google-research/google-research.git HEAD` | `a1e7371c5e006f4e8b314bd23d99220d2fe44c51` | Public repository HEAD metadata only. |
| `git ls-remote https://github.com/SWE-bench/SWE-bench.git HEAD` | `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e` | Public repository HEAD metadata only. |
| `git ls-remote https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified refs/heads/main` | `78f471bf655a3137b2e8a75af1501690ec009ec3` | Dataset revision metadata only; no payload fetched. |
| `git ls-remote https://github.com/evalplus/evalplus.git refs/tags/v0.3.1` | `e5d0ed0bab96280b60b637ec7f15b5e4841b0cb2` | Release metadata; dataset payload not fetched. |
| `git ls-remote https://github.com/evalplus/evalplus.git HEAD` | `26d6d00bb1fd0fa37f39c99d5290da67891d1c5e` | Data-version adapter metadata pin. |
| `uv run --offline --locked ruff check packages/core/src/polycodebench_core/benchmark_audit_registry.py packages/services/src/polycodebench_services/benchmark_audit_catalog.py tests/test_benchmark_audit_catalog.py` | Passed: All checks passed | Changed Python files. |
| `uv run --offline --locked pytest -q tests/test_benchmark_audit_catalog.py` | Passed: 10 passed | Local behavior tests; no source/model dispatch. |
| `uv run --offline --locked mypy packages/core/src/polycodebench_core/benchmark_audit_registry.py packages/services/src/polycodebench_services/benchmark_audit_catalog.py` | Passed: no issues in 2 files | Strict type check for new modules. |
| `uv run --offline --locked python scripts/check_boundaries.py` | Failed: 14 prohibited imports/dependencies | All reported paths are under existing evaluation/orchestration modules and their package metadata; no new audit module is listed. These unrelated paths were not changed by Prompt84. |

## Prompt85 contract, persistence and queue checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run --offline --locked --all-packages pytest -q tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py tests/test_benchmark_audit_catalog.py` | Passed: 33 passed | Local schema, safety and catalog tests. No PostgreSQL/source/model/human evidence. |
| Ruff check over the 12 changed Prompt85 Python/test paths | Passed: All checks passed | Prompt85 code and focused tests. |
| Mypy over the 12 changed Prompt85 Python/test paths | Passed: no issues in 12 source files | Strict type check for Prompt85 implementation. |
| `corepack pnpm --filter @polycodebench/contracts build` | Passed | TypeScript contract package compiled. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: shared audit vectors plus existing canonical/invalid/property cases | TypeScript byte/digest agreement for all 18 shared audit vectors; kind-specific payload validation remains Python-owned. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://pcb:pcb@localhost/polycodebench'; uv run --offline --locked --all-packages alembic -c packages/persistence/alembic.ini upgrade 2a62b6001aa1:c3a4e14f8b29 --sql` | Passed: targeted Prompt85 PostgreSQL DDL rendered | Offline syntax/render only; no connection or old-row migration execution. |
| `uv run --offline --locked --all-packages alembic -c packages/persistence/alembic.ini upgrade head --sql` | Blocked by existing revision `b9e04c7a1f38` calling an online `SELECT` during offline rendering | The pre-existing revision must be adapted or an actual PostgreSQL migration database supplied to render/execute the full chain. |
| `uv run --offline --locked --all-packages pytest -q tests/test_persistence_postgres.py` | 4 skipped | Each opt-in integration case skipped because `PCB_TEST_DATABASE_URL` is unset. |
| PostgreSQL environment check | `PCB_TEST_DATABASE_URL` and `PCB_MIGRATION_DATABASE_URL` are not configured | PostgreSQL upgrade, downgrade, old-worker drain, CAS/fence and duplicate-dispatch integration checks were not run. |

## Prompt86 importer, persistence and safety checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest -q tests/test_benchmark_importers.py tests/test_benchmark_audit_catalog.py` | Passed: 24 passed | Local synthetic adapter, malformed/missing input, archive defense, sampling, visibility and catalog checks; no live source bytes. |
| Ruff check on the seven changed Prompt86 Python paths | Passed: All checks passed | Core contract, parser, persistence, migration, models and focused tests. |
| `uv run ruff format --check` on the seven changed Prompt86 Python paths | Passed: 7 files already formatted | Formatting verified after applying fixes. |
| `uv run mypy packages/core/src/polycodebench_core/benchmark_imports.py packages/services/src/polycodebench_services/benchmark_importers.py packages/persistence/src/polycodebench_persistence/benchmark_imports.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/d52a7e11b30f_benchmark_import_membership.py` | Passed: no issues in 5 source files | Strict types for new core/service/persistence/migration code. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://offline:offline@localhost/polycodebench'; uv run alembic -c packages/persistence/alembic.ini upgrade c3a4e14f8b29:d52a7e11b30f --sql` | Passed: targeted PostgreSQL DDL rendered | Offline SQL render only; PostgreSQL migration execution and rollback were not run. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `d52a7e11b30f` is the only head | Migration graph has one head. |
| `git diff --check` on Prompt86 paths | Passed | No whitespace errors; Git reported expected LF-to-CRLF normalization warnings for YAML/Markdown files. |
| PostgreSQL integration/source approval check | Blocked: no DB URLs, approved rights artifact or exact local benchmark snapshot | No import was attempted. Registry rows remain blocked. |

## Prompt87 fingerprint and privacy checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest -q tests/test_task_fingerprints.py` | Passed: 16 passed | Synthetic local text/binary fixtures test exact, normalized, shingle, privacy, bounds and explicit unsupported/config-blocked states. |
| Ruff check on the six changed Prompt87 Python paths | Passed: All checks passed | Core contract, local feature service, persistence, model/migration and tests. |
| `uv run ruff format --check` on the six changed Prompt87 Python paths | Passed: 6 files already formatted | Formatting verified after fixes. |
| `uv run mypy packages/core/src/polycodebench_core/task_fingerprints.py packages/services/src/polycodebench_services/task_fingerprinting.py packages/persistence/src/polycodebench_persistence/task_fingerprints.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/e9b30a7c1f42_private_fingerprint_artifacts.py` | Passed: no issues in 5 source files | Strict type check for fingerprint implementation and migration. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://offline:offline@localhost/polycodebench'; uv run alembic -c packages/persistence/alembic.ini upgrade d52a7e11b30f:e9b30a7c1f42 --sql` | Passed: targeted PostgreSQL DDL rendered | Offline SQL render only; migration execution, artifact upload and rollback were not run. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `e9b30a7c1f42` is the only head | Migration graph has one head. |
| PostgreSQL/model/crypto configuration check | Blocked: no DB URL, approved parser/model config or key custody | No AST, embeddings, sealed commitments or live index rebuild were attempted. |

## Prompt88 connector plans, egress bounds and coverage checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py -q` | Passed: 32 passed | Synthetic connector policies and records test URI allowlisting, source caps, authorization/credential fail-closed behavior, per-request timing/byte/retry/rate bounds, candidate-only tool metadata and coverage denominators. No network I/O. |
| `uv run mypy --strict packages/core/src/polycodebench_core/corpus_connectors.py packages/services/src/polycodebench_services/corpus_connectors.py` | Passed: no issues in 2 source files | Strict type check of new connector contract/service modules. |
| `uv run ruff check packages/core/src/polycodebench_core/corpus_connectors.py packages/services/src/polycodebench_services/corpus_connectors.py tests/test_corpus_connectors.py` | Passed: All checks passed | Prompt88 implementation and focused tests. |
| `uv run ruff format --check packages/core/src/polycodebench_core/corpus_connectors.py packages/services/src/polycodebench_services/corpus_connectors.py tests/test_corpus_connectors.py` | Passed: all 3 files formatted | Formatting verified after fixes. |
| `uv run pytest tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py -q` | Passed: 85 passed | Combined BA1 regression for connector, catalog, importer, fingerprint, immutable-document and audit-control behavior. |
| Source/credential/index availability check | Blocked: all eight policies remain `not_approved` / `not_implemented` / `not_run`; credential verifier and optional index tools are absent | No fetch, remote query, source conformance, snapshot write, Data Portraits or infini-gram request was attempted. |

## Prompt89 bounded retrieval, coverage and replay checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py -q` | Passed: 44 passed | Synthetic contracts test stage/coverage completeness, component identity, blocked-source behavior, deterministic deduplication and caps, truncation, failed/no-match distinctions, cache isolation and replay. No index or network query. |
| `uv run ruff check packages/core/src/polycodebench_core/retrieval.py packages/services/src/polycodebench_services/retrieval.py tests/test_retrieval.py` | Passed: All checks passed | Prompt89 core/service/test paths. |
| `uv run ruff format --check packages/core/src/polycodebench_core/retrieval.py packages/services/src/polycodebench_services/retrieval.py tests/test_retrieval.py` | Passed: all 3 files formatted | Formatting verified after final review. |
| `uv run mypy --strict packages/core/src/polycodebench_core/retrieval.py packages/services/src/polycodebench_services/retrieval.py tests/test_retrieval.py` | Passed: no issues in 3 source files | Strict type check for Prompt89 contracts, helpers and tests. |
| `uv run pytest -q tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` | Passed twice: 97 passed each run. First run printed a transient Windows `0x8007000e` WMI/platform exception during SQLAlchemy import; immediate rerun completed cleanly. Both exited with code 0. | Combined Prompt85–89 regression. No DB connection or source I/O. |
| Approved corpus/index and database availability | Blocked: no approved snapshot/index artifact, index runtime, or configured audit database URL | No retrieval dispatch, durable checkpoint/cache write, database migration or artifact replay lookup was attempted. |

## Prompt90 match evidence, review and correction checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest -q tests/test_match_verification.py tests/test_benchmark_audit_documents.py tests/test_retrieval.py` | Passed: 36 passed | Tests v2 evidence round-trip, v1 vector compatibility, retrieval/source/component/span binding, conservative normalization, self-source/boilerplate gates, untrusted no-tools judge packets, independent adjudication and immutable correction successors. All source/candidate fixtures are synthetic. |
| `uv run ruff check packages/core/src/polycodebench_core/canonical.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/match_verification.py packages/services/src/polycodebench_services/match_verification.py tests/test_match_verification.py` | Passed: All checks passed | Prompt90 Python core/service/test paths. |
| `uv run ruff format --check packages/core/src/polycodebench_core/canonical.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/match_verification.py packages/services/src/polycodebench_services/match_verification.py tests/test_match_verification.py` | Passed: all 5 files formatted | Formatting verified after final review. |
| `uv run mypy --strict packages/core/src/polycodebench_core/canonical.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/match_verification.py packages/services/src/polycodebench_services/match_verification.py tests/test_match_verification.py` | Passed: no issues in 5 source files | Strict type check for new schema, verifier/review contracts and tests. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: canonical v1/v2 envelope and 256 property cases | TypeScript and Python retain matching canonical key-order rules for schema versions1/2; existing shared v1 vectors remain unchanged. |
| `uv run pytest -q tests/test_match_verification.py tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` | Passed: 109 passed | Combined Prompt85–90 audit regression. No PostgreSQL, network, source artifact, human-review or model call. |
| Source, rights, reviewer/model and DB availability | Blocked: no approved corpus artifacts/rights resolver, live source reader, audit database URL, reviewer session or approved judge context/exposure writer | No live source evidence was accepted, no review/adjudication event was persisted, and no judge request was sent. |

## Prompt91 observed-risk policy and missingness checks

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest -q tests/test_risk_assessment.py tests/test_match_verification.py tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` | Passed: 121 passed | Prompt85–91 regression, including all §12 score vectors/thresholds, exact bounds, candidate/review gating, self-review and rights guards, correlated-signal caps, context/behavior separation and v2 document round trips. Source and calibration evidence are synthetic. |
| `uv run ruff check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/risk_assessment.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py tests/test_risk_assessment.py` | Passed: All checks passed | New policy, assessment and schema paths. |
| `uv run ruff format --check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/risk_assessment.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py tests/test_risk_assessment.py` | Passed: 5 files formatted | No formatter changes required. |
| `uv run mypy --strict packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/risk_assessment.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py tests/test_risk_assessment.py` | Passed: no issues in 5 source files | Strict type checking for the v2 docs, pure scorer, metadata model and migration. |
| `PCB_MIGRATION_DATABASE_URL=postgresql://offline:offline@localhost/offline uv run alembic -c packages/persistence/alembic.ini upgrade e9b30a7c1f42:1a2b3c4d5e6f --sql` | Passed: offline SQL rendered | The v1-only check is replaced by `schema_version IN (1,2)`. The placeholder URL was used only for offline rendering; no connection was attempted. |
| `PCB_MIGRATION_DATABASE_URL=postgresql://offline:offline@localhost/offline uv run alembic -c packages/persistence/alembic.ini downgrade 1a2b3c4d5e6f:e9b30a7c1f42 --sql` | Passed: offline SQL rendered | Downgrade first raises if any v2 audit document exists, then restores the v1-only check. It was not executed against PostgreSQL. |
| Calibration/source/database availability | Blocked: no independent calibration sample, approved source/rights scope or audit database connection | Score fixtures validate policy arithmetic, not source findings, calibration, migration state or live persistence. |
