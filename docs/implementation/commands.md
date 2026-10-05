# Verified command registry - Prompts 00-30

The registry preserves Prompt 00 inspection history and records commands actually verified in each later prompt. Planned commands are not listed as working.

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

## Prompt 07 checks (2026-09-30)

### Review fixes: final isolated candidate

All commands below ran from `.cache/p07-review-checkout`, with `PYTHONPATH` pointing to that snapshot's ten package source directories and the repository `.venv` tools. The snapshot excludes concurrent Prompt 08 code and reconstructs shared models with only Prompt 06/07 additions.

| Command actually run | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --tb=short` with `PCB_TEST_DATABASE_URL=<pcb_prompt07_review_test>`, local SeaweedFS credentials/endpoint and `PCB_TEST_DOCKER=1` | PASS: 124 passed, no skips, 28.60 seconds. Includes 20 actual PostgreSQL scheduler cases, 30 worker lifecycle tests, six operator command tests and actual local Docker cancellation/containment. |
| `alembic -c packages/persistence/alembic.ini upgrade head`; repeat upgrade; `check` | PASS: fresh local database through `8ac42e1d09bf`; previous Prompt 07 database upgraded from `7b8cc92d13ea`; no schema drift. |
| `mypy packages/core/src packages/configuration/src packages/persistence/src packages/services/src packages/orchestration/src packages/runner/src scripts` | PASS: 56 source files. |
| `mypy --follow-untyped-imports --follow-imports=silent packages/runner/src infra/sandbox/guest/pcb-guest-control.py tests/test_sandbox.py tests/test_worker.py` | PASS: six explicitly scoped files. The broad pass above checks the scheduler/persistence modules without suppressing their errors. |
| `ruff format --check .`; `ruff check .` | PASS: 107 files formatted, lint clean. |
| `uv build --offline --all-packages --out-dir <local review dist>`; `python -m compileall -q packages scripts`; `python -m polycodebench_orchestration.cli --help` and `reap` with local service configuration | PASS: source and wheel for all ten packages; compilation and live operator command checks. Periodic mode is covered by the retained CLI tests. |
| Package boundaries, smoke/import checks, startup/contract schema `--check`, pilot contracts and ledger/source verification | PASS: ten imports, 21 generated outputs and 14 REQ/24 WP/43 E2E/35 prompts/142 tickets and source hashes. |

The original Prompt 07 checks below are historical. Final review details are in `reports/prompt-07-review.md` and the `review_validation` object in the integration evidence. Hosted CI, production VM and full usage/release evidence remain unrun.

| Command / check | Result |
|---|---|
| `$env:UV_CACHE_DIR='.cache/uv'; $env:PCB_TEST_DATABASE_URL='<isolated local test DB>'; $env:PCB_MIGRATION_DATABASE_URL=$env:PCB_TEST_DATABASE_URL; .\.venv\Scripts\python.exe -m alembic -c packages/persistence/alembic.ini upgrade head` | PASS: applied durable scheduler revision `f17b6b04a237` and evaluation-cancellation revision `7b8cc92d13ea` to actual local PostgreSQL 17.6. The test database name is `pcb_prompt07_test`; credential-bearing values are not recorded. |
| `$env:UV_CACHE_DIR='.cache/uv'; $env:PCB_TEST_DATABASE_URL='<isolated local test DB>'; $env:PCB_MIGRATION_DATABASE_URL=$env:PCB_TEST_DATABASE_URL; .\.venv\Scripts\python.exe -m alembic -c packages/persistence/alembic.ini check` | PASS: no schema drift detected. |
| `Get-Content packages/persistence/sql/grant_permissions.sql -Raw | docker exec -i polycodebench-local-postgres-1 psql -v ON_ERROR_STOP=1 -U polycodebench -d pcb_prompt07_test` | PASS: applied idempotent scheduler table/column grants to the isolated database; the role-grant integration check confirms scheduler read/insert/finalize privileges and denied event deletion. |
## Prompt 10 checks actually run (2026-10-01)

All Python image and admission evidence is `development_sandbox` tier (local Docker), not a production worker.

| Command / check | Result |
|---|---|
| `python scripts/build_python_images.py` | PASS: both images rebuilt with `docker build --network none`; the script verified the installed set against each lock and failed on drift. Runtime `sha256:beb3dd62e10b…`, evaluator `sha256:dd801029a063…`; identities and the plugin allowlist rewritten. A rebuild changes digests, so all manifests were resealed afterwards. |
| `python scripts/python_task_tool.py seal-all` | PASS: all 12 pilot manifests resealed against the new image digests. |
| `python scripts/python_admit_all.py` | PASS: 12/12 packages, 21–24 checks each, every variant executed in the pinned images. Reports in `.protected/reports/`. |
| `python scripts/python_pilot_inventory.py --protected .protected/taskpacks/python-pilot --reports .protected/reports --output taskpacks/python-pilot/inventory.yaml` | PASS: 12 packages, 12 executable-admission-passed, clusters 12/12. |
| `python scripts/python_conformance.py --report docs/implementation/evidence/prompt-10-conformance.json` | PASS: 14/14 cases across all 7 categories. |
| `python -m pytest tests -q -p no:cacheprovider` | PASS: 346 passed, 136 skipped (Docker/PostgreSQL gated). |
| `$env:PCB_TEST_DOCKER=1; python -m pytest tests/test_python_conformance_docker.py tests/test_python_guest.py -q` | PASS: 2 Docker conformance/admission tests passed in 594s; guest POSIX tests remain covered in-image. |
## PCB-11-1 checks actually run (2026-10-01)

Rust evidence is `development_sandbox` tier (local Docker). Two steps touch the network; every
other command runs offline.

| Command / check | Result |
|---|---|
| `python scripts/fetch_rust_components.py` | PASS. The one online step: builds the analyzer components image (clippy, rustfmt, pinned nightly Miri + rust-src) and the analyzer-free base image, and vendors the 1262 crate files Miri's sysroot resolves. |
| `python scripts/build_rust_images.py` | PASS. Builds the three recipes with `--network none`. `require_distinct()` verified the analyzers run only in the evaluator image, are absent from the other two, and the three digests differ. It rejected a first attempt at the runtime recipe that was not actually distinct. |
| `docker run --network none pcb-rust-<recipe> bash probe.sh` | PASS. Confirmed by execution (not `command -v`, which finds rustup shims in every image): runtime = rustc/cargo only; performance = rustc/cargo plus a baked `[profile.release]`; evaluator = plus clippy, rustfmt and Miri. |
| `pytest tests/test_rust_locks.py` | PASS, 8 tests: real cargo locks parse; the digest tracks the resolution and ignores formatting; a real dependency change moves the identity; an unpinned lock is rejected; malformed locks are rejected; the lock digest reaches `ToolIdentity.lock_digest`; the recorded identity describes three distinct recipes. |
| `pytest tests -q -p no:cacheprovider` (excluding Docker/PostgreSQL-gated modules) | PASS: 323 passed, 66 skipped, 0 failed. |
| `mypy --disable-error-code=import-untyped plugins/languages/rust/src scripts/build_rust_images.py scripts/fetch_rust_components.py` | PASS: 9 source files, strict, no issues. Rust guest scripts are covered by a documented `ignore_errors` override, as for Python (D-11-07). |
| `python -m ruff format --check .`; `python -m ruff check .` | PASS. |
| `python scripts/check_boundaries.py` | PASS. `lang_rust` was added to the allowed dependency map with the same permissions as `lang_python`; without it the checker raised `KeyError`. |
| `python docs/implementation/verify_prompt00.py` | PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00–34, 142 PCB tickets, owners/evidence, progress, source hashes. |
| Online installation during a scored run | NOT USED by design: images are built `--network none` from the prebuilt components image, cargo source replacement points at the vendored crates, and plans declare `network: none`. |
| Docker/PostgreSQL-gated suites and live providers | NOT RUN: unchanged from earlier prompts and out of scope for this ticket. No cloud, registry or paid-provider action was taken. |
| `python -m mypy --disable-error-code=import-untyped packages/plugins-api/src packages/evaluation/src plugins/languages/python/src` | PASS: 26 source files, strict, no issues. Guest scripts are covered by a documented `ignore_errors` override (D-10-09). |
| `python -m ruff format --check .`; `python -m ruff check .` | PASS: 201 files formatted; lint clean. |
| `python scripts/check_boundaries.py` | PASS. |
| `python docs/implementation/verify_prompt00.py` | PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00–34, 142 PCB tickets, owners/evidence, progress and source hashes. |
| Network access during image build and during scored runs | NOT USED by design: the build runs with `--network none` from a hash-verified local wheelhouse and every plan declares `network: none`. |
| Production execution tier; registry publication; live model/judge endpoints | NOT RUN: owner-deferred Prompt 06 cloud gate and Prompt 14/17 judge/pilot work. No cloud, registry or paid-provider action was taken. |
| `uv run --locked --offline pytest -q -p no:cacheprovider --tb=short tests/test_jobs_postgres.py` | PASS, 7 passed; actual PostgreSQL 17.6, SeaweedFS 4.48 and Docker Linux engine 29.7.2. E2E-07/08 and development E2E-09 subcases passed. Exact setup keeps local database/object-store test variables and local-only credentials out of evidence files. |
| `uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short` | PASS, 75 passed in 24.35s with the same isolated local DB/object store and opt-in live Docker test enabled; see `docs/implementation/evidence/prompt-07-integration.json`. |
| `uv run --locked --offline --package polycodebench-orchestration pcb-scheduler --help`; `uv run --locked --offline --package polycodebench-orchestration pcb-scheduler reap --limit 5` | PASS: registered scheduler CLI help and actual PostgreSQL expired-lease reaper invocation; no expired items were reported in the operator invocation. |
| `uv run --locked --offline mypy packages/core/src/polycodebench_core packages/persistence/src/polycodebench_persistence packages/orchestration/src/polycodebench_orchestration`; `uv run --locked --offline python scripts/check_boundaries.py` | PASS: strict typing and package dependency boundaries. |
| `.\.venv\Scripts\ruff.exe check packages/core/src/polycodebench_core packages/persistence/src/polycodebench_persistence packages/orchestration/src/polycodebench_orchestration tests/test_jobs_postgres.py scripts/check_boundaries.py docs/implementation/verify_prompt00.py`; `.\.venv\Scripts\ruff.exe format --check packages/core/src/polycodebench_core packages/persistence/src/polycodebench_persistence packages/orchestration/src/polycodebench_orchestration tests/test_jobs_postgres.py scripts/check_boundaries.py docs/implementation/verify_prompt00.py` | PASS: lint clean; 37 files already formatted. |
| `uv build --offline --all-packages`; `uv sync --locked --offline --all-packages --group dev`; `\.venv\Scripts\python.exe -m compileall -q packages scripts tests`; `git diff --check` | PASS where reported in `docs/implementation/evidence/prompt-07-integration.json`; local locked dependency state only. |

Prompt 07 used only the local development Docker/SeaweedFS/PostgreSQL services. Hosted CI, production EC2, live model calls, paid work, upload, or release actions were not performed.

## Prompt 03 checks (2026-09-30)

| Command / check | Result |
|---|---|
| PostgreSQL 18.4 ephemeral UTF-8 cluster on `127.0.0.1:55434`; provision dedicated `pcb_prompt03_final2_test` DB and role groups | PASS: actual local PostgreSQL; Docker Desktop Linux engine was unavailable. A prior migration attempt exposed the cyclic FK missing from the database; migration corrected to add the FK after both tables exist. |
| `$env:UV_CACHE_DIR='.cache/uv'; $env:PCB_MIGRATION_DATABASE_URL='postgresql://postgres@127.0.0.1:55434/pcb_prompt03_final2_test'; uv run --locked alembic -c packages/persistence/alembic.ini upgrade head` | PASS on empty DB; 42 domain tables plus Alembic bookkeeping, guards/views, artifact/execution cyclic FK. |
| `psql ... -f packages/persistence/sql/provision_roles.sql`; `psql ... -f packages/persistence/sql/grant_permissions.sql` | PASS: no-login role groups provisioned; scoped grants and submitter/reviewer RLS applied. Exact local commands used the test DB URL above and `-v ON_ERROR_STOP=1`; no credential-bearing URL was emitted. |
| `uv run --locked alembic -c packages/persistence/alembic.ini upgrade head` (second invocation); `uv run --locked alembic -c packages/persistence/alembic.ini check` | PASS: repeat upgrade was a no-op; Alembic reported “No new upgrade operations detected.” |
| `$env:PCB_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55434/pcb_prompt03_final2_test'; uv run --locked pytest -q -p no:cacheprovider --tb=short tests/test_persistence_postgres.py` | PASS: 4 PostgreSQL integration tests (same-key concurrent replay, changed-body conflict, atomic rollback after injected attempt insert failure, immutable writes, DB grants/RLS, RBAC, identity/audit and optimistic version rejection). |
| `uv run --locked alembic -c packages/persistence/alembic.ini upgrade head` on initial pre-fix draft DB; `alembic check` | Initial check FAILED because the cyclic `artifact.producer_execution_id` FK was represented in metadata but absent from the actual migration. The migration was fixed; fresh empty DB migration and drift check above both passed. |
| `git diff --check`; `python -m json.tool docs/implementation/progress.json`; `python docs/implementation/verify_prompt00.py` | Run after ledger reconciliation below; PASS required before final report. |
| PostgreSQL 17.6 hosted CI job | Configured with the pinned development image and role setup/migration/drift/integration checks. Remote GitHub Actions was not run during this session; local actual DB evidence is PostgreSQL 18.4. |
| HTTP/API routes, all administrative permission routes/roles, live model/provider work, PG17 local Docker service | Not run: HTTP/application routes and provider endpoints do not yet exist; every E2E-25 route variant is explicitly pending; Docker Linux engine unavailable. |

