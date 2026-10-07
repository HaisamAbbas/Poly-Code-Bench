# Phase 7 (Operational hardening) — BLOCKED

Exit gate: "Staging deploy/recovery/isolation/withdrawal rehearsals have real evidence."

| Gate element | State | Evidence / cause |
|---|---|---|
| Production-shaped IaC, environment-separated | implemented; validated, never applied | `infra/terraform/`; `terraform validate` PASS for staging and production |
| Identity/policy enforces environment and tier | implemented; local tests pass | `polycodebench_core/deployment.py`, `modules/identity`; `tests/test_operations_deployment.py` |
| Clean staging deployment (E2E-43) | **blocked** | no authorized AWS account, cap or principals |
| Isolated restore, 10 scorecards, projection, timing (E2E-42) | local variant passed; staging **blocked** | `evidence/prompt-33/e2e-42-local-restore.json` |
| Orphan / outage / withdrawal / key rotation / drain drills | local variants passed; staging **blocked** | `evidence/prompt-33/e2e-43-local-drills.json`, `tests/test_operations_postgres.py`, gateway E2E-11/12 |
| Migration compatibility | local **PASS**; staging **blocked** | D-33-03 was fixed at `d8f971ea2b34`; the current local PostgreSQL database is at Alembic revision `d4f082b91c33`, which adds the approved public artifact catalog view. Earlier isolated PostgreSQL upgrade, downgrade and re-upgrade rehearsal passed. Staging rollout rehearsal still needs authorization. |
| Telemetry and required alerts | implemented; promtool-tested; delivery not exercised | `infra/observability/`, `polycodebench_core/telemetry.py` |
| Runbooks, retention/rights, rehearsal reports | implemented | `docs/operations/` |

The phase is not complete. Every element that can run without cloud access was implemented and verified locally. The phase gate itself requires staging evidence, which needs either the AWS inputs in `docs/operations/staging-execution-plan.md` §1 or confirmed Alibaba trial entitlements and a separate provider plan in `docs/operations/alibaba-trial-readiness.md`. Production-only steps were not executed. No benchmark results were published.

Prior phase states: Phases 2, 3 and 4 remain blocked. Prompt 32 has completed its local/test scope; the E2E-25 local six-role service/API matrix and E2E-26 local controlled artifact routes pass. The Phase 6 aggregate gate remains blocked on production artifact/IAM denial. E2E-40's local API load rehearsal passed (3,000 requests across 11 routes, 0 errors, 750/750 ETag revalidations); browser-rendered page load under declared load and the staging CDN/cached-latency measurement remain open. REST OpenAPI/client parity and local PostgreSQL submission approval integration are verified.

**Local application refresh (2026-10-07):** The API and web app are running on loopback with the latest route code. API health, artifact-route OpenAPI, leaderboard HTTP 200 and local Keycloak discovery pass. The documented browser smoke passes all nine PostgreSQL release-backed pages plus Keycloak sign-in, metadata-only submission, reviewer authorization, owner isolation, keyboard use and mobile layout. Evidence: `docs/implementation/evidence/prompt-33/local-functional-stack-2026-10-07.json`. Screenshots remain local under ignored `.cache/`; no provider URL was contacted or model run created.

Next: check the Alibaba trial console values recorded in `docs/operations/alibaba-trial-readiness.md`; if they do not cover the stack within an explicit cap and shutdown window, remain local. Then continue Prompt 34 — Perform the final integrated audit and repair pass.
