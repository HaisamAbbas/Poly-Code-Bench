# Free cloud options for a small public demo

Research checked 2026-10-07. No cloud account, resources, trial credits, or model tokens have been used. The current local Compose stack remains the verified deployment target.

## Fit for PolyCodeBench

| Option | Free allocation seen in provider documentation | Fit and limits |
| --- | --- | --- |
| Alibaba Cloud trial | The public trial page advertises 80+ products and includes an ECS t5 offer listed as 1 vCPU / 1 GB for one year. Product eligibility, region, exact quota, and end date are account-specific. | That published ECS size is below the local stack's comfortable footprint. Other account-specific offers may fit, but the console must confirm them. Alibaba product trials require an eligible account and payment method; usage beyond the free quota can be billed. OSS's S3 API requires virtual-hosted addressing, while the current adapter uses path-style addressing. |
| Oracle Cloud Free Tier | The Always Free documentation lists up to 2 Ampere A1 OCPUs and 12 GB memory per month, plus 20 GB Object Storage. The separate US$300 trial credit lasts up to 30 days and is only available in select countries. | More plausible for a low-traffic, single-machine demo. PostgreSQL would need to run on the VM because the Always Free database list does not include OCI Database with PostgreSQL. Capacity is tied to the home region; Oracle documents possible A1 capacity shortages and idle-instance reclamation. This is not a highly available production setup. |

Provider documentation:

- [Alibaba Cloud Free Trial](https://www.alibabacloud.com/en/free) and [trial eligibility, payment method, and overage rules](https://www.alibabacloud.com/help/en/user-center/product-overview/learn-about-free-trials)
- [Alibaba OSS S3 API compatibility and addressing requirements](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3)
- [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/), [Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), and [trial terms and account verification](https://www.oracle.com/cloud/free/faq/)
- [OCI Database with PostgreSQL billing](https://docs.oracle.com/en-us/iaas/Content/postgresql/billing.htm) and [OCI S3-compatible Object Storage API](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi_topic-Amazon_S3_Compatibility_API_Support.htm)

## Recommended staging boundary

For a first internet-facing demo, use only public release snapshots and metadata-only model-submission requests. Keep hidden task bundles, candidate source, evaluation artifacts, model credentials, and benchmark execution out of both free-tier targets. The current AWS Terraform does not configure Alibaba or Oracle. Either cloud needs a separate reviewed deployment target, provider-specific object-storage settings, identity setup, HTTPS/DNS, backups, quotas, and teardown controls.

Do not enable a solve or grading worker as part of this small demo. The public application can show verified releases and accept bounded metadata requests, but executing submitted code requires a separately reviewed disposable sandbox target and explicit cost limits. Alibaba's solution-trial resources are described as proof-of-concept resources that are deleted with their data when the trial ends; they are unsuitable for retaining benchmark evidence. Do not use the advertised Model Studio token allowance for benchmark runs.

Before preparing either cloud target, check the signed-in console and record:

1. Provider, tenancy/account, home region, and any region restrictions.
2. Exact free products, instance shapes, storage/network quotas, expiry dates, and whether a payment method is required.
3. Maximum approved total spend, including possible overage, public egress, domain, and certificate charges; set alerts and hard quotas where supported.
4. Whether a persistent public demo is allowed by the selected offer and when all trial resources must be shut down.

The console details are still unchecked. Until they are available, the local stack is the only verified, no-cloud-cost target; no cloud resources will be created.
