# Self-repair methodology: the repair protocol and its fixtures

Prompt 26 (PCB-26-4). This document is the frozen description of PolyCodeBench's self-repair
candidate protocol, its admitted fixtures, and where their methodology boundary lies.

## Methodology boundary (PCB-26-4)

The native reference for self-repair is the **LiveCodeBench self-repair scenario**, recorded in
`docs/methodology/livecodebench.md`: LiveCodeBench continuously collects time-stamped
competitive-programming problems and defines distinct scenarios including self-repair, preserving
each scenario's own input/feedback rules and its official metric per selected code/data revision.

The fixtures here are **authored adaptations**, labelled `adapted` in their manifests - never a
native reproduction. `taskpacks/self-repair/py-listsort-v1` is a custom authored problem (a Python
`sort_names(rows)` developer request) driven through the PolyCodeBench feedback loop, not an
imported LiveCodeBench task. No official LiveCodeBench task, test, or score is used, and **no
claim of official task access or score comparability** is made anywhere in the packs, the reports
or this document. Nothing here is measured on the native benchmark, so no result can be compared
with a LiveCodeBench self-repair score, and none is reported or implied.

Labels remain a provenance record, not a ranking: `native` is reserved for imported upstream
benchmark records; this pack is `adapted` because the scenario shape is taken from a public
methodology description while every task asset is authored by PolyCodeBench.

## The repair protocol (`self-repair-v1`)

Self-repair is modelled in `polycodebench_core.repair_contracts` as an explicitly versioned
candidate protocol over a single-shot base solve protocol (`RepairProtocol`, prompt policy
`pcb-repair-v1`). The invariants the fixtures and runs are held to:

- **Rounds are ordered candidate rounds with permitted PUBLIC feedback only.** Every round is an
  immutable record (candidate digest, exact request, the feedback given, its cost), appended and
  never edited. A repair round may be driven only by `RepairFeedback` built from the frozen public
  case inventory; hidden case results cannot be attached to feedback at all
  (`HiddenFeedbackRejected`). For py-listsort-v1 that means each round sees only the public case
  results of its previous submission (`visible/tests_public/`), never the hidden inventory.
- **Round and budget limits are fixed before any candidate exists.** `RepairLimits` freezes
  maximum rounds, model calls, tokens, time and cumulative cost; a changed limit is a new protocol
  version, and a run over any limit stops with `RepairBudgetExhausted` - there is no
  discretionary extra round. The pack's `protocol_constraints` record `protocol_id: self-repair-v1`
  with `maximum_model_turns: 3`, `maximum_tool_calls: 0`, `maximum_wall_seconds: 30`.
- **The protocol selects the final artifact - never hidden-score best-of.** `select_final` is a
  pure function of the frozen `selection_rule` (`final_round` or `first_public_pass`) and the
  rounds' own *public* results. It has no parameter through which a hidden score could arrive.
  Initial and final native correctness are recorded afterwards in `RepairMetrics`; selection never
  reads them.
- **Infrastructure redelivery is not a repair round.** `redeliver` is the only recovery
  transition: it adds a delivery to the last round and its cost to the accumulated spend. It
  cannot create a round, move the round counter backwards, or erase spent budget.

## Native versus adapted

| Aspect | LiveCodeBench self-repair (native) | PolyCodeBench `self-repair-v1` (adapted) |
| --- | --- | --- |
| Input / task material | Time-stamped competitive-programming problems collected by LiveCodeBench; the scenario's own input format, preserved per selected revision and rechecked against that revision before any import (see the methodology record). | Custom authored developer request: implement `sort_names(rows)` in one submitted file (`solution.py`), with fixture-authored public and hidden `unittest` inventories. |
| Feedback rules | The scenario's own feedback rules, preserved unchanged per selected revision; exact rules must be rechecked against the chosen code/data revision rather than inferred across scenarios. | Public test results of the previous round only (`feedback_policy: public_tests_only`); hidden case results can never enter feedback. |
| Repair loop & budget | The scenario's own repair setting, preserved per selected revision. | Ordered candidate rounds under limits frozen before any candidate exists (`maximum_model_turns: 3`, `maximum_tool_calls: 0`, `maximum_wall_seconds: 30`); final artifact chosen by the frozen `selection_rule`. |
| Metric | The official scenario metric, preserved per revision; no native score is generated or claimed in this workspace. | PolyCodeBench acceptance: test groups `public-listsort-v1` and `hidden-listsort-v1`, with `hidden-listsort-v1` as the hard condition. No cross-benchmark comparability. |
| Provenance label | `native` (imported upstream benchmark records only). | `adapted` (authored adaptation, declared in the pack manifest). |

## The fixture pack: `taskpacks/self-repair/py-listsort-v1`

A standard task package (strict import through `TaskPackageImporter`, visible/hidden separation,
disclosure scan) plus suite-mode fixture declarations whose expectations are authored before any
variant is executed:

- The **initial candidate** (`visible/repo/solution.py`) is a real but wrong implementation:
  case-sensitive whole-row ordering. It passes 2 of 3 public cases (`test_public_basic`,
  `test_public_stability`) and fails `test_public_order_insensitive`, so the repair loop sees
  visible feedback, and it fails both hidden cases. The fixed reference passes all 5.
- **Visible/hidden split.** The solver sees `visible/task.md`, the candidate scaffold and
  `visible/tests_public/`; the hidden inventory (`hidden/tests_hidden.py`) and the admission
  variants stay in the hidden bundle. Public failures are the only feedback a repair round gets.
- **Admission variants** (declared in `manifest.yaml`, executed only for admission evidence):
  `reference` (stable key sort), `alternative` (an independent correct decomposition - an explicit
  pairwise comparator rather than a key function), `faulty` (repairs case sensitivity but breaks
  tie stability, so it fails `test_hidden_stability` while passing every public case), and
  `quality_defective` (functionally passes all 5 while a convention scan reports the declared
  defect families `convention` and `duplication`).

## What is not claimed

- **No live model calls.** Every repair run exercised in this workspace is a scripted fixture over
  authored candidates; no provider or live model call is made or implied by any fixture outcome.
- **No official scores.** No LiveCodeBench score, no native metric export, and no score
  comparability with the native benchmark or any other benchmark.
- No production-worker execution: admission evidence is local fixture execution, per the Prompt 25
  evidence boundary.
