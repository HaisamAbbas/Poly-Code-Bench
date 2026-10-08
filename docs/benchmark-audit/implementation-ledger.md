# Benchmark audit implementation ledger

Status: active. Prompts83-84 are complete; Prompts85-97 are partial foundations; Prompt98 is next. This ledger describes the actual worktree and never treats metadata or fixtures as live audit evidence.

## Repository inventory at Prompt83

- Python modular monolith under `packages/*`; Next.js/TypeScript frontend under `apps/web`.
- Canonical JSON helpers exist at `packages/core/src/polycodebench_core/canonical.py`; §7 audit evidence-document schemas/persistence are not implemented. Prompt84 adds separate catalog/planning contracts only.
- Persistence/migrations live in `packages/persistence/src/polycodebench_persistence`. The tracked `stage_job` has attempt, evaluation and release FKs only, versus six scopes in addendum §8.
- Fenced queue definitions/repository are `packages/core/src/polycodebench_core/jobs.py` and `packages/persistence/src/polycodebench_persistence/jobs.py`.
- API routes mount in `packages/api/src/polycodebench_api/app.py`; public release/submission routes have no benchmark audit operations.
- Tracked benchmark importers exist for bounded local inputs; Prompt88 adds contract-only corpus connector plans, with no live fetch/query runtime. Match review, risk/temporal assessment, sealing, monitoring, health and audit attestations remain later work.
- Prompt84 added strict registry/capability contracts at `packages/core/src/polycodebench_core/benchmark_audit_registry.py`, safe duplicate-rejecting YAML loading and a no-dispatch resource planner at `packages/services/src/polycodebench_services/benchmark_audit_catalog.py`, plus versioned configuration in `config/benchmark-audit/`. The catalog has 25 §5 families, eight source policies and a capability row for each family. At that point no importer or connector was claimed; Prompt86 later added bounded local benchmark imports and Prompt88 added contract-only source plans.
- The worktree contains uncommitted task generation/screening/overlap/exposure/canary code under `packages/taskgen` and contamination-control docs. These are inspected as work in progress and are excluded from Prompt83 commits.
- Native benchmark metrics and code scoring remain in the existing evaluation/scoring/plugin paths; audit health is a separate module and must not change them.

## Contract reuse

| Contract | Current evidence | Audit implication |
|---|---|---|
| Canonical JSON | `polycodebench_core.canonical` | Extend/reuse; no duplicate digest profile. |
| Durable queue | core jobs, persistence jobs and Alembic migrations | Add explicit audit scope, fencing, idempotency and reservations. |
| Immutable evidence | persistence artifacts/object store and release manifests | Reuse SHA-256 refs/visibility; add strict audit payloads. |
| Curation/exposure | task admission plus uncommitted taskgen | Inspect before reuse; no persisted benchmark audit contract exists. |
| Gateway/accounting | orchestration gateway and persistence call ledger | Diagnostics use ordinary accounting and stay out of ordinary recommendations. |
| Authorization | API identity/auth and service RBAC | Derive tenant from authenticated identity; public routes never dispatch work. |
| Frontend | `apps/web`, with local rules in `apps/web/AGENTS.md` | Follow its Next.js-version-specific documentation before UI edits. |

## Exact-byte source bridge

The addendum §1.1 says five documents were read but names/hashes only three and leaves a blank row. The published three hashes match exact current bytes. The two related repository implementation supplements below are not asserted to be in the source bridge; hashes are recorded to preserve their input identity.

| Source | SHA-256 | Authority |
|---|---|---|
| `PolyCodeBench-Architecture-v1.md` | `6dfee84b9787f315d7d5aa7afd9b1de29760aac16b5d3ac887b7ecd76d899d69` | addendum §1.1 |
| `PolyCodeBench-Technical-Implementation-Spec-v1.md` | `2dbfd0f00c561b9348419a2659794913658fb47d15090e09a3e905a89cdea7bd` | addendum §1.1 |
| `PolyCodeBench-Codex-End-to-End-Prompt-Pack-v1.md` | `0687c2e1e6340fd3b0f69b553418c290aeb59aa7cdf3a88b9d2ef3159c7e1344` | addendum §1.1 |
| `docs/implementation/requirements-matrix.md` | `35092955bddeee000da5af886968b821f939f7af18505226252ce1830ba26122` | supplemental contract |
| `docs/implementation/tickets.md` | `45b2bcef28fcd359471e157711075e2e619c72f7b83c1f7448cfc38dc4efa3c4` | supplemental contract |

