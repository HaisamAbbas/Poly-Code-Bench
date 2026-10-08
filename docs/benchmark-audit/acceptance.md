# Benchmark audit traceability and acceptance ledger

The addendum §§3, 25–28 remains authoritative for exact requirement text and ticket/gate DoDs. This ledger assigns owners and records evidence without modifying those contracts.

Through Prompt102, BWP-01 and BWP-02 are complete; BWP-03 through BWP-20 are partial foundations pending live database/source/resource/reviewer/calibration and lifecycle evidence. BWP-21 through BWP-24 remain pending. Capability requirements remain partial or pending until their assigned import, evidence, lifecycle and live gates close.

## Requirements (BREQ)

| ID | Primary prompts | Acceptance gates | Status | Evidence owner |
|---|---|---|---|
| BREQ-01 | 84–86 | BX-02,06–08 | partial | reports/prompt-86.md; exact adapters and membership are implemented, but live imports and PostgreSQL retry checks are pending |
| BREQ-02 | 84,86,104 | BX-02,59 | partial | reports/prompt-104.md and source-observations-2026-10-09.md; all §5 rows now carry pinned/unresolved revision, split, access, rights, scope, runtime and live-state evidence; only GSM8K adds a synthetic parser fixture |
| BREQ-03 | 87 | BX-09–11 | partial | reports/prompt-87.md; deterministic exact/lexical views implemented, parser/semantic/entity features remain blocked |
| BREQ-04 | 84,88,104 | BX-12–14,59 | partial | reports/prompt-88.md and reports/prompt-104.md; eight bounded source policies and a 25-family scope report exist, while approved live connectors and source conformance remain absent |
| BREQ-05 | 88–89 | BX-13,15–17 | partial | reports/prompt-88.md and reports/prompt-89.md; bounded selection and coverage/replay contracts exist, but no approved corpus snapshots, derived indexes or durable query-result store are available |
| BREQ-06 | 90 | BX-18–21 | partial | reports/prompt-90.md; source/component/span verification, relation rubric and review/correction contracts exist; trusted artifact/rights verification and durable review history remain absent |
| BREQ-07 | 91 | BX-22–23 | partial | reports/prompt-91.md; exact decimal observed-risk formula, bounds, tiers and missingness gates are implemented and golden-tested; independent score-policy calibration and actual accepted source evidence remain absent |
| BREQ-08 | 91,94 | BX-24,31–33 | partial | reports/prompt-91.md and reports/prompt-94.md; all eight signals are typed, while behavior is kept diagnostic and age/popularity contextual; controlled behavior evidence and live exposure/corpus evidence remain absent |
| BREQ-09 | 92 | BX-25–26 | partial | reports/prompt-92.md; chronology intervals, immutable model contexts and interval evaluation are tested; no accepted live source/cutoff evidence exists |
| BREQ-10 | 92–93 | BX-27–28 | partial | reports/prompt-92.md; salted commitments and local Ed25519 receipt binding are tested; no approved external timestamp authority is configured |
| BREQ-11 | 93,102 | BX-28–29,54 | partial | reports/prompt-93.md and reports/prompt-102.md; private envelope/access contracts and reference-only seal campaign accounting exist, but approved production keys, live authorization and six real disclosures are unavailable |
| BREQ-12 | 93,102 | BX-30,54 | partial | reports/prompt-93.md and reports/prompt-102.md; local canary and mode-disclosure contracts exist, but no reviewed source/date evidence, production query adapter or live diagnostic configuration exists |
| BREQ-13 | 94,101 | BX-31–33,53 | partial | reports/prompt-94.md; opt-in preregistration and descriptive observations exist, while a pinned method, approved model capabilities and controlled calibration remain unavailable |
| BREQ-14 | 85,94 | BX-05,32–33 | partial | `run.purpose`, audit metadata, gateway linkage and frozen diagnostic protocols are implemented; live dispatch authorization and model exposure integrations remain gated; see reports/prompt-85.md and reports/prompt-94.md |
| BREQ-15 | 95 | BX-34 | partial | reports/prompt-95.md; finite-scope, validity, rights, temporal, risk and reviewed-overlap decisions are fail-closed; live authorized evidence is unavailable |
| BREQ-16 | 95,102 | BX-35,54 | partial | reports/prompt-95.md and reports/prompt-102.md; preregistered quotas and campaign round/cost/time reconciliation exist, but no authorized generation runner, human authors, production worker or live oracle evidence is available |
| BREQ-17 | 95,102 | BX-36,54 | partial | reports/prompt-95.md and reports/prompt-102.md; ancestry gates and candidate lineage summaries are persisted/checked, but no approved task-generation or live exposure-review pipeline is available |
| BREQ-18 | 86,95,97 | BX-08,37,42 | partial | reports/prompt-86.md and reports/prompt-95.md; official membership remains immutable; derived manifests compute family/split/competency/difficulty distributions and separate score labels; live import and projection review remain unavailable |
| BREQ-19 | 96,102 | BX-38-40,55 | partial | reports/prompt-96.md and reports/prompt-102.md; bounded slot, usage and alert-history reconciliation contracts exist; owner-role verification, changed-source live rescan, scheduler/connectors and PostgreSQL evidence are unavailable |
| BREQ-20 | 90,96,100 | BX-21,39-40,49 | partial | reports/prompt-90.md and reports/prompt-96.md; immutable match successors, accepted-evidence successor-assessment alert checks, correction/dispute records and no-delete retention are implemented; live reviewer/source history and production alert delivery remain unavailable |
| BREQ-21 | 97,99 | BX-41-43,46 | partial | reports/prompt-97.md; denominator-first Decimal health metrics, explicit unknown/unscanned counts, frozen cohort keys and trend breaks are implemented; live source, database and reviewed projection evidence remain unavailable |
| BREQ-22 | 98–99 | BX-44–47 | partial | reports/prompt-98.md and reports/prompt-99.md; tenant-scoped API reads, plan/run create, CLI/SDK contracts and public aggregate report journeys are tested; private transition adapters, shared curator ACL, model-context/evidence views and live reviewed projection remain |
| BREQ-23 | 100 | BX-48–50 | partial | reports/prompt-100.md; canonical Ed25519 public claims, document digest binding, stale/offline qualification, lifecycle reducer, and correction links pass synthetic checks; approved signer/reviewer authority, timestamp proof, production trust-store publication, and live database/revocation evidence remain unavailable |
| BREQ-24 | 86–90,93,98–100,103 | BX-07,11,14,20,28–30,44,47–50,58 | partial | reports/prompt-86.md through reports/prompt-90.md and reports/prompt-98.md through reports/prompt-103.md; untrusted source/judge boundaries, tenant/owner-scoped reads, allowlisted no-store projections, signed-claim privacy, restore-reference validation and diagnostic-message suppression are tested; private artifact-byte authorization, audit-specific exposure writer, shared reviewer ACL, live trust/revocation checks and production authorization evidence remain absent |
| BREQ-25 | 85,89,96,98,103 | BX-04–05,16–17,38,44,56–57 | partial | six exclusive queue FKs, CAS enqueue, tenant/idempotent plan/run API creation, fences and preserved call/reservation/monitor counts are implemented; Prompt89 has no fenced query-result/checkpoint writer; the available restore is pre-audit and the local test database has no migrations; see reports/prompt-85.md, reports/prompt-89.md, reports/prompt-98.md and reports/prompt-103.md |
| BREQ-26 | 94,101 | BX-31–33,52–53 | partial | reports/prompt-94.md and reports/prompt-101.md; owned-training manifests and separate compute caps are represented, but no authorized trained/untrained manifests or behavioral ground truth exist |
| BREQ-27 | 101–102 | BX-51–55 | partial | reports/prompt-101.md and reports/prompt-102.md; exact-scope calibration and reference-only replacement/seal/monitor accounting contracts exist, while source scans, independent labels, 12 reviewed replacements, six production seals and live rescan are unavailable |
| BREQ-28 | 85,97,103,105 | BX-05,42,58–59 | partial | native scoring paths were not changed; diagnostic exclusion from recommendation surfaces is unverified because no such query exists in this checkout; see prompt 85 report |
| BREQ-29 | 84,88–89,101,104 | BX-12–17,51,59 | partial | catalog, connectors, bounded retrieval, coverage, local replay, exact-source pilot preflight and all-family scope coverage exist; approved snapshots/indexes, actual scans and durable replay remain pending; see reports/prompt-88.md through reports/prompt-89.md, reports/prompt-101.md and reports/prompt-104.md |
| BREQ-30 | 103,106 | BX-56–58,60 | partial | reports/prompt-103.md; fail-closed restore checks, local malicious-input/privacy regressions and recovery/key/outage runbooks exist; current-schema restore, provider recovery and representative search/monitor capacity evidence remain blocked |
| BREQ-31 | 83,104–106 | BX-01,59–60 | partial | acceptance.md and reports/prompt-83.md; remaining prompts pending |
| BREQ-32 | 83 and every prompt,106 | BX-01,60 plus all96 ticket DoDs | partial | acceptance.md and reports/prompt-83.md; remaining prompts pending |

