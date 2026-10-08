# Prompt93 / BWP-11 implementation report

## Implemented functionality and changed files

- Added per-artifact AES-256-GCM sealing with fresh 32-byte data keys, 12-byte nonces and AAD bound to tenant, artifact and media type. Data keys are wrapped through an injected provider interface. The bundled AES-KWP adapter is for local development only, takes a caller-supplied key and requires explicit opt-in; it is not a production KMS.
- Added sealed-manifest v2 and strict access-event/canary documents. A manifest binds one encrypted artifact, private wrapped-key reference, P92 hiding commitment, key-provider/version/recovery data and append-only access history. Decrypt validates tenant, artifact identity, storage digest, media type and authentication tag. Key rotation rewraps the same data key without changing ciphertext or hiding commitment.
- Added injected authorization, private artifact writer and atomic access-event appender boundaries. Local screening, candidate delivery, remote query and public disclosure require an exact authorization document. Exact recipients and payload digests are recorded, then the event and successor manifest are committed before plaintext is handed to a local worker or bytes are handed to a remote callback. Local-screening results are boolean-only, and purpose/recipient labels reject free-form content. Persistence rejects standalone access events, branches, reordered/erased history and exposure-state regression.
- Added 256-bit random synthetic canary markers, bounded exact local collision checks, encrypted private marker/key storage, local observations and conservative interpretation limits. External canary observations bind the exact query payload digest to an authorized remote-query event. Missing hits do not imply cleanliness; hits do not prove training inclusion.
- Changed: `packages/core/src/polycodebench_core/benchmark_audit_documents.py`, `packages/services/src/polycodebench_services/sealed_evaluations.py`, `packages/persistence/src/polycodebench_persistence/benchmark_audit.py`, `packages/persistence/src/polycodebench_persistence/models.py`, `packages/persistence/src/polycodebench_persistence/migrations/versions/93b11c2d7e4f_sealed_access_and_canary_documents.py`, `tests/test_sealed_evaluations.py`, `tests/test_benchmark_audit_documents.py`, `tests/test_benchmark_audit_controls.py`, `tests/fixtures/contracts/benchmark-audit-vectors.json`, `docs/benchmark-audit/acceptance.md`, `docs/benchmark-audit/implementation-ledger.md`, `docs/benchmark-audit/commands.md`, and `TASKS.md`.

## Tests/commands actually run and results

- Focused Prompt93 and shared document/persistence regression: **37 passed**. Combined Prompt85-93 audit regression: **154 passed**.
- Ruff check passed for the changed Python implementation, migration and test paths.
- Strict mypy passed for the sealed document contracts, service and persistence repository.
- TypeScript contract tests passed, including Python/TypeScript canonical agreement for encrypted canary references.
- Alembic reports `93b11c2d7e4f` as the only head. Targeted upgrade and guarded downgrade SQL both rendered offline successfully. No PostgreSQL instance was configured, so no migration execution, lock-race or restore test was run.
- Exact command lines and outcomes are recorded in `docs/benchmark-audit/commands.md`. The Python test process patches `platform.machine()` in-process to avoid an unrelated SQLAlchemy Windows WMI error; no application code is patched for that workaround.

## Acceptance gates satisfied, pending and blocked

- **Partial:** BX-28, BX-29 and BX-30. Local encryption, tamper, tenant binding, access ordering, monotonic history, key rotation and canary semantics are covered by tests.
- **Partial:** BREQ-11, BREQ-12, BWP-11 and BAT-11-A through BAT-11-D. Interfaces and local implementations exist, but fixtures do not establish production readiness or live evidence.
- Production completion remains blocked on an approved KMS/key-custody adapter and restore test, production authorization verifier, private artifact-store adapter, PostgreSQL migration/integration run, approved source/query scope and independent source/date review. No external data, model or source was contacted.

## Decisions or specification discrepancies recorded

- The spec dependency says an approved key system is required, but none is configured in the checkout. The implementation keeps key-provider and storage boundaries injectable and labels the local AES-KWP provider so it cannot silently stand in for a reviewed production service.
- Prompt93 describes the source bridge as five Markdown files, while §1.1 supplies only three names/hashes and leaves the remaining rows blank. The three published hashes match current bytes; no missing source identities were inferred.
- Canary markers are encrypted before private storage, and shared contract vectors expose only a private sealed-artifact reference. A local test marker value remains available to its trusted scanner object and is excluded from repr and serialized audit documents.
- Access event and successor manifest persistence is implemented but only unit-tested through an atomic appender fake and rendered migration SQL. Database concurrency and object-store/KMS behavior remain unverified.

## Exact next command or numbered prompt

**Prompt94 / BWP-12 — Optional behavioral diagnostic protocols and applicability.** Keep model calls disabled unless approved plans, access, budgets and ordinary gateway accounting are available.
