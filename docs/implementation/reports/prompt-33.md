# Prompt 33 — BLOCKED

## Blocked on me

- **Staging authorization.** There is no authorized AWS account, region, monthly cap (proposed: USD 600/month plus USD 150 one-off), operator SSO principals, state backend, domain/certificates, approved guest AMI, or pushed service image digests. Without these, no clean staging deployment and no staging E2E-42/E2E-43 run is possible. The exact inputs, commands and budget are in `docs/operations/staging-execution-plan.md`.

## Resolved during audit repair

- **Migration drift (D-33-03).** Declared the existing `ix_repair_round_run` and `ix_repair_delivery_round` indexes in the model and added migration `d8f971ea2b34` to align the three repair-table foreign keys with the model's `ON DELETE RESTRICT` rules. On isolated PostgreSQL 17.6, upgrade, downgrade, re-upgrade and `alembic check` pass; `tests/test_repair_state_postgres.py` passes 5/5.

## Changed

- **IaC.** `infra/terraform/` has 13 modules: network, keys, identity, database, backup, artifacts, registry, control services, workers, performance, public delivery, telemetry and stack. Separate staging and production roots each carry their own state, an `allowed_account_ids` guard, an AWS Budgets cap, and parameters kept apart from module code. `infra/sandbox/aws` is now a reusable child module.
- **Identity enforcement.**
  - IAM roles carry `pcb:environment`/`pcb:role` tags under a permissions boundary that denies cross-environment resources and changes to identity tags.
  - KMS and bucket policies enforce the same boundary. The hidden bucket and key admit only the grading and admission roles.
  - In the application, `polycodebench_core.deployment` resolves environment, role and isolation tier from the verified STS principal against `config/environments/<env>.yaml`.
  - `pcb-ops identity verify --exec` is every Python container's entrypoint.
- **Operations package.** `packages/operations` provides the `pcb-ops` CLI:
  - doctor and env validate/reconcile;
  - migrate check/rehearse/upgrade, with an expand-only gate;
  - workers drain, orphans sweep, artifacts collect-garbage;
  - backup create, restore rehearse/verify;
  - keys rotate/revoke/verify (a new `publication/keyring.py` that retains old verification keys);
  - alerts check.
- **Telemetry.**
  - Redacting JSON logs carry correlation IDs and drop held-out-content fields.
  - A bounded metric catalog covers the T 22.5 signals, wired into the worker (stale-commit refusals, completions) and `pcb-scheduler` (expired leases, `/metrics`).
  - 11 alert rules with promtool tests live in `infra/observability/prometheus/`, plus a Grafana dashboard, a CloudTrail hidden-access alarm and AMP loading in IaC.
- **Hardening found while rehearsing.**
  - Lifecycle prefixes are reserved as encryption domains (`object_store.py`).
  - The EC2 orphan sweep now covers every lane, not just the three the driver manages.
  - Trivy fixes: CloudTrail and SNS now use customer-managed KMS keys, and CloudTrail is multi-region.
- **Docs.** `docs/operations/` holds 12 runbooks, the retention/rights policy, the staging plan and budget, and the rehearsal report. CI gained an `infra` job.
- **Scripts.** `scripts/seed_ops_rehearsal.py`, `ops_drills.py` and `ops_load_rehearsal.py`; tests in `tests/test_operations_*.py`.

## Found

- **Restore.** `pcb-ops restore rehearse` (local isolated containers) **PASS**:
  - 113 foreign keys checked, 0 orphan rows; row counts equal to the backup;
  - 50/50 artifact digests;
  - 10 stratified scorecards (10 of 11 strata) replayed byte-identically;
  - projection digest equals the signed manifest;
  - recovery measured at 41.8 s and 58.8 s; resources reclaimed.
  
  The missing-artifact negative control fails as required. Evidence: `docs/implementation/evidence/prompt-33/e2e-42-local-restore.json`.
