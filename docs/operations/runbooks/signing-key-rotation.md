# Runbook: publication signing-key rotation and compromise

Covers T 19.5 rotation, with retained verification keys and the compromise path. E2E-43 key-rotation subcase.

## Signals

- Scheduled rotation: annually, or when a publisher leaves.
- Suspected exposure of a private key: an unexpected `GetSecretValue` on `pcb/<env>/signing/*` in CloudTrail, a leaked file, or a compromised publisher identity.

## Authorized role

Key ceremony: two release approvers plus the platform owner. Only the `publisher` task role can read the signing secret; the secret policy and KMS key policy both deny every other `pcb:role`.

## Procedure: routine rotation

1. Generate the new key and update the keyring:
   - **[V-local]** `pcb-ops keys rotate --keyring <keyring.json> --new-key-id <env>-ed25519-<yyyy-mm> --private-key-out <offline path>`.
   - The previous active key becomes `retired`. Its public key is **kept**, and key IDs are never reused.
2. **[S]** Add the key ID to `signing_key_ids` in the environment tfvars and `terraform apply`. This creates the empty secret container. Store the private key:

   ```bash
   aws secretsmanager put-secret-value --secret-id pcb/<env>/signing/<new-id> --secret-binary fileb://<pem>
   ```

   Then shred the offline copy.
3. Update `secrets.active_signing_key_id` and `signing_key_refs` in `config/environments/<env>.yaml`, then run `pcb-ops env validate`.
4. Publish the new keyring to `/keys/keyring.json` (publisher role) before the first release is signed with the new key.
5. Verify:
   - **[V-local]** `pcb-ops keys verify --keyring <keyring> --manifest <old release manifest>` gives `{"valid": true, "detail": "retired"}`.
   - On a new release, the same command gives `"active"`.

## Procedure: compromise

1. **[V-local]** `pcb-ops keys revoke --keyring <keyring> --key-id <id> --reason "<incident ref>"`. Verification of everything that key signed now fails closed (`"signing key revoked"`).
2. Rotate as above. Re-sign each affected **current** release as a correction successor (publication-rollback-and-withdrawal.md), with reason "re-signed after key revocation". Withdraw the predecessors with the incident reference.
3. Follow compromised-worker-identity.md if the key leaked through a service identity.

## Expected state transitions

- Rotation: key `active` → `retired` (still verifies), new key `active`.
- Compromise: key → `revoked` (fails closed), and successors are signed by the new key.

## Recovery verification

- The keyring digest is unchanged by transport: `Keyring.from_document` checks it.
- Old manifests verify after rotation. Revoked ones fail.
- The drill's 10 assertions all pass.

## Escalation

Platform owner. Security contact for compromise.

## Never

Never delete a retired public key. Never re-use a key ID. Never copy a private key into a worker, a CI log or the repository.
