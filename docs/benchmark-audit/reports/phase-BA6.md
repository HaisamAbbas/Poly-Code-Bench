# Phase BA6 — Actual validation

## Implemented functionality and changed files

Prompt 101 adds a query-free preflight for the frozen 300-task pilot, exact source-population and calibration-evidence resolver boundaries, and family-cluster detector calibration contracts. Prompt 102 adds reference-only accounting for the 12 replacement, six sealed-task and one changed-source monitor campaign. See the [Prompt 101 report](prompt-101.md) and [Prompt 102 report](prompt-102.md). Prompt 103 operations/recovery/load evidence remains pending.

## Tests/commands actually run and results

- Prompt 101 focused regression: **51 passed**, using synthetic plans and evidence only.
- Prompt 101 Ruff lint/format and MyPy checks passed for core, service and test paths.
- Prompt 102 replacement/firewall, seal, monitoring and campaign regression: **46 passed**, using synthetic refs and resolver only.
- Prompt 102 Ruff lint/format and MyPy checks passed for core, service and test paths.
- No approved corpus, model, reviewer, human-label, database, browser, or hardware operation was available or attempted. Fixture results are not BA6 live validation.

## Acceptance gates satisfied, pending and blocked

| Gates | Status | Evidence and remaining work |
|---|---|---|
| BREQ-27 / BAT-19-A,C / BWP-19 | Partial | Frozen membership, trusted evidence boundaries, family bootstrap and blockers are implemented. Actual source rights/snapshots, scans and independent held-out labels remain unavailable. |
| BX-51 | Partial | Exact plan and bounded preflight exist. The three 100-task source scans and coverage/cost evidence require authorized source snapshots and live connectors. |
| BX-52 | Partial | Calibration math and evidence gates exist. Qualified reviewers must label at least 100 held-out pairs across 30 families with verified immutable evidence. |
| BX-53 / BAT-19-D | Blocked | Controlled trained/untrained manifests and approved behavioral ground truth are absent; source overlap cannot establish training inclusion. |
| BX-54 / BAT-20-A,B,D | Partial | Reference-only replacement/seal evidence and mode-specific accounting contracts are tested; no actual 12 reviewed candidates, six production disclosures or author/oracle/crypto records are available. |
| BX-55 / BAT-20-C | Partial | Changed-source and bounded query/cost/alert-history reconciliation contracts exist; approved live source rescan and persisted tick evidence are absent. |
| BX-56–58 / BAT-21 | Pending | Prompt 103 restore, outage/fence/cancel recovery, malicious-input and load evidence remains. |

## Decisions or specification discrepancies recorded

`ADDENDUM-DECISION-48` binds pilot membership to imported source bytes; `ADDENDUM-DECISION-49` requires verified family-held-out evidence; `ADDENDUM-DECISION-50` separates model-training evidence from source overlap. `ADDENDUM-DECISION-51` keeps Prompt102 readiness reference-only; `ADDENDUM-DECISION-52` separates development modes and claimed usage from verified evidence. The existing §1 source-list discrepancy remains; no new discrepancy was found.

## Exact next command or numbered prompt

Proceed to Prompt 103 / BWP-21, implementing local operations and failure checks; use an approved restore/load environment before any live restore, outage or capacity test.