Hashes computed from exact current bytes on 2026-10-08. No source document was edited.

The existing checkout contains REQ/WP/E2E identifiers in the source specifications and `docs/implementation/requirements-matrix.md`; repository-wide Markdown search did not find the named DREQ/DWP/DXE or AREQ/AWP/AE2E families. Their absence is recorded as ADDENDUM-GAP-04 rather than repaired by inventing IDs.

## Prerequisites and blockers

| Capability | State | Exact evidence/unblock action |
|---|---|---|
| HumanEval/MBPP/SWE-bench inputs | Repository metadata pinned for HumanEval, MBPP, SWE-bench and SWE-bench Verified; item bytes/splits not imported | Confirm exact immutable dataset bytes/splits and authorized local snapshots; gated access stays blocked. |
| Corpus coverage | No approved benchmark-audit snapshots found | Owner-approved finite scopes, rights/retention, snapshots and request caps. |
| Human review | No assigned audit reviewer evidence found | Independent authorized reviewer and recorded review events. |
| Model diagnostics | No approved target/reference plan found | Purpose, exact model context, endpoint, labels and budget; otherwise blocked. |
| Timestamp authority | No audit receipt provider configured | Approved independently verifiable receipt/key or local-only claim. |
| Seal key custody | No audit envelope-encryption/KMS workflow evidenced | Approved key manager, access/rotation and restore plan. |
| Agent/image modalities | No conformant audit adapters found | Authorized exact assets and modality-specific parser/runtime. |

Independent CPU/local work continues around these gates. The exact historical baselines and queue discrepancy are in `decisions.md`.

## Prompt84 / BWP-02

- Complete: 25 §5 benchmark records with explicit version/split/access/rights/modality/status fields; eight finite source policies; 25 component/source/modality/runtime capability rows; strict catalog cross-reference validation; safe duplicate-key YAML loading; and a pure bounded planner.
- The planner computes the proposed 300-task/8-source/5-stage ceiling (12,000 planned query units and 30,000 candidate slots) while returning `dispatch_allowed=false`, zero model calls, unknown prices and per-benchmark/source blockers.
- Current source checks pin metadata only: HumanEval, MBPP, SWE-bench repository, SWE-bench Verified dataset revision, EvalPlus v0.3.1 and its two data version labels. No task payloads were downloaded. All source connectors remain unimplemented and corpus scopes unapproved.
- Prompt85 reconciled the tracked three-scope queue with the six-scope requirement through a staged migration; details and remaining live checks follow.

## Prompt85 / BWP-03

- Added strict, immutable Python payload contracts for all 18 §7 document kinds, explicit enum/state transitions, typed document/artifact refs, fixed-string decimal/null-reason rules, UTC precision checks, strict duplicate/float/unsafe-integer rejection and semantic digests that exclude row identity/operational metadata.
- Added 18 shared canonical document vectors consumed by Python and TypeScript tests. These prove byte/digest agreement; TypeScript does not independently implement the kind-specific Pydantic payload schemas.
- Added immutable audit-document persistence with same-kind successor FK enforcement, relational registry/item/corpus/run/query/checkpoint/match/risk/temporal tables, logical uniqueness, scope reservations and a guarded Alembic migration. Historical `run.purpose` stays `NULL`; rollback refuses to discard audit data, new scope rows, populated curation anchors, assigned diagnostic purposes or audit-capable worker registrations.
- Extended `stage_job` to exclusive attempt/evaluation/release/curation_round/discovery_search/audit_run FKs. The checkout had only the first three scopes. The migration creates minimal curation/discovery parent anchors; their workflows remain outside this prompt. Audit queue claims require `dispatch_authorized=true`; migrated worker registrations default to `audit_capable=false`.
- Added `run.purpose` / `run.audit_run_id`, bounded diagnostic run validation, audit-run budget parenting and explicit diagnostic audit metadata on ordinary attempt-scoped call intents. The audit reference is separate from the call’s authoritative attempt FK. The audit budget comes only from a frozen plan diagnostic cap and defaults to zero.
- Four Prompt84 catalog YAML files contained a literal `\\n` line at EOF. Prompt85 regression checks exposed and removed these invalid lines.
- Partial, not complete: no PostgreSQL migration/integration database is configured; the repository has no language recommendation query to verify diagnostic exclusion; no API/role currently grants dispatch authorization, so audit execution remains closed by default; old-worker drain and six-scope runtime/fence recovery are not verified against a live queue. No source/model calls were made.
- Exact next prompt: Prompt87 / BWP-05; continue with local exact/lexical fingerprints and keep semantic embeddings gated on approved configuration.