### Final Prompt 03 verification results

| Command / check | Result |
|---|---|
| `uv run --locked --all-packages --group dev ruff format --check .`; `ruff check .` | PASS: all 60 files formatted; all lint checks passed. A migration-only E501 exception covers long generated constraint/embedded DDL lines. |
| `uv run --locked --all-packages --group dev mypy packages/core/src packages/configuration/src packages/persistence/src packages/services/src scripts` | PASS: 29 source files, no issues. Initial typing findings were fixed and the command rerun. |
| `uv run --locked --all-packages --group dev pytest -q -p no:cacheprovider` with `PCB_TEST_DATABASE_URL` pointing to the dedicated local PG18.4 test database | PASS: 18 tests total, including 4 PostgreSQL integration tests. |
| `uv run --locked python scripts/check_boundaries.py`; `uv run --locked python scripts/smoke_workspace.py`; startup and contract schema `--check` commands | PASS: package boundaries, ten imports/startup checks, startup schema and 15 generated contract outputs. |
| `uv run --locked alembic -c packages/persistence/alembic.ini upgrade head`; `uv run --locked alembic -c packages/persistence/alembic.ini check` | PASS on the migrated integration DB: repeat upgrade is a no-op and no schema drift detected. Empty-database upgrade had already passed before test rows were inserted. |
| `uv build --all-packages --out-dir .cache/prompt03-build` using approved PyPI access | PASS: source distributions and wheels for all 10 packages. An offline attempt and an ordinary sandboxed online attempt could not find/fetch the pinned `uv-build==0.12.15` backend; the authorized elevated PyPI retry passed. |
| `python -c` wheel listing for `.cache/prompt03-build/polycodebench_persistence-0.1.0-py3-none-any.whl` | PASS: packaged wheel includes Alembic env, template, and `5c9545180d80_initial_persistence_schema.py`. |
| `python docs/implementation/verify_prompt00.py`; `python -m json.tool docs/implementation/progress.json`; `git diff --check` | PASS: 14 requirements, 24 work packages, 43 scenarios, Prompts 00–34, 142 tickets with owners/evidence, valid progress JSON and clean whitespace. |


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

## Prompt 02 verification (2026-09-30)

| Command / check | Result |
|---|---|
| `uv sync --locked --all-packages --group dev --offline` | PASS: locked workspace and dev dependencies available locally; no network access required. |
| `\.venv\Scripts\python.exe -m ruff format --check .`; `\.venv\Scripts\python.exe -m ruff check .`; `\.venv\Scripts\python.exe -m mypy packages/core/src packages/configuration/src scripts`; `\.venv\Scripts\python.exe -m pytest -p no:cacheprovider`; `\.venv\Scripts\python.exe scripts/check_boundaries.py`; `\.venv\Scripts\python.exe scripts/smoke_workspace.py`; `\.venv\Scripts\python.exe scripts/export_startup_schema.py --check`; `\.venv\Scripts\python.exe scripts/export_contract_schemas.py --check` | PASS: 44 files formatted; Ruff clean; mypy clean for 13 files; all 14 tests passed; 10 packages imported; startup and contract schemas match their generators. |
| `python scripts/export_contract_schemas.py --check` | PASS: 15 generated JSON Schema/OpenAPI/TypeScript contract outputs match the authoritative models. |
| `python scripts/check_boundaries.py` | PASS: package dependency boundaries. |
| `corepack pnpm --filter @polycodebench/contracts typecheck`; `build`; `test:contracts` | PASS on Node 24.21.0 / pnpm 12.5.1: TypeScript typecheck and build; shared golden/invalid fixtures and 256 deterministic key-order property cases; shared seed/bundle digest, path, UTF-8, UUID and monotonic-time checks. |
| `uv --offline build --all-packages --out-dir .cache/prompt02-dist` | PASS: source distributions and wheels built for all 10 Python workspace packages. An initial online attempt retried against the unavailable package index and was interrupted; the offline build passed. |
| `python -m pytest tests/test_core_contracts.py -q -p no:cacheprovider` | PASS: 11 contract tests passed. A prior cache-enabled run emitted a Windows cache permission warning; it did not affect results, and final evidence used the no-cache-provider form. |
| `python docs/implementation/verify_prompt00.py`; parse `progress.json` and `source-manifest.json`; `git diff --check` | PASS after ledger updates: 14 requirements, 24 work packages, 43 E2E scenarios, 35 prompts and 142 tickets with owners; source hashes unchanged; valid JSON and clean diff. |
| Full application E2E, hosted GitHub Actions, provider/judge tests, production sandbox and browser tests | Not run: this prompt establishes contracts only; no application workflow/provider authorization or production sandbox is part of the available implementation. |

Earlier records in this file document the initial registry/engine blockers; the Auxiliary R1 results above supersede those environment observations. Prompts 01–03 ran no benchmark workload, model/judge request, paid work, cloud provisioning, upload or release. Prompt 04 uploaded only synthetic fixture bytes to the local SeaweedFS development service for integration tests; no production upload or publication occurred.

## Prompt 04 verified commands and results (2026-09-30)