## Work packages (BWP)

| ID | Prompt | Status | Evidence |
|---|---|---|---|
| BWP-01 | 83 | complete | reports/prompt-83.md |
| BWP-02 | 84 | complete | reports/prompt-84.md |
| BWP-03 | 85 | partial | reports/prompt-85.md; relational migration/queue and diagnostics surfaces need live integration |
| BWP-04 | 86 | partial | reports/prompt-86.md; fixture-only adapters, no approved bytes/rights, DB integration or parser worker isolation |
| BWP-05 | 87 | partial | reports/prompt-87.md; exact/lexical fingerprints implemented; approved parser/model config, live persistence and semantic coverage pending |
| BWP-06 | 88 | partial | reports/prompt-88.md; bounded plan/coverage and optional-index metadata contracts exist; live connectors, immutable snapshot writes and index rebuild remain pending |
| BWP-07 | 89 | partial | reports/prompt-89.md; deterministic selection, cap/truncation, coverage, scoped cache identity and stored-hit replay contracts are tested; approved indexes and durable query recovery are absent |
| BWP-08 | 90 | partial | reports/prompt-90.md; versioned evidence/content verification, relation rubric, independent review/adjudication and successor correction contracts are tested; trusted source/rights resolution and persistent review history are absent |
| BWP-09 | 91 | partial | reports/prompt-91.md; versioned score/assessment contracts, eight signal descriptors, exact decimal aggregation and missingness bounds are tested; calibration, accepted live evidence, database execution and public projection remain pending |
| BWP-10 | 92 | partial | reports/prompt-92.md; model-context snapshots, chronology precedence, interval outcomes and commitment verification are implemented; live source/model/TSA evidence remains unavailable |
| BWP-11 | 93 | partial | reports/prompt-93.md; sealed access, monotonic history and private canary contracts are tested with local adapters; approved KMS, PostgreSQL integration and source review remain pending |
| BWP-12 | 94 | partial | reports/prompt-94.md; registry, frozen plans, descriptive reconciliation and calibration/power limits exist; no pinned method adapter, approved model access or owned-training calibration is available |
| BWP-13 | 95 | partial | reports/prompt-95.md; fail-closed firewall, bounded replacement contracts and derived manifests exist; production admission authority and live source/task evidence are unavailable |
| BWP-14 | 96 | partial | reports/prompt-96.md; bounded monitor policies, reservations, retries and verified in-app alert persistence exist; no trusted owner-role verifier, live scheduler/source adapter, or PostgreSQL integration is available |
| BWP-15 | 97 | partial | reports/prompt-97.md; descriptive health v2 contracts, Decimal goldens, sampling/missingness and trend-discontinuity rules are implemented; live source/database evidence and integrated projection review remain unavailable |
| BWP-16 | 98 | partial | reports/prompt-98.md; tenant-scoped API, CLI and generated SDK foundations pass local checks; transition adapters, shared reviewer ACL, private artifact-byte authorization and live service evidence remain |
| BWP-17 | 99 | partial | reports/prompt-99.md; public aggregate report and safe curator-blocked pages pass synthetic browser checks; private transitions, comparative/evidence journeys and live reviewed projection remain unavailable |
| BWP-18 | 100 | partial | reports/prompt-100.md; Ed25519 verification, canonical digest binding, stale/offline qualification, append-only lifecycle repository methods, public verification route and limitations UI are implemented; approved trust/key/reviewer authority, trusted timestamp proof, transition API, live PostgreSQL and reviewed publication remain unavailable |
| BWP-19 | 101 | partial | reports/prompt-101.md; frozen 300-item preflight, trusted population/plan/source/label resolver boundaries, family-cluster calibration and bounded resampling work exist; no approved source bytes/rights, independent labels, live scan or behavioral ground truth are available |
| BWP-20 | 102 | partial | reports/prompt-102.md; fixed-scope replacement/sealed/monitor evidence contracts and separate reported/verified usage counts exist; no authorized live campaign, production key/timestamp authority or source rescan is available |
| BWP-21 | 103 | pending | prompt report |
| BWP-22 | 104 | partial | reports/prompt-104.md; all §5 families reconcile metadata, scope, modality, access, runtime and blockers; only GSM8K adds a new synthetic-fixture parser; live rights/source conformance remain blocked |
| BWP-23 | 105 | pending | prompt report |
| BWP-24 | 106 | pending | prompt report |