## Prompt86 / BWP-04

- Added pinned HumanEval, MBPP original/sanitized and SWE-bench Verified import plans with deterministic 100-ID membership, exact source/parser digests, self-source exposure, independent-duplicate exclusion and explicit source/child lineage.
- Added local JSONL/JSON parsers with bounded input, record, component and ZIP expansion; they reject unsafe members and never fetch, execute official harnesses, evaluate task code or run source scripts. HumanEval, MBPP and SWE Verified component adapters are fixture-tested only.
- Added atomic immutable external import persistence with verified artifact bindings and private/restricted storage, membership/lineage schema, guarded migration and import-specific grants. External benchmark tasks do not receive native task-version IDs.
- Partial, not complete: no approved source bytes or item-rights artifacts exist; registry states remain blocked; no PostgreSQL migration/integration run occurred; the SWE Verified adapter consumes only a locally provided JSONL export (not native Parquet); and parsers are not isolated in resource-constrained workers. No official harness, external data or network fetch was used.
- Exact next prompt: Prompt87 / BWP-05, starting with local exact/lexical fingerprints; semantic embeddings remain blocked pending approved pinned configuration.

## Prompt87 / BWP-05

- Added a versioned local configuration and deterministic exact-byte, conservative normalized-text and literal-preserving token-shingle fingerprints per component. Configuration and feature digests are included in private artifact payloads; payload contracts reject source text or token strings.
- Added persistence over the existing append-only `fingerprint` rows. A guarded migration requires every row to reference a verified hidden/internal artifact and refuses downgrade while fingerprint history exists.
- Partial, not complete: there is no approved embedding/model/tokenizer config, Python/Java parser config, semantic/entity extractor or sealed-commitment key custody. No embedding, AST, entity, reasoning, vector or commitment feature is claimed. PostgreSQL integration, private artifact upload and index rebuild are unverified.
- Exact next prompt at that point: Prompt88 / BWP-06. Implement local connector contracts/planning while keeping live fetch/query blocked until rights, access and budgets are approved.

## Prompt88 / BWP-06

- Added strict capability, request, plan, execution-observation, coverage and optional-index query metadata contracts for all eight source groups. The contracts distinguish Common Crawl URL-index metadata from acquired WARC content and extracted text; preserve separate metadata/content/extraction/date/rights dimensions; and keep Data Portraits and infini-gram results candidate-only with no closed-model training-membership claim.
- Added exact HTTPS host/path validation, policy-bound request and response ceilings, global byte and timeout caps, a minimum one-second request interval, retry limits and an ordered per-attempt timing/byte ledger. Current policy lacks verified finite-scope/revision manifests, an authorization verifier, approved credential store, append-only remote-exposure event writer and fetch/query runtime, so every plan stays blocked and execution records zero I/O.
- Reused Prompt85's immutable `CorpusSnapshotPayload`, `corpus_source`, `corpus_snapshot` and `corpus_document` contracts/tables; Prompt88 did not add a snapshot persistence adapter, derived-index builder, extraction-manifest writer or measured resource plan. No source fixtures or approved source bytes were available for connector conformance.
- Partial, not complete: all eight policy rows remain `not_approved` / `not_implemented` / `not_run`; no benchmark/GitHub/Hugging Face initial source scope, rights evidence, private query verifier, Data Portraits sketch or infini-gram index is configured. No network fetch/query or DB write occurred.
- Exact next prompt at that point: Prompt89 / BWP-07. Continue with a bounded local retrieval planner and replay contracts if they can remain independently testable; actual retrieval awaits approved immutable corpus snapshots and derived indexes.

## Prompt89 / BWP-07

