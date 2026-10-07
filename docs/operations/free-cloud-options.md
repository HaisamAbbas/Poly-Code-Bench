# Free cloud options for a small public demo

Research checked 2026-10-07. No cloud resources have been provisioned for this project, and this work has not consumed trial credits or model tokens. The current local Compose stack remains the verified deployment target.

## Fit for PolyCodeBench

| Option | Free allocation seen in provider documentation | Fit and limits |
| --- | --- | --- |
| Alibaba Cloud trial | The public page advertises one ECS t5 instance at 1 vCPU / 1 GiB for one year. A separate ECS trial provides a finite CNY 300 personal or CNY 660 enterprise quota for three months; the personal maximum rate consumes CNY 300 in about 15 continuous days. | The 1-GiB offer is below the measured local stack footprint. The larger trial shape may support a short private POC, subject to the account's region and remaining quota. It uses pay-as-you-go billing, is not automatically released at expiry, and the ECS trial cannot be used for ICP filing for sites hosted in mainland China. The code now has an opt-in OSS S3-compatibility adapter with ECS RAM-role credentials, but no Alibaba deployment root or real ECS/OSS test. |
| Oracle Cloud Free Tier | Always Free includes up to 2 Ampere A1 OCPUs / 12 GiB RAM, 200 GB combined boot and block storage, and 20 GB Object Storage with 50,000 monthly requests. One 10-Mbps load balancer is also Always Free. The separate US$300 trial credit expires after 30 days or when consumed. | Better compute capacity for a remote private POC if A1 capacity exists in the home region. PostgreSQL must run on the VM because OCI's Always Free databases are not PostgreSQL. Idle A1 VMs may be reclaimed; the 20-GB object quota and single-host layout are poor fits for retained benchmark evidence or highly available staging. OCI identity and deployment integration are not implemented. |

Provider documentation:

- [Alibaba Cloud Free Trial](https://www.alibabacloud.com/en/free) and [trial eligibility, payment method, and overage rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials)
- [Alibaba ECS trial quota, expiry, billing, regions, and ICP limitation](https://help.aliyun.com/en/ecs/user-guide/ecs-free-trial)
- [Alibaba OSS S3 API compatibility and addressing requirements](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3)
- [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/), [Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), and [trial terms and account verification](https://www.oracle.com/cloud/free/faq/)
- [OCI Database with PostgreSQL billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm) and [OCI S3-compatible Object Storage API](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi_topic-Amazon_S3_Compatibility_API_Support.htm)

## Recommended staging boundary

For a first internet-facing demo, use only public release snapshots and metadata-only model-submission requests. Keep hidden task bundles, candidate source, evaluation artifacts, model credentials, and benchmark execution out of both free-tier targets. The current AWS Terraform does not configure Alibaba or Oracle. Alibaba's OSS/RAM-role adapter is only a storage integration, not a deployment target; either cloud needs a separate reviewed infrastructure and identity design, database/secret wiring, HTTPS/DNS, backups, quotas, and teardown controls.

**Current fit:** Alibaba's finite ECS product trial is suitable only for a short, private, synthetic-data POC after its console quotas and shutdown date are verified. OCI Always Free A1 is the stronger candidate for an ongoing private development POC, subject to its home-region capacity and idle-instance policy. Neither option satisfies the existing AWS staging gate or verifies the production sandbox, recovery, and identity controls. The local Compose stack remains the only environment currently verified end to end.

Do not enable a solve or grading worker as part of this small demo. The public application can show verified releases and accept bounded metadata requests, but executing submitted code requires a separately reviewed disposable sandbox target and explicit cost limits. Alibaba's solution-trial resources are described as proof-of-concept resources that are deleted with their data when the trial ends; they are unsuitable for retaining benchmark evidence. Do not use the advertised Model Studio token allowance for benchmark runs.

Before preparing either cloud target, check the signed-in console and record:

1. Provider, tenancy/account, home region, and any region restrictions.
2. Exact free products, instance shapes, storage/network quotas, expiry dates, and whether a payment method is required.
3. Maximum approved total spend, including possible overage, public egress, domain, and certificate charges; set alerts and hard quotas where supported.
4. Whether a persistent public demo is allowed by the selected offer and when all trial resources must be shut down.

The console details are still unchecked. Until they are available, the local stack is the only verified, no-cloud-cost target; no cloud resources will be created.
