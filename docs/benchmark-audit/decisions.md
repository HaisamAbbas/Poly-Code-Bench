# Benchmark audit decisions and specification discrepancies

The addendum's BADR identifiers are preserved below. Source documents remain read-only.

## Binding decisions

| ID | Decision | Implementation consequence |
|---|---|---|
| BADR-01 | Extend the modular monolith/shared evidence services. | No duplicate evaluator, scoring authority or canonical digest implementation. |
| BADR-02 | Benchmark snapshots immutable; parses and derived sets are children. | Preserve original bytes/membership; edits produce successors. |
| BADR-03 | Keep component and method distinctions in fingerprints. | Exact, normalized, semantic, code and modality signals stay labeled separately. |
| BADR-04 | Corpus scope is finite and explicit. | No global web search or clean-zero from missing coverage. |
| BADR-05 | Separate model-agnostic exposure from model-specific chronology/context. | Public overlap cannot imply model training membership. |
| BADR-06 | Versioned heuristic index with missingness bounds. | No probability wording; incomplete required scope cannot display low observed risk. |
| BADR-07 | Self-imports are exposure, not independent duplicate counts. | Exclude official source self-hits from independent match totals. |
| BADR-08 | Approximate retrieval produces candidates only. | Embedding/sketch hits require source evidence and adjudication. |
| BADR-09 | Semantic matches require independent review until calibrated. | AI cannot self-approve replacements or sign evidence. |
| BADR-10 | Date intervals and commitments prove limited chronology. | Receipt proves existence by time, not originality or first invention. |
| BADR-11 | Local-first seals and append-only disclosure events. | Resealing cannot erase prior recipient exposure. |
| BADR-12 | Canary hits show observed marker disclosure only. | No training-membership or universal detection claim. |
| BADR-13 | Behavioral tests are optional and preregistered. | Preserve controls, applicability, assumptions and power limits. |
| BADR-14 | Firewall combines scan, validity, rights, lineage and family gates. | Novelty or zero-hit cannot bypass admission review. |
| BADR-15 | Preserve replacement ancestry. | Paraphrases remain family-linked; derived scores are not official-comparable by default. |
| BADR-16 | Six queue scopes are exclusive and use existing accounting/fences. | Reconcile actual three scopes with required six; do not add only one assumed FK. |
| BADR-17 | Monitoring is bounded, idempotent and append-only. | No unbounded crawl/catch-up; in-app alerts default. |
| BADR-18 | Health exposes denominators, unknowns, sample design and policy breaks. | No sampled-census estimate or hidden missingness. |
| BADR-19 | Attestations bind reviewed scope and support expiry/revocation/successors. | Signature authenticates bytes, not a universal unseen-data guarantee. |
| BADR-20 | CPU/text-first rollout with qualified claims. | Missing live, human, calibration and modality evidence stays blocked. |
| BADR-21 | Audit document successors are same-kind, immutable rows. | Composite FK and immutable triggers preserve typed history; edits require successors. |
| BADR-22 | Diagnostic calls retain their attempt accounting identity and a separate audit reference. | `call_intent.attempt_id` stays authoritative for budget settlement; diagnostic audit metadata is append-only and plan-capped. |

## Recorded discrepancies

### ADDENDUM-GAP-01 — Incomplete source bridge
Section §1.1 claims five source documents but lists three hashed files and one blank row. The prompts repeat “five source MDs” without resolving the identities. I read the three pinned documents and inspected the repository's requirements matrix and ticket ledger as supplemental sources to preserve prior contracts. Their exact hashes are recorded in `implementation-ledger.md`. The addendum is unchanged.

### ADDENDUM-GAP-02 — Queue scopes differ from the tracked schema
Section §8 assumes five prior scopes and asks to add a sixth. The tracked `stage_job` had only attempt, evaluation and release FKs; no curation/discovery scope columns were found. Prompt85 added the missing direct scope columns and minimal parent anchors. The real curation/discovery workflows and old-row PostgreSQL migration checks remain pending.

### ADDENDUM-GAP-03 — Live evidence prerequisites are absent
No approved source snapshots/rights, independent audit reviewer, controlled model ground truth or audit key custody were found. Fixture evidence can test contracts but cannot satisfy live pilot/calibration gates. Continue local implementation and prepare exact bounded dry-runs.

### ADDENDUM-GAP-04 — Historical identifier families are not present in this checkout
The addendum says to preserve DREQ/DWP/DXE and AREQ/AWP/AE2E identifiers. Repository-wide Markdown search found the original REQ/WP/E2E families in the requirements matrix and source specs, but no DREQ/DWP/DXE or AREQ/AWP/AE2E entries. Preserve the existing IDs verbatim; do not invent missing historical identifiers. If their source release is supplied later, add a hash and map it without rewriting current history.

### ADDENDUM-DECISION-05 — Catalog state is separate from import capability
Prompt84 records every §5 family, but unsupported/unpinned entries stay `metadata_only`, `catalogued` or `blocked`. The planner blocks current runs because rights/import adapters and source approvals are absent. Three pilot family metadata records do not imply importability.

### ADDENDUM-DECISION-06 — Source prices and corpus sizes are unknown inputs
The planner computes bounded query/candidate/storage ceilings but reports monetary cost as null and source access as blocked. No price or corpus-volume estimate is inferred from public availability or the candidate ceiling.

### ADDENDUM-DECISION-07 — Local planner storage ceiling is provisional
The 512 MiB cap prevents a dry-run plan from claiming unbounded local storage, but it is not based on a measured corpus workload. It cannot authorize a scan; a real plan needs an approved, workload-specific cap and measured bytes.

### ADDENDUM-DECISION-08 — Missing queue parents use minimal scope anchors
The checkout had no curation-round or discovery-search tables despite the six-scope contract. Prompt85 creates minimal state/version parent rows to make the database FKs enforceable. Those rows do not imply that curation or discovery workflows exist; later prompts own those workflows.

### ADDENDUM-DECISION-09 — Audit dispatch authorization remains closed by default
New audit runs start with `dispatch_authorized=false`; no operator API or ordinary worker can flip that bit. Audit claims and diagnostic model calls require an authorized scanning run. The DB migration defaults old worker registrations to `audit_capable=false`; operational drain and enablement are pending live queue deployment procedures.

### ADDENDUM-DECISION-10 — Diagnostic spend requires a frozen plan cap
An audit-run budget account uses `max_diagnostic_cost_micro_usd` from the immutable audit plan and defaults to zero. A diagnostic child run must provide explicit cost/token/endpoint caps and cannot exceed that frozen account limit. No model dispatch occurred.

### ADDENDUM-DECISION-11 — Recommendation exclusion cannot be verified in this checkout
Repository search found no default language recommendation query/surface. Diagnostic runs retain `purpose` and audit references, while old run purposes remain `NULL`; Prompt85 records BREQ-28/BX-05 as partial until an actual recommendation surface can exclude them and be tested.

### ADDENDUM-DECISION-12 — Prompt85 downgrade refuses any non-empty audit history
The downgrade checks audit documents, new-scope queue rows, curation/discovery anchor rows, non-null new run purposes and audit-capable workers before dropping the extension. It is intentionally online-only and refuses destructive rollback when new data exists.
