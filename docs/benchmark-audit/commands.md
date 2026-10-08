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

## Prompt92 temporal eligibility and timestamp commitments

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_audit_temporal.py','tests/test_risk_assessment.py','tests/test_match_verification.py','tests/test_retrieval.py','tests/test_corpus_connectors.py','tests/test_benchmark_audit_catalog.py','tests/test_benchmark_importers.py','tests/test_task_fingerprints.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py']))"` | Passed: 140 passed | Combined Prompt85-92 audit regression. The in-process platform probe bypass avoids a Windows WMI exception from SQLAlchemy; no product code is patched and no database connection is made. Fixtures are local/synthetic. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: canonical v1/v2 envelope and 256 property cases | Shared vectors now include model_context v1 and temporal_assessment v2; Python and TypeScript canonical bytes/digests agree. |
| `uv run ruff check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/audit_temporal.py packages/services/src/polycodebench_services/audit_temporal.py tests/test_audit_temporal.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/92a10b7c6d5e_model_context_document_kind.py` | Passed: All checks passed | Prompt92 implementation, migration and focused tests. |
| `uv run ruff format --check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/audit_temporal.py packages/services/src/polycodebench_services/audit_temporal.py tests/test_audit_temporal.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/92a10b7c6d5e_model_context_document_kind.py` | Passed: 8 files already formatted | Formatting verified after final review. |
| `uv run mypy packages/core/src/polycodebench_core/audit_temporal.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/audit_temporal.py tests/test_audit_temporal.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/92a10b7c6d5e_model_context_document_kind.py` | Passed: no issues in 6 source files | Repository mypy configuration is strict. |
| `uv lock --check --offline` | Passed: resolved 138 packages | Service dependency lock is current; unrelated existing task-generation lock entries remain outside the Prompt92 commit. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql://offline:offline@localhost/offline'; .\\.venv\\Scripts\\python.exe -c "import platform; platform.machine=lambda: 'AMD64'; from alembic.config import main; main()" -c packages/persistence/alembic.ini upgrade 1a2b3c4d5e6f:92a10b7c6d5e --sql` | Passed: targeted PostgreSQL DDL rendered | Correctly drops/recreates `ck_audit_document_kind`; placeholder URL used only for offline rendering. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql://offline:offline@localhost/offline'; .\\.venv\\Scripts\\python.exe -c "import platform; platform.machine=lambda: 'AMD64'; from alembic.config import main; main()" -c packages/persistence/alembic.ini downgrade 92a10b7c6d5e:1a2b3c4d5e6f --sql` | Passed: guarded downgrade SQL rendered | The downgrade blocks while model_context rows exist, then restores the previous kind check; not executed against PostgreSQL. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `92a10b7c6d5e` is the only head | Migration graph remains linear. |
| Approved model/source/TSA/database availability | Blocked: no approved source/model evidence, timestamp authority adapter/trust roots or audit DB URL | No live source/model request, external receipt verification or database write/migration was attempted. |

## Prompt93 sealed evaluations, access and canaries

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['tests/test_sealed_evaluations.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py','-q']))"` | Passed: 37 passed | Focused encryption, tamper, authorization, append-before-disclosure, rotation, canary and shared-document regressions. `platform.machine()` was patched only in-process to avoid the SQLAlchemy Windows WMI exception; no database connection was made. |
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_audit_temporal.py','tests/test_risk_assessment.py','tests/test_match_verification.py','tests/test_retrieval.py','tests/test_corpus_connectors.py','tests/test_benchmark_audit_catalog.py','tests/test_benchmark_importers.py','tests/test_task_fingerprints.py','tests/test_sealed_evaluations.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py']))"` | Passed: 154 passed | Combined Prompt85-93 audit regression; local/synthetic fixtures only, no database/source/model calls. |
| `uv run ruff check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/sealed_evaluations.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/93b11c2d7e4f_sealed_access_and_canary_documents.py tests/test_sealed_evaluations.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` | Passed: All checks passed | Prompt93 core, service, persistence, migration and test paths. |
| `uv run ruff format --check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/sealed_evaluations.py packages/persistence/src/polycodebench_persistence/migrations/versions/93b11c2d7e4f_sealed_access_and_canary_documents.py tests/test_sealed_evaluations.py` | Passed: 4 files already formatted | Formatting check for the changed core/service contract, migration and sealed-evaluation tests. |
| `uv run mypy --strict packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/sealed_evaluations.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py` | Passed: no issues in 3 source files | Strict type check for the sealed document transitions, encryption/access service and persistence repository. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: canonical v1/v2 envelope and 256 property cases | Shared canary policy/observation vectors now use private envelope-encrypted marker media types; TypeScript and Python canonical bytes/digests agree. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `93b11c2d7e4f` is the only head | Migration graph remains linear. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://offline:offline@localhost/polycodebench'; uv run alembic -c packages/persistence/alembic.ini upgrade 92a10b7c6d5e:head --sql | Out-Null` | Passed: targeted PostgreSQL DDL rendered | Adds access/canary kinds and a partial unique index enforcing one sealed-manifest successor. The placeholder URL was used for offline rendering only; no database connection or execution occurred. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://offline:offline@localhost/polycodebench'; uv run alembic -c packages/persistence/alembic.ini downgrade 93b11c2d7e4f:92a10b7c6d5e --sql | Out-Null` | Passed: guarded downgrade SQL rendered | Refuses downgrade while access-event or canary-observation rows exist, then removes the successor index and restores the previous kind check. Not executed against PostgreSQL. |
| Approved KMS, authorization, source review and PostgreSQL integration availability | Blocked: no production KMS/role verifier, audit database URL, approved source snapshot or independent review evidence | The only included key provider is explicitly local-development-only. Canary tests use synthetic data; no remote/model/source calls or database writes occurred. |