| Command / check actually run | Result |
|---|---|
| `docker compose ps` | PASS with approved elevated Docker access: local PostgreSQL 17.6 healthy on host port 55432 and SeaweedFS 4.48 S3 endpoint on 8333. |
| `docker compose exec -T postgres createdb -U polycodebench pcb_prompt04_test7`; piped `provision_roles.sql` and `grant_permissions.sql` into local PostgreSQL | PASS: disposable `pcb_prompt04_test7` database provisioned, non-login groups created and grants applied. |
| `PCB_MIGRATION_DATABASE_URL=<local test URL> uv run --locked alembic -c packages/persistence/alembic.ini upgrade head`; repeated upgrade; `... alembic ... check` | PASS from an empty PostgreSQL 17.6 database; repeat upgrade no-op; no model/schema drift. An initial check found two implicit unique-constraint name mismatches; migration names were aligned, then the fresh-database upgrade and drift check passed. |
| `PCB_TEST_DATABASE_URL=<dedicated local test URL> PCB_OBJECT_STORE_ENDPOINT=http://127.0.0.1:8333 AWS_ACCESS_KEY_ID=<local development credential> AWS_SECRET_ACCESS_KEY=<local development credential> uv run --locked --all-packages --group dev pytest -q -p no:cacheprovider` | PASS: 21 tests, including three actual PostgreSQL + SeaweedFS artifact integration tests. They cover altered-size/digest/truncated upload rejection, duplicate upload/finalize replay, cross-visibility dedup separation, DB/app role denial, quota refusal, manifest cycles/scope, upload interruption recovery, reviewer-gated projection, 24-hour quota expiry and provisional/canonical orphan cleanup after the 30-day retention interval. Active in-flight artifact identities are protected from canonical orphan collection. Repeated the three artifact tests on the same DB: 3 passed. |
| `uv sync --locked --all-packages --group dev --offline` | PASS: all 42 locked packages already available and synchronized. |
| `ruff format --check .`; `ruff check .`; `MYPYPATH=packages/core/src mypy packages/core/src packages/configuration/src packages/persistence/src packages/services/src scripts` | PASS: 67 Python files formatted, lint clean, 34 source files type-check. |
| `scripts/check_boundaries.py`; `scripts/smoke_workspace.py`; `scripts/export_startup_schema.py --check`; `scripts/export_contract_schemas.py --check` | PASS: package boundaries, ten package imports/startup checks and all 15 generated schema/OpenAPI/shared TypeScript outputs. |
| `docker compose config --quiet` | PASS. |
| `uv build --all-packages --out-dir .cache/prompt04-build` | PASS: source and wheel built for all ten packages. Ordinary sandboxed network access could not fetch isolated `uv-build`; the retry through the already approved PyPI path succeeded. Persistence wheel listing confirmed `artifacts.py`, `object_store.py` and the Prompt 04 migration are packaged. |
| Hosted GitHub Actions; production S3/IAM policy validation; public API/page/download/export privacy probes | NOT RUN: hosted CI was not dispatched; no production object-store target/principals exist; those public routes are later prompts. Local emulator tests do not establish production policy behavior. |

## Prompt 04 review-fix verification (2026-09-30)

| Command / check actually run | Result |
|---|---|
| `.venv/Scripts/alembic.exe -c packages/persistence/alembic.ini upgrade head` against existing dedicated `pcb_prompt04_test7` PostgreSQL database | PASS: applied approval and declassification-guard revisions. |
| Updated `grant_permissions.sql` applied to the dedicated test database | PASS. |
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --tb=short` with local PostgreSQL 17.6 and SeaweedFS 4.48 test environment | PASS: 24 tests, including six artifact integration tests for concurrent finalization, exact approval, publication conflict and expiry retention. |
| `.venv/Scripts/ruff.exe check` and `format --check` on changed Python files; `.venv/Scripts/mypy.exe` on source packages and scripts | PASS: lint and formatting clean; 36 source files type checked. |
| `.venv/Scripts/alembic.exe -c packages/persistence/alembic.ini check` | PASS: no schema drift. |

## Prompt 05 verified commands and results (2026-09-30)

The following initial results are historical. The review-fix verification section below records the corrected implementation.

| Command / check actually run | Result |
|---|---|
| `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/pcb.py task validate taskpacks/admission-smoke --admission-profile admission-v1 --report docs/implementation/evidence/prompt-05-authored-fixture-admission-v2.json` | PASS with approved local Docker access. Five identical reference passes, intended faulty output mismatch, alternative pass; report records the digest-bound output contract, `local_fixture`, immutable image digest and isolation profile. |
| `uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider tests/test_task_packages.py tests/test_core_contracts.py` | PASS: 18 tests covering import, privacy, path, cutoff, split and contract behavior. |
| `$env:PCB_TEST_DATABASE_URL=<local disposable PostgreSQL 17.6 test database>; uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider tests/test_task_admission_postgres.py` | PASS: 1 PostgreSQL registration/task-set freeze/scored-split-denial integration test. Test setup seeds verified artifact registry records; Prompt 04 separately covers object bytes. |
| `$env:PCB_MIGRATION_DATABASE_URL=<same disposable PostgreSQL database>; uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev alembic -c packages/persistence/alembic.ini check` | PASS: no new upgrade operations detected. |
| `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/export_contract_schemas.py --check` | PASS: all 21 generated contract schema/OpenAPI/shared TypeScript files. |
| `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/validate_task_contracts.py`; `uv --cache-dir .cache/uv run --locked --offline --all-packages python scripts/check_boundaries.py` | PASS: pilot-contract consistency and package dependency boundaries. |
| `uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev ruff check packages/core/src/polycodebench_core/__init__.py packages/core/src/polycodebench_core/models.py packages/core/src/polycodebench_core/tasksets.py packages/persistence/src/polycodebench_persistence/tasks.py packages/services/src/polycodebench_services/task_packages.py packages/services/src/polycodebench_services/task_fixture_runner.py packages/services/src/polycodebench_services/tasks.py scripts/pcb.py scripts/export_contract_schemas.py scripts/validate_task_contracts.py tests/test_task_packages.py tests/test_task_admission_postgres.py tests/test_core_contracts.py`; the same paths with `ruff format --check` | PASS: Prompt 05 files lint-clean and formatted. |
| `$env:MYPYPATH='packages/core/src'; uv --cache-dir .cache/uv run --locked --offline --all-packages --group dev mypy packages/core/src packages/persistence/src packages/services/src scripts` | PASS: 42 source files type-checked. A broader all-tests mypy attempt is not a passing command; it reports existing typing issues in pre-existing test files and is noted in `reports/prompt-05.md`. |

## Prompt 05 review-fix verification (2026-09-30)

| Command / check actually run | Result |
|---|---|
| `.venv/Scripts/alembic.exe -c packages/persistence/alembic.ini upgrade head` and `check` with `PCB_MIGRATION_DATABASE_URL` set to the dedicated local `pcb_prompt04_test7` database | PASS: applied `e8c51d90ab73` to preserve complete task admission evidence; no schema drift. |
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --tb=short` with `PCB_TEST_DATABASE_URL`, local SeaweedFS endpoint/development credentials and `PCB_TEST_DOCKER=1` | PASS: final run 56 passed, no skips. Includes all seven database admission regressions and all seven runner/CLI checks, including an actual infinite-loop Docker timeout with no container left running. Earlier sandboxed runner tests were denied temporary-directory access; the approved elevated full runs passed (53 before the three CLI cases; 56 afterward). |
| `.venv/Scripts/python.exe scripts/pcb.py task validate taskpacks/admission-smoke --admission-profile admission-v1 --report docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json` | PASS with permitted local Docker access: five stable reference runs, intended faulty mismatch and alternative pass. Complete snapshot is bound to the report; digest `sha256:2c3e44a86daad9c6e37e17c63831e2622f514b9a64cc7484fcc760fb2a4cbe64`. |
| `.venv/Scripts/ruff.exe format --check .`; `.venv/Scripts/ruff.exe check .`; `MYPYPATH=packages/core/src .venv/Scripts/mypy.exe packages/core/src packages/configuration/src packages/persistence/src packages/services/src scripts` | PASS: 89 Python files formatted; lint clean; 45 source files type-checked. |
| `.venv/Scripts/python.exe scripts/export_contract_schemas.py` followed by `--check`; startup schema `--check`; `scripts/check_boundaries.py`; `scripts/validate_task_contracts.py`; `scripts/smoke_workspace.py` | PASS: regenerated and checked 21 contract outputs, startup schema consistency, package boundaries, pilot-contract consistency and ten-package smoke checks. |
| `corepack pnpm --filter @polycodebench/contracts typecheck`; `build`; `test:contracts` | PASS after permitted Corepack cache access: generated TypeScript compiles; shared golden/invalid contract fixtures and 256 property cases pass. Available Node 22.23.2 used locally; pinned Node 24 CI was not run. |
| Hosted CI; trusted production worker admission; production VM isolation | NOT RUN. Production admission and every non-fixture task set are explicitly blocked pending trusted worker evidence authority. |

## Prompt 06 verification (2026-09-30)

