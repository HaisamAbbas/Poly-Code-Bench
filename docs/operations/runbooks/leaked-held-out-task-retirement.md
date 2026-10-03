# Runbook: leaked held-out task retirement

Use when hidden tests, oracles or private task material may have been exposed.

## Signals

- The CloudTrail alarm `pcb-<env>-unexpected-hidden-access`: a hidden-bucket `GetObject` by any role other than `eval-supervisor`/`admission-operator`.
- `PcbUnexpectedSecretOrHiddenAccess`.
- An external report of hidden content.
- The task appears in public data.

## Authorized role

On-call operator contains. The methodology owner retires the task. A security contact is involved if the exposure came from a compromised identity.

## Procedure

1. Contain the exposure. If an identity is involved, follow compromised-worker-identity.md first.
2. Determine scope from CloudTrail data events on the hidden bucket. The trail is created by `modules/telemetry`, with log-file validation enabled. **[S]**:

   ```bash
   aws cloudtrail lookup-events ...
   ```

   Alternatively, use CloudWatch Logs Insights on `/pcb/<env>/cloudtrail`, filtering on `requestParameters.bucketName = <hidden bucket>`.
3. Quarantine the affected tasks (task-quarantine.md). Their frozen versions stay immutable.
4. Retire them from future task sets. Releases that included them get a correction successor that discloses the exposure window. Results produced **before** the exposure remain valid as recorded; the methodology owner decides on disclosure wording.
5. Add the retirement to the task's rights and provenance record (retention-and-rights-policy.md §Holds).

## Expected state transitions

Task `admitted` → excluded from new task-set versions. Affected releases are succeeded by corrections. A legal/incident hold is placed on the related evidence.

## Recovery verification

- No hidden-bucket reads outside the allowed roles after containment: the alarm stays clear.
- New task sets exclude the task.
- The correction release is published.

## Escalation

Methodology owner and security contact.

## Never

Never delete access logs or the trail. Never silently reuse a leaked task in a new cohort. Never copy hidden content into tickets or chat.
