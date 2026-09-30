# Prompt 00 prerequisite assessment

| Area | Observed state | Interpretation / next action |
|---|---|---|
| Repository instructions | No `AGENTS.md` found in workspace or ancestors. | No additional local instructions apply. |
| Git | Git 2.55.0 installed; current directory is not a Git repository. | No branch, commit, or dirty-state assessment is possible. Initialize/reconcile repository tracking in Prompt 01 without replacing workspace contents. |
| Runtime | Python 3.12.10 and Node v25.2.1/npm 11.6.2 present; uv 0.12.17 present. | Suitable tools are available for Prompt 01 investigation; versions are observed, not pinned project runtimes. Python version meets spec baseline version family; Node 25 is not yet approved/pinned. |
| Package managers | pnpm and yarn commands not found; Corepack 0.34.6 is available. npm reports `offline=true`; the explicit Corepack attempt could not reach the registry. | pnpm 12.5.1 is declared via Corepack, but the package-manager install and lockfile remain blocked. |
| PostgreSQL | PostgreSQL 18 Windows service Running; `psql` and `pg_isready` unavailable. | Presence is not connectivity or version compatibility evidence. Spec baseline targets PostgreSQL 17; confirm an approved local Postgres 17/container approach after Docker daemon is available. |
| Object storage | No S3/MinIO endpoint configuration names and no service configuration files observed. | No object store established. Prompt 01 local service choice remains to be implemented. |
| Docker / VM | Docker CLI 29.7.2 installed; daemon pipe unavailable. Podman absent. | Local containers and guest lifecycle cannot currently be exercised. Production disposable VM/cloud driver is not configured or verified. |
| Cloud / IaC | AWS CLI and Terraform not found; no relevant environment-variable names found. | Cloud account, region, IAM identities, infrastructure target and authorization are unknown; no provisioning attempted. |
| Model and judge | No provider/config files, endpoint variable names, or model registry present in workspace. | Endpoints, credentials, model revisions/capabilities, judge identity and authorization are unknown. No calls made. |
| Budget | No run config or budget record present. | Monetary caps and provider price snapshots must be explicitly supplied before live runs; no spending attempted. |
| Data/source rights | No task packs or rights records present. | Rights and source eligibility remain unverified; no task admitted. |
| Human calibration | No calibration packets, labels, or reviewer records present. | Required human calibration is not evidenced; identify qualified reviewers and collect labels in the relevant phase. |
| Publication | No domain, hosting, signing key, or publication config present. | Target is unknown; no deployment or publication attempted. |


## Prompt 01 update

Python 3.12.10, uv 0.12.17, and Node 24.21.0 are pinned in workspace files; Corepack/pnpm 12.5.1 is declared. The currently observed machine remains Node 25.2.1 (not the pinned Node 24 runtime). `npm config get offline` returned `true`. Attempts to generate `uv.lock` and `pnpm-lock.yaml` failed because the outbound PyPI/npm connections are refused; package-manager cache directories outside the workspace are not writable. The Python environment has Pydantic 2.13.4, which was sufficient for the local configuration tests, but it is not the declared locked environment. Docker Compose v5.5.1 is installed while Docker's Linux engine pipe is unavailable. PostgreSQL 18 service remains present, but the local Compose target is pinned to PostgreSQL 17.6 per the spec and has not been started. No spending or provider calls occurred. Rights and calibration evidence remain absent. Configuration schema, tests and import/boundary smoke checks now exist; formatting, typing, package builds and frontend checks await the locked install.

### Prompt 01 resume observations (2026-09-30)

The current checkout is Git branch `main`, tracking `origin/main`, at pushed commit `08a0edd4b674e8da62f6a6f5e65cdd53db1a086b` (Haisam Abbas, `HaisamAbbas@outlook.com`). This resolves the earlier “no Git repository” state recorded during Prompt 00. New `uv lock` and Corepack/pnpm lock attempts still fail because the configured proxy refuses registry connections. Direct Ruff format/lint, pytest, imports, boundaries, schema consistency and Compose parsing pass after fixes. `mypy` and the `uv_build` backend are not installed; clean Python/Node installs, builds, full CI, Docker-backed services, development image digest approval and PostgreSQL connectivity remain unverified. No provider spending, cloud provisioning, upload or release action occurred.


## Prompt 01 completion update (2026-09-30)

- Python 3.12.10 / uv 0.12.17 and Node 24.21.0 / npm 11.19.0 / Corepack pnpm 12.5.1 were used. PyPI and npm access succeeded for lock generation and dependency installs after the approved network retry; do not infer persistent registry availability outside this run. `uv.lock` and `pnpm-lock.yaml` are present and frozen installs passed.
- Docker Desktop Linux engine is available. PostgreSQL 17.6 is healthy and `pg_isready` accepts `polycodebench` at host port 55432. SeaweedFS 4.48 starts in local `mini` mode; HTTP on the S3 endpoint at `localhost:8333` responds. Both image references are immutable and verified by pinned registry/index digest and pulled linux/amd64 content identity. The images are approved for local development only; signature provenance was not independently verified.
- Production VM/cloud execution, model/judge endpoints and credentials, explicit spend budgets, task/source rights, human calibration evidence, and publication/signing target remain absent or unverified. No model call, paid run, cloud provisioning, upload, or release action occurred.
