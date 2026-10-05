# Runbook: deployment, migration, rollback and worker drain

Covers T 22.4 rollout, the alert `PcbStaleCommitRefusals`, and the expand → backfill → contract procedure.

## Signals

- A planned release.
- `PcbStaleCommitRefusals`: workers keep losing leases. Their results are refused, not lost.
- `pcb-ops doctor` or `pcb-ops env reconcile` reports drift between the manifest and the deployment.

## Authorized role

The on-call operator deploys. The migrator task applies schema changes. Contract (destructive) migrations additionally need an approved retention plan, listed in `config/operations/migration-policy.yaml`, and platform-owner sign-off.

## Pre-flight

```bash
pcb-ops env validate             # [V-local] exit 0: all four manifests valid, no shared resources
pcb-ops doctor --profile staging # [V-local] exit 3 today: staging is a template with unresolved inputs
pcb-ops migrate check            # [V-local] expand-only check since the released revision
pcb-ops migrate rehearse --admin-url <admin dsn> --persistence-root <release worktree>/packages/persistence
                                 # [V-local] empty->head and previous->head must give identical schemas
```

Prompt 33's original migration snapshot (superseded by the fixes below):

- The first rehearsal had pre-existing model/schema drift in the Prompt 26 `repair_*` tables (D-33-03).
- The concurrent Prompt 32 migration initially created a second head (D-33-04).

Current local validation:

- `migrate check` passes at head `b390a26f17cd`. The only constraint replacements are the three
  policy-listed `NO ACTION` to `RESTRICT` changes for repair tables. The checker allows only exact
  table, constraint, and column matches recreated with `RESTRICT`; unmatched, weakened, and raw
  SQL constraint drops remain blocked. The migration bounds lock waits at five seconds and each
  DDL statement at 30 seconds; PostgreSQL rolls the FK replacement back as one transaction on
  timeout.
- PostgreSQL migration rehearsal passes from an empty schema and from `b9e04c7a1f38` to head;
  `alembic check` passes and both resulting schemas match (1,064 objects).
- `pcb-ops doctor --profile dev` passes. Staging remains a template with 29 unresolved
  owner-supplied inputs and requires a verified AWS principal; no staging resource has been
  provisioned.

## Procedure (expand / contract)

1. **Expand release.** Ship additive migrations only: new tables, nullable columns, new indexes. `pcb-ops migrate check` must pass.
2. Run the migration:
   - **[S]** `aws ecs run-task --task-definition pcb-<env>-migrator` (command `pcb-ops migrate upgrade`). It refuses to run if the expand-only check fails.
   - **[V-local]** locally: `PCB_MIGRATION_DATABASE_URL=<dsn> pcb-ops migrate upgrade --target <revision>`.
3. Drain workers of the old code:
   - **[V-test]** `pcb-ops workers drain <worker-id> ...` (`tests/test_operations_postgres.py`). Draining workers take no new claims, even with a free slot and ready work. In-flight leases either complete or expire. After `pcb-scheduler reap`, the job runs under a higher fence, and the stale worker's `complete` raises `LeaseLost`.
4. Deploy the compatible services:
   - **[S]** `terraform apply` with the new image digests. ECS rolls out with `minimumHealthyPercent=100` and a circuit breaker with rollback. Each task's entrypoint runs `pcb-ops identity verify`, and a mis-targeted task exits 4 before serving.
5. Backfill and validate in a later release. **Contract** in a third release, only if the revision is approved with a retention plan.

## Rollback

Roll back **code**, not data: redeploy the previous image digests (`terraform apply` with the previous `services` map). The expanded schema stays, and old code must tolerate it, which is the expand rule. Never downgrade migrations: revisions refuse `downgrade` to preserve evidence. Publication rollback is a separate procedure (publication-rollback-and-withdrawal.md).

## Expected state transitions

workers `active` → `draining` → (lease complete or expiry + reap) → old tasks stopped; jobs `leased` → `ready` (fence + 1) → `completed` by new workers; `alembic_version` moves from the released revision to the new head.

## Recovery verification

- `pcb-ops doctor --profile <env>` shows no migration problems.
- `pcb_stale_commit_refusals_total` stops increasing.
- The queue age alert stays clear.
- The structured logs from the migration and rollout carry `run_id`/`job_id`/`fence` and no secrets. Redaction is covered by `tests/test_operations_telemetry.py`.

## Escalation

Platform owner for failed rollouts. Methodology owner if a migration touches scoring or provenance tables.

## Never

Never run a contract migration in the same release as its expand. Never drop or rewrite published provenance. Never bypass `migrate check` by running `alembic` directly in staging or production.
