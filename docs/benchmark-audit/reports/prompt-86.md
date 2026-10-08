# Prompt 86 — Immutable benchmark imports and initial code adapters

## Implemented functionality and changed files

- Added strict, immutable import plans, components, 100-item memberships, results, source exposure, dates and explicit family lineage in [benchmark_imports.py](../../../packages/core/src/polycodebench_core/benchmark_imports.py). Plans pin the HumanEval, MBPP and SWE-bench Verified revisions, allowed split and variant, parser configuration, source digest and deterministic sample; official harness execution is structurally disabled.
- Added bounded local-only adapters in [benchmark_importers.py](../../../packages/services/src/polycodebench_services/benchmark_importers.py) for HumanEval JSONL, MBPP original/sanitized JSON and JSONL, and a local SWE-bench Verified JSONL export. They preserve original record bytes and extract prompt/solution/tests or repository/issue/patch components separately. No network, shell, official harness, dataset script or task code is invoked. Missing, malformed, duplicate and blocked members remain in their frozen membership positions.
- Added atomic import persistence and artifact verification in [benchmark_imports.py](../../../packages/persistence/src/polycodebench_persistence/benchmark_imports.py). Imported external tasks do not receive fake native `task_version` references. Raw source, rights evidence, item records, components and lineage use verified artifact references; imported benchmark storage is private or restricted, independently of upstream source visibility.
- Added [d52a7e11b30f_benchmark_import_membership.py](../../../packages/persistence/src/polycodebench_persistence/migrations/versions/d52a7e11b30f_benchmark_import_membership.py), membership and lineage tables/constraints/triggers in [models.py](../../../packages/persistence/src/polycodebench_persistence/models.py), and least-privilege grants in [grant_permissions.sql](../../../packages/persistence/sql/grant_permissions.sql).
- Updated the HumanEval, MBPP and SWE-bench Verified catalog/capability entries in [registry-v1.yaml](../../../config/benchmark-audit/registry-v1.yaml) and [capability-matrix-v1.yaml](../../../config/benchmark-audit/capability-matrix-v1.yaml). Adapters are fixture-conformant, while real imports remain blocked by rights and source approvals.
- Added focused tests in [test_benchmark_importers.py](../../../tests/test_benchmark_importers.py) and extended [test_benchmark_audit_catalog.py](../../../tests/test_benchmark_audit_catalog.py). Updated the implementation ledger, acceptance ledger, decisions and command registry.

## Tests/commands actually run and results

- `uv run pytest -q tests/test_benchmark_importers.py tests/test_benchmark_audit_catalog.py` — **24 passed**. Synthetic local fixtures only; no official benchmark payload was fetched or imported.
- Ruff check on the seven changed Python files — **passed**. Ruff formatting was applied to the importer test; final format check is recorded in `commands.md`.
- `uv run mypy packages/core/src/polycodebench_core/benchmark_imports.py packages/services/src/polycodebench_services/benchmark_importers.py packages/persistence/src/polycodebench_persistence/benchmark_imports.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/d52a7e11b30f_benchmark_import_membership.py` — **passed, 5 files checked**.
- `PCB_MIGRATION_DATABASE_URL=postgresql+psycopg://offline:offline@localhost/polycodebench uv run alembic -c packages/persistence/alembic.ini upgrade c3a4e14f8b29:d52a7e11b30f --sql` — **passed**, targeted PostgreSQL DDL rendered, including import/lineage constraints. This is offline rendering only, not execution against PostgreSQL.
- `uv run alembic -c packages/persistence/alembic.ini heads` — **passed**, one head: `d52a7e11b30f`.
- No PostgreSQL URL, approved benchmark bytes, rights approval artifacts, parser worker isolation, live source import, harness execution or model call was available or used.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-06 / BAT-04-A:** pinned plan, variants, split rules, config digest, reproducible 100-ID sample, retained source records and semantic retry checks are implemented. Database idempotency and immutable trigger behavior have not been exercised against PostgreSQL.
- **Partial — BX-07 / BAT-04-C:** local parsers bound source/member/record/component/archive size and reject unsafe archive paths, links, duplicate names, unsupported compression, expansion bombs and malformed content. SSRF is excluded by design because there is no fetch path. Rights and missing inputs produce explicit blocked results. A scoped, resource-isolated parser worker is not implemented; actual rights-approved source inputs are also unavailable.
- **Partial — BX-08 / BAT-04-B,D:** adapters preserve split components, source-date precision/raw invalid values, family links and public source exposure; official self-source is explicitly ineligible as independent duplicate evidence. Missing and invalid members remain in the 100-item denominator. No live source bytes or reviewed lineage evidence exists.
- **Partial — BWP-04, BREQ-01, BREQ-02, BREQ-18 and BREQ-24.** The importer does not implement derived benchmark versions or establish score comparability. Storage visibility and source visibility are separate, but live tenant isolation and audit logging were not exercised.
- No gate is complete from synthetic fixtures. PostgreSQL integration, real-source conformance and rights approval remain prerequisites for live import.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-13:** benchmark source exposure and retained-object visibility are separate controls. Upstream-public text is still stored private/restricted by default; no public object storage is permitted by this importer.
- **ADDENDUM-GAP-05:** the SWE-bench Verified adapter accepts an approved local JSONL export, not the pinned dataset's native Parquet payload. Conversion must be separately versioned, rights-approved and bound into the local source manifest before importing. This path does not fetch the Hugging Face dataset or run dataset scripts.
- Parser bounds are implemented in-process. The prompt's scoped parser-worker isolation remains unsatisfied and must be added before processing untrusted live snapshots.
- The registry remains `blocked` for all three sources because source bytes and item-level rights approval are absent. Repository/dataset revision metadata and fixture success do not grant import permission.

## Exact next command or numbered prompt

Prompt87 / BWP-05 can proceed on independent local exact/lexical fingerprints. Keep embeddings and semantic features blocked until an approved, pinned local embedding/parser configuration is available; do not start corpus queries or claim semantic coverage.
