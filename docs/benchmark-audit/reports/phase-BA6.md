# Phase BA6 — Actual validation

## Implemented functionality and changed files

Prompt 101 adds a query-free preflight for the frozen 300-task pilot, exact source-population and calibration-evidence resolver boundaries, and family-cluster detector calibration contracts. Prompt 102 adds reference-only accounting for the 12 replacement, six sealed-task and one changed-source monitor campaign. Prompt 103 hardens local restore bundles and verifies audit document/reference/seal/attestation history, with explicit compatibility and index-readiness blockers. See the [Prompt 101 report](prompt-101.md), [Prompt 102 report](prompt-102.md) and [Prompt 103 report](prompt-103.md).

## Tests/commands actually run and results

- Prompt 101 focused regression: **51 passed**, using synthetic plans and evidence only.
- Prompt 101 Ruff lint/format and MyPy checks passed for core, service and test paths.
- Prompt 102 replacement/firewall, seal, monitoring and campaign regression: **46 passed**, using synthetic refs and resolver only.
- Prompt 102 Ruff lint/format and MyPy checks passed for core, service and test paths.
- Prompt 103 recovery bundle regression: **14 passed, 1 skipped**. Aggregate recovery/importer/connector/seal/attestation/monitor/worker/sandbox/gateway/telemetry/scheduler regression: **237 passed, 39 skipped**. Ruff, format and MyPy passed for the recovery verifier, CLI, local Docker streaming helper and tests.
- A local isolated restore from the documented synthetic backup verified the bundle and generic database/object integrity, then failed closed because the snapshot predates the benchmark-audit schema. All rehearsal containers/network were reclaimed. A separate attempt to run PostgreSQL provider/queue regressions stopped at fixture setup: the designated test database has no Alembic schema; no migration was run.
- No approved corpus, source connector, model, reviewer, human-label set, current-schema audit backup, production key, staging service or hardware/load environment was available or contacted. Fixture and legacy-backup results are not BA6 live validation.

## Acceptance gates satisfied, pending and blocked

| Gates | Status | Evidence and remaining work |
|---|---|---|
| BREQ-27 / BAT-19-A,C / BWP-19 | Partial | Frozen membership, trusted evidence boundaries, family bootstrap and blockers are implemented. Actual source rights/snapshots, scans and independent held-out labels remain unavailable. |
| BX-51 | Partial | Exact plan and bounded preflight exist. The three 100-task source scans and coverage/cost evidence require authorized source snapshots and live connectors. |
| BX-52 | Partial | Calibration math and evidence gates exist. Qualified reviewers must label at least 100 held-out pairs across 30 families with verified immutable evidence. |
| BX-53 / BAT-19-D | Blocked | Controlled trained/untrained manifests and approved behavioral ground truth are absent; source overlap cannot establish training inclusion. |
| BX-54 / BAT-20-A,B,D | Partial | Reference-only replacement/seal evidence and mode-specific accounting contracts are tested; no actual 12 reviewed candidates, six production disclosures or author/oracle/crypto records are available. |
| BX-55 / BAT-20-C | Partial | Changed-source and bounded query/cost/alert-history reconciliation contracts exist; approved live source rescan and persisted tick evidence are absent. |
| BX-56 / BAT-21-A | Partial | Restore validates audit schemas/digests/refs, seal transitions and attestation history. Available synthetic snapshot lacks required audit tables; provider key recovery and index config/rebuild remain unverified. |
| BX-57 / BAT-21-B | Partial | Local worker fence/cancel and retry contracts pass. Persisted provider response replay and current-schema reservation reconciliation require a migrated isolated PostgreSQL test DB; no live source/object outage was injected. |
| BX-58 / BAT-21-C,D | Partial / blocked | Malicious archive, connector, sandbox and diagnostic-redaction tests pass locally. Search/monitor latency/storage/cost needs an approved representative corpus and index runtime, neither is configured. |

## Decisions or specification discrepancies recorded

`ADDENDUM-DECISION-48` binds pilot membership to imported source bytes; `ADDENDUM-DECISION-49` requires verified family-held-out evidence; `ADDENDUM-DECISION-50` separates model-training evidence from source overlap. `ADDENDUM-DECISION-51` keeps Prompt102 readiness reference-only; `ADDENDUM-DECISION-52` separates development modes and claimed usage from verified evidence. Prompt103 decisions `ADDENDUM-DECISION-53/54` require current audit-schema recovery and record the missing durable index/rebuild path. The existing §1 source-list discrepancy remains.

## Exact next command or numbered prompt

Proceed to **Prompt 104 / BWP-22**, broader benchmark adapters and scope conformance. Keep imports bounded to authorized immutable sources; do not represent unsupported modalities or absent rights as conformant.
