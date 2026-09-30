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

At the original Prompt 01 run, format/mypy, clean locked install, Python package build, pnpm install/typecheck/lint/Next build, and GitHub Actions were not run because locks/dependencies were unavailable and the workspace had no Git metadata. The updated Prompt 01 resume checks below supersede the checks they explicitly reran. `docker compose up` remains unrun because Docker's Linux engine pipe is unavailable. The CI workflow defines checks; it is not evidence that they passed.

## Prompt 01 resume checks (2026-09-30)

| Command / check | Result |
|---|---|
| `git status --short --branch`; `git log -1 --format='%H%n%an <%ae>%n%s'`; `git remote -v` | Before resumed edits: clean `main...origin/main`; HEAD `08a0edd4b674e8da62f6a6f5e65cdd53db1a086b`, author `Haisam Abbas <HaisamAbbas@outlook.com>`, subject `Bootstrap PolyCodeBench workspace`; configured Poly-Code-Bench remote. |
| Prompt 01 source-section review | Read Technical Spec §§1–2, 22 and 26; Architecture §§2–3 and 16. |
| `python -m ruff format --check .` | Initially found five unformatted Python files and parsed normative source Markdown; fixed Python formatting and excluded only the three source Markdown files from Ruff. Re-run PASS: `34 files already formatted`. |
| `python -m ruff check .` | Initially reported 16 style/import issues. Fixed those in Python implementation/checker/test files; re-run PASS: `All checks passed!`. |
| `python -m pytest -q -p no:cacheprovider` | PASS: 3 configuration tests. |
| `python docs/implementation/verify_prompt00.py` | PASS: requirement/work-package/E2E/prompt/ticket/owner/evidence counts and source hashes. |
| `python -m compileall -q packages scripts tests`; `python scripts/check_boundaries.py`; `python scripts/smoke_workspace.py`; `python scripts/export_startup_schema.py --check` | PASS: syntax compile; package boundaries; ten package imports plus valid/invalid config; generated schema consistency. |
| `docker compose config --quiet` | PASS: Compose syntax only; no image or service verification. |
| `uv lock` with workspace-local `UV_CACHE_DIR` | BLOCKED: PyPI request to `https://pypi.org/simple/pydantic/` refused by the environment proxy; no lockfile written. |
| `COREPACK_HOME=<workspace .cache>`, `npm_config_offline=false`, `corepack pnpm@12.5.1 install --lockfile-only --ignore-scripts` | BLOCKED: Corepack could not fetch pnpm from `https://registry.npmjs.org/pnpm/-/pnpm-12.5.1.tgz`; connection to configured proxy `127.0.0.1:9` refused; no lockfile written. |
| `uv build --all-packages` with workspace-local `UV_CACHE_DIR` | Interrupted after build dependency retries caused by registry refusal; no build result claimed. |
| `uv build --no-build-isolation --all-packages` with workspace-local `UV_CACHE_DIR` | FAIL: local `uv_build` backend is absent. Installing it requires the currently blocked package registry; package build remains pending. |
| `python -m mypy packages/configuration/src scripts` | FAIL: `mypy` is absent from the local Python environment; locked dev dependency install is blocked. Typecheck remains pending. |
| `docker info --format 'server={{.ServerVersion}}'` | BLOCKED: Docker Desktop Linux engine pipe is absent. No images were pulled and services were not started. |
| `Test-Path uv.lock`; `Test-Path pnpm-lock.yaml` | Both `False`; reproducible lockfiles do not exist. |

## Subsequent Prompt 01 resume checks (2026-09-30)

