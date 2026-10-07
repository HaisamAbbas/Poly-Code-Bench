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
- Corrected the load rehearsal so its default remains the ephemeral synthetic loopback API, while an explicit `--base-url ... --confirm-target` mode discovers a published release and exercises its read-only public API routes. After final runner hardening, the explicit-target mode was run only against the local PostgreSQL-backed API: 3,000 requests across 11 routes at concurrency 16, 0 errors, p95 1,539.525 ms, and 750/750 ETag revalidations returned 304. Evidence: `docs/implementation/evidence/prompt-33/load-rehearsal-local-postgres-api-2026-10-06.json`. This is local API-only evidence; browser page load and staging CDN remain unverified.
- The rehearsal's under-sampled-route case (`requests < discovered routes`) initially exposed an empty-percentile crash; it now records null per-route percentiles for routes with zero load samples. The 1-request explicit loopback smoke passes.
- Added an opt-in local solve-worker assembly using the existing lease/heartbeat service, solve executor, frozen task runtime, gateway budget/ledger, verified internal artifacts and the network-disabled local Docker sandbox. The slot accepts only a canonical, digest-pinned resource document whose image digest matches the frozen task version. The production path still needs its separate guest provider and service identities.
- Extended the local bootstrap with a separate `pcb_local_worker` login, explicit-false setup/dispatch flags and 64 MiB/1 GiB internal artifact quotas. Bootstrapped and registered one `local-fixture-small` worker against the loopback PostgreSQL/SeaweedFS stack; a repeated registration reused the existing worker/config. A one-shot run with a process-only dispatch opt-in found zero matching queued jobs and claimed none. Gateway intent/delivery counts remained zero. Evidence: `docs/implementation/evidence/prompt-33/local-solve-worker-2026-10-06.json`.
- Worker verification: 8 focused unit/bootstrap tests pass; `tests/test_jobs_postgres.py` 8 passed/1 opt-in Docker skip; `tests/test_persistence_postgres.py` 4 passed; `tests/test_public_api_submissions_postgres.py` 4 passed using the restricted API role and separate migration identity. Ruff and strict mypy pass for changed production Python. The wheel-installed `pcb-worker --help` command starts successfully.
- Fresh public-flow browser rerun: Prompt 30 4/4, Prompt 31 3/3, Prompt 32 2/2. Results and refreshed screenshots are in the existing prompt evidence directories; each result file omits the machine-local Node executable path, and pages show synthetic test data only.
- At the time of the original solve-worker verification, no endpoint or model was configured and no model was called or downloaded. No cloud resource or public benchmark result was created. Judge/evaluation/scoring/publication processors and a production worker runtime remain open.

## Local inference follow-up (2026-10-06)

- Installed the official Ollama `qwen2.5-coder:1.5b` model tag (digest `d7372fd82851`, 986 MB) on the workstation. The model was used only for one local smoke completion and two loopback conformance probes; no candidate, submitted endpoint, or task data was sent.
- Registered and approved `http://127.0.0.1:11434/v1` in the local development database after basic-completion and input/output usage checks passed. Tool calling was not claimed or probed. The endpoint is confined by an explicit `127.0.0.1/32` policy.
- `pcb-model plan` reports one planned call, compatible context/usage controls, strict-cap eligibility, and a USD 0 provider-fee ceiling. This is a plan only: no run, score, or release was created. Local power and hardware costs are outside that fee figure.
- Evidence: `docs/implementation/evidence/prompt-33/local-ollama-smoke-2026-10-06.json`. This model is not calibrated or admitted for ranked releases. Production worker modes and judge/evaluation/scoring/publication processors remain open.

## EC2 launch-policy follow-up (2026-10-06)

