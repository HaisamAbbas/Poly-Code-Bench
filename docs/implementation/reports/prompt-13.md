# Prompt 13 — DONE (independent performance measurement; development tier, dedicated hardware blocked)

## 1. Implemented functionality and changed files

- **Paired measurement runner** — `packages/evaluation/src/polycodebench_evaluation/performance.py`.
  `PerformanceRunner.measure()` reserves one worker exclusively for the whole measurement window,
  re-verifies hardware identity from the guest's own report, refuses instrumented/profiling builds,
  builds both sides (compile time measured separately), then runs canary-bounded blocks of
  randomized paired iterations and freezes one `PerformanceEvidence` manifest. Pair order is
  derived from `(plan_seed, workload_seed, iteration)` — reproducible from evidence, never from
  wall-clock.
- **Speed lane + side equivalence** — `check_speed_lane()` matches bare instrumentation stems across
  argv, *environment keys and values*, the image reference and the tool identity, reporting
  `where:token`. `specialize_for_side()` re-roots only the candidate inputs (`cand/`, `ref/`);
  `side_invariants()` digests image, flags, resources, exit semantics, tool, trusted inputs and
  environment, and the runner refuses to measure when the two sides' digests differ.
- **Worker reservation** — `PlanRunner.reserved_guest()` plus `ReservedGuest`
  (`plan_runner.py`): one guest, many fresh processes, same digest-checked staging and the same
  in-guest deadline wrapper. A production worker satisfies the same contract by holding one
  scheduler capacity slot for the stage.
- **Blocks and canary** — canary on the trusted reference at the largest declared scale, ±10% band
  and ≤5% relative MAD against the frozen baseline, at most two blocks, both retained, **first
  valid block by time order selected**; an invalid canary invalidates its whole block.
- **Pure aggregation** — `efficiency.py`: symmetric positive floors, median/MAD/relative-MAD/stdev,
  weighted geometric mean over basis-point weights, the documented `f(r,b)` transform and
  `E = 0.70·f(r_time,4) + 0.30·f(r_memory,2)`, censored-timeout bounds, empirical-only scale growth.
  `efficiency_score()` refuses to produce a value when either component is missing.
- **Contracts** — `perfcontracts.py`: `HardwareRecord`, `IterationRecord`, `CanaryRecord`,
  `BlockRecord`, `BuildTiming`, `SeriesSummary`, `WorkloadMeasurement`, `EfficiencyResult`,
  `PerformanceEvidence` (with `hardware_gate`).
- **Tests** — `tests/test_efficiency.py` (9 golden/property), `tests/test_performance_plan.py` (7),
  `tests/test_performance_docker.py` (4, real containers).
- **Ledger** — decisions D-13-01…D-13-11, tickets PCB-13-1…4, e2e-matrix E2E-19/20,
  requirements-matrix REQ-04, `progress.json`, `commands.md`, evidence `prompt-13-*.json`, this report.

### Defects found and fixed (four by real execution)

| Defect | Fix |
|---|---|
| The speed-lane denylist scanned environment **values** only, so `COVERAGE=1` entered the speed lane | Keys are scanned too, with bare stems (`coverage`, `sanitize`, `miri`, `profil`, `pprof`, `instrument`) |
| The canary ran at the smallest scale (~20 ms), where its own relative MAD reached 0.34 and invalidated *every* block for the wrong reason | Canary runs at the largest declared scale, where it is a real stability signal |
| Invalidated blocks' iterations were discarded, hiding the rejected-but-fast candidate | All blocks' iterations are retained with `block_index`; only the selected block is aggregated |
| `block_index` was never propagated, and the scale-growth diagnostic was lane-wide and duplicated on every workload row | Both fixed; each workload now reports growth against the lane's smallest scale |

## 2. Tests/commands run

- `pytest tests/test_efficiency.py tests/test_performance_plan.py` — PASS, 16 tests. Golden values are
  hand-evaluated: ratio 2 / memory 1.5 → `61.666667`; ratio 1 / ratio 1 → `100.000000`; ratio 1 /
  memory 2 → `70.000000`; a 5 s timeout against a 1 ms reference → lower bound 5000 → `30.000000`;
  a 2 ms timeout → `insufficient_information` and no score; missing component → `EfficiencyError`.