- Added strict immutable contracts for fixed exact/normalized, lexical/code and semantic stage pins; query-unit denominators; source/index references; selection limits; query outcomes; coverage manifests; cache identity; and replay results. Query-unit identity includes the full component fingerprint reference.
- Added a local deterministic selector for already-produced candidate hits. It validates frozen task/component/source/revision/stage scope, deduplicates repeated stage hits, applies stable stage/rank/source/seed ordering, caps at 20 per source and 100 per task, and preserves observed, unique, retained and discarded counts/digests.
- Added coverage reconciliation so every component/source/stage unit appears exactly once. No-match requires an attempted finite query and a result digest; failures, truncation and unsupported/blocked units cannot become complete coverage. Cache identity includes tenant and permission scope, task/component, snapshot/index/method/query, stage, seed, limits and selection-rule version.
- Replay recomputes selection only from supplied stored candidate hits. Missing or changed input reports missing/mismatch; no web fetch is possible. Cache/replay are pure contracts and helpers, not a persisted cache or artifact store.
- Partial, not complete: every production plan remains blocked because approved snapshots/rights/indexes, source artifact verification and an index runtime are unavailable. No fenced query reservation/checkpoint/result writer or durable replay artifact lookup was implemented; no live retrieval, source I/O or database operation occurred. Synthetic provided pins are confined to pure selection tests and are not approval evidence.
- Exact next prompt: Prompt90 / BWP-08. Implement evidence-verification and review contracts only where they can be tested without substituting synthetic candidates for approved source evidence; otherwise record the specific blocked gate and continue independent authorized work.

## Prompt90 / BWP-08

- Added a second `match_evidence` schema version while preserving and testing the existing v1 canonical vectors. Canonical envelopes now support schema versions 1 and 2 in Python and TypeScript with the same canonical JSON ordering rules.
- V2 match evidence binds its task/component refs, target benchmark, Prompt89 retrieval plan/result/candidate digests, source snapshot/revision/content artifact, source lineage, byte-offset spans, answer relationship, date evidence, rights refs, normalizer/rubric versions, reviewers and counterevidence. V1 match evidence remains parseable and byte-stable.
- Added a content verifier that checks the frozen plan/candidate/result binding, source and component artifact hashes, byte-span offsets/digests and exact or pinned NFC/LF equality. It always returns `source_trust=unverified`, `rights_trust=unverified` and `accepted_evidence=false`; no caller-supplied authorization flag can upgrade its result. Self-imports are excluded and boilerplate/mixed labels remain review-gated.
- Added a frozen relation rubric with separate exact, near, semantic, family, concept, no-match and unresolved meanings. Semantic and ambiguous matches require human review; concept-only evidence contributes zero; exact auto-accept is disabled without a calibrated policy. Judge packet contracts label source spans untrusted, expose no tools and grant proposal-only authority.
- Added immutable human opinion/adjudication ledgers. Author self-review is rejected, conflicting accepted/rejected opinions remain present in disputed state, and adjudication requires an independent third subject. Corrections create a newer evidence document with `supersedes_id` plus an immutable correction record; the prior document remains unchanged.
- Partial, not complete: source artifact reading is supplied as bytes to a digest/span verifier, but no approved source snapshot/rights resolver, trusted artifact-store adapter, persistent candidate/review/adjudication writer, audit API, database integration, approved match judge/model context or model exposure writer exists. Pure fixture candidates and opinions do not close live source or human-review gates.
- Exact next prompt: Prompt91 / BWP-09. Implement the pure observed-risk policy and missingness gates over these candidate evidence contracts; do not treat content integrity as verified source provenance or accepted human evidence.

## Prompt91 / BWP-09

- Added immutable schema-v2 risk policy and assessment documents while preserving the v1 vectors. A reversible migration permits document schema versions 1 and 2; downgrade checks for v2 rows before restoring the v1-only constraint.
- Added the frozen observed-risk-v1 formula `50*M + 25*C + 15*E + 10*L`, exact weights and thresholds, all eight signal descriptors, applicability, missingness policy, calibration status and claim restriction. Publication age retains a raw day interval; popularity retains a timestamped count descriptor; neither contributes points. Model familiarity is a separate behavioral diagnostic. The training-likelihood signal is named corpus/source overlap evidence.
- Added typed signal observations that require pinned configuration, observation time and evidence, and require verified source/rights plus an independent human reviewer before a positive signal is accepted. Exact/semantic, question-only, distinctive-solution, family and concept/boilerplate strengths map to 1.0, 0.6, 0.8, 0.3 and 0.0; concept/boilerplate and family remain distinct. Correlated observations use the maximum within each weighted group.
- Added a pure Decimal aggregator. Complete reviewed finite scope can produce observed zero; unknown, failed, blocked, truncated or unresolved review remains missing. Bounds add the full weight of each incomplete component. Low/medium tiers require both complete scope and a policy marked calibrated; a known lower bound at least 60 can display high observed risk with bounds. Fixed claim wording cannot describe the index as probability or a cleanliness guarantee.
- Partial, not complete: every score fixture is synthetic, no independent calibration sample or approved corpus/source scope exists, accepted evidence references are not resolved by a trusted repository, and the assessment has no persistent writer/API/report integration. The migration only rendered offline; PostgreSQL was not available. It is not verified that live `audit_document` rows satisfy the new check.
- Exact next prompt: Prompt92 / BWP-10. Implement temporal holdouts/model-context contracts while retaining unknown cutoff and source chronology as explicit missingness; no live model/source claim is available.

