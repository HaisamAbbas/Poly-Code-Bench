# Local self-hosted website

This is the no-cloud, loopback-only development stack. It uses the repository's PostgreSQL and SeaweedFS containers plus Keycloak for a real local OIDC login. The UI reads signed `synthetic_internal` release snapshots from PostgreSQL; they are authored display data, not benchmark results. The source release store, local signing key and public keyring stay under ignored `.cache/`. The sample submitter is verified only inside the local test realm. No provider endpoint is called and no model run is created by submitting a request.

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

`prepare` creates a gitignored `.env`, a Keycloak import file under `.cache/`, and a local-only identity fingerprint file. Passwords, object-store credentials, and signing keys are random and are not printed. The local submitter username and password are in `.env`; keep that file private. `bootstrap-db` migrates PostgreSQL, configures scoped application roles, and rotates the local admin role to the generated password. The API login receives `pcb_public_reader`, `pcb_submitter`, `pcb_submission_reviewer`, `pcb_submission_approver` and `pcb_endpoint_administrator`; it is not a member of the broad `pcb_reviewer`, `pcb_operator` or `pcb_administrator` groups. `seed` creates signed synthetic release documents, verifies them against the local keyring, and mirrors only the public snapshots into PostgreSQL using the separate publisher role. The API itself never receives that publisher credential.

If you run `prepare` again while the API is running, restart the API afterward. It loads the trusted local identity file at startup and does not watch for changes.

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

Local refresh on 2026-10-07 passed: all nine PostgreSQL release-backed pages, Keycloak login, reviewer authorization, owner isolation, keyboard access and mobile layout. The refreshed API also exposes the release-scoped artifact routes, with its catalog migration at `d4f082b91c33`. Sanitized evidence is in `docs/implementation/evidence/prompt-33/local-functional-stack-2026-10-07.json`; the browser screenshots remain in ignored `.cache/`.

Runtime refresh on 2026-10-08 passed against the current loopback stack: PostgreSQL is healthy at migration `2a62b6001aa1`; API health, readiness and release-list routes return 200; the nine-page browser smoke again passed OIDC sign-in, metadata-only submission, reviewer authorization, owner isolation, keyboard access and mobile layout; the PostgreSQL submission integration module passed all four tests; the scorer PostgreSQL repository module passed both tests against the migration-scoped disposable test database; the combined worker, grading, and scorer suite passed all 24 tests; and the PostgreSQL public-release catalog module passed both tests for idempotent sync, immutability, withdrawal, public API reads and scoped publisher access. The release responses and pages identify their contents as synthetic test data, not benchmark results. Current sanitized evidence is in `docs/implementation/evidence/prompt-33/local-functional-stack-2026-10-08.json`; screenshots stay local under ignored `.cache/`.

To run the PostgreSQL-backed submission/approval integration against the disposable local test database, set the opt-in test URLs from `.env` and run only that integration module:

```powershell
. .\scripts\load-local-env.ps1
$env:PCB_TEST_DATABASE_URL = $env:PCB_DATABASE_URL
$env:PCB_TEST_MIGRATION_DATABASE_URL = $env:PCB_MIGRATION_DATABASE_URL
uv run --locked --all-packages pytest tests/test_public_api_submissions_postgres.py
```

Run the in-memory API policy tests separately in a fresh shell without loading `.env`:

```powershell
uv run --locked --all-packages pytest tests/test_public_api_prompt32.py
```

The local operator-drill tests inspect Alembic state and use SeaweedFS, so give that test module the local migration URL and loopback object-store endpoint:

```powershell
. .\scripts\load-local-env.ps1
$env:PCB_TEST_DATABASE_URL = $env:PCB_MIGRATION_DATABASE_URL
$env:PCB_OBJECT_STORE_ENDPOINT = 'http://127.0.0.1:8333'
uv run --locked --all-packages pytest tests/test_operations_postgres.py
```

