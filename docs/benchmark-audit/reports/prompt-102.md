# Prompt 102 / BWP-20 — Live replacements, sealed workflow and monitoring evidence

## Implemented functionality and changed files

- Added `packages/core/src/polycodebench_core/benchmark_pilot_campaigns.py` with strict reference-only evidence contracts for 12 replacement candidates (four in each of three benchmark competency slices), six private sealed tasks, and one changed-source monitor tick.
- Added `packages/services/src/polycodebench_services/benchmark_pilot_campaigns.py` to reconcile immutable refs, independent author/checker/reviewer identities, explicit rights/ancestry references, lineage/review states, preregistration chronology, source/round/cost/time caps, seal/disclosure/provider modes, changed-source coverage, alert history, and query/cost reservations. Resolver-verified usage is reported separately from caller-reported usage. The readiness check dispatches no work.
- Development versus production source/model/key/timestamp modes are explicit. Only trusted resolver evidence can count as verified; six production-mode private disclosures and one complete verified source rescan are required for a ready status.
- Added `tests/test_benchmark_pilot_campaigns.py`, using only synthetic document refs and a fake resolver. Updated `TASKS.md`, acceptance, command and decision ledgers, implementation ledger, and phase BA6; added decisions 51–52.
- Existing Prompt95 replacement, Prompt93 sealed-artifact and Prompt96 monitor implementations remain the behavior sources. Prompt102 adds no authoring, encryption, source, model or notification gateway.

## Tests/commands actually run and results

- `uv run --locked python -c "import platform,sys; platform.machine=lambda:'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_benchmark_pilot_campaigns.py','tests/test_benchmark_firewall.py','tests/test_sealed_evaluations.py','tests/test_benchmark_monitoring.py']))"` — **46 passed**. All data and resolver outcomes were synthetic. Covers complete/blocked campaigns, per-slice counts, budget overruns, development-only seals, timestamp chronology, usage reconciliation and duplicate evidence.
- `uv run --locked ruff check packages/core/src/polycodebench_core/benchmark_pilot_campaigns.py packages/services/src/polycodebench_services/benchmark_pilot_campaigns.py tests/test_benchmark_pilot_campaigns.py` — passed.
- `uv run --locked ruff format --check packages/core/src/polycodebench_core/benchmark_pilot_campaigns.py packages/services/src/polycodebench_services/benchmark_pilot_campaigns.py tests/test_benchmark_pilot_campaigns.py` — passed.
- `uv run --locked mypy packages/core/src/polycodebench_core/benchmark_pilot_campaigns.py packages/services/src/polycodebench_services/benchmark_pilot_campaigns.py tests/test_benchmark_pilot_campaigns.py` — passed; no issues in three files.
- No actual authors, checkers, oracle, human reviewers, source/model/crypto provider, database, or alert delivery were used. No replacement or sealed task was created or disclosed, and no source was queried.

## Acceptance gates satisfied, pending and blocked

- **Partial — BREQ-11/12/16/17/19/27, BWP-20, BX-54/55:** exact campaign limits, independent role fields, lineage/review summaries, private seal and provider modes, bounded monitor tick and separate claimed/verified usage are enforced by the readiness contracts. The production resolver and actual campaign records are absent.
- **Partial — BAT-20-A/B/D:** per-benchmark four-candidate counts and author/checker budget reconciliation, six-task screening/disclosure records, mode-specific evidence and campaign accounting are synthetically exercised. Real author/oracle/reviewer evidence, six independently authored tasks, production keys and trusted timestamps remain unavailable.
- **Blocked — BAT-20-C:** no approved changed snapshot, owner-approved live policy, connector rescan or persisted alert/usage history exists. Source owner and monitor operator must provide these through the authorized adapter before the tick can be accepted.
- Overall readiness remains **partial**. Tests that use a resolver fixture do not establish production authorization or live evidence.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-51`: campaign readiness is reference-only and requires trusted verification of persisted records and authority evidence; it never dispatches work.
- `ADDENDUM-DECISION-52`: development modes and reported usage remain distinct from verified source/model/human/crypto/monitor evidence.
- No new specification discrepancy was found. The existing §1 source-list discrepancy remains.

## Exact next command or numbered prompt

Proceed in order to **Prompt 103 / BWP-21 — operations, malicious-input defenses and recovery/load**. Use only the local/synthetic/approved environments already available; require an approved restore/load environment before any live operation.
