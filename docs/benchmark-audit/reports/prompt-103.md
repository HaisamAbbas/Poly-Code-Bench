# Prompt 103 / BWP-21 — Operations, malicious-input defenses and recovery/load

## Implemented functionality and changed files

- Hardened `packages/operations/src/polycodebench_operations/recovery.py` backup preflight. It bounds manifest size and object count; validates schema, bucket separation, table counts, file inventory, digest syntax, object sizes/digests and unique inventory paths; and rejects traversal and symbolic links before creating Docker resources. Digesting is chunked, object restore uses file streams, and database dumps stream through Docker during both backup and restore.
- Added current benchmark-audit restore verification: strict document reparsing and semantic digests, embedded document/artifact reference resolution, sealed-manifest successor transitions, attestation lifecycle chains, and row-count evidence for audit docs/events/runs/queries/checkpoints, calls/deliveries, budgets, usage and monitor slots/inboxes. Old snapshots without the audit schema fail closed with the stable code `benchmark_audit_schema_missing`.
- Removed raw driver exception messages from recovery/restore evidence. Reports retain error classes, safe recovery codes and constrained SQLSTATE values. `packages/operations/src/polycodebench_operations/cli.py` applies the same redaction to `pcb-ops restore verify` stdout and evidence.
- Added `tests/test_operations_recovery_bundle.py` for malformed/path-colliding bundles, digest/size mismatches, audit digests/references, safe error evidence and restore-CLI redaction.
- Added [benchmark audit recovery and sealed evidence](../../operations/runbooks/benchmark-audit-recovery.md), and linked it from the [database/object-store recovery runbook](../../operations/runbooks/database-object-store-restore.md). Updated `TASKS.md`, acceptance, implementation/command/decision ledgers, this report and phase BA6.

## Tests/commands actually run and results

- Focused recovery bundle regression: **14 passed, 1 skipped** (Windows symlink creation is unavailable).
- Combined regression across recovery, benchmark importers, corpus connectors, sealed evaluations, attestations, monitoring, workers, sandbox, gateway secrecy/fixtures, operations telemetry and scheduler regressions: **237 passed, 39 skipped**. Other skips include opt-in Docker restore/sandbox checks and PostgreSQL integration tests without an initialized schema.
- `uv run --locked ruff check packages/operations/src/polycodebench_operations/recovery.py packages/operations/src/polycodebench_operations/cli.py tests/test_operations_recovery_bundle.py` — passed.
- `uv run --locked ruff format --check` on those three files — passed.
- `uv run --locked mypy` on those three files — passed, no issues.
- Ran `pcb-ops restore rehearse` against the ignored local backup documented as synthetic internal data in the existing operations report. Bundle validation, database/object restore, generic FK/row-count integrity and isolated-resource teardown ran. Audit verification then rejected that 2026-10-03 pre-audit snapshot with `benchmark_audit_schema_missing`; result: `passed: false`, `recovery_time_seconds: null`, `resources_reclaimed: true`. The sanitized evidence is under ignored `.local/ops-rehearsal/prompt-103-restore-20261009.json`. This is a useful fail-closed result, not a successful current-schema recovery.
- Attempted the queue/provider PostgreSQL regression modules using the designated local test URL. All **50 cases failed at fixture setup**, before test bodies, because that database has no `alembic_version` table. No migration or database write was attempted. Do not run migrations there without an explicitly provisioned test schema.
- The recovery symlink test skipped because this Windows filesystem did not permit symlink creation. The Docker negative-control test skipped because its opt-in backup environment variable was not set. No source/provider outage, production key, staging resource or external service was contacted.

## Acceptance gates satisfied, pending and blocked

- **Partial — BAT-21-A / BX-56:** bundle verification and benchmark-audit reconciliation are implemented. The only available local restore backup predates the audit schema, so a compatible audit restore was not demonstrated. External KMS/provider key recovery is unverified, and there is no persisted retrieval-index configuration or rebuild adapter.
- **Partial — BAT-21-B / BX-57:** worker fence/cancellation, monitor retry, usage, and local failure contracts passed their contained regressions. Provider response replay and real persisted reservation reconciliation remain blocked because the designated PostgreSQL test DB is uninitialized. No live source or object-store outage was injected.
- **Partial — BAT-21-C / BX-58:** malicious archive/path handling, connector/source boundaries, sandbox contracts, telemetry secrecy, and restore failure redaction passed local checks. Production tenant authorization, KMS/key custody and live canary-log review remain unavailable.
- **Blocked — BAT-21-D / BX-58:** no approved representative corpus, search/index runtime or monitor-load environment exists. The restore stopped before the audit verification completed, so its elapsed step times do not qualify as recovery RTO or capacity evidence. Alerts, retention, key rotation and incident procedures are documented in the operations runbooks.
- **BREQ-24/25/30:** partial. Local privacy/fence/failure contracts pass, while current-schema, live authorization/provider, and capacity evidence remain absent. No BA6 gate is complete from fixtures or a legacy restore.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-53`: restore requires current audit tables and verifies immutable audit document/artifact/seal/attestation history; generic restore success does not qualify. Failure reports omit raw driver messages.
- `ADDENDUM-DECISION-54`: restored audit documents do not establish search readiness without a durable index manifest and rebuild adapter. This is a newly confirmed implementation gap against Prompt103/BX-56; the existing §1 source-list discrepancy also remains.

## Exact next command or numbered prompt

Proceed in order to **Prompt 104 / BWP-22 — broader benchmark adapters and scope conformance**. Keep all imports bounded to authorized immutable sources; do not represent unsupported modalities or absent rights as conformant.
