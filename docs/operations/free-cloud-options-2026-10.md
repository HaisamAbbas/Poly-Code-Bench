# Free cloud option review — 2026-10-06

This is a documentation review only. No provider account was accessed and no cloud resource was
created. Free-tier eligibility, quotas, regions, and expiry still need to be checked in the owner’s
console before selecting a target.

## Alibaba Cloud

Alibaba separates product trials from solution trials. Product trials are tied to individual
products and their current offer cards. The official rules require a verified account and payment
method, and the ECS trial is limited to users with no previous qualifying ECS/Simple Application
Server order, bill, or trial. This does not establish that this account is eligible or that the
whole PolyCodeBench service set is covered.

Solution trials use trial points, currently described as a one-time 50-point grant for eligible
users. The docs value points against trial resource bills at USD 1 per point, limit each solution
trial to 168 hours, and state that trial resources and data are released when the trial ends or the
points reach zero. These solution-trial resources are for the named proof of concept, not a general
host for this application.

The current OSS offer is 500 GB of Standard LRS capacity for one month for eligible individual
users, but it covers storage only; other billable items remain outside that quota and usage can
become pay-as-you-go after it is consumed or expires. Alibaba’s KMS software-key trial is 14 days
and automatically becomes pay-as-you-go unless released. These offers therefore need active expiry
and billing checks.

The public trial materials do not verify the account’s ECS, ACK, RDS for PostgreSQL, KMS, registry,
and networking quotas together in one region. Until the owner checks the console, Alibaba remains
an unverified candidate. The current infrastructure code is AWS-specific; Alibaba would require a
separate deployment target and provider implementation.

## Oracle Cloud Infrastructure

OCI documents a USD 300 trial credit valid for up to 30 days. Paid resources created with trial
credits are reclaimed after the trial unless the account is upgraded. Always Free resources persist,
subject to their limits and region availability.

The published Always Free allowance includes up to two AMD micro VMs or a combined 2 OCPUs and
12 GB memory of Ampere A1 compute, 200 GB of block volumes, 20 GB of object storage with 50,000
requests per month, and 150 Vault secrets. Compute and several Always Free resources are limited to
the tenancy’s home region. OCI also reserves the right to reclaim idle Always Free compute under
its documented seven-day utilization conditions.

The Always Free database list includes Oracle Autonomous AI Database, Oracle NoSQL, and MySQL
HeatWave; it does not list a PostgreSQL service. OCI Database with PostgreSQL has billed compute,
storage, backups, and network components. Self-hosting PostgreSQL on an Always Free VM may be a
separate small-hosting design, but it would not reproduce the repository’s current managed
PostgreSQL/AWS staging architecture. OCI would also need its own deployment target; the existing
production sandbox is EC2-specific.

## Decision and remaining inputs

Keep the running website local-only. Alibaba remains the owner-selected trial direction, but its
actual eligibility and quotas are unknown. Do not start an Alibaba solution trial or any OCI paid
trial resources until the owner has verified the account’s region, exact per-service quota and
expiry, allowed networking/domain setup, and accepted maximum total spend and shutdown date. A
provider’s trial credit is not an account-wide spending cap.

Official sources:

- [Alibaba Cloud free trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials)
- [Alibaba Cloud OSS new-user trial quota](https://www.alibabacloud.com/help/en/oss/free-quota-for-new-users)
- [Alibaba Cloud KMS trial billing FAQ](https://www.alibabacloud.com/help/en/kms/key-management-service/product-overview/faq-2)
- [Oracle Cloud Infrastructure Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [OCI Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI Database with PostgreSQL billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm)
