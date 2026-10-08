# Prompt84 — Benchmark catalog, source policies and capability planning

## Implemented functionality and changed files
Added strict registry, source-policy, capability and resource-plan contracts in `packages/core/src/polycodebench_core/benchmark_audit_registry.py`; duplicate-key-rejecting safe YAML loading and a deterministic no-dispatch planner in `packages/services/src/polycodebench_services/benchmark_audit_catalog.py`; and four versioned config files under `config/benchmark-audit/`. The catalog covers all 25 §5 benchmark rows, all eight source groups and every benchmark/source capability row. Added official metadata observations, planning assumptions and behavioral tests.

The planner enforces 300 tasks per plan, up to eight sources/five stages, no more than 20 candidate slots per source/task and 100 per task, a 12,000 query-unit ceiling, and a provisional 512 MiB local storage ceiling. A 300-item/eight-source/five-stage dry run reports 12,000 query units and 30,000 candidate slots, zero model calls, null monetary cost and `dispatch_allowed=false`. Unapproved sources and benchmarks produce explicit blockers. The storage ceiling is not a measured campaign estimate.

## Tests/commands actually run and results
- `uv run --offline --locked ruff check packages/core/src/polycodebench_core/benchmark_audit_registry.py packages/services/src/polycodebench_services/benchmark_audit_catalog.py tests/test_benchmark_audit_catalog.py` — passed.
- `uv run --offline --locked pytest -q tests/test_benchmark_audit_catalog.py` — 10 passed.
- `uv run --offline --locked mypy packages/core/src/polycodebench_core/benchmark_audit_registry.py packages/services/src/polycodebench_services/benchmark_audit_catalog.py` — passed, no issues in 2 files.
- `uv run --offline --locked python scripts/check_boundaries.py` — failed on 14 prohibited imports/dependencies in existing evaluation/orchestration paths; none of the reported paths is in the Prompt84 diff.
- Read-only metadata checks pinned repository/dataset references for HumanEval, MBPP, SWE-bench, SWE-bench Verified and EvalPlus. No benchmark bytes or harnesses were downloaded or run.

## Acceptance gates satisfied, pending and blocked
- Satisfied: BX-02 and BAT-02-A/B/C/D. Evidence: `config/benchmark-audit/`, `source-observations-2026-10-08.md`, `resource-planning.md`, and this report.
- BWP-02 is complete as a catalog and dry-run planning slice.
- Pending overall requirements: BREQ-01, BREQ-02, BREQ-04 and BREQ-29 still need importers, actual connectors, indexing and conformance evidence in later prompts.
- Blocked for live work: all eight source policies remain `not_approved`/`not_implemented`; exact rights and source snapshots are absent; gated GPQA and private owner data remain inaccessible. Prices and corpus sizes are unknown and keep the plan blocked. No source or model calls were made.

## Decisions or specification discrepancies recorded
- ADDENDUM-DECISION-05: registry/catalog state never implies import or audit conformance.
- ADDENDUM-DECISION-06: source prices and corpus sizes remain null/unknown instead of inferred.
- ADDENDUM-DECISION-07: the 512 MiB storage cap is a provisional local guard with no workload measurement.
- Primary-page observations are in `source-observations-2026-10-08.md`; AIME uses the MAA competition page and stays year-specific. The source specification remains unchanged.

## Exact next command or numbered prompt
Implement Prompt85 / BWP-03: strict canonical audit schemas, audit persistence and exclusive six-scope queue migration. First reconcile actual `stage_job` constraints and repository callers; the current baseline has only attempt, evaluation and release scopes.
