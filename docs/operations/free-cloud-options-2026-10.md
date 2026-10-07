# Free cloud option review — 2026-10-06

This is a documentation review only. No provider account was accessed, no trial was claimed,
and no cloud resource was created. Product eligibility, region, quota, and expiry must still be
verified in the owner's account before selecting or deploying a target.

## Alibaba Cloud

Alibaba offers product trials and separate solution trials. Product trials require a verified
account, a supported payment method, no overdue balance, and no prior qualifying order for that
product. Each user generally gets one trial per product. The ECS trial has stricter prior-use
rules. The public page currently advertises an ECS t5 offer of 1 vCPU/1 GB for one year; that
does not show this account is eligible or that the whole application stack is covered. The
offer card and account console are authoritative for instance shape, region, and duration.

The one-year 1-vCPU/1-GiB t5 card is distinct from Alibaba's ECS free-trial guide, which describes
a three-month eligibility window backed by finite CNY 300 personal or CNY 660 enterprise quota.
That guide permits larger instance shapes, subject to identity and region, but quota is consumed
at the selected instance's hourly rate: CNY 300 covers about 15 continuous days at CNY 0.833/hour.
It may support a short private POC if the signed-in console confirms enough remaining quota and a
suitable SKU. The instance does not automatically release at expiry, so continued use can be
billed. Neither public offer establishes this account's entitlement. See the [ECS free-trial
guide](https://help.aliyun.com/en/ecs/user-guide/ecs-free-trial).

That ECS shape is too small to select as the host for the complete PolyCodeBench Compose stack
(PostgreSQL, object store, Keycloak, API, web, and an optional worker); at most, it is a candidate
for a pared-down demonstration after resource testing. ApsaraDB RDS supports PostgreSQL, but I
did not verify an eligible RDS for PostgreSQL free-instance quota on the current public offer
cards. Treat managed PostgreSQL as billable unless the account console shows an exact applicable
quota. The ACK public card says the service is free to use, while its cluster documentation also
requires an account balance of at least CNY 100 when creating a cluster with pay-as-you-go cloud
resources. This does not establish free worker nodes or free networking.

Alibaba solution trials are isolated POC environments, not general-purpose production accounts.
The current rules describe a one-time initial 50-point grant for eligible users, with actual
resource bills deducted at USD 1 per point. A solution trial is limited to 168 hours. At trial
end, resource release, or zero points, Alibaba releases the trial resources, deletes the trial
account, and erases trial data. The actual solution page controls its included services and point
requirements.

The new-user OSS offer currently covers 500 GB of Standard LRS storage for one month for eligible
individual users (1 TB for eligible enterprise users). It does not cover every OSS billable item;
excess or post-expiry use can be billed. KMS's separate software-key trial lasts 14 days and
automatically changes to pay-as-you-go unless the instance is released. These are separate,
product-specific offers, not evidence that ECS, ACK, RDS for PostgreSQL, KMS, a registry, and
public networking fit under one free quota.

Alibaba remains the owner's selected trial direction, but actual eligibility and quotas are
unknown. The repository deployment code is AWS-specific; Alibaba would need a separately reviewed
target. Do not treat trial credits or a budget notification as a hard spending cap.

## Oracle Cloud Infrastructure

OCI advertises USD 300 in trial credits for up to 30 days, in addition to Always Free services.
The credit ends when it is consumed or the period expires, whichever comes first. If no upgrade is
made, eligible Always Free resources remain available, subject to service limits, region support,
and capacity. Signup requires a supported credit/debit card for identity checks and can place a
temporary authorization hold. Oracle allows one Free Tier account per person; it also says
accounts idle for 30 days or more may be suspended or terminated.

The current Always Free infrastructure includes up to two AMD E2.1.Micro VMs, or Ampere A1
compute totaling 2 OCPUs and 12 GB memory; 200 GB combined boot/block storage; 20 GB object storage
and 50,000 monthly object requests; 150 Vault secrets; and a 10 Mbps flexible load balancer for
eligible tenancy dates. These figures have individual details and are generally home-region
limited. Oracle may reclaim Always Free compute when its documented CPU/network (and A1 memory)
utilization conditions are all below 20% for seven days. Verify the console's Always Free labels
and service limits before deployment.

OCI Always Free database offerings include Oracle Autonomous AI Database, Oracle NoSQL, and MySQL
HeatWave; they do not include PostgreSQL. OCI Database with PostgreSQL bills for compute, storage,
backups, and network components. Self-hosted PostgreSQL on an eligible VM could be a separate
small-hosting design, but it would not match the current AWS managed PostgreSQL staging
architecture, and it needs its own backup, restore, patching, and availability plan. OCI also
requires a separate deployment target.

## Decision and remaining inputs

Keep the running website local-only. Alibaba is the selected candidate to investigate, not a
verified deployment target. The owner has not checked account eligibility or service quotas. No
cloud resource should be provisioned until the account console confirms the region and exact
service quotas/expiry, and the owner supplies the networking/domain plan and an approved maximum
spend and shutdown date. The app's infrastructure has no provider-neutral target today.

Official sources, checked 2026-10-06:

- [Alibaba Cloud Free Trial](https://www.alibabacloud.com/en/Free?_p_lc=1)
- [Alibaba Cloud free trial rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials)
- [Alibaba Cloud ACK free-tier offer](https://www.alibabacloud.com/en/free?_p_lc=1&tags=always_free)
- [Alibaba Cloud ACK limits and account-balance requirement](https://help.aliyun.com/en/ack/product-overview/limits)
- [ApsaraDB RDS PostgreSQL support](https://www.alibabacloud.com/en/product/apsaradb-rds?_p_lc=1)
- [Alibaba Cloud OSS new-user trial quota](https://www.alibabacloud.com/help/en/oss/free-quota-for-new-users)
- [Alibaba Cloud KMS billing FAQ](https://www.alibabacloud.com/help/en/kms/key-management-service/product-overview/faq-2)
- [Oracle Cloud Free Tier and FAQ](https://www.oracle.com/cloud/free/)
- [OCI Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI Database with PostgreSQL billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm)
