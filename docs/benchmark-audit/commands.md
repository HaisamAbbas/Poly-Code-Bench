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
