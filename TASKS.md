# Local website functionalization

This checklist tracks the owner-approved, local-only path after the AWS account/budget inputs were unavailable. Public benchmark claims and live model calls stay disabled; local release data is explicitly synthetic.

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
- [ ] Resolve the production release-projection storage path: `ReleaseStore` is file-backed SQLite while the AWS API service is horizontally scaled and the task definition has no shared release-store mount or loader. This is outside the local-only stack and must be addressed before a public deployment.

## Current local evidence

- API: `http://127.0.0.1:8010`, backed by `pcb_local_web_test` at migration `d8f971ea2b34`.
- Web: `http://127.0.0.1:3001`; public routes and real Keycloak submission flow use the local API.
- Release store: `.cache/polycodebench-local-release-store.sqlite3`; contains two synthetic display releases, not benchmark results.
- Browser evidence: `.cache/local-stack-browser/`; local screenshots only, containing synthetic account and request data.
- Last submission DB check: 11 synthetic requests remained pending with no resulting run; `run` and `call_delivery` were empty, and the browser observed no provider endpoint request.
- Port 8000 is already owned by another local Uvicorn process and was left untouched.
- No cloud account, paid resource, real provider credential, or public hostname is configured.