Open `http://127.0.0.1:3001/leaderboard`. Sign-in at `/model-submissions` uses the local Keycloak account from `.env`. The API listens on `http://127.0.0.1:8010`; the web app uses that port as its development fallback when `PCB_PUBLIC_API_URL` is unset. Loading `.env` is still required for OIDC sign-in. PostgreSQL and SeaweedFS remain on `127.0.0.1:55432` and `127.0.0.1:8333`.

## Optional local solve worker

The repository includes development-only solve and evaluator worker assemblies. The solve worker uses the frozen task runtime; the evaluator worker loads the frozen candidate and trusted task bundles, checks plugin plans against the plugin and image allowlists, and stores the evidence package as an internal artifact. It requires the local PostgreSQL and SeaweedFS services above; `bootstrap-db` provisions a `pcb_local_worker` login and finite artifact quotas. That local-only login combines scheduler, solve, evaluator, model-gateway and artifact-finalizer permissions. Production deployments must use separate service identities.

Registration is opt-in and does not claim work or contact a model endpoint:

```powershell
. .\scripts\load-local-env.ps1
$env:PCB_LOCAL_WORKER_SETUP_ENABLED = 'true'
uv run --locked --all-packages pcb-worker local-register
Remove-Item Env:PCB_LOCAL_WORKER_SETUP_ENABLED
```

The command prints the worker ID. The generated `.env` keeps both worker setup and dispatch disabled. Do not enable dispatch until a finite run has been approved, its endpoint and secret reference are deliberately configured, and you intend to make that model request. The owner's approved request status shows the resulting run ID. For a bounded local one-shot, verify that run's approved model/configuration, task scope and finite cap, then run `pcb-worker local-run --worker-id <worker-id> --run-id <owner-visible-run-id>` with `$env:PCB_WORKER_DISPATCH_ENABLED = 'true'` in that process. The repository claim is filtered by that run ID and stage `solve`; it never falls back to another run if the target has no eligible solve job. The command claims one job for the run. To keep polling only that run, add `--watch`; queue-wide processing also requires explicit `--watch` and no target. The lower-level `--job-id` filter remains available for a single exact job and cannot be combined with `--watch`. A local one-shot without `--job-id` or `--run-id` fails before database access. The sandbox image must already exist locally; the worker does not pull or install it. Local execution uses the network-disabled, read-only, non-root Docker sandbox and is development evidence only.

Grading capacity has a separate lane and queue. Register it explicitly with `pcb-worker local-grading-register`; setup still requires `PCB_LOCAL_WORKER_SETUP_ENABLED=true`. It registers the frozen scoring policy and emits its `policy_config_id` with the worker ID. To queue work, select a completed attempt and run `pcb-worker local-grading-enqueue --attempt-id <attempt-id> --policy-config-id <policy-config-id> --resource-class local-grading-small` with `PCB_LOCAL_GRADING_SCHEDULE_ENABLED=true` for that process. The transaction verifies a frozen internal file candidate and matching task/policy artifacts, then atomically creates one evaluation and one grading job; replays return the same IDs and cannot create another job. Enqueueing does not claim work. `pcb-worker local-grading-run --worker-id <worker-id> --job-id <evaluation-job-id>` can claim only that exact `evaluate` job, and dispatch must separately be enabled for that process. The evaluator starts one plan guest at a time, verifies it is destroyed before returning the capacity slot, and commits the private evidence artifact and gate transition atomically with the scheduler result. The language evaluator images must already exist locally; the worker does not pull images.

An operator may separately score one completed evaluation with `pcb-worker local-score-evaluation --evaluation-id <evaluation-id>` after `prepare` and `bootstrap-db` have provisioned the dedicated `pcb_local_scorer` login. The command requires `PCB_LOCAL_SCORING_ENABLED=true` for that process and reads only verified internal evaluation artifacts; it never dispatches candidate or model code. A replay of the same evaluation, scorer source and frozen evidence resolves to the same scorecard. The pilot policy and language profiles remain pending calibration, so a passing evaluation stays pending review; the command does not queue or publish a release.

