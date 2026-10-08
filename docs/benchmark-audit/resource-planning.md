# Resource planning v1

The planner in `polycodebench_services.benchmark_audit_catalog.plan_audit_resources` is a pure local calculation. It accepts selected benchmark counts, source groups, retrieval stages and an optional average item byte size. It never opens a URL, invokes a connector, starts a model call, reserves money or dispatches a job.

## Frozen limits

- 300 tasks per plan, matching the proposed Prompt101 pilot ceiling.
- At most 8 registered source groups and 5 retrieval stages.
- At most 20 candidates per selected source per task and 100 candidates per task total.
- At most 12,000 planned query units per plan and 512 MiB estimated selected-item storage.
- No source price is pinned, so monetary cost is `null / unknown_price`; no model diagnostics are planned (call ceiling 0).

For a 300-task plan with eight sources and five stages, the upper bounds are 12,000 planned query units (one unit per task/source/stage) and 30,000 candidate slots. They are capacity ceilings, not executed requests, retrieved matches or recall guarantees. The 512 MiB storage cap is a provisional local planner guard with no workload measurement behind it; a real campaign needs an approved, workload-specific storage cap. A missing item-size input leaves storage unknown. Limits are immutable policy input; exceeding them rejects the requested scope instead of clipping it.

## Authorization state

The v1 source policies all have `authorization_state: not_approved`; connectors are `not_implemented`, and conformance is `not_run`. Catalogued benchmark payloads also lack approved rights/import adapters. A plan reports per-benchmark and per-source blockers and remains blocked. Unknown pricing is also a blocker, even though the plan is only a dry run. Even a future approved plan has `dispatch_allowed: false`; dispatch belongs to the later authenticated service/queue workflow.

This planner is not a cost estimate when upstream prices or corpus sizes are missing. An operator must supply reviewed source scope, source revision, actual byte counts and approved caps before a real scan plan can pass later gates.
