# Prompt92 / BWP-10 implementation report

## 1. Implemented functionality and changed files

- Added a strict `model_context` v1 document and temporal-assessment v2 document while preserving the temporal-assessment v1 contract and shared v1 vectors. Model contexts record provider, alias, exact revision or weight digest, pin confidence, cutoff interval/source/confidence, updates, retrieval/tool policies, prior deliveries and audit time.
- Added digest-bound model-context snapshots to temporal assessments. Assessment validation recomputes the interval result and rejects a status or qualifier that disagrees with the evaluator.
- Added chronology intervals that retain date precision, derivation and separate evidence bases. Archive captures and timestamp receipts are upper bounds only; they cannot be entered as proof of public exposure. Earlier verified upstream evidence remains effective when a later benchmark publication is also present.
- Added a pure evaluator for pre-cutoff exposure, post-declared-cutoff, overlap, unknown source/cutoff and mutable model context. Mutable aliases, missing cutoffs, incomplete source intervals, unverified earlier claims and model updates not proven before cutoff cannot receive a post-cutoff result.
- Added salted SHA-256 commitments over canonical manifest bytes with a 32-byte random nonce, private nonce references, local Ed25519 receipts with a fixed non-independent trust label, token artifact digest checks and an explicit provider/version/protocol allowlist for replaceable trusted timestamp adapters.
- Added persistence kind migration and dependency-lock metadata.

Changed files: `packages/core/src/polycodebench_core/benchmark_audit_documents.py`, `packages/core/src/polycodebench_core/audit_temporal.py`, `packages/services/src/polycodebench_services/audit_temporal.py`, `packages/services/pyproject.toml`, `packages/persistence/src/polycodebench_persistence/models.py`, `packages/persistence/src/polycodebench_persistence/migrations/versions/92a10b7c6d5e_model_context_document_kind.py`, `tests/test_audit_temporal.py`, `tests/test_benchmark_audit_documents.py`, `tests/test_benchmark_audit_controls.py`, `tests/fixtures/contracts/benchmark-audit-vectors.json`, `uv.lock`, `docs/benchmark-audit/acceptance.md`, `docs/benchmark-audit/implementation-ledger.md`, `docs/benchmark-audit/commands.md`, `TASKS.md`.

## 2. Tests and commands run

- Combined Prompt85-92 audit regression: **140 passed**. The test process patched `platform.machine()` in memory because SQLAlchemy's Windows WMI probe raised a resource exception; application code was unchanged, and no database connection was made.
- TypeScript contract verification passed for the shared canonical vectors, including model-context v1 and temporal-assessment v2.
- Ruff check: passed for all changed Python implementation, migration and test paths.
- Ruff format check: passed for all eight checked Python paths.
- Mypy: passed with no issues in six implementation/test/migration files under the repository's strict configuration.
- `uv lock --check --offline`: passed.
- Alembic upgrade and downgrade SQL both rendered successfully offline. The downgrade includes a guard against removing the model-context kind while rows remain. `92a10b7c6d5e` is the only migration head.

Exact commands and results are recorded in `docs/benchmark-audit/commands.md`.

## 3. Acceptance gates

- **Partial:** BX-25, BX-26 and BX-27. Synthetic tests cover earlier-source precedence, unknown/mutable context, interval overlap, commitment binding and local signature verification.
- **Partial:** BREQ-09, BREQ-10, BWP-10 and BAT-10-A through BAT-10-D. The contracts and evaluator are implemented, but no approved live source/model evidence or external timestamp authority was available.
- No gate is marked complete based on fixtures. No actual model training, source originality or exposure finding is claimed.

## 4. Decisions and specification discrepancy

- The §7 canonical kind table does not list `model_context`, while §13 and Prompt92 require a separately auditable model-context record. This implementation adds a `model_context` v1 kind and guarded migration rather than storing cutoff claims only as untyped temporal fields. Existing temporal-assessment v1 documents remain readable.
- An archive capture or timestamp receipt proves an upper-bound existence time, not the first creation/public exposure time. Its chronology interval therefore cannot have a fabricated lower boundary.
- Trusted timestamp adapters are versioned and allowlisted but none is configured. Local Ed25519 receipts verify their signed bytes and commitment, yet remain explicitly non-independent and cannot produce a trusted-provider proof.

## 5. Exact next prompt

**Prompt93 / BWP-11 — Sealed evaluations, encryption, access and canaries.**
