# Local website functionalization

This checklist tracks the owner-approved local path and staging preparation while no cloud account, verified free-tier quota, public hostname, or spend limit is configured. Public benchmark claims and live model calls stay disabled; local release data is explicitly synthetic.

## Work

- [x] Inspect the web/API/persistence/deployment path and confirm AWS is not available.
- [x] Bring up the real API against a migrated isolated PostgreSQL database; seed two `synthetic_internal` releases in a separate local release store.
- [x] Smoke-check all seven public analysis pages, the submission page, the task-content proxy, and the `/v1` rewrite against the running API.
- [x] Add repeatable local OIDC with Keycloak and validate the submitter login callback.
- [x] Exercise a metadata-only submission through the browser into PostgreSQL; verify owner isolation and that no model call or run is created.
- [x] Exercise submitter database grants, transaction-local row scope, idempotent retry and owner isolation against PostgreSQL.
- [x] Enqueue one durable solve job and creation event per attempt atomically with an approved bounded run, route it using the frozen task runtime resource class, and verify a matching scheduler worker can claim it. PostgreSQL approval recovery/replay creates no duplicate job and performs no provider request; synthetic-only evidence is in `docs/implementation/evidence/prompt-33/local-queue-routing-2026-10-06.json`.
- [x] Return persisted attempt and solve-job state counts on the owning submitter's status view; verify API role access, responsive browser rendering and synthetic-only evidence.
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
- [x] Make the production Terraform example bootstrap-safe: services default to zero, schedules are empty, and Terraform requires nonnegative integer counts plus resolved image digests before enabling a service.
- [x] Review the current official Alibaba Cloud and OCI free-tier rules; record that account eligibility, exact product quotas and a provider-specific deployment target are still unverified. No provider account or resource was touched.
- [ ] Build and configure the model/judge gateway, solve/evaluation supervisors, scorer and publisher work-processing runtimes. Approved bounded runs currently create durable queued jobs but are not executed locally; the loopback Ollama service has zero installed models (read-only check, no model call or download).
- [ ] Run the equivalent reaper smoke against staged PostgreSQL and complete staging E2E-42/43 before raising worker task counts.
- [ ] Select and verify a cloud account/region and its actual trial quotas, expiry, network/domain inputs and owner-approved maximum spend. Alibaba/OCI require a separate target; the current Terraform is AWS-specific. Do not provision resources until those limits are confirmed.

## Current local evidence

- API: `http://127.0.0.1:8010`, backed by `pcb_local_web_test` at migration `b390a26f17cd`.
- Web: `http://127.0.0.1:3001`; public routes and real Keycloak submission flow use the local API.
- Release store: `.cache/polycodebench-local-release-store.sqlite3`; contains two synthetic display releases, not benchmark results.
- Fresh 2026-10-06 smoke: API `/healthz` and web `/` returned HTTP 200; OIDC discovery matched the local issuer; `/v1/releases` returned 2 `synthetic_internal` releases and `/v1/leaderboard` returned 4 synthetic rows. `pcb-ops doctor --profile dev` passed; staging doctor correctly refused the template manifest. Sanitized evidence is in `docs/implementation/evidence/prompt-33/local-stack-health-2026-10-06.json`.
- Fresh production-shaped image check 2026-10-06: rebuilt the API and web images from the latest source; the API contract exposes owner-scoped run progress. Ran the web image read-only/non-root against the real local API, confirmed synthetic notices on leaderboard/comparison/tasks, keyboard focus, zero browser errors, and no horizontal overflow at 375/1440 px. Screenshots and sanitized image digests are in `docs/implementation/evidence/prompt-33/local-production-images-2026-10-06.json` and its sibling PNGs.
- Browser evidence: `.cache/local-stack-browser/`; local screenshots only, containing synthetic account and request data.
- Latest public API release: `7c7fffc5-308f-4837-af58-1d18ba752b22`, verified as a synthetic internal test release. Production-shaped web container rendered its synthetic-data notice, table, and release selector against the host API; keyboard focus and 375/1440 px overflow checks passed with no page errors.
- Browser suites: Prompt 30 4/4, Prompt 31 3/3, Prompt 32 2/2; refreshed reports/screenshots live under `docs/implementation/evidence/prompt-30/`, `prompt-31/`, and `prompt-32/`. These artifacts use test fixtures only.
- API image: read-only root, UID/GID 10001, `/healthz` returns 200. Without a database/release sync it correctly has no release rows; staging must inject PostgreSQL and sync a verified publication before web services start.
- Scheduler image: read-only root, UID/GID 10001; dev identity guard exports `local-development` and runs `reap --limit 100` against disposable local PostgreSQL. Sixteen PostgreSQL scheduler/operations integration cases passed with a separate temporary object store. Staging PostgreSQL remains untested; the scheduler only reaps expired leases and does not execute queued work. Sanitized evidence is in `docs/implementation/evidence/prompt-33/scheduler-disposable-postgres.json`.
- Both staging and production Terraform configurations validate locally; no plan/apply or provider API request was run.
- Production bootstrap guard: both Terraform roots validate in an isolated Linux container; all deployment tests pass 26/26. Four provider-free plan cases confirm disabled placeholders pass, enabling a placeholder is refused, a resolved image is allowed, and fractional counts are refused. The production example has eight always-on service roles at count zero and no schedules. Evidence is in `docs/implementation/evidence/prompt-33/production-bootstrap-safety.json`.
- Last submission DB check: 11 synthetic requests remained pending with no resulting run; `run` and `call_delivery` were empty, and the browser observed no provider endpoint request.
- Approved-run queue evidence: four submission PostgreSQL integration cases passed against a disposable PostgreSQL 16 database using separate API and migration roles. One bounded synthetic approval produced exactly one queued run, attempt, solve job, and creation event; the interrupted approval retry reused the run/job, and no provider endpoint was contacted. Evidence is in `docs/implementation/evidence/prompt-33/approved-run-queue-2026-10-06.json`.
- Approved-run progress evidence: the owner status response exposes persisted attempt/job states, while the UI renders each API state count without calculating a score. Five API tests, four disposable-PostgreSQL submission tests, and both Prompt 32 browser cases passed. Desktop and 375 px mobile approved-state screenshots are in `docs/implementation/evidence/prompt-32/`; the mobile page has no horizontal overflow.
- Free-cloud review: current Alibaba trial and OCI Always Free terms are summarized in `docs/operations/free-cloud-options-2026-10.md`; they do not establish the owner’s actual quotas or an immediately deployable target.
- Port 8000 is already owned by another local Uvicorn process and was left untouched.
- No cloud resource, real model-provider credential, verified Alibaba/OCI trial quota, or public hostname is configured. The AWS staging bootstrap remains disabled by default.
