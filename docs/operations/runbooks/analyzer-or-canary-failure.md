# Runbook: analyzer failure and performance canary failure

Covers the alert `PcbPerformanceCanaryRepeatedlyInvalid` and analyzer crash spikes.

## Signals

- `pcb_analyzer_crashes_total` rising for one language.
- `pcb_evidence_missing > 0` for a stage.
- `pcb_canary_invalid_consecutive >= 3` or `pcb_canary_drift_ratio` beyond the configured band on one `hardware_class`.

## Authorized role

On-call operator triages. Disabling an analyzer or a hardware class for scoring is a methodology-owner decision.

## Procedure

1. Confirm the failure is infrastructure, not candidate code. A required analyzer that reports `incomplete`/`missing` makes evidence incomplete. The scorer then reports unknown/blocked rather than a value: replay stratum `incomplete-analyzer` in E2E-42 **[V-local]** shows that state reproduces exactly from the archive.
2. Re-run the language plugin's conformance check to tell an image or analyzer regression from a task problem:
   - **[V-local, earlier prompts]** `python scripts/<language>_conformance.py`, for example `scripts/go_conformance.py` and `scripts/python_conformance.py`. Evidence: `docs/implementation/evidence/prompt-2x-*-conformance.json`.
3. Canary: stop scheduling performance work on the affected hardware class:
   - **[V-test]** drain those workers with `pcb-ops workers drain <worker-id> ...`.
   - **[S]** release the capacity reservation only with platform-owner approval.
4. A fixed analyzer or new hardware creates a **new evaluation identity** (T 22.3). Affected evaluations are re-run under it; old results are not overwritten.

## Expected state transitions

The evaluation moves to `infra_blocked`/`needs_review` (never `ready` with missing evidence). Workers go `active` → `draining`. After the fix, a new evaluation identity runs.

## Recovery verification

Conformance passes. Three consecutive valid canary runs reset `pcb_canary_invalid_consecutive`. `pcb_evidence_missing` returns to 0.

## Escalation

Methodology owner (scoring meaning). Platform owner (hardware).

## Never

Never score with a required analyzer missing. Never mix canary-invalid performance measurements into a release. Never silently swap the hardware class.
