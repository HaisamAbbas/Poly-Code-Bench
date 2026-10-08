# Staging execution plan and budget (Prompt 33 remaining scope)

**Status: blocked on authorization.** Nothing has been provisioned, and no AWS call has been made. This plan is the exact remaining work for E2E-42 and E2E-43 in actual staging. The IaC is written and validated (`terraform validate` passes for both roots, with the AWS provider pinned at 6.36.0). The application-side tooling has been rehearsed locally (rehearsal-report-2026-10.md).

The owner has since selected Alibaba Cloud as a possible trial target, but has not checked account
entitlements, quotas, region, or shutdown date. This file remains AWS-specific. See
[`alibaba-trial-readiness.md`](alibaba-trial-readiness.md) for current official trial terms and the
console values needed before designing a separate provider target. Oracle Cloud Free Tier was also
reviewed as an alternative; see [`oracle-free-tier-readiness.md`](oracle-free-tier-readiness.md). No cloud
target is ready to provision yet, so continue with the verified local stack until account, region,
resource quotas, and an owner-approved trial envelope are known.

## 1. Inputs the owner must supply (T 25.3)

| # | Input | Used by |
|---|---|---|
| 1 | Dedicated staging AWS account ID and region | `account_id` / `region` in `terraform.tfvars`; provider `allowed_account_ids` |
| 2 | **Owner-approved monthly infrastructure alert threshold.** USD 600/month is only a planning estimate; the one-off USD 150 rehearsal allowance in §4 is also unapproved | `monthly_budget_usd` (SNS notifications at 50/80/100% and forecast) |
| 3 | State bucket and lock table for staging | `backend.hcl` |
| 4 | Operator SSO role ARNs (named people, MFA) | `operator_principal_arns` |
| 5 | Bucket name prefix (org slug) | `bucket_prefix` |
| 6 | Domain for the staging test board, plus ACM certificates (regional and us-east-1) | `domain_names`, `alb_certificate_arn`, `cdn_certificate_arn` |
| 7 | Approved guest AMI and instance type, built by the AMI pipeline from `infra/sandbox/aws/guest/bootstrap-control.sh`, with its image manifest/SBOM; reconcile its three lane subnets/security groups and supervisor control security group from Terraform output | `approved_guest_ami_id`, `guest_instance_type`, `lane_subnet_ids`, `guest_security_group_ids`, `control_security_group_id` |
| 8 | Public API/web image digests and the operations image digest, pushed to the environment's ECR; see the bootstrap cycle below | `services.*.image` |
| 9 | On-call alert addresses | `alert_email_endpoints` |
| 10 | Activation of the `pcb:environment` cost-allocation tag in Billing | budget filter |
| 11 | OIDC issuer URL and registered public client ID with the exact HTTPS callback URI | web task environment; callback must be registered at the issuer |