## Prompt94 behavioral diagnostics and applicability

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_behavioral_diagnostics.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py']))"` | Passed: 31 passed | Focused method applicability, frozen-plan, all-unit, missingness, retry-budget, family-power, denied-access and private-response checks. Fixtures are synthetic. |
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_audit_temporal.py','tests/test_risk_assessment.py','tests/test_match_verification.py','tests/test_retrieval.py','tests/test_corpus_connectors.py','tests/test_benchmark_audit_catalog.py','tests/test_benchmark_importers.py','tests/test_task_fingerprints.py','tests/test_sealed_evaluations.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py','tests/test_behavioral_diagnostics.py']))"` | Passed: 162 passed | Combined Prompt85–94 audit regression. No database, source, or model call. |
| `uv run ruff check` on Prompt94 core, service, persistence, model metadata, migration and test files | Passed: All checks passed | Changed Python implementation and tests. |
| `uv run ruff format --check` on Prompt94 core, service, persistence, model metadata, migration and test files | Passed: 8 files already formatted | Formatting verification after final review. |
| `uv run mypy --strict packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/behavioral_diagnostics.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py tests/test_behavioral_diagnostics.py` | Passed: no issues in 4 source files | Strict behavioral contract, applicability, persistence and focused-test typing. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: TypeScript canonical v1/v2 envelope and property checks | Shared canonical envelope behavior remains stable with the additional Python vectors. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://offline:offline@localhost/polycodebench'; .\.venv\Scripts\python.exe -c "import platform; platform.machine=lambda: 'AMD64'; from alembic.config import main; main()" -c packages/persistence/alembic.ini upgrade 93b11c2d7e4f:head --sql | Out-Null` | Passed: offline PostgreSQL DDL rendered | Adds behavioral document kinds, a unique root planned observation-unit index and a linear immutable-successor index; no connection or execution occurred. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql+psycopg://offline:offline@localhost/polycodebench'; .\.venv\Scripts\python.exe -c "import platform; platform.machine=lambda: 'AMD64'; from alembic.config import main; main()" -c packages/persistence/alembic.ini downgrade a194d6c3e781:93b11c2d7e4f --sql | Out-Null` | Passed: guarded offline downgrade rendered | Refuses removal while behavioral documents or plan-v2 rows exist. No database execution occurred. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `a194d6c3e781` is the only head | Migration graph remains linear. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: TypeScript canonical v1/v2 envelopes and 256 property cases | Shared canonical vectors remain cross-runtime consistent. |
| `uv lock --check --offline` | Passed: 138 packages resolved | No new dependency was required. |
| Approved method/model/source/training/database availability | Blocked | No executable pinned ConStat adapter, approved stable model contexts, exposure writer/KMS, audit database, accepted live control review or authorized controlled-training evidence is configured; no model request was made. |


