# Runbook: publication rollback and release withdrawal

Covers T 19.5 withdrawal and correction, the alert `PcbPublicPointerUnavailable`, and E2E-43's withdrawal subcase.

## Signals

- `pcb_public_pointer_unavailable > 0`: the board pointer references data that cannot be served.
- A confirmed defect in a published release: wrong evidence, a rights problem, or a leaked task.
- A request from the methodology owner.

## Authorized role

A release approver withdraws, with `publisher` and MFA. The correction successor needs `curator`, `reviewer` and `publisher` actions by an approver. Every action is audited.

## Procedure

All `pcb-release` commands below were executed in the publication drill **[V-local]**, against a synthetic local store. Evidence: `e2e-43-local-drills.json`, drill `key-rotation-correction-withdrawal`.

1. Read the current pointer generation: `ReleaseStore.current()`, or the `generation` value from the last publish output.
2. If a corrected release is ready, publish the **successor** first:

   ```bash
   pcb-release create   --store <store> --subject <you> --role curator --role reviewer --role publisher \
     --request-id <id> --content <content.json> --projection <projection.json> \
     --predecessor <bad-release-id> --correction-reason "<what changed and why>"
   pcb-release validate --store <store> ... --release-id <new> --expected-version <v> --evidence <receipts.json>
   pcb-release review   --store <store> ... --release-id <new> --expected-version <v> --reason "<review>"
   pcb-release approve  --store <store> ... --release-id <new> --expected-version <v> --reason "<approval>"
   pcb-release publish  --store <store> ... --release-id <new> --expected-version <v> \
     --expected-generation <g> --key-file <active-key.pem> --key-id <active-key-id>
   ```

3. Withdraw the defective release:

   ```bash
   pcb-release withdraw --store <store> --subject <you> --role publisher --request-id <id> \
     --release-id <bad> --expected-version <v> --expected-generation <current g> \
     --reason "<public reason shown on the historical page>"
   ```

   A stale generation is refused with exit 4 (CAS). Re-read the pointer and retry; never force it.
4. **[S]** Invalidate the CDN for the release's paths: `aws cloudfront create-invalidation --distribution-id <id> --paths "/releases/<bad>/*" "/v1/public/*"`. Published objects are under Object Lock and are **not** deleted.

## Expected state transitions

`published` → `withdrawn`. The pointer moves to the successor, or is cleared if the withdrawn release was current. The pointer never dangles.

## Recovery verification

All of these passed in the drill:

- The withdrawn release still resolves, with its original manifest and a `withdrawal.reason`.
- `pcb-ops keys verify --keyring <keyring> --manifest <withdrawn manifest>` is still valid, unless the key was revoked.
- The pointer references the successor, or is empty.
- The audit trail shows `publish` and `withdraw`.

## Escalation

Methodology owner (content), platform owner (pointer/CDN failures).

## Never

Never delete a published release, its objects or its audit rows. Never edit a published release in place. Never publish "just because the infrastructure works": publication needs the full validation receipts.
