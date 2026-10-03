# Retention, access and rights policy

Implements T 22.6 and A 15.4. Enforcement points are named so each rule can be audited. Durations are the defaults in `infra/terraform/modules/artifacts/variables.tf`; changing one is a reviewed configuration change.

## Retention classes

| Class | Where | Retention | Enforced by |
|---|---|---|---|
| Published manifests, public projections, verification keys | public bucket `releases/`, `keys/` | Lifetime of the release, subject to rights; never deleted by lifecycle | S3 Object Lock (governance, default 3650 days); bucket policy lets only `publisher` write; withdrawal writes a notice and never deletes |
| Scorecards, score items, minimal replay evidence (scoring replay bundles), provenance rows | PostgreSQL + internal bucket | Lifetime of any release that references them | No lifecycle rule matches them; bucket policy denies application deletes; DB guard triggers make evidence rows immutable |
| Hidden bundles (tests, oracles, private task material) | hidden bucket | While the task is admitted, then as the rights record says | Hidden-bucket policy plus KMS key allow only `eval-supervisor` and `admission-operator`; every read is a CloudTrail data event and the unexpected-access alarm fires on any other reader |
| Unreferenced provisional uploads and debug artifacts | internal `provisional/`, `debug/` | **30 days** | S3 lifecycle; `pcb-ops artifacts collect-garbage` (daily schedule) expires abandoned upload records |
| Non-published cancelled-run logs | internal `cancelled-logs/` | **90 days** | S3 lifecycle |
| Noncurrent object versions | all buckets | 35 days (longer than the 7–35 day database PITR window) | S3 lifecycle |
| Service logs | CloudWatch `/pcb/<env>/*` | 90 days | log group retention |
| Audit trail (CloudTrail) | trail bucket, `/pcb/<env>/cloudtrail` | 365 days in logs; bucket kept | log group retention; trail log-file validation |
| Database backups | RDS PITR and AWS Backup vault | PITR 7 days (staging) / 35 days (production); daily vault copies 365 days | `modules/database`, `modules/backup`; production vault lock |

## Holds override lifecycle deletion

Legal, rights and incident holds are recorded by the audited hold workflow (`ArtifactRepository.add_hold` / `release_hold`, table `artifact_retention_hold`):

- Hold rows cannot be deleted. A hold can be released exactly once, and the release is audited (database guard triggers).
- Committed artifacts are never removed by garbage collection. `collect_garbage` expires only abandoned upload records, and canonical objects that **no** artifact row references.
- The bucket expiry rules match only the `provisional/`, `debug/` and `cancelled-logs/` prefixes. Canonical evidence keys (`<domain>/<aa>/<digest>`) cannot fall under them, because the object store rejects those three names as encryption domains.

Gap recorded for Prompt 34: the hold table is not yet consulted by an explicit evidence-deletion path, because no such path exists. Any future deletion feature must refuse held artifacts.

## Access rules

- Hidden material: grading/admission lanes only. Solve guests never receive hidden bundles, and execution guests have no AWS role at all.
- Secret values are resolved only inside the gateway or service that owns their namespace (`pcb/<env>/model/*`, `pcb/<env>/judge/*`, `pcb/<env>/signing/*`).
- Logs and metrics: no source code, task statements, hidden tests, prompts, model content or secrets.
  - The structured log formatter drops fields with such names and redacts credential shapes.
  - Metric labels are bounded and named from an allowlist (`polycodebench_core.telemetry`; tests in `tests/test_operations_telemetry.py`).

## Rights

- A task is admitted only with a recorded rights basis (task admission, Prompts 05 and 11). Withdrawal of rights triggers task-quarantine.md, and the affected releases get correction successors.
- Published evidence stays available for the life of the release unless the rights holder's terms require removal. Then the release is withdrawn with a notice, and the hidden or redacted portion is placed under an incident hold rather than silently deleted.
- **Open:** rights confirmation for real pilot tasks is still an owner input (see `docs/implementation/progress.json`). This policy defines the mechanism, not the legal basis for any particular dataset.
