# Prompt 91 — Explainable index, eight signals and missingness gates

## 1. Implemented functionality and changed files

- Added v2 `risk_policy` and `risk_assessment` document contracts in [benchmark_audit_documents.py](../../../packages/core/src/polycodebench_core/benchmark_audit_documents.py), preserving v1 documents and vectors. Policies pin `observed-risk-v1`, its weights, thresholds, signal descriptors, applicability, calibration state and no-probability claim rule. Assessments retain component values, evidence/configuration/time references, coverage, reasons, bounds, context and accepted evidence references.
- Added [risk_assessment.py](../../../packages/services/src/polycodebench_services/risk_assessment.py), a pure Decimal scorer for `50*M + 25*C + 15*E + 10*L`. It computes exact six-place scores, combines correlated signals by maximum, produces conservative missingness bounds, withholds low/medium tiers unless scope is complete and the frozen policy is marked calibrated, and permits high observed lower-bound wording at 60 or above.
- Typed all eight signals. Publication age carries an interval in days; popularity carries a timestamped count; both are context only. Model familiarity remains a separate diagnostic. Corpus/source overlap, public exposure, substantive duplication/synthetic similarity and corroborated leakage reports have separate score roles. Positive scored evidence requires evidence and config refs, observation time, verified source/approved rights claims and an independent human reviewer identity.
- Updated [models.py](../../../packages/persistence/src/polycodebench_persistence/models.py) and added migration [1a2b3c4d5e6f_audit_document_schema_v2.py](../../../packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py) so the audit document table can store versions 1 and 2. Downgrade refuses while v2 rows remain.
- Added [test_risk_assessment.py](../../../tests/test_risk_assessment.py), plus acceptance, decisions, command and ledger updates. Added [phase-BA2.md](../phase-BA2.md) with aggregate Prompt89–91 gate evidence.

## 2. Tests and commands actually run

- `uv run pytest -q tests/test_risk_assessment.py tests/test_match_verification.py tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` — **121 passed**. This covers the §12 score vectors (100, 65, 45, 0, 65–90 partial, and 0–50 measured-only), thresholds 25/60, family/concept behavior, correlated caps, candidate/review gates, no-match versus failed scope, self-review/rights/interval validation, and v2 round trips.
- `uv run ruff check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/risk_assessment.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py tests/test_risk_assessment.py` — passed, all checks passed.
- `uv run ruff format --check packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/risk_assessment.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py tests/test_risk_assessment.py` — passed; all five files are formatted.
- `uv run mypy --strict packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/services/src/polycodebench_services/risk_assessment.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/1a2b3c4d5e6f_audit_document_schema_v2.py tests/test_risk_assessment.py` — passed, no issues in five source files.
- With `PCB_MIGRATION_DATABASE_URL=postgresql://offline:offline@localhost/offline`, `uv run alembic -c packages/persistence/alembic.ini upgrade e9b30a7c1f42:1a2b3c4d5e6f --sql` and `uv run alembic -c packages/persistence/alembic.ini downgrade 1a2b3c4d5e6f:e9b30a7c1f42 --sql` — both rendered successfully. The placeholder URL did not connect to PostgreSQL.

## 3. Acceptance gates

- **Partial — BX-22:** formula, exact decimal arithmetic, goldens, thresholds and correlated-group maximum behavior pass synthetic tests. Independent detector/reviewer calibration is unavailable.
- **Partial — BX-23:** incomplete, blocked, truncated, unmeasured and review-pending components remain missing and contribute full policy weight to the upper bound. Failed scope does not become zero; low/medium tiers require complete scope and a calibrated policy. No approved source scope was run.
- **Partial — BX-24:** all eight signal descriptors are present and exposure, corpus overlap, context and behavioral diagnostics remain separate. No source bytes, approved rights, popularity feed or controlled behavioral results were available.
- **Partial — BAT-09-A–D / BWP-09:** contracts, pure aggregation, v2 canonical parsing and the offline schema migration are implemented. There is no trusted evidence-reference resolver, persistent assessment writer, API/report integration, actual calibration or live PostgreSQL verification.

## 4. Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-27:** keep the four fixed M/C/E/L groups, max correlated contributions and heuristic-only language; publication age/popularity are contextual and model familiarity is diagnostic.
- **ADDENDUM-DECISION-28:** missing components add their full group weight to the upper bound. A complete finite scope may record zero; low/medium need calibration and complete scope; a high lower bound retains bounds and a coverage qualifier.
- **ADDENDUM-DECISION-29:** the scorer consumes already accepted immutable signal records but does not resolve evidence refs. The current Prompt90 verifier cannot establish trusted source/rights acceptance, so current live findings remain unavailable.
- **ADDENDUM-DECISION-30:** v2 storage uses a reversible forward migration with a downgrade data guard. SQL rendering is verified; PostgreSQL execution is not.

## 5. Exact next command or numbered prompt

Proceed in order to **Prompt92 / BWP-10**: implement temporal holdout and model-context contracts while preserving unknown cutoff and source chronology as explicit uncertainty. No actual model or source exposure claim is available in this environment.
