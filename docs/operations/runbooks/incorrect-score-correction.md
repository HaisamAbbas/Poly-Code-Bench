# Runbook: incorrect score correction

Use when a published score is found wrong because of a scorer, evidence or configuration defect.

## Signals

- A replay mismatch: `pcb-score replay` or the E2E-42 restore rehearsal reports `replay_matched: false`.
- A reported scoring defect.
- `pcb_publication_validation_failures_total` rising.

## Authorized role

Methodology owner decides. Curator and release approver execute.

## Procedure

1. Reproduce from the archive alone, with no provider calls and no task execution:
   - **[V-test]** `pcb-score replay --policy <policy> --ownership <ownership> --task <task> --evidence <manifest> --language-profile <profile> --archived-outcome <outcome.json>` (`tests/test_scoring_replay.py`, E2E-24).
   - **[V-local]** For restored environments, `pcb-ops restore verify ...` replays 10 stratified scorecards. 10 of 10 matched in Prompt 33.
2. Classify the defect:
   - If the archive replays identically but the **policy** is wrong, this is a methodology change. Create a new scoring-policy version and recompute the affected cohort. A changed policy digest is refused by replay; it does not get silently rescored.
   - If the archive fails to replay, it is an integrity incident. Follow database-object-store-restore.md.
3. Publish a correction successor that lists the changed tasks, policies and evidence, and the reason. Follow publication-rollback-and-withdrawal.md; the steps are **[V-local]** in the drill.
4. Withdraw the incorrect release with a notice that links the successor.

## Expected state transitions

Old release `published` → `withdrawn` (historical notice). A new release, `predecessor=<old>`, goes `published`. The new policy version is recorded.

## Recovery verification

The successor's manifest verifies. The old release still resolves with its notice. Replaying the successor's scorecards reproduces them.

## Escalation

Methodology owner.

## Never

Never edit a published release, scorecard or score item in place. Never rescore an old cohort under a new policy without a new labelled release.
