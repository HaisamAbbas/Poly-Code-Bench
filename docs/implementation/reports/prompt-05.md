# Prompt 05 completion report (2026-09-30)

## Review corrections and final validation (2026-09-30)

The seven confirmed review defects are resolved. The original command results below are historical; this section records validation of the corrected implementation.

- Admission reports require all three observed variants, five reference runs, consistent execution outcomes and checks derived from those outcomes. Complete evidence is persisted by migration `e8c51d90ab73`. The CLI replays the immutable imported snapshot before freezing and rejects a report that differs from that replay.
- Production admission fails closed until a trusted worker evidence authority exists. Relabeling a JSON report as `production_worker` cannot authorize a scored or held-out task set. Local authored evidence is limited to fixture sets.
- Package identity binds the raw manifest and both complete archives. Reference/test/visible-file changes invalidate prior reports even when the manifest is unchanged. Execution consumes imported archive bytes.
- Freeze rehashes the task-set document, verifies row identity/split and rechecks its verified internal manifest. Bundle artifact IDs in the task document must equal the relational artifact references.
- Fixture solutions and expected outputs must remain hidden. Undeclared files, symlinks and junctions are rejected in every snapshot area.
- Each Docker invocation has a unique container name and is forcibly removed on timeout and completion. Unconfirmed cleanup raises an error. The actual timeout regression verified no running container remained.

Validation: **56 tests passed, no skips**, with PostgreSQL 17.6, SeaweedFS 4.48 and `PCB_TEST_DOCKER=1`. This includes seven task-admission database tests and seven runner/CLI tests. Ruff formatting (89 files), lint, mypy (45 source files), Alembic drift, pilot contracts, dependency boundaries, ten-package smoke checks, startup schema and 21 generated contract outputs passed. Shared TypeScript typecheck, build and contract tests passed using the available Node 22.23.2 runtime; the pinned Node 24 CI environment was not run.

Actual authored Docker execution was repeated and saved as `docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json`. Report digest: `sha256:2c3e44a86daad9c6e37e17c63831e2622f514b9a64cc7484fcc760fb2a4cbe64`; complete package digest: `sha256:330c9a0e95ca8b2a7f2be880e4303e5d64cdc3bcb4e196010015676696c3a2fc`. Earlier evidence files are retained as historical records.

The first sandboxed runner-test attempt failed because Windows denied access to the newly created temporary directories. The permitted elevated rerun passed, including the real Docker test. The first Corepack attempt similarly encountered a cache-directory permission denial; its elevated retry passed. Hosted CI and production worker admission remain unrun.

Prompt 05 / Phase 1 — DONE