- Found that the EC2 driver issued raw launch parameters while the IAM role only authorizes a lane-specific Terraform launch template. The driver now supplies that exact template at a numerically pinned version, includes the environment and ownership tags at creation, and validates the returned instance type, image, subnet, security group, metadata and public-address state before the first guest-control command. Terraform exposes template versions; deployed manifests require them and reconciliation checks for version drift.
- Added the missing read-only `ec2:DescribeSecurityGroups` permission to the lane supervisor roles; the runtime's isolation attestation already requires this call.
- Fake-EC2 policy verification: `tests/test_sandbox.py` passed 11 tests (one opt-in live-Docker case skipped). The negative case gives the fake instance a public IP and confirms that no guest command is sent. The deployed-manifest test rejects missing and non-positive launch-template versions; Terraform reconciliation checks the pinned version. The provider and deployment suite passed 38 tests (one opt-in live-Docker skip); Ruff, formatting and strict mypy passed. Terraform formatting and staging/production `init -backend=false` plus `validate` passed with the pinned Terraform 1.13/AWS 6.36 toolchain.
- No AWS API was called and no VM was created. A production worker runtime/CLI, approved guest AMI and launch-template deployment remain open; this fixes the existing driver/IAM mismatch only.

## Provider credential resolution follow-up (2026-10-06)

- Replaced production use of process environment credentials with a Secrets Manager resolver. Staging/production selection now requires the verified environment emitted by `pcb-ops identity verify` to match the claimed environment, plus an allowed verified STS role. Model gateway and solve supervisor resolve only `pcb/<env>/model/*`; judge gateway resolves only `pcb/<env>/judge/*`. Development and integration retain the local resolver.
- Granted the solve supervisor the same narrowly scoped model-secret read used by the accountable in-process `ModelGateway`; judge and model gateway permissions remain separately scoped. AWS errors are sanitized before reaching callers and returned credential values remain in the existing opaque `Secret` wrapper.
- Verification: `tests/test_gateway_secrets.py`, `tests/test_model_gateway_units.py` and `tests/test_model_gateway_review_regressions.py`: 82 passed, 16 database-backed cases skipped because `PCB_TEST_DATABASE_URL` is not configured. Ruff and strict mypy passed for all changed Python; Terraform 1.13.5 `fmt -check` passed for the identity policy. The resolver test uses only a fake Secrets Manager client; zero AWS API calls and zero provider calls were made.
- Adjacent wiring regression checks: `tests/test_worker_runtime.py` passed 3/3 and `tests/test_judge_cli.py` passed 10/10.
- Operators still need to provision provider secret values out of band. No real credentials were requested, stored or used. Staging remains blocked on the deployment inputs listed above.

## Local website and submission flow rerun (2026-10-06)

- Reconnected the loopback API and web application to the already-running local PostgreSQL, SeaweedFS and Keycloak services. API `/healthz` and the release-backed `/leaderboard` returned HTTP 200.
- Against the signed PostgreSQL release projection, all nine public pages returned HTTP 200 and rendered the synthetic-data notice: leaderboard, comparison, task explorer, language profile, model profile, task detail, scorecard/evidence, methodology and model submission. The API OpenAPI snapshot and generated TypeScript client parity checks passed.
- `apps/web/tests/e2e/local-stack-auth.smoke.mjs` passed against the live local stack: Keycloak OIDC login, synthetic metadata submission persisted to PostgreSQL, owner status lookup, reviewer queue access, cross-owner 404, anonymous reviewer 401, keyboard focus and horizontal scrolling, and 390 px responsive layout. The test confirmed the submitted example endpoint was never contacted.
- Local API/OIDC policy tests passed 20/20; web lint and TypeScript checks passed; Docker Compose configuration validation passed. The API and leaderboard remained healthy after verification.
- The displayed release is labelled `Synthetic internal test data`; it is not benchmark evidence. Desktop/mobile screenshots are retained only in ignored `.cache/local-stack-browser/`; the committed JSON records no account, request ID, or credential data. No cloud resource or model call was used.

## Approved submission to bounded queue (2026-10-06)

- Ran `test_approved_submission_recovers_one_bounded_postgres_run` against the actual local PostgreSQL 17.6 website database, with the API's restricted application role and a separate migration connection. The synthetic administrator path refused approval while the endpoint was pending, then approved the endpoint using a fixture conformance report, injected a retry interruption after idempotent run creation, and recovered the same run on replay.
- The test verified one queued run, one attempt and one solve job, persisted run/token limits, audit events and submitter-safe queued progress. The generated job remains queued in the local test database. `PCB_WORKER_DISPATCH_ENABLED=false`; no worker process ran, no provider endpoint or model was contacted, and no score/release was created. Evidence: `docs/implementation/evidence/prompt-33/approved-bounded-run-local-postgres-2026-10-06.json`.

