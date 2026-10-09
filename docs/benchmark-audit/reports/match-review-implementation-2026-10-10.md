# Match review CLI implementation update

Date: 2026-10-10 (Asia/Karachi)

## Delivered

- `pcb audit matches review <match-evidence-document-id> --decision match-review.json --idempotency-key <key>` submits a strict reviewer opinion through the private API.
- The API requires a tenant-bound MFA principal with restricted-evidence read and adjudication permissions, plus installation object-ACL approval. The authenticated subject supplies reviewer identity; the server assigns opinion ID, sequence and time.
- Persistence stores the opinion JSON in the existing append-only `match_review` ledger. One transaction validates document/artifact references, locks the candidate, appends the opinion and updates the candidate-state projection. Idempotency keys bind to reviewer and candidate; history reads verify the full sequence and projection.
- `pcb audit matches history <match-evidence-document-id>` reads the MFA- and object-ACL-protected opinion ledger. Decision artifacts remain private or restricted.
- `pcb audit matches adjudicate <match-evidence-document-id> --decision match-adjudication.json --idempotency-key <key>` resolves an accepted/rejected conflict. The authenticated adjudicator must be independent of the candidate author and every reviewer. The server binds the adjudication to all conflicting opinion IDs.
- Migration `d9e4f0a2b631` (`c81a4d2e7f30` to `d9e4f0a2b631`) adds nullable opinion/idempotency columns, a shape check and a partial idempotency index. It reuses the immutable-row trigger and database grants established by the audit foundation; the existing audit-document kind constraint is unchanged.
- Migration `e1f2a3b4c5d6` adds a separate immutable adjudication row. The candidate projection retains the existing `accepted`/`rejected` states; the verified ledger reports `adjudicated` and downstream containment checks use the recorded adjudication decision and relation.

## Verification

- Focused match-review/adjudication, API, CLI, contract, migration-policy and operator-capability tests: **64 passed**.
- Ruff check/format and strict mypy pass for the changed core, API and persistence modules. OpenAPI snapshot check passes. Offline Alembic rendering succeeds through `e1f2a3b4c5d6`; migration-policy tests confirm expand-only behavior.
- Full Python suite: **1,690 passed, 219 skipped**, with one Starlette/httpx deprecation warning.
- No live database was contacted or migrated.

## Remaining match-review limits

The shared ACL and verified private artifact references still require deployment configuration. Historical rows without a strict opinion payload cannot be reconstructed and therefore fail closed in append/history operations. PostgreSQL integration against a migrated disposable database was not run. These commands record human decisions only; they do not launch source queries, model calls, guest execution, signing or publication.
