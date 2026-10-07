# Free cloud target review (2026-10-07)

## Decision

Keep the Compose stack as the usable no-cloud environment. The account-specific Alibaba and OCI
quotas remain unchecked, and no spend ceiling or shutdown date has been approved, so do not create
cloud resources. The current Terraform targets AWS and cannot deploy to either provider.

The Alibaba Free Trial page is login-dependent; its generic offers do not prove this account's
eligibility or quota. The currently advertised ECS t5 offer is 1 vCPU / 1 GiB for one year, which
is below a sensible size for the full stack. Product trials require a verified account and linked
payment method, and the ECS trial is limited to new computing-product users. A past ECS or Simple
Application Server purchase, bill, or trial can disqualify the account. Verify the exact offer in
the signed-in console; see [current Free Trial offers](https://www.alibabacloud.com/en/free?_p_lc=1)
and [Alibaba's trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials).

That listed 1 GiB offer is below a sensible target for the complete site stack (API, web, OIDC,
PostgreSQL, object store, monitoring and backups); this is an engineering estimate, not a vendor
quota. OSS's new-user offer covers storage capacity only for one month; requests, traffic, and
other excluded items may still be billable. The managed PostgreSQL 17 service is not established
as free by the trial page. Alibaba's separate solution-trial points are not a persistent-hosting
substitute: they are for the named solution, trial duration is at most 168 hours, and trial
resources and their data are deleted when the trial ends or points run out. Billing deductions
can continue after the trial. A promotional label therefore is not a zero-spend limit.

The project currently has no owner-supplied region, exact ECS SKU/quota, trial expiry, PostgreSQL
or OSS entitlement, domain, or authorized spend cap. The API's public OIDC callback also needs a
stable HTTPS origin, and the local-only Keycloak configuration is not suitable for public use.
Until those inputs exist, stay on the already-tested loopback stack. A trial deployment would
need a separately reviewed Alibaba IaC target, TLS/domain setup, secret setup and teardown drill;
none of those resources have been created.

## OCI comparison

OCI offers US$300 of promotional credit for up to 30 days, separate from its Always Free
resources. Always Free A1 compute totals 2 OCPUs / 12 GB memory with 200 GB block storage in the
home region, subject to available host capacity. It is a more plausible size for a private,
self-managed Compose/PostgreSQL development session than Alibaba's generic 1 GiB ECS offer, if
the account can allocate it and the app's images are built for ARM64. However, OCI may reclaim an
Always Free instance after a seven-day period if its CPU p95, network, and A1 memory utilization
are all below 20%; it is not reliable public hosting. The Always Free Autonomous Database is
Oracle Database, not PostgreSQL, so PostgreSQL must run on the VM. Paid resources made with trial
credits are reclaimed after the trial grace period unless the account is upgraded. Signup
requires card verification; the temporary authorization hold is not a resource charge. See
[OCI Always Free quotas](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[OCI trial terms](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm), and the
[OCI FAQ](https://www.oracle.com/cloud/free/faq/).

For a no-spend private remote-development experiment, OCI's Always Free A1 shape appears more
capable than the generic Alibaba ECS card, but ARM64 build compatibility, home-region capacity,
idle reclamation, and account eligibility still need checking. Alibaba remains a possible
time-boxed trial if the signed-in console shows a larger suitable instance and sufficient
database/storage/network entitlements. Neither provider can close the repository's AWS staging
gate, and neither should be selected until its exact console quota, billing exposure, and
shutdown date are known.

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
