# Oracle Cloud Free Tier readiness

**Status: preparation only.** The owner linked Oracle's Free Tier page, but has not reported an
Oracle account, selected a home region, checked account quotas, or approved a spend limit. No OCI
API has been called and no cloud resource has been created. This assessment was checked against
Oracle's official documentation on October 7, 2026; the signed-in console remains authoritative
for the account's limits and available capacity.

## Can OCI Free Tier host this application?

It is a plausible candidate for a **private, synthetic-data remote development proof of
concept**, if the account can provision an Always Free Arm VM in its home region and the
repository's containers and runtime fit the available resources. Oracle documents a total of
2 OCPUs and 12 GB of memory for Ampere A1 Always Free compute, and 200 GB of block-volume storage
for boot and block volumes combined. Those Always Free resources persist after the promotional
credit period, subject to the account remaining active and OCI's idle-resource policy.

The $300 promotional credit is separate: it expires when used or after 30 days. Paid resources
created with promotional credits are reclaimed after the trial grace period unless the account
is upgraded. The free account's Always Free compute and database must be created in its home
region, and Free Tier tenancies are limited to one subscribed region. Always Free host capacity
may be temporarily unavailable in a selected home region.

Oracle may reclaim an Always Free compute instance as idle when, over a seven-day period, CPU
utilization at the 95th percentile, network utilization, and (for A1 shapes) memory utilization
are each below 20%. That makes it a poor reliability choice for a low-traffic public benchmark
site. It also means the user must check current resource activity and retention rules before
treating a free VM as persistent hosting.

The Always Free Autonomous AI Database is **not** a drop-in for this application: the current
service is Oracle Database, while PolyCodeBench expects PostgreSQL. Oracle documents that the
Always Free database has no private endpoint/VCN placement, no long-term or manual backups, and
a limit of 20 simultaneous database sessions. Do not point the application at it or treat it as
satisfying the PostgreSQL backup and restore requirements. Oracle's current [Always Free resource
limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm#Database)
list 20 sessions; older Oracle documentation showed 30.

OCI also offers a separate managed **Database with PostgreSQL** service and added PostgreSQL 17
support in April 2026. This service is not listed as Always Free. Its compute, storage,
performance units, and networking are billed while the database is active. The smallest current
flexible shape depends on its family: E5 starts at 1 OCPU/16 GB RAM and Standard3 at 2 OCPUs/32
GB. It can be considered for a short trial-credit POC only after checking the selected region's
shape availability and estimating the full cost against the account's remaining USD 300 credit
and expiry date. Paid trial resources are reclaimed after the trial if the account is not
upgraded. See [PostgreSQL 17 support](https://docs.oracle.com/en-us/iaas/releasenotes/postgresql/db-17.htm),
[supported shapes](https://docs.oracle.com/en-us/iaas/Content/postgresql/supported-shapes.htm),
and [billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm).

## Recommended scope if an eligible account exists

A single A1 VM may be used for a time-boxed, private synthetic-data POC after verifying ARM64
image compatibility and memory use. Run the local Compose dependencies and the API/web processes
on the VM, keep listeners bound to loopback, and reach them through an SSH tunnel or OCI Bastion.
Use only the signed `synthetic_internal` release fixture. Do not expose Keycloak development
mode, PostgreSQL, SeaweedFS, reviewer routes, or an unauthenticated benchmark board to the
internet. Keep credentials generated for that isolated host, and destroy the VM and its volumes
when the POC ends.

An image-index inspection on October 6, 2026 confirmed that the pinned PostgreSQL 17.6,
SeaweedFS 4.48, and Keycloak 26.8.0 dependency images each publish a `linux/arm64` variant.
This check does not cover Python/Node dependencies or prove the combined stack fits in 12 GB;
build the API and web app on an ARM64 Linux host and measure memory before using the VM.

This would test remote operation of the local development stack. It would **not** complete the
repository's cloud staging gate or E2E-42/E2E-43. The checked-in staging plan uses AWS ECS, EC2,
RDS PostgreSQL, S3, KMS, Secrets Manager, ECR, IAM/STS and AWS Budgets. Core deployment manifests
accept AWS identity only, the guest sandbox uses EC2, and evidence storage uses the S3 adapter.
There is no OCI identity verifier, object-store adapter, guest lifecycle driver, or OCI
infrastructure root. A production-shaped OCI target therefore needs a separate implementation
and a revised set of acceptance checks; a Compose VM cannot be relabeled as production isolation.

The currently verified no-cloud path is still the loopback-only setup in
[local-self-hosting.md](local-self-hosting.md). For Alibaba's alternative and its separate
finite ECS trial quota, see [alibaba-trial-staging-readiness.md](alibaba-trial-staging-readiness.md).

## Console facts needed before a remote POC

Check the signed-in OCI console and record only non-secret values. Redacted screenshots are
enough; do not send tenancy IDs, user IDs, API keys, auth tokens, private keys, or passwords.

- Whether the account is in the promotional trial, Always Free only, or a paid state; remaining
  promotional credit and its expiry; and confirmation that no paid resource will be provisioned.
- The chosen home region, its A1 availability, and the tenancy's remaining A1 OCPU/memory and
  boot/block-volume quotas. Include any current host-capacity error.
- Current Always Free limits and usage for Object Storage, Vault, load balancer, and outbound
  data. These are not required for the private VM POC unless the design explicitly needs them.
- Existing billing alerts/controls, the owner-approved maximum total exposure, and the shutdown
  date. Free quotas and alerts are not a hard account-wide spend cap.
- Whether the POC remains SSH/Bastion-only. If public access is later requested, the owner must
  also decide on domain, HTTPS, access control, and the residual cost/exposure limits.

Do not upgrade the account, create a paid resource, or apply infrastructure until the account
status, region, quotas, reviewed resource plan, and owner-approved exposure are known. There is no
cloud change to make while those values remain unknown.

## Official Oracle references

- [OCI Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm): $300 promotional credit for up to 30 days, home-region constraints, and what happens after expiry.
- [OCI Free Tier FAQ](https://www.oracle.com/cloud/free/faq/): sign-up verification and account/billing terms.
- [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm): A1/E2 shapes, memory and storage quotas, idle-instance reclamation, Object Storage and Vault allowances.
- [Regions and availability domains](https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm): Free Tier and trial subscribed-region limit.
- [Always Free Autonomous AI Database](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/autonomous-always-free.html): database limits, network placement and backup restrictions.
- [What happens when the promotion expires](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/signingup_topic-What_Happens_When_the_Promotion_Expires.htm): free account and paid-resource handling after the promotion.
