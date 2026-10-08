# Benchmark audit operator handoff

This handoff describes the current checkout. Local contracts and synthetic fixtures are available; no live benchmark audit is ready to dispatch or publish. Review the [acceptance ledger](../acceptance.md), [source observations](../source-observations-2026-10-09.md), and [recovery and sealed-evidence runbook](../../operations/runbooks/benchmark-audit-recovery.md) before authorizing any operation.

## Configuration and read-only checks

The versioned inputs are `config/benchmark-audit/registry-v1.yaml`, `source-policy-v1.yaml`, `planning-limits-v1.yaml`, and `capability-matrix-v1.yaml`. The source policy currently marks connectors unimplemented and access not approved. A plan or metadata pin does not authorize a fetch, import, model call, or publication.

Run these local checks from the repository root:

```text
uv run --locked python scripts/benchmark_audit_traceability.py
uv run --locked python scripts/benchmark_scope_conformance.py
uv run --locked alembic -c packages/persistence/alembic.ini heads
```

The traceability command reads repository files and prints JSON; exit 0 means ledger/report structure is internally consistent, even when the reported audit status is `partial`. The scope command is a metadata preview, not a live source scan or public health report. Alembic `heads` is read-only; apply migrations only to an explicitly provisioned, approved isolated target using the [deployment/migration/drain procedure](../../operations/runbooks/deployment-migration-and-drain.md). The designated local test database has no initialized audit schema and must not be migrated as an ad hoc check.

## Resource and cost limits

`planning-limits-v1.yaml` caps a plan at 300 tasks, 8 source groups, 20 candidates per source and task, 100 candidates total per task, 5 stages per task, 12,000 query units, and 536,870,912 bytes (512 MiB) of planned storage. The eight source policies cap each plan at 2,000 requests and each response at 5,242,880 bytes (5 MiB). These are configured ceilings, not measured throughput or costs; some connectors and the durable index/rebuild path are not implemented. No external or paid units were dispatched for Prompt106. No live API price, storage cost, production budget, search/monitor latency, or representative capacity measurement is available.

Before any approved campaign, freeze the source set, task/component membership, query units, storage and currency budgets, retry policy, and stop conditions in versioned plan evidence. Require an operator-visible reservation and reconcile actual provider usage before resuming ambiguous calls. Do not treat a configured cap or local fixture as proof that an external call ran within budget.

## Source, rights, and import procedure

1. The source owner identifies the exact dataset/repository revision, configuration, split, component list, lineage, checker/runtime, and retention terms. Obtain written rights and access approval for that exact scope, then store an immutable manifest and verify artifact digests before import.
2. Use only the named source policy and allowlisted endpoints. The policy is not a connector implementation. Do not run dataset loading scripts or downloaded task code. Common Crawl is limited to named index snapshots and selected WARC records; GitHub requires owner-approved repositories and commits; Hugging Face requires an exact dataset/revision/config/split.
3. Keep GAIA blocked until its owner grants gated access and private-storage terms. Keep HellaSwag blocked pending resolution of the recorded upstream notice; do not use mirrors or cached copies to bypass it. Treat image/OCR, tool-call and agent-environment scope as unsupported until approved data, runtime isolation and modality evidence exist.
4. Retain only approved artifacts, exact digests, rights references and minimum necessary private excerpts. Quarantine a task with unresolved rights or integrity using the [task quarantine](../../operations/runbooks/task-quarantine.md) and [held-out task retirement](../../operations/runbooks/leaked-held-out-task-retirement.md) procedures; preserve immutable history.

Repository/dataset commit pins currently establish metadata identity only. Four local data-only adapters have synthetic parser fixtures; the synthetic GSM8K parser does not authorize official payload access or establish benchmark conformance.

## Deploy, identity, and keys

Use the deployment runbook's environment validation, migration rehearsal, worker drain, expand-only migration, compatible service rollout and code rollback steps. Staging is a template with unresolved owner inputs; do not infer that a deployment target or credential exists. Preserve forward-only migration and immutable evidence behavior.

