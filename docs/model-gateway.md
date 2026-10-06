# Model gateway (Prompt 08)

The gateway is the only path by which PolyCodeBench spends provider money. It protects
secrets, preserves provider semantics, and accounts for outcomes it cannot be sure of.
Implementation map: contracts and pure rules in `packages/core` (`model_contracts.py`,
`model_planning.py`, `endpoint_policy.py`), ledger and registration in `packages/persistence`
(`model_ledger.py`, `endpoints.py`, `model_configs.py`), adapters, transport and the gateway in
`packages/orchestration/src/polycodebench_orchestration/gateway/`.

## Operator workflow

1. **Register** (administrator): `pcb-model register --provider <kind> --url <base URL>
   --secret-ref secret://models/<name> --policy policy.json --capabilities caps.json`.
   Hosted providers (`openai_compatible`, `anthropic`, `google`) need an HTTPS allowlist policy
   naming the exact host; `local` needs an internal policy naming the CIDRs it may resolve to.
   The endpoint is `pending` and is never contacted by a gateway call.
2. **Check**: `pcb-model check <endpoint-id>` resolves DNS under the registered policy and
   confirms the secret is provisioned, without contacting the endpoint.
   `pcb-model check <endpoint-id> --live model-config.json` runs a small conformance probe set
   (basic completion, claimed usage counters, native tool call) against the endpoint.
3. **Approve** (administrator): `pcb-model decide <endpoint-id> approved --reason ...
   --expected-version N [--conformance report.json]`. OpenAI-compatible and local endpoints need
   a passing conformance report. Identity fields (URL, secret reference, policy, declared
   capabilities) are immutable; change them by registering a new endpoint.
4. **Plan**: `pcb-model plan --config model-config.json --protocol protocol.json --tasks N
   --samples M` prints compatibility per control and a labeled worst-case cost estimate. Exit
   code 4 means a blocker: an unsupported control without a named cohort exception, or no
   enforceable cost bound for a strict money cap.
5. **Run**: `ModelGateway.call(...)` (used by Prompt 09). Account rows must exist for the
   scope (`PostgresModelLedger.ensure_account`, campaign → run → attempt).
6. **Reconcile**: `pcb-model reconcile <delivery-id> --evidence "..." (--unbilled |
   --cost-micro-usd N [--input-tokens N --output-tokens N])` resolves retained exposure. The
   charge lands on the accounts of the delivery's own call, and each resource can be resolved
   once. `pcb-model account <id>` shows balances, ledger discrepancies and unresolved exposure.

Exit codes: 0 success, 2 validation/config, 3 permission, 4 budget or compatibility block,
5 infrastructure. `PCB_DATABASE_URL`, `PCB_SERVICE_IDENTITY` and `PCB_ROLES` select the database
and the acting principal; the model secret namespace is `PCB_MODEL_SECRET_NAMESPACE` (default
`models`).

## Secrets

Registrations store only `secret://<namespace>/<name>`. The value is read at call time from
`PCBSECRET__<NAMESPACE>__<NAME>` (upper-case, `-` → `_`). The prefix deliberately avoids `PCB_`
so the startup-config loader, which rejects unknown `PCB_` keys, never sees credential material.
The value appears only in the outgoing request header, never in stored requests, registrations,
audit rows, exceptions or the CLI, and any echo of it in a response body is replaced with
`[REDACTED]` before the bytes are stored.

Staging and production use AWS Secrets Manager instead of environment values. The container
entrypoint verifies its STS role against the environment manifest first; the resolver then
requires that verified environment to match `PCB_ENVIRONMENT`. `model-gateway` and
`solve-supervisor` can resolve only `pcb/<env>/model/<name>`, while `judge-gateway` can resolve
only `pcb/<env>/judge/<name>`. Existing endpoint references retain their logical
`secret://models/<name>` form. IAM grants `GetSecretValue` only under the role's environment path.
An operator must provision the secret value separately; Terraform and this change create no
provider credentials. Development and integration continue to use the local environment resolver.

## Capability model