The base Compose stack does not install or start a model server. The optional local Ollama smoke below is separate from Compose and does not create a run. The worker remains idle until an operator configures a concrete bounded run and explicitly enables dispatch.

## Optional local model smoke

Ollama is a self-hosted inference option; keep its listener on loopback. On 2026-10-06 this
workstation pulled [`qwen2.5-coder:1.5b`](https://ollama.com/library/qwen2.5-coder) from the
official Ollama library (986 MB, model tag digest `d7372fd82851`). Its
[Hugging Face model card](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct) identifies
Apache-2.0 licensing. The local
endpoint `http://127.0.0.1:11434/v1` passed the gateway's basic-completion and usage-counter
conformance probes. The smoke declares tool calls unsupported, pins the model context to 32K,
and passed a one-attempt planning check with a USD 0 provider-fee ceiling. Evidence:
`docs/implementation/evidence/prompt-33/local-ollama-smoke-2026-10-06.json`.

This only verifies local inference and endpoint accounting metadata. It did not submit task code,
create or process a benchmark attempt, or produce a score. The model is not quality-calibrated or
admitted for ranked releases; a zero provider-fee estimate excludes local electricity and hardware
costs. The endpoint registration and approval live only in this machine's development database.
No model server is exposed to the LAN or internet.

To stop the containers, use `docker compose --profile local-auth down`. This does not delete the named database, object-store, or Keycloak volumes. Do not add `--volumes` unless you intentionally want to remove all local development data.

## Scope and deployment limits

The local stack validates public projections, OIDC submitter sessions, metadata-only request persistence, ownership checks, reviewer API authorization, and bounded run/job creation. Owner status follows the persisted PostgreSQL run, attempt and job lifecycle from queued through running to its terminal state as the solve worker processes a job. A completed solve run is not evidence of an evaluation, score or release. Opt-in development solve and exact-job evaluator workers are available, but Compose does not start them. Evaluation scheduling is a separate operator action. Completed evaluations can now be scored through the dedicated local scorer role, but outputs stay internal while the policy and language profiles are pending calibration; publication processing is still a separate reviewed workflow. Execution requires a specifically approved endpoint, a provisioned secret reference where applicable, a finite reviewed run plan and a per-process dispatch opt-in. The local Ollama conformance endpoint is not quality-calibrated and must not be used for ranked releases. This stack does not include production sandbox isolation, public DNS, or public HTTPS.

For a no-cloud setup, Docker Compose, PostgreSQL, SeaweedFS and Keycloak are open-source local components; no paid AWS account is needed. The local API reads the signed, explicitly synthetic public release snapshots from PostgreSQL. SQLite is used only as the local source store that `seed` verifies and mirrors into PostgreSQL; the API does not read that source store. Staging/production mode also requires a shared PostgreSQL public-release catalog, so multiple API tasks do not depend on a writable local file. A publisher mirrors a source SQLite publication only after the release signature and typed public document verify:

```powershell
uv run --locked --all-packages pcb-ops releases sync-publication `
  --store .local/ops-rehearsal/source/releases.db `
  --keyring .local/ops-rehearsal/source/keyring.json `
  --target staging:test-board
```

To repeat the local catalog sync, load `.env` and point the CLI at its separately scoped publisher DSN:

```powershell
. .\scripts\load-local-env.ps1
$env:PCB_DATABASE_URL = $env:PCB_PUBLISHER_DATABASE_URL
uv run --locked --all-packages pcb-ops releases sync-publication `
  --store .cache/polycodebench-local-verified-release-store.sqlite3 `
  --keyring .cache/polycodebench-local-keyring.json `
  --target local:board
```

The command copies only public release fields and publication timestamps; review, validation and approval records stay private. Replays are idempotent, published documents are immutable apart from a withdrawal notice, and the release pointer only advances. The Terraform task definition injects the API's database DSN and cursor key through Secrets Manager references. No live DNS, internet endpoint, cloud resource, or benchmark spend has been provisioned; an internet deployment still needs a host/account, a domain, HTTPS, and owner-approved operating limits.
