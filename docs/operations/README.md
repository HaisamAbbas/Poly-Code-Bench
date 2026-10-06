# PolyCodeBench operations

This directory is the operator handbook for WP-24 (Prompt 33). It covers deployment, recovery, drills and runbooks.

| Document | Purpose |
|---|---|
| [staging-execution-plan.md](staging-execution-plan.md) | Exact remaining steps, inputs and budget for the first staging deployment. Nothing has been provisioned. |
| [oracle-free-tier-readiness.md](oracle-free-tier-readiness.md) | OCI Free Tier feasibility for a private synthetic POC and the gap to cloud staging. |
| [alibaba-trial-staging-readiness.md](alibaba-trial-staging-readiness.md) | Alibaba trial feasibility, verified quota limits and prerequisites for an isolated target. |
| [retention-and-rights-policy.md](retention-and-rights-policy.md) | What is kept, for how long, who may read it, and how holds override deletion. |
| [rehearsal-report-2026-10.md](rehearsal-report-2026-10.md) | Restore, drill, load and security rehearsals that were actually executed, with measurements. |
| [runbooks/](runbooks/) | One runbook per required procedure (T 22.7) and per alert. |

## Environments and where they are defined

| Environment | Manifest | Infrastructure | Isolation tier | State today |
|---|---|---|---|---|
| `dev` | `config/environments/dev.yaml` | `compose.yaml` | development | deployed locally |
| `integration` | `config/environments/integration.yaml` | `compose.yaml` and the CI service containers | development | deployed locally/CI |
| `staging` | `config/environments/staging.yaml` (template) | `infra/terraform/environments/staging` | production | **not provisioned**: no authorized AWS account |
| `production` | `config/environments/production.yaml` (template) | `infra/terraform/environments/production` | production | **not provisioned** |

A process never decides its environment from a string. `pcb-ops identity verify` runs as the container entrypoint. It resolves the process's STS principal against the manifest, and refuses to start on any mismatch: exit 4. IAM tags, the permissions boundary, KMS key policies and bucket policies enforce the same environment and role boundary in AWS itself. See `infra/terraform/modules/identity`.

## Roles that appear in runbooks

| Role | Identity (staging/production) | Local equivalent |
|---|---|---|
| On-call operator | `pcb-<env>-oncall-operator` (SSO, MFA) | developer with Docker access |
| Release approver | `pcb-<env>-release-approver` (SSO, MFA) | `pcb-release` with `reviewer` and `publisher` roles |
| Restore operator | `pcb-<env>-restore-operator` task role, started by the on-call operator | `pcb-ops restore rehearse` |
| Ops reaper | `pcb-<env>-ops-reaper` task role on a 5-minute schedule | `pcb-ops orphans sweep --provider local` |
| Migrator | `pcb-<env>-migrator` task role | `pcb-ops migrate upgrade` with `PCB_MIGRATION_DATABASE_URL` |
| Platform owner (escalation) | named owner of the AWS account and budget | repository owner |
| Methodology owner (escalation) | owner of scoring/judging policy decisions | repository owner |

## Command status legend used in runbooks

- **[V-local]**: executed during Prompt 33 against the local Docker/PostgreSQL/SeaweedFS stack. The result is recorded under `docs/implementation/evidence/prompt-33/`.
- **[V-test]**: exercised by an automated test that passed during Prompt 33. The test name is given.
- **[S]**: a staging/production command. It is written against the deployed IaC names, but has **not been executed**, because no authorized AWS account exists. Treat its first execution as part of the staging rehearsal in the execution plan.

## Invariants every runbook keeps

- Never delete evidence, scorecards, manifests, audit rows or published objects to make a run look complete. Failures stay in the denominator; corrections are successors; withdrawals keep the historical URL.
- Never weaken a gate, budget, isolation tier or alert threshold to clear an incident. Change those only by a reviewed configuration change.
- Unmeasured SLOs are **targets**: 15 min RPO, 4 h RTO, 10-minute orphan reclamation, and a 300 ms cached public p95. Measured values are in the rehearsal report, and none is claimed for staging or production.
