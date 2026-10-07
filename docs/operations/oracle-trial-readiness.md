# Oracle Cloud trial readiness (2026-10-07)

## Decision

OCI is a possible **time-limited cloud proof of concept**, if the owner can open an eligible
account and confirms the required region, quotas, and trial balance. It is not presently a
reliable, no-cost home for persistent staging. The repository's checked-in Terraform target is
AWS-only, and the cloud staging workflow also depends on worker execution modes that are not yet
ready. No OCI resources have been created.

Keep using the verified local Compose stack while these conditions remain unchecked. A trial
deployment would be a separate OCI implementation and would not count as the AWS staging E2E
acceptance run.

## Current official offer and operational limits

- Oracle advertises USD 300 of credits for up to 30 days plus selected Always Free resources. The
  credits expire when either the balance is used or 30 days pass. Most sign-ups require a phone
  number and a credit/debit card for verification; Oracle says the card is not charged unless the
  account is upgraded. There is one free trial/Always Free account per person. See the [OCI Free
  Tier FAQ](https://www.oracle.com/cloud/free/faq/) and [Free Tier terms](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm).
- After trial expiry, Always Free resources remain available, but trial-created paid resources are
  reclaimed if the account is not upgraded. Oracle documents a 30-day grace period before paid
  resources are reclaimed. Anything that must persist needs an export and recovery plan before the
  expiry date. See [what happens when the promotion expires](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/signingup_topic-What_Happens_When_the_Promotion_Expires.htm).
- The Always Free Ampere allowance is up to 2 OCPUs and 12 GB RAM in the tenancy's home region.
  Capacity can be unavailable, and idle Always Free instances may be reclaimed when CPU, network,
  and (for A1) memory usage stay below Oracle's thresholds over seven days. This is not an uptime
  guarantee for a quiet staging website. See [Always Free resource limits and reclaim rules](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
- OCI Database with PostgreSQL supports PostgreSQL 17. The documented Always Free database offers
  are Autonomous AI Database, MySQL HeatWave, and NoSQL; OCI Database with PostgreSQL is billed by
  provisioned compute, storage, performance units, and networking. Its smallest current flexible
  shape depends on the shape family (E5 starts at 1 OCPU/16 GB; Standard3 at 2 OCPUs/32 GB). So a
  managed PostgreSQL 17 instance is a candidate for the 30-day credits, not an assumed Always Free
  resource. See [PostgreSQL 17 support](https://docs.oracle.com/en-us/iaas/releasenotes/postgresql/db-17.htm),
  [supported shapes](https://docs.oracle.com/en-us/iaas/Content/postgresql/supported-shapes.htm),
  and [PostgreSQL billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm).
- Published Always Free allowances include 200 GB block volume, limited Object Storage, a 10 Mbps
  flexible load balancer, and 10 TB/month outbound transfer. Quotas vary by resource and account
  state; verify actual entitlement under **Limits, Quotas and Usage** in the selected tenancy and
  region. See [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## Fit to Poly-Code-Bench

| Need | OCI trial fit | Remaining issue |
|---|---|---|
| Next.js and API demo | Plausible on a small VM or trial compute | This is a new deployment target; the repository currently has AWS Terraform only. |
| PostgreSQL 17 | Managed OCI PostgreSQL supports v17 | Managed service is paid usage; determine shape/region availability and trial-credit burn first. |
| Public HTTPS and domain | OCI networking and load balancing can support a small demo | Confirm public IP, DNS ownership, certificate, bandwidth, and any non-free components. |
| Artifacts and secrets | OCI has Object Storage and Vault offerings | Confirm quotas, API integration, policies, retention, and charges; the app's AWS IAM/S3/KMS wiring is not an OCI deployment. |
| Isolated benchmark execution | Trial VM resources can run bounded experiments | The current cloud plan separates control, solve, grading, admission, and performance lanes. A single free VM is not equivalent isolation or scale, and worker runtime modes still need implementation. |
| Durable staging | Not a safe no-cost assumption | Paid trial resources are reclaimed at expiry; Always Free compute can be reclaimed for idleness. |

## Required console facts before designing an OCI target

Record values without sharing credentials, card details, or tenancy secrets:

1. Account eligibility, home region, free-credit balance, and exact trial expiry date.
2. Available Always Free and trial quotas for compute, public networking/load balancing, storage,
   registry, Vault, logging, and email/alert delivery in that region.
3. PostgreSQL 17 availability, smallest offered shape, storage/backup settings, full hourly
   estimate, and how long the trial credits would cover it.
4. Domain/DNS ownership and a tested HTTPS certificate path.
5. A written maximum acceptable trial-credit consumption and a shutdown/export date. Credits are
   limited, and cost estimates/alerts are not a promise of capacity or a substitute for checking
   each resource's billing terms.

If those checks pass, prepare a separate OCI Terraform/provider path and a short-lived demo run
plan. Do not reuse AWS account identifiers, state, credentials, or Terraform inputs. Keep model
provider calls disabled unless separately authorized; the cloud infrastructure rehearsal does not
need model spend.
