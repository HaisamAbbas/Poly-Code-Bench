# Phase BA1 — Imports, fingerprints and corpus connector foundations

## Implemented functionality and changed files

- Prompt86 added pinned local import plans/adapters, deterministic sample membership, private artifact persistence and source/lineage controls. The adapters remain fixture-conformant only; see [prompt-86.md](reports/prompt-86.md).
- Prompt87 added exact, conservative normalized-text and literal-preserving lexical fingerprints with private feature artifacts and guarded append-only persistence. Parser, embedding, entity and commitment methods remain blocked without approved configuration; see [prompt-87.md](reports/prompt-87.md).
- Prompt88 added contract-only capabilities for all eight corpus source groups; bounded plans, per-attempt egress observations and explicit metadata/content/text/date/rights coverage; private-query and credential fail-closed behavior; and candidate-only Data Portraits/infini-gram metadata; see [prompt-88.md](reports/prompt-88.md).
- BA1 reuses the existing immutable audit document and corpus tables. No live source connector, approved source snapshot, corpus index, parser worker, external query tool or new persistence writer was added in this phase.
- The evidence ledger is maintained in [acceptance.md](acceptance.md), [decisions.md](decisions.md), [implementation-ledger.md](implementation-ledger.md) and [commands.md](commands.md).

## Tests/commands actually run and results

- Prompt86 importer/catalog fixtures: `uv run pytest -q tests/test_benchmark_importers.py tests/test_benchmark_audit_catalog.py` — **24 passed**. Strict Mypy and Ruff checks passed for the changed paths; targeted Alembic SQL rendered offline. No source fetch or database execution occurred.
- Prompt87 fingerprint fixtures: `uv run pytest -q tests/test_task_fingerprints.py` — **16 passed**. Strict Mypy/Ruff checks and targeted offline Alembic SQL rendering passed. No benchmark source, parser, embedding or live index was used.
- Prompt88 connectors/catalog: `uv run pytest tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py -q` — **32 passed**. Strict Mypy and Ruff checks passed. No source request, external index query or DB write occurred.
- Combined BA1 regression after Prompt88: `uv run pytest tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py -q` — **85 passed**.
- PostgreSQL integration and source approvals remain unavailable; the earlier Prompt85 phase report records the existing full-chain offline migration limitation.

## Acceptance gates satisfied, pending and blocked

- **BX-06–08 — partial:** pinned plans, deterministic membership, safe local parsers, source/lineage and missingness behavior are fixture-tested. Actual imports, approved bytes/rights and database retry behavior remain pending; parser worker isolation is absent.
- **BX-09–11 — partial:** local exact/normalized/shingle distinctions and private-artifact constraints are tested. Approved Python/Java parser and embedding config, entity/reasoning extraction, source-wide coverage, live persistence and index rebuild are unavailable.
- **BX-12–14 — partial:** all eight source contract profiles are explicit, egress/coverage bounds and remote-query fail-closed rules are tested, and optional-index metadata stays candidate-only. No source-specific live conformance, snapshot/index writes, authorization/exposure verifier or tool query was exercised.
- **BWP-04–06 — partial.** No BA1 gate is complete based on fixture substitution. Source bytes/rights, PostgreSQL execution, tenant isolation, resource-isolated parsers and a measured corpus/index workflow remain open prerequisites.

## Decisions or specification discrepancies recorded

- Prompt86 decisions **ADDENDUM-DECISION-13** and **ADDENDUM-GAP-05** preserve the distinction between upstream visibility and private storage, and identify the local JSONL-only SWE-bench Verified import format.
- Prompt87 decisions **ADDENDUM-DECISION-14** and **ADDENDUM-DECISION-15** keep normalization conservative and parser/model/commitment methods gated on approved configuration.
- Prompt88 decisions **ADDENDUM-DECISION-16–19** keep connector plans non-dispatchable, treat authorization digests as unverified metadata, require explicit coverage denominators and prevent optional index hits from claiming closed-model training membership.
- Source approval, actual benchmark/corpus bytes, PostgreSQL credentials, independent review and parser/model configuration were not available; no external source or model was contacted.

## Exact next command or numbered prompt

Prompt89 / BWP-07 — implement a bounded local retrieval planner and replay/coverage contracts only where they can be independently tested. Actual retrieval depends on approved immutable snapshots and derived indexes, which this phase did not create.
