# Prompt 98 — Private API, CLI, SDK and permission contracts

## Implemented functionality and changed files

- Added authenticated, tenant-filtered audit registry/document/run reads; private plan and blocked-by-default run creation; signed cursor pagination; ETags; strict bounded JSON; idempotency; safe correlated error envelopes; and allowlisted public health/report projections. No route accepts source URLs or dispatches scans, model calls, guest execution, signing, or publication.
- Bound audit documents to tenants with migration `b7c3e9a4d281`. Legacy rows remain nullable and are not visible to tenant APIs. Audit references must resolve within the same tenant. The default object policy grants owners only; deployments need an explicit reviewed ACL for shared reviewers.
- Added the `pcb audit` command tree with help and real exit codes. Registry, plan, run, status, and attestation reads have API mappings. Import remains local metadata-only; other review/temporal/seal/firewall/replacement/monitor/health/verification operations fail closed until an authorized adapter exists. Dry-run performs local validation and no network operation.
- Regenerated `packages/api/openapi.v1.json` and `apps/web/src/lib/generated-public-api.ts` from schema contracts. Decimal values remain strings, nullable reasons and enums remain typed, and public health is a separate read projection.
- Kept historical submission approval JSON stable by excluding absent optional audit fields. Changed implementation paths: `packages/api/README.md`, `packages/api/pyproject.toml`, `packages/api/openapi.v1.json`, `packages/api/src/polycodebench_api/{app.py,audit_cli.py,auth.py,benchmark_audit_access.py,benchmark_audit_routes.py,context.py,submission_routes.py}`, `packages/persistence/src/polycodebench_persistence/{benchmark_audit.py,models.py,migrations/versions/b7c3e9a4d281_audit_document_tenant_scope.py}`, `apps/web/src/lib/generated-public-api.ts`, and `tests/test_{benchmark_audit_api.py,benchmark_audit_cli.py,api_oidc_bff_auth.py}`.

## Tests/commands actually run and results

- `uv run pytest tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_api_oidc_bff_auth.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py tests/test_benchmark_audit_catalog.py tests/test_sealed_evaluations.py tests/test_benchmark_firewall.py tests/test_benchmark_monitoring.py tests/test_benchmark_health.py tests/test_public_api_openapi.py tests/test_public_api_prompt32.py -q` — passed, 117 tests in 9.30s. Local synthetic fixtures only; no live database, source, model, reviewer or signer work.
- `uv run ruff check packages/api/src/polycodebench_api/auth.py packages/api/src/polycodebench_api/context.py packages/api/src/polycodebench_api/benchmark_audit_access.py packages/api/src/polycodebench_api/benchmark_audit_routes.py packages/api/src/polycodebench_api/audit_cli.py packages/api/src/polycodebench_api/app.py packages/api/src/polycodebench_api/submission_routes.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/b7c3e9a4d281_audit_document_tenant_scope.py tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_api_oidc_bff_auth.py` — passed.
- `uv run ruff format --check packages/api/src/polycodebench_api/auth.py packages/api/src/polycodebench_api/context.py packages/api/src/polycodebench_api/benchmark_audit_access.py packages/api/src/polycodebench_api/benchmark_audit_routes.py packages/api/src/polycodebench_api/audit_cli.py packages/api/src/polycodebench_api/app.py packages/api/src/polycodebench_api/submission_routes.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/b7c3e9a4d281_audit_document_tenant_scope.py tests/test_benchmark_audit_api.py tests/test_benchmark_audit_cli.py tests/test_api_oidc_bff_auth.py` — passed, 13 files formatted.
- `uv run mypy --strict packages/api/src/polycodebench_api/auth.py packages/api/src/polycodebench_api/context.py packages/api/src/polycodebench_api/benchmark_audit_access.py packages/api/src/polycodebench_api/benchmark_audit_routes.py packages/api/src/polycodebench_api/audit_cli.py packages/api/src/polycodebench_api/app.py packages/persistence/src/polycodebench_persistence/benchmark_audit.py packages/persistence/src/polycodebench_persistence/models.py` — passed, 8 source files.
- `uv run python scripts/export_public_api_openapi.py` — passed, generated one REST OpenAPI snapshot.
- `corepack pnpm --filter @polycodebench/web api:types:check` — passed with openapi-typescript 7.13.0.
- `uv run --project packages/api pcb audit --help` — passed; all command families are listed in the installed CLI entry point.
- `uv run alembic -c packages/persistence/alembic.ini heads` — passed; `b7c3e9a4d281` is the only head.
- `$env:PCB_MIGRATION_DATABASE_URL='postgresql://offline:offline@127.0.0.1:5432/polycodebench'; uv run alembic -c packages/persistence/alembic.ini upgrade f67a3d91c4b2:head --sql` and the corresponding `downgrade b7c3e9a4d281:f67a3d91c4b2 --sql` — passed with a placeholder URL; no database connection or DDL execution occurred.
- `git diff --check` — passed.

## Acceptance gates satisfied, pending and blocked

- **Partial:** BX-44, BAT-16-A/B/D, BREQ-22 and BREQ-24. Role, tenant, owner/object, cursor, idempotency, input-size, no-redirect, no-scan, and public-projection boundaries have local checks. Plan/run creation remains non-dispatching.
- **Partial:** BX-45 and BAT-16-C. OpenAPI-derived types preserve Decimal strings, null reasons, enums and the available capability surface. CLI exit codes/help and supported API calls are tested.
- **Pending:** private artifact-byte authorization, shared reviewer ACL integration, authenticated audit transition writers for match/temporal/seal/firewall/replacement/monitor/health/attestation workflows, and live PostgreSQL authorization/idempotency checks. Existing artifact routes are not a private audit artifact API.
- **Blocked:** live source, model, guest, signer, public publication, browser and human-review evidence is unavailable. No external work was attempted. BREQ-25 remains partial because prior fenced query/checkpoint writers and live queue recovery are absent.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-42`: new API documents require a tenant UUID; legacy null-tenant rows stay inaccessible; references cannot cross tenants; owner-only access is the default until a reviewed shared ACL is injected.
- `ADDENDUM-DECISION-43`: unsupported CLI/API transitions fail closed with explicit exit codes instead of simulating success or bypassing the service authority. Dry-run is local-only.
- The API run parser rejects duplicate JSON object members. No new specification discrepancy was found. The previously recorded §1 source-list discrepancy remains (the spec refers to five source Markdown files but lists and hashes three).

## Exact next command or numbered prompt

Prompt 99 / BWP-17 — benchmark health dashboard and evidence journeys.