| Command / check actually run | Result |
|---|---|
| `$env:UV_CACHE_DIR='.cache/uv'; $env:PCB_TEST_DOCKER='1'; uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short tests/test_sandbox.py::test_live_docker_containment_and_cleanup` | PASS: 1 live development-tier Docker containment test in 9.19s on Docker Linux engine 29.7.2, pinned Python 3.12 slim digest. Covers stage/read/idempotent replay, network and metadata denial, no Docker socket, symlink snapshot denial, process/memory/swap/disk/time limits, cancellation, TTL orphan collection and verified destruction. Evidence: `docs/implementation/evidence/prompt-06-local-docker.json`. This is not production VM evidence. |
| `$env:UV_CACHE_DIR='.cache/uv'; uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short` | PASS: full workspace, 49 passed, 19 skipped in 3.24s. PostgreSQL cases skipped because `PCB_TEST_DATABASE_URL` was unset; live Docker cases are opt-in and were run separately. |
| `uv run --locked --offline --all-packages --group dev ruff format --check .`; same prefix `ruff check .` | PASS: 95 Python files formatted; lint clean. |
| `uv run --locked --offline --all-packages --group dev mypy --disable-error-code=import-untyped packages/core/src packages/configuration/src scripts`; `uv run --locked --offline --all-packages --group dev mypy --follow-untyped-imports packages/runner/src infra/sandbox/guest/pcb-guest-control.py tests/test_sandbox.py` | PASS: 17 and 5 source files respectively. |
| `uv run --locked --offline --all-packages python scripts/check_boundaries.py`; `scripts/smoke_workspace.py`; `scripts/export_contract_schemas.py --check`; `scripts/validate_task_contracts.py` | PASS: boundaries, ten-package import/config smoke, 21 generated outputs and pilot contracts. |
| `uv build --offline --all-packages`; `git diff --check` | PASS: source and wheel built for all ten Python packages; no whitespace errors (Git reported CRLF normalization warnings). |
| `Get-Command aws,terraform -ErrorAction SilentlyContinue` | Neither CLI is installed. No AWS target/account, supervisor principal, reviewed AMI or cloud spend limit is configured. `terraform validate/plan`, deployment and real EC2 E2E-05/06 are not run; no cloud calls were made. |
| `uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short tests/test_sandbox.py` | PASS: 11 passed, 1 skipped (opt-in live test). |
| `bash -n infra/sandbox/aws/guest/bootstrap-control.sh` | BLOCKED: Windows WSL Bash returned `E_ACCESSDENIED`. CI syntax check added; hosted CI not run. |

## Prompt 08 checks (2026-09-30)

All database/object-store commands ran against the local PostgreSQL 17.6 and SeaweedFS 4.48 containers from `compose.yaml`; the test database was the dedicated, freshly created `pcb_prompt08_test`. Credential-bearing values are local development values and are not recorded.

| Command / check | Result |
|---|---|
| `alembic -c packages/persistence/alembic.ini upgrade head` on an empty `pcb_prompt08_test`, then `alembic ... check` | PASS: ten revisions applied including `9d3a71c05e24`; no schema drift. Re-created from scratch after each migration edit. The migration's `downgrade()` refuses by design. |
| `psql ... < packages/persistence/sql/provision_roles.sql` then `< packages/persistence/sql/grant_permissions.sql` (with `ON_ERROR_STOP`) | PASS: adds the `pcb_model_gateway` group and its least-privilege grants. |
| `uv run --offline --locked --all-packages pytest -q -p no:cacheprovider tests/test_model_gateway_units.py tests/test_model_gateway_fixtures.py` | PASS: 73 + 8 tests (policy/SSRF matrix, capability matrix, cost bounds, adapter wire/parse, provider-shaped fixtures, transport against loopback sockets, conformance probes). |
| `PCB_TEST_DATABASE_URL=<test db> PCB_OBJECT_STORE_ENDPOINT=http://127.0.0.1:8333 AWS_ACCESS_KEY_ID=<local> AWS_SECRET_ACCESS_KEY=<local> uv run --offline --locked --all-packages pytest -q -p no:cacheprovider tests/test_model_gateway_postgres.py tests/test_model_gateway_review_regressions.py` | PASS: 27 + 18 tests (E2E-10/11/12, endpoint governance, secret handling, review regressions) with fixture transport faults. |
| Same environment plus `PCB_LIVE_LOCAL_URL=http://127.0.0.1:11434/v1 PCB_LIVE_LOCAL_MODEL=llama3.2:3b PCB_LIVE_EVIDENCE_DIR=docs/implementation/evidence`, `pytest tests/test_model_gateway_live_smoke.py -rs` | PASS for the local adapter against a real Ollama 0.34.4 (conformance: completion, usage counters, native tool call; budgeted gateway call settled; ledger consistent). OpenAI-compatible hosted, Anthropic and Google: SKIPPED, reported untested (no credentials configured). Evidence: `docs/implementation/evidence/prompt-08-live-smoke-local.json`. |
| Full suite: `uv run --offline --locked --all-packages pytest -q -p no:cacheprovider -rs` (same environment, no Docker/live opt-ins) | PASS: 247 passed, 7 skipped (Docker lifecycle opt-ins and live-smoke cases without configuration). |
| `uv run --offline --locked --all-packages ruff format --check .`; `ruff check .` | PASS. |
| `uv run --offline --locked --all-packages mypy packages/core/src packages/persistence/src/polycodebench_persistence/{endpoints,model_ledger,model_configs}.py packages/orchestration/src/polycodebench_orchestration/gateway packages/services/src/polycodebench_services/model_endpoints.py` | PASS: 33 source files, strict. |
| `uv run --offline --locked --all-packages mypy packages/configuration/src scripts` | FAIL (pre-existing, not from this prompt): `scripts/pcb.py` and `scripts/export_contract_schemas.py` import workspace packages that ship no `py.typed`; 11 `import-untyped`/`no-any-return` findings. |
| `uv run ... python scripts/check_boundaries.py`; `scripts/smoke_workspace.py`; `scripts/export_startup_schema.py --check`; `docs/implementation/verify_prompt00.py` | PASS. |
| `uv build --all-packages --offline --out-dir <scratch>` | PASS: all ten packages build; the wheel contains `polycodebench_orchestration/gateway/**`. |
| `pcb-model plan --config <cfg> --protocol <protocol> --tasks 10 --samples 3 --max-request-bytes 4000` | exit 0 with labeled estimate; exit 4 when the configuration has no price snapshot. |
| `pcb-model register ...` without / with `PCB_ROLES=administrator`; `pcb-model check <id>`; `pcb-model account <id>` | exit 3 (permission) / exit 0 pending registration; static check resolves DNS under the policy, reports the secret provisioned, `contacted_endpoint: false`; account summary shows zero ledger discrepancies. |
| `Get-Command`/environment-variable name scan for provider credentials (`OPENAI*`, `ANTHROPIC*`, `GOOGLE*`, `GEMINI*`, `PCBSECRET*`) | None present. Secret values were not read. A local Ollama on 127.0.0.1:11434 was used for the live local check. |

## Prompt 09 checks (2026-10-01)

Database and object-store commands ran against the local PostgreSQL 17.6 and SeaweedFS 4.48 containers; the dedicated test database was the freshly created `pcb_prompt09_test` (11 Alembic revisions, drift check clean, grants applied). Credential-bearing values are local development values and are not recorded. Docker tests use the pinned `python:3.12-slim` digest in the local engine (development isolation only).

| Command / check | Result |
|---|---|
| `alembic ... upgrade head` on an empty `pcb_prompt09_test`; `alembic ... check`; `psql ... < grant_permissions.sql` | PASS: 11 revisions including `b2f6d4a91c73`; no drift; grants applied. |
| `pytest -q tests/test_solve_core.py tests/test_solve_guest_helper.py` | PASS: 70 + 29 (one symlink case skipped on this Windows host): protocol/effective identity, tool schemas, context policy, 29 extraction fixtures, patch engine, path safety. |
| `PCB_TEST_DOCKER=1 pytest -q tests/test_solve_guest_docker.py` | PASS: 12: tools and process cleanup inside a real container. |
| `PCB_TEST_DATABASE_URL=<test db> PCB_OBJECT_STORE_ENDPOINT=http://127.0.0.1:8333 AWS_ACCESS_KEY_ID=<local> AWS_SECRET_ACCESS_KEY=<local> PCB_TEST_DOCKER=1 pytest -q tests/test_solve_sessions.py tests/test_solve_families.py tests/test_solve_loader.py` | PASS: 22 + 4 + 4 (E2E-13, E2E-14, recovery, budgets, families, loader/executor), FIXTURE model. |
| `PCB_TEST_DATABASE_URL=<test db> PCB_OBJECT_STORE_ENDPOINT=http://127.0.0.1:8333 AWS_ACCESS_KEY_ID=<local> AWS_SECRET_ACCESS_KEY=<local> PCB_TEST_DOCKER=1 pytest -q` | PASS: 390 passed, 5 skipped (4 live-provider smoke tests unconfigured, 1 symlink case on this Windows host). |
| `ruff format --check .`; `ruff check .` | PASS. |
| `mypy packages/core/src packages/persistence/src/polycodebench_persistence/solve_state.py packages/runner/src packages/orchestration/src/polycodebench_orchestration/{solve,gateway,worker.py} packages/services/src/polycodebench_services/{model_endpoints,rbac}.py` | PASS: 48 source files, strict (the guest helper is excluded by a documented mypy override: POSIX-only plain stdlib source). |
| `mypy packages/configuration/src scripts` | FAIL (pre-existing, unchanged): `scripts/` imports workspace packages without `py.typed`. |
| `python scripts/check_boundaries.py`; `scripts/smoke_workspace.py`; `scripts/export_startup_schema.py --check`; `docs/implementation/verify_prompt00.py` | PASS. |
| `uv build --all-packages --offline --out-dir <scratch>` | PASS: all workspace packages built. |
| `pcb-solve protocols` | Lists both installed protocols with digests, tools, budgets and ceilings. |
| `pcb-solve inspect <attempt-id>` | Exercised through `inspect_attempt` on the E2E-13 and E2E-14 attempts (tests assert its output); the CLI wrapper only adds environment wiring and was not run against a live stack. |

## Prompt 11 completion (PCB-11-2 and PCB-11-4)