## Free cloud target review (2026-10-06)

- Reviewed the current official Alibaba and OCI free-tier terms and recorded the account-specific checks in `docs/operations/free-cloud-target-review-2026-10.md`. Alibaba's listed ECS free offer is not enough to infer this account's quotas; solution trials are temporary and their data is deleted on expiry. OCI has an Always Free A1 option, but requires a different target and self-managed PostgreSQL for this application.
- No provider was selected for deployment, and no account API or cloud resource was used. Alibaba remains the preferred candidate only if the owner's console confirms the exact compute, storage, PostgreSQL/secrets/registry entitlements and an approved residual-spend limit. The checked-in Terraform remains AWS-specific.

## Post-recovery local verification (2026-10-06)

- After restarting the local services, the loopback PostgreSQL, object store, Keycloak OIDC discovery, API and web app returned healthy responses. The first manual discovery probe used the wrong realm path; the configured `polycodebench-local` realm then returned HTTP 200 and its issuer matched the application configuration.
- All nine public routes returned HTTP 200 and displayed the synthetic test-data notice. The API exposed two `synthetic_internal` release projections; no benchmark result was presented as live data.
- Re-ran the browser submission smoke: OIDC login, PostgreSQL-backed metadata/status, reviewer authorization, cross-owner denial, anonymous reviewer denial, keyboard interaction and the 390 px layout passed. The submitted example provider endpoint received zero requests.
- `pnpm --filter @polycodebench/web build` passed (Next.js production build, TypeScript and static route generation). `pcb-ops migrate check` passed at `b390a26f17cd` with no violations. Terraform 1.13.5/AWS provider 6.36.0 `init -backend=false` and `validate` passed for staging and production; no plan/apply was run.
- Evidence: `docs/implementation/evidence/prompt-33/post-recovery-local-verification-2026-10-06.json`. Browser screenshots remain only in the ignored local cache; no credentials, account identifiers or submission IDs were added to the repository. No cloud resource or model/provider call was used.

## Sandbox deployment-manifest reconciliation repair (2026-10-06)

- The staging audit found that `pcb-ops env reconcile` checked launch templates but ignored Terraform's region, operator roles, database/signing/cursor secret references and namespaces, VPC CIDR, approved guest AMI/type, lane subnets and guest/supervisor security groups. A drifted deployed manifest could therefore have been reported as reconciled while pointing at a different authority, credential namespace, VPC or EC2 network boundary.
- The strict deployed manifest now requires distinct guest lane subnets, launch templates and lane/control security groups plus a concrete AMI and guest instance class. Reconciliation compares those fields and the operator roles, database/signing/cursor secret ARNs, model/judge namespaces and VPC CIDR with Terraform output. Secret ARNs must match the manifest account, region, Secrets Manager service and referenced resource name. Environment separation also rejects reuse of principal, secret namespace, subnet, launch template or security group identities across environments. Staging and production templates now identify each sandbox value.
- Verification: `tests/test_operations_deployment.py` passed 28/28 and `tests/test_sandbox.py` passed 12/12 runnable cases (the opt-in live-Docker case skipped); strict mypy, Ruff and Terraform formatting passed. Staging and production Terraform roots passed `init -backend=false` and `validate` with Terraform 1.13.5/AWS provider 6.36.0. No Terraform plan/apply or cloud API call was made. Evidence: `docs/implementation/evidence/prompt-33/sandbox-manifest-reconciliation-2026-10-06.json`.
- Actual staging remains unprovisioned and the manifest remains a template. The deployment doctor still requires owner inputs, and the long-running production worker/evaluation/scoring/publishing runtimes remain separate implementation work before E2E-42/E2E-43 can pass.

## AWS solve-worker identity and assembly follow-up (2026-10-06)

