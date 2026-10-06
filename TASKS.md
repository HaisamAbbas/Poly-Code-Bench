# Local website functionalization

This checklist tracks the owner-approved local path and staging preparation while no cloud account, verified free-tier quota, public hostname, or spend limit is configured. Public benchmark claims and live model calls stay disabled; local release data is explicitly synthetic.

## Work

- [x] Inspect the web/API/persistence/deployment path and confirm AWS is not available.
- [x] Bring up the real API against a migrated isolated PostgreSQL database; seed two `synthetic_internal` releases in a separate local release store.
- [x] Smoke-check all seven public analysis pages, the submission page, the task-content proxy, and the `/v1` rewrite against the running API.
- [x] Add repeatable local OIDC with Keycloak and validate the submitter login callback.
- [x] Exercise a metadata-only submission through the browser into PostgreSQL; verify owner isolation and that no model call or run is created.
- [x] Exercise submitter database grants, transaction-local row scope, idempotent retry and owner isolation against PostgreSQL.
- [x] Provide a safe local environment/bootstrap path for credentials, database migrations/grants, and synthetic release seeding.
- [x] Run browser coverage against the real local API/database/provider and preserve screenshots under `.cache/`.
- [x] Review local service exposure and distinguish local development from an internet deployment.
- [x] Use the shared PostgreSQL public-release catalog for API reads and verify the signed, typed, sanitized SQLite-to-PostgreSQL publication sync. The catalog replay/immutability/withdrawal and scoped publisher-role integration tests pass against the local PostgreSQL test database.
- [x] Build pinned production-shaped API and web images; verify read-only/non-root runtime, API health, and the web image against the actual local API release.
- [x] Prepare the AWS service path with private API discovery, OIDC/HMAC secret references, and disabled staging bootstrap services/schedules.
- [x] Run the Prompt 30/31/32 browser suites with isolated Next build directories and refreshed synthetic-only screenshots/results.
- [x] Export the identity guard's verified principal as `PCB_SERVICE_IDENTITY`, required for service audit attribution.
- [x] Build the scheduler lease-reaper image and verify its guarded CLI startup as a non-root, read-only container; it does not execute queued work.
- [x] Verify scheduler reaping against disposable local PostgreSQL: migrated a throwaway PostgreSQL 17.6 container, ran 16 scheduler/operations integration cases with a separate throwaway object store, and ran the guarded scheduler image against that database.
- [ ] Run the equivalent reaper smoke against staged PostgreSQL, and build the model/judge gateway, solve/evaluation supervisor, scorer and publisher work-processing runtimes; complete staging E2E-42/43 before raising their task counts.
- [ ] Select and verify a cloud account/region and its actual trial quotas, expiry, network/domain inputs and owner-approved maximum spend. Alibaba/OCI require a separate target; the current Terraform is AWS-specific. Do not provision resources until those limits are confirmed.

## Current local evidence

- API: `http://127.0.0.1:8010`, backed by `pcb_local_web_test` at migration `d8f971ea2b34`.
- Web: `http://127.0.0.1:3001`; public routes and real Keycloak submission flow use the local API.
- Release store: `.cache/polycodebench-local-release-store.sqlite3`; contains two synthetic display releases, not benchmark results.
- Browser evidence: `.cache/local-stack-browser/`; local screenshots only, containing synthetic account and request data.
- Latest public API release: `7c7fffc5-308f-4837-af58-1d18ba752b22`, verified as a synthetic internal test release. Production-shaped web container rendered its synthetic-data notice, table, and release selector against the host API; keyboard focus and 375/1440 px overflow checks passed with no page errors.
- Browser suites: Prompt 30 4/4, Prompt 31 3/3, Prompt 32 2/2; refreshed reports/screenshots live under `docs/implementation/evidence/prompt-30/`, `prompt-31/`, and `prompt-32/`. These artifacts use test fixtures only.
- API image: read-only root, UID/GID 10001, `/healthz` returns 200. Without a database/release sync it correctly has no release rows; staging must inject PostgreSQL and sync a verified publication before web services start.
- Scheduler image: read-only root, UID/GID 10001; dev identity guard exports `local-development` and runs `reap --limit 100` against disposable local PostgreSQL. Sixteen PostgreSQL scheduler/operations integration cases passed with a separate temporary object store. Staging PostgreSQL remains untested; the scheduler only reaps expired leases and does not execute queued work. Sanitized evidence is in `docs/implementation/evidence/prompt-33/scheduler-disposable-postgres.json`.
- Both staging and production Terraform configurations validate locally; no plan/apply or provider API request was run.
- Last submission DB check: 11 synthetic requests remained pending with no resulting run; `run` and `call_delivery` were empty, and the browser observed no provider endpoint request.
- Port 8000 is already owned by another local Uvicorn process and was left untouched.
- No cloud resource, real model-provider credential, verified Alibaba/OCI trial quota, or public hostname is configured. The AWS staging bootstrap remains disabled by default.
