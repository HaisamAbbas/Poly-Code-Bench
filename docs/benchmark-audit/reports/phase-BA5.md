# Phase BA5 — Private access, evidence journeys and attestations

## Implemented functionality and changed files

Prompt 98 adds tenant-scoped private reads, plan/run API operations, the `pcb audit` command tree, schema-generated API types, and a reviewed allowlist boundary for public health projections. Prompt 99 adds public aggregate report lookup and health views plus an explicit private-curator access boundary. Prompt 100 adds canonical signed claims, Ed25519 verification, fresh trust/revocation checks, a lifecycle reducer and append-only repository methods, offline CLI verification, a public no-store route, and a certificate-style UI with correction links. See the [Prompt 98 report](prompt-98.md), [Prompt 99 report](prompt-99.md), and [Prompt 100 report](prompt-100.md). BA5 remains partial because approved signer/reviewer authority, trusted timestamp proof, shared curator authorization, live database execution, and reviewed source/report evidence are unavailable.

## Tests/commands actually run and results

- Prompt 98 combined API, CLI, auth, audit document/control/catalog, sealed, firewall, monitoring, health, OpenAPI and submission lifecycle suite: 117 passed.
- Ruff check, Ruff format check (13 Python paths), strict MyPy (8 source files), OpenAPI export, and generated API type check passed.
- Alembic reports one head (`b7c3e9a4d281`); upgrade and guarded downgrade SQL render offline. No database, source, model, signer, browser or human-review operation occurred.
- Prompt 99 web typecheck, ESLint, production build, four isolated Playwright scenarios and Ruff check/format on the Python fixture passed. Browser coverage is synthetic and uses an injected allowlisted health projection; it does not constitute a live reviewed projection or authorization check.
- Prompt 100 focused API/CLI/signing/lifecycle tests: 24 passed. Ruff and Mypy passed; OpenAPI export and generated API type check passed.
- Prompt 100 web typecheck, ESLint, production build, and three isolated Playwright scenarios passed. Browser fixtures use an ephemeral development key generated in memory, and include valid, tampered, revoked, superseded, and private-field cases.
- Prompt 100 does not establish trusted timestamp proof, real reviewer approval, live key revocation, a production trust-store publisher, or PostgreSQL execution. Its lifecycle adapter uses existing immutable `audit_event` storage but is not connected to a transition API.

## Acceptance gates satisfied, pending and blocked

| Gates | Status | Evidence and remaining work |
|---|---|---|
| BX-44 / BAT-16-A / BREQ-24 | Partial | Tenant/owner filtering, RBAC/MFA, bounded input, idempotency, pagination, ETags and safe errors are tested. Shared reviewer ACL, private artifact-byte authorization, transition writers and live PostgreSQL checks remain. |
| BX-45 / BAT-16-B/C | Partial | CLI help/exit paths and schema-derived Decimal/null/enum types are tested. Most review and lifecycle operations block until real service adapters exist. |
| BAT-16-D | Partial | Public health uses a strict allowlist and dry-run has no remote side effects. Reviewed live publication and browser journeys remain. |
| BX-46 / BAT-17-B | Partial | Aggregate public views show version, source window, scope/missingness, observed-risk counts and limitations, without a clean verdict or model-specific inference. Temporal context, prior exposure, trends, corrections, derived sets, attestations and live reviewed projections are not available. |
| BX-47 / BAT-17-A/C/D | Partial | Synthetic keyboard/mobile/loading/partial/not-found/blocked/privacy browser checks pass. Private actions remain absent without a shared curator ACL and authorized transition services; revoked-state handling and production access/log review are not represented by the current contracts. |
| BX-48 / BAT-18-A/B | Partial | Prompt 100: canonical Ed25519 claims, private document digest binding, public allowlist, local signing/verification, tamper checks and stale/offline qualification pass synthetic tests. Approved keys, reviewers, timestamp authority and live document verification remain unavailable. |
| BX-49 / BAT-18-C | Partial | Prompt 100: role-separated lifecycle reducer and tenant-scoped immutable append/read repository methods exist. No authenticated transition writer, key rotation/reissue service, live revocation feed or PostgreSQL execution is available. |
| BX-50 / BAT-18-D | Partial | Prompt 100: certificate-style UI discloses scope and limitations, hides claims after signature failure, separates report from task-text publication, and links a verified correction. The browser fixture is synthetic; reviewed live publication remains unavailable. |

## Decisions or specification discrepancies recorded

`ADDENDUM-DECISION-42` records tenant and owner-only object scope. `ADDENDUM-DECISION-43` records fail-closed unsupported operations and local-only dry-run. `ADDENDUM-DECISION-44` records the no-store public projection and no-request curator boundary. `ADDENDUM-DECISION-45` records the signed private-document digest and the absence of independent timestamp proof; `ADDENDUM-DECISION-46` requires fresh trust and lifecycle snapshots for current endorsement; `ADDENDUM-DECISION-47` binds correction events to persisted successor documents. No new specification discrepancy was found; the earlier §1 source-list discrepancy remains documented.

## Exact next command or numbered prompt

Prompt 101 / BWP-19 — actual benchmark pilot and detector calibration. Continue only with authorized immutable source snapshots, rights, independent labels, and bounded execution; keep unsupported source/model work blocked.