- The EC2 supervisor badge gate compared the STS caller ARN literally with the IAM role ARN from the manifest. AWS STS returns role-session credentials as an `arn:...:sts::...:assumed-role/<role>/<session>` ARN, so a valid ECS solve supervisor could never pass this check. The verifier now matches AWS partition, account and role name, and refuses other principals or role sessions. Reference: [AWS STS GetCallerIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html).
- Added a production-tier `build_ec2_solve_worker` assembly. It revalidates the STS deployment against the deployed manifest, requires the `solve-supervisor` role, builds the EC2 provider from the manifest's pinned solve/grading/admission lanes, and passes only an injected Secrets Manager client for model credentials. Local Docker assembly still uses its development provider.
- Added distinct environment-scoped guest-control SSH identity references for solve, evaluation and admission roles. Terraform creates reference-only Secrets Manager entries encrypted by the environment secret key; each supervisor role can read only its own guest-control identity. `pcb-ops env reconcile` compares those references to Terraform outputs. The helper resolves the exact role reference to a temporary key file and removes it after use; no value is stored in Terraform state or logged.
- Verification: `tests/test_sandbox.py`, `tests/test_worker_runtime.py` and `tests/test_operations_deployment.py` pass 46 tests; one opt-in Docker containment test skips. Tests cover assumed-role matching, account/role mismatch, template and role refusals, lane selection, secret-reference scoping, temporary-file cleanup and Terraform-output drift. Ruff and strict mypy pass. Terraform validation is recorded with the next IaC check. Evidence: `docs/implementation/evidence/prompt-33/aws-solve-worker-assembly-2026-10-06.json`.
- This adds the assembly and secret boundary, not an operable production worker service. A guarded `pcb-worker` production command, container image/service command and administrative worker-registration flow remain open. No AWS API call, Secrets Manager read, EC2 launch or model call was made; staging still lacks an account, approved AMI, known-hosts bundle and spend authorization.

## AWS solve-worker CLI and registration follow-up (2026-10-06)

- Added the opt-in `pcb-worker ec2-run` solve-supervisor command and `ec2-register` migrator command. Both re-query STS and require the live principal to match the identity established by the startup guard and the deployed environment manifest. Registration is idempotent, separately enabled from dispatch, serializes capacity changes and enforces the manifest's aggregate active-slot cap. The migrator can write only worker-config artifacts under the environment's internal bucket prefixes and has no EC2 launch permission.
- Added `Dockerfile.solve-worker` and a dedicated `Dockerfile.ops`; both default to development-only inputs. Terraform injects the partition-aware S3 endpoint and bucket names and `pcb-ops env reconcile` checks them against the manifest. Staging and production tfvars keep worker dispatch and registration disabled and the solve service at zero.
- Verification: the sandbox, worker-runtime and deployment suites pass 49 tests (one opt-in live-Docker containment case skipped); strict mypy and Ruff pass. Terraform 1.13.5/AWS provider 6.36.0 `init -backend=false` and `validate` pass for both roots. Both images build locally as UID/GID 10001; ops CLI help, worker help, OpenSSH startup and opt-in refusal pass. No Terraform plan/apply, AWS API call, secret read, worker registration, guest launch, ECR push or model call was performed. Evidence: `docs/implementation/evidence/prompt-33/aws-worker-cli-2026-10-06.json`.
- This closes the worker CLI/container/registration implementation gap. A real staging run still requires an authorized account, region, budget, operator principals, deployed manifest, approved AMI and launch templates, guest key/known-hosts bundle, candidate image allowlist, image digests and explicit spend authorization. Evaluation, judging, scoring and publication processors also remain unfinished.

## Worker-registration audit follow-up (2026-10-06)

- Production worker creation now writes `worker.register` to `audit_event` in the same PostgreSQL transaction as the worker and its capacity slots. The event records the verified STS actor, worker registration digest, lane/driver/resource identities and slot count; it contains no secret values. Idempotent replays reuse the registration and do not create duplicate audit events.
- Verification: `tests/test_jobs_postgres.py::test_worker_registration_creates_an_atomic_audit_record` passed against the local `pcb_local_web_test` PostgreSQL database and loopback SeaweedFS. Ruff and strict mypy pass for the repository and CLI changes. No AWS, cloud storage, or model endpoint was contacted. Evidence: `docs/implementation/evidence/prompt-33/worker-registration-audit-2026-10-06.json`.

## Parent-run lifecycle follow-up (2026-10-07)

