# Local self-hosted website

This is the no-cloud, loopback-only development stack. It uses the repository's PostgreSQL and SeaweedFS containers plus Keycloak for a real local OIDC login. The UI release rows are generated `synthetic_internal` data used for development; they are not benchmark results. The sample submitter is verified only inside the local test realm. No provider endpoint is called and no model run is created by submitting a request.

Do not expose these containers to the LAN or internet. Keycloak runs in development mode over HTTP, and the generated reviewer token is a trusted local development identity rather than proof of production MFA. Do not reuse `.env` values in another environment.

## One-time local setup

Run from the repository root in PowerShell:

```powershell
uv run --locked --group dev python scripts/local_stack.py prepare
docker compose --profile local-auth up -d postgres object-store keycloak
uv run --locked --group dev python scripts/local_stack.py wait-for-keycloak
uv run --locked --group dev python scripts/local_stack.py bootstrap-db
uv run --locked --group dev python scripts/local_stack.py seed
```

`prepare` creates a gitignored `.env`, a Keycloak import file under `.cache/`, and a local-only identity fingerprint file. Passwords and signing keys are random and are not printed. The local submitter username and password are in `.env`; keep that file private. PostgreSQL is migrated to the current Alembic head and grants are applied. The API connects as a database role scoped to public reads, submission, and the local review API.

Keycloak's `polycodebench-local` realm uses the public `polycodebench-web` client with PKCE. The provider listens only on `127.0.0.1:8080`. Its data and `.env` must be retained together so the imported user's password and bootstrap administrator remain available across restarts.

## Run the API and website

In a terminal for the API:

```powershell
. .\scripts\load-local-env.ps1
uv run --locked --all-packages uvicorn polycodebench_api.app:app --host 127.0.0.1 --port 8010
```

In another terminal for the web app (Node 24 is required by `apps/web/package.json`):

```powershell
. .\scripts\load-local-env.ps1
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web exec next dev --hostname 127.0.0.1 --port 3001
```

With both servers running, verify the real local sign-in and submission path:

```powershell
npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web exec node tests/e2e/local-stack-auth.smoke.mjs
```

This browser smoke uses the generated local account, writes one synthetic metadata-only request per run, verifies the owner status path, reviewer queue and owner isolation, and saves local-only screenshots under `.cache/local-stack-browser/`. It does not contact the submitted provider URL or create a run.

Open `http://127.0.0.1:3001/leaderboard`. Sign-in at `/model-submissions` uses the local Keycloak account from `.env`. The API listens on `http://127.0.0.1:8010`; PostgreSQL and SeaweedFS remain on `127.0.0.1:55432` and `127.0.0.1:8333`.

To stop the containers, use `docker compose --profile local-auth down`. This does not delete the named database, object-store, or Keycloak volumes. Do not add `--volumes` unless you intentionally want to remove all local development data.

## Scope and deployment limits

The local stack validates public projections, OIDC submitter sessions, metadata-only request persistence, ownership checks, and reviewer API authorization. Approval still requires an explicit endpoint registration and finite reviewed run plan. This stack does not include production sandbox isolation, public DNS, or public HTTPS.

For a no-cloud setup, Docker Compose, PostgreSQL, SeaweedFS and Keycloak are open-source local components; no paid AWS account is needed. The local development API continues to read the explicitly synthetic SQLite fixture. Staging/production mode now requires a shared PostgreSQL public-release catalog, so multiple API tasks do not depend on a writable local file. A publisher mirrors a source SQLite publication only after the release signature and typed public document verify:

```powershell
uv run --locked --all-packages pcb-ops releases sync-publication `
  --store .local/ops-rehearsal/source/releases.db `
  --keyring .local/ops-rehearsal/source/keyring.json `
  --target staging:test-board
```

For a local rehearsal, load `.env` and point the CLI at its separately scoped publisher DSN:

```powershell
. .\scripts\load-local-env.ps1
$env:PCB_DATABASE_URL = $env:PCB_PUBLISHER_DATABASE_URL
uv run --locked --all-packages pcb-ops releases sync-publication `
  --store .local/ops-rehearsal/source/releases.db `
  --keyring .local/ops-rehearsal/source/keyring.json `
  --target local:test-board
```

The command copies only public release fields and publication timestamps; review, validation and approval records stay private. Replays are idempotent, published documents are immutable apart from a withdrawal notice, and the release pointer only advances. The Terraform task definition injects the API's database DSN and cursor key through Secrets Manager references. No live DNS, internet endpoint, cloud resource, or benchmark spend has been provisioned; an internet deployment still needs a host/account, a domain, HTTPS, and owner-approved operating limits.
