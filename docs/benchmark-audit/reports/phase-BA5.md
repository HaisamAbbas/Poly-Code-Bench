# Phase BA5 — Private access, evidence journeys and attestations

## Implemented functionality and changed files

Prompt 98 adds tenant-scoped private reads, plan/run API operations, the `pcb audit` command tree, schema-generated API types, and a reviewed allowlist boundary for public health projections. See [Prompt 98 report](prompt-98.md). Prompts 99 and 100 remain outstanding, so BA5 is partial.

## Tests/commands actually run and results

- Prompt 98 combined API, CLI, auth, audit document/control/catalog, sealed, firewall, monitoring, health, OpenAPI and submission lifecycle suite: 117 passed.
- Ruff check, Ruff format check (13 Python paths), strict MyPy (8 source files), OpenAPI export, and generated API type check passed.
- Alembic reports one head (`b7c3e9a4d281`); upgrade and guarded downgrade SQL render offline. No database, source, model, signer, browser or human-review operation occurred.

## Acceptance gates satisfied, pending and blocked

| Gates | Status | Evidence and remaining work |
|---|---|---|
| BX-44 / BAT-16-A / BREQ-24 | Partial | Tenant/owner filtering, RBAC/MFA, bounded input, idempotency, pagination, ETags and safe errors are tested. Shared reviewer ACL, private artifact-byte authorization, transition writers and live PostgreSQL checks remain. |
| BX-45 / BAT-16-B/C | Partial | CLI help/exit paths and schema-derived Decimal/null/enum types are tested. Most review and lifecycle operations block until real service adapters exist. |
| BAT-16-D | Partial | Public health uses a strict allowlist and dry-run has no remote side effects. Reviewed live publication and browser journeys remain. |
| BX-46–50 | Pending | Prompts 99–100: dashboard/evidence journeys and signed attestation lifecycle. |

## Decisions or specification discrepancies recorded

`ADDENDUM-DECISION-42` records tenant and owner-only object scope. `ADDENDUM-DECISION-43` records fail-closed unsupported operations and local-only dry-run. No new specification discrepancy was found; the earlier §1 source-list discrepancy remains documented.

## Exact next command or numbered prompt

Prompt 99 / BWP-17 — benchmark health dashboard and evidence journeys.