1. Implemented functionality and changed files
   - Added safe task-package manifest loading, repository path/symlink/secret checks, allowlisted visible and hidden archives, rights/provenance metadata and earliest-exposure cutoff handling in `packages/services/src/polycodebench_services/task_packages.py`.
   - Added strict admission/task-set/output-contract models, cluster split validation and immutable task-version/task-set registration with verified artifact digest/visibility checks in core, services and PostgreSQL persistence; task and task-set manifests now require matching verified internal artifacts.
   - Added `pcb task import|validate|freeze` and `pcb taskset create|freeze`. The actual local authored-fixture path is pinned to an approved immutable Python image and records image digest, isolation profile and execution tier. Fixture evidence cannot be admitted to scored/held-out sets.
   - Added the authored package `taskpacks/admission-smoke/`, pilot contracts in `config/`, methodology and source/terms records in `docs/methodology/`, generated schemas including `TaskOutputContract`, `d7b419c0e82a_taskset_frozen_document.py`, and focused tests.
   - Prompt 05 also uses verified primary-source method descriptions: [SWE-bench harness](https://www.swebench.com/SWE-bench/api/harness/), [LiveCodeBench](https://livecodebench.github.io/), [CursorBench](https://cursor.com/blog/cursorbench), and [DeepCodeBench](https://www.qodo.ai/blog/deepcodebench-real-world-codebase-understanding-by-qa-benchmarking/). CursorBench records explicitly do not claim reproduction of private tasks.

2. Tests/commands actually run and their results
   - `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/pcb.py task validate taskpacks/admission-smoke --admission-profile admission-v1 --report docs/implementation/evidence/prompt-05-authored-fixture-admission-v2.json` — PASS, executed with local Docker access. Five reference repetitions passed identically; intended faulty variant exited 0 with mismatching output; alternative passed. The package includes a schema-validated, digest-bound output contract. Evidence digest: `sha256:268dc761dda9c094e286e987ee4358d9362182e105dac21217a53fb1d222f9ab`.
   - `uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider tests/test_task_packages.py tests/test_core_contracts.py` — PASS, 18 passed.
   - `PCB_TEST_DATABASE_URL=<local disposable PostgreSQL 17.6 test database> uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider tests/test_task_admission_postgres.py` — PASS, 1 passed: verified artifact references, immutable task version, fixture task-set freeze, audit and scored-set denial. The test uses seeded verified artifact rows; object-byte integrity was separately exercised by Prompt 04 SeaweedFS tests.
   - `PCB_MIGRATION_DATABASE_URL=<same local test database> uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev alembic -c packages/persistence/alembic.ini check` — PASS, no upgrade operations detected.
   - `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/export_contract_schemas.py --check` — PASS, 21 generated schema/OpenAPI/shared TypeScript outputs.
   - `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/validate_task_contracts.py` and `... python scripts/check_boundaries.py` — PASS.
   - `uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev ruff check <Prompt 05 changed Python files>` and corresponding `ruff format --check` — PASS.
   - `MYPYPATH=packages/core/src uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev mypy packages/core/src packages/persistence/src packages/services/src scripts` — PASS, 42 source files. A broader attempted mypy invocation including all existing tests reported typing issues in pre-existing persistence/artifact/core tests; it is not claimed as passing or required by Prompt 05.
   - The first PostgreSQL run exposed omission of the required task-set manifest artifact; implementation was corrected and the final integration run passed. An initial typed fixture run exposed use of a nonexistent runtime field; corrected to enforce the declared immutable `image_digest`, then the new report-schema-validating run passed.
   - REST/admin HTTP routes, language/evaluation workers, production VM admission, hosted CI, external benchmark data admission, human scoring calibration, and public-filter integration were not run; those entrypoints or authorized inputs do not exist yet.

3. Acceptance gates
   - Satisfied: PCB-05-1 through PCB-05-4 for the foundation and trusted authored-fixture scope; schema/import/path/privacy/date/split/task-freeze checks passed. PostgreSQL task registration is immutable and task-set freeze binds a matching internal manifest artifact. Local fixture admission is clearly tiered and cannot enter scored/held-out sets.
   - E2E-04: authored reference/faulty/alternative and reference stability subcase passed; full scenario remains `not_run` until language and evaluator variants in Prompts 10–12/17.
   - E2E-27: local cutoff/exposure/cluster split and task-set registry subcases passed; full scenario remains `not_run` until common-cohort and public filters in Prompts 16/29.
   - Prompt 05 work package gate: DONE for foundational task admission and local authored-fixture scope. Phase 1 aggregate gate: PASS; the project owner approved the unchanged v1 baseline weights on 2026-09-30 as recorded in D-05-04 and the policy file. Scoring remains inactive pending human/judge calibration, source/task rights, and production-worker admission. No scored task set is authorized yet.

4. Decisions or specification discrepancies recorded
   - D-05-01 clarifies sequencing: Prompt 05's permitted local authored-fixture runner is a prerequisite slice of WP-06. Prompt 06 must extend isolation and VM lifecycle; this does not change task acceptance or mean production isolation is verified.
   - D-05-02 records the exact local fixture image digest and bounded Docker profile; it is local development evidence only.
   - D-05-04 records owner approval of the existing baseline weights only. Scoring remains inactive pending calibration; budget authorization is separate and not granted. Benchmark-family methods are documented as native versus adapted, with private CursorBench assets unavailable and no rights claims invented.
   - The original-requirements source `Pasted markdown(5).md` remains absent as recorded in `source-manifest.json`.

5. Exact next command or numbered prompt
   - Next: Prompt 06 — Implement sandbox drivers and isolation.

## Phase 1 aggregate gate — PASS

The foundational engineering work is verified: reproducible workspace, contracts, PostgreSQL, local artifact integrity, task provenance and an actual authored-fixture freeze path. The project owner approved the specified pilot baseline, satisfying Architecture §17's policy-approval gate. The policy is frozen as a baseline but remains scoring-inactive. Production VM validation, language/evaluation workers, external task/source rights and human calibration remain prerequisites for scored use and cannot be inferred from phase completion.
