# Prompt 101 / BWP-19 — Actual benchmark pilot and detector calibration

## Implemented functionality and changed files

- Added `packages/core/src/polycodebench_core/benchmark_pilot.py` with strict readiness, pinned revision/split, eligible-ID membership, rights/source evidence, frozen calibration plan, pair-family assignment, independent relation labels, and metric-report contracts.
- Added `packages/services/src/polycodebench_services/benchmark_pilot.py` with a query-free preflight for the exact HumanEval/MBPP/SWE-bench 300-task pilot; deterministic family-cluster bootstrap intervals with a plan-bound 20-million family-resample work ceiling and pre-aggregated family counts; precision, recall, FPR/FNR, unknown and stratum reporting; control filtering checks; and an explicit unmeasured behavioral-ground-truth block.
- Trusted preflight must verify the full eligible population against the exact pinned source artifact. Trusted calibration must verify the persisted plan, exact source snapshots and private/restricted immutable independent label artifacts. Probability thresholds are bounded to [0,1]. Unverified rows remain descriptive; no result enables semantic auto-admission.
- Added `tests/test_benchmark_pilot.py`. Its plans, source and label artifacts, reviewers and resolver are synthetic fixtures, not pilot evidence.
- Updated `TASKS.md`, `docs/benchmark-audit/acceptance.md`, `commands.md`, `decisions.md`, `implementation-ledger.md`, and `reports/phase-BA6.md`; added decisions 48–50.

## Tests/commands actually run and results

- `uv run --locked python -c "import platform,sys; platform.machine=lambda:'AMD64'; import pytest; sys.exit(pytest.main(['-q','tests/test_benchmark_pilot.py','tests/test_benchmark_importers.py','tests/test_match_verification.py','tests/test_benchmark_health.py']))"` — **51 passed**. Synthetic membership, resolver, chronology, review, control and family-bootstrap cases only.
- `uv run --locked ruff check packages/core/src/polycodebench_core/benchmark_pilot.py packages/services/src/polycodebench_services/benchmark_pilot.py tests/test_benchmark_pilot.py` — passed.
- `uv run --locked ruff format --check packages/core/src/polycodebench_core/benchmark_pilot.py packages/services/src/polycodebench_services/benchmark_pilot.py tests/test_benchmark_pilot.py` — passed; all three files formatted.
- `uv run --locked mypy packages/core/src/polycodebench_core/benchmark_pilot.py packages/services/src/polycodebench_services/benchmark_pilot.py tests/test_benchmark_pilot.py` — passed; no issues in three files.
- No source/network/model/database/reviewer operation occurred. No live import, scan, human label, training manifest, or behavioral ground-truth evidence was available.

## Acceptance gates satisfied, pending and blocked

- **Partial — BREQ-26/27/29, BWP-19, BX-51/52:** contracts enforce exact frozen membership, capped source scopes, provenance verification, family-aware intervals, controls and explicit missingness. No authorized source bytes/rights, production resolver, actual scan coverage/cost report, or 100 independent held-out labels across 30 families exists.
- **Partial — BAT-19-A/C:** dry-run input and calibration contracts are implemented and synthetically exercised. Owners must provide authorized exact imports, rights, source snapshots, caps, qualified independent reviewers, and immutable evidence before live execution.
- **Blocked — BAT-19-B / BX-51 live evidence:** no approved corpora or live connector is configured to produce the 300 source-audit records. Source/corpus owner must provide authorized immutable snapshots and bounded access.
- **Blocked — BAT-19-D / BX-53:** no controlled trained/untrained manifests or approved reference model exists. The model-evaluation owner must provide authorized controlled manifests and behavioral ground truth; source overlap and endpoint accuracy do not substitute.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-48`: sample membership is recomputed from the full eligible-ID population and verified against pinned bytes and rights before readiness.
- `ADDENDUM-DECISION-49`: family-held-out calibration requires verified source and label artifacts; unverified values are descriptive and auto-admission remains disabled.
- `ADDENDUM-DECISION-50`: training inclusion is a distinct claim from source overlap and stays blocked without controlled manifests.
- No new specification discrepancy was found. The existing §1 discrepancy remains: five source Markdown files are described, but only three are named and hashed.

## Exact next command or numbered prompt

Proceed in order to **Prompt 102 / BWP-20 — live replacements, sealed workflow and monitoring evidence**. Continue with independent local contracts and bounded preparations; keep live work blocked until approved source, author, reviewer, model and campaign resources are available.
