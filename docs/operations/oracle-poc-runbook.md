# Oracle Cloud A1 proof-of-concept runbook

**Status: kit written, nothing provisioned.** No OCI API was called and no `terraform plan` or
`apply` was run while preparing this kit; Terraform was only formatted, initialised without a
backend and validated in a container. The owner has not yet confirmed an Oracle account, home
region, quotas or a spend limit. Read [oracle-free-tier-readiness.md](oracle-free-tier-readiness.md)
first: it explains the Always Free limits, idle reclamation and why this is a private,
synthetic-data POC only.

Scope: one `VM.Standard.A1.Flex` VM (default 2 OCPU / 12 GB), SSH or OCI Bastion access only, all
application listeners on `127.0.0.1`, reached from your laptop through an SSH tunnel. Use only
the signed `synthetic_internal` release data. All steps marked **OWNER** are run by you; the
kit never runs them.

Files: [`infra/oracle/terraform/`](../../infra/oracle/terraform/) (Terraform root),
[`infra/oracle/cloud-init.yaml`](../../infra/oracle/cloud-init.yaml) (Docker + deploy user),
[`scripts/oracle/bootstrap-poc.sh`](../../scripts/oracle/bootstrap-poc.sh) (on-VM secrets).

## 1. Pre-checks in the OCI console (OWNER)

Record only non-secret facts. Stop if any answer is unknown or unacceptable.

1. **Account status**: Always Free only, promotional trial, or paid. Note remaining credit and
   its expiry date.
2. **Home region**: write down the region identifier. Always Free A1 must be created in the home
   region, and a Free Tier tenancy has only one subscribed region.
3. **A1 availability and quotas**: Governance > Limits, Quotas and Usage. Confirm at least 2
   `standard-a1-core-count` and 12 GB `standard-a1-memory-count` remain, and boot-volume storage
   (Always Free total 200 GB across all volumes). "Out of host capacity" errors are common; if
   one appears, retry later or change `availability_domain_index`.
4. **Billing alert**: create a budget with an alert at a low threshold (for example 1 USD) before
   creating anything. Alerts are notifications, not a hard spend cap.
5. **Shutdown date**: pick the date the POC is destroyed and put it in your calendar.
6. **Image**: Compute > Images (or the instance-create wizard), filter to Canonical Ubuntu 24.04
   for the **aarch64 / Arm** architecture in the home region. Copy its image OCID.
7. **SSH key**: have an OpenSSH key pair; only the public key is used by Terraform.
8. **Your CIDR**: your current public IP as a `/32`. Never use `0.0.0.0/0`.

## 2. Terraform (OWNER; the commands below were written but not run)

Install Terraform >= 1.9 and the OCI CLI config profile (`oci setup config`) on your machine.

```bash
cd infra/oracle/terraform
cp terraform.tfvars.example terraform.tfvars   # ignored by git; fill in the placeholders
terraform init
terraform providers lock -platform=linux_amd64 -platform=windows_amd64 -platform=darwin_arm64
terraform plan -out pcb-poc.tfplan             # review: one VCN, subnet, VM, bastion; no surprises
terraform apply pcb-poc.tfplan
```

Nothing is created until `owner_spend_acknowledged = true`, `home_region`, `compartment_ocid`,
`image_id`, `ssh_public_key` and `allowed_ssh_cidr` are set; guard preconditions fail the plan
otherwise. `free_tier_only = true` (default) additionally refuses any shape other than A1 with
more than 2 OCPUs or 12 GB. Keep `terraform.tfstate` private; it can contain instance metadata.

Access mode (see `access_mode`):

- `bastion` (default): the VM is in a private subnet with no public IP. Outbound goes through a
  NAT gateway. You reach it through the OCI Bastion service, restricted to `allowed_ssh_cidr`.
- `public_ssh`: public IP, with SSH open only to `allowed_ssh_cidr`. Simpler, but the VM is on the
  internet; pick it only if the Bastion service is unavailable in your region.

Get the tunnel recipe with `terraform output ssh_tunnel_command_template`.

## 3. Connect and bootstrap the VM

Wait a few minutes for cloud-init, then connect (Bastion session or direct SSH) as `deploy`:

```bash
cloud-init status --wait && docker --version && docker compose version
git clone <your-repository-url> polycodebench && cd polycodebench
scripts/oracle/bootstrap-poc.sh
```

`bootstrap-poc.sh` checks `aarch64`, Docker and compose, then writes generated credentials to
`/etc/pcb/poc.env` (root-only, mode 600, outside the checkout). It prints no secret values. The
key set matches `REQUIRED_ENV` in `scripts/local_stack.py`. Install it as the ignored `.env`:

```bash
sudo install -m 600 -o "$USER" /etc/pcb/poc.env .env
```

## 4. Build the ARM64 images on the VM