The Terraform budget sends notifications for tagged cost data; it does **not** impose a hard account-wide spending limit. AWS says budget data updates only a few times per day and charges can exceed a threshold before its notification arrives ([AWS Budgets timing and limits](https://docs.aws.amazon.com/cost-management/latest/userguide/bcm-lite-use-budget.html)). The example tfvars sets `monthly_budget_usd = 0`, which fails positive-value validation until the owner supplies an approved threshold. Do not treat the proposed $600 or $150 planning amounts as authorization, and do not apply staging based on budget alerts alone. The owner must approve the actual resource envelope and shutdown response while accepting the residual billing-delay risk.

The public API/web runtime additionally requires a selected OIDC issuer, registered client ID and
callback URL, a reconciled environment manifest, populated database/cursor secrets, and a
randomly generated shared web/API signing key. The API receives its principal fingerprint export
as bounded JSON from Secrets Manager; the initial export is empty and grants no operator role.
The control-services module advertises API through the environment-scoped ECS Service Connect
name `api`, which is the URL baked into the web image. The web task health path is `/`.

Production-shaped API, web, and scheduler lease-reaper image builds exist. The scheduler image is
not a job worker. The solve supervisor has an identity-gated runner, and the evaluator now has an
identity-gated grading runner plus a separate migrator registration command. Neither supervisor
has a reviewed staging image, registration, or dispatch verification. The model/judge gateways,
scorer, and publisher still lack complete long-running processing modes. These image builds do not
make E2E-42/E2E-43 deployable; do not scale roles without a passing runtime check. Build details
and the evidence scope are recorded in
[`container-images.md`](container-images.md).

E2E-42 and E2E-43 need **no model or judge provider spend**. Provider outage is exercised against the gateway with egress denied, not against a live provider. Live pilot spend remains a separate authorization (Prompt 17).

## 2. Exact steps

Every command is **[S]**: not executed. Run them as the named role.

```bash
# 0. Pre-flight (local, already passing)
uv run --offline --locked --all-packages pcb-ops env validate
uv run --offline --locked --all-packages pcb-ops migrate check     # current local tree passes

# 1. Bootstrap the infrastructure with services/schedules left disabled (platform owner).
#    The committed staging and production examples set every desired_count to 0 and schedules to {}.
#    The control-services module rejects nonzero services with unresolved placeholder image digests.
#    Do not raise counts while the image fields still contain REQUIRED/zero-digest placeholders.
cd infra/terraform/environments/staging
cp backend.hcl.example backend.hcl && cp terraform.tfvars.example terraform.tfvars   # fill every REQUIRED
terraform init -backend-config=backend.hcl
terraform plan -out=staging-bootstrap.plan # review: no resource outside pcb:environment=staging
terraform apply staging-bootstrap.plan    # creates infra/task definitions, starts no app service
terraform output -json deployment > ../../../../config/environments/staging.terraform-output.json
cd ../../../../                           # back to repository root for pcb-ops and Docker builds

# 2. Make the manifest deployable (platform owner)
#    fill config/environments/staging.yaml from the output, set status: deployed
uv run --offline --locked --all-packages pcb-ops env reconcile --env staging \
  --terraform-output config/environments/staging.terraform-output.json   # must print no differences
uv run --offline --locked --all-packages pcb-ops doctor --profile staging # must exit 0

# 3. Set OIDC values in terraform.tfvars and create the required secret values (two approvers + owner).
#    The identity-export secret already contains only {schema_version:1, principals:[]}; it grants no roles.
#    Add only SHA-256 token fingerprints and scoped claims for approved reviewer/admin accounts.
aws secretsmanager put-secret-value --secret-id pcb/staging/db/<role> ...          # per role
aws secretsmanager put-secret-value --secret-id pcb/staging/api/cursor-signing-key ...
aws secretsmanager put-secret-value --secret-id pcb/staging/api/web-auth-signing-key ... # random >=32 bytes, same for API+web
# If the OIDC provider requires a confidential client, set PCB_OIDC_CLIENT_SECRET by secret reference.
pcb-ops keys rotate --keyring keyring.json --new-key-id staging-ed25519-2026-10 --private-key-out <offline>
aws secretsmanager put-secret-value --secret-id pcb/staging/signing/staging-ed25519-2026-10 --secret-binary fileb://<offline>

# 4. Build and push reviewed immutable app images after the environment manifest is deployed.
#    The api image also carries pcb-ops for identity-guarded one-off operations.
docker build --platform linux/amd64 --file Dockerfile.api --build-arg `
  PCB_ENV_MANIFEST_SOURCE=config/environments/staging.yaml --tag pcb-api:staging .
docker build --platform linux/amd64 --file Dockerfile.web --build-arg `
  PCB_PUBLIC_API_URL=http://api:8000/v1 --tag pcb-web:staging .
docker build --platform linux/amd64 --file Dockerfile.scheduler --build-arg `
  PCB_ENV_MANIFEST_SOURCE=config/environments/staging.yaml --tag pcb-scheduler:staging .
docker build --platform linux/amd64 --file Dockerfile.eval-worker --build-arg `
  PCB_ENV_MANIFEST_SOURCE=config/environments/staging.yaml --build-arg `
  PCB_GUEST_KNOWN_HOSTS_SOURCE=<reviewed-guest-host-keys-file> --tag pcb-eval-worker:staging .
#    Push API to the api and ops ECR repositories, web to web, scheduler to scheduler,
#    and the evaluator image to eval-supervisor. Its registered image identities must match
#    the config/images documents embedded in this immutable image.
#    Resolve immutable ECR digests and update only the matching task image references.
#    Keep the scheduler count at 0 until its staged PostgreSQL connection/reaper behavior is
#    verified. Keep eval-supervisor at 0 until a migrator registers its resource plan and the
#    owner explicitly enables dispatch. Other worker/gateway roles remain at 0 until their
#    processing daemons and images are verified.

# 5. Schema (migrator task; refuses non-expand migrations)
# Bootstrap PostgreSQL roles with provision_roles.sql before migration and apply
# grant_permissions.sql after Alembic. The API login belongs to exactly these scoped groups:
# pcb_public_reader, pcb_submitter, pcb_submission_reviewer,
# pcb_submission_approver, pcb_endpoint_administrator. Do not grant it pcb_reviewer,
# pcb_operator or pcb_administrator; the separate pcb_migrator login runs Alembic.
aws ecs run-task --cluster pcb-staging --task-definition pcb-staging-migrator --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<control>],securityGroups=[<control-sg>]}'