- **Drills** (`scripts/ops_drills.py`) **PASS**:
  - orphan reclaimed 39.9 s after expiry (5/5 assertions);
  - key rotation, correction and withdrawal (10/10).
  
  Drain/stale-commit and containment commands pass on PostgreSQL (`tests/test_operations_postgres.py`, 3/3). The gateway outage/ambiguity suite passes 14/14.
- **Migrations.** `pcb-ops migrate rehearse` on the committed tree: empty→head and previous→head **PASS**, with identical schemas (1,032 objects). The Prompt 32 integration rebased `a20c4e619d32` onto `e5f6a7b8c9d0`; the current chain now has the single head `d8f971ea2b34`, resolving D-33-04. D-33-03 is resolved by the model/migration alignment above. Against isolated PostgreSQL 17.6, applying the current head, downgrading one revision, reapplying head and `alembic check` all **PASS**. The deployment policy remains pinned to the last released revision until an environment is actually deployed.
- **IaC checks.** `terraform fmt -check` and `terraform validate` **PASS** for both roots. promtool check and test **PASS** (11 rules). Trivy: 3 findings fixed, 2 accepted (D-33-05).
- **Unit tests** (`tests/test_operations_telemetry.py`, `tests/test_operations_deployment.py`): 30 pass, covering telemetry, identity, manifests, migrations, keyring, sweep and rehearsal logic.
- **Load.** `scripts/ops_load_rehearsal.py`: 3,000 requests, 0 errors, uncached origin p95 443 ms on the workstation. The 300 ms cached p95 remains a **target**.
- **Regression** (worker, scheduler, jobs, artifacts, sandbox, publication, scoring replay, startup config, plus operations): **129 passed**, 0 failed (6 min 41 s; real PostgreSQL 17.6, SeaweedFS and Docker; PCB_TEST_DOCKER=1).
- **Ruff and strict mypy** on all changed Python: PASS. The only mypy findings are three pre-existing `unused-ignore` imports in `object_store.py`. `pcb-ops alerts check`, `pcb-ops env validate` and the boundary checker for `operations`: PASS. The boundary checker still reports pre-existing `evaluation` violations from concurrent work.
- `docs/implementation/verify_prompt00.py` fails on the E2E-36 row. That row is unchanged from HEAD, so the failure predates Prompt 33. The E2E-24/42/43 rows edited here pass its rule.
- **Real orphans.** A dry-run sweep found **16 genuinely orphaned local development guests** (the oldest about 30.7 h past TTL). They were left in place for their owners. Reclaim with `pcb-ops orphans sweep --provider local --provider-id local-default --image <approved image>`.
- **Not run:**
  - clean staging deployment and every staging drill (no authorization);
  - production-only steps (Multi-AZ failover, vault lock, capacity reservation, DNS cut-over);
  - hosted CI.
  
  No benchmark result was published. All rehearsal data is `synthetic_internal`.

## Current follow-up verification (2026-10-06)

- `pcb-ops migrate check` passes for the current chain through `b390a26f17cd` with no violations; `alembic check` against the local migration database reports no new operations.
- Terraform formatting passes, and staging plus production pass `terraform init -backend=false` and `terraform validate` in the pinned 1.13 container. The check used isolated temporary Terraform data directories; no plan or apply was run.
- `tests/test_operations_telemetry.py` and `tests/test_operations_deployment.py`: 36 passed on the current worktree. `tests/test_operations_postgres.py`: 3 passed against local PostgreSQL and SeaweedFS with the local migration identity.

Decisions: D-33-01 to D-33-08 in `docs/implementation/decisions.md`. D-33-03 and D-33-04 are resolved in the current tree; staging authorization remains open.

Next: provide the staging inputs in `docs/operations/staging-execution-plan.md` §1, then run plan §2–3 to close E2E-42/E2E-43. Prompt 34 — Perform the final integrated audit and repair pass — follows once Phase 7 is accepted, or by explicit authorization with Phase 7 recorded as blocked.
