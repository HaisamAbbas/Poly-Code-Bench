# Verified command registry - Prompts 00-01

These are inspection commands actually executed during Prompt 00. They are not application commands. No application tests were run or added.

| Command / check | Result |
|---|---|
| `Get-Location` | `D:\Poly-Code-Bench`. |
| `Get-ChildItem -Force` and `rg --files -g "!node_modules" -g "!vendor" -g "!dist" -g "!build"` | Only the three source Markdown files were present. |
| Search `AGENTS.md` from workspace through filesystem ancestors | No `AGENTS.md` found. |
| `git status --short --branch`, `git rev-parse --show-toplevel`, `git rev-parse HEAD`, `git ls-files` | Git reported "not a git repository"; no status, commit, or tracked-file list is available. |
| `Get-FileHash -Algorithm SHA256 -LiteralPath <three source files>` | Architecture `6dfee84b9787f315d7d5aa7afd9b1de29760aac16b5d3ac887b7ecd76d899d69`; Technical Spec `2dbfd0f00c561b9348419a2659794913658fb47d15090e09a3e905a89cdea7bd`; prompt pack `0687c2e1e6340fd3b0f69b553418c290aeb59aa7cdf3a88b9d2ef3159c7e1344`. First two match pack section 1.1. |
| `python --version`; `uv --version`; `node --version`; `npm --version` | Python 3.12.10, uv 0.12.17, Node v25.2.1, npm 11.6.2. |
| `pnpm --version`; `yarn --version`; `psql --version`; `pg_isready --version`; `podman --version`; `aws --version`; `terraform --version` | Commands not found (pnpm/yarn, PostgreSQL clients, Podman, AWS CLI, Terraform). |
| `docker --version` | Docker CLI 29.7.2 is installed. |
| `docker info --format 'server_version={{.ServerVersion}} os={{.OperatingSystem}}'` | Failed: Docker Desktop Linux engine pipe unavailable; daemon/VM execution is not currently usable. |
| `Get-Service -Name postgresql-x64-18` | PostgreSQL 18 Windows service reports Running; database readiness/connectivity was not verified because client tools are absent. |
| Environment-variable name filter for `PCB_*`, model-provider, cloud, S3/MinIO, and PostgreSQL prefixes | No matching variable names. Secret values were not read. This does not establish that credentials are unavailable from every external credential store. |
| Workspace directory check for `apps`, `packages`, `tests`, `config`, `infra`, `taskpacks`, `docs` | None present before Prompt 00. |

`python docs/implementation/verify_prompt00.py` - Prompt 00 ran the original version and reported PASS for the then-counted 142 PCB tickets. Prompt 01 expanded its checks to all 14 REQ, 24 WP, 43 E2E, Prompts 00-34, all 142 pack PCB tickets, owners/evidence, progress counts, and source hashes; final Prompt 01 result is recorded below.

## Prompt 01 checks actually run

| Command / check | Result |
|---|---|
| `python scripts/export_startup_schema.py` | PASS: generated `schemas/configuration/startup-config.v1.json` from the Pydantic settings model. |
| `python scripts/export_startup_schema.py --check` | PASS: checked in schema matches deterministic generator output. |
| `python scripts/check_boundaries.py` | PASS: internal package dependency directions, core/scoring prohibited imports, and frontend hidden-schema references checked. |
| `python scripts/smoke_workspace.py` | PASS: imported all 10 owned Python packages and exercised missing-config rejection plus a valid local API config. This is not a product execution test. |
| `python -m pytest -q -p no:cacheprovider` | PASS, 3 startup configuration tests. Earlier iterations failed before fixes to environment string parsing and safe validator messages; final run passed. |
| `python -m compileall -q packages scripts tests` | PASS: Python source syntax compilation. |
| `docker compose config --quiet` | PASS: local service definition parses. This does not start or pull images. |
| JSON syntax check for `package.json`, `apps/web/package.json`, `apps/web/tsconfig.json`, and `schemas/configuration/startup-config.v1.json` | PASS. |
| `uv lock` with `UV_CACHE_DIR` set to a workspace-local cache | BLOCKED: PyPI connection refused by the environment proxy. No `uv.lock` was fabricated. |
| `corepack pnpm@12.5.1 install --lockfile-only --ignore-scripts` | BLOCKED: Corepack's default user cache could not be written. Retried with `COREPACK_HOME` inside the workspace; the pnpm registry request was refused by the proxy. No `pnpm-lock.yaml` was fabricated. |
| `npm view next@16.3.7 version` (also React/Tailwind/ECharts/TypeScript/pnpm metadata queries) | BLOCKED: npm is configured `offline=true`; metadata is not cached. Declared versions need registry resolution before acceptance. |
| `python docs/implementation/verify_prompt00.py` | PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00-34, 142 PCB tickets, owners/evidence, progress and source hashes. |

Format, mypy, clean locked install, Python package build, pnpm install/typecheck/lint/Next build, and GitHub Actions were not run: lockfiles/dependencies are unavailable and this workspace has no Git repository. `docker compose up` was not run because Docker's Linux engine pipe is unavailable. The CI workflow defines these checks but is not evidence that they passed.