# 6. Set the reviewed API/web counts (and only implemented one-shot operations) in terraform.tfvars,
#    set schedules only after their ops image exists, plan, review, and apply. Verify each task's
#    startup log contains the expected verified identity before sending traffic.
aws logs filter-log-events --log-group-name /pcb/staging/api --filter-pattern '"PCB_VERIFIED_ENVIRONMENT"'
```

This bootstrap is infrastructure preparation, not the full staging acceptance run. Although a
scheduler image exists, it only reaps expired leases and has not been exercised against staging
PostgreSQL. The solve/evaluation supervisors have guarded source commands, but neither has a
reviewed, built staging image, registered capacity, or verified runtime. The model/judge gateways,
scorer and publisher still lack complete long-running processing modes. Their Terraform example
counts remain zero; the API/web/scheduler images cannot pass E2E-42/E2E-43. Do not raise
worker/gateway counts or point them at the API image.

## 3. Staging acceptance runs (E2E-42, E2E-43)

| Check | How | Pass condition |
|---|---|---|
| Clean deploy reproducible | `terraform plan` immediately after apply | "No changes" |
| Identity enforces environment | (a) run a staging task with `PCB_ENVIRONMENT=production`; (b) from the staging scheduler role, `aws s3 ls s3://<prod-bucket>` and `kms:Decrypt` on a production key; (c) from `solve-supervisor`, `GetObject` on the hidden bucket | (a) exits 4 before serving; (b) and (c) AccessDenied; (c) also fires the CloudTrail hidden-access alarm within 5 minutes |
| Isolation tier | Run an E2E-05/06 grading stage on the EC2 driver | Results carry `isolation_tier=production` and the attestation passes; a local-driver result is refused for a ranked release (`refuse_inadmissible_tiers`) |
| Orphan drill | Launch a guest with a 300 s TTL, stop the supervisor task, wait | Scheduled `pcb-ops orphans sweep --provider ec2` reclaims it; record measured time since expiry against the 10-minute **target** |
| Outage drill | Temporarily deny control egress to the provider endpoint (security-group rule change, reverted afterwards) during a fixture campaign | Deliveries become not-delivered/ambiguous with exposure kept; `PcbProviderOutage` fires; no duplicate result after restore |
| Drain / stale commit | Roll out a new task-definition revision during queued work | Old workers drain; `pcb_stale_commit_refusals_total` counts refusals; no job has two results |
| Withdrawal | Publish a synthetic **test-board** release, then a correction, then withdraw | Same assertions as the local drill; historical URL and notice served through CloudFront; Object Lock prevents deletion |
| Key rotation | Rotate during the withdrawal drill | Old manifest verifies as `retired`; revoked fails closed |
| Restore (E2E-42) | database-object-store-restore.md §A staging steps 1–4 | `pcb-ops restore verify` passes; ten stratified scorecards replay; projection rebuilt; measured recovery time recorded; the rehearsal DB is deleted |
| Alerts | Trigger each test condition above | Each required alert reaches the SNS topic |
| Load | Run the explicit API-origin command below against the deployed public API/CDN origin after the load window and request count are approved | Record the selected release, per-route latency, ETag revalidations and errors. This measures API routes only; browser page timings require a separate browser load. The 300 ms target is met only if the intended cached path is measured |

The load script's default mode starts a synthetic API on loopback; it does not accept or contact a
CDN. For a staging rehearsal, use the explicit-target mode below. These commands are **[S]** and
must not be run against a cloud origin until the owner has approved its request volume and
exposure. The runner rejects values above 10,000 requests or concurrency 64; keep the 3,000/16
profile below unless a separately reviewed load profile authorizes a change. They are read-only
and do not call model providers.

