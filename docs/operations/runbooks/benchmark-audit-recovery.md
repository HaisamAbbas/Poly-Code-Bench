# Runbook: benchmark audit recovery and sealed evidence

This runbook supplements [database and object-store restore](database-object-store-restore.md) for
benchmark-audit documents, sealed tasks, audit calls, monitor reservations and attestation history.
The procedure is fail-closed: a successful database restore alone does not establish a usable audit
recovery.

## Signals

- `PcbBackupRestoreIntegrityFailure` or a restore report with `passed: false`.
- Missing or mismatched private artifact, audit document, seal access event, attestation lifecycle
  event, call delivery, usage, query reservation or monitor slot after restore.
- A KMS/key-provider outage, key-version mismatch, uncertain source call or stale worker lease.

## Isolated restore

1. Select a database, object-store and publication backup from the same recovery point. Preserve the
   original backup and manifest unchanged.
2. Use the database/object-store restore runbook to restore into a newly labeled isolated pair. The
   local rehearsal must use the existing synthetic internal source and a new work directory.
3. The recovery verifier checks foreign keys and row-count parity, audit document schemas and
   semantic digests, embedded document/artifact references, sealed-manifest successor transitions,
   attestation lifecycle chains, verified artifact digests, archived scorecard replay and the public
   projection. It records counts for audit calls, deliveries, usage, budget accounts, monitor slots,
   reservations and alert inbox rows.
4. A missing required audit table fails with `benchmark_audit_schema_missing`. Restore a backup made
   from a compatible schema or rehearse the reviewed migration path in a separate disposable target;
   never treat an old pre-audit snapshot as a complete benchmark-audit recovery.
5. Keep the isolated target unpromoted when any integrity check fails. Retain the failed report and
   select an earlier recovery point only through the incident owner.

## Keys, retention and privacy

- Database backups preserve key-provider and key-version references. They do not contain or validate
  production key material. Recover provider access and retained key versions through the approved
  secrets/KMS process, then verify access in an isolated target before any promotion.
- Do not re-encrypt, re-seal, re-sign, reset budgets or rewrite exposure history to make a restore
  pass. Keep old key versions available for retained sealed artifacts and preserve access events,
  canary observations, commitments and attestation lifecycle records under the configured retention
  policy.
- Never place task text, plaintext digests, canary markers, ciphertext keys, secret references or
  provider exception messages in tickets, logs or public reports. Recovery evidence reports stable
  error classes/codes and aggregate row counts only.
- For signing-key rotation or compromise, follow
  [publication signing-key rotation](signing-key-rotation.md). Never delete retired public keys.

## Outage, ambiguity and stale workers

- Follow [provider outage and ambiguous billing](provider-outage-and-ambiguous-billing.md) and
  [deployment migration and drain](deployment-migration-and-drain.md) before restoring a live target.
- Reuse persisted logical call identities and stored provider responses. Treat timeout-after-send as
  ambiguous exposure; do not resend under a new identity or reset query, cost, storage or attempt
  accounting.
- Drain workers before recovery. Fences reject stale commits; cancellation must stop guest/parser
  work and reclaim its local resources before capacity is reused. Preserve the original queue,
  exposure and usage history.

## Indexes and operational limits

The current checkout does not persist an approved retrieval-index configuration or provide an index
rebuild adapter. The verifier reports `retrieval_index_configuration: not_persisted_or_rebuildable`;
this is an explicit blocker for BX-56 and production promotion. Do not claim that corpus search is
available from restored document and artifact records alone.

No approved benchmark corpus, live source connector, production key provider, staging database or
monitor/search load environment was available for this Prompt 103 run. Local synthetic restore and
failure tests are not a production RTO/RPO or capacity measurement. The required owner action is to
provide a versioned index manifest/rebuild path and an approved isolated staging corpus before
measuring search and monitor latency, storage and cost.
