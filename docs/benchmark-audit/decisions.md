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

### ADDENDUM-DECISION-13 — Imported benchmark bytes remain private by default

Upstream source visibility is evidence metadata and does not determine storage ACLs. Imported raw sources, records and components are private or restricted; rights evidence must be verified and non-public before an import can persist. Public source URLs remain references, not public artifact permissions.

### ADDENDUM-GAP-05 — SWE-bench Verified local export format

The initial adapter accepts a pre-approved local JSONL export for the pinned SWE-bench Verified revision. It does not read the repository's native Parquet payload or execute dataset scripts. Before using transformed data, bind an approved, versioned conversion manifest and its input/output digests to the import evidence. No conversion or source payload is present in this checkout.

### ADDENDUM-DECISION-14 — Conservative fingerprint normalization

The initial exact/lexical fingerprint config normalizes only CRLF/CR newlines and Unicode NFC. It preserves other whitespace, comments, case, identifiers, operators, numeric values, types and constraints. Normalized and shingle matches remain separate candidate features; they are not byte-exact or duplicate decisions.

### ADDENDUM-DECISION-15 — Parser, semantic and commitment methods require approved configuration

No local embedding/model/tokenizer configuration, Python/Java parser approval or crypto key custody is present. AST, embedding, entity/reasoning extraction and sealed hiding commitments stay blocked or unsupported. Exact/lexical private artifacts carry their complete local configuration and digest; no substitute model or commitment scheme is introduced.

### ADDENDUM-DECISION-16 — Source connector contracts are not live connectors

Prompt88 defines all eight source capability profiles and validates bounded HTTPS egress plans, but no source has approved authorization, a verified finite-scope/revision manifest, a registered connector or live conformance. The service always blocks dispatch, even if local policy fields are edited to look approved. Existing source-policy rows remain `not_approved`, `not_implemented` and `not_run`; fixtures do not change them.

### ADDENDUM-DECISION-17 — Caller-supplied digests do not verify authorization or exposure

A query-payload digest and authorization-evidence digest bind metadata only. This checkout has no persisted authorization verifier or append-only remote exposure event writer. Private remote queries therefore remain blocked and no delivery path can claim consent or exposure. Opaque credential UUIDs are also blocked until a trusted credential-scope verifier exists.

### ADDENDUM-DECISION-18 — Coverage completeness requires a recorded denominator

URL metadata, source content, extracted text, source dates and rights evidence are separate coverage dimensions. A completed request with no eligible-scope denominator is `unknown`, not complete or zero. Request-level observations bind attempts, response bytes, timing, retries and spacing to the frozen plan caps. Common Crawl URL-index metadata is not WARC/full-text coverage; arXiv publication dates do not date embedded tasks; Stack Exchange revisions and Wikipedia dump/page revisions remain distinct evidence.

### ADDENDUM-DECISION-19 — Optional index results remain candidate evidence

Data Portraits metadata must identify the sketch, corpus, method and error model. infini-gram metadata must identify the indexed corpus/revision and result positions. The typed contract records query/result digests, but no tool is configured or queried here. Approximate membership and index hits never establish a closed model's training membership.

### ADDENDUM-DECISION-20 — Candidate selection is fixed, bounded and recall-limited

Prompt89 selection deduplicates the same source revision/document/component candidate across stages, keeps its earliest stage and best rank, then orders by stage, rank, source group and a seed-derived identity digest. It retains at most 20 candidates per source group and 100 per task and records discarded counts. These caps limit recall; without a real index and controlled recall set, no recall quality is claimed.

### ADDENDUM-DECISION-21 — Query coverage and replay cannot infer successful searches

Coverage binds every frozen component/source/stage query unit to one explicit outcome. `no_match` requires an attempted finite query, zero observed candidates and a result digest. Failed, truncated, blocked and unsupported outcomes remain incomplete. Replay only uses supplied stored candidate-hit records; absent or changed records report missing/mismatch and never trigger a web fetch. No durable checkpoint/result writer or stored-artifact resolver exists in this checkout.

### ADDENDUM-DECISION-22 — Cache identity includes selection and privacy scope

The Prompt89 cache identity includes tenant and permission-scope digests, task/component/source/corpus/index/method/query identity, stage, seed, caps and selection-rule version. The helper only computes a digest; without a persisted permission verifier, fenced cache store and result writer it cannot authorize access or assert prior query coverage.

### ADDENDUM-DECISION-23 — Match evidence v2 preserves the canonical v1 history

Prompt90 adds a second `match_evidence` payload schema with an explicit document schema version2. Existing v1 payloads and canonical vectors stay unchanged. Canonical JSON key ordering is identical for envelope versions1 and2; the version selects the strict document payload contract. No prior evidence document is rewritten.

### ADDENDUM-DECISION-24 — Content integrity does not establish trusted source evidence

The Prompt90 verifier binds the stored retrieval plan/selection/candidate, source revision/content digest, component artifact digest and source byte offsets/spans. It can report content integrity only. Source authorization, rights, snapshot provenance and source-date authenticity remain `unverified`; the result hard-codes `accepted_evidence=false`. Self-imports are excluded. Boilerplate/mixed classifications require review, and no uncalibrated exact or semantic auto-accept is enabled.

### ADDENDUM-DECISION-25 — Human opinions and corrections append without erasure

Review opinions are distinct immutable records. Conflicting accept/reject opinions remain in disputed state until a third independent adjudicator cites all conflicting opinions. Corrected evidence is a newer document linked by `supersedes_id` with a separate correction record; the prior evidence and exposure history are retained. These are pure contracts/reducers until a persistent writer is implemented.

### ADDENDUM-DECISION-26 — Source instructions remain data and model output remains a proposal

Match judge packets label source spans `untrusted_source_data`, reject tools and fix verdict authority to `proposal_only`. Existing generic judge packets also isolate untrusted comments from evidence scope. No audit-specific model-context, fresh-session dispatch or exposure-event writer is available, so no judge call is made and no AI output can create an accepted finding here.
