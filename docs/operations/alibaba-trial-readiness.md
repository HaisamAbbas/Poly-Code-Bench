# Alibaba Cloud trial readiness (2026-10-07)

## Decision state

The owner selected Alibaba Cloud as the possible trial target, but has not checked the account's
product entitlements, region, quotas, or expiry. No Alibaba resource has been provisioned. The
repository's Terraform currently targets AWS only; this is a provider-readiness note, not an
Alibaba deployment plan. Do not feed Alibaba credentials or region values into the AWS Terraform.

The current no-cloud local stack remains the verified functional environment. Alibaba staging
needs a separate design and Terraform provider/module set after the console values below are
confirmed. OCI is another possible time-limited target; see
[`oracle-free-tier-readiness.md`](oracle-free-tier-readiness.md) for its current fit and constraints.

## Current official trial rules checked

- Alibaba Cloud's product trials generally require a verified phone number, an account with no
  overdue payments, no prior order for the selected product, and a linked payment method. The
  documented methods are an internationally enabled credit/debit card or PayTM in India. ECS
  compute trials have stricter new-compute-user eligibility. See the [free-trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials).
- The current Free Tier page advertises an ECS t5 1-vCPU/1-GiB offer for one year, subject to
  account eligibility. That single small compute offer does not establish free database,
  object-store request/egress, DNS, registry, or secrets coverage. See [Free Tier](https://www.alibabacloud.com/en/Free).
- Alibaba also publishes a **separate ECS free-trial guide** with a three-month eligibility
  window and finite compute/system-disk quota (CNY 300 personal or CNY 660 enterprise). This is
  not the same entitlement as the one-year t5 card. The guide permits larger selectable shapes
  (up to 4 vCPU/8 GiB personal or 8 vCPU/16 GiB enterprise outside Hong Kong), but the quota is
  consumed at the instance's hourly reference price: at CNY 0.833/hour, CNY 300 lasts about 15
  continuous days. It lists seven regions, all in China, and warns that an instance is not
  automatically released at expiry; uncovered use becomes pay-as-you-go. Whether this account
  sees that offer, and which shape and region it can claim, must be checked in the console. See
  the [ECS free-trial guide](https://help.aliyun.com/en/ecs/user-guide/ecs-free-trial).
- The individual OSS trial currently advertises 500 GB of Standard LRS capacity for one month.
  It covers storage capacity only; other billable items are excluded and can be charged. It also
  requires identity verification, a valid payment method, and no prior OSS activation. See [OSS
  trial details](https://www.alibabacloud.com/help/en/oss/free-quota-for-new-users).
- ACK Basic cluster management has no management fee, but worker ECS instances and associated
  cloud resources remain billable. Pro cluster management is billed separately. See [ACK cluster
  billing](https://www.alibabacloud.com/help/en/ack/ack-managed-and-ack-dedicated/product-overview/ack-pro-cluster-billing).
- The current RDS offer page lists a 30-day MySQL trial. Its PostgreSQL Serverless listing starts
  at USD 0.02/hour; I found no equivalent free PostgreSQL offer on that page. Verify the exact
  PostgreSQL 17 product card in the signed-in account and selected region before using RDS. See
  [ApsaraDB offers](https://www.alibabacloud.com/en/product/databases) and [PostgreSQL Serverless
  pricing](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-postgresql/pricing-of-serverless-apsaradb-rds-for-sql-server-instances).
- A software KMS instance's advertised 14-day trial automatically becomes pay-as-you-go if it is
  not released before expiry. Do not treat it as a persistent no-cost secrets backend. See the
  [KMS trial terms](https://www.alibabacloud.com/help/en/kms/key-management-service/product-overview/faq-2).
- Solution trials are separate from product trials. They use a dedicated trial account, last at
  most 168 hours per run, and release the trial account/resources and erase trial data at expiry
  or when points are exhausted. They are unsuitable for persistent staging. See the [solution
  trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials).

These offers can change, depend on account history and region, and may leave usage outside the
included item/quantity billable. A free quota is not a hard account spend cap.

## Repository adapter status

The object-store client now has an opt-in Alibaba OSS path using
`PCB_OBJECT_STORE_PROVIDER=alibaba_oss`, `PCB_OBJECT_STORE_REGION=<region>`,
`PCB_OBJECT_STORE_ADDRESSING_STYLE=virtual`, and the region's OSS S3 endpoint. The path signs
OSS's `x-oss-forbid-overwrite` header for writes and uses `Content-MD5`; AWS S3 keeps conditional
`If-None-Match` and SHA-256 request checksums. OSS does not implement S3 conditional `PutObject`
headers, and its no-overwrite header is ineffective when bucket versioning is enabled or suspended.
See the official [S3 compatibility scope](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3),
[PutObject behavior](https://www.alibabacloud.com/help/en/oss/developer-reference/putobject),
and [conditional PutObject error](https://www.alibabacloud.com/help/en/oss/user-guide/0017-00000245).

This code path has only been checked against Botocore's signed request construction; it has not
been tested against an OSS account. It does not configure Alibaba RAM-role credential refresh,
create OSS bucket lifecycle policies, or validate the account's bucket versioning and trial
quotas. Do not treat it as ready for cloud deployment until those items and real OSS reads/writes
are verified with a budgeted account.

## Capacity and database cost reality check

The one-year Free Tier t5 card advertises 1 vCPU and 1 GiB RAM. The measured local development
stack reports about 1.47 GiB across PostgreSQL, SeaweedFS, Keycloak, API and Next processes,
before the operating system and Docker engine. That is not a production sizing benchmark, but it
means the full local stack cannot be assumed to fit on that 1 GiB shape. The separate finite
quota ECS trial may offer larger shapes, so it could support a short private synthetic-data POC
if the console confirms a sufficient SKU, region, and remaining quota. It is not a persistent
no-cost host: the quota is finite and instances continue on pay-as-you-go after expiry unless
released. The underlying workstation snapshot and its limitations are captured in
[`alibaba-local-capacity-snapshot-2026-10-07.json`](../implementation/evidence/prompt-33/alibaba-local-capacity-snapshot-2026-10-07.json).

Managed PostgreSQL does not remove the cost uncertainty: Alibaba's PostgreSQL Serverless service
bills both consumed RCUs and provisioned storage; storage expansion is enabled by default and the
provisioned storage capacity is billed even when unused. Its minimum configuration and auto-pause
settings do not make it a free database. OSS's one-month 500 GB individual offer covers Standard
LRS storage only; requests and other excluded usage can still be billed. The actual console
benefits and charges must be checked before choosing a design. [ECS free offer](https://www.alibabacloud.com/en/Free?_p_lc=1), [PostgreSQL Serverless billing and configuration](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-postgresql/create-a-serverless-apsaradb-rds-for-postgresql-instance), [OSS trial coverage](https://www.alibabacloud.com/help/en/oss/free-quota-for-new-users).

## Console values required before an Alibaba target can be designed

Record these values from the owner's console without sharing access keys or passwords:

1. Exact region and whether each product is available there.
2. ECS instance family, vCPU/RAM, disk, bandwidth, expiry, and whether the offer covers a
   persistent instance rather than a solution-trial account.
3. Whether PostgreSQL 17 is available under the trial; its instance tier, storage, backup quota,
   network charges, expiry, and post-trial billing mode.
4. OSS capacity, request and transfer quotas, region, expiry, and post-trial billing mode.
5. ACK, registry, VPC, public IP/load balancer, certificate/domain, secrets/KMS, and monitoring
   coverage and quota, if offered.
6. Maximum approved total spend (including uncovered/overage items) and a calendar shutdown date.
7. Whether billing alerts, automatic resource release, and account-level spending controls are
   available; alerts alone do not block charges.

Once those details are known, compare them with the AWS-oriented `staging-execution-plan.md` and
design an Alibaba-specific deployment path. No cloud calls, credential setup, or paid resource
creation is authorized by this research note.