## Engineering tickets (BAT)

Each prompt owns four tickets (A–D). The addendum retains each exact DoD.

| ID | Prompt | Status | Evidence owner |
|---|---|---|---|
| BAT-01-A | 83 | complete | prompt-83.md |
| BAT-01-B | 83 | complete | prompt-83.md |
| BAT-01-C | 83 | complete | prompt-83.md |
| BAT-01-D | 83 | complete | prompt-83.md |
| BAT-02-A | 84 | complete | reports/prompt-84.md |
| BAT-02-B | 84 | complete | reports/prompt-84.md |
| BAT-02-C | 84 | complete | reports/prompt-84.md |
| BAT-02-D | 84 | complete | reports/prompt-84.md |
| BAT-03-A | 85 | complete | reports/prompt-85.md; 18 Python schemas and shared Python/TypeScript canonical vectors |
| BAT-03-B | 85 | partial | reports/prompt-85.md; migration renders, but no old-row PostgreSQL upgrade/rollback run |
| BAT-03-C | 85 | partial | reports/prompt-85.md; atomic enqueue and authorization gates implemented, live queue/fence/drain checks pending |
| BAT-03-D | 85 | partial | reports/prompt-85.md; attempt-scoped diagnostic metadata/caps implemented, dispatch approval and recommendation surface absent |
| BAT-04-A | 86 | partial | reports/prompt-86.md; frozen plans and semantic retry checks, DB retry not exercised |
| BAT-04-B | 86 | partial | reports/prompt-86.md; components/dates/lineage adapter-tested; no approved source import |
| BAT-04-C | 86 | partial | reports/prompt-86.md; bounded rejection paths tested, scoped parser worker isolation absent |
| BAT-04-D | 86 | partial | reports/prompt-86.md; denominator/self-source controls tested with synthetic snapshots only |
| BAT-05-A | 87 | partial | reports/prompt-87.md; exact/normalized/shingle separation tested; benchmark item corpus not available |
| BAT-05-B | 87 | partial | reports/prompt-87.md; local exact/lexical config pinned; Python/Java AST and embedding config unavailable |
| BAT-05-C | 87 | partial | reports/prompt-87.md; entity/answer/reasoning extractors remain explicitly blocked |
| BAT-05-D | 87 | partial | reports/prompt-87.md; append-only private artifact storage is implemented but DB/index rebuild is unverified |
| BAT-06-A | 88 | partial | reports/prompt-88.md; strict host, rate, retry, byte and time caps are covered, but approved credential verification, exposure persistence and dispatch are absent |
| BAT-06-B | 88 | partial | reports/prompt-88.md; existing immutable corpus document/table contracts are identified and reusable, but no snapshot persistence adapter, extraction/index manifest writer or measured rebuild exists |
| BAT-06-C | 88 | partial | reports/prompt-88.md; source-specific record kinds and metadata/content/date/rights coverage are distinct, but no approved snapshots or source conformance fixtures were available |
| BAT-06-D | 88 | partial | reports/prompt-88.md; typed Data Portraits/infini-gram query metadata is candidate-only; neither tool is configured or queried |
| BAT-07-A | 89 | partial | reports/prompt-89.md; stage/method/source/index pins, seed and caps freeze; deterministic selection is tested, but no index executor or approved corpus is available |
| BAT-07-B | 89 | partial | reports/prompt-89.md; complete component/source/stage denominator and strict outcome reconciliation are implemented; no runtime outcome writer or source query was exercised |
| BAT-07-C | 89 | partial | reports/prompt-89.md; cache identity binds tenant, permission, corpus, method, query, seed and limits; fenced persistence/checkpoint/result storage is absent |
| BAT-07-D | 89 | partial | reports/prompt-89.md; replay deterministically recomputes selection from supplied stored hits or reports missing/mismatch; snapshot/index artifact lookup is absent |
| BAT-08-A | 90 | partial | reports/prompt-90.md; source artifact/component digests, retrieval candidate binding, byte offsets, answer relation and date context are validated; approved artifact/rights and source revision resolver are absent |
| BAT-08-B | 90 | partial | reports/prompt-90.md; versioned exact/near/semantic/family/concept/unresolved rubric disables auto-accept and requires semantic review; actual human calibration is unavailable |
| BAT-08-C | 90 | partial | reports/prompt-90.md; source excerpts are untrusted data and audit judge packets expose no tools or decision authority; no audit-specific fresh-session dispatcher, approved model context or exposure writer exists |
| BAT-08-D | 90 | partial | reports/prompt-90.md; append-only opinions, independent adjudication and immutable successor corrections are tested; no persistent match/review repository or database integration exists |
| BAT-09-A | 91 | partial | reports/prompt-91.md; all eight signal descriptors carry applicability, evidence, configuration, observation time and explicit unknown state; age intervals/popularity are context and familiarity is diagnostic only; source observations remain synthetic |
| BAT-09-B | 91 | partial | reports/prompt-91.md; frozen 50M+25C+15E+10L aggregation, six-place Decimal arithmetic, max/correlation caps and §12 score/threshold goldens pass; independent validation data are unavailable |
| BAT-09-C | 91 | partial | reports/prompt-91.md; unknown groups remain null/bounded, incomplete scope cannot produce low/medium and high lower-bound wording retains bounds; no approved finite source scope was executed |
| BAT-09-D | 91 | partial | reports/prompt-91.md; immutable v2 policy/assessment payloads and constrained claim projection are implemented; PostgreSQL migration was rendered offline only, with no persistence writer/API/public projection integration |
| BAT-10-A | 92 | partial | reports/prompt-92.md; provenance classes and upper-bound-only capture/receipt dates preserve earlier verified upstream dates; no live upstream records are available |
| BAT-10-B | 92 | partial | reports/prompt-92.md; strict context records pin confidence, cutoff source, updates, retrieval and prior deliveries; no provider context was supplied |
| BAT-10-C | 92 | partial | reports/prompt-92.md; pure evaluator and assessment validator cover pre/after/overlap/unknown/mutable states; only synthetic date fixtures ran |
| BAT-10-D | 92 | partial | reports/prompt-92.md; high-entropy commitments and local Ed25519 receipts verify byte binding; no approved external timestamp adapter/trust roots are configured |
| BAT-11-A | 93 | partial | reports/prompt-93.md; per-artifact AES-GCM, wrapped-key rotation, tenant binding and tamper tests pass; approved production KMS and restore integration are unavailable |
| BAT-11-B | 93 | partial | reports/prompt-93.md; exact authorization/payload/recipient events append atomically before delivery; live role/policy verifier and PostgreSQL integration are unavailable |
| BAT-11-C | 93 | partial | reports/prompt-93.md; P92 hiding commitments and encrypted private marker storage are reused; no production artifact-store/public-projection integration exists |
| BAT-11-D | 93 | partial | reports/prompt-93.md; 256-bit local collision scan and conservative observation contracts are tested; approved source, date review and query adapters are unavailable |
| BAT-12-A | 94 | partial | reports/prompt-94.md; ConStat is registered and fail-closed, but its exact implementation and compatible approved capabilities are not pinned |
| BAT-12-B | 94 | partial | reports/prompt-94.md; original/control validity, family splits, contexts, prompts, decoding, tests, multiplicity, decision rules and budgets are frozen in schema v2; no independent live validity review exists |
| BAT-12-C | 94 | partial | reports/prompt-94.md; all planned outcomes and per-retry access/cost/token records reconcile to the gateway contracts; database execution and live calls were unavailable |
| BAT-12-D | 94 | partial | reports/prompt-94.md; owned exposure-separated manifests and separate training caps are represented; training compute, power analysis and calibration data remain unavailable |
| BAT-13-A | 95 | partial | reports/prompt-95.md; deterministic decision reduction and finite coverage persistence checks are tested; no authorized live source/risk/reviewer evidence |
| BAT-13-B | 95 | partial | reports/prompt-95.md; approved source IDs bind to task sources; total/per-source draft caps are serialized under a plan lock; round/cost/time caps are frozen but generation execution is unavailable; distinct participants and private oracle evidence are required |
| BAT-13-C | 95 | partial | reports/prompt-95.md; transformed ancestry, prospective template review and cross-split family invariants are enforced; exposure pipeline and reviewed family data are absent |
| BAT-13-D | 95 | partial | reports/prompt-95.md; official membership, dispositions, oracle/validation links, competency/difficulty mappings, private sampling policy, computed distributions, score labels and membership digest are preserved; production projection review is absent |
| BAT-14-A | 96 | partial | reports/prompt-96.md; strict timezone/cadence/cap policy, DST-safe unique slot identity, locked atomic reservation, per-source daily caps and bounded durable retry fields exist; trusted owner-role verification and public API auth are unavailable |
| BAT-14-B | 96 | partial | reports/prompt-96.md; only changed approved sources enter incremental plans, finite full refreshes use every approved source, and retry reserves fit frozen caps; production corpus-snapshot trigger and connector execution are unavailable |
| BAT-14-C | 96 | partial | reports/prompt-96.md; alerts require accepted verified evidence plus a same-task successor risk assessment, risk increases require comparable measured scores, dedupe is deterministic, inbox rows insert atomically, and immutable evidence has no delete path; live production evidence is unavailable |
| BAT-14-D | 96 | partial | reports/prompt-96.md; staleness, outage, dispute, correction, policy/corpus/method discontinuity and compromised-seal alerts are distinct; routes are in-app only and payloads contain typed references without source text; external recipient authorization and a live notification UI are unavailable |
| BAT-15-A | 97 | partial | reports/prompt-97.md; count, percentage, overlap-union, null-denominator, family and coverage goldens pass with Decimal; live scope/query evidence remains unavailable |
| BAT-15-B | 97 | partial | reports/prompt-97.md; census/sample identity is frozen, no sample extrapolation is exposed, and eligible mean risk reports eligible/missing counts; no live cohort or reviewed projection is available |
| BAT-15-C | 97 | partial | reports/prompt-97.md; membership, policy, context, source window, source set and versioned freshness/provenance definitions drive comparability breaks; live source freshness/outage integration remains unavailable |
| BAT-15-D | 97 | partial | reports/prompt-97.md; native benchmark/code-quality/ranking paths were not changed and existing behavior-family bootstrap remains intact; integrated recommendation-surface review remains unavailable |
| BAT-16-A | 98 | partial | reports/prompt-98.md; reads and plan/run creation enforce tenant/role/object scope, cursor, idempotency and ETag preconditions; legal transitions, private artifact bytes and live PostgreSQL checks remain |
| BAT-16-B | 98 | partial | reports/prompt-98.md; all command families have help and exit-code behavior; operations without a service adapter fail closed |
| BAT-16-C | 98 | partial | reports/prompt-98.md; generated API types preserve Decimal strings, null reasons and enums; unimplemented operations have no SDK service to invoke |
| BAT-16-D | 98 | partial | reports/prompt-98.md; allowlisted public health projection and local-only dry-run are tested; live review/publication and production browser/access checks remain |
| BAT-17-A | 99 | partial | reports/prompt-99.md; curator page fails closed without private requests; no source/cost planner or authorized review/firewall/seal/monitor transition service exists |
| BAT-17-B | 99 | partial | reports/prompt-99.md; public aggregate shows benchmark/version, source window, scope, unknowns, risk tiers and limitations; temporal/model-context and prior-exposure panels require unavailable reviewed evidence |
| BAT-17-C | 99 | partial | reports/prompt-99.md; unavailable trends/corrections/derived sets/attestations are explicitly disclosed; comparable views and correction/revocation sources are not integrated |
| BAT-17-D | 99 | partial | reports/prompt-99.md; synthetic Playwright checks cover keyboard, mobile, loading, not-found/privacy and blocked curator access; revoked-state and production access/log review remain unavailable |
| BAT-18-A | 100 | partial | reports/prompt-100.md; signed claims bind the canonical private attestation digest and allowlisted health aggregate, and the report ID must match the review reference; no real reviewed source/scan document establishes the bound scope |
| BAT-18-B | 100 | partial | reports/prompt-100.md; real Ed25519 operations, public-only trust records, tamper/key/digest/expiry checks pass synthetic tests; approved key custody, trust-store publication and timestamp proof are unavailable |
| BAT-18-C | 100 | partial | reports/prompt-100.md; role-separated transition reducer, immutable event-chain append/read adapter, expiry/revocation/successor checks exist; transition API, live DB execution, key rotation and live revocation feed remain |
| BAT-18-D | 100 | partial | reports/prompt-100.md; UI discloses scoped authority, model-context limits, no unseen-data guarantee and task-text separation; invalid signatures hide claims and verified supersession links to a successor; live reviewed publication remains unavailable |
| BAT-19-A | 101 | partial | reports/prompt-101.md; exact revision/split/seeded 100-ID membership and bounded scopes are preflighted, but no approved imports, rights, caps or reviewer roster are available |
| BAT-19-B | 101 | blocked | reports/prompt-101.md; no authorized corpus snapshots/bytes or live connectors exist to produce 300 audit records, bounded retrieval, coverage or cost reports |
| BAT-19-C | 101 | partial | reports/prompt-101.md; held-out family bootstrap, metric, control and reviewer-evidence contracts exist; 100 actual independent labels across 30 families are absent |
| BAT-19-D | 101 | blocked | reports/prompt-101.md; no authorized trained/untrained manifests or reference model are available; source-overlap evidence is kept separate |
| BAT-20-A | 102 | partial | reports/prompt-102.md; four-per-benchmark slice, independent roles, lineage, and round/cost/time caps reconcile; no real candidate authors, checkers, or oracle evidence are available |
| BAT-20-B | 102 | partial | reports/prompt-102.md; six-task private manifest, screen/disclosure, lineage, key and timestamp mode contracts exist; no six owner-authored tasks or production key/provider evidence exists |
| BAT-20-C | 102 | blocked | reports/prompt-102.md; no approved monitor tick, changed source snapshot, live rescan or persisted owner alert history was available |
| BAT-20-D | 102 | partial | reports/prompt-102.md; mode-specific evidence and separate reported/verified query/cost accounting are implemented; no live source/model/human/crypto ledger exists |
| BAT-21-A | 103 | partial | reports/prompt-103.md; backup path/digest checks and audit document/artifact/seal/attestation reconciliation are implemented; available synthetic restore predates audit tables, external key-provider recovery and index configuration remain unverified |
| BAT-21-B | 103 | partial | reports/prompt-103.md; bounded worker cancellation/fence and monitor retry regressions pass; provider response replay tests cannot run because the dedicated PostgreSQL test database has no Alembic schema; no live source outage or object-store failure was injected |
| BAT-21-C | 103 | partial | reports/prompt-103.md; malicious archive/path, SSRF/source boundary, sandbox, telemetry and restore diagnostic-redaction regressions pass locally; live tenant ACL, KMS and production canary-log review remain absent |
| BAT-21-D | 103 | blocked | reports/prompt-103.md and operations recovery runbooks; isolated restore step times and cleanup were recorded, but the legacy backup cannot pass current audit-schema checks and no approved representative search/monitor load corpus or index rebuild adapter exists |
| BAT-22-A | 104 | partial | reports/prompt-104.md; GSM8K has bounded JSONL component extraction and fixture coverage; exact versions/rights/import adapters for the other code/math rows remain incomplete |
| BAT-22-B | 104 | partial | reports/prompt-104.md; BBH, ARC, TruthfulQA and IFEval metadata/lineage/template/checker blockers are explicit; only catalog contracts ran, IFEval checker and live source components remain unpinned |
| BAT-22-C | 104 | blocked | reports/prompt-104.md; GAIA is officially gated; Terminal-Bench, BFCL, image/OCR, environment and custom-private runtime/access remain unsupported or unverified |
| BAT-22-D | 104 | partial | reports/prompt-104.md; all 25 §5 rows map version, access, components, sources, tests, runtime and live blockers; native scoring paths are unchanged; live source conformance is unavailable |
| BAT-23-A | 105 | pending | prompt report |
| BAT-23-B | 105 | pending | prompt report |
| BAT-23-C | 105 | pending | prompt report |
| BAT-23-D | 105 | pending | prompt report |
| BAT-24-A | 106 | pending | prompt report |
| BAT-24-B | 106 | pending | prompt report |
| BAT-24-C | 106 | pending | prompt report |
| BAT-24-D | 106 | pending | prompt report |