| Command | Result |
|---|---|
| `python scripts/build_rust_images.py` (twice) | PASS. Rebuilt with the guest interpreter and a baked Miri sysroot; `require_distinct()` passes. Digests are in `config/images/rust-v1.json`. |
| `python scripts/record_rust_tool_fixtures.py` | PASS. 10 scenarios recorded from real sandbox runs into `tests/fixtures/rust_tool_output/`. |
| `pytest tests/test_rust_plugin.py tests/test_rust_parsers.py tests/test_rust_guest.py tests/test_rust_profile.py tests/test_rust_locks.py tests/test_rust_pilot_inventory.py` | PASS, 93 tests. |
| `PCB_TEST_DOCKER=1 pytest tests/test_rust_docker.py tests/test_plan_runner.py tests/test_sandbox.py` | PASS, 22 tests (real Docker, development tier). |
| `pytest tests/test_suite_admission.py tests/test_plan_runner.py tests/test_python_plugin.py tests/test_python_parsers.py tests/test_python_guest.py tests/test_task_packages_suite.py tests/test_sandbox.py` | PASS: 185 passed, 7 skipped (the Python side after the shared-contract and admission-engine changes). |
| `python scripts/rust_task_tool.py seal-all --check` | PASS, 12/12 sealed. |
| `python scripts/rust_admit_all.py` | PASS, 12/12 packages: executable admission passed, `quality_admission: pending`. |
| `python scripts/rust_pilot_inventory.py --protected .protected/taskpacks/rust-pilot --reports .protected/reports --output taskpacks/rust-pilot/inventory.yaml` | PASS: 12 packages, 12 executable-admission-passed. |
| `python scripts/rust_conformance.py --report docs/implementation/evidence/prompt-11-conformance.json` | PASS, 16/16 cases across the 7 categories (real Docker, development tier). |
| `ruff format --check .`; `ruff check .`; `check_boundaries.py`; `verify_prompt00.py` | PASS. |
| PostgreSQL-gated suites, cloud, registry and paid-provider actions | NOT RUN: out of scope; none was taken. |

## Prompt 12 completion (PCB-12-1 to PCB-12-4)

| Command | Result |
|---|---|
| `pytest tests/test_evaluator.py` | PASS: 4 offline normalization tests (cross-tool dedup, baseline relations, ambiguous mapping, family parsing). |
| `PCB_TEST_DOCKER=1 pytest tests/test_evaluator_docker.py tests/test_evaluator.py` | PASS: 8 tests, 118s. Real Docker: Python reference evaluation (gate pass, scenario credit 10000, property evidence, profile complete), Rust reference evaluation, E2E-17 disallowed-path and fake-success submissions, E2E-18 dedup/baseline-debt manifest. |
| `python scripts/python_conformance.py --report docs/implementation/evidence/prompt-12-python-conformance.json` | PASS: 14/14 cases, digest `sha256:ab2abace4e68334d5f4744ea3fb71a08ed88b958309d3bdf279dc823fa0acecf`. |
| `PYTHONPATH=plugins/languages/rust/src python scripts/rust_conformance.py --report docs/implementation/evidence/prompt-12-rust-conformance.json` | PASS: 16/16 cases across all 7 categories, including the Miri hang/unsupported variants. |
| `pytest tests -q -p no:cacheprovider` | PASS: 445 passed, 143 skipped (skips are the PostgreSQL/Docker opt-ins this host did not enable). |
| `ruff format --check packages/evaluation/src tests/test_evaluator.py tests/test_evaluator_docker.py`; `ruff check` same paths | PASS. |
| `mypy --disable-error-code=import-untyped packages/plugins-api/src packages/evaluation/src plugins/languages/python/src plugins/languages/rust/src` | No new errors in the Prompt 12 modules; remaining notes are the repo's pre-existing `import-untyped`/`unused-ignore` items in other files. |
| `python scripts/check_boundaries.py`; `python docs/implementation/verify_prompt00.py` | PASS (ledger counts unchanged: 14 REQ, 24 WP, 43 E2E, 35 prompts, 142 tickets). |
| Production worker tier, sealed hidden lane, curator/owner rights, paid providers | NOT RUN: out of scope for this prompt; none was taken. |

## Prompt 16 — Aggregation and local reviewed publication

| Command / check | Result |
|---|---|
| `.venv\Scripts\python.exe -m pytest tests/test_publication_aggregation.py tests/test_publication_reporting.py tests/test_publication_releases.py tests/test_releases.py tests/test_uncertainty.py -q -p no:cacheprovider --tb=short` | PASS: 23 synthetic/internal acceptance tests; see `evidence/prompt-16-acceptance.json`. |
| `.venv\Scripts\python.exe -m ruff format --check ...` and `.venv\Scripts\python.exe -m ruff check ...` for Prompt 16 files | PASS. |
| `$env:MYPYPATH='packages/core/src;packages/scoring/src;packages/publication/src'; .venv\Scripts\python.exe -m mypy packages/publication/src/polycodebench_publication` | PASS: 5 source files. |
| `.venv\Scripts\python.exe scripts/export_publication_schemas.py --check` | PASS: 9 current schemas. |
| `.venv\Scripts\python.exe scripts/check_boundaries.py`; `.venv\Scripts\python.exe scripts/pcb.py release --help`; `.venv\Scripts\python.exe docs/implementation/verify_prompt00.py`; `git diff --check` | PASS. |
| `uv build --package polycodebench-publication --offline --out-dir .cache/prompt16-dist` | PASS: source distribution and wheel built. |
| `uv lock; uv sync --all-packages --locked` | PASS after adding pinned Ed25519 signing dependency. |
| Public API, real benchmark result publication, cloud/production target | NOT RUN; no target or authorized reviewed benchmark release was provided. |


## Prompt 17 preflight

| Command / check | Result |
|---|---|
| `.venv\Scripts\python.exe scripts/python_pilot_inventory.py --protected .protected/taskpacks/python-pilot --reports .protected/reports --output taskpacks/python-pilot/inventory.yaml` | PASS: 12 packages, 12 executable-admission-passed; 0 fully admitted/frozen. |
| `.venv\Scripts\python.exe scripts/rust_pilot_inventory.py --protected .protected/taskpacks/rust-pilot --reports .protected/reports --output taskpacks/rust-pilot/inventory.yaml` | PASS: 12 packages, 12 executable-admission-passed; 0 fully admitted/frozen. |
| `.venv\Scripts\python.exe -m pytest tests/test_solve_core.py tests/test_solve_sessions.py tests/test_solve_families.py tests/test_model_gateway_units.py -q -p no:cacheprovider --tb=short` | PASS: 144 passed, 25 skipped; database/Docker integration cases skipped because `PCB_TEST_DATABASE_URL` is absent. |
| `$env:PCB_TEST_DOCKER='1'; .venv\Scripts\python.exe -m pytest tests/test_solve_sessions.py tests/test_solve_families.py -q -p no:cacheprovider --tb=short` | Exit 0: 1 passed, 25 skipped because `PCB_TEST_DATABASE_URL` is absent; a Windows WMI `0x8007000e` diagnostic appeared during import. Not integrated pilot evidence. |
| `.venv\Scripts\python.exe -m pytest tests/test_solve_core.py -q -p no:cacheprovider --tb=short` | PASS: 70 passed. |
| `.venv\Scripts\pcb-solve.exe protocols --directory config/protocols` | PASS: both single-shot and standard-agent protocol definitions and digests listed. |
| `.venv\Scripts\pcb-model.exe plan --help`; `.venv\Scripts\python.exe scripts/pcb.py --help` | PASS: model command is plan/check/registration/account tooling; operator `pcb` exposes task/taskset only, with no run-start command. |
| E2E-31 live two-model pilot | BLOCKED before dispatch: 144 expected, 0 completed, 0 model-failed, 144 infrastructure/pre-dispatch-blocked. No provider delivery, cloud action or public publication occurred. |
| Exact bounded planning invocation once resolved model configs exist | `.venv\Scripts\pcb-model.exe plan --config <resolved-model-config.json> --protocol docs/implementation/plans/single-shot-v1.json --tasks 24 --samples 3 --max-deliveries 3` (run once per model config; planning only, not dispatch). |
| Exact bounded dispatch invocation | UNAVAILABLE: no run-start CLI or HTTP route exists in this checkout. Do not substitute the planning invocation for execution. |

## Prompt 13 completion (PCB-13-1 to PCB-13-4)

| Command | Result |
|---|---|
| `pytest tests/test_efficiency.py tests/test_performance_plan.py` | PASS: 16 tests. Golden efficiency values (ratio 2 / memory 1.5 → 61.666667; ratio 1 / ratio 1 → 100.000000; ratio 1 / memory 2 → 70.000000), censored-timeout bounds, refusals for a missing component, side-invariant equality, speed-lane refusals (argv, environment key), hardware-class and reference-digest binding. |
| `PCB_TEST_DOCKER=1 pytest tests/test_performance_docker.py` | PASS: 4 tests in 620s, real containers in the pinned Python image. E2E-19 paired measurement on one reserved worker (24 retained iterations over 3 weighted scales, 9 same-input pairs, separate build timings, efficiency 100.000000 for a candidate identical to its reference), E2E-20 canary drift, instrumented-lane refusal before any guest is created, and the wrong-but-fast rejection. |
| `pytest tests/test_efficiency.py` after the scale-growth fix | PASS: 9 tests. |
| `ruff check` / `ruff format --check` on `packages/evaluation/src/polycodebench_evaluation/{performance,perfcontracts,efficiency,plan_runner}.py` and `tests/test_{efficiency,performance_plan,performance_docker}.py` | PASS. Scoped deliberately: a directory-wide `ruff --fix` also reordered imports in a concurrent session's untracked `judge_inputs.py`. |
| `python docs/implementation/verify_prompt00.py` | PASS (ledger counts unchanged). |
| Dedicated/homogeneous hardware, production worker, paid providers | NOT RUN: unavailable on this host; recorded as a blocked gate rather than worked around. Shared-CI timings are labelled `blocked_shared_ci` in every measurement manifest. |

Measured artifacts are written to `.cache/performance/*.json` by the test and copied to
`docs/implementation/evidence/prompt-13-*.json` for the ledger.

## Prompt 15 completion (PCB-15-1 to PCB-15-4)

