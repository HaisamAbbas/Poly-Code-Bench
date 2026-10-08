# Phase BA5 — Private access, evidence journeys and attestations

## Implemented functionality and changed files

Prompt 98 adds tenant-scoped private reads, plan/run API operations, the `pcb audit` command tree, schema-generated API types, and a reviewed allowlist boundary for public health projections. Prompt 99 adds public aggregate report lookup and health views plus an explicit private-curator access boundary. See the [Prompt 98 report](prompt-98.md) and [Prompt 99 report](prompt-99.md). Prompt 100 remains outstanding, and shared curator authorization plus live reviewed source/report evidence are unavailable, so BA5 is partial.

## Tests/commands actually run and results

- Prompt 98 combined API, CLI, auth, audit document/control/catalog, sealed, firewall, monitoring, health, OpenAPI and submission lifecycle suite: 117 passed.
- Ruff check, Ruff format check (13 Python paths), strict MyPy (8 source files), OpenAPI export, and generated API type check passed.
- Alembic reports one head (`b7c3e9a4d281`); upgrade and guarded downgrade SQL render offline. No database, source, model, signer, browser or human-review operation occurred.
- Prompt 99 web typecheck, ESLint, production build, four isolated Playwright scenarios and Ruff check/format on the Python fixture passed. Browser coverage is synthetic and uses an injected allowlisted health projection; it does not constitute a live reviewed projection or authorization check.

## Acceptance gates satisfied, pending and blocked

| Gates | Status | Evidence and remaining work |
|---|---|---|
| BX-44 / BAT-16-A / BREQ-24 | Partial | Tenant/owner filtering, RBAC/MFA, bounded input, idempotency, pagination, ETags and safe errors are tested. Shared reviewer ACL, private artifact-byte authorization, transition writers and live PostgreSQL checks remain. |
| BX-45 / BAT-16-B/C | Partial | CLI help/exit paths and schema-derived Decimal/null/enum types are tested. Most review and lifecycle operations block until real service adapters exist. |
| BAT-16-D | Partial | Public health uses a strict allowlist and dry-run has no remote side effects. Reviewed live publication and browser journeys remain. |
| BX-46 / BAT-17-B | Partial | Aggregate public views show version, source window, scope/missingness, observed-risk counts and limitations, without a clean verdict or model-specific inference. Temporal context, prior exposure, trends, corrections, derived sets, attestations and live reviewed projections are not available. |
| BX-47 / BAT-17-A/C/D | Partial | Synthetic keyboard/mobile/loading/partial/not-found/blocked/privacy browser checks pass. Private actions remain absent without a shared curator ACL and authorized transition services; revoked-state handling and production access/log review are not represented by the current contracts. |
| BX-48–50 / BWP-18 | Pending | Prompt 100: signed attestation payload, lifecycle, signer and public verification. No signer or reviewer credentials are configured. |

## Decisions or specification discrepancies recorded

`ADDENDUM-DECISION-42` records tenant and owner-only object scope. `ADDENDUM-DECISION-43` records fail-closed unsupported operations and local-only dry-run. `ADDENDUM-DECISION-44` records that the public UI reads only the no-store allowlisted aggregate and that the curator UI remains a no-request blocker until a reviewed shared ACL and transition services exist. No new specification discrepancy was found; the earlier §1 source-list discrepancy remains documented.

## Exact next command or numbered prompt

Prompt 100 / BWP-18 — signed audit attestations and public verification. Keep signing and publication blocked until reviewed signer, key custody and approval adapters exist.
