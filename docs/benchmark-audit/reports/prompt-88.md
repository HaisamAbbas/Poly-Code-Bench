# Prompt 88 — Corpus connectors, scoped indexing and optional tools

## Implemented functionality and changed files

- Added [corpus_connectors.py](../../../packages/core/src/polycodebench_core/corpus_connectors.py) with strict source capability, bounded plan, per-request observation, coverage and optional-index metadata contracts. All connector profiles are explicitly `contract_only`.
- Added [corpus_connectors.py](../../../packages/services/src/polycodebench_services/corpus_connectors.py) for the eight source groups in the current policy registry. Plans require exact allowlisted HTTPS hosts and canonical paths and enforce source request/response ceilings plus byte/time/retry/rate bounds. They also remain blocked until verified finite-scope/revision manifests, source authorization, conformance and a registered runtime exist. There is no network client or source dispatch path.
- Execution observations bind attempted requests, response bytes, elapsed time and retries to the frozen plan. Coverage separates URL metadata, acquired source content, extracted text, source dates and rights evidence. A completed request with no eligible-record denominator reports `unknown`; it cannot report complete coverage or a clean zero.
- Private remote query disclosure metadata binds recipient, tenant and payload digest, but cannot prove authorization. Queries remain blocked without a trusted verifier and append-only exposure event store. Opaque credential references also remain blocked without a credential-scope verifier.
- Added typed optional-index metadata for Data Portraits sketches/error models and infini-gram corpus/index/result positions. Results are candidate-only; the schema forbids a closed-model training-membership claim. Both tools remain unconfigured and unqueried.
- Reused Prompt85's strict `CorpusSnapshotPayload` and immutable `corpus_source`, `corpus_snapshot` and `corpus_document` persistence contracts. No new persistence adapter, extraction/index manifest writer, derived-index builder or measured rebuild plan was added because approved source snapshots and resource measurements are absent.
- Added [test_corpus_connectors.py](../../../tests/test_corpus_connectors.py), covering all eight contract profiles, host and URI rejection, source budgets, private-query and credential fail-closed behavior, per-request/total egress caps, optional-index metadata and coverage missingness.
- Updated [acceptance.md](../acceptance.md), [decisions.md](../decisions.md), [implementation-ledger.md](../implementation-ledger.md), [commands.md](../commands.md) and the BA1 phase report.

## Tests/commands actually run and results

- `uv run pytest tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py -q` — **32 passed**. Tests use local synthetic metadata only; no source was fetched or queried.
- Extended BA1 audit regression: `uv run pytest tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py -q` — **85 passed**.
- `uv run mypy --strict packages/core/src/polycodebench_core/corpus_connectors.py packages/services/src/polycodebench_services/corpus_connectors.py` — **passed**, no issues in 2 source files.
- `uv run ruff check packages/core/src/polycodebench_core/corpus_connectors.py packages/services/src/polycodebench_services/corpus_connectors.py tests/test_corpus_connectors.py` — **passed**.
- `uv run ruff format --check packages/core/src/polycodebench_core/corpus_connectors.py packages/services/src/polycodebench_services/corpus_connectors.py tests/test_corpus_connectors.py` — **passed**, all 3 files formatted.
- No PostgreSQL persistence, source conformance, Data Portraits or infini-gram query, or live network action was run.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-12 / BAT-06-A:** all eight policy groups have explicit contract profiles and bounded plans. Every source remains `not_approved` / `not_implemented` / `not_run`; no verified source scope/revision manifest, production connector or authorization/credential verifier exists.
- **Partial — BX-13 / BAT-06-B,C:** URL metadata, acquired content, extracted text, dates and rights are separate dimensions; request coverage enforces explicit denominators and reports incomplete/missing evidence. No approved Common Crawl, arXiv, Stack Exchange, Wikipedia, GitHub or Hugging Face snapshot was available for source conformance. Immutable snapshot tables are reused, but no writer or derived index/rebuild is implemented.
- **Partial — BX-14 / BAT-06-A,D:** private remote queries are denied without a trusted authorization/exposure workflow. Optional-tool records pin corpus, query, result and tool-specific error/index metadata, but no endpoint/tool is configured or queried; all hits remain candidates and cannot support a closed-model training claim.
- **Partial — BWP-06, BREQ-04, BREQ-05, BREQ-24 and BREQ-29.** No gate is complete from synthetic tests. Exact initial benchmark/GitHub/Hugging Face scopes, approved rights, source bytes, immutable source documents, extracted/indexed corpus artifacts, measured rebuild costs and live conformance remain pending.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-16:** source connector code and capability contracts do not imply live connector or corpus-scan coverage; all plans stay blocked until finite-scope/revision manifests, source authorization, a registered runtime and conformance are present.
- **ADDENDUM-DECISION-17:** caller-provided authorization digests are bindings, not verified consent or persisted exposure events. Remote private queries and unverified credential references remain blocked.
- **ADDENDUM-DECISION-18:** coverage completeness requires a known eligible denominator. URL index records, source bytes, extracted text, dates and rights evidence are distinct; unobserved coverage remains unknown.
- **ADDENDUM-DECISION-19:** Data Portraits and infini-gram metadata is candidate-only and cannot establish an undocumented model's training membership.
- No source authorization, credential, query or external tool access was provided; no attempt was made to fetch or query source services.

## Exact next command or numbered prompt

Prompt89 / BWP-07 — implement the bounded local retrieval planner and replay/coverage contracts where they can be tested independently. Actual candidate retrieval remains gated on approved immutable corpus snapshots and derived indexes.
