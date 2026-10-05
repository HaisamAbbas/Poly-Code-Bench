# Phase 7 (Operational hardening) — BLOCKED

Exit gate: "Staging deploy/recovery/isolation/withdrawal rehearsals have real evidence."

| Gate element | State | Evidence / cause |
|---|---|---|
| Production-shaped IaC, environment-separated | implemented; validated, never applied | `infra/terraform/`; `terraform validate` PASS for staging and production |
| Identity/policy enforces environment and tier | implemented; local tests pass | `polycodebench_core/deployment.py`, `modules/identity`; `tests/test_operations_deployment.py` |
| Clean staging deployment (E2E-43) | **blocked** | no authorized AWS account, cap or principals |
| Isolated restore, 10 scorecards, projection, timing (E2E-42) | local variant passed; staging **blocked** | `evidence/prompt-33/e2e-42-local-restore.json` |
| Orphan / outage / withdrawal / key rotation / drain drills | local variants passed; staging **blocked** | `evidence/prompt-33/e2e-43-local-drills.json`, `tests/test_operations_postgres.py`, gateway E2E-11/12 |
| Migration compatibility | local **PASS**; staging **blocked** | D-33-03 is fixed at `d8f971ea2b34`; isolated PostgreSQL 17.6 upgrade, downgrade, re-upgrade and `alembic check` pass. The chain has one head. Staging rollout rehearsal still needs authorization. |
| Telemetry and required alerts | implemented; promtool-tested; delivery not exercised | `infra/observability/`, `polycodebench_core/telemetry.py` |
| Runbooks, retention/rights, rehearsal reports | implemented | `docs/operations/` |

The phase is not complete. Every element that can run without cloud access was implemented and verified locally. The phase gate itself requires staging evidence, which needs the owner inputs in `docs/operations/staging-execution-plan.md` §1. Production-only steps were not executed. No benchmark results were published.

Prior phase states are unchanged by this work: Phase 2, 3 and 4 blocked; Phase 6 in progress, with Prompt 32 running concurrently.

Next: the unblock step in `docs/operations/staging-execution-plan.md`, then Prompt 34 — Perform the final integrated audit and repair pass.