All commands were run on this Windows host with `.venv\Scripts\python.exe` (Python 3.12.10),
`local_fixture` tier, no network, no paid provider and no task execution by the scorer.

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests/test_scoring_golden.py tests/test_scoring_properties.py tests/test_scoring_policy.py tests/test_scoring_replay.py -q -p no:cacheprovider` | PASS: 66 passed in 13.86s. This is the whole Prompt 15 scope: E2E-23 golden fixtures, the ?24.1 scoring properties, the policy/profile/ownership configurations and E2E-24 clean-process replay. |
| `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --ignore=tests/test_analyzer_contracts.py --ignore=tests/test_judge_cli.py --ignore=tests/test_judging_core.py --ignore=tests/test_judging_postgres.py --ignore=tests/test_judge_inputs.py --ignore=tests/test_python_plugin.py --ignore=tests/test_python_parsers.py --ignore=tests/test_rust_parsers.py --ignore=tests/test_publication_cli.py` | PASS: 488 passed, 147 skipped. The ignored modules are in-flight Prompt 13/14/16 work plus three modules confirmed failing on this tree with the Prompt 15 changes stashed; none is caused by this prompt. |
| `.venv/Scripts/python.exe -m mypy --strict packages/core/src packages/plugins-api/src packages/scoring/src packages/configuration/src` | PASS: no issues in 41 source files. Adding `py.typed` to core and plugins-api removed the `--disable-error-code=import-untyped` suppression the earlier prompts needed and surfaced two real typing defects in Prompt 14's `judge_contracts.py`/`judge_calibration.py`, fixed without behaviour change. |
| `.venv/Scripts/ruff.exe check packages/scoring tests/scoring_support.py tests/test_scoring_golden.py tests/test_scoring_properties.py tests/test_scoring_policy.py tests/test_scoring_replay.py scripts/hash_scoring_profile_source.py scripts/check_boundaries.py` | PASS. (A repository-wide `ruff check .` still reports 71 findings, all in the in-flight Prompt 14/16 modules and `tmp_probe/`; one pre-existing `UP012` remains in `judge_calibration.py`.) |
| `.venv/Scripts/ruff.exe format --check` on the same paths | PASS: 18 files already formatted. |
| `.venv/Scripts/python.exe scripts/check_boundaries.py` | PASS. `scoring -> plugins_api` is now declared in `ALLOWED`, because T ?14.1 names `FrozenTask` (a plugins-api type) as a scorer input. |
| `.venv/Scripts/python.exe scripts/hash_scoring_profile_source.py` | PASS: `config/scoring/pilot-v1.yaml` records the real sha256 of `config/languages/profiles-v1.yaml` (`sha256:55caf3f9...842f6`). `--write` recomputes it, so a profile edit cannot silently pass for an unchanged scoring input. |
| `.venv/Scripts/python.exe docs/implementation/verify_prompt00.py` | PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00-34, 142 PCB tickets, owners/evidence, progress and source hashes. |
| `uv lock --offline` then `uv sync --locked --offline --all-packages` | PASS. The lock gained `polycodebench-plugins-api` and `PyYAML==6.0.3` for the scoring package; no new distribution was fetched. |
| `uv run --locked --offline --package polycodebench-scoring pcb-score --help` | PASS: registers the `score` and `replay` subcommands. |
| `uv run --locked --offline --package polycodebench-scoring pcb-score score --policy .cache/scorer-cli-demo/policy.json --ownership ... --task ... --evidence ... --language-profile ... --output ... --explain` | PASS: printed the six-dimension chain and `total: 84.000000` with the full arithmetic (`3000bp*100.000000/10000 + 2000bp*75.000000/10000 + 1500bp*61.666667/10000 + 1500bp*85.000000/10000 + 1000bp*90.000000/10000 + 1000bp*80.000000/10000`). Security 75.000000 is the duplicate-high-finding fixture; efficiency 61.666667 is the time-ratio-2/memory-ratio-1.5 fixture. |
| `uv run --locked --offline --package polycodebench-scoring pcb-score replay ... --archived-outcome .cache/scorer-cli-demo/outcome.json` | PASS: `{"outcome_digest": "sha256:4f8b9eabcb03963fb7b93dc5321f54b80605d0ba980a90d22960e3a031d32133", "replayed": true}`. |
| `uv build --all-packages --offline --out-dir .cache/prompt15-build` | PASS: every workspace package built, including `polycodebench-scoring`. |
| Production worker tier, sealed hidden lane, live model/judge endpoints, human calibration | NOT RUN: unconfigured and unauthorized on this host. `config/scoring/pilot-v1.yaml` therefore stays `effective_for_scoring: false` / `calibration_status: pending`, and E2E-24's live-pilot replay variant stays with Prompt 17. |

Detailed evidence: `docs/implementation/evidence/prompt-15-scoring.json`. Decisions:
`docs/implementation/decisions/prompt-15.md`.


## Prompt 18 Track A checks (2026-10-02)

| Command / check | Result |
|---|---|
| `.venv\Scripts\pytest.exe tests/test_track_a.py tests/test_track_a_publication.py -q -p no:cacheprovider --tb=short` | PASS: 14 unit/CLI/release-draft integration tests. E2E-32/33 use synthetic internal findings/oracles; the publication test creates a local draft and verifies it remains unpublished. |
| `$env:PCB_TEST_DOCKER='1'; $env:PCB_WRITE_EVIDENCE='1'; .\.venv\Scripts\pytest.exe tests/test_track_a.py tests/test_track_a_docker.py -q -p no:cacheprovider --tb=short` | PASS: 13 tests in 102.56s, including authored Python/Rust pre-fix and mutation reproduction, passing references, and E2E-34 bad-patch grading in the development sandbox. The command ran before adding one CLI-only unit case; its two Docker paths are unchanged and retained evidence is under `docs/implementation/evidence/prompt-18-*.json`. |
| `.venv\Scripts\pytest.exe tests/test_publication_aggregation.py tests/test_publication_reporting.py tests/test_publication_releases.py tests/test_releases.py tests/test_uncertainty.py tests/test_publication_cli.py -q -p no:cacheprovider --tb=short` | PASS: 26 publication/aggregation regression tests. Unsafe projections are refused on draft creation and update. |
| `.venv\Scripts\ruff.exe format` / `ruff.exe check` on Track A evaluation, publication adapter/CLI/release store and related tests | PASS. |
| Mypy with workspace `MYPYPATH` on Track A evaluation and CLI (2 files), then publication Track A adapter/release CLI/store (3 files) | PASS: no issues. |
| `uv run --locked --package polycodebench-evaluation pcb-track-a --help`; `... pcb-track-a source-requirements`; `uv run --locked --package polycodebench-publication pcb-release track-a-draft --help` | PASS: real entrypoints resolve. The source command reports zero verified external security examples and `status: blocked`, as intended. |
| `uv lock --check` | PASS: lock remains consistent. |
| `uv sync --locked --all-packages --group dev` | BLOCKED by the in-flight JavaScript package: `polycodebench-lang-javascript` lacks `src/polycodebench_lang_javascript/__init__.py`; `uv run --package` installs each required Track A entrypoint independently. |
| `python scripts/check_boundaries.py` | BLOCKED by the shared checker raising `KeyError: 'lang_c'` for an existing language package whose owner is absent from its `ALLOWED` table; Track A's own strict mypy and package entrypoint checks pass. |

No model provider, human calibration, production worker, external source download or public release was used.

## Prompt 14 completion (PCB-14-1 to PCB-14-4)

Judge responses in every command below are FIXTURES replayed through the real gateway; the
database, object store, ledger and adjudication records are real. No judge model endpoint and no
human calibration label exists in this workspace, so `pcb-judge run` is refused by design.

| Command actually run | Result |
|---|---|
| `docker compose up -d`; `createdb pcb_prompt14_test`; `provision_roles.sql`; `grant_permissions.sql` (admin, `ON_ERROR_STOP=1`) | PASS: local PostgreSQL 17.6 on 55432 and SeaweedFS 4.48 on 8333; role groups and scoped grants applied before migration. |
| `alembic -c packages/persistence/alembic.ini upgrade head` then `check` on an empty `pcb_prompt14_test` | PASS: twelve revisions applied ending at `b9e04c7a1f38` (judge execution records); "No new upgrade operations detected" (no model/schema drift). The migration refuses to run if `judge_packet` already holds rows. |
| `pytest tests/test_judging_core.py` | PASS: 56 offline tests (frozen rubric/panel, packet blinding, 16 adversarial judge responses, three-vote averaging, recovery bound, disagreement triggers, adjudication and supersession, cohort versioning, calibration selection/metrics/blocked paths, plus eleven regression tests from the independent review). |
| `pytest tests/test_judging_postgres.py` with `PCB_TEST_DATABASE_URL`, `PCB_OBJECT_STORE_ENDPOINT` and local development credentials | PASS: 10 tests on real PostgreSQL/SeaweedFS through the real `ModelGateway` (endpoint approval, capability validation, cost reservation, settlement, three-turn ledger). E2E-21 mean `0.833333`; E2E-22 six retained deliveries, `infra_blocked` with two valid votes; reviewer override appends a result and preserves all votes; judge rows reject UPDATE/DELETE. |
| `PCB_TEST_EVIDENCE_DIR=docs/implementation/evidence pytest tests/test_judging_postgres.py` | PASS: wrote `prompt-14-e2e-21.json` and `prompt-14-e2e-22.json` (secret-free; judge response text never recorded). |
| `pytest tests/test_judge_cli.py` | PASS: 10 tests \u2014 rubric, blocked panel, packet build, vote validation accept/reject, blocked calibration report written to disk, unqualified label refused, missing database configuration refused, adjudication permission enforced, `result` refused without restricted-evidence read. |
| `pytest tests/test_judge_inputs.py` | PASS: 7 tests building packet input from a real `EvaluationEvidence` manifest produced by the Prompt 12 evaluator; gate results, candidate digest and baseline debt are excluded, candidate comments arrive as untrusted data. |
| `python -m polycodebench_orchestration.judge.cli calibration --packets <placeholder set> --report docs/implementation/evidence/prompt-14-calibration.json --notes ...` | PASS with exit code 4 (blocked): `status: blocked`, `exact_agreement_bp: null`, `promotion_target_met: null`, `disjointness: not_demonstrated`, missing inputs named. |
| `ruff check` / `ruff format --check` on the 19 Prompt 14 files | PASS: lint clean, all formatted. |
| `mypy` (strict) on the 12 new/changed Prompt 14 source files | PASS: no issues. Scoped to source, as every other prompt's command in this ledger does; including the test files surfaces environment errors of the same kind in the concurrently modified test files (`polycodebench_plugins_api` has no editable path in the shared venv after the parallel session re-synced it, so those imports are `import-untyped`). |
| `python scripts/check_judge_boundaries.py` | PASS: judge files respect core\u2192services\u2192persistence\u2192orchestration/evaluation boundaries. The repository-wide `scripts/check_boundaries.py` cannot run while the concurrent Prompt 13 session has plugin members without map entries (`KeyError: 'lang_c'`). |
| `python docs/implementation/verify_prompt00.py` | PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00\u201334, 142 PCB tickets with owners and evidence. |
| `pytest tests/test_judging_core.py tests/test_judging_postgres.py tests/test_judge_cli.py tests/test_judge_inputs.py` | PASS: 83 tests (56 offline, 10 PostgreSQL/object-store/gateway, 10 CLI, 7 evaluation handoff). |
| Live judge endpoint, real judge model, qualified human calibration labels, hosted CI | NOT RUN: no endpoint, credentials, price snapshot, reviewer roster or labels exist; nothing was fabricated or substituted. |
| Full-workspace `pytest` | NOT CLEAN, and not because of this prompt: the shared working tree contains a concurrent Prompt 13 session whose untracked `tests/test_analyzer_contracts.py` imports a helper that no longer exists in its own `identity.py`, which blocks whole-suite collection until that session finishes. An earlier scoped run reported 666 passed / 50 failed, with every failure in concurrently modified Prompt 13 files. |


## Prompt 23 - Java and language coverage audit

Java's components were seeded before the offline image build. The recipes themselves use no network;
the task admission command runs the pinned images through the shared development Docker sandbox.

```powershell
uv run python scripts/build_java_images.py --check
uv run python scripts/build_java_images.py
uv run python scripts/java_task_tool.py seal plugins/languages/java/fixtures/top-words
uv run python scripts/java_task_tool.py validate plugins/languages/java/fixtures/top-words
uv run python scripts/java_task_tool.py admit plugins/languages/java/fixtures/top-words --report docs/implementation/evidence/prompt-23-java-admission.json
uv run pytest -q tests/test_language_extension_audit.py tests/test_cpp_build_images.py tests/test_java_build_images.py tests/test_java_guest.py tests/test_java_plugin.py tests/test_java_taskspec.py tests/test_java_testparse.py tests/test_go_plugin.py tests/test_cpp_plugin.py tests/test_python_plugin.py tests/test_rust_plugin.py
uv run pytest -q tests/test_c_plugin.py
uv run pytest -q tests/test_cpp_profile.py tests/test_cpp_locks.py
uv run pytest -q tests/test_language_extension_audit.py
uv run ruff check scripts/build_cpp_images.py scripts/build_java_images.py scripts/fetch_java_components.py scripts/java_task_tool.py plugins/languages/java/src tests/test_cpp_build_images.py tests/test_java_build_images.py tests/test_java_guest.py tests/test_java_plugin.py tests/test_java_taskspec.py tests/test_java_testparse.py tests/test_language_extension_audit.py
uv run ruff check scripts/go_conformance.py tests/test_language_extension_audit.py
uv run mypy plugins/languages/java/src/polycodebench_lang_java
uv run python scripts/build_java_images.py --check
uv run python scripts/go_conformance.py --report docs/implementation/evidence/prompt-22-go-conformance.json
```

The complete task admission is deliberately rerun after any fixture byte changes because the report
binds the sealed package digest. Python/Rust Prompt 10/11 image evidence is reused only as historical
evidence; current shared contracts are checked locally. E2E-15/35 remain partial until TypeScript
has a task pack and C++ has current sandbox admission. Java performance remains unmeasured. The
corrected Go conformance report passes 18/18 in the development Docker sandbox; its digest is
`sha256:f0d700f87cc4992ef9a732a39889689987bf7f880589bd3872c599e8a36a32f6`. The previous 17/18
failure is preserved at `docs/implementation/evidence/prompt-22-go-conformance-before-newline-fix.json`.

## Go (Prompt 22)

```powershell
# Rebuild the three pinned recipes and re-record their identities and the allowlist entry.
.venv/Scripts/python.exe scripts/fetch_go_components.py   # only online step
.venv/Scripts/python.exe scripts/build_go_images.py

