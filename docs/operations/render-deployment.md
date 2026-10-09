# Render deployment (public read-only site)

Topology: `pcb-web` (Next.js, `Dockerfile.web`) and `pcb-api` (FastAPI, `Dockerfile.api`) as Render
web services, plus the managed Postgres `pcb-db`. Evaluations (solve, grade, score) keep running on
your PC against the local stack. A finished, signed `live_exploratory` release is then published
into `pcb-db`; the API serves it from the Postgres catalog (`PCB_PUBLIC_RELEASE_BACKEND=postgres`).
No Keycloak, object store or workers exist on Render, so sign-in and model submissions are off.

Files: `render.yaml`, `scripts/render/bootstrap_remote.py`, `scripts/render/publish_remote.py`.

## What was verified locally

Both images built with the exact Render arguments and ran against a throwaway PostgreSQL 17.
`/readyz`, `/v1/leaderboard`, `/v1/releases`, `/v1/tasks` answered 200 from the API container. The
web container returned 200 for `/`, `/leaderboard`, `/tasks`, `/methodology/v1` and rendered the
leaderboard from that API. Idle-after-load memory: API about 100-125 MB, web about 80-135 MB (limit
on small plans: 512 MB). Image sizes: API 388 MB, web 391 MB on disk.
Not verifiable offline: Render's build machines, the `onrender.com` URLs and Render's Postgres role
privileges. `bootstrap_remote.py` was tested with a superuser and with a `CREATEROLE`-only owner.

## Runtime requirements (from the code)

API (`PCB_ENVIRONMENT=production`, fail-closed): `PCB_PUBLIC_RELEASE_BACKEND=postgres`,
`PCB_DATABASE_URL`, `PCB_CURSOR_SIGNING_KEY` (64 hex), `PCB_WEB_AUTH_SIGNING_KEY` (>= 32 bytes),
`PCB_API_IDENTITY_JSON` (empty principal list). The object store is optional: public artifact
download is disabled when `PCB_OBJECT_STORE_ENDPOINT` is unset, and the API does not touch
Keycloak. The DB login role only needs `pcb_public_reader`.

Web: only `PCB_PUBLIC_API_URL`. OIDC is evaluated lazily (`oidcConfigured()`), so the site boots
and renders without any OIDC variable; `/auth/sign-in` answers 503 and `/model-submissions` shows
"not configured". Nothing in `apps/web` has to change.

`PCB_PUBLIC_API_URL` is baked into the web image at build time (Next.js rewrites for `/v1/*`), so
the API URL must be known before `pcb-web` builds. `render.yaml` uses the fixed name `pcb-api` and
`https://pcb-api.onrender.com/v1`. Render passes env vars to `docker build` as build ARGs.

## Owner steps

1. Create a Render account and connect GitHub. Push the repo (including the uncommitted work you
   want deployed: `render.yaml`, `.dockerignore`, `scripts/render/`) to the GitHub repository. This
   kit never commits or pushes for you.
2. Render dashboard, New, Blueprint, pick the repo, apply `render.yaml`. It asks for the two
   `sync: false` values (`PCB_DATABASE_URL`, `PCB_CURSOR_SIGNING_KEY`); enter a placeholder for now
   or leave the services failing until step 4. If `pcb-api` was not granted that exact name (the URL
   is not `https://pcb-api.onrender.com`), set `PCB_PUBLIC_API_URL` on `pcb-web` to
   `https://<real-api-host>/v1` and redeploy `pcb-web` (it rebuilds the image).
3. From the `pcb-db` page copy the **External** connection string. On your PC:
   ```
   export PCB_RENDER_DATABASE_URL='postgresql://pcb_owner:...@dpg-xxx.oregon-postgres.render.com/pcb'
   uv run --locked --all-packages python scripts/render/bootstrap_remote.py --verbose
   ```
   It creates the group roles, runs `alembic upgrade head`, applies the grants and creates the login
   roles `pcb_render_api` (reader) and `pcb_render_publisher`. It is idempotent and reuses
   credentials. If the Render owner can neither create roles nor is a superuser it stops with a
   message; the exact privileges Render grants could not be checked without an account.
4. Open `.local/render/render-secrets.env` (git-ignored, never printed). Set on `pcb-api`:
   `PCB_DATABASE_URL` (the value in the file, internal hostname) and `PCB_CURSOR_SIGNING_KEY`.
   Redeploy `pcb-api`; `/readyz` must turn green.
5. Publish after each local run:
   ```
   uv run --locked --all-packages python scripts/publish_live_release.py --run-id <uuid> --no-sync
   uv run --locked --all-packages python scripts/render/publish_remote.py --dry-run
   uv run --locked --all-packages python scripts/render/publish_remote.py
   ```
   `publish_live_release.py` builds and signs locally (`--no-sync` skips its local-DB mirror).
   `publish_remote.py` verifies every signature and digest, copies only public fields to
   `pcb-db` with the publisher role, and is safe to re-run. It refuses a non-`live_exploratory`
   current release unless you pass `--allow-synthetic`. Its `--target` (default `render:board`)
   must equal `PCB_PUBLICATION_TARGET` on `pcb-api`.
6. Custom domain later: add the domain on `pcb-web` (Settings, Custom Domains), create the CNAME at
   your DNS provider that Render shows, wait for the certificate. If the API gets a custom domain
   too, update `PCB_PUBLIC_API_URL` and redeploy `pcb-web`.
7. Tighten the database's allowed IPs to your own address once setup is done.

## Signing key and keyring

Releases are signed locally by key id `synthetic-local-fixture` (auto-generated by
`_persistent_fixture_signer`; private key `.cache/polycodebench-local-signing-key.pem`, public
keyring `.cache/polycodebench-local-keyring.json`). The Render API does **not** verify signatures
when it reads the Postgres catalog; verification happens in `publish_remote.py` against that local
keyring before anything is written. The public API does not expose release manifests, and the
signed manifest is stored in `pcb-db` (`public_release_document`). Nothing keyring-related has to be
configured on Render. Keep the private key and keyring safe: losing the key means future
releases are signed by a new key (the keyring rotates) and old ones remain verifiable only with the
old keyring. If you want outsiders to verify, they need both the signed manifest (not
exposed by the API today) and the keyring's public keys; neither is published by this kit. The key id is a local fixture name, not a production identity.

## Costs and limits (verify current pricing at render.com/pricing)

- Free web services sleep after about 15 minutes idle and take tens of seconds to wake; free
  instance hours are limited per month. The free Postgres has a 256 MB-class instance, small disk
  and is **deleted after about 30 days** unless upgraded. Re-run steps 3-5 on a new database, or move
  to a paid plan, before that date. Paid plans start at a few USD per month per service.
- Free docker builds may be slow or run out of memory on `next build`; upgrade the build plan or
  build with a paid instance if that happens.
- No sign-in, submissions, workers, artifact downloads or Keycloak initially.
- `.dockerignore` re-includes `apps/web/tests/e2e/browser-artifacts.ts`, because `next build`
  type-checks Playwright configs that import it; without it `docker build` fails.
