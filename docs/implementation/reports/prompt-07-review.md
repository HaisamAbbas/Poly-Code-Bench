# Prompt 07 review and correction checklist

Scope: review the uncommitted Prompt 06/07 integration against Prompt 07 and the Constitution, repair confirmed defects, validate locally, then commit and push. Production VM, model usage and release evidence retain their recorded prerequisites.

- [x] Read the Constitution, Prompt 07 contracts, scheduler, worker and integration tests.
- [x] Reproduce and fix concurrent campaign/provider fairness enforcement.
- [x] Fence capacity cleanup and preserve reservations during guest creation.
- [x] Make heartbeat/provisioning/cancellation lifecycle fail closed and drain child tasks.
- [x] Verify cancellation under competing transactions and parent-scope revocation.
- [x] Verify dependency failure propagation, concurrent DAG completion and scope states.
- [x] Verify complete result replay identity, gate outcomes and durable event integrity.
- [x] Run real PostgreSQL, object-store and Docker regressions plus the full suite: 124 passed, no skips.
- [x] Run formatting, lint, typing, migration drift, schemas, builds and ledgers.
- [x] Independently inspect the final diff and update evidence and remaining gates.

Commit scope is Prompt 06 prerequisites, Prompt 07 and these review corrections. The owner requested keeping concurrent Prompt 08 outside the commit. Final commit/push identity is reported after Git confirms it.

The five original reproducible probes failed before correction (provider cap exceeded, dead prerequisite stalled, unused branch failed, changed outcome replayed, provisioning slot freed). The retained regression suite adds campaign cap concurrency, stale cleanup, locked cancellation, parent revocation, evaluation cancellation/exhaustion, event integrity and parallel DAG joins. Worker fixtures are explicitly synthetic lifecycle tests; the integration suite separately exercises actual PostgreSQL, object-store bytes and Docker guests.
