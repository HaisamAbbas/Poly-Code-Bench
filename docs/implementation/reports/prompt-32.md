# Prompt 32 / Phase 6 — DONE (local/test tier; Phase 6 aggregate gate blocked)

## 1. Implemented functionality and changed files

- Added metadata-only model-submission requests bound to a verified account subject and email. Requests are rate-limited, idempotent, owner-scoped, and create no run, VM, endpoint contact or model call. Unknown fields such as provider credentials and run plans are rejected.
- Added MFA-protected reviewer/admin routes for request review, source permission decisions, endpoint registration and approval. Endpoint registrations accept a secret reference only. Approval requires an approved endpoint matching the submitted URL and binds source-rights review, model configuration, task/protocol identity, fixed cost/token bounds and at most 500 attempts to one versioned request.
- Added durable PostgreSQL submission storage and a reversible migration, while retaining the local SQLite fixture store. Approval calls the existing bounded/idempotent run service once; submitters receive only their own request and safe run status.
- Added the release-backed model-submission page and bounded same-origin API routes. The verified account token stays in page memory; the page never requests a provider secret. Added request, validation/error, pending, status-check and narrow-screen keyboard paths. The development release is identified as synthetic.
- Updated the shared mobile navigation wrap and its keyboard regression count. Main implementation is in `packages/api/src/polycodebench_api/{auth.py,submission_routes.py,submissions.py,postgres_submissions.py}`, `packages/persistence/src/polycodebench_persistence/migrations/versions/a20c4e619d32_reviewed_model_submissions.py`, `packages/services/src/polycodebench_services/{rbac.py,model_endpoints.py,runs.py}`, and `apps/web/src/{app/model-submissions/,app/api/model-submissions/,components/model-submission-form.tsx,lib/public-api.ts}`.

## 2. Tests and commands actually run

- `uv run --locked --group dev python -m pytest -q -p no:cacheprovider --tb=short tests/test_public_api_prompt30.py tests/test_public_api_prompt31.py tests/test_public_api_prompt32.py tests/test_public_api_projections.py tests/test_publication_releases.py` — PASS, 32 tests. Includes role/MFA separation, owner privacy, secret-schema rejection, rate limiting, idempotent bounded synthetic approval, run status progress, private/mixed DNS rejection and proof that an unapproved malicious endpoint is not contacted.
- `npm run test:e2e:prompt32` — PASS, 2/2 browser cases: leaderboard-to-submission navigation, pending creation/tracking, unauthenticated and foreign-owner denial, verified-email error, keyboard submit and 1440px/375px widths with no horizontal overflow. The page also asserts no request reaches the submitted provider host.
- `npm run test:e2e:prompt31` — PASS, 3/3 cases for exact comparison/task/evidence navigation, public privacy/export, keyboard/lazy payloads and paginated 64-task list.
- `npm run test:e2e` — PASS, 4/4 Prompt 30 regression cases for release/filter state, language/model profiles, answer-only N/A handling, keyboard operation and empty/error states.
- `npm run build` — PASS, optimized Next.js build includes `/model-submissions` and both bounded BFF routes. `npm run typecheck` and `npm run lint` — PASS.
- Scoped `ruff check` and `ruff format --check` — PASS for the 15 changed Python/test modules; `git diff --check` — PASS. Alembic reports one head, `a20c4e619d32`.
- PostgreSQL integration was not run: `PCB_TEST_DATABASE_URL` is unset. An offline Alembic SQL attempt also stops at the pre-existing Python data migration `b9e04c7a1f38`, which requires a live bind; no schema was applied. No live provider endpoint or spend was used.

## 3. Acceptance gates

- **PCB-32-1:** PASS at the local API/browser tier. Public requests use verified account identity, ownership checks, validated metadata, rate limits and distinct lifecycle states; creating a request cannot launch work.
- **PCB-32-2:** PASS at the local API/service tier. Reviewer/admin roles and MFA are enforced server-side. Private/mixed address checks, unapproved endpoint rejection, secret references and pinned finite approval plans are covered.
- **PCB-32-3:** PASS for the synthetic API lifecycle: repeated approval returns the original run, a changed plan conflicts, and only the owner sees progress. PostgreSQL-backed transaction, budget-ledger and audit integration remains unverified in this environment.
- **PCB-32-4:** PASS for page/build and browser scope. The submission page uses the release/API contract and works for request/error/pending/status flows. Prompt 30/31 regressions pass across the existing product pages.

## 4. Decisions, discrepancies and persisted state

- Public callers supply a short-lived verified-account token issued by the deployment identity provider. The API validates it against a mounted, expiry-checked identity-fingerprint file; the issuer and production OAuth/OIDC token flow are deployment inputs. Raw token values are not stored in that file.
- Provider credentials are provisioned out of band through secret references. Public request JSON cannot include secret values. Approval is administrator-only and cannot authorize unlimited future evaluations.
- The Prompt 32 browser account tokens and release data are test fixtures only. Synthetic releases are explicitly labeled; no live benchmark result, provider contact or spend is claimed.
- `PCB_TEST_DATABASE_URL` was unavailable, so PostgreSQL migration application and repository integration could not be verified. Offline migration generation cannot traverse the pre-existing Python data migration. Phase 6 also retains the open E2E-26 production artifact/IAM and E2E-40 declared-load cases, plus WP-21 generated-client parity.
- `docs/implementation/progress.json`, tickets, requirements, E2E matrix and phase map now record Prompt 32 as done at the local/test tier, Prompt 33 as next, and the Phase 6 aggregate gate as blocked.

## 5. Exact next command or numbered prompt

- Next: **Prompt 33 — Harden deployment and rehearse operations.** Resolve the recorded deployment/database prerequisites before claiming the Phase 6 aggregate gate is complete.

## Phase 6 aggregate gate — BLOCKED

E2E-39 passes through the public analysis and submission/status journeys. E2E-25 and E2E-41 pass locally but still need PostgreSQL submission/endpoint/run/audit integration. E2E-26 remains partial until binary artifact access and production IAM denial are exercised. E2E-40 page/accessibility/responsive cases pass, but its declared-load rehearsal remains open. WP-21 public routes and the typed UI client are exercised; generated-client parity is not independently verified. Production OAuth/OIDC identity issuance, provider secret provisioning, and cloud/IAM validation belong to deployment hardening.
