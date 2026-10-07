# Alibaba Cloud trial staging readiness

**Status: preparation only.** The owner reports access to Alibaba's free-trial offer, but
account-specific product eligibility, quotas, region, expiry, and an approved spend ceiling
have not been checked. No Alibaba API has been called and no cloud resource has been created.
This document records the code boundary and the inputs needed before a deployable target can be
selected. Alibaba's general trial terms were rechecked against the official documentation on
October 7, 2026; the signed-in console remains authoritative for account-specific offers.

## Can the linked free trial host this application?

The link can be a way to fund a **temporary synthetic-data proof of concept**, if the account
is eligible for every required product and the displayed quotas cover the reviewed plan. It is
not an immediate deployment target for this repository: the committed deployment and runtime
integrations are AWS-specific, as detailed below, and need a separate Alibaba implementation.

Alibaba documents two different trial types:

- **Product trials** are separate per product. Eligibility requires a verified phone and a
  supported payment method, no overdue balance, and no previous order for the product. ECS
  compute trials have additional first-use restrictions. The signed-in Free Trial page is
  dynamic; its account-specific product cards, regions, quantities, durations, and eligible
  configurations must be checked in the user's console. The current general terms list supported
  credit/debit cards with international payments enabled and PayTM for India; PayPal alone does
  not qualify. ECS additionally excludes accounts with a prior ECS/Simple Application Server
  purchase, pay-as-you-go bill, or trial order.
- **Solution trials** use trial points, are limited to at most 168 hours, are scoped to that
  solution, and delete the trial account/resources/data when ended or expired. They are suitable
  for a disposable POC, not a persistent staging environment. Point deductions follow cloud
  product billing cycles and may continue after the trial ends.

Alibaba's current general terms offer 50 initial solution-trial points once per user; completing
a solution's first deployment can award another 5 points for that solution, up to 50 incentive
points across eligible solutions. Points last one year, are applied at one point per US dollar of
actual resource bills, and billing-cycle deductions can continue after a trial ends. This is not
a zero-bill guarantee or an account-wide spending cap. See the [official solution-trial terms](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials).

As of October 6, 2026, Alibaba's published ECS trial guide describes a three-month eligibility
window backed by a finite quota: CNY 300 for personal verification or CNY 660 for enterprise
verification, covering ECS instances and system disks. The maximum hourly covered rates are
CNY 0.833 and CNY 1.833 respectively. At the maximum personal hourly rate, CNY 300 covers about
15 continuous days, not three months of continuous runtime. The published traffic quota is
20 GB/month in mainland China plus 200 GB/month outside mainland China. The guide lists seven
ECS trial regions, all in China: Beijing, Hangzhou, Guangzhou, Chengdu, Ulanqab, Heyuan, and Hong
Kong. The signed-in trial card remains authoritative for this account.

ECS usage beyond the quota is pay-as-you-go. At expiry, a trial instance is not automatically
released and continues on pay-as-you-go billing. Changing the instance type, attaching any data
disk, or changing the network billing method can also cause uncovered charges. Therefore, a
trial quota is not an account-wide spend cap and does not by itself make a remote host safe to
create.

As of September 8, 2026, Alibaba's OSS new-user offer lists 500 GB (individual) or 1 TB
(enterprise) of Standard LRS capacity for one month, subject to account eligibility. It covers
storage capacity only; other billable items remain chargeable, and use beyond the quota is
pay-as-you-go. This quota alone does not establish that the private evidence-storage design is
free.

The published KMS software-instance trial is 14 days and automatically converts to
pay-as-you-go unless the instance is released before expiry. Do not count this trial as a
bounded or automatically expiring staging secret store; avoid enabling it for a disposable POC
unless an owner has set a release reminder and verified the account's billing controls.

The free-trial terms do not establish that this account has ACK, ECS, RDS PostgreSQL 17, OSS,
KMS/Secrets Manager, or a registry quota in a compatible region. Nor do trial points or a budget
alert establish a hard account-wide spend cap. Check the product cards and `My Trial` page before
choosing resources. Review the account's billing controls and an owner-approved exposure limit
before any resource creation.