## End-to-end gates (BX)

| ID | Owner prompt(s) | Status | Evidence |
|---|---|---|---|
| BX-01 | 83 | complete | prompt-83.md |
| BX-02 | 84 | complete | config/benchmark-audit/ and reports/prompt-84.md |
| BX-03 | 85 | complete | reports/prompt-85.md; 18 shared canonical vectors, strict Python validation and TS byte/digest agreement |
| BX-04 | 85 | partial | reports/prompt-85.md; six scopes and atomic/fenced queue code added, but old-row migration and recovery need PostgreSQL verification |
| BX-05 | 85 | partial | reports/prompt-85.md; old purposes remain NULL and diagnostic metadata is explicit, but recommendation exclusion and dispatch approval are not available to verify |
| BX-06 | 86 | partial | reports/prompt-86.md; pinned plans and deterministic sample tested; live source and DB retry verification pending |
| BX-07 | 86 | partial | reports/prompt-86.md; local safety/blocked cases tested; worker isolation and approved input absent |
| BX-08 | 86 | partial | reports/prompt-86.md; self-source, exposure and missingness controls fixture-tested; no real source/lineage evidence |
| BX-09 | 87 | partial | reports/prompt-87.md; method-level distinctions tested, imported benchmark coverage unavailable |
| BX-10 | 87 | partial | reports/prompt-87.md; exact/lexical config pinned; parser and embedding capabilities remain blocked |
| BX-11 | 87 | partial | reports/prompt-87.md; no unsupported feature is zero-filled; entity/answer features and live private index checks pending |
| BX-12 | 88 | partial | reports/prompt-88.md; all eight source groups have blocked contract profiles; source-specific conformance and authorized live connectors are missing |
| BX-13 | 88 | partial | reports/prompt-88.md; URL metadata, content, extracted text, dates and rights are separate dimensions; no Common Crawl snapshot was available to measure |
| BX-14 | 88 | partial | reports/prompt-88.md; private queries fail closed without a verifier and exposure store; no permitted delivery path exists yet |
| BX-15 | 89 | partial | reports/prompt-89.md; deterministic 20/source and 100/task selection records discarded counts, but index execution and controlled recall validation are unavailable |
| BX-16 | 89 | partial | reports/prompt-89.md; failed/truncated/unsupported cannot become no-match, cache identity is scoped; durable fenced resume/cache behavior is absent |
| BX-17 | 89 | partial | reports/prompt-89.md; stored candidate hits replay by digest or report missing/mismatch without refetch; approved snapshot/index artifact verification is absent |
| BX-18 | 90 | partial | reports/prompt-90.md; v2 evidence binds retrieval candidate, source revision/content digest, component bytes, matching offsets, answer and date context; live trusted source/rights verification is absent |
| BX-19 | 90 | partial | reports/prompt-90.md; frozen relation definitions keep concept-only at zero and semantic/ambiguous relations in human review; no labeled semantic calibration set or actual reviewers were available |
| BX-20 | 90 | partial | reports/prompt-90.md; untrusted source spans cannot become policy/tools and match judge packet authority is proposal-only; no audit model delivery or exposure record was exercised |
| BX-21 | 90 | partial | reports/prompt-90.md; conflicting opinions and third-party adjudication remain in the ledger; corrections create new evidence successors; persistence and live dispute history remain absent |
| BX-22 | 91 | partial | reports/prompt-91.md; formula, thresholds, score vectors, concept/family values and correlated-signal caps pass synthetic goldens; detector/reviewer calibration remains blocked |
| BX-23 | 91 | partial | reports/prompt-91.md; unavailable components are bounded, complete-scope no-match is distinct from failure, and low/medium require validated calibration plus complete scope; no real scope was scanned |
| BX-24 | 91 | partial | reports/prompt-91.md; eight descriptors preserve separate corpus overlap, public exposure, contextual popularity/age and behavioral diagnostics; no approved source or controlled behavior data are available |
| BX-25 | 92 | partial | reports/prompt-92.md; earlier verified source dates outrank later release dates in interval evaluation; no live source evidence |
| BX-26 | 92 | partial | reports/prompt-92.md; unknown cutoff, mutable alias, interval overlap, post-cutoff model updates and incomplete source bounds cannot yield an unqualified post-cutoff result |
| BX-27 | 92 | partial | reports/prompt-92.md; signature and token artifact digest bind receipts to commitment bytes, and local receipts stay non-independent; external trusted TSA validation unavailable |
| BX-28 | 93 | partial | reports/prompt-93.md; local encryption, tamper, tenant and key-rotation checks pass; approved production KMS and live restore evidence are missing |
| BX-29 | 93 | partial | reports/prompt-93.md; authorized local decrypts and remote deliveries record exact recipient/payload digests before handoff, with monotonic successor checks; production authorizer/database adapter remain unavailable |
| BX-30 | 93 | partial | reports/prompt-93.md; encrypted canary collision/detection and external-query linkage checks pass; no actual reviewed source/date evidence or production query adapter is configured |
| BX-31 | 94 | partial | reports/prompt-94.md; ConStat remains unsupported until an exact implementation is pinned; no heuristic substitute is emitted |
| BX-32 | 94 | partial | reports/prompt-94.md; preregistration validates family-disjoint splits, reviewed controls, exact configs, statistics and budgets; independent live control evidence is unavailable |
| BX-33 | 94 | partial | reports/prompt-94.md; planned outcomes, retries, access, cost, tokens and missingness reconcile; production persistence, supported method execution and calibration remain unavailable |
| BX-34 | 95 | partial | reports/prompt-95.md; incomplete scope/validity/rights/temporal/calibration stays review, prohibited overlap/invalidity/denied rights rejects, and zero hits do not bypass checks; live authorized evidence missing |
| BX-35 | 95 | partial | reports/prompt-95.md; approved source metadata, per-source and total draft caps, distinct participants and private test/oracle evidence are enforced; round/cost/time caps are frozen but no generation runner accounts them; production worker/reviewer verification missing |
| BX-36 | 95 | partial | reports/prompt-95.md; transformed tasks retain family ancestry, prospective tasks require accepted template review and split consistency is enforced; no reviewed family/exposure evidence |
| BX-37 | 95 | partial | reports/prompt-95.md; derived version accounts for official membership, replacements/exclusions/oracles, difficulty, separate labels and no automatic comparison; production distribution review/import projection missing |
| BX-38 | 96 | partial | reports/prompt-96.md; unique (policy, slot) storage, idempotent replay, bounded catch-up/retry and atomic source-rate reservations are tested at contract/persistence-metadata level; retries crossing their reserved source-local day close terminally; PostgreSQL race/recovery and live scheduler evidence are unavailable |
| BX-39 | 96 | partial | reports/prompt-96.md; accepted v2 evidence plus successor risk assessment is required for deterministic deduped in-app alerts; inbox insertion is transactional and historical documents are restrictive; no live source evidence or authenticated inbox UI exists |
| BX-40 | 96 | partial | reports/prompt-96.md; stale, outage, correction/dispute, policy/corpus/method discontinuity and seal compromise use distinct alert types; external delivery is structurally disabled; live role authorization and key/source event integration are unavailable |
| BX-41 | 97 | partial | reports/prompt-97.md; section 18 Decimal goldens and reconciliation tests pass; no approved live coverage/source evidence or database execution exists |
| BX-42 | 97 | partial | reports/prompt-97.md; sampled cohorts remain descriptive and mean eligibility/missingness is explicit; live sampled audit evidence is unavailable |
| BX-43 | 97 | partial | reports/prompt-97.md; immutable trend points identify membership/policy/context/source/time/method/definition breaks; integrated reviewed projection is unavailable |
| BX-44 | 98 | partial | reports/prompt-98.md; tenant/owner/RBAC/MFA, signed pagination, create idempotency and ETag preconditions are tested; transition writers, shared ACL, private artifact bytes and live DB checks remain |
| BX-45 | 98 | partial | reports/prompt-98.md; OpenAPI SDK types preserve Decimal strings/null reasons/enums and CLI exits are tested; unsupported operations remain blocked pending authorized adapters |
| BX-46 | 99 | partial | reports/prompt-99.md; public aggregate view and explicit evidence limits are implemented; live reviewed projection, context-aware evidence journeys and comparable trends remain |
| BX-47 | 99 | partial | reports/prompt-99.md; keyboard/mobile/loading/partial/not-found/blocked/privacy synthetic browser checks pass; revoked report state, production shared ACL and live browser privacy evidence remain |
| BX-48 | 100 | partial | reports/prompt-100.md; exact Ed25519 bytes, document digest, wrong-key/tamper/expiry/revocation and fresh-state checks pass synthetic tests; no approved signer, timestamp authority, real scope source or live trust publisher |
| BX-49 | 100 | partial | reports/prompt-100.md; append-only lifecycle event chain, role separation, expiry, revocation and successor digest checks are implemented; no authenticated transition API or live PostgreSQL/key-rotation/revocation evidence |
| BX-50 | 100 | partial | reports/prompt-100.md; synthetic browser checks verify claim suppression, scope caveats, task-text separation and correction link; no human-reviewed live public report or production access review |
| BX-51 | 101 | partial | reports/prompt-101.md; exact sample and bounded source preflight checks exist, but no approved source bytes/rights or actual 300-item scan evidence |
| BX-52 | 101 | partial | reports/prompt-101.md; family-cluster precision/recall/FPR intervals and evidence-backed plan/reviewer gates are implemented; actual independent held-out labels and source diversity are unavailable |
| BX-53 | 101 | blocked | reports/prompt-101.md; no controlled trained/untrained manifests or approved behavioral ground truth; source overlap and existing endpoint accuracy are not substitutes |
| BX-54 | 102 | partial | reports/prompt-102.md; exact 12/6 limits, independent roles, lineage, private access and execution-mode boundaries are validated; no actual reviewed replacements or six production sealed disclosures exist |
| BX-55 | 102 | partial | reports/prompt-102.md; changed-source, bounded usage and reference-only alert-history contracts exist; approved live rescan and authenticated persisted alert/usage evidence are unavailable |
| BX-56 | 103 | partial | reports/prompt-103.md; verified bundle parsing, document digests/references, sealed transitions and lifecycle checks are implemented; the available snapshot lacks the benchmark-audit schema, key-provider restore is unverified and no retrieval-index config/rebuild adapter exists |
| BX-57 | 103 | partial | reports/prompt-103.md; worker fence/cancel and bounded retry regressions pass; persisted provider response replay, current-schema reservation reconciliation and live source/object-store failure injection remain blocked on a migrated approved local test environment |
| BX-58 | 103 | partial | reports/prompt-103.md; malicious archive, connector, sandbox, telemetry-redaction and bounded restore-bundle checks pass; no representative search/monitor latency, storage or cost load was measured |
| BX-59 | 104, 105 | partial | reports/prompt-104.md; complete 25-row catalog reconciliation and strict no-overclaim checks pass locally; source/import rights, runtime, multimodal and live fixture evidence remain pending or blocked |
| BX-60 | 106 | pending | criterion in addendum §26; prompt report |

## Phase ownership

| Phase | Prompts | Status | Report |
|---|---|---|---|
| BA0 | 83–85 | partial | `phase-BA0.md` |
| BA1 | 86–88 | partial | `phase-BA1.md` |
| BA2 | 89–91 | pending | `reports/phase-BA2.md` |
| BA3 | 92–94 | partial | `phase-BA3.md` |
| BA4 | 95-97 | partial | `reports/phase-BA4.md` |
| BA5 | 98–100 | partial | `reports/phase-BA5.md`; Prompts98–100 foundations implemented; shared curator authorization, approved signer/reviewer authority, trusted timestamps, live PostgreSQL/revocation and reviewed source evidence remain unavailable |
| BA6 | 101–103 | partial | `reports/phase-BA6.md`; Prompts101–103 local foundations and failure checks exist, but live pilot inputs, compatible audit backup, key/index recovery, provider replay DB and representative search/monitor load remain unavailable |
| BA7 | 104–106 | partial | `reports/phase-BA7.md`; Prompt104 scope foundation is implemented; integrated lifecycle, reviewed projections and final traceability remain |

Update each status only when its evidence exists. A phase is partial if mandatory live, source, human or modality evidence remains unavailable.