## Prompt92 / BWP-10

- Added strict `model_context` v1 and temporal-assessment v2 contracts while preserving temporal-assessment v1 and all existing canonical vectors. Contexts record provider, alias/revision/weight digest and pin confidence, cutoff interval/source/confidence, model updates, retrieval/tool policies, prior delivery references and audit time. Temporal assessments freeze and digest-bind the exact context snapshot they evaluate.
- Added chronology interval evidence with precision, derivation and distinct owner/git/download/archive/trusted-receipt/local-receipt/provider/upstream bases. Archive and timestamp receipts provide upper bounds only; they cannot be recorded as public exposure. Earlier verified upstream exposure remains effective even when a later benchmark release is present.
- Added a pure interval evaluator and assessment validator for pre-cutoff exposure, post-declared-cutoff, overlap, unknown source/cutoff and mutable context. Unknown or mutable model identity/cutoff, incomplete source intervals, unverified potentially earlier claims and model updates not proven before cutoff cannot receive a post-cutoff result. All classifications carry explicit claim qualifiers and never prove training or originality.
- Added canonical salted SHA-256 hiding commitments using a 32-byte cryptographic nonce, private nonce artifact binding, local Ed25519 signed receipts with a fixed non-independent label, token-artifact digest checks and an explicit provider/version/protocol allowlist for replaceable trusted timestamp adapters. A verified receipt binds the commitment and provider token bytes to a recorded time only; there is no provider implementation configured in this environment.
- Added audit-document kind migration with guarded downgrade, service dependency metadata, model-context canonical vector, temporal evaluator/crypto tests and acceptance traceability. The migration rendered offline in both directions; no database connection was made.
- Partial, not complete: all chronology/model/cryptographic tests use local fixtures; there is no approved upstream source, provider model identity/cutoff evidence, external TSA adapter/trust root, audit persistence writer or public projection. No claim about real model training, source originality or actual exposure is supported.
- Exact next prompt: Prompt94 / BWP-12. Implement optional behavioral diagnostic protocols and applicability; keep all model calls gated on frozen plans, approved access and ordinary gateway accounting.

## Prompt93 / BWP-11

- Added per-artifact AES-256-GCM encryption with fresh 32-byte DEKs and 12-byte nonces, tenant/artifact/media-type AAD, and AES-KWP envelope wrapping behind a `DataKeyProvider` boundary. The bundled adapter is explicitly local-development-only, requires a caller-provided key and explicit opt-in, and cannot be used as an approved production KMS.
- Added v2 sealed manifests binding one encrypted artifact, private wrapped-key and P92 hiding-commitment refs, provider/version/recovery metadata and append-only access-event refs. Tampering, changed storage digests/media types, tenant mismatch and cross-artifact substitution fail closed. Key rotation rewraps the DEK while preserving ciphertext and task identity.
- Added injected authorization and persistence boundaries for scoped decrypt/local screening, candidate delivery, public disclosure and remote query dispatch. Exact authorization ref, recipient and payload digest are recorded; event and successor manifest are committed atomically before plaintext is handed to a local worker or remote callback. Persistence enforces one linear manifest successor and monotonic disclosure state.
- Added 256-bit synthetic canary generation, exact local collision checks, encrypted private marker storage, strict policy/observation documents and source/date/review validation. External query observations bind the marker, exact query digest and authorized query event. No-hit observations explicitly do not imply clean status; hits do not prove training inclusion.
- Partial, not complete: no approved KMS/key-custody adapter, production authorization verifier, artifact-store writer, PostgreSQL database, approved source snapshot or independent human date/review evidence is configured. Migration DDL only rendered offline; no external source/model request or production database write occurred. Canary tests use synthetic bytes only.
- Specification discrepancy: §1.1 says five source Markdown files were read, but lists and hashes three and leaves the remaining source rows blank. The three published hashes match exact current bytes; no absent sources were invented. Prompt93 also depends on an approved key system, which is absent from this checkout and recorded as a production blocker.
- Exact next prompt: Prompt94 / BWP-12. Implement optional behavioral diagnostic protocols and applicability without issuing model calls until approved plans, access, budgets and gateway accounting are present.

