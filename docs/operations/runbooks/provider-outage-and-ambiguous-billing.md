# Runbook: provider outage and ambiguous billing

Covers the alerts `PcbProviderOutage` and `PcbCampaignSpendApproachingLimit`. Provider outages are a subcase of E2E-43.

## Signals

- `pcb_provider_outage > 0` for 5 minutes, or a rise in `pcb_provider_throttles_total` and `pcb_provider_latency_seconds`.
- `pcb_uncertain_cost_calls > 0`: deliveries that may have been billed with no stored response.
- `pcb_campaign_spend_ratio >= 0.9` of the configured campaign threshold.

## Authorized role

The on-call operator pauses work. The budget owner (platform owner) decides on reconciliation and any budget change. Model credentials stay inside the model gateway; operators never read them.

## Procedure

1. **Do not retry by hand, and do not raise budgets.** The gateway is designed to fail safe, and the drill confirmed it **[V-test]**: `tests/test_model_gateway_postgres.py -k "e2e11 or e2e12 or retry_classes or delivery_cap or crash_between or late_response or lease_loss"` gave **14 passed** on the Prompt 33 database. The tests show that:
   - a not-delivered failure releases its reservation;
   - rate limits back off;
   - an ambiguous timeout keeps its spend exposure and a retry reserves more;
   - the delivery cap bounds retries;
   - a crash between dispatch and response is treated as ambiguous;
   - a late response is recorded as evidence, never as a second result;
   - lease loss stops dispatch before any spend.
2. If the outage persists, pause the affected attempts so no new reservations are made:
   - **[V-test]** `pcb-scheduler cancel-attempt <attempt-id> --reason "provider outage <ref>"` (`tests/test_operations_postgres.py::test_runbook_containment_commands_disable_workers_and_cancel_attempts`).
   - Cancelled scopes cannot spend: E2E-09 in `test_model_gateway_postgres.py`.
3. Inspect the budget account: `pcb-model account <account-id>` shows confirmed spend, open reservations and uncertain exposure. **[S]** in staging; it was not run against real provider data.
4. When the provider bill arrives, reconcile each ambiguous delivery with evidence:
   - `pcb-model reconcile <delivery-id> --evidence <invoice line ref> --cost-micro-usd <n> --input-tokens <n> --output-tokens <n>`, or `--unbilled` if the provider confirms it wasn't billed.
   - Reconciliation is append-only and needs evidence (E2E-12 tests above). **[S]**: there is no live provider data.
5. Resume: requeue the cancelled attempts by creating a new run under the same frozen plan, so failures stay in the denominator.

## Expected state transitions

delivery `dispatched` → `ambiguous` (exposure retained) → `reconciled` (billed or unbilled). Attempt `running` → `cancelled`. Budget: reserved → confirmed or released.

## Recovery verification

`pcb_uncertain_cost_calls` returns to 0 after reconciliation. Spend ratio is below 0.9, or the budget owner has approved a new, recorded budget. No attempt has two results.

## Escalation

Budget owner for spend. Platform owner for gateway faults.

## Never

Never delete ambiguous deliveries to "clear" exposure. Never mark a failed attempt as excluded so it leaves the denominator. Never switch to another model or provider to finish a cohort.
