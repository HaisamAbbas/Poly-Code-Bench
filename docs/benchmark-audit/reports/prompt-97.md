# Prompt 97 / BWP-15 - Benchmark health aggregation and comparable trends

Status: **partial implementation foundations**. Contract, regression and offline migration checks pass. No live benchmark/corpus source, independent detector set, authenticated health projection or PostgreSQL integration was available.

## Implemented functionality and changed files

- Added versioned health document schema v2 while preserving historical v1 parsing and refusing new v1 writes. The health scope binds an exact benchmark snapshot, audit plan, risk policy, optional model context, sorted source snapshots, source window, selected census/sample task versions, sampling design, scan methods, private family mapping digest/artifact, and versioned provenance/freshness thresholds.
- Added strict denominator-first metrics for complete/partial/unknown/unscanned/blocked assessments, reconciled risk tiers, unique families, exact and semantic prevalence, deduplicated overlap union, contextual pre-cutoff exposure, mandatory coverage units, provenance, freshness, and mean observed risk with eligible/missing counts. Samples remain descriptive and produce no census extrapolation or benchmark-clean claim.
- Percentages and means use Decimal precision 28, half-even rounding and six-place output. Null denominators expose a reason. Detector precision, recall, false-positive and false-negative rates are stratified by relation, language, modality and source, with Wilson 95% intervals and unknown-label counts.
- Added immutable trend points and explicit discontinuity reasons for membership, sampling, policy, context, source set/window, scan method, family map, freshness/provenance definitions and metric version. Persistence ties the scope to stored plan/snapshot/policy/context and exact task assessment refs; it checks accepted match evidence against the selected task, benchmark, plan and source set, reconciles assessment/tier/mean/duplicate numerators, validates temporal/coverage references and recomputes linked trend breaks. A database constraint prevents health-document successors.
- Added migration `f67a3d91c4b2_benchmark_health_contracts.py`, one shared canonical v2 vector, focused section 18 tests, and updates to `TASKS.md`, acceptance, decision, ledger, command and BA4 phase evidence.
- Changed implementation files: `packages/core/src/polycodebench_core/benchmark_audit_documents.py`, new `packages/services/src/polycodebench_services/benchmark_health.py`, `packages/persistence/src/polycodebench_persistence/benchmark_audit.py`, `packages/persistence/src/polycodebench_persistence/models.py`, the migration above, `tests/test_benchmark_health.py`, and `tests/fixtures/contracts/benchmark-audit-vectors.json`.

## Tests and commands run

| Command/check | Result | Interpretation |
|---|---|---|
| Focused Prompt97 `tests/test_benchmark_health.py` | Passed: 10 passed | Synthetic section 18 count, percentage, family, unknown, trend and persistence-scope cases. |
| Combined Prompts85-97 audit regression | Passed: 194 passed | Local/synthetic fixtures only; no database, source or model calls. |
| Strict MyPy, Ruff and format checks on Prompt97 Python files | Passed | Health schema, service, persistence, migration, model and tests checked. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed | Python and TypeScript shared vector plus existing property cases agree. |
| Offline Alembic upgrade `e5c7b2a94d10:head` and guarded downgrade `f67a3d91c4b2:e5c7b2a94d10` | Passed | PostgreSQL SQL rendered both ways; no connection or database write. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `f67a3d91c4b2` is the only head | Migration history remains linear. |
| `git diff --check` | Passed | No whitespace errors. |

The Windows-only SQLAlchemy WMI workaround patched `platform.machine()` in-process for tests and Alembic. It did not modify product code or connect to a database.

## Acceptance gates

- **BX-41: partial.** All local section 18 Decimal/count/coverage/family/null goldens pass, including unscanned and overlap-union reconciliation. Approved live source coverage and database evidence are unavailable.
- **BX-42: partial.** Census and sample identity are explicit; sampled data are not extrapolated; mean risk shows eligible and missing denominators. No live sampled cohort or reviewed projection is available.
- **BX-43: partial.** Trend points record and validate scope discontinuities, including policy and source-window changes. Integrated authenticated projection review and live evidence are unavailable.

Native benchmark scores, code-quality weights, ranking gates and existing paired-family behavior uncertainty were not changed. BA4 remains partial because Prompts95-97 still lack live worker/source/database/reviewer evidence.

## Decisions and specification discrepancies

- Recorded decision 41: preserve denominators and cohort identity, use Decimal half-even metrics, keep family mappings private, mark scope changes, and never treat health as a cleanliness or code-quality score.
- No new Prompt97 specification discrepancy was found. The earlier Prompt93 source-input discrepancy remains: section 1.1 describes five source Markdown files but lists and hashes three.

## Next prompt

Proceed in order to **Prompt98 / BWP-16 - Private API, CLI, SDK and permission contracts**.