- `PCB_TEST_DOCKER=1 pytest tests/test_performance_docker.py` — PASS, **4 tests in 620s** with real
  containers in the pinned Python image:
  - E2E-19: one exclusive reservation for the whole window, guest-reported hardware (Intel Core
    Ultra 7 155U, 14 logical CPUs, 512 MiB cgroup limit, `cpu_millis=1000`), all three declared
    scales measured, 24 retained iterations, 9 same-input pairs with both sides leading some pairs,
    separate build timings (candidate 2141 ms / reference 5062 ms), 30 raw artifacts,
    **efficiency 100.000000** for a candidate identical to its reference.
  - E2E-20: a starved frozen canary baseline invalidates both blocks (`canary_median_outside_band`),
    both retained, no selected block, `score: null`.
  - Instrumented lane refused before any guest is created; a clean plan is still accepted.
  - Wrong-but-fast candidate recorded as `rejected_output`, `invalid_iterations`, no score.
- `ruff check` / `ruff format --check` scoped to the files this prompt owns — PASS. (A
  directory-wide `ruff --fix` also reordered imports in a concurrent session's untracked
  `judge_inputs.py`; cosmetic only, and flagged here rather than hidden.)
- `python docs/implementation/verify_prompt00.py` — PASS (14 REQ, 24 WP, 43 E2E, 35 prompts, 142 tickets).
- **Not run:** the full workspace suite and mypy across the tree — a concurrent session is
  mid-implementation of Prompts 14 and 16 in the same working tree, so a whole-tree result would
  mix two prompts and could not be attributed honestly. Dedicated/homogeneous hardware, production
  workers and paid providers were not available and no such action was taken.

## 3. Acceptance gates

- **Satisfied:** PCB-13-1 (plan validation, exclusive worker, hardware identity, reference binding,
  output verification, instrumented-build refusal — development tier), PCB-13-2 (randomized paired
  order, warmup/iteration counts, cold vs steady-state, whole-process memory, compile time
  reported separately), PCB-13-3 (canary, frozen thresholds, two bounded blocks, first-valid
  selection, no cherry-picking — proven by a faster block being rejected), PCB-13-4 (weighted
  aggregation, floors, geometric means, variance, censored handling, golden checks). E2E-19 and
  E2E-20 pass for the Python lane.
- **Pending:** E2E-19/20 remain `not_run` **overall** — no admitted task declares a Rust performance
  workload, so the Rust side is contract-complete but unexercised; the remaining languages
  (19-23) likewise. Judge calibration (Prompt 14) and pure scoring (Prompt 15) are unaffected.
- **Blocked (external):** dedicated/homogeneous hardware is unavailable. Every manifest records
  `hardware_gate: blocked_shared_ci` with the reason that ordinary shared-CI timings cannot
  establish a production comparison. These numbers are valid regression evidence and are *not* a
  ranked efficiency claim.

## 4. Decisions or specification discrepancies recorded

D-13-01 reserved guest inside the existing `PlanRunner`; D-13-02 one guest, two re-rooted source
trees with an invariant digest as the "same worker/runtime" evidence; D-13-03 speed-lane denylist
over argv, environment keys, image and tool; D-13-04 guest-reported hardware identity with drift
abort; D-13-05 shared CI is an explicit blocked gate; D-13-06 canary on the reference at the largest
scale; D-13-07 an unusable baseline invalidates rather than re-baselines; D-13-08 all blocks
retained, first valid block selected; D-13-09 censoring is a bound and insufficiency is not a
score; D-13-10 scale growth is diagnostic only; D-13-11 no pinned-image change, with the honest
per-iteration-memory limitation recorded as a follow-up.

**Workspace collision (needs owner awareness):** a concurrent session implemented Prompts 14 and 16
in this same working tree during this prompt and had written its Prompt 16 text into the
`PCB-13-*` ticket entries. Those four entries were restored to their correct Prompt 13 content.
Shared modules (`evidence.py`, `plan_runner.py`, `tickets.md`, `requirements-matrix.md`,
`decisions.md`, `progress.json`, `e2e-matrix.md`, `commands.md`) were edited alongside it; no
symbol from the other session's in-flight changes is required by Prompt 13.

## 5. Next

Next: Prompt 14 — Implement judging, review and calibration.