The pinned base images in `Dockerfile.api` (Python 3.12.14, uv 0.12.17) and `Dockerfile.web`
(Node 24.21.0) all publish `linux/arm64` variants (checked 2026-10-08 with
`docker buildx imagetools inspect`). `docs/operations/container-images.md` shows
`--platform linux/amd64`; on the A1 host use `linux/arm64`:

```bash
docker build --platform linux/arm64 --file Dockerfile.api --tag pcb-api:poc .
docker build --platform linux/arm64 --build-arg PCB_PUBLIC_API_URL=http://127.0.0.1:8010/v1 \
  --file Dockerfile.web --tag pcb-web:poc .
```

Not verified: that every Python or Node dependency installs from arm64 wheels or builds from
source within the VM's resources. Watch the build output and `free -h`.

## 5. Prepare and start the stack

The verified procedure is the host-process path in [local-self-hosting.md](local-self-hosting.md),
translated to bash. It needs `uv` and Node 24 on the VM:

```bash
# uv (pinned release); Node 24 arm64 tarball with checksum verification
curl -LsSf https://astral.sh/uv/0.12.17/install.sh | sh
NODE=24.21.0; cd /tmp
curl -fsSLO https://nodejs.org/dist/v$NODE/node-v$NODE-linux-arm64.tar.xz
curl -fsSL https://nodejs.org/dist/v$NODE/SHASUMS256.txt | grep "node-v$NODE-linux-arm64.tar.xz" | sha256sum -c -
sudo tar -xJf node-v$NODE-linux-arm64.tar.xz -C /usr/local --strip-components=1
cd ~/polycodebench
```

Then, with the `.env` from step 3 in place:

```bash
uv run --locked --group dev python scripts/local_stack.py prepare   # keeps .env; writes Keycloak realm and identity files
docker compose --profile local-auth up -d postgres object-store keycloak
uv run --locked --group dev python scripts/local_stack.py wait-for-keycloak
uv run --locked --group dev python scripts/local_stack.py bootstrap-db
uv run --locked --group dev python scripts/local_stack.py seed
set -a; . ./.env; set +a
uv run --locked --all-packages uvicorn polycodebench_api.app:app --host 127.0.0.1 --port 8010 &
npm exec --yes --package=pnpm@12.5.1 -- pnpm install --frozen-lockfile
npm exec --yes --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web exec next dev --hostname 127.0.0.1 --port 3001 &
```

If `prepare` rejects the file, it lists the missing keys and changes nothing; delete `.env` and
let `prepare` generate it itself instead (the `/etc/pcb/poc.env` file is then unused). Compose
publishes only `127.0.0.1:55432`, `127.0.0.1:8333` and `127.0.0.1:8080`. The images from step 4
are an ARM64 build check; the web image runs in production mode, which rejects the local HTTP
Keycloak issuer, so the dev server above is the supported way to sign in.

Check that nothing listens publicly: `ss -ltn` must show only `127.0.0.1` (and SSH on `:22`).
Check memory: `docker stats --no-stream` and `free -h`; the 12 GB shape is expected to suffice
(local snapshot about 1.5 GiB) but this was not measured on A1.

## 6. Reach it from your laptop

With the SSH tunnel from `terraform output ssh_tunnel_command_template` running, open
`http://127.0.0.1:3001/leaderboard`. Sign in at `/model-submissions` with the
`LOCAL_SUBMITTER_*` values from the VM's `.env` (read them on the VM; do not copy them into
chat, tickets or the repository).

## 7. Teardown (OWNER)

On or before the shutdown date:

```bash
terraform destroy      # in infra/oracle/terraform; review the list, then confirm
```

Then confirm in the console that the instance, boot volume, VCN, NAT gateway and Bastion are
gone, delete the budget alert if no longer needed, and securely delete `terraform.tfstate`,
`terraform.tfvars` and any saved plan file. The VM's generated credentials disappear with it.

**Idle reclamation caveat.** Oracle may reclaim an Always Free compute instance when, over seven
days, CPU 95th percentile, network utilisation and (for A1) memory utilisation are each below
20%. A mostly idle POC can be stopped or reclaimed; treat data on it as disposable and re-create
from this kit. Do not rely on this VM for persistence. Whether to upgrade the account (which
changes the spend picture) is an owner decision outside this kit.

## What this does NOT prove

- It is not the AWS staging environment and does not satisfy the cloud staging gate or
  E2E-42/E2E-43; the deployment manifests, guest sandbox and evidence storage target AWS.
- It has no managed PostgreSQL and no managed backups or restore evidence. PostgreSQL is a
  container with a Docker volume, and the Always Free Autonomous Database is not PostgreSQL.
- It has no public exposure, domain, HTTPS, production OIDC or access control review.
- It is not a benchmark release: the data is synthetic `synthetic_internal` display data, no
  model endpoint is contacted and no benchmark run is created.
- It does not prove OCI Object Storage, instance-principal or Bastion behaviour beyond the VM.
