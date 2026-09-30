# Prompt 08 completion report

Prompt 08 / Phase 2 — PARTIAL

1. Implemented functionality and changed files
   - Model gateway with OpenAI-compatible, Anthropic, Google and local adapters behind `ModelAdapter`/`BaseAdapter`; per-control capability validation (tools, structured output, seed, temperature, reasoning, context, usage) that rejects or records a named cohort exception; endpoint registration/approval with secret references only, HTTPS-allowlist or explicit internal-CIDR policy, per-connection DNS validation with address pinning and no redirects; call intents/deliveries, raw and normalized responses stored before the controller is told, usage and price snapshots; atomic campaign→run→attempt reservations with separate turn/token limits; ambiguous outcomes retain exposure, retries reserve more, missing usage stays NULL, append-only reconciliation; plan/cost output, `pcb-model` CLI, conformance probes, provider-shaped capability fixtures and an opt-in live smoke test. Operator guide: `docs/model-gateway.md`.
   - Key paths: `packages/core/src/polycodebench_core/{model_contracts,model_planning,endpoint_policy}.py`; `packages/persistence/src/polycodebench_persistence/{model_ledger,endpoints,model_configs}.py` and migration `9d3a71c05e24`; `packages/orchestration/src/polycodebench_orchestration/gateway/`; `packages/services/src/polycodebench_services/model_endpoints.py`; SQL role/grant additions; tests `tests/test_model_gateway_*.py`, `tests/fixtures/model_gateway/`, `tests/migration_support.py`; ledgers, decisions D-08-01..11, evidence `docs/implementation/evidence/prompt-08-*.json`.
   - Compatibility edits outside Prompt 08: Prompt 07's test fixture now requires its migration to be an ancestor of the database revision instead of the exact head (`tests/test_jobs_postgres.py`), and `verify_prompt00.py` accepts `done` for the last-worked prompt.

2. Tests/commands actually run and their results
   - Fresh `pcb_prompt08_test` on PostgreSQL 17.6: Alembic upgrade (10 revisions) and drift check PASS; provision/grant SQL applied PASS.
   - `pytest` model-gateway suites: units 73, fixtures 8, PostgreSQL+SeaweedFS with fixture transport faults 27, review regressions 18 — all PASS. Full suite: 247 passed, 7 skipped (Docker and live opt-ins).
   - Live: local adapter against real Ollama 0.34.4 (`llama3.2:3b`): conformance (completion, usage counters, native tool call) and a budgeted gateway call PASS; evidence `prompt-08-live-smoke-local.json`.
   - Ruff format/check PASS; strict mypy on the new modules PASS (33 files); package boundaries, workspace smoke, startup-schema check and `verify_prompt00.py` PASS; `uv build --all-packages` PASS; `pcb-model plan/register/check/account` exercised (exit codes 0/4/3 as specified).
   - Not run: live OpenAI-compatible hosted, Anthropic and Google adapters (no provider credentials exist in this environment); any paid provider call; hosted CI; Docker lifecycle opt-in tests (unchanged by this prompt). `mypy packages/configuration/src scripts` still fails on 11 findings in `scripts/` that predate this prompt.

3. Acceptance gates
   - Satisfied: PCB-08-1, PCB-08-2, PCB-08-3, PCB-08-4 (implemented; verification passed at unit, fixture, real-PostgreSQL and loopback-socket levels); E2E-10 and E2E-12 (real PostgreSQL + SeaweedFS, FIXTURE provider transport); E2E-11 gateway subcase.
   - Pending: E2E-11 agent-controller restart variant (Prompt 09); E2E-09 ranked-release exclusion and live usage (Prompt 17, model-usage retention passed with fixtures); live verification of the OpenAI-compatible, Anthropic and Google adapters; the two-model live pilot (Prompt 17) — none of this prompt's evidence satisfies E2E-31. Prompt 06 production E2E-05/06 and Prompt 07's remaining variants are unchanged.
   - Blocked: live hosted-adapter checks need provider credentials (`PCBSECRET__MODELS__<NAME>`), operator price snapshots and an authorized spend budget; no credentials were available.

4. Decisions or specification discrepancies recorded
   - D-08-01 gateway location; D-08-02 reservation-uniqueness/delivery-immutability schema discrepancy; D-08-03 outcome classification; D-08-04 cost bounds and strict caps; D-08-05 provider documentation discrepancies (OpenAI docs 403, Google Interactions-vs-generateContent, Anthropic temperature/seed); D-08-06 conformance probing of pending endpoints; D-08-07 secret references; D-08-08 arrival-order resolution; D-08-09 independent review (12 findings fixed); D-08-10 checker/test-pin compatibility; D-08-11 evidence boundary.

5. Exact next command or numbered prompt
   - Next: Prompt 09 — Implement single-shot and agent execution.
