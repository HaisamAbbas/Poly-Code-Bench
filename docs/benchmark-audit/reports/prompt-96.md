# Prompt 96 / BWP-14 - Continuous monitoring, risk changes and owner alerts

Status: **partial implementation foundations**. No live monitoring was enabled, no remote source was queried, and no PostgreSQL instance was available.

## Implemented functionality and changed files

- Added strict monitor-policy schema v2 with an exact audit-plan, benchmark, task and corpus-snapshot scope; independent owner/approver subjects; private approval evidence; daily/weekly cadence and IANA timezone; full-refresh/staleness intervals; query, storage and cost caps; per-source daily query limits; bounded retry/catch-up values; and frozen in-app recipients. External delivery is fixed to `disabled`, and target-model diagnostic calls/cost are fixed to zero. Legacy v1 policy documents remain parseable for history but cannot reserve monitor work.
- Added deterministic schedule planning with stable daily/weekly slot keys. Ambiguous fall-back times use the first occurrence; nonexistent spring-forward times move to the first valid local minute, with a 180-minute resolution limit. The newest due tick is retained and older catch-up is capped by policy. Full refresh selection uses every approved source; incremental selection includes only approved changed corpus snapshots. Query reservations account for the full retry ceiling.
- Added bounded exponential retry planning and stale/full-refresh due checks. PostgreSQL slot reservation locks the immutable policy row, enforces `(policy, slot)` idempotency, checks query/storage/cost caps and serializes per-source/day quota reservations. Failure recording requires an already dispatched, authorized audit run, advances by row version, schedules only the frozen retry allowance within the same source-local day, and appends a fixed-code audit event. A retry that would cross the reserved day becomes terminal to prevent quota bypass. The existing audit queue remains dispatch-authorized by default.
- Added reference-only typed alert documents with deterministic dedupe keys and a unique in-app inbox row per frozen recipient. Persistence requires in-scope task/source evidence, an exact frozen audit plan and successor risk assessment before a risk alert; new exposure also requires accepted verified v2 evidence, and measured score increases require comparable context/risk-policy versions. Coverage alerts must reference only approved sources. It also distinguishes staleness, source outages, disputes, corrections, policy/corpus/method discontinuities and compromised seals. Alert insertion and inbox fan-out are one transaction. Documents and evidence use restrictive references; source deletion has no erase path.
- Added the durable slot, per-source reservation and in-app inbox tables, policy/alert indexes and a guarded downgrade in migration `e5c7b2a94d10_continuous_monitoring.py`. Added focused schedule, retry, refresh, dedupe, strict-contract and metadata tests plus a shared canonical alert vector.
- Changed implementation and tracking files: `packages/core/src/polycodebench_core/benchmark_audit_documents.py`, new `packages/core/src/polycodebench_core/monitor_schedule.py`, `packages/services/src/polycodebench_services/benchmark_monitoring.py`, `packages/persistence/src/polycodebench_persistence/benchmark_audit.py`, `packages/persistence/src/polycodebench_persistence/models.py`, the migration above, `tests/test_benchmark_monitoring.py`, `tests/test_benchmark_audit_documents.py`, `tests/fixtures/contracts/benchmark-audit-vectors.json`, `TASKS.md`, `docs/benchmark-audit/acceptance.md`, `docs/benchmark-audit/commands.md`, `docs/benchmark-audit/decisions.md`, `docs/benchmark-audit/implementation-ledger.md`, and `docs/benchmark-audit/reports/phase-BA4.md`.

## Tests and commands run

| Command/check | Result | Interpretation |
|---|---|---|
| Focused Prompt96, audit-document and persistence-contract tests | Passed: 34 passed | Synthetic schedule, DST, quota, alert-scope and schema cases; no database/source/model access. |
| Combined Prompts85-96 audit regression | Passed: 184 passed | Local and synthetic audit tests; no live database, source or model calls. |
| Strict Mypy on Prompt96 core/service/persistence/models/tests | Passed: no issues in 6 files | All changed Python contracts and repository logic type-check. |
| Ruff check and format check on changed Python | Passed | Lint and formatting clean. |
| `corepack pnpm --filter @polycodebench/contracts test:contracts` | Passed | Shared Python/TypeScript audit-alert canonical vector and existing 256 contract properties agree. |
| Alembic offline upgrade `d4f7b2a196c3:head` and downgrade `e5c7b2a94d10:d4f7b2a196c3` | Passed: PostgreSQL SQL rendered both ways | Downgrade checks for monitor evidence before dropping Prompt96 tables/kinds/indexes. No database was contacted. |
| `uv run alembic -c packages/persistence/alembic.ini heads` | Passed: `e5c7b2a94d10` is the only head | Migration history remains linear. |
| `git diff --check` | Passed | No whitespace errors. |

The Windows-only SQLAlchemy WMI workaround patched `platform.machine()` in-process for pytest/Alembic. It did not alter the environment or make a database connection.

## Acceptance gates

- **BX-38: partial.** Slot uniqueness, exact replay checks, capped catch-up/retries and serialized source-rate reservations are implemented. PostgreSQL concurrent reservation, restart/recovery and live schedule evidence remain unverified.
- **BX-39: partial.** Accepted verified evidence plus its successor assessment is required; in-app fan-out is deduped and transactional; correction/dispute history is retained. There is no live source feed or authenticated inbox UI.
- **BX-40: partial.** Staleness, outage, correction/dispute, policy/corpus/method discontinuity and seal compromise are separate typed events. Only in-app routes are representable. Trusted role authorization and live key/source-event integration are absent.

Production prerequisites still unavailable: trusted owner/approver role verification, an authenticated policy enable/pause API, a production timer/scheduler, authorized corpus-snapshot and source connectors, live evidence/reviewer history, PostgreSQL integration and an authenticated in-app inbox. Policy subject names and approval artifacts are stored, but this code cannot prove those identities or roles. Slots remain blocked from remote dispatch until the existing explicit audit-run authorization is set by a trusted operator path.

## Decisions and specification discrepancies

- Recorded decisions 38-40 in `decisions.md`: schedule identity and DST resolution, all retries counted in frozen source/query reservations, reference-only alerts, and separately identified policy/corpus/method breaks. External notifications remain disabled until a separately trusted recipient/channel authorization feature exists.
- No new Prompt96 specification discrepancy was found in sections 17 or 22. The earlier source-document discrepancy recorded in Prompts93-95 remains unchanged: section 1.1 describes five source Markdown files but lists and hashes only three.

## Next prompt

Proceed in order to **Prompt97 / BWP-15 - Benchmark health aggregation and comparable trends**.