`adapter.capabilities()` is the wire ceiling of the adapter. The registration's declared
capabilities carry model facts the operator attests (context window, seed range, which usage
counters appear, which output parameter the endpoint accepts). Effective capability is the
conjunction. Nothing is inferred from a model name.

| Control | Unsupported result |
|---|---|
| Native tools (agent protocols) | reject; single-shot or a separate text-tool protocol is required |
| Structured output | reject unless a named cohort exception; if excepted the schema is not sent and the drop is recorded |
| Temperature | reject unless a named cohort exception (recorded, not sent) |
| Seed | `pass_if_supported`: recorded as unsupported, not sent; `deterministic_mapping`: reject |
| Reasoning control | reject |
| Context | reject when protocol input plus output exceeds the declared window or the window is undeclared |
| Usage counters | reject unless excepted; calls then settle as unknown-cost exposure |

Provider seed mapping is `modulo-v1` over the declared range. Dropped controls are stored in
each call's `price_snapshot.dropped_controls`.

## Dispatch and accounting

Per logical call (`UQ(scope, logical_call_key)`): lease guard → recorded state (a stored response
or failure is returned without any check below) → approved endpoint → capability validation →
cost bound → credential and request checks → throttle slot → **one transaction** creating the
intent, the delivery and a reservation against every account from campaign to run to attempt
(locked root-first) → lease guard again → send → raw bytes stored and bound to the delivery →
settle → return. A call names one request and one model configuration; the request must carry the
resolved config's temperature and reasoning and an output cap no larger than the config's, so the
reservation covers exactly what can be billed. Each balance change is an
`accounting_entry` moving an amount between `none`, `reserved_open`, `uncertain_committed` and
`spent_confirmed`; cached balances can be re-derived with `verify_balances`.

- Money is integer micro-USD rounded up: input tokens (provable bytes-based upper bound) and
  output tokens (the output cap) times the operator price snapshot. No price, or an output cap
  that is not documented to bound all billed output, means no enforceable bound: a strict cap is
  blocked. An operator per-call reserve is allowed only as an explicitly non-strict policy.
- Turns, input tokens and output tokens are separate limits (`budget_resource`). A retry does not
  consume a second turn.
- 4xx, 429 and pre-send failures release their reservation. Timeouts after send, 5xx, 408 and
  unparseable 2xx bodies are **ambiguous**: the reservation moves to `uncertain_committed` and is
  never released automatically. A retry reserves additional exposure. Delivery count is bounded.
- Missing or partial usage stays `NULL`; the exposure is retained until reconciled through
  append-only `usage_record` revisions and ledger entries.
- A recorded response is returned on recovery without a request; a recorded definitive failure is
  returned without a request; reusing a key with different request bytes or a different model
  configuration conflicts. If two deliveries of one call both answer (a stalled original and a
  recovered retry), the first to arrive is the only result consumed; the other is stored and
  billed as evidence. Bytes persisted before a crash are settled on restart, not re-requested.
- Lock order is advisory key → call intent → delivery → accounts root-first, everywhere, so a
  retry starting cannot deadlock with a worker settling.
- Reported usage of zero next to real output is treated as implausible: the response is kept but
  the call settles as unknown-cost exposure.

## Known limits

- Throttling (concurrency, spacing, `Retry-After` cooldown) is per process; cross-process
  provider concurrency is governed by the scheduler's provider fairness cap.
- Exactly-once billing cannot be guaranteed; ambiguity is bounded and disclosed, not removed.
- Anthropic billed input is the sum of reported uncached, cache-creation and cache-read segments;
  estimates use the list price with no cache discount and are labeled as estimates.
- Google: `generateContent` only. Thinking controls and schema-constrained output are not
  implemented because their REST field names could not be confirmed from the fetched docs, so
  requests that need them are rejected. OpenAI wire facts come from the public OpenAPI schema
  (the docs site returned 403); the adapter assumes `POST {base}/chat/completions`.
- Live provider behaviour is unverified except where an evidence file under
  `docs/implementation/evidence/` says otherwise.