## Prompt94 / BWP-12

- Added strict behavioral method registry and task-validity records, plus schema-v2 frozen experiment plans. Plans bind the method/version, original/control/family split, independent semantic/difficulty evidence, target/reference model contexts, private prompt/tool/decoding/grading artifacts, exact statistical test, alpha, effect, target power, bootstrap resamples, multiplicity, decision rule, exposure policy and separate model/training caps.
- Registered ConStat against its published source and comparison boundary while keeping its implementation `not_pinned`. No uncalibrated high-accuracy detector, prose-derived likelihood, training-inclusion claim, or universal probability is emitted. Applicability checks require verified adapter capabilities and accepted validity evidence.
- Added descriptive outcome reconciliation for every planned task/model unit, including negative findings, failed/blocked/not-run results, private response artifacts, per-retry access events, gateway usage, cost and tokens. Assessment reports insufficient independent families, unavailable power analysis, unaccounted controlled-training compute and calibration blockers explicitly.
- Persistence binds observations to pre-dispatch audit runs, actual task rows, exact pinned model revisions, the frozen decoding config, private request/response artifacts, ordinary gateway intents/deliveries, matching sealed recipient/payload events and latest usage settlements. Migration `a194d6c3e781` adds the four document kinds, unique observation roots and linear successors for ambiguity resolution, and a data-preserving guarded downgrade.
- Partial, not complete: ConStat has no pinned executable adapter; there are no approved model contexts/capabilities, production exposure writer/KMS, audit database, independent live control review, owned authorized training manifest, accounted training compute, or validated power/calibration dataset. All behavioral tests are synthetic. No model/source call or database write occurred; migration SQL was rendered offline.
- Specification discrepancy: §1.1 says five source Markdown files were read, but lists/hashes three. This discrepancy remains as recorded in Prompt93.
- Commit: `6d8b4d6`.
- Exact next prompt: Prompt95 / BWP-13. Implement firewall admission and independently validated replacements while preserving official versions and requiring complete scoped evidence, rights, validity, lineage and independent review.


## Prompt95 / BWP-13

- Added strict v2 firewall policy/decision contracts, exact finite scope outcomes and fail-closed reduction. A complete no-match scope is insufficient without accepted task validity, rights, calibrated risk, required temporal review and a separate final reviewer; prohibited overlap, invalidity and denied rights reject. Persistence binds no-match outcomes to completed exact-scope queries and refuses unresolved or accepted candidates from being called no-match.
- Added reviewed source-family metadata, bounded replacement plans, frozen private configs/difficulty/exposure policy, source quotas consumed under a per-plan lock and distinct author/checker/final reviewer requirements. Replacement validation binds task sources to approved source IDs, enforces total/per-source draft caps under a locked plan row, ties exposure status to the complete task-specific firewall scope, and requires trusted production-worker reports, exact task/package/runtime digests, private test/oracle artifacts, approved rights and recorded family ancestry. Transformed tasks retain source-family cluster lineage; prospective tasks require independent lineage and accepted template review.
- Added immutable derived manifests that account for every official item, preserve imported source membership, record retain/replace/exclude dispositions, private oracle mappings and validation refs, competency/difficulty mappings, private sampling policy, computed family/split/competency/difficulty distributions, derived membership digest and distinct official/derived score labels with automatic comparison disabled. Family assignments cannot cross splits within a manifest or across split manifests for the same registry/version.
- Added document-kind and append-only successor checks plus unique policy/scope/decision/validation/derived-version indexes in migration `d4f7b2a196c3`; downgrade refuses to remove persisted Prompt95 evidence. Added shared canonical vectors and focused decision/contract regressions.
- Partial, not complete: this checkout has no production-worker admission evidence, authorized source/rights verifier, trusted reviewer identity/role service, approved benchmark/corpus bytes, task-generation/lineage service, PostgreSQL integration database or production derived-set distribution/reporting path. Production persistence checks are static/offline only; fixtures are synthetic and no source, model or database operation occurred.
- Exact next prompt: Prompt96 / BWP-14, continuous monitoring, risk changes and owner alerts.


