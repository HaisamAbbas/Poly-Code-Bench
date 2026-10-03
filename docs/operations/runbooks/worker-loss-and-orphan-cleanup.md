# Runbook: worker loss and orphaned guest cleanup

Covers the alerts `PcbOrphanGuestBeyondTtl` and `PcbRequiredQueueStalled`, and E2E-43's orphan subcase.

## Signals

- `pcb_orphan_guests_over_alert_threshold > 0`: a guest has outlived its TTL by more than 10 minutes.
- `pcb_queue_oldest_age_seconds` above the environment SLO while `pcb_queue_depth > 0`.
- Supervisor task stopped or crashed (ECS events). `pcb-scheduler reap` reports `guest_ids` for cleanup.

## Authorized role

On-call operator. Reclamation itself runs as `ops-reaper`. That role may terminate only instances tagged `pcb:owner=polycodebench` in its own environment.

## Procedure

1. See what is orphaned without changing anything:
   - **[V-local]** `pcb-ops orphans sweep --provider local --provider-id local-default --image <approved image> --dry-run`. On the Prompt 33 host this found **16 real expired admission guests**, the oldest about 30 h past TTL, with `alert_firing: true`. Evidence: `orphan-dry-run-local-default.json`.
   - **[S]** `aws ecs run-task --cluster pcb-<env> --task-definition pcb-<env>-ops-reaper --overrides '{"containerOverrides":[{"name":"ops-reaper","command":["pcb-ops","orphans","sweep","--provider","ec2","--environment","<env>","--dry-run"]}]}' ...`
2. Recover leases so the work is retried under a new fence:
   - **[V-test]** `pcb-scheduler reap --limit 100` (`tests/test_operations_postgres.py`). It prints `job_id`, `slot_ids` and `guest_ids`.
3. Reclaim the expired guests:
   - **[V-local]** `pcb-ops orphans sweep --provider local --provider-id <id> --image <approved image>`. In the drill, a guest whose supervisor was lost was reclaimed 39.9 s after expiry and verified gone. Evidence: `e2e-43-local-drills.json`.
   - **[S]** the same `run-task` without `--dry-run`. The scheduled sweep also runs every 5 minutes.
4. If a guest survives the sweep (`"clean": false`), look up its instance. Termination protection is never set by the launch templates, so check whether the instance is outside this environment's tags. That would be a tagging incident; escalate.

## Expected state transitions

stage job `leased` → lease expires → `reap` returns it to `ready` (fence + 1) and frees the slot → the guest is destroyed by the sweep → `pcb_orphan_guests` returns to 0.

## Recovery verification

The sweep's JSON shows `"clean": true`. The next scheduled sweep reports `expired_before: 0`. The re-queued job completes under the new fence.

## Escalation

Platform owner if orphans persist after two sweeps, or if an orphan lacks `pcb:` tags. The latter can mean resources were launched outside the supervisor.

## Never

Never terminate instances by hand outside the reaper role. Never mark the lost attempt "completed". Never delete its stage events.

## Known limitation

`Ec2VmSandboxProvider.collect_expired` only manages solve, grading and admission guests. The `ops-reaper` sweep covers every PolyCodeBench-tagged lane, including `performance`, and treats a guest without a parseable expiry as expired (`tests/test_operations_deployment.py::test_ec2_sweep_reclaims_every_expired_lane_but_not_live_or_foreign`).