# Reseal the fixture manifest against the rebuilt digests, then validate it.
.venv/Scripts/python.exe scripts/go_task_tool.py seal plugins/languages/go/fixtures/top-words
.venv/Scripts/python.exe scripts/go_task_tool.py validate plugins/languages/go/fixtures/top-words

# Executable admission (six variants through the real supervisor).
.venv/Scripts/python.exe scripts/go_task_tool.py admit plugins/languages/go/fixtures/top-words `
    --report docs/implementation/evidence/prompt-22-go-admission.json

# Language conformance surface.
.venv/Scripts/python.exe scripts/go_conformance.py `
    --report docs/implementation/evidence/prompt-22-go-conformance.json

# Real-sandbox tests (opt-in).
$env:PCB_TEST_DOCKER = "1"
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_go_docker.py

# Offline suite.
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_go_plugin.py `
    tests/test_go_guest.py tests/test_go_profile.py tests/test_go_locks.py
```

`scripts/go_quick_check.py PACKAGE --variant NAME --check build,test,vet,staticcheck,gosec,gofmt,context,race`
is an authoring loop, not evidence: it stages one variant and prints what the tool said.


## Prompt 25 - Realistic repository tasks (CursorBench-inspired)

Repository-task packs are ordinary task packages plus an authoring contract. Sealing verifies the
import, the authoring cross-checks and the acceptance-contract digest; admission runs the hidden
acceptance inventory for the full variant matrix (five stable reference repetitions) through a
bounded local subprocess; the matrix grades every variant with deterministic fixture judge votes
through the real judge services and scores them with the frozen pilot policy.

```powershell
uv run python scripts/repo_task_tool.py digest taskpacks/repo-tasks/ini-interpolate
uv run python scripts/repo_task_tool.py seal taskpacks/repo-tasks/ini-interpolate
uv run python scripts/repo_task_tool.py seal taskpacks/repo-tasks/history-group
uv run python scripts/repo_task_tool.py admit taskpacks/repo-tasks/ini-interpolate --report docs/implementation/evidence/prompt-25-admission-ini-interpolate.json
uv run python scripts/repo_task_tool.py admit taskpacks/repo-tasks/history-group --report docs/implementation/evidence/prompt-25-admission-history-group.json
uv run python scripts/repo_task_tool.py matrix taskpacks/repo-tasks/ini-interpolate --report docs/implementation/evidence/prompt-25-matrix-ini-interpolate.json
uv run python scripts/repo_task_tool.py matrix taskpacks/repo-tasks/history-group --report docs/implementation/evidence/prompt-25-matrix-history-group.json
uv run pytest -q tests/test_repo_tasks.py tests/test_repo_task_grading.py tests/test_repo_task_admission.py
uv run pytest -q tests/test_evaluator.py tests/test_judge_inputs.py tests/test_judging_core.py tests/test_suite_admission.py tests/test_scoring_golden.py tests/test_scoring_policy.py tests/test_scoring_properties.py tests/test_scoring_replay.py
uv run ruff check packages/evaluation/src/polycodebench_evaluation packages/services/src/polycodebench_services/repo_tasks.py packages/scoring/src/polycodebench_scoring/judge_evidence.py scripts/repo_task_tool.py tests/repo_task_support.py tests/test_repo_tasks.py tests/test_repo_task_grading.py tests/test_repo_task_admission.py
uv run mypy packages/evaluation/src/polycodebench_evaluation/repo_task_grading.py packages/evaluation/src/polycodebench_evaluation/repo_task_admission.py packages/evaluation/src/polycodebench_evaluation/repo_task_conventions.py packages/services/src/polycodebench_services/repo_tasks.py packages/scoring/src/polycodebench_scoring/judge_evidence.py
```

Judge evidence in these runs is fixture-class (deterministic fixture votes through the real
build/parse/aggregate services); the judge panel is unprovisioned and no live judge call is made.
Execution evidence is `local_fixture` tier.


## Prompt 26 - Self-repair protocol, durable rounds and E2E-37

The repair protocol is pure core contracts plus one orchestration driver; rounds persist through
the standard alembic migration chain and the model gateway. Local infrastructure: the compose
PostgreSQL 17.6 (127.0.0.1:55432) with a dedicated test database, the SeaweedFS object store
(127.0.0.1:8333), the repo's role provisioning, and fixture model replies.

