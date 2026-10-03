# Runbook: judge drift

Covers the alert `PcbJudgeDisagreementHigh` and judge revision changes.

## Signals

- `pcb_judge_disagreement_ratio > 0.2` for 30 minutes.
- `pcb_judge_invalid_total` rising.
- The provider announces a judge model revision.
- The calibration audit disagrees with human labels.

## Authorized role

On-call operator pauses. The methodology owner owns judge identity and calibration.

## Procedure

1. Inspect the unresolved queue: `pcb-judge review-queue --limit 50`. This was verified in Prompt 14 tests; in staging it is **[S]**.
2. Re-run calibration against the human labels:
   - `pcb-judge calibration --packets <packets.json> --labels <labels.json> --judge-results <results.json> --report <out>` (Prompt 14, `tests/test_judging*.py`).
   - **Blocked today**: no human calibration labels exist, so calibration reports blocked and scoring stays inactive. That is an existing project blocker, not a Prompt 33 one.
3. If the judge revision changed, the judge panel is a **new evaluation identity** (T 22.3). Pause judge stages for the affected cohort with `pcb-scheduler cancel-attempt` for queued work. Results already computed stay tied to the old panel.
4. Adjudicate disagreements with `pcb-judge adjudicate ...`. Human adjudication is recorded; it never overwrites votes.

## Expected state transitions

judge packet `pending` → `disagreement` → `adjudicated`. Panel identity stays fixed per cohort. A new panel means a new cohort.

## Recovery verification

The disagreement ratio returns below threshold on the same panel. The calibration report passes the T 15.3 gate before scoring is re-enabled.

## Escalation

Methodology owner.

## Never

Never swap the judge model inside a cohort. Never drop disagreeing packets. Never enable scoring without passing calibration.