| Command / check | Result |
|---|---|
| `git status --short --branch` | `main...origin/main` with the same 15 uncommitted files from the prior resume. They were reviewed and preserved; no additional unrelated edits appeared. Baseline HEAD remains `08a0edd4b674e8da62f6a6f5e65cdd53db1a086b`. |
| `python -m ruff format --check .`; `python -m ruff check .`; `python -m pytest -q -p no:cacheprovider` | Re-run PASS: 34 files formatted, Ruff checks pass, 3 tests pass. |
| `python docs/implementation/verify_prompt00.py`; `python -m compileall -q packages scripts tests`; `python scripts/check_boundaries.py`; `python scripts/smoke_workspace.py`; `python scripts/export_startup_schema.py --check` | Re-run PASS: all ledger/source hash counts, compile, boundaries, imports/config smoke and schema check pass. |
| `docker compose config --quiet`; `git diff --check`; parse source-manifest/progress JSON | Re-run PASS for Compose syntax, whitespace check and JSON parsing. Docker service startup remains unverified. |
| `uv lock` with workspace-local `UV_CACHE_DIR` | Reattempted; BLOCKED after three retries, now failing on `https://pypi.org/simple/pytest/` with proxy connection refused. No lockfile written. |
| `COREPACK_HOME=<workspace .cache>`, `npm_config_offline=false`, `corepack pnpm@12.5.1 install --lockfile-only --ignore-scripts` | Reattempted; BLOCKED fetching pnpm from `https://registry.npmjs.org/pnpm/-/pnpm-12.5.1.tgz`; configured proxy connection `127.0.0.1:9` refused. No lockfile written. |
| `python -c "import pydantic; print(pydantic.__version__)"`; `npm config get offline`; `Test-Path uv.lock`, `Test-Path pnpm-lock.yaml`, `Test-Path node_modules`, `Test-Path apps/web/node_modules` | Local Pydantic is 2.13.4, not the declared 2.13.5; npm offline setting is `true`; all four lock/install paths are absent. |

No durable model/job/upload/release identity exists to retry; the persisted state and workspace contain no such action. No fresh provider or external write was initiated.

No Node package install/typecheck/lint/build, complete locked CI run, PostgreSQL connectivity check, container startup, model call, paid work, upload, or release action was performed.


## Auxiliary R1 completion checks (2026-09-30)

| Command / check | Result |
|---|---|
| `uv lock` | PASS: generated `uv.lock` using the approved PyPI route. |
| `corepack pnpm@12.5.1 install --lockfile-only --ignore-scripts` | PASS: generated `pnpm-lock.yaml`; exact pnpm version comes from the workspace packageManager declaration. |
| `uv sync --locked --all-packages --group dev` | PASS: all ten workspace packages and locked dev dependencies synchronized. |
| `corepack pnpm install --frozen-lockfile --store-dir .cache/pnpm-store` | PASS: supply-chain policy passed for 402 lock entries; 343 packages installed; approved `unrs-resolver` postinstall completed. Store was workspace-local because the system cache is outside the writable workspace. |
| `uv run --locked --all-packages --group dev ruff format --check .`; Ruff lint; `mypy packages/configuration/src scripts`; `pytest -p no:cacheprovider`; boundary, smoke, and schema checks | PASS: 34 files formatted, Ruff clean, mypy clean, 3 tests passed, 10 packages imported, boundary and schema checks passed. |
| `uv build --all-packages` | PASS: source distributions and wheels built for all 10 packages. |
| `pnpm --filter @polycodebench/web typecheck`; `pnpm --filter @polycodebench/web lint`; `pnpm --filter @polycodebench/web build` (Node 24.21.0) | PASS: typecheck clean, ESLint clean after naming the flat config export, optimized Next.js production build completed and prerendered `/` and `/_not-found`. |
| `docker buildx imagetools inspect` for Postgres 17.6 and SeaweedFS 4.48; `docker image inspect` of both pulled immutable references | PASS: registry index digests and pulled linux/amd64 identities matched the references in D-01-04. Image refs are approved for local development only; SeaweedFS signature provenance was not independently verified. |
| `docker compose config --quiet`; `docker compose up -d --wait`; `docker compose ps`; `docker compose exec -T postgres pg_isready -U polycodebench -d polycodebench` | PASS: Compose valid; both services healthy; PostgreSQL accepts readiness check on mapped host port 55432. |
| `python -c` HEAD request to `http://localhost:8333/` | PASS: HTTP 405 for HEAD confirms the S3 service endpoint is reachable (method not supported); SeaweedFS startup log reports mini components ready and bucket `pcb-local` created. |
| `python docs/implementation/verify_prompt00.py`; JSON parse of `progress.json`/`source-manifest.json`; `git diff --check` | PASS after ledger updates: requirements/work package/E2E/prompt/ticket counts and source hashes; valid JSON; no whitespace errors. |
| GitHub Actions hosted workflow | Not run remotely. Its constituent Python and web commands above were run locally; no remote CI action was dispatched. |

Earlier records in this file document the initial registry/engine blockers; the Auxiliary R1 results above supersede those environment observations. No benchmark workload, model/judge request, paid work, cloud provisioning, upload, or release occurred.
