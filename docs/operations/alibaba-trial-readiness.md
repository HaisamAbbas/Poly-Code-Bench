# Alibaba Cloud trial readiness (2026-10-07)

## Decision state

The owner selected Alibaba Cloud as the possible trial target, but has not checked the account's
product entitlements, region, quotas, or expiry. No Alibaba resource has been provisioned. The
repository's Terraform currently targets AWS only; this is a provider-readiness note, not an
Alibaba deployment plan. Do not feed Alibaba credentials or region values into the AWS Terraform.

The current no-cloud local stack remains the verified functional environment. Alibaba staging
needs a separate design and Terraform provider/module set after the console values below are
confirmed.

## Current official trial rules checked

- Alibaba Cloud's product trials generally require a verified phone number, an account with no
  overdue payments, no prior order for the selected product, and a linked payment method. The
  documented methods are an internationally enabled credit/debit card or PayTM in India. ECS
  compute trials have stricter new-compute-user eligibility. See the [free-trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials).
- The current Free Tier page advertises an ECS t5 1-vCPU/1-GiB offer for one year, subject to
  account eligibility. That single small compute offer does not establish free database,
  object-store request/egress, DNS, registry, or secrets coverage. See [Free Tier](https://www.alibabacloud.com/en/Free).
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
