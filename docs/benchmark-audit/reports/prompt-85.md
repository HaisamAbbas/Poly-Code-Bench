# Prompt 85 — Audit schemas, persistence and six-scope queue migration

## Implemented functionality and changed files

- Added strict immutable contracts for all 18 §7 audit document kinds, refs, precision-aware timestamps, fixed-string decimals, null reasons, legal run/query state transitions and semantic digests in [benchmark_audit_documents.py](../../../packages/core/src/polycodebench_core/benchmark_audit_documents.py).
- Added 18 shared canonical payload vectors in [benchmark-audit-vectors.json](../../../tests/fixtures/contracts/benchmark-audit-vectors.json); Python validates each kind and TypeScript verifies matching canonical bytes/digests.
- Added immutable document storage, same-kind successor enforcement, bounded plan reservations and atomic query/job enqueue in [benchmark_audit.py](../../../packages/persistence/src/polycodebench_persistence/benchmark_audit.py).
- Added audit registry, corpus, run, query, checkpoint, evidence, risk and temporal persistence tables, plus six exclusive queue scopes, diagnostic run metadata, budget checks and least-privilege grants in [grant_permissions.sql](../../../packages/persistence/sql/grant_permissions.sql). The guarded migration is [c3a4e14f8b29_benchmark_audit_foundation.py](../../../packages/persistence/src/polycodebench_persistence/migrations/versions/c3a4e14f8b29_benchmark_audit_foundation.py).
- Extended queue and call-ledger behavior so audit claims require explicit dispatch authorization, old workers default to incapable, and diagnostic call intents retain an ordinary attempt FK plus a separate audit reference. Diagnostic budget is frozen in the audit plan and defaults to zero.
- Added focused safety tests in [test_benchmark_audit_controls.py](../../../tests/test_benchmark_audit_controls.py) and [test_benchmark_audit_documents.py](../../../tests/test_benchmark_audit_documents.py). Removed invalid literal `\n` EOF lines from four Prompt84 catalog YAML files after Prompt85 regression tests surfaced them.
- Updated [implementation-ledger.md](../implementation-ledger.md), [acceptance.md](../acceptance.md), [decisions.md](../decisions.md), and [commands.md](../commands.md).

## Tests/commands actually run and results

- Focused Python tests: **33 passed** across audit documents, audit controls and the Prompt84 catalog.
- Ruff: **passed** on the 12 changed Prompt85 Python/test paths.
- Mypy: **passed**, 12 source files checked.
- TypeScript contracts: build and `test:contracts` **passed**; all 18 shared audit vectors matched canonical bytes and digests.
- Targeted Alembic PostgreSQL SQL rendering from `2a62b6001aa1` to `c3a4e14f8b29`: **passed**. This renders SQL without connecting to a database.
- Full offline migration-chain rendering stopped in pre-existing revision `b9e04c7a1f38`, which executes an online `SELECT`. PostgreSQL test and migration URLs are not configured, so the new migration was not run against old rows and no queue integration claims are made.
- The opt-in PostgreSQL persistence suite ran and skipped all four tests because `PCB_TEST_DATABASE_URL` is unset.
- No remote source, model, human review, diagnostic dispatch or benchmark payload was used.

## Acceptance gates satisfied, pending and blocked

- **Satisfied:** BX-03 / BAT-03-A: strict Python validation for all 18 kinds and cross-language shared canonical vectors.
- **Partial:** BX-04 / BAT-03-B,C: migration, six-scope checks, authorization gate, CAS enqueue and existing fencing paths are implemented; live old-row migration, worker drain, claim/recovery and duplicate-dispatch checks await a configured PostgreSQL test database.
- **Partial:** BX-05 / BAT-03-D: diagnostic purpose, approved audit reference, ordinary attempt accounting identity and plan-bounded cost/token/endpoint caps are represented. No authorization API currently opens dispatch, and no recommendation query/surface exists here to prove diagnostic exclusion.
- **Requirements:** BREQ-14, BREQ-25 and BREQ-28 are partial. BREQ-07 and BREQ-24 remain pending their later risk/source/privacy workflows. No gate is claimed from fixtures as live evidence.

## Decisions or specification discrepancies recorded

- ADDENDUM-GAP-02: the source assumes five queue scopes, while this checkout had three. Two minimal parent anchors were added for curation and discovery; their workflows remain future work.
- ADDENDUM-DECISION-08 through -12: scope-anchor limits, fail-closed dispatch approval, plan-frozen diagnostic budget, absent recommendation surface and non-empty-history downgrade guard.
- BADR-21/22: same-kind immutable document successors; diagnostic metadata stays separate from the attempt identity used for settlement.
- Existing run purposes remain `NULL` after migration because historical meaning is unknown. Downgrade refuses populated audit history or new-purpose/scope state.

## Exact next command or numbered prompt

Prompt86 / BWP-04 — immutable benchmark imports and initial code adapters. Before enabling live work, configure a dedicated PostgreSQL test/migration database and run the new migration and queue integration tests against the historical schema.