```bash
export PCB_STAGING_PUBLIC_API_ORIGIN="https://<origin-from-the-reviewed-staging-deployment>"
uv run --offline --locked --all-packages python scripts/ops_load_rehearsal.py \
  --base-url "$PCB_STAGING_PUBLIC_API_ORIGIN" --confirm-target \
  --requests 3000 --concurrency 16 \
  --evidence docs/implementation/evidence/prompt-33/staging/public-api-load.json
```

The staging seed is the same synthetic rehearsal source, labelled `synthetic_internal`, published to `staging:test-board` only. **Nothing is published to a public board, and no benchmark result is published.** The API service uses `PCB_PUBLIC_RELEASE_BACKEND=postgres` and `PCB_PUBLICATION_TARGET=staging:test-board`; `pcb-ops releases sync-publication` verifies the local signed source release with the public keyring before mirroring the sanitized snapshots and pointer into PostgreSQL. The task definition supplies the API DSN and cursor-signing key only as Secrets Manager references. This staging path has not been provisioned or exercised against AWS.

Production-only steps not executed and not planned in staging: production Multi-AZ failover, backup vault compliance lock, capacity reservation for the performance class, public DNS cut-over.

## 4. Budget

The prior full-staging estimate below describes 2 AZs, `db.t4g.medium` single-AZ and 8 always-on
Fargate tasks (3.75 vCPU, 7.5 GB total), with no reserved performance capacity. The committed
bootstrap profile starts no ECS service, but an applied stack still accrues database, VPC/NAT,
endpoint, load balancer, monitoring and storage charges. A later API/web-only profile also differs
from both quantities. Re-price the exact reviewed plan and shutdown window before authorization.

The unit prices are **planning assumptions** taken from publicly listed us-east-1 on-demand prices, as known to the author at the time of writing. They were **not** fetched from AWS for this plan. Re-price everything with the AWS Pricing Calculator for the approved region on the authorization date.

| Item | Quantity | Assumed unit price | Monthly |
|---|---|---|---|
| Fargate vCPU | 3.75 vCPU × 730 h | $0.04048/vCPU-h | $110.8 |
| Fargate memory | 7.5 GB × 730 h | $0.004445/GB-h | $24.3 |
| VPC interface endpoints | 9 endpoints × 2 AZ × 730 h | $0.01/h | $131.4 |
| NAT gateway | 1 × 730 h (+ data) | $0.045/h | $32.9 + data |
| RDS PostgreSQL `db.t4g.medium` | 730 h + 100 GB gp3 | $0.065/h; $0.115/GB-mo | $59.0 |
| Application Load Balancer | 730 h + ~1 LCU | $0.0225/h; $0.008/LCU-h | $22.3 |
| Public IPv4 (NAT EIP + ALB) | 3 × 730 h | $0.005/h | $11.0 |
| Managed Prometheus | ~200 M samples ingested | $0.90/10 M | ~$18 |
| CloudWatch Logs, flow logs, CloudTrail data events | ~20 GB | $0.50/GB ingested | ~$10 |
| KMS (5 keys), Secrets Manager (~13) | | $1/key; $0.40/secret | $10.2 |
| S3, ECR, AWS Backup, CloudFront (synthetic data) | small | | ~$15 |
| **Baseline** | | | **≈ $445/month** |

Rehearsal allowance (one-off, about USD 150):

- disposable `m7i.large` guests, ~50 guest-hours at about $0.10/h;
- restored RDS instances for 2 rehearsals × 2 h;
- **dedicated-tenancy performance guests**: AWS charges a regional dedicated fee of about $2/h while any dedicated instance runs, so keep performance drills to a few hours or set `tenancy=default` in staging;
- data transfer.

Planning estimate only: **USD 600/month for staging infrastructure plus USD 150 one-off rehearsal allowance**. These amounts are not authorized and the AWS Budgets resource cannot guarantee them as hard caps. Re-price everything for the approved region, obtain the owner's actual thresholds and resource envelope, and review shutdown controls before any apply. The estimate leaves headroom for interface endpoints. To trim about $66/month, set `az_count=1` for endpoints in staging (this requires an IaC parameter not yet exposed) or drop unused endpoints.

## 5. Exit criteria

E2E-42 and E2E-43 are marked passed only when every row of §3 passes in staging, with evidence files committed under `docs/implementation/evidence/prompt-33/staging/`. Until then they stay **blocked** (local variants passed).
