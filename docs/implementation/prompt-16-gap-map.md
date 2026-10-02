# Prompt 16 implementation scope

REQ-09/10/12; WP-16; PCB-16-1 through PCB-16-4.

The publication package previously declared its ownership boundary only. Existing scorer-owned
immutable Scorecard documents and canonical serialization are reused. Existing artifact projection
services remain separate: they control artifact visibility, while the release publisher controls
reviewed aggregate projections and board pointers.

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Frozen cohort, hierarchy, failures and coverage | publication/aggregation.py | test_publication_aggregation.py; E2E-28 |
| Cluster hierarchy and paired uncertainty | publication/aggregation.py | test_uncertainty.py; E2E-29 |
| Internal reports and fixed-seed replay | publication/reporting.py, cli.py | test_publication_reporting.py |
| Exact review, immutable corrections | publication/releases.py | test_publication_releases.py; E2E-30 |
| Safe projection, signing and atomic pointer | publication/releases.py | test_releases.py; E2E-30 |

D-16-01: The authorized local release target is transactional SQLite, with signing keys supplied
outside execution workers. The API is an authenticated-principal application API; HTTP transport
and deployed public readers remain with Prompt 29. The CLI is trusted local administration and its
role flags do not establish remote authentication. Remote targets fail closed. Synthetic fixtures
remain explicitly internal/exploratory; public publication requires an authorized target, trusted
validator receipts, and exact-content human review. Full-product editorial index remains disabled.

No scientific benchmark, rights approval, human calibration or production deployment is inferred
from synthetic acceptance tests. Prompt 13/14/15 dependency acceptance remains owned by those runs.
