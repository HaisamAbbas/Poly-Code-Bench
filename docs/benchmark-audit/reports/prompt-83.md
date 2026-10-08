# Prompt83 — Repository reconciliation and audit ledgers

## Implemented functionality and changed files
Created the source/contract/gap inventory, BADR and discrepancy register, BREQ/BWP/BAT/BX ownership matrix, command evidence registry, and reusable five-field prompt/phase templates under `docs/benchmark-audit/`. Source specifications remain unchanged. Existing uncommitted taskgen work is noted as inspected but is not included in the prompt commit.

## Tests/commands actually run and results
- Focused backend Ruff check: passed.
- `uv run --offline --locked pytest -q tests/test_core_contracts.py tests/test_suite_admission.py tests/test_repo_task_admission.py`: 37 passed in 14.69s.
- These are pre-change baselines; no new code behavior or live infrastructure was exercised.

## Acceptance gates satisfied, pending and blocked
- Satisfied: BX-01, BAT-01-A/B/C/D, BWP-01; evidence is this report plus `implementation-ledger.md`, `acceptance.md` and `commands.md`.
- Pending: BX-02–50, BX-59 and BX-60 await their ordered implementation.
- Blocked pending external evidence: BX-51–58 require authorized source snapshots/rights, independent labels/reviewer, controlled model ground truth where claimed, approved rescan, restore and measured security/load environment. Independent local work proceeds.
- BA0 remains partial.

## Decisions or specification discrepancies recorded
- ADDENDUM-GAP-01: §1.1 names five sources but only hashes three. The repository requirements matrix and tickets are supplemental, not silently substituted.
- ADDENDUM-GAP-02: §8 assumes five existing queue scopes; tracked schema has three.
- ADDENDUM-GAP-03: live source, human, model-calibration and key-custody evidence is absent.
- ADDENDUM-GAP-04: the checkout contains REQ/WP/E2E but no DREQ/DWP/DXE or AREQ/AWP/AE2E identifiers; no historical IDs were invented.
- BADR-01–20 are preserved in `decisions.md`.

## Exact next command or numbered prompt
Implement Prompt84 / BWP-02: create the versioned benchmark/source catalog, capability matrix and dry-run bounded resource planner; preserve unknown access and rights as blocked.
