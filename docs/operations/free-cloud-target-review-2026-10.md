# Free cloud target review (2026-10-06)

## Decision

Keep the Compose stack as the usable no-cloud environment. Alibaba Cloud can be a **short,
account-specific staging experiment** only after the console confirms the trial resources and a
hard owner-approved spend envelope. The account quotas are still unchecked, so there is no
deployable Alibaba target yet. Do not apply the existing Terraform: its providers and modules are
AWS-specific.

The Alibaba Free Trial page currently lists an ECS t5 offer at 1 vCPU / 1 GiB for one year, but
trial eligibility depends on product history and a payment method. Alibaba says ECS trials are
limited to new computing-product users and explains that any past ECS/Simple Application Server
purchase, bill, or trial can disqualify the account. The product-trial rules require a verified
account and payment method. See [current Free Trial offers](https://www.alibabacloud.com/en/free?_p_lc=1)
and [Alibaba's trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials).

That listed 1 GiB offer is below a sensible target for the complete site stack (API, web, OIDC,
PostgreSQL, object store, monitoring and backups); this is an engineering estimate, not a vendor
quota. Alibaba's separate solution-trial points are not a persistent-hosting substitute: they are
for the named solution, trial duration is at most 168 hours, and trial resources and their data
are deleted when the trial ends or its points run out ([trial lifecycle](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials)).
The same rules say billing deductions can continue after the trial ends. A promotional label
alone therefore does not set a zero-spend limit.

The project currently has no owner-supplied region, exact ECS SKU/quota, trial expiry, PostgreSQL
or OSS entitlement, domain, or authorized spend cap. The API's public OIDC callback also needs a
stable HTTPS origin, and the local-only Keycloak configuration is not suitable for public use.
Until those inputs exist, stay on the already-tested loopback stack. A trial deployment would
need a separately reviewed Alibaba IaC target, TLS/domain setup, secret setup and teardown drill;
none of those resources have been created.

## OCI comparison

OCI offers $300 of trial credit for up to 30 days plus Always Free resources. The current Always
Free quota includes up to 2 Ampere A1 OCPUs / 12 GB memory and 200 GB total block-volume storage,
subject to home-region placement and host capacity ([OCI Always Free quotas](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[OCI trial terms](https://www.oracle.com/cloud/free/faq/)). Those compute resources could host a
small self-managed Compose/PostgreSQL test stack, but they are not an SLA-backed production
service. OCI's Always Free Autonomous Database is Oracle Database, not the PostgreSQL engine this
application uses; PostgreSQL would still need to run on the VM. Always Free database/compute must
be in the tenancy's home region, and A1 capacity may be unavailable there. OCI signup requires a
payment card for identity verification; Oracle describes the hold as temporary rather than a
charge. Do not upgrade to Pay As You Go to obtain more capacity without a separately authorized
spend cap.

OCI is a possible fallback if the owner later chooses to open an account and its home region has
capacity. It does not make the current AWS Terraform portable. Alibaba remains the first cloud
option to investigate because the owner selected that trial, but its account-specific free
entitlements must be checked before choosing either provider.

## Required console facts before any cloud action

Record these facts without copying keys, passwords, card details or other credentials into the
repository:

1. Alibaba account region and the exact trial product cards currently eligible for that account.
2. ECS instance family, CPU, RAM, disk and public egress quotas, trial end date and any resources
   that continue billing after expiry.
3. Whether RDS for PostgreSQL, OSS, KMS/Secrets Manager, and the container registry have an
   account-specific quota; record SKU, size, region, expiry and excluded billable features.
4. Maximum total spend authorized by the owner (including residual network, disk, backup, DNS and
   egress charges) and the shutdown date.
5. A domain and DNS/TLS plan for the authenticated public site, or confirmation that this remains
   a private IP-only test.

No Alibaba or OCI account API was called for this review. No cloud resource, payment method,
secret, model endpoint or benchmark run was created. The local stack remains the only verified
deployment target until the above inputs are known and an Alibaba-specific plan is reviewed.