The sealed-evaluation development crypto adapter and ephemeral browser keys are not production key custody. Production signing/KMS requires the approved key ceremony and role separation in [signing-key rotation](../../operations/runbooks/signing-key-rotation.md) and the [audit recovery runbook](../../operations/runbooks/benchmark-audit-recovery.md). Keep retired public keys and required historical key versions. Never put secret material, private key paths, credentials, task text, or private digests in this handoff, logs, or tickets.

For a specific public attestation, run `uv run --project packages/api pcb audit verify-attestation --dry-run <public-attestation.json> --trust-store <public-trust.json>` with only public artifacts. The dry-run path makes no HTTP call. The verifier does not create trust or reviewer approval. No production signer, external timestamp authority, configured public trust publisher, or approved attestation is available in this checkout. The public browser/API journeys used synthetic fixtures.

## Monitoring, outage, and recovery

Monitor schedules, retries, reservation rows, and alert documents have tested local contracts, but no production scheduler, approved source connector, authenticated owner inbox, or live alert writer is configured. Do not enable polling or dispatch from a policy document alone. For provider timeout or ambiguous billing, preserve the logical request ID and usage history and follow [provider outage and ambiguous billing](../../operations/runbooks/provider-outage-and-ambiguous-billing.md); never resend as a new exposure identity or reset budgets.

Restore only into a newly labeled isolated database/object-store pair from a compatible recovery point. Follow [database/object-store restore](../../operations/runbooks/database-object-store-restore.md) and [benchmark audit recovery](../../operations/runbooks/benchmark-audit-recovery.md), verify audit schema, semantic digests, artifact references, seal and attestation chains, counts, and key-provider access, and keep failures unpromoted. The available local synthetic backup predates the audit tables and correctly fails with `benchmark_audit_schema_missing`. Production recovery time, RTO/RPO, key-provider restoration, and retrieval-index rebuild have not been measured or demonstrated.

## Corrections and publication

Never edit accepted evidence, a frozen task, a score, or a published report in place. For score defects, reproduce from the archive and issue a successor under [incorrect-score correction](../../operations/runbooks/incorrect-score-correction.md). For unsafe or rights-affected tasks, use [task quarantine](../../operations/runbooks/task-quarantine.md) and the held-out retirement process. For a published release, require the authorized owner/reviewer/publisher roles and follow [publication rollback and withdrawal](../../operations/runbooks/publication-rollback-and-withdrawal.md); retain predecessor notices and link the correction successor.

Audit service correction/dispute and attestation lifecycle contracts exist, but shared authenticated transition writers, live PostgreSQL history, reviewer authority, signer authority, and an approved public projection are absent. The above runbooks describe existing repository procedures; they do not close those audit-specific authorization or publication gates.

## Required unblock owners

| Owner | Required action before the corresponding gate can advance |
|---|---|
| Specification owner | Identify and supply the two historical source MDs claimed by §1.1 but absent from its source table/workspace. |
| Benchmark/source owner and rights reviewer | Provide immutable source snapshots, exact membership/component manifests, permission and retention evidence; resolve gated/takedown sources without mirrors. |
| Platform/database owner | Provision an isolated current-schema audit database/object store and compatible backup/key-provider test; provide the approved deployment target and migration window. |
| Security/key owner | Configure approved KMS/signing custody, public trust publication, external timestamp authority if required, rotation/revocation review and authorized identities. |
| Methodology/reviewer owner | Supply independent reviewers, calibration labels and approved model-context/exposure records; review the exact public projection before publication. |
| Runtime/modality owners | Supply approved isolated runtimes, parsers/checkers and image/OCR/tool/environment evidence or explicitly keep those catalog scopes unsupported. |
| Operations owner | Approve an index manifest/rebuild path and representative corpus, then measure storage, query/search/monitor latency, currency spend and recovery objectives under frozen caps. |

Until those inputs are present, retain the partial/blocked status in the acceptance ledger. The next repository action is the verified read-only traceability command above; resolving external blockers requires the named owner inputs, not a new prompt or a fixture substitution.