## Prompt95 firewall admission and replacements

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_benchmark_firewall.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py']))"` | Passed: 34 passed | Pure decision and strict contract/persistence-metadata tests use synthetic data. No database/source/model access. |
| Ruff check on Prompt95 implementation, migration and tests | Passed: all checks passed | Changed Python implementation and tests. |
| Ruff format check on Prompt95 implementation, migration and tests | Passed: 9 files already formatted | Changed Python implementation and tests. |
| `uv run mypy --strict` on Prompt95 contracts, service, persistence and tests | Passed: no issues in 8 source files | Strict static typing. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed: TypeScript canonical v1/v2 envelopes and 256 property cases | Shared canonical vectors remain cross-runtime consistent. |
| Combined Prompt85-95 audit regression | Passed: 173 passed | Local synthetic fixtures only; no database/source/model calls. |
| `git diff --check` | Passed | No whitespace errors. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `d4f7b2a196c3` is the only head | Migration graph remains linear. |
| Offline Alembic upgrade `a194d6c3e781:head` and guarded downgrade `d4f7b2a196c3:a194d6c3e781` with placeholder PostgreSQL URL | Passed: SQL rendered | No live connection or database execution. |
| `uv lock --check --offline` | Passed: 138 packages resolved | No new dependency was required. |
| Production worker/source/reviewer/PostgreSQL availability | Blocked | No trusted production worker report, rights-authorized corpus, human role verifier, generation runner/cost ledger or integration DB is configured. No external or database operation was attempted. |


## Prompt96 continuous monitoring and owner alerts

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run python -c "import platform,sys; platform.machine=lambda: 'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_benchmark_monitoring.py','tests/test_benchmark_audit_documents.py','tests/test_benchmark_audit_controls.py']))"` | Passed: 34 passed | Synthetic timezone/DST, bounded catch-up/retry, finite refresh, strict alert-scope and schema checks; no DB/source/model access. |
| Combined Prompts85-96 audit regression | Passed: 184 passed | Local and synthetic audit tests; no live database, source or model calls. |
| Ruff check and format check on Prompt96 core, service, persistence, migration and tests | Passed | Changed Python files are clean and formatted. |
| `uv run mypy --strict` on Prompt96 core/service/persistence/models/tests | Passed: no issues in 6 files | Strict type check covers the policy/scheduler contracts and durable repository paths. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed | Shared Python/TypeScript audit-alert canonical vector and existing 256 property cases agree. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `e5c7b2a94d10` is the only head | Prompt96 migration history is linear. |
| Offline Alembic upgrade `d4f7b2a196c3:head` and guarded downgrade `e5c7b2a94d10:d4f7b2a196c3` with a placeholder PostgreSQL URL | Passed: SQL rendered both ways | Downgrade refuses to remove monitor policy v2, alert, slot, source-reservation or inbox evidence. No database was contacted. |
| `git diff --check` | Passed | No whitespace errors. |
| Trusted owner roles, live scheduler/connectors, authenticated inbox and PostgreSQL integration | Blocked | No live monitoring, source/model request or database write occurred. |

## Prompt97 benchmark health and comparable trends

| Command/check | Result | Interpretation |
|---|---|---|
| Focused `tests/test_benchmark_health.py` | Passed: 10 passed | Decimal goldens, unknown/unscanned reconciliation, family grouping, detector intervals, scope discontinuities and persistence-scope checks use synthetic data. |
| Combined Prompt85-97 audit regression | Passed: 194 passed | Local/synthetic audit tests only; no live database, source or model calls. |
| Ruff check and format check on Prompt97 core, service, persistence, migration and tests | Passed | Changed Python files are clean and formatted. |
| Strict MyPy on Prompt97 core/service/persistence/models/tests | Passed | Strict type check covers the health contracts, aggregation and persistence paths. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed | Python/TypeScript canonical vector and existing property cases agree. |
| Offline Alembic upgrade `e5c7b2a94d10:head` and guarded downgrade `f67a3d91c4b2:e5c7b2a94d10` | Passed: PostgreSQL SQL rendered both ways | Append-only health-kind constraint renders; downgrade guard retains stored health evidence. No database was contacted. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `f67a3d91c4b2` is the only head | Migration history remains linear. |
| `git diff --check` | Passed | No whitespace errors. |
| Approved live source, PostgreSQL, independent detector labels and reviewed projection | Blocked | None was available; fixtures/offline SQL do not count as live evidence. |

## Prompt98 private API, CLI and generated SDK

| Command/check | Result | Interpretation |
|---|---|---|
| `uv run pytest tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_api_oidc_bff_auth.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py tests/test_benchmark_audit_catalog.py tests/test_sealed_evaluations.py tests/test_benchmark_firewall.py tests/test_benchmark_monitoring.py tests/test_benchmark_health.py tests/test_public_api_openapi.py tests/test_public_api_prompt32.py -q` | Passed: 117 passed in 9.30s | Local synthetic regression; includes auth/tenant, duplicate JSON keys, CLI exits, public projection, and legacy submission lifecycle. No live database/source/model call. |
| `uv run ruff check packages/api/src/polycodebench_api/auth.py packages/api/src/polycodebench_api/context.py packages/api/src/polycodebench_api/benchmark_audit_access.py packages/api/src/polycodebench_api/benchmark_audit_routes.py packages/api/src/polycodebench_api/audit_cli.py packages/api/src/polycodebench_api/app.py packages/api/src/polycodebench_api/submission_routes.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/b7c3e9a4d281_audit_document_tenant_scope.py tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_api_oidc_bff_auth.py` | Passed | Prompt98 Python implementation, migration and tests. |
| `uv run ruff format --check packages/api/src/polycodebench_api/auth.py packages/api/src/polycodebench_api/context.py packages/api/src/polycodebench_api/benchmark_audit_access.py packages/api/src/polycodebench_api/benchmark_audit_routes.py packages/api/src/polycodebench_api/audit_cli.py packages/api/src/polycodebench_api/app.py packages/api/src/polycodebench_api/submission_routes.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/b7c3e9a4d281_audit_document_tenant_scope.py tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_api_oidc_bff_auth.py` | Passed: 13 files formatted | Format verification after the final Python edits. |
| `uv run mypy --strict packages/api/src/polycodebench_api/auth.py packages/api/src/polycodebench_api/context.py packages/api/src/polycodebench_api/benchmark_audit_access.py packages/api/src/polycodebench_api/benchmark_audit_routes.py packages/api/src/polycodebench_api/audit_cli.py packages/api/src/polycodebench_api/app.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py` | Passed: 8 source files | Strict API/persistence typing. |
| `uv run python scripts/export_public_api_openapi.py` | Passed: generated one REST OpenAPI snapshot | Exported the route schemas. |
| `corepack pnpm --filter @polycodebench/web api:types:check` | Passed | Generated TypeScript contracts match the OpenAPI snapshot. |
| `uv run --project packages/api pcb audit --help` | Passed | Package-installed CLI entry point lists all supported command families. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `b7c3e9a4d281` is the only head | Migration graph is linear. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql://offline:offline@127.0.0.1:5432/polycodebench'; uv run alembic -c packages/persistence/alembic.ini upgrade f67a3d91c4b2:head --sql` | Passed: offline SQL rendered | Placeholder URL only; no connection or database execution. |
| `$env:PCB_MIGRATION_DATABASE_URL='postgresql://offline:offline@127.0.0.1:5432/polycodebench'; uv run alembic -c packages/persistence/alembic.ini downgrade b7c3e9a4d281:f67a3d91c4b2 --sql` | Passed: guarded offline SQL rendered | Downgrade refuses persisted tenant-bound rows; no database execution. |
| `git diff --check` | Passed | No whitespace errors. |
| Live database/source/model/signer/reviewer/browser availability | Blocked | No approved runtime credentials or evidence source was available; none was contacted. |