- Fixed the durable run state that previously stayed `queued` after solve work began and never became terminal. A scheduler claim advances `queued`/`planned` to `running`; once all attempts are terminal, the scheduler persists `completed`, `failed`, or `cancelled` in the same transaction that finalizes the attempt. Attempt cancellation also updates an all-terminal parent run.
- Terminal aggregation locks the parent run row before reading sibling attempts, so concurrent completions cannot leave a finished run stuck in `running`. The new `c02ea53a4d17` migration grants `pcb_scheduler` update access to only `run.status` and `run.row_version`; `packages/persistence/sql/grant_permissions.sql` carries the same grant for role bootstrap.
- Verification on a fresh, temporary loopback-only PostgreSQL 18 database: full migration upgrade, downgrade/re-upgrade of the new ACL migration, column-permission assertions and `alembic check` all pass. `tests/test_public_api_submissions_postgres.py` passes 4/4 with a least-privilege submitter/reviewer/approver API login and separate migration identity. It drives one synthetic approved run from queued to running to completed and reads each state and its attempt/job counts through the owner API. `tests/test_scheduler_regressions.py` passes 6 relevant database cases; 11 object-store cases skip because the Compose object store is not running. The Prompt 32 browser suite passes 2/2 and now renders queued, running and completed owner progress using synthetic API fixtures. Web typecheck and ESLint, Ruff, persistence strict mypy, formatting and `git diff --check` pass.
- The successful solve outcome is synthetic repository evidence: no sandbox, submitted endpoint, model provider or cloud service was contacted. The API flow still creates a bounded queued run; processing requires an explicitly enabled solve worker. Evaluation, judging, scoring and publication processors are still open, as are authorized AWS staging inputs and E2E-42/E2E-43.
- The temporary PostgreSQL server is stopped. Automatic execution review rejected recursive deletion of its verified temp-directory data, so those synthetic test files remain in the OS temp folder; no password or provider credential was provisioned or persisted.
- Evidence: `docs/implementation/evidence/prompt-33/parent-run-lifecycle-2026-10-07.json`.

## Local full-stack verification (2026-10-07)

- Started the loopback Compose dependencies and ran the documented local bootstrap: PostgreSQL is healthy at the current migration head with scoped API roles; SeaweedFS is reachable; Keycloak OIDC discovery is ready. The signed `synthetic_internal` release projection is available through the actual PostgreSQL-backed API.
- All 11 public API routes pass against that release. A compatible code-model comparison returns 3 common tasks and 6 paired task deltas; an incompatible pair is returned with both `protocol_mismatch` and `budget_mismatch` rather than silently combined.
- `pnpm --filter @polycodebench/web build` passes with TypeScript and all seven public routes generated. The standalone production page serves static assets and actual release data; the 390 px metrics region scrolls with the keyboard. Production OIDC intentionally refuses HTTP loopback because production sign-in requires HTTPS. The local development server's full Keycloak submission smoke passes, including reviewer authorization and cross-owner isolation.
- The development browser smoke also opened all nine release-backed public routes against the real local API: leaderboard, language profile, compatible comparison, task browser/detail, model profile, scorecard, frozen methodology and model submission. Each rendered the synthetic release notice; comparison showed the API's three common tasks.
- Browser suites remain green on the latest evidence: Prompt 30 4/4, Prompt 31 3/3, Prompt 32 2/2. The local form contacted no submitted provider endpoint; no model/judge call, cloud resource or benchmark release was created. Evidence: `docs/implementation/evidence/prompt-33/local-stack-functional-smoke-2026-10-07.json`.
- The website/API/database and local OIDC path now run together on this workstation. End-to-end benchmark execution still stops after bounded solve work: evaluation, judging, scoring and publication processors are not assembled. The solve worker remains opt-in and was not dispatched. Cloud staging remains blocked on account/trial quota, authorized spend, operator identities, domain/HTTPS and the other inputs in `docs/operations/staging-execution-plan.md`.

Decisions: D-33-01 to D-33-10 in `docs/implementation/decisions.md`. D-33-03 and D-33-04 are resolved in the current tree; staging authorization remains open.

Next: provide the staging inputs in `docs/operations/staging-execution-plan.md` §1, then run plan §2–3 to close E2E-42/E2E-43. Prompt 34 — Perform the final integrated audit and repair pass — follows once Phase 7 is accepted, or by explicit authorization with Phase 7 recorded as blocked.