### Possible first use: private remote development on an ECS product trial

If the console offers an eligible ECS product trial, it can host the existing development stack
for a time-boxed remote session: PostgreSQL, SeaweedFS, Keycloak, the API, and web app, all in
development mode with synthetic release data. Bind services to loopback on the VM and reach them
through an SSH tunnel; do not expose the development Keycloak, database, object store, or web
ports to the internet. Restrict SSH to the operator's address and use a short-lived operator
credential. This can exercise the site and API from another machine without adding Alibaba SDK
support to the application.

That option requires an ECS trial card that allows the selected instance, region, disk, and
networking. It is temporary remote development, not a public website or the production-isolated
cloud staging gate: it does not exercise Alibaba RAM integration, managed PostgreSQL/OSS, or the
cloud VM sandbox driver. Keep only synthetic test data on it and explicitly release every
resource by the trial expiry date.

This is a separate alternative to the existing AWS staging plan. If the required project gate
continues to require AWS staging, using this trial does not satisfy that gate; it can only support
an additional Alibaba target after the owner authorizes that change.

## What the repository currently supports

The checked-in staging target is AWS-specific end to end:

- `infra/terraform/environments/staging` and the shared Terraform modules provision AWS VPC,
  ECS, EC2, RDS, S3, KMS, Secrets Manager, ECR, CloudWatch, and AWS budget resources.
- `EnvironmentManifest` accepts only `aws` as a trusted identity provider and requires the
  `ec2_vm` sandbox. `pcb-ops identity verify` verifies an AWS STS principal.
- The production sandbox adapter and orphan reaper call EC2 APIs and validate AWS resource
  tags and supervisor identity.
- `S3ArtifactStore` uses boto3 with S3 path-style addressing and sends `IfNoneMatch="*"` on
  object writes. OSS requires virtual-hosted requests. Alibaba documents that OSS `PutObject`
  rejects conditional headers, so changing only the endpoint or addressing style would break
  the store's no-overwrite guarantee.
- The deployment manifest's database, signing-key, and model-secret references use AWS
  Secrets Manager naming. Those references are not Alibaba secret resolution.

The local Compose stack remains usable without any cloud account. Its release projections are
synthetic internal test data and must continue to be labelled that way.

## Proposed isolated Alibaba staging shape

This is a design to validate against the account's trial entitlements before IaC is written or
applied. It must remain a separate target; it must not reuse AWS state or AWS manifests.

1. **Control plane:** ACK Kubernetes with a distinct Kubernetes service account and Alibaba RAM
   role per service. Use RRSA temporary credentials; do not distribute a long-lived AccessKey
   to pods or nodes.
2. **Database:** Alibaba managed PostgreSQL 17, subject to region/trial availability and
   migration/extension compatibility checks. Keep it private to the control-plane network and
   use separate least-privilege database users for each service.
3. **Evidence storage:** an OSS-native artifact adapter and three separate private buckets for
   hidden, internal, and public material. Preserve the current immutable-key contract with OSS
   `x-oss-forbid-overwrite`; verify all checksum, list, read, delete, and lifecycle behavior
   before enabling the adapter. Do not use an irreversible WORM lock for disposable trial
   staging.
4. **Secrets and signing:** Alibaba KMS/Secrets Manager with narrowly scoped RAM policies and
   versioned key references. Complete a key-rotation and restore rehearsal before considering
   this target trusted.
5. **Execution isolation:** dedicated private ECS guest instances by lane, with no public IP,
   no RAM instance role, disabled metadata access, no guest egress, and a supervisor-only
   forced-command channel. This requires a separate Alibaba ECS sandbox adapter and orphan
   reaper; the EC2 driver cannot be reused by renaming its configuration.
6. **Network and publication:** private service/database/guest subnets with explicit ingress,
   egress, and security groups; no public ranked publication from a trial deployment. Any test
   board must identify its release projections as synthetic.

ACK/RRSA is a material resource and eligibility choice, not a confirmed trial entitlement. If
the account lacks the required ACK/RRSA quota, stop and choose a smaller, explicitly
development-only deployment instead of weakening per-service credential boundaries.

