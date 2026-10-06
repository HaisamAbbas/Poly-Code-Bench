# Operations rehearsal report: Prompt 33, 2026-10-03/04 (UTC)

**Environment class.** All runs below executed on one Windows 11 developer workstation:

- Docker Desktop;
- PostgreSQL 17.6 and SeaweedFS 4.48 from the digest-pinned images in `compose.yaml`;
- Python 3.12.10.

This is production-*shaped* rehearsal evidence. It is **not** staging evidence, and no figure here is a staging or production SLO measurement. All data is `synthetic_internal`, and nothing was published outside local stores. Evidence files are in `docs/implementation/evidence/prompt-33/`.

## 1. Isolated restore: E2E-42, local variant: PASSED

Source environment, seeded by `scripts/seed_ops_rehearsal.py`:

- a dedicated database migrated exactly as CI does, to the released revision `e5f6a7b8c9d0`;
- 22 scorecards across 11 strata, produced by the real pure scorer from hand-constructed fixtures (2 samples per stratum);
- 50 verified artifacts uploaded through the real upload/verify path;
- one Ed25519-signed exploratory release on a local board.

Backup: `pcb-ops backup create`, 9.0 s. Contents: a custom-format database dump (58 tables, 230 rows), 50 objects with SHA-256 digests, the publication store and the keyring.

Restore: `pcb-ops restore rehearse` into a brand-new container pair (fresh volumes, loopback ephemeral ports, unique label).

| Step | Result | Seconds (run 2) |
|---|---|---|
| Backup file digests | pass | 0.4 |
| Provision isolated environment | pass | 30.9 |
| Restore database (roles provisioned, then `pg_restore --exit-on-error`) | pass | 13.0 |
| Restore 50 objects | pass | 4.0 |
| Referential integrity | **113 foreign keys, 0 orphan rows**; row counts equal the backup for all 58 tables | 3.1 |
| Artifact digest integrity | **50/50** verified artifacts present, bytes hash to the recorded digest | 2.7 |
| Replay | **10 stratified scorecards** from 10 of 11 strata. Each matched outcome digest, scorecard digest, total and item count, and each DB row's gate/composite matched its archive | 4.4 |
| Rebuild projection | rebuilt projection digest `sha256:69871d67…` = signed published digest; signature valid; content lists all 22 restored members; board pointer references the restored release | 0.3 |
| Teardown and reclamation | 2 containers, 2 volumes and 1 network removed; **0 remaining** | 15.0 |

**Measured recovery time** (provision start to verified projection): **41.8 s** (run 1) and **58.8 s** (run 2), on a loaded host. This is local container restore time. It says nothing about RDS point-in-time restore, which is typically minutes to hours. The 4 h RTO and 15 min RPO remain **targets**.

**Negative control: PASSED.** `tests/test_operations_recovery_docker.py` removes one scorecard archive from the backup. The rehearsal then fails only at "artifact digest integrity" (1 missing object), reports `recovery_time_seconds: null`, and still reclaims all resources.

`pcb-ops restore verify` runs the same four verification steps against the live source environment and passes. That command is the verification half of the staging procedure.

Not covered by this variant: the selection reached 10 of the 11 strata (`unknown-gate` was not selected, by deterministic round-robin); AWS PITR; AWS Backup S3 restore; cross-account or cross-region recovery.

## 2. Operational drills: E2E-43, local variants: PASSED

| Drill | Commands (real CLIs) | Result |
|---|---|---|
| Orphan cleanup | The real `LocalDockerSandboxProvider` creates a guest with a 30 s TTL; the supervisor is dropped; `pcb-ops orphans sweep` runs before and after expiry | A live guest is never reclaimed. The expired orphan was reclaimed and verified gone **39.9 s after expiry** (sweep 31.7 s, dominated by the driver inspecting every labelled container). No container left with the drill label. 5/5 assertions |
| Key rotation + correction + withdrawal | `pcb-ops keys rotate/verify/revoke`, `pcb-release create/validate/review/approve/publish/withdraw` | 10/10 assertions: (1) old key retired and new key active; (2) pre-rotation release verifies as `retired`; (3) post-rotation release verifies as `active`; (4) successor links its predecessor; (5) withdrawn release keeps manifest and notice; (6) pointer stays on the successor; (7) stale-generation withdrawal refused (exit 4); (8) withdrawing the current release clears the pointer; (9) revoked key fails closed; (10) audit has 2 publishes and 2 withdrawals |
| Drain + stale commit | `pcb-ops workers drain`, `pcb-scheduler reap` on real PostgreSQL | A drained worker with a free slot and a ready job takes nothing. The recovered job is claimed by a new worker with a higher fence. The old worker's commit raises `LeaseLost`. The new commit succeeds (`tests/test_operations_postgres.py`) |
| Containment commands | `pcb-scheduler worker-status … disabled`, `pcb-scheduler cancel-attempt` | A disabled worker takes nothing; the attempt's jobs are cancelled (same test file) |
| Provider outage / ambiguous billing | gateway E2E-11/12 tests on the Prompt 33 database | **14 passed**: ambiguity keeps exposure, the delivery cap bounds retries, crash-after-dispatch is ambiguous, a late response is evidence only, lease loss stops dispatch before spend |