```powershell
docker exec -i polycodebench-local-postgres-1 psql -U polycodebench -d polycodebench_test -f - < packages/persistence/sql/provision_roles.sql
docker exec -i polycodebench-local-postgres-1 psql -U polycodebench -d polycodebench_test -f - < packages/persistence/sql/grant_permissions.sql
$env:PCB_MIGRATION_DATABASE_URL="postgresql+psycopg://polycodebench:local-development-only@127.0.0.1:55432/polycodebench_test"
uv run python -m alembic -c packages/persistence/alembic.ini upgrade head
uv run pytest -q tests/test_repair_contracts.py
$env:PCB_TEST_DATABASE_URL="postgresql+psycopg://polycodebench:local-development-only@127.0.0.1:55432/polycodebench_test"; $env:PCB_OBJECT_STORE_ENDPOINT="http://127.0.0.1:8333"; $env:AWS_ACCESS_KEY_ID="local-development-only"; $env:AWS_SECRET_ACCESS_KEY="local-development-only"
uv run pytest -q tests/test_repair_session.py tests/test_repair_state_postgres.py
uv run pytest -q tests/test_solve_sessions.py tests/test_persistence_postgres.py tests/test_solve_core.py tests/test_judging_core.py
uv run python -m alembic -c packages/persistence/alembic.ini downgrade -1
uv run python -m alembic -c packages/persistence/alembic.ini upgrade head
uv run ruff check packages/core/src/polycodebench_core/repair_contracts.py packages/core/src/polycodebench_core/repair_prompts.py packages/persistence/src/polycodebench_persistence/repair_state.py packages/orchestration/src/polycodebench_orchestration/repair tests/test_repair_contracts.py tests/test_repair_state_postgres.py tests/test_repair_session.py
uv run mypy packages/core/src/polycodebench_core/repair_contracts.py packages/core/src/polycodebench_core/repair_prompts.py packages/persistence/src/polycodebench_persistence/repair_state.py
```

Operational note: run `grant_permissions.sql` AFTER `alembic upgrade head` on a fresh database -
migrating first and granting second is what makes the least-privilege role tests pass.


## Prompt 27 - Repository Q&A as answer evaluation

Pure contracts plus one grading module; entailment judging runs through the real judge services
with fixture votes (the panel is unprovisioned like every other panel).

```powershell
uv run pytest -q tests/test_qa_contracts.py tests/test_qa_grading.py tests/test_qa_fixtures.py
uv run pytest -q tests/test_judging_core.py tests/test_judge_inputs.py tests/test_judge_cli.py tests/test_solve_core.py
uv run python scripts/export_contract_schemas.py --check
uv run ruff format --check packages/core/src/polycodebench_core/qa_contracts.py packages/core/src/polycodebench_core/qa_prompts.py packages/evaluation/src/polycodebench_evaluation/qa_grading.py tests/test_qa_contracts.py tests/test_qa_grading.py tests/test_qa_fixtures.py
uv run ruff check packages/core/src/polycodebench_core/qa_contracts.py packages/core/src/polycodebench_core/qa_prompts.py packages/evaluation/src/polycodebench_evaluation/qa_grading.py tests/test_qa_contracts.py tests/test_qa_grading.py tests/test_qa_fixtures.py
uv run mypy packages/core/src/polycodebench_core/qa_contracts.py packages/core/src/polycodebench_core/qa_prompts.py packages/evaluation/src/polycodebench_evaluation/qa_grading.py
```

Entailment evidence in these runs is fixture-class (deterministic fixture votes through the real
build/parse/aggregate services); no live judge call is made. Prediction-family variants remain
pending until Prompt 28.


## Prompt 30 - Release-backed public pages

The browser fixture creates `synthetic_internal` releases through the local draft, validate, review,
approve and publish lifecycle. Its measurements and confidence-interval strings are display fixtures,
not benchmark results. The supported web runtime is Node 24; this host's global Node 25 is outside
the repository engine range, so web commands below used a temporary Node 24.21.0 runtime.

```powershell
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web test:e2e
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web build
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web typecheck
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web lint
uv run --locked --group dev pytest -q tests/test_public_api_prompt30.py tests/test_public_api_projections.py tests/test_publication_releases.py -p no:cacheprovider
uv run --locked --group dev ruff check packages/api/src/polycodebench_api/app.py packages/api/src/polycodebench_api/dev_fixture.py packages/api/src/polycodebench_api/documents.py packages/api/src/polycodebench_api/envelope.py packages/api/src/polycodebench_api/public_routes.py packages/publication/src/polycodebench_publication/projections.py packages/publication/src/polycodebench_publication/projections_query.py packages/publication/src/polycodebench_publication/releases.py tests/test_public_api_prompt30.py
uv run --locked --group dev ruff format --check packages/api/src/polycodebench_api/app.py packages/api/src/polycodebench_api/dev_fixture.py packages/api/src/polycodebench_api/documents.py packages/api/src/polycodebench_api/envelope.py packages/api/src/polycodebench_api/public_routes.py packages/publication/src/polycodebench_publication/projections.py packages/publication/src/polycodebench_publication/projections_query.py packages/publication/src/polycodebench_publication/releases.py tests/test_public_api_prompt30.py
git diff --check
```

The E2E command passes four Prompt 30 browser cases. Playwright JSON results and six desktop/mobile/
profile/error screenshots are in `docs/implementation/evidence/prompt-30/`. E2E-39 and E2E-40 remain
partial overall: comparison/task/methodology, the seventh page and large-task-list/load variants
belong to Prompts 31/32.

## Prompt 31 - Public comparison and explanation workflows

Prompt 31's development releases are generated through the local publication lifecycle and marked
`synthetic_internal`. Authored values and interval endpoints exist only to test the UI; they are not
benchmark results. `test:e2e` is pinned to Prompt 30 cases, while Prompt 31 has an isolated config
and writes browser results/screenshots to its own evidence directory.

```powershell
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web test:e2e:prompt31
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web test:e2e
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web build
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web typecheck
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web lint
uv run --locked --group dev pytest -q tests/test_public_api_prompt30.py tests/test_public_api_prompt31.py tests/test_public_api_projections.py tests/test_publication_releases.py -p no:cacheprovider
uv run --locked --group dev ruff check packages/api/src/polycodebench_api/dev_fixture.py packages/api/src/polycodebench_api/public_routes.py packages/publication/src/polycodebench_publication/projections.py packages/publication/src/polycodebench_publication/projections_query.py packages/publication/src/polycodebench_publication/releases.py tests/test_public_api_prompt30.py tests/test_public_api_prompt31.py tests/test_public_api_projections.py
uv run --locked --group dev ruff format --check packages/api/src/polycodebench_api/dev_fixture.py packages/api/src/polycodebench_api/public_routes.py packages/publication/src/polycodebench_publication/projections.py packages/publication/src/polycodebench_publication/projections_query.py packages/publication/src/polycodebench_publication/releases.py tests/test_public_api_prompt30.py tests/test_public_api_prompt31.py tests/test_public_api_projections.py
git diff --check
```

Prompt 31's three browser cases pass, including the leaderboard → model → comparison → task →
scorecard journey, API/UI scalar equality, budget/protocol incompatibility, private-ID/export probes,
withdrawal/successor navigation, keyboard activation, lazy payloads, responsive viewports and the
64-task paginated list. Prompt 30 regression cases also pass 4/4. Current run artifacts are under
`docs/implementation/evidence/prompt-31/`. E2E-26 remains partial for binary artifact-download
routing and production IAM validation; E2E-40 submission-page variants remain Prompt 32 scope.

## Prompt 33 - Operations (verified commands)

Local stack: `docker compose up -d` (PostgreSQL 17.6 on 127.0.0.1:55432, SeaweedFS on 8333).
Terraform runs in `hashicorp/terraform:1.13`; nothing is applied.

```bash
# IaC (no credentials needed)
docker run --rm -v "$PWD/infra:/infra" -w /infra hashicorp/terraform:1.13 fmt -recursive -check
docker run --rm -v "$PWD/infra:/infra" -w /infra/terraform/environments/staging hashicorp/terraform:1.13 init -backend=false
docker run --rm -v "$PWD/infra:/infra" -w /infra/terraform/environments/staging hashicorp/terraform:1.13 validate
docker run --rm -v "$PWD/infra/observability/prometheus:/rules" --entrypoint promtool prom/prometheus:v3.5.0 test rules /rules/alerts.test.yaml
docker run --rm -v "$PWD/infra:/infra" aquasec/trivy:0.67.2 config /infra/terraform

# Manifests, migrations, alerts
uv run --offline --locked --all-packages pcb-ops env validate
uv run --offline --locked --all-packages pcb-ops doctor --profile staging      # exit 3: template
uv run --offline --locked --all-packages pcb-ops migrate check               # passes: exact FK action repairs are policy-checked
uv run --offline --locked --all-packages pcb-ops migrate rehearse --admin-url <admin dsn> --persistence-root <worktree>/packages/persistence
uv run --offline --locked --all-packages pcb-ops alerts check

# Recovery rehearsal (E2E-42 local variant)
uv run --offline --locked --all-packages python scripts/seed_ops_rehearsal.py --replace
uv run --offline --locked --all-packages pcb-ops backup create --out .local/ops-rehearsal/backup-<UTC>
uv run --offline --locked --all-packages pcb-ops restore rehearse --backup .local/ops-rehearsal/backup-<UTC> --evidence <file>

# Drills and load (E2E-43 local variants)
uv run --offline --locked --all-packages python scripts/ops_drills.py --evidence <file>
uv run --offline --locked --all-packages python scripts/ops_load_rehearsal.py --evidence <file>

# Tests (PCB_TEST_DATABASE_URL must name a *_test database migrated to the released revision)
uv run --offline --locked --all-packages pytest -p no:cacheprovider tests/test_operations_telemetry.py tests/test_operations_deployment.py tests/test_operations_postgres.py tests/test_operations_recovery_docker.py
```

Host note: on the Prompt 33 Windows workstation child Python processes intermittently exited with
`0xC000070A` before running project code; re-run the command (Alembic calls are retried for that
status only).
