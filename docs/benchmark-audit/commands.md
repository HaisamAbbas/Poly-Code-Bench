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
