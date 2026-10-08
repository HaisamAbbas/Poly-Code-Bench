# Prompt105 — Integrated end-to-end demonstration and reviewed projections

## Implemented functionality and changed files

- Added `tests/test_benchmark_audit_lifecycle.py`. It runs a deterministic, synthetic GSM8K parse through private fingerprinting, bounded fixture candidate selection, exact byte/span verification, the proposed review ledger, missingness-aware risk scoring, temporal eligibility, and benchmark health aggregation.
- The integration stops at the true trust boundary: match content is intact but source and rights trust remain unverified; the review ledger has no human opinions; the score remains `insufficient_evidence` with a null index; temporal status remains `mutable_model_context`; and health has no low/medium/high tiers. The test does not create persisted audit history or a public report.
- Rechecked existing API, CLI, public projection, service, historical scoring, and browser contracts. Changed tracking/evidence files: `TASKS.md`, `docs/benchmark-audit/acceptance.md`, `commands.md`, `decisions.md`, `implementation-ledger.md`, `reports/phase-BA7.md`, this report, and the new lifecycle integration test.
- The synthetic importer plan includes a fixture-only rights field solely to exercise parsing. No official source row or rights evidence was used. The public health preview remains the internal metadata-only 25-family scope report; it is not human-reviewed or approved for publication.

## Tests/commands actually run and results

- `uv run --locked pytest -q tests/test_benchmark_audit_lifecycle.py` — **1 passed**.
- `uv run --locked ruff check tests/test_benchmark_audit_lifecycle.py` — **passed**; `uv run --locked ruff format --check tests/test_benchmark_audit_lifecycle.py` — **passed**; `uv run --locked mypy tests/test_benchmark_audit_lifecycle.py` — **passed, no issues**.
- `uv run --locked pytest -q tests/test_benchmark_audit_lifecycle.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_retrieval.py tests/test_match_verification.py tests/test_risk_assessment.py tests/test_audit_temporal.py tests/test_benchmark_health.py tests/test_benchmark_firewall.py tests/test_sealed_evaluations.py tests/test_benchmark_monitoring.py tests/test_audit_attestations.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_public_api_projections.py tests/test_scoring_golden.py tests/test_scoring_policy.py tests/test_scoring_adapter.py tests/test_scoring_replay.py` — **259 passed**.
- `uv run --locked pytest -q tests/test_scoring_properties.py` — **28 passed**. Across the focused service/API/historical-score regression and property suite, **287 distinct tests passed**.
- `corepack pnpm --filter @polycodebench/web test:e2e:prompt99` — **4 passed**: public aggregate lookup/mobile layout, accessible loading, invalid projection privacy, and curator authorization block. All browser/API responses were synthetic.
- `uv run --locked alembic -c packages/persistence/alembic.ini heads` — **one head, `b7c3e9a4d281`**. Offline `upgrade c3a4e14f8b29:head --sql` rendered successfully. No database was contacted or migrated.
- `uv run --locked python scripts/benchmark_scope_conformance.py` — internal preview reports **25 families, 0 live-verified, 4 synthetic-source-fixture families**. It performs no source/model requests and does not meet public report review requirements.

## Acceptance gates satisfied, pending and blocked

- **Partial — BAT-23-A / BX-59:** the local contract flow exercises import through health and proves unreviewed candidates, missing sources, and unknown model context cannot produce accepted evidence or a low/medium/high tier. Persistence, approved sources, real review, full temporal evidence and live scan remain absent.
- **Blocked — BAT-23-B:** firewall, replacement, seal, monitor, match correction and attestation service contracts pass their focused tests, but no authenticated cross-service transition writer or shared curator/reviewer ACL exists. The current-schema PostgreSQL test environment is uninitialized; no admission/review/disclosure/correction/alert event chain was created.
- **Partial — BAT-23-C:** 259 focused API/service/scoring regressions and four browser journeys pass. Public API allowlists reject private fields and public routes are read-only; CLI dry-runs do not dispatch. Database migration execution, production browser/access review, approved public publication and live authorization remain unavailable.
- **Partial — BAT-23-D:** the 25-row internal scope preview and exact local checks/caps are concrete. There is no human-reviewed public health projection, exact projection approval, signed publication, approved source scope, or measured live cost.
- **BWP-23 / BREQ-01–31 / BX-59: partial.** No user-facing mutation or external publication occurred. BA7 remains partial.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-58` states that synthetic lifecycle transformations validate deterministic contracts only. Fixture refs, rights fields, candidate hits, hashes, and actors never satisfy source authorization, independent review, accepted evidence, calibration, persisted history, or publication approval.
- The exact unblock for integrated transitions is an isolated database at the current Alembic head plus the authorized shared reviewer/owner ACL and service writers. Public attestation additionally requires an approved signer/trust configuration and a reviewed exact projection digest.
- Historical source bridge discrepancy `ADDENDUM-GAP-06`, provider/recovery gaps `ADDENDUM-DECISION-53/54`, and source/modality blockers from Prompt104 remain open.

## Exact next command or numbered prompt

Proceed to **Prompt106 / BWP-24**, perform the final requirement/ticket/gate traceability audit, fix in-scope defects, and prepare the operator handoff. Keep absent live, human, crypto, database and modality evidence explicitly partial or blocked; finish with a verified read-only audit command or the precise prerequisite-unblock action.
