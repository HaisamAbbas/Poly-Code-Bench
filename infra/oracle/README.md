# Oracle Cloud A1 POC kit

Private, synthetic-data proof of concept on one OCI Always Free Ampere A1 VM. Written but never
applied; nothing here has provisioned a resource.

- `terraform/` - OCI root (VCN, subnet, security list, optional Bastion, A1 instance, guards)
- `cloud-init.yaml` - installs Docker Engine and compose for arm64, creates the `deploy` user
- `../../scripts/oracle/bootstrap-poc.sh` - run on the VM to generate secrets under `/etc/pcb`

Start with the runbook: [`docs/operations/oracle-poc-runbook.md`](../../docs/operations/oracle-poc-runbook.md).