## Implementation sequence

1. Confirm account trial eligibility, target region, exact included products/quotas, and an
   owner-approved maximum total exposure plus shutdown date.
2. Add provider-specific manifest and identity verification using Alibaba STS; test refusal on
   wrong account, wrong RAM role, missing identity, and template manifests.
3. Add and test an OSS-native object-store adapter that keeps immutable writes and visibility
   separation. Do not route evidence through the existing path-style S3 adapter.
4. Add an Alibaba ECS guest lifecycle adapter and tagged orphan reaper. Test lane isolation,
   metadata denial, egress denial, ownership/fence validation, TTL cleanup, and failed-destroy
   handling before connecting it to a trusted environment.
5. Add a separate pinned-provider IaC root, reviewed RAM policies, private networking, database,
   buckets, secrets, service deployment, and explicit teardown/runbook. Validate locally, then
   review a provider plan before any apply authorization.
6. Deploy only synthetic internal fixtures first. Run identity, database migration, object
   integrity, sandbox isolation, orphan, restore, privacy, and teardown checks. Do not call the
   deployment production-ready until those checks pass.

## Owner inputs required for a concrete trial target

Before choosing an implementation, check the signed-in console and record only these
non-secret facts (a screenshot with account identifiers redacted is sufficient):

- Under **My Trial > Service Free Trial**, the exact ECS/OSS/KMS offers shown, eligible region,
  instance/disk configuration, remaining compute and traffic quota, and trial expiry. Confirm
  whether each resource is released or becomes billable at expiry.
- Under **My Trial > Solution Free Trial**, any solution points, balance, expiry, and the
  selected solution's maximum duration. Do not assume that solution-trial resources persist.
- The billing currency, existing account spending controls/alerts, and an owner-approved maximum
  total exposure and shutdown date. Alerts and free quotas are not hard spending caps.

- The Alibaba region selected in the console.
- Which of ACK, ECS, RDS PostgreSQL 17, OSS, KMS/Secrets Manager, and container registry are
  available to this account's trial, including quotas and trial expiry dates. A screenshot or
  exported quota view with account identifiers redacted is sufficient.
- The approved maximum total spend in the account's billing currency and the shutdown date.
  Trial eligibility or a budget alert is not a hard spending cap.
- Whether there is a domain and certificate available. Without them, deployment can remain
  private or use a temporary test endpoint; do not expose a public service by default.
- The operator identity method (Alibaba Cloud Shell/SSO or another short-lived role session).
  Do not send AccessKeys, passwords, tokens, or private keys in chat or commit them to the repo.

## Official compatibility references

- [OSS S3 API compatibility](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3): virtual-hosted requests are required.
- [OSS PutObject](https://www.alibabacloud.com/help/en/oss/developer-reference/putobject): documents `x-oss-forbid-overwrite`.
- [Unsupported conditional PutObject headers](https://www.alibabacloud.com/help/en/oss/user-guide/0017-00000245): OSS rejects `If-None-Match` and related conditional headers on `PutObject`.
- [ACK RRSA](https://www.alibabacloud.com/help/en/ack/ack-managed-and-ack-dedicated/user-guide/use-rrsa-to-authorize-pods-to-access-different-cloud-services): pod-scoped RAM roles use short-lived STS credentials.
- [RDS PostgreSQL release notes](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-postgresql/rds-pg): PostgreSQL 17 and 18 support is documented; availability still needs confirmation in the selected region and trial.
- [Alibaba Cloud free-trial terms](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials): eligibility, trial duration, and resource cleanup depend on the product offer.
- [ECS free-trial guide](https://help.aliyun.com/en/ecs/user-guide/ecs-free-trial): finite ECS and system-disk quota, traffic allowance, eligible regions, and pay-as-you-go billing after expiry.
- [KMS billing FAQ](https://www.alibabacloud.com/help/en/kms/key-management-service/product-overview/faq-2): one software KMS trial instance auto-converts to pay-as-you-go after 14 days unless released.
- [OSS free trial for new users](https://www.alibabacloud.com/help/en/oss/free-quota-for-new-users): current capacity quota and excluded billable items.
