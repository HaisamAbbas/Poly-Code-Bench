# Runbook: database and object-store restore

Covers T 22.6 recovery, quarterly restore rehearsals (E2E-42) and the alerts `PcbBackupRestoreIntegrityFailure` and `PcbRestoreRehearsalOverdue`.

## Signals

- `PcbBackupRestoreIntegrityFailure` (`pcb_backup_integrity_failures > 0`), or an AWS Backup vault notification (`BACKUP_JOB_FAILED`, `RESTORE_JOB_FAILED`, `S3_BACKUP_OBJECT_FAILED`).
- `PcbRestoreRehearsalOverdue`: no verified rehearsal in about 99 days.
- Real incident: database unavailable, corrupted, or data lost; object reads fail integrity verification (`ArtifactRepository.read_verified` raises).

## Authorized role

On-call operator starts the work. The restore itself runs as `restore-operator`, which may create and delete only DB instances tagged `pcb:purpose=restore-rehearsal`. Promoting a restored database to serve traffic requires the platform owner.

## A. Rehearsal (no incident). Run quarterly and before first production launch

Local production-shaped rehearsal (verified):

```bash
# [V-local] seed a labelled synthetic source (22 scorecards, 11 strata, one signed release)
uv run --offline --locked --all-packages python scripts/seed_ops_rehearsal.py --replace
# [V-local] back up database, objects, publication store, keyring
uv run --offline --locked --all-packages pcb-ops backup create --out .local/ops-rehearsal/backup-<UTC>
# [V-local] restore into a NEW isolated pair, verify, replay 10 stratified scorecards,
#           rebuild the public projection, measure, tear down, prove reclamation
uv run --offline --locked --all-packages pcb-ops restore rehearse \
  --backup .local/ops-rehearsal/backup-<UTC> --evidence docs/implementation/evidence/prompt-33/e2e-42-local-restore.json
```

Expected result: exit 0 and `"passed": true`, with a measured `recovery_time_seconds` (41.8 s and 58.8 s on the Prompt 33 host) and `"resources_reclaimed": true`. Any failed step gives exit 1. The step name says what failed. `recovery_time_seconds` is then `null`, because a restore without verified artifacts is not a recovery.

Staging rehearsal against AWS (not yet executed):

```bash
# [S] 1. point-in-time restore into a tagged, isolated instance (restore-operator role)
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier pcb-staging-postgres \
  --target-db-instance-identifier pcb-staging-restore-$(date -u +%Y%m%d%H%M) \
  --use-latest-restorable-time --db-subnet-group-name pcb-staging-data \
  --vpc-security-group-ids <database-sg> --no-publicly-accessible \
  --tags Key=pcb:purpose,Value=restore-rehearsal Key=pcb:environment,Value=staging
aws rds wait db-instance-available --db-instance-identifier pcb-staging-restore-<ts>
# [S] 2. restore the internal/public buckets to the same recovery point into rehearsal buckets
aws backup start-restore-job --recovery-point-arn <s3-recovery-point> \
  --iam-role-arn <pcb-staging-backup-service-role> --metadata DestinationBucketName=<rehearsal-bucket>,NewBucket=true
# [S] 3. verify (same code path as the local rehearsal; S3 via the role credentials)
pcb-ops restore verify --database-url <restored-dsn> \
  --bucket-hidden <hidden> --bucket-internal <restored-internal> --bucket-public <restored-public> \
  --release-store <restored-publication-store> --keyring <published /keys/keyring.json> \
  --board staging:test-board --evidence restore-verify-<ts>.json
# [S] 4. reclaim
aws rds delete-db-instance --db-instance-identifier pcb-staging-restore-<ts> --skip-final-snapshot
```

`pcb-ops restore verify` itself is **[V-local]**: it passed against the local source environment. Record the wall-clock time from step 1 to the end of step 3 as the measured recovery time.

## B. Incident restore

1. Declare the incident. Stop writers: scale `scheduler`, `solve-supervisor`, `eval-supervisor`, `scorer` and `publisher` to zero **[S]**: `aws ecs update-service --cluster pcb-<env> --service <svc> --desired-count 0`. In-flight leases expire, and fences refuse late commits (see deployment-migration-and-drain.md).
2. Pick the recovery point: the last time before the fault. Run section A steps 1–3 against it.
3. If verification passes, the platform owner repoints `PCB_DATABASE_DSN_REF` and the bucket configuration through a Terraform change, never by hand-editing tasks. Then redeploy and scale services back up.
4. If verification fails, do **not** promote. Pick an earlier recovery point and escalate.

## Expected state transitions

source healthy → backup verified (file digests) → isolated environment up → database and objects restored → integrity, digest, replay and projection checks pass → measured → reclaimed. In an incident, the last state is "promoted".

## Recovery verification

- Every foreign key has zero orphan rows. Row counts equal the backup manifest.
- Every `verified` artifact's bytes hash to its `content_digest`.
- Ten stratified scorecards replay byte-identically, and their rows match the archive.
- The rebuilt projection digest equals the signed manifest. The signature verifies against the keyring. The board pointer references an available release.

## Escalation

Platform owner. Also the methodology owner if any replayed scorecard differs, because that is a scoring-integrity incident, not an infrastructure one.

## Never

Never promote a database whose artifacts failed verification. Never "fix" a replay mismatch by rescoring. Never delete the failed restore's evidence file.