## Prompt96 / BWP-14

- Added strict monitor-policy schema v2 for exact private audit-plan/task/source scope, independent owner/approver subjects, private approval artifact, IANA timezone and cadence, stale/full-refresh intervals, query/storage/cost caps, source/day rate limits, bounded retries/catch-up and frozen in-app recipients. External delivery is structurally disabled and target-model diagnostic work/cost is zero. Legacy v1 policies remain parseable but cannot reserve work.
- Added daily/weekly slot planning with DST-safe local time resolution, deterministic slot keys, bounded historical catch-up and retry ceilings. Incremental refresh plans select only changed approved corpus snapshots; full refresh plans select the entire approved source set. Query reservations include every allowed retry.
- Added PostgreSQL monitor-slot persistence with a policy-row lock, `(policy, slot)` idempotency and atomic per-source/local-day query reservations. Storage/cost/query caps are checked at reservation time. Dispatched failures can persist controlled retry state and append audit events only when the linked audit run was already explicitly authorized. A retry crossing its source-local quota day closes terminally instead of spending against another day's reservation.
- Added reference-only alert schema, deterministic evidence-based dedupe, frozen task/source/plan scope checks, accepted v2 evidence and successor-risk checks, comparable-score checks, distinct outage/staleness/dispute/correction/discontinuity/seal event types and transactionally created in-app inbox rows. Source documents remain immutable and have no delete path.
- Added monitor policy/alert indexes, slot/source/inbox tables and guarded migration `e5c7b2a94d10`; added DST, catch-up, refresh, retry, strict-contract and dedupe coverage plus a shared canonical alert vector.
- Partial, not complete: trusted owner/approver roles, authenticated enable/pause API, production scheduler, approved source adapters, live reviewer/source history, authenticated inbox UI, PostgreSQL concurrency/recovery and production evidence are not available. No scan or external notification was enabled; offline DDL rendering and synthetic fixtures do not close those gates.
- No new Prompt96 specification discrepancy. The Prompt93 source-input discrepancy remains: §1.1 describes five source Markdown files but lists/hashes three.
- Exact next prompt: Prompt97 / BWP-15, benchmark health aggregation and comparable trends.

## Prompt97 / BWP-15

- Added versioned benchmark-health v2 documents while retaining historical v1 parsing and refusing new v1 writes. The frozen scope binds exact benchmark membership, plan, risk policy, optional model context, sorted source snapshots, source window, census/sample method and task-set digest, family-map digest/private artifact, scan methods, and versioned provenance/freshness thresholds.
- Added denominator-first health counts and Decimal-only six-place half-even percentages, null reasons, explicit unknown/lower-bound counts, risk-tier reconciliation, exact/semantic/union duplicate prevalence, contextual pre-cutoff exposure, planned/completed/failed/truncated/blocked/unknown coverage, provenance/freshness, eligible mean observed risk, and detector precision/recall/FPR/FNR with strata and Wilson intervals. Samples remain descriptive; no extrapolation or cleanliness verdict is produced.
- Added immutable trend points with explicit discontinuity reasons for membership, sampling, policy, model context, source set/window, scan method, family mapping, provenance/freshness definitions and metric version. Persistence binds health scope to stored plan/snapshot/policy/context and task assessment refs, validates accepted match evidence against exact task/benchmark/plan/source scope, reconciles tier/state/mean/duplicate numerators, checks temporal/coverage references and linked trend breaks, and forbids health successors.
- Added append-only health-kind migration `f67a3d91c4b2`, a cross-runtime canonical v2 vector, and section 18 goldens for the 10,000-task tiers, 200 unscanned tasks, overlap deduplication, 2.1% pre-cutoff, 40/50 coverage, empty denominators, half-even means, family grouping and detector unknowns.
- Partial, not complete: tests use synthetic inputs; no approved benchmark/corpus source scope, live query coverage, temporal exposure source, PostgreSQL integration, independent detector labels, or authenticated health projection is available. Migration SQL was rendered offline only. Health outputs remain descriptive and do not change native benchmark, code-quality, ranking, or behavior-statistic contracts.
- No new Prompt97 specification discrepancy. The Prompt93 source-input discrepancy remains: section 1.1 describes five source Markdown files but lists and hashes three.
- Exact next prompt: Prompt98 / BWP-16, private API, CLI, SDK and permission contracts.
