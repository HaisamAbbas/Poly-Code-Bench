# Local development

## Pinned tools

- Python 3.12.10 (`.python-version`) and `uv` 0.12.17. Install `uv` from its official installer or a managed package source, then verify with `uv --version`.
- Node 24.21.0 (`.node-version`), Corepack, and pnpm 12.5.1 (`package.json`). Run `corepack pnpm --version` after enabling Corepack. Do not rely on a globally installed pnpm.
- Docker Engine with Compose v2 is required only for the optional local PostgreSQL 17.6 and MinIO services in `compose.yaml`.

## Bootstrap

From the repository root, run:

> These install commands intentionally require committed `uv.lock` and `pnpm-lock.yaml`. Prompt 01 could not generate them because registry access is blocked in the current environment; until they are present, locked installation is pending and is not reported as working.

```powershell
uv sync --locked --all-packages --group dev
corepack pnpm install --frozen-lockfile
```

Then copy `.env.example` to `.env` for local configuration. It contains local-only references and must never contain real passwords or provider credentials. The current startup config smoke checks pass explicit mappings; process launch wiring is added in later prompts.

Start the local dependencies only when Docker Engine is available:

```powershell
docker compose up -d --wait
docker compose ps
```

Check the current locked workspace:

```powershell
uv run --locked --all-packages --group dev ruff format --check .
uv run --locked --all-packages --group dev ruff check .
uv run --locked --all-packages --group dev mypy packages/configuration/src scripts
uv run --locked --all-packages --group dev pytest -p no:cacheprovider
uv run --locked --all-packages --group dev python scripts/check_boundaries.py
uv run --locked --all-packages --group dev python scripts/smoke_workspace.py
corepack pnpm --filter @polycodebench/web typecheck
corepack pnpm --filter @polycodebench/web lint
corepack pnpm --filter @polycodebench/web build
```

These are repository commands; only commands listed as verified in `docs/implementation/commands.md` have been run successfully in this environment. Prompt 01 does not install a production driver, start paid model/judge calls, provision cloud services, execute benchmark tasks, or authorize release publication.
