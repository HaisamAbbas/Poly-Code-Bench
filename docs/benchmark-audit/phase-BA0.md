# Phase BA0 — Foundation, registry and audit persistence

## Implemented functionality and changed files

- Prompt83 reconciled the existing contracts, source files, repository boundary and audit ledgers; see [prompt-83.md](reports/prompt-83.md).
- Prompt84 added the 25-family benchmark catalog, eight source policies, capability matrix and a bounded no-dispatch planner; see [prompt-84.md](reports/prompt-84.md).
- Prompt85 added strict schemas for all 18 audit document kinds, shared Python/TypeScript vectors, immutable relational evidence, six exclusive queue scopes, diagnostic audit metadata and a guarded migration; see [prompt-85.md](reports/prompt-85.md).
- The ledger and acceptance state are maintained in [implementation-ledger.md](implementation-ledger.md), [acceptance.md](acceptance.md) and [decisions.md](decisions.md).

## Tests/commands actually run and results

- Prompt83 focused baseline: 37 tests passed; baseline Ruff check passed.
- Prompt84 focused catalog tests: 10 passed; Ruff and Mypy passed; read-only source metadata pins were checked. Boundary check reported 14 existing evaluation/orchestration import violations outside the Prompt84 diff.
- Prompt85 focused audit documents/controls/catalog tests: 33 passed; four opt-in PostgreSQL tests skipped because `PCB_TEST_DATABASE_URL` is unset; Ruff and Mypy passed on changed paths; TypeScript contract build and tests passed, including all 18 shared audit vectors.
- Prompt85 targeted Alembic SQL rendering passed. Full offline migration-chain rendering hits pre-existing revision `b9e04c7a1f38`, which performs an online query. No PostgreSQL test/migration URL is configured, so no database migration, old-row, queue fencing or worker-drain test ran.
- No live benchmark source, model diagnostic, human review, modality or cryptographic key workflow was exercised.

## Acceptance gates satisfied, pending and blocked

- **Satisfied:** BX-01 (repository/gap baseline), BX-02 (catalog and capability metadata), BX-03 (strict audit schemas and shared canonical vector agreement).
- **Partial:** BX-04 (six-scope schema, atomic enqueue and authorization checks exist; PostgreSQL migration and queue/recovery integration remain unverified); BX-05 (diagnostic purpose and accounting metadata exist, while dispatch approval and recommendation exclusion cannot yet be exercised).
- **Partial work packages:** BWP-01 and BWP-02 complete; BWP-03 partial.
- **Still pending/blocked:** live item imports and rights, approved corpus scopes, independently reviewed evidence, risk calibration, diagnostic/model ground truth, KMS/seal custody, monitoring and public attestation. Metadata and fixtures do not satisfy these gates.

## Decisions or specification discrepancies recorded

- ADDENDUM-GAP-01: the specification's five-source bridge names only three hashed sources and leaves one row blank.
- ADDENDUM-GAP-02: the source assumed five queue scopes; the tracked schema had three. Two minimal curation/discovery parent anchors now exist, but their workflows do not.
- ADDENDUM-GAP-03: no approved source rights/snapshots, reviewers, model ground truth or seal-key custody are configured.
- ADDENDUM-GAP-04: referenced DREQ/DWP/DXE and AREQ/AWP/AE2E identifier families were absent; existing IDs were preserved without invention.
- ADDENDUM-DECISION-08 through -12 record scope anchors, fail-closed dispatch, frozen diagnostic budgets, missing recommendation surface and guarded rollback.

## Exact next command or numbered prompt

Prompt86 / BWP-04 — immutable benchmark imports and initial code adapters. Run it in order after provisioning a dedicated PostgreSQL test/migration database for the Prompt85 upgrade and queue checks.
