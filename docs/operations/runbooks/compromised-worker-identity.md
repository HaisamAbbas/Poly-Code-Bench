# Runbook: compromised worker or service identity

Covers the alert `PcbUnexpectedSecretOrHiddenAccess` and the CloudTrail hidden-access alarm.

## Signals

- Increasing `pcb_secret_access_denied_total`.
- The CloudTrail metric `UnexpectedHiddenAccess > 0`.
- IAM `AccessDenied` bursts in CloudTrail from one task role.
- A guest attestation failure: `Ec2VmSandboxProvider._attest` refuses a guest.
- An identity guard refusal at startup: a container exits 4 with "identity refused".

## Authorized role

On-call operator contains. The platform owner rotates credentials. A security contact leads the investigation.

## Procedure

1. Contain without destroying evidence:
   - **[V-test]** Drain the worker so it gets no new claims: `pcb-ops workers drain <worker-id>` (`tests/test_operations_postgres.py`).
   - **[V-test]** Then disable it: `pcb-scheduler worker-status <worker-id> disabled` (same test as `cancel-attempt`). Its leases expire, and fences refuse its late commits.
   - **[S]** Stop the task: `aws ecs update-service --cluster pcb-<env> --service <svc> --desired-count 0`.
   - **[S]** Revoke its sessions. Attach an inline deny with an `aws:TokenIssueTime` condition to the task role (the standard "revoke sessions" policy).
2. Snapshot before teardown. For a suspect guest, keep the instance stopped (not terminated) only if the security contact asks. Otherwise let the reaper terminate it, after copying its stage evidence.
3. Rotate everything the role could read:
   - its `pcb/<env>/db/<role>` credential;
   - for the model or judge gateway, the provider keys in its namespace;
   - for the publisher, the signing key (signing-key-rotation.md, compromise path).
4. Check blast radius:
   - The permissions boundary denies cross-environment resources and identity-tag changes (`modules/identity`).
   - The hidden bucket and KMS key deny every role except `eval-supervisor`/`admission-operator`.
   - Confirm in CloudTrail that no `Decrypt`/`GetObject` succeeded outside those.
5. If hidden material was read, follow leaked-held-out-task-retirement.md.

## Expected state transitions

worker `active` → `draining` → `disabled`. Its jobs are reaped and re-run under new fences on clean workers. Credentials are rotated. The service is redeployed, and the identity guard verifies the new tasks.

## Recovery verification

- `pcb-ops identity verify` succeeds for the redeployed tasks. The ECS logs show the verified environment, role and tier.
- No further denied-access metrics.
- The re-run jobs complete under higher fences.

## Escalation

Security contact, platform owner.

## Never

Never re-enable the identity before credentials are rotated. Never wipe the guest or logs before evidence is captured. Never widen a role's policy to make the alerts stop.