## Prompt99 public dashboard and evidence journeys

| Command/check | Result | Interpretation |
|---|---|---|
| `corepack pnpm --filter @polycodebench/web typecheck` | Passed: `tsc --noEmit` | TypeScript checks for the public report, lookup form and curator boundary. |
| `corepack pnpm --filter @polycodebench/web lint` | Passed | ESLint for the web app. |
| `corepack pnpm --filter @polycodebench/web test:e2e:prompt99` | Passed: 4 scenarios | Synthetic Playwright coverage for keyboard/mobile report lookup, delayed loading state, invalid projection filtering, and blocked curator access with no private request. The API fixture is not live evidence. |
| `corepack pnpm --filter @polycodebench/web build` | Passed | Production build includes `/audit-reports`, `/audit-reports/[reportId]`, and `/benchmark-audit`. |
| `uv run --locked ruff check apps/web/tests/e2e/launch-prompt99-api.py` and `uv run --locked ruff format --check apps/web/tests/e2e/launch-prompt99-api.py` | Passed | Python fixture is lint-clean and formatted. |
| Live reviewed projection, tenant-bound curator workflow, revoked report lifecycle, database/source/reviewer evidence | Blocked | No live service, reviewer ACL, lifecycle writer, source snapshot or revocation state is available. No external operation was attempted. |
