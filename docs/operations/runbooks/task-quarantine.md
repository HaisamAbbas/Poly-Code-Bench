# Runbook: task quarantine

Use when a task is found broken, mislabelled or unsafe: a flaky oracle, a wrong reference, a rights problem, or suspected exposure.

## Signals

- Grading disagreement on a task: reference failing, or mutants passing.
- A rights or licensing notice.
- Suspected leak (see leaked-held-out-task-retirement.md).

## Authorized role

Curator proposes. Methodology owner approves removal from a cohort.

## Procedure

1. Stop new work on the task:
   - **[V-test]** `pcb-scheduler cancel-attempt <attempt-id> --reason "task quarantine <ref>"` (`tests/test_operations_postgres.py`) for each queued attempt.
   - Already completed attempts keep their evidence.
2. Mark the affected evaluations. The evaluation state machine includes `quarantined` (`polycodebench_core.models.EvaluationState`).
   - **Gap (recorded for Prompt 34):** no operator CLI sets task or evaluation quarantine yet. Until it exists, quarantine is a curator change to the **task set**: freeze a new task-set version without the task (`python scripts/pcb.py taskset create|freeze`, **[V-local]** in Prompts 05–17).
   - Record the reason in the task-set manifest. Never edit the frozen task version in place.
3. Any release that contains the task gets a correction successor (publication-rollback-and-withdrawal.md) with the quarantine reason.

## Expected state transitions

The task version stays frozen and immutable. A new task-set version excludes it. Affected attempts are `cancelled` or complete under the old set, and the release is succeeded by a correction.

## Recovery verification

The new task-set digest differs and is used by new runs. The release's methodology page shows the correction. Replay of the old release's scorecards still reproduces them (history is preserved).

## Escalation

Methodology owner.

## Never

Never delete the task, its attempts or their evidence. Never drop the task's failures from an already-published cohort without a correction successor.
