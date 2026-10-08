# Prompt 89 — Bounded staged retrieval and coverage-aware replay

## 1. Implemented functionality and changed files

- Added [retrieval.py](../../../packages/core/src/polycodebench_core/retrieval.py) and [retrieval.py](../../../packages/services/src/polycodebench_services/retrieval.py) with immutable stage, method, source/index, query-unit, candidate, outcome, coverage, cache-identity and replay contracts.
- Plans freeze exact/normalized → lexical/code → semantic stages, fingerprint method digests, source revision/index references, seed and the catalog's bounded limits. Query-unit digests bind the audit plan, task and full component fingerprint reference, source group and stage.
- The local selection helper accepts only already-produced hits within a frozen planned task/component/source/revision/stage scope. It deduplicates cross-stage hits, uses deterministic stage/rank/source/seed ordering, caps at 20 per source and 100 per task, and retains truncation counts and digests.
- Coverage reconciles every component/source/stage unit exactly once. `no_match` requires an attempted finite query and a result digest. Failed, truncated, blocked and unsupported outcomes cannot be reported as complete. Cache identity includes tenant and permission scope, task/component, snapshot/index/method/query, stage, seed, caps and selection-rule version.
- Replay recomputes from supplied stored candidate hits and reports missing/mismatch without any refetch path. It does not load corpus/index artifacts or persist checkpoints/cache/results.
- Added [test_retrieval.py](../../../tests/test_retrieval.py). Updated [acceptance.md](../acceptance.md), [decisions.md](../decisions.md), [implementation-ledger.md](../implementation-ledger.md) and [commands.md](../commands.md).

## 2. Tests and commands actually run

- `uv run pytest tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py -q` — **44 passed**.
- `uv run ruff check packages/core/src/polycodebench_core/retrieval.py packages/services/src/polycodebench_services/retrieval.py tests/test_retrieval.py` — passed.
- `uv run ruff format --check packages/core/src/polycodebench_core/retrieval.py packages/services/src/polycodebench_services/retrieval.py tests/test_retrieval.py` — passed.
- `uv run mypy --strict packages/core/src/polycodebench_core/retrieval.py packages/services/src/polycodebench_services/retrieval.py tests/test_retrieval.py` — passed, no issues in 3 files.
- Combined Prompt85–89 regression: `uv run pytest -q tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` — **97 passed twice**, both exit code 0. The first run printed a transient `0x8007000e` WMI/platform exception during SQLAlchemy import; an immediate rerun completed cleanly. Both runs are recorded in `commands.md`.
- No PostgreSQL, network, source, or model calls were made.

## 3. Acceptance gates

- **Partial — BX-15 / BAT-07-A:** fixed stages, stable bounded candidate selection, per-source/task truncation counts and digests are implemented and tested. There is no approved index executor or controlled recall set, so real retrieval and recall validation remain blocked.
- **Partial — BX-16 / BAT-07-B:** all component/source/stage units reconcile, and failure/truncation/unsupported cannot become a clean no-match. No source query or persistent outcome writer ran.
- **Partial — BX-16 / BAT-07-C:** cache identity binds privacy scope and selection inputs. Fenced reservation/checkpoint/result persistence and cache lookup are absent.
- **Partial — BX-17 / BAT-07-D:** selection replays deterministically from supplied candidate-hit records or reports missing/mismatch without refetch. Snapshot/index artifact verification and durable artifact retrieval are absent.
- **BWP-07: partial.** Production plans remain blocked: all source policies are unapproved/unimplemented/unverified; no approved immutable snapshot, rights/scope evidence, derived index, artifact verifier, local index runtime, or audit database URL is available. Synthetic pins in pure selector tests are algorithm fixtures only, not source authorization evidence.

## 4. Decisions and specification discrepancies

- **ADDENDUM-DECISION-20:** fixed deterministic order is stage, rank, source group and seed-derived candidate identity; cross-stage duplicates keep their earliest stage. The caps are recall-limited, and no recall quality is claimed without real indexes and a controlled set.
- **ADDENDUM-DECISION-21:** no-match means a completed finite query with zero candidates and a result digest. Replay only consumes stored hits and never re-fetches changed web content. Durable checkpoints and artifact lookup are not implemented.
- **ADDENDUM-DECISION-22:** cache keys include tenant/permission scope and all selection inputs, including seed, caps and rule version. A digest-only helper does not verify permission or prove prior query coverage.
- Prompt89 depends on approved snapshots/indexes that Prompt88 could not produce. Therefore BAT-07-A/B/D are implemented as local contracts and pure helpers; BAT-07-C has an identity contract only. No acceptance gate is marked complete based on hypothetical pins.

## 5. Exact next prompt and unblock action

Proceed to **Prompt90 / BWP-08** in order with local evidence-verification/review contracts only where independently testable. Trusted match verification against live source bytes remains blocked until an owner-approved immutable source snapshot, its rights/scope manifest, and a verified source-document artifact are available. Do not substitute synthetic candidate hits for that evidence.