**Real orphan finding.** A dry-run sweep of the workstation's default local driver found **16 genuinely orphaned development guests**. All are admission lane, left by earlier admission runs. The oldest was about 30.7 h past TTL. `alert_firing: true` (`orphan-dry-run-local-default.json`). They were **not** removed, because they belong to other sessions' work. Reclaim command: `pcb-ops orphans sweep --provider local --provider-id local-default --image <approved image>`. This is the condition `PcbOrphanGuestBeyondTtl` exists to catch. Until now, no sweep was ever scheduled.

## 3. Migration rehearsal

`pcb-ops migrate rehearse`, run against a clean worktree of the committed HEAD:

- **empty → head**: pass.
- **`b9e04c7a1f38` → head**: pass.
- **Schemas identical**: 1,032 columns, constraints and triggers.
- **`alembic check` (models == schema)**: **FAIL**. The Prompt 26 `repair_*` migration (`e5f6a7b8c9d0`) created foreign keys without `ON DELETE RESTRICT`, plus two indexes the models do not declare. Recorded as D-33-03.

`pcb-ops migrate check` on the working tree **fails by design**: the concurrent Prompt 32 migration `a20c4e619d32` creates a second head. Both must be fixed before any deployment.

## 4. Load rehearsal (public API)

`scripts/ops_load_rehearsal.py` ran the real ASGI app with uvicorn over loopback on a synthetic development store. Traffic: 3,000 GETs at concurrency 16 across `/healthz`, `/v1/releases`, `/v1/releases/{id}`, `/v1/leaderboard` and `/v1/tasks`; 600 of them were ETag revalidations, and all 600 returned 304.

| Measure | Value |
|---|---|
| Error rate | 0 |
| Throughput | 55.2 req/s |
| Latency p50 / p95 / p99 / max | 276 / 443 / 556 / 997 ms |

This is **uncached origin** latency on a shared workstation, with no CDN. It does **not** meet, and is not evidence about, the A 15.3 proposed target of 300 ms **cached** p95. That target remains unmeasured until the staging CDN run.

### Follow-up: current PostgreSQL-backed API, 2026-10-06

The explicit API-origin mode of `scripts/ops_load_rehearsal.py` was run against the existing
loopback API at `127.0.0.1:8010`, serving the published `synthetic_internal` release
`7c7fffc5-308f-4837-af58-1d18ba752b22` from the local PostgreSQL-backed catalog. The run sent
3,000 read-only GETs at concurrency 16 across 11 release, leaderboard, language/model profile,
comparison, task, scorecard and methodology routes. It measured **19.0 req/s**, p50 **754.325 ms**,
p95 **1,539.525 ms**, p99 **2,382.115 ms**, with **0 errors** and **750/750 ETag revalidations
returning 304**.

This is a local API-origin measurement with no CDN or browser rendering. It does not meet, or
claim anything about, the staging cached-p95 target. Evidence:
`docs/implementation/evidence/prompt-33/load-rehearsal-local-postgres-api-2026-10-06.json`.

## 5. Security rehearsal

- **IaC static analysis** (Trivy 0.67.2, `trivy config infra/terraform`):
  - The first scan found 5 distinct issues. Three were fixed: CloudTrail now uses a customer-managed KMS key and is multi-region, and the SNS alert topic uses a customer-managed key.
  - Two remain and are accepted by design:
    - control-tier HTTPS egress to `0.0.0.0/0`, needed for model/judge provider APIs; the gateway's approved-endpoint registry is the control;
    - an internet-facing ALB, whose security group admits only the CloudFront origin-facing prefix list.
  - Report: `trivy-iac-scan.json`.
- **Identity enforcement (unit and CLI)**, in `tests/test_operations_deployment.py`. The environment string alone never grants trust, and each of these is refused:
  - a wrong claimed environment;
  - a missing principal;
  - a wrong account;
  - a production role presented to staging;
  - one role borrowing another's identity;
  - a template manifest;
  - a cloud principal running under a development manifest.
  
  `pcb-ops identity verify` exits 4 when STS is unavailable or the manifest is a template, and that was observed live.
- **Telemetry leakage** (`tests/test_operations_telemetry.py`):
  - Provider keys, AWS keys, bearer tokens, DSN passwords and `PCBSECRET__` values are redacted in messages, fields and exceptions.
  - Fields named like hidden tests, task statements or model content are dropped and counted.
  - Metric labels refuse content-like names and unbounded values.
- **Not executed:** live IAM/KMS/bucket-policy denial tests, CloudTrail alarm firing, penetration testing. These need staging; see staging-execution-plan.md §3.

## 6. Host observations

On this workstation, child Python processes intermittently died at start-up with Windows status `0xC000070A`, inside the OS thread pool (WMI query or `os.urandom`), before running any project code. The migration runner retries only that status for Alembic, which commits per revision. The other affected commands were re-run. No result in this report depends on a run that failed this way.
