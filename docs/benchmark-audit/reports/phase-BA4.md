# Phase BA4 - Task lifecycle

Phase BA4 covers Prompts 95-97. All three prompts have partial implementation foundations. BA4 remains partial because required live worker/source/database/reviewer and projection evidence is unavailable.

## Prompt95 evidence

Finite-scope firewall policy, fail-closed task decisions, bounded replacement plans and derived benchmark manifests are implemented. The report and acceptance ledger record partial status because no production worker admission authority, trusted reviewer roles, approved corpus/source rights, live benchmark import, or PostgreSQL integration environment is configured. Synthetic fixtures and offline migration rendering do not close those gates.

See [Prompt95 report](prompt-95.md), [Prompt96 report](prompt-96.md), and [Prompt97 report](prompt-97.md).

## Prompt96 evidence

Versioned finite-scope monitor policies, DST-safe slot planning, bounded incremental/full refresh planning, serialized source/query/storage/cost reservations, conditional durable retry recording, verified typed in-app alerts and inbox dedupe are implemented. The migration and synthetic contract tests render and pass offline. BA4 remains partial because trusted owner/approver roles, a production scheduler and source connectors, live evidence/review history, an authenticated inbox, and PostgreSQL concurrency/recovery evidence are unavailable.

## Prompt97 evidence

Versioned descriptive health v2 contracts, strict denominator/unknown/tier reconciliation, Decimal percentages and means, explicit sampled-cohort/missingness disclosure, unique-family metrics, deduped overlap prevalence, coverage/freshness/provenance and detector-quality strata are implemented. Trend scopes identify policy, membership, context, source/window, sampling, method, family and definition changes. Persistence binds reports to stored plans, snapshots, policies and accepted task evidence; its append-only migration renders offline, and the synthetic section 18 goldens plus combined Prompts85-97 regression pass. Health remains partial because no live corpus/query/temporal source evidence, PostgreSQL run, independent detector labels, or authenticated reviewed projection is available. Native benchmark/code-quality/ranking paths and existing paired-family behavior uncertainty are unchanged.

## BA4 aggregate gates

| Gate | Status | Evidence and remaining work |
|---|---|---|
| BX-34-37 firewall and replacement lifecycle | Partial | See Prompt95 report; trusted source rights, production workers, human review and live database remain unavailable. |
| BX-38-40 monitoring and alerts | Partial | See Prompt96 report; scheduler, source integration, role verifier, inbox UI and database concurrency/recovery remain unavailable. |
| BX-41-43 health metrics and comparability | Partial | See Prompt97 report; local Decimal/count goldens pass, while live source/query, database and reviewed-projection evidence remain unavailable. |

BA4 is partial overall. Synthetic fixtures and offline migration SQL establish contract behavior only; they do not substitute for live source, human, model or database evidence. The exact next task is Prompt98 in phase BA5.
