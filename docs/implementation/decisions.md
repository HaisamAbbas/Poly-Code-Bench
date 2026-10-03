# Decisions and specification discrepancies

## Prompt 18 — Track A

Recorded in full in [`decisions/D-18-01-track-a-evidence-boundary.md`](decisions/D-18-01-track-a-evidence-boundary.md).
Prompt 18 was authorized independently while the accepted-pilot prerequisite stays blocked;
authored internal Python/Rust evidence is not model benchmark data, and disclosed-security sources
remain fail-closed pending rights, advisory, reproduction and curator evidence.

## Prompt 15 - deterministic scoring and replay

Recorded in full in [`decisions/prompt-15.md`](decisions/prompt-15.md). Index:

- **D-15-01 -** purity is enforced by a static import scan and a runtime import tripwire in a clean replay process, not promised; the recorded timestamp and scorer digest are inputs, and `scorecard_id` is derived from the score's content.
- **D-15-02 -** the composite sums exact rational weights (as T 14.3 and its 14.6 worked example require) while the integer `effective_weight_bps` on the frozen `ScoreItem` contract is the rounded presentation; both are recorded.
- **D-15-03 -** a failed correctness gate zeroes correctness as well, because the composite is `30g + g × quality`.
- **D-15-04 -** missing required evidence blocks completion with `total_score = null`; an applicability disagreement with the frozen task plan is refused instead.
- **D-15-05 -** the efficiency dimension value is WP-13's output, re-derived and cross-checked against its ratios by the scorer; a disagreement is refused.
- **Discrepancy (T 14.4 vs T 15.1):** the idiomatic and robustness dimensions have no room for their residual judge items inside the language rubric's own 10000 basis points. Resolved with a frozen, explicitly pilot-status policy split (`8000`/`2000`), renormalising the language rubric within its block exactly as A 8.7 describes, with the scorer computing the expected weights so no producer can redefine them. Alternative rejected: folding judge items into a rubric item, which would let a judge silently move a language weight.
- **D-15-06 -** a duplicate report moves the evidence-manifest digest but not the score; `score_identity()` digests only the awarded numbers, and the manifest digest is order-insensitive because every repeated collection is a set.
- **D-15-07 -** `policy_digest` is recorded on the scorecard and on every explanation row rather than added to the frozen public `ScoreItem` schema.
- **D-15-08 -** archives keep every field, including the non-semantic identity fields canonical bytes drop, because scoring needs them as inputs; the digest recorded is the manifest's own content digest.
- **D-15-09 -** `check_boundaries.py` now allows `scoring -> plugins_api`, because T 14.1 names `FrozenTask` (a plugins-api type) as a scorer input.
- **D-15-10 -** `py.typed` added to `polycodebench-core` and `polycodebench-plugins-api`, which removed the `import-untyped` suppression class and surfaced two real typing defects that were fixed without behaviour change.

## Prompt 14 independent review of this prompt's own work

An independent adversarial review of the new judge modules was run before hand-off (no file was
modified by the reviewer). It reproduced its findings by executing the code. Nine were release
blockers or materially wrong behaviour and are fixed; each now has a regression test.

- **R-01 — A stored result failed its own digest check.** `report_digest` is the document's own
  digest but took part in the canonical bytes the digest was computed over, so `pcb-judge show` /
  `result` recomputed a different digest than the registered one for every stored result. Fixed by
  declaring `report_digest` in `canonical_excluded_fields`, exactly as `packet_id` already is for a
  packet. Test: `test_a_stored_result_reproduces_its_own_registered_digest`.
- **R-02 — A reviewer's decision could rewrite another candidate's score.** `latest_decision`
  matched only on `item_id`. Decisions are now scoped by `decisions_for_packet` to the packet id,
  packet digest, rubric digest and panel digest. Test:
  `test_a_decision_on_one_packet_never_rewrites_another_packet`.
- **R-03 — Supersession ran backwards.** `supersedes_id` names the decision a decision replaces,
  so the *referenced* row is the superseded one; the lookup had it the other way round and could
  return the decision that had been replaced. Test: `test_the_newest_decision_is_the_one_that_supersedes`.
- **R-04 — Contract maxima were not enforced by the validator.** An over-long rationale, note or
  fact subject, or too many uncertainty flags or citations, raised an uncaught pydantic error
  instead of a named rejection, which would have lost the delivery. The validator now enforces every
  bound, and the runner additionally records any unanticipated contract violation as an invalid
  delivery with its reason. Test: `test_over_long_or_over_large_votes_are_named_rejections_not_crashes`.
- **R-05 — Replaying a panel half-wrote its ledger.** `record_delivery` now returns the existing
  row for a repeated (packet, vote, delivery) triple, so a resumed run completes instead of
  failing on a uniqueness violation.
- **R-06 — The seeded 10% audit sample was computed and thrown away.** `audit_selected(packet,
  panel)` derives the decision from the panel's frozen audit seed and the packet digest, and the CLI
  now passes it into both `run` and the recompute path. Test:
  `test_the_seeded_audit_sample_is_deterministic_and_panel_defined`.
- **R-07 — Disjointness and packet identity were asserted, not checked.** `measure_calibration`
  now takes the scored packet digests: without them the report says `not_demonstrated`, with an
  overlap it reports `blocked`/`violated`, and a comparison is only made when the result carries
  that packet's own id and digest. Tests: `test_disjointness_is_only_claimed_when_it_was_checked`,
  `test_calibration_comparisons_require_the_result_of_the_same_packet`.
- **R-08 — Basis points carried a percentage.** `exact_agreement_bp` and `audit_rate_bp` now hold
  integer counts of basis points (0..10000), matching `score_bp`, `credit_bp`, `weight_bp` and
  `promotion_target_bp` elsewhere in the repository; the promotion comparison no longer scales twice.
- **R-09 — Smaller defects with the same character, fixed the same way:** an adjudication may no
  longer be recorded under a different rubric or panel; a human label off the anchor grid is refused
  at import instead of raising a `KeyError` mid-metric; a repeated vote index counts once, so a
  two-vote panel can never average; an item mean must sit inside the anchor range; the review queue
  lists only the newest result per packet, so an adjudicated packet stops filling it; recomputed
  deliveries keep their artefact links; a vote whose stored document is missing is an error, not a
  silent skip; a result without a frozen digest is refused at the repository boundary; `run` needs
  the run-plan permission and `result` needs restricted-evidence read.

One finding was partly mitigated and is recorded as a residual limitation rather than a fix:
candidate-controlled text is escaped before it reaches the prompt, so a candidate can no longer close
an `<evidence>` element or forge an item section (test:
`test_candidate_text_cannot_forge_packet_structure`), but semantic prompt injection inside a comment
is still possible in principle. The mitigation and the trigger that exposes a followed instruction
(`untrusted_comment_only`) are the defences; the guarantee is not that a hostile comment is
harmless, only that it carries no authority and leaves evidence behind.

## Prompt 14 decisions and recorded dependencies

- **D-14-01 — Identity is withheld structurally, then proven:** `JudgePacket` has no candidate identity, provider, rank, cost or expected-score field, and judges receive no tools (`JudgePanel.tools == ()`, `load_judge_protocol()` refuses a protocol with tools or a tool-call budget). `assert_no_identity_leak` then checks the packet's own field names against a forbidden-token list and scans the rendered text for the concrete identity strings a caller withheld. Span *content* is deliberately not scanned for field-like words, because candidate code legitimately contains its own JSON and a rule that fires on it is a rule no reviewer would honour.
- **D-14-02 — A comment citation is review evidence, not a schema error:** Technical Spec 15.1 makes an *unverifiable* reference a schema error, and a comment anchor is verifiable — it exists in the packet. It is outside every item's evidence scope, so a vote justified only by candidate comments records `untrusted_comment_only` and goes to review with all votes intact. Rejecting such a vote outright would hide exactly the manipulation attempt the clause exists to expose.
- **D-14-03 — Disagreement counts anchor *steps*, not distinct values:** T 15.3 triggers on a max–min spread of 1 and A 8.8 on "two anchor levels". With anchors `{0, 0.5, 1}` both mean the full range, so `spread_levels()` divides the spread by the 0.5 anchor step. A `1 / 0.5 / 1` panel is one step apart and stays `ready`, which is the E2E-21 expectation; counting distinct values would have made it `needs_review`.
- **D-14-04 — The replacement delivery re-asks the same question:** the schema-repair instruction is a fixed prefix applied to the rendered user message after validation fails. The packet, schema, evidence and recorded seed are unchanged, so "the same input" holds in the sense the gateway can verify (the request differs only by the frozen instruction) and a repair cannot smuggle new evidence.
- **D-14-05 — Judge output is capped by the resolved model configuration:** the panel declares its ceiling and the runner sends `min(panel ceiling, configuration ceiling)`. A request never asks for more than the configuration declared; a judge that cannot answer inside its own ceiling returns an invalid vote rather than a silently enlarged request.
- **D-14-06 — Stored judge records keep their timestamps:** `canonical_document_bytes` drops non-semantic fields so content digests ignore recording detail, which would strip `created_at` from a stored vote. `judge_record_bytes()` serializes a record in full under the same envelope; a packet's `digest()` still excludes its own self-referential `packet_id`, so reading a packet back means parsing the bytes and recomputing the digest.
- **D-14-07 — Packet and adjudication identities are content-derived and local:** `derived_judge_id()` lives with the judge contracts and a packet id covers the rubric, panel, language, role, task statement, constraints, items, span material and comments, so an id can never be reused for different evidence. An adjudication row's primary key is its decision's content identity, which makes a replayed decision the same immutable row and makes `judge_item_result.adjudication_id` verifiable. A separate shared helper was deliberately not used: the concurrent Prompt 13 work had just moved `identity.derived_entity_id`, and judge identity must not depend on another package's id helper.
- **D-14-08 — Calibration without labels reports `blocked`, never `measured`:** `CalibrationReport` refuses to carry an agreement figure, a promotion verdict or a bias direction while the status is blocked, and requires the missing inputs to be named. The selection is seeded, stratified, drawn without replacement and disjoint from scored packet digests; a candidate set that overlaps a scored packet is refused. Metric arithmetic is exercised with FIXTURE labels and labelled as such — it is evidence about the calculator, not about human agreement.
- **Recorded external dependencies (not implemented here):** no approved judge endpoint, credentials, price snapshot or model configuration exists (`judge-panel-v1` therefore reports `provisioned: false` and `pcb-judge run` exits 4 with `JUDGE_PANEL_UNAVAILABLE`); no qualified reviewer roster and no human labels exist. These block the T 15.3 calibration gate and keep `effective_for_scoring: false`; the ticket ledger records them per ticket.
- **Shared workspace note:** a concurrent Prompt 13 session was active in this working tree. New workspace members without boundary-map entries made `scripts/check_boundaries.py` fail (`KeyError: 'lang_c'`), and their in-flight plugin/contract edits made 47 parser and policy tests fail independently of this prompt. `scripts/check_judge_boundaries.py` reproduces the same rule for the judge files only. A repository-wide `ruff format` was run once while scoping arguments and reformatted several of those in-flight files; the change is formatting-only and idempotent.

## Prompt 07 review corrections

- **D-07-06 — Concurrent state and result authority:** short scope transactions serialize DAG mutations; nonblocking fairness locks and fresh counts enforce concurrent campaign/provider caps. An unused conditional branch receives `branch_not_selected`, while failed/unknown prerequisites cannot supply successful inputs. A completion key includes the complete observed outcome as well as the verified artifact. The new result document is durable evidence; legacy completions without it require explicit reconciliation before replay.
- **D-07-07 — Provisioning and cleanup fencing:** provisioning reserves capacity before an external resource can exist and maintains its lease through creation. Cleanup confirms the exact worker/job/fence. Any unknown create failure keeps its slot unavailable pending driver-verified recovery. This trades temporary unavailable capacity for preventing premature reuse or an untracked live guest. Local driver checks establish development behavior only.
- **D-07-08 — Review commit boundary:** the owner confirmed concurrent Prompt 08 work must remain outside the Prompt 06/07 review commit. Validation uses an isolated snapshot, and shared models are staged with only the scheduler additions. Model gateway code, accounting migration/role additions and Constitution files remain outside this commit.

## Prompt 06 security and deployment decision

- **D-06-01 — EC2 guest control channel:** use a private-IP SSH forced command with pinned host keys, a supervisor-only ingress security group, fixed remote command, JSON on stdin, short-lived stage capability, and no port/agent/socket forwarding. The worker verifies the exact configured AWS principal and live instance/network/container attestation before granting production-tier operations. This is a deployment implementation choice within the spec; the Terraform plan and driver are not deployment evidence. No AWS account/region, supervisor principal, reviewed AMI or spend ceiling was authorized, so `terraform validate/plan` and real-VM E2E-05/06 remain blocked.

## Prompt 06 owner deferral

- **D-06-02 ? Production cloud verification deferred:** on 2026-09-30 the owner stated cloud access is unavailable for now and deferred the production EC2 work. No infrastructure was provisioned. Keep PCB-06-1 through PCB-06-4 production verification and full E2E-05/06 blocked; resume only when the access, AMI, network, tooling, and spend inputs listed in `progress.json` are available.

## Prompt 05 decisions and sequencing clarification

## Prompt 05 decisions and sequencing clarification

- **D-05-05 — Admission evidence authority after review:** the CLI must replay the exact imported fixture snapshot and match the supplied report. Registration stores the full observed report and binds it to the manifest and both bundle digests. Production admission is unavailable until a trusted worker authority exists; a caller-controlled tier string never establishes that authority. Every non-fixture task set remains blocked. Prior reports based only on manifest identity must be regenerated.

- **D-05-01 — Local admission as the WP-06 prerequisite slice:** Prompt 05 explicitly permits the smallest compliant local sandbox invocation for trusted authored admission fixtures. `DockerFixtureRunner` is that narrow slice and records `local_fixture`; it does not establish disposable-VM isolation or production task admission. Prompt 06 extends the runner/interface to the required development/production VM lifecycle. Task acceptance and scored-task criteria are unchanged.
- **D-05-02 — Fixture runner image and boundary:** authored admission uses the locally verified immutable `python:3.12-slim` linux/amd64 digest recorded in the admission report. Container settings disable network, use read-only root and nonroot execution, cap CPU/memory/PIDs/time, and mount only the candidate. This is development evidence, not a production isolation approval.
- **D-05-03 — Pilot contract status and methodology rights:** versioned profiles remain pilot contracts; human/judge calibration is required before scored use, and no live budget is authorized. Native methods and adaptations are separately described for all four source families. Private CursorBench assets are unavailable, and benchmark/task redistribution rights remain unresolved until the corresponding source-specific review.
- **D-05-04 — Owner approval of pilot scoring baseline (2026-09-30):** the project owner approved `polycodebench-code-pilot-v1` as written: correctness 30%, security 20%, efficiency 15%, code quality 15%, idiomatic 10%, robustness 10%. This freezes the baseline policy only. `effective_for_scoring` remains false until human/judge calibration and all task/source/runtime gates pass; this approval authorizes no live model calls or spending.

## Prompt 00 baseline

## Prompt 00 baseline

- **No architecture or scoring decision added.** Follow the Architecture v1, Technical Implementation Spec v1, and prompt pack v1 in their stated precedence. No existing implementation was found to reconcile or refactor.
- **Source identity checked:** Architecture and Technical Spec SHA-256 values match those recorded in prompt-pack section 1.1. See `source-manifest.json`. The pack has no expected self-hash to compare.
- **Missing original source:** `Pasted markdown(5).md` is referenced as the original requirements source by the Architecture and Technical Spec, but is absent from the workspace. This prevents independent comparison against that source; it does not contradict the two present, detailed specs. Locate it before claiming source provenance complete.
- **Sequencing:** Use prompt-pack phases/prompts and dependencies as written. Prompt 05 allows a minimal local admission execution slice if needed; Prompt 06 completes the sandbox work. This is an explicit permitted dependency slice, not an acceptance-gate change.
- **Repository state:** The directory is not a Git repository. No pre-existing edits or history can be assessed; no files were overwritten. Establish version control as part of Prompt 01 if this workspace is intended to be tracked.

## Open prerequisites (not design discrepancies)

Cloud account/region, OIDC identities, provider/judge endpoint credentials, explicit spend limits, source rights, human calibration data/reviewers, object-store endpoint, and publication target are not established in the inspected workspace. These are configuration or external-evidence inputs called out by the specs; do not infer them from installed tools.


## Prompt 01 decisions and discrepancies

- **D-01-01 - Workspace boundaries:** use the Technical Implementation Spec's Python ownership boundaries (`core`, `services`, `persistence`, `orchestration`, `runner`, `evaluation`, `scoring`, `publication`, `plugins`) as the package/dependency direction. Keep plugin interfaces separate from plugin implementations. Scaffolds declare ownership only; no production behavior is inferred from a package existing.
- **D-01-02 - Runtime identity pins:** pin Python 3.12.10 (spec baseline) and Node 24.21.0 (active LTS verified against the official release index on 2026-09-30); pin direct Python/npm dependencies in project manifests. Lock resolution, clean frozen dependency sync/install, and package builds passed on 2026-09-30.
- **D-01-03 - Local service scope:** Compose declares PostgreSQL and S3-compatible object storage for local development only. These are not approved scored-run images and are not the production VM execution boundary.
- **SD-01-01 - Initial registry block resolved:** the first run could not reach PyPI/npm, but the authorized Auxiliary R1 retry succeeded. `uv.lock` and `pnpm-lock.yaml` now exist; frozen installs and builds pass. Registry availability can still depend on the execution environment.
- **SD-01-02 - Identity/allowlist boundary:** `PCB_SERVICE_IDENTITY` is validated startup metadata, not cryptographic evidence of a platform identity. External endpoint/object-store/image allowlists and real service-principal checks require deployment policy inputs and are not claimed implemented by this config scaffold.
- **Method source status:** Current source descriptions preserve native/adapted boundaries. CursorBench private tasks/grader assets are unavailable; current source/data licenses and rights must be checked at task admission. No method execution or task import occurred.
- **Prompt 01 completion:** the registry and Docker engine gates were resolved during Auxiliary R1. Frozen installs, Python package builds, mypy, test/config/boundary checks, frontend typecheck/lint/build, and local service readiness passed. The pushed baseline commit remains `08a0edd4b674e8da62f6a6f5e65cdd53db1a086b`; completion and ledger updates are uncommitted. No product-contract change was made.
- **D-01-04 - Immutable local development images:** Approve only for local development `docker.io/library/postgres:17.6@sha256:00bc86618629af00d2937fdc5a5d63db3ff8450acf52f0636ec813c7f4902929` (linux/amd64 child `sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409`) and `docker.io/chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d` (linux/amd64 child `sha256:aba492e2a4e4c90bff795745e8e660affa1f09e7650f5981bd7bccd1a06cd931`). Digests were resolved from Docker Registry manifests and the pulled linux/amd64 image identities were checked on 2026-09-30; the pulled image IDs and RepoDigests matched the pinned references. These exact identities are approved for local development only. SeaweedFS is confined to the optional local S3 role; this does not establish full AWS S3 compatibility or production suitability. Cryptographic signature provenance was not independently verified.
- **SD-01-03 - MinIO image no longer usable here:** the configured Docker Hub image returned `pull access denied`; Quay returned `401 Unauthorized`. MinIO's upstream release instructions now direct container users to build from source. Replace it for local development with the pinned SeaweedFS S3 endpoint rather than asserting the unavailable MinIO image was verified. References: https://github.com/minio/minio/releases and https://github.com/seaweedfs/seaweedfs/blob/master/docker/README.md.

- **D-01-05 - pnpm install policy:** keep dependency lifecycle scripts blocked by default and explicitly allow only the reviewed `unrs-resolver` native-binding postinstall. The exact pinned Next 16.3.7 packages are listed in `minimumReleaseAgeExclude` to account for their release metadata; the lockfile supply-chain policy passed for all 402 entries.

## Prompt 03 decisions and scope notes

- **D-03-01 - Non-destructive initial downgrade:** the first schema revision refuses downgrade because dropping task provenance, attempts, evaluations, audit, accounting and published evidence would destroy benchmark records. Schema retirement requires a separately reviewed backup and retention plan.
- **D-03-02 - Database workload identities:** SQL role groups are provisioned as `NOLOGIN`; deployment creates managed per-service login identities and attaches them to a single narrow group. Passwords and tokens remain in deployment secret management and are not checked into SQL, audit records or logs. The local test connection used the PostgreSQL cluster's trust-authenticated ephemeral test administrator and explicitly `SET ROLE`d in permission tests; it does not verify deployed credentials.
- **D-03-03 - Cyclic artifact/execution identity:** both tables are created before the artifact-to-execution foreign key is added. An initial schema drift check caught the missing edge in the first draft; a fresh-database migration plus repeat-upgrade and drift check passed after correction.
- **D-03-04 - Prompt 03 evidence boundary:** PostgreSQL 18.4 integration tests cover the transaction/repository and selected DB-role/service foundation. The CI job targets pinned PostgreSQL 17.6, but hosted CI was not run; HTTP/API routes, full administrative-role coverage, provider execution and all remaining E2E-02/25 variants remain pending by their later owners.

## Prompt 04 decisions and scope notes

- **D-04-01 - Storage domains and key layout:** choose one bucket per visibility (`hidden`, `internal`, `public`) and use the validated encryption domain as the first canonical key segment, followed by the two-character SHA-256 prefix and full digest. This implements the spec's `{kind}/{digest-prefix}/{digest}` shape while the current Artifact contract has no distinct persisted artifact-kind field. Provisional keys use upload UUIDs and are never authoritative references. Adding stage/artifact-kind namespaces belongs with the stage control contract; no digest is an access token.
- **D-04-02 - Service-proxied upload and finalization:** workers/guests do not receive bucket listing or broad S3 credentials. The current foundation accepts service-proxied bytes and finalizes by independent readback. Short-lived one-object stage transfer credentials, lease/fence checks and stage-bound allowed-kind checks are deferred until the stage-control/lease contract in Prompt 07 exists; this is not evidence that guest transfer is complete.
- **D-04-03 - Public projection boundary:** initial public export is limited to a strict reviewed-metadata object. A reviewer first stores an immutable approval bound to the source and exact canonical projection digest. A distinct publisher uses that approval ID; verified public artifact registration and declassification commit together. Public downloads require the approval and declassification link. The private source's visibility/key remain unchanged. Scorecard/release-specific redaction and public API routes remain with later publication prompts.
- **D-04-04 - Local versus production storage evidence:** PostgreSQL 17.6 and SeaweedFS 4.48 integration passed using three local buckets. This validates application behavior against the local S3-compatible emulator only. No production IAM, bucket policy, encryption/key-management, lifecycle or deployed service-identity denial checks ran because no production object-store target or principals are configured. No normative scoring change was made.
- **D-04-05 - Unreferenced object retention and quota:** expire upload reservation after 24 hours by default and release reserved quota at expiry. Retain provisional bytes for 30 days after expiry and unreferenced canonical objects for 30 days after object creation before GC. This separates abandoned-upload accounting from the Technical Spec's unreferenced-object retention interval and safely cleans a canonical write whose database transaction did not commit.

## Prompt 02 decisions and discrepancies

- **D-02-01 - Seed wire representation:** Technical Spec §3 requires unsigned 64-bit seeds to survive serialization without signed overflow, so `master_seed` is a canonical decimal string throughout the versioned contracts. A numeric `master_seed: 4096` example in Technical Spec §4 is treated as illustrative shorthand; the narrow wire-safe representation in §3 governs. No score or sampling semantics are changed.
- **D-02-02 - Contract E2E evidence boundary:** E2E-01 passed using the shared cross-runtime contract fixtures and property checks. This is contract-level verification, not an application workflow or benchmark execution result; broader immutable persistence and score replay remain pending under REQ-09.

## Prompt 07 decisions and discrepancies

- **D-07-01 - Stage execution finalization discrepancy:** the initial persistence migration installed a trigger rejecting every update to `stage_execution`, while Technical Spec §7.3 requires inserting the execution during claim and finalizing its result in the same completion/recovery transaction. Migration `f17b6b04a237` replaces that trigger with immutable identity plus exactly-once finalization: identity fields cannot change; `finished_at` and `result` become final once; deletion is rejected. Scheduler SQL grants permit updates only to `finished_at`, `result`, `failure_class`, and `output_manifest_id`. This is the narrow change needed to satisfy both the evidence immutability requirement and the explicit completion transaction.
- **D-07-02 - Fairness bounds:** use default caps of four active leases per campaign and two per provider, configurable when constructing `PostgresJobRepository`. The source requires bounded fairness but supplies no numeric limits. PostgreSQL selects lower-load campaigns/providers first; these operational pilot defaults do not change task selection or score weights.
- **D-07-03 - Durable evaluation cancellation:** the initial evaluation state constraint omitted `cancelled`, although Prompt 07 cancellation applies to unfinished evaluation work. Migration `7b8cc92d13ea` adds the explicit terminal state. Release cancellation remains outside this scheduler method because release withdrawal has separate publication governance.
- **D-07-04 - E2E-09 evidence boundary:** actual local Docker tool-stage cancellation, lease revocation, completed artifact preservation and guest destruction passed. No model request or usage record is fabricated: durable model-call usage preservation and ranked-release exclusion remain pending Prompt 08/17. Local Docker evidence does not clear the owner-deferred production VM gates from Prompt 06.
- **D-07-05 - Explicit later-prompt resume ledger rule:** `verify_prompt00.py` initially required `active_prompt == last_completed_prompt + 1`, which rejected the execution contract's explicit permission to work a later prompt while an earlier prompt remains partial. It now accepts a later active prompt only when every intervening prompt is explicitly persisted as `partial`; the active prompt and all following prompt owners/statuses are still checked. Prompt 06 remains partial and is not silently completed.

## Prompt 08 decisions and discrepancies

- **D-08-01 - Gateway location:** adapters, transport, secrets, throttle and the gateway live in `polycodebench_orchestration.gateway` rather than `plugins/models/`. Architecture §16 names `plugins/models/` for provider adapters, but no plugin packaging or loader exists yet and adding a workspace package would change the dependency-boundary model. The `ModelAdapter` protocol and the `BaseAdapter` seam are in place; moving the four adapters into a plugin package is a mechanical change when the plugin loader exists. Core holds only pure contracts and rules (`model_contracts`, `model_planning`, `endpoint_policy`).
- **D-08-02 - Schema discrepancy (reservation uniqueness and deliveries):** Technical Spec §8.2 lists `UQ(account_id, call_intent_id)` for `budget_reservation`, but §8.3 requires a retry to reserve additional exposure while the earlier delivery's exposure stays. Migration `9d3a71c05e24` widens the key to `(account_id, call_intent_id, delivery_index)`. It also adds: `budget_resource` (turn/input/output limits, separate from money), bucketed ledger columns (`resource`, `from_bucket`, `to_bucket`) so balances can be re-derived, endpoint approval evidence columns, and `call_delivery.normalized_response_artifact_id`. The initial blanket immutability trigger on `call_delivery` made outcome recording impossible, so it is replaced by a transition guard: identity is fixed, each outcome field is write-once, `responded`/`failed` are terminal, and an `ambiguous` delivery can only become `responded` (first-arriving response). Partial unique indexes allow at most one responded and one in-flight delivery per intent. `downgrade()` refuses: accounting is financial evidence. No scoring or cohort meaning changes.
- **D-08-03 - Outcome classification:** 4xx (except 408), 429 and failures before any byte is sent are definitive and release their reservation. Timeouts after send, connection loss after send, 408/5xx and unparseable 2xx bodies are **ambiguous**: exposure moves to `uncertain_committed` and is never released automatically, including for 502/503/529, because a gateway timeout can follow processing and the provider documentation fetched does not promise otherwise. Local request-construction errors are rejections, not spend. An oversized response is ambiguous but not retried. Retries are bounded (default three deliveries per logical call) and each reserves additional exposure.
- **D-08-04 - Cost bounds and strict caps:** the money bound is integer micro-USD rounded up from a provable input bound (one token per request byte, plus 256 framing tokens, plus 1024 when tools are supplied because providers may inject hidden tool instructions; the two allowances are conservative assumptions, not provider figures) and the output cap actually sent, times the operator-supplied price snapshot. A strict cap additionally requires the registration to declare that the output cap bounds all billed output including reasoning. Without a price or that declaration no enforceable bound exists and the configuration is blocked (CLI exit 4); an explicit operator per-call reserve is permitted only as a labeled non-strict policy. Estimates use list price without cache discounts and are labeled estimates. A request must carry the resolved config's temperature and reasoning and an output cap no larger than the config's, so what is reserved is what can be billed.
- **D-08-05 - Provider documentation discrepancies:** (a) OpenAI docs pages returned HTTP 403; wire facts come from the public OpenAPI schema (`openai/openai-openapi`), so the adapter assumes `POST {base}/chat/completions`, `max_completion_tokens` (declared per endpoint as `max_tokens` when needed) and JSON-string tool arguments. (b) Google's current function-calling and structured-output guides document the newer Interactions API; the `generateContent` REST field names for thinking and schema-constrained output could not be confirmed, so the Google adapter implements text, tools, seed and temperature only and rejects thinking and structured-output requests instead of guessing. (c) Anthropic's reference states `temperature` is deprecated for newer models and there is no `seed`; both are therefore declared per model, never assumed. The Anthropic version header value `2023-06-01` was not shown on the fetched page. None of these changes the specification; each narrows what is claimed.
- **D-08-06 - Conformance probing of pending endpoints:** approval of compatible and local endpoints requires a passing conformance report, which needs a probe. `get_for_conformance` lets an administrator-initiated probe reach a *pending* endpoint (never rejected/revoked) under the same network policy; gateway calls use `get_approved` only and never reach pending endpoints.
- **D-08-07 - Secret references:** registrations store only `secret://<namespace>/<lowercase-name>`; values resolve inside the gateway from `PCBSECRET__<NAMESPACE>__<NAME>`. The prefix avoids `PCB_` because `load_startup_config` rejects unknown `PCB_` keys. Names are lowercase with hyphens so two references cannot map to one variable. Credentials are trimmed, must be header-safe, and any echo in a response is replaced before storage. Production secret-store integration remains a deployment input.
- **D-08-08 - Arrival-order resolution:** when a recovered retry and a stalled original delivery both answer, the first response to arrive is the only one consumed; the other is stored and billed as evidence (`late_response_not_consumed`). The choice is by arrival, never by content, so it cannot be used to pick a better sample. A response persisted before settlement is settled on restart without a new request (`recover_raw`).
- **D-08-09 - Independent review:** an adversarial review found 12 defects; five were reproduced against the first implementation (double charge on repeated reconciliation, lost turn after a retried or unbilled delivery, intent/delivery lock-order deadlock, caller-supplied scope redirecting a charge, reservation smaller than the wire output cap). All twelve were fixed and pinned in `tests/test_model_gateway_review_regressions.py` and `tests/test_model_gateway_units.py`. The review's unverified note about provider tool-prompt overhead is handled conservatively by D-08-04.
- **D-08-10 - Consistency-checker and test-pin compatibility:** `verify_prompt00.py` previously refused an active prompt whose status is `done`; it now accepts `done` for the last-worked prompt (subsequent prompts still must be `not_started`). Prompt 07's integration fixture pinned the exact Alembic head; it now requires the Prompt 07 revision to be an ancestor of the database revision (`tests/migration_support.py`), so later migrations do not break earlier prompts' suites.
- **D-08-11 - E2E evidence boundary:** E2E-10 and E2E-12 passed on actual PostgreSQL 17.6 and SeaweedFS 4.48 with a deterministic fixture transport; E2E-11 passed at the gateway level and keeps the Prompt 09 agent-controller restart variant pending; the model-usage half of E2E-09 passed with fixture transport and ranked-release exclusion remains pending Prompt 17. The only live evidence is one bounded local Ollama run. OpenAI-compatible hosted, Anthropic and Google adapters were not exercised against live endpoints (no credentials); none of this satisfies E2E-31 or the Prompt 17 two-model live pilot.

## Prompt 09 decisions and discrepancies

- **D-09-01 - Protocol identity:** solve protocols are frozen `SolveProtocol` documents in `config/protocols/`; the protocol digest is the canonical document digest, and the per-task effective protocol (tools and ceilings intersected with `ProtocolConstraints`, minimum of each) has its own digest. Both are written to the `session_started` event, every checkpoint and the candidate row, and a resume under a different effective digest is refused. `RunConfig` carries only a `protocol_id` slug and is left unchanged; binding the digest at session level avoids a schema change, but the run planner (Prompt 12/17) should also hash the protocol digest into the run configuration digest.
- **D-09-02 - Checkpoint model:** an event batch and the checkpoint covering it commit in one transaction under an attempt row lock with a sequence compare-and-swap. The checkpoint row (migration `b2f6d4a91c73` adds `protocol_digest`, `workspace_digest`, `transcript_digest`, `binding_digest`) names a tar workspace archive and a transcript manifest (event refs with digests, budget, pending call IDs, file modes); restoration re-verifies the binding digest, the manifest bytes, the workspace bytes, the event list and the budget recomputed from events. The spec names these columns' concepts (9.3) but not their encoding. The migration refuses downgrade (durable execution evidence).
- **D-09-03 - Context policy values:** the spec fixes the latest eight turns and metadata summaries but not a per-request window or a token counter. `standard-agent-v1` uses a 60,000-token estimated ceiling, `single-shot-v1` 32,000, and the counter `utf8_bytes_div4_v1` (ceil(bytes/4)); provider-reported input tokens are stored next to the estimate on every turn so discrepancies are visible. These are proposed harness values (a change is a new protocol version and a new cohort). Over the ceiling the oldest summarised turns are dropped whole, then recent turns are summarised oldest-first; the latest turn and the instructions are core, and if they cannot fit the session ends as budget exhaustion on `context_budget` (frozen where permitted).
- **D-09-04 - Public tests and reserved names:** the spec defines `run_public_tests` over "allowed public test group IDs" but not where groups are declared. They are declared in the visible bundle's `public-tests.json` (`id`, fixed `argv`, `cwd`, `timeout_seconds`); the model picks group IDs, never commands, and the file is protected. Names starting `.pcb_` are reserved for the harness (patch inbox, temporary files) and invisible to every tool and to candidates.
- **D-09-05 - Sandbox snapshot defect (Prompt 06) and execution policy:** `LocalDockerSandboxProvider.snapshot` used `docker cp` on a tmpfs workspace, which returns an empty archive, so workspace checkpoints were impossible. Snapshots are now produced inside the guest (sorted, mtime-normalised tar, links and special files refused before any byte is emitted), keeping the provider's existing path/link validation. Docker also mounts tmpfs `noexec`, so scripts run through an interpreter; native-binary languages (Prompt 10+) need an execution-policy decision. Neither changes a specification clause.
- **D-09-06 - Failure classification:** model-side and recorded as results: invalid or missing final output, contract-invalid candidates, protected-file modification, budget exhaustion (turns, tool calls, tokens, active time, the gateway's spending limit, context ceiling), refusals and empty replies. Infrastructure and resumable (`SolveInterrupted`, no terminal event, no candidate): guest failure, helper failure, provider call failure after the gateway's bounded policy, lease loss. Capability, endpoint-approval and cost-bound errors propagate as configuration errors rather than model failures.
- **D-09-07 - Budget semantics:** one model turn is one request/response cycle; every tool call attempt, valid or not, consumes the tool-call budget. When the tool-call or active-time budget is hit inside a turn, the remaining calls of that turn receive typed `budget_exhausted` results and the session then stops (freezing a valid workspace where the protocol permits). Unknown provider usage is charged at the request's conservative estimate, never zero. Answer-style submissions (text, typed JSON, findings) have nothing to freeze on exhaustion and become recorded model failures.
- **D-09-08 - Extraction rules:** single-shot accepts the whole response as one JSON envelope (duplicate keys, extra fields and surrounding prose are invalid) or, for a one-file output only, exactly one fenced block; two blocks are ambiguous and never resolved. File and patch tasks under the agent protocol freeze the declared paths from the workspace as written; answer, typed and findings tasks use the same envelope in the final reply. A findings list over the limit is invalid, never trimmed, and a separately valid patch stays evaluable.
- **D-09-09 - Candidate vocabulary discrepancy:** the core `Candidate` contract uses `source_bundle/unified_diff/findings/answer/typed_value` while the task output contract and the stored candidate row use `files/patch/structured_findings/text/typed_json`. Solve sessions store the output-contract vocabulary (the row's `submission_kind` is unconstrained); mapping to the `Candidate` contract belongs to the evaluation stages (Prompts 10-12). Patch candidates contain only changes inside the output contract; other changes are recorded as ignored, and a modified or deleted protected file invalidates the candidate.
## Prompt 10 — Python language plugin

- **D-10-01 - A plugin describes work; a trusted supervisor runs it.** `LanguagePlugin` returns typed `BuildPlan`/`TestGroupPlan`/`AnalysisPlan`/`PerformancePlan` objects and parses recorded bytes back through an `ArtifactReader`. Nothing in `plugins/languages/python` imports `subprocess`, opens a socket, or starts a process. `PlanRunner` (`packages/evaluation`) owns execution and `LocalDockerSandboxProvider` owns isolation; the plugin decides only what a command means and how its output is read. This keeps plugin code out of the privileged position and is why a plugin cannot influence its own isolation.
- **D-10-02 - Two images, not one.** `pcb-python-runtime` carries Python, pytest and Hypothesis and is also what solve guests run; `pcb-python-evaluator` adds Ruff, mypy, Bandit and Semgrep. Build/test plans therefore run in an image a candidate can also see, while analyzer output is produced in an image the candidate never writes to. Both are built with `docker build --network none` from a hash-verified local wheelhouse and the build script fails if the installed set drifts from the lock.
- **D-10-03 - Two context-scanner detectors were widened after admission exposed them as too narrow.** `manual-counter` previously caught only `d[k] = d.get(k, 0) + v` and missed the `count = 0` + `for _ in items: count += 1` form (i.e. `len(items)`); `string-concat-in-loop` previously required a literal or f-string RHS and missed `text += str(part) + "."`. Both are the same single canonical family each, so widening adds no duplicate penalty. Each new match is deliberately narrow: the counting rule fires only when the loop discards its target and its entire body is the increment, and the string rule accepts only provably textual operands, leaving numeric and unproven expressions to the reviewer. All 12 pilot reference solutions were re-scanned afterwards and remain free of both families. Regression tests: `tests/test_python_guest.py::test_counting_an_iterable_by_hand_is_reported_as_a_manual_counter` and `::test_string_accumulation_is_detected_for_every_provable_shape`.
- **D-10-04 - Tool caches are not task content.** `python_task_tool.py` refuses to seal or admit a package containing `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.hypothesis`, `__pycache__` or `.cache`. A cached analysis directory under `hidden/reference/` becomes part of the candidate file set through `variant_files`, which both breaks the `solution.py`-only output contract and would leak the reference's analysis state. This was a real authoring failure (`archive-path-guard`, `output-contract-reference`); the guard exists so the slip cannot return silently.
- **D-10-06 - A scan that cannot prove itself is `missing`, never clean.** A required analyzer that crashes, is killed, is absent (exit 127), has an invalid config (exit 2), times out, exits with findings but emits no report, or exits without findings while reporting them, produces one `python.<tool>.scan` observation with status `missing` and zero findings. Profile items fed by that scan become `missing` and the weighted aggregate is withheld. Bandit is the sharp case: it exits 1 both for a finding and for an uncaught crash, so the exit code alone can never be read as a clean result.
- **D-10-07 - Executable admission is not quality admission.** A green suite-admission report means the variants really ran and the oracles really discriminate. It explicitly does not mean performance baselines, judge anchors, scoring replay, production isolation, curator approval or rights confirmation exist. Every report carries `quality_admission: pending` and the committed inventory carries `fully_admitted: 0` and `frozen: 0`.
## Prompt 13 — Performance measurement (PCB-13)

- **D-13-01 — The reserved guest is a separate `PlanRunner` entry point, not a new executor.**
  A normal plan run creates and destroys one guest per plan, which is wrong for measurement: the
  paired algorithm needs candidate and reference on the *same physical worker* for the whole
  measurement window, with each iteration as a fresh process inside it. `PlanRunner.reserved_guest()`
  was added for that, reusing the same `materialize_inputs` digest checks, the same in-guest
  deadline wrapper and the same supervisor-owned execution records. There is still exactly one
  component that talks to the sandbox provider.
- **D-13-02 — Both sides are staged in one guest under separate roots (`cand/`, `ref/`).**
  Re-rooting the candidate-role inputs is the only difference between the two plans;
  `side_invariants()` digests image, flags, resources, exit semantics, tool identity, trusted
  inputs and environment, and the runner refuses to measure if the two sides' invariant digests
  differ. That digest equality is the evidence for Technical Spec 13.1/13.2's "same worker, same
  runtime, same workload" instead of an assertion in prose.
- **D-13-03 — The speed lane is a denylist over argv, environment keys, image and tool.**
  Instrumentation is usually switched on somewhere other than a compiler flag - `COVERAGE=1`,
  `RUSTFLAGS=-Cinstrument-coverage`, a Miri recipe. The check therefore matches bare stems
  (`coverage`, `sanitize`, `miri`, `profil`, `pprof`, `valgrind`, `instrument`) across argv,
  *environment keys and values*, the image reference and the tool identity, and reports
  `where:token` so a reviewer sees which field tripped it. A clean plan is still accepted: the
  refusal is specific, never blanket.
- **D-13-04 — Hardware identity comes from the guest, and is re-checked before every score.**
  The reserved guest reports its own CPU model, machine, logical CPUs, kernel, cgroup memory limit
  and cgroup peak with a bounded python one-liner, so no image rebuild is needed. When the caller
  supplies a recorded baseline, any drift in CPU model/machine/CPU count/kernel aborts the
  measurement rather than producing a number on a different machine.
- **D-13-05 — Shared CI hardware is an explicit blocked gate, not a caveat.** `hardware_gate`
  is `satisfied` only when the worker is declared dedicated; otherwise it is
  `blocked_shared_ci` with the reason that ordinary shared timings cannot establish a production
  comparison. Development-sandbox numbers are still recorded and are still useful as *regression*
  evidence, but they cannot support a ranked efficiency claim.
- **D-13-06 — The canary runs on the trusted reference at the largest declared scale.** A canary
  must itself be stable. At the smallest declared scale a real run took ~20 ms and its own
  relative MAD reached 0.34, which invalidated every block for the wrong reason (host jitter and
  process start-up noise, not drift). Running the canary at the largest scale made it a genuine
  stability signal. The canary measures the *reference* only: it detects contention and throttling,
  and never candidate behaviour.
- **D-13-07 — An unusable frozen canary baseline is an invalid block, not a re-baseline.**
  When the caller supplies no baseline, the first block's median is recorded and labelled
  `measured_first_run`; when one is supplied and the median falls outside ±10% or the relative MAD
  exceeds 5%, the whole block is invalid - including every iteration taken under it. The block is
  still measured and retained as evidence; it just cannot be selected.
- **D-13-08 — Every block's iterations are retained; only the selected block is aggregated.**
  The first *valid* block by time order is selected, never the faster one. In the recorded E2E-19
  run, block 1's canary was faster (21.5 ms vs 32.8 ms median) but noisy, so block 0 was selected.
  Invalidated blocks keep their iterations - a rejected-but-fast candidate is exactly the evidence
  a reviewer needs to see - but `efficiency` reads only the selected block.
- **D-13-09 — Censoring is a bound, and insufficiency is not a score.** A timed-out iteration has
  no duration. The timeout gives a lower bound on the candidate's time; if that bound already
  reaches the breakpoint the time component is zero and labelled censored, otherwise the plan does
  not contain enough information for the transform and the lane reports `incomplete` rather than
  inventing a duration or awarding partial credit. `efficiency_score()` refuses to produce a value
  when either component is missing, so an incomplete measurement can never read as 0 or as 100.
- **D-13-10 — Empirical scale growth is a diagnostic, never a complexity claim.** Each workload
  row reports the observed time and input-size ratio against the lane's smallest scale. The label
  in the contract is `scale_growth`, and no code path turns it into a Big-O assertion.
- **D-13-11 — Prompt 13 does not touch the pinned guest images.** The iteration driver already
  writes a per-iteration JSON record with `elapsed_ns` and `peak_rss_kb`, so Prompt 13 reads it
  back with a typed guest command rather than changing the image and re-digesting every sealed
  manifest. The honest limitation: the per-iteration memory metric is the guest process peak RSS
  (`guest_process_peak_rss_ru_maxrss_kb`), valid because the Python workload runs in-process; the
  container-wide cgroup peak is recorded alongside as corroboration, and a workload that forks
  children would need the cgroup metric per iteration. That upgrade changes image digests and is
  left as an explicit follow-up rather than done silently.

## Prompt 12 — Independent grading and normalized evidence

- **D-12-01 — The evaluator is a graph, not an admission variant.** `SuiteAdmission` (Prompt 10/11)
  proves *tasks* are admissible; it deliberately runs each authored variant and answers "does this
  oracle discriminate?". Prompt 12 needs the opposite direction: a frozen submission in, one
  normalized evidence manifest out, for both languages, without task-authoring knowledge.
  `packages/evaluation/src/polycodebench_evaluation/evaluator.py` therefore takes
  `(FrozenTask, Candidate, candidate_files, overlay, config, allowed_paths, baseline_files)` and
  reuses the same `PlanRunner` and `ExecutableLanguagePlugin` surface as admission. It never calls
  admission code: the acceptance semantics needed by a grader (candidate validation, gate gating of
  quality work, cross-tool dedup, baseline relations) differ from admission's, and folding them into
  one module would have made both harder to verify.
- **D-12-02 — Candidate bytes can never mint control material.** Overlays, `config` inputs and the
  expected inventory reach plans only through `materialize_inputs`' role pools, which the supervisor
  fills from trusted storage; the candidate is not a member of those pools. A submission that
  contains files outside the output contract's `allowed_paths` (for example a replacement copy of
  `tests/test_acceptance.py`) is rejected *before any execution* with `disallowed_paths`, so it
  cannot even reach the test stage. The alternative — silently ignoring extra paths — was rejected
  because a malicious submission should be visible in the evidence, not invisible in it.
- **D-12-03 — "In scope" is decided by file content, not by file list.** Technical Spec 12.4 allows
  unchanged debt to be penalised when it is "explicitly in the task's required repair scope". A
  single-module task lists only the candidate file in `allowed_paths`, so a path test marks every
  carried-over issue as in scope. The evaluator instead marks an unchanged issue
  `unchanged_in_scope` when the candidate's bytes for that file differ from the baseline's (the
  candidate edited the file and could have fixed it) and `unchanged_out_of_scope` when they are
  byte-identical (carried over verbatim). This is the narrowest reading that keeps "unrelated debt
  visible without unjustified blame" (E2E-18) honest; tasks that need finer repair-scope rules can
  express them through their quality plan without changing the evaluator.
- **D-12-04 — Ambiguity is a first-class result, never a silent choice.** If the candidate reports a
  canonical key the baseline does not, but the *same family* existed at the *same path*, the
  relation is `unknown` and the issue is written to `reviews` as `ambiguous_baseline_mapping`. It is
  neither counted as newly introduced (unjustified blame) nor silently dropped (invisible debt).
  Same-family keys that vanished from the candidate are listed in `resolutions` as `resolved`, or as
  `unknown`+ambiguous when the family moved. Prompt 15 decides whether an `unknown` relation may
  carry a deduction; adjudication remains a review-time decision.
- **D-12-05 — Native tool metrics and the PolyCodeBench gate are separate fields.** `native_metrics`
  keeps each tool's own status/finding count (ruff `completed_with_findings` + 4 findings is the
  tool's truth), while `gate`/`group_verdicts` carry the stricter PolyCodeBench verdict. A findings
  exit is a *complete* scan for this purpose: only `tool_error`, `timed_out`, `output_missing` or a
  non-measured scan observation make an analyzer incomplete, and every required analyzer that is
  incomplete adds `required_scan_not_measured` to `incomplete` plus a review item. It is therefore
  impossible for an empty report, crash or unsupported check to read as a clean scan.
- **D-12-06 — Robustness scenarios get full credit or nothing.** No scenario declares partial
  credit, so `ScenarioEvidence` only allows `credit_bp = weight_bp` when every declared repetition of
  the scenario's group passes, or `0`. If any repetition is incomplete, the whole scenario is
  `incomplete` and `robustness_score_bp` is `None` — not a partial number computed after the fact.
  `hard_acceptance` scenarios fail the correctness gate outright; quality-only scenarios only reduce
  the weighted robustness value. Weights are read from the task oracle (which enforces that they sum
  to 10000 basis points), never from observed outcomes.
- **D-12-07 — Property/fuzz identity is what the harness actually ran.** The pytest guest plugin
  registers a derandomized Hypothesis profile and reports its version, example count and shrinking
  policy in the report's session record; the evaluator lifts those facts verbatim into
  `PropertyEvidence` instead of assuming them from the oracle. Rust tasks record
  `cargo-test-source-seeded`, because the property cases there are deterministic PRNG cases whose
  generator lives in the test source, not an external fuzz engine. Nothing here asserts fuzz
  coverage, corpus digest or time budgets; those remain N/A for these adapters.
- **D-12-08 — A duplicate report is kept as evidence but counted once.** `plugin.normalize()` decides
  the canonical entry (context verdict wins, otherwise highest severity), and the evaluator's
  `IssueEvidence.tools` lists *every* check id that reported the key. This was found by real
  execution: an earlier version grouped the already-normalized observations, which silently dropped
  the second tool's name and made a genuinely deduplicated issue look like a single-tool finding.
  The manifest therefore keeps the pre-normalization member list for provenance and the normalized
  observation for severity/owner.
- **D-12-09 — Evidence artifacts are digest-addressed, and rejected submissions are still evidence.**
  `raw_artifacts` records stage, path, digest and size for every plan output actually collected
  (build reports, test-report JSONL, analyzer JSON). A submission rejected in step 1 or at build time
  still produces a manifest, with `gate: fail`, the rejection reason in `gate_reasons` and the
  collected raw artifacts attached — a rejection is a result, not an absence of one.

## Prompt 11 — Rust (PCB-11-1: toolchains and evaluator identity)

- **D-11-01 - The Rust wheelhouse is a components *image*, not a directory of files.** `rustup` unpacks a component and then deletes its payload, so unlike Python wheels there is nothing file-level to vendor or hash-verify per package. The faithful analogue of the Python wheelhouse download is therefore an image that already contains every pinned component. `scripts/fetch_rust_components.py` builds it once and is the only step in the Rust pipeline that uses a network; `build_rust_images.py` then builds all three recipes with `--network none`.
- **D-11-02 - Recipe distinctness is enforced, not asserted.** The DoD asks for distinct regular/instrumented/performance recipes. Distinct *digests* are not sufficient evidence: an earlier build produced three differently-tagged images whose contents were identical, because every recipe copied the same complete toolchain home. `require_distinct()` now fails the build unless the analyzers actually *run* in the evaluator image, are actually absent from the other two, and the three digests differ. Note that `command -v` cannot detect this, because `rustup` installs proxy shims for every tool regardless; only execution proves a component is present.
- **D-11-03 - Absence is recorded, not omitted.** Every recipe is probed for all five tools, so a tool an image does not ship is recorded as `absent`. Recording only the tools a recipe was expected to provide made a missing tool indistinguishable from an unprobed one, which is exactly the ambiguity that hid the defect in D-11-02.
- **D-11-04 - The performance recipe fixes codegen in the image.** Measurement profile settings (`opt-level`, `codegen-units`, `debug-assertions`, `overflow-checks`, `panic`) are baked into the image's cargo config rather than left to each task's manifest, so timings are comparable across candidates and reruns. An earlier version wrote an env file that nothing read, which made the recipe a label with no behaviour behind it.
- **D-11-05 - Miri's sysroot needs vendored crates.** Miri interprets `std` from source and builds a sysroot that resolves real crates from crates.io. A scored run is offline, so those crates (1262 files here) are vendored into the components image and `CARGO_HOME` source replacement is baked in. The vendor set is whatever `cargo miri setup` actually resolves, so it cannot drift from the pinned nightly. An incomplete vendor set makes the sysroot build fail loudly, which is classified `failed` — never clean.
- **D-11-06 - A `Cargo.lock` that does not pin is rejected.** The DoD makes the lock part of the evaluator identity, which is only meaningful if the lock actually fixes the resolution. A registry package with no cargo checksum is refused (`LockError`) rather than digested, because such a lock would let the identity drift silently between two runs that look identical. The digest is taken over a canonical, sorted resolution so re-ordering or re-formatting the file is not a spurious identity change while a real dependency change is.
- **D-11-07 - The language plugin stays an adapter.** `lang_rust` is permitted to import only `core` and `plugins_api`, enforced by `check_boundaries.py`, exactly as for `lang_python`. No Rust code executes a process: the plugin describes plans and parses recorded bytes, and the supervisor owns isolation.
- **D-11-08 - The allowlist entry is now live.** `config/plugins/allowlist-v1.yaml` registers `polycodebench_lang_rust.plugin:RustLanguagePlugin` and the module exists as of PCB-11-2. The digests are those of the rebuilt images (the allowlist is regenerated by `scripts/build_rust_images.py`).
- **D-11-09 - Evidence boundary.** All of this is `development_sandbox` tier on local Docker. Registry publication and production-worker pinning are deployment inputs, and production isolation remains the owner-deferred Prompt 06 gate. No cloud, registry or paid-provider action was taken.
- **D-11-10 - The Rust images carry the pinned Python interpreter, started through its own loader.** The sandbox provider drives every guest with `python -I -B -S`, and the Rust base image ships no interpreter. The interpreter of the Python images (glibc 2.41) is copied with its own libraries into `/opt/pcb/python` and launched by a one-line `/usr/local/bin/python` through `ld-linux --library-path`, so rustc/cargo and the test binaries keep linking against the base image's glibc 2.36. No toolchain, network access or crate is added; the interpreter reference is recorded as `build.guest_interpreter`.
- **D-11-11 - Compiled languages get an explicit `executable_workspace` (default off).** The workspace tmpfs is `noexec` by default, which is right for interpreted guests and fatal for `cargo test`. `ResourcePolicy`/`SandboxSpec.executable_workspace` adds `exec` to that one mount only; the root stays read-only, network `none`, capabilities dropped, uid 65532. A live test asserts that without it a test plan never produces a passing gate.
- **D-11-12 - Miri uses the sysroot baked into the evaluator image (`MIRI_SYSROOT`).** The sandbox cannot rebuild one (read-only root, noexec `/tmp`), and Miri's own freshness check proved sensitive to how the cache was warmed. Plans set `MIRI_SYSROOT=/opt/pcb/cache/miri` so the interpreter uses it as built; the image build still warms the cache with the plan's `cargo miri test --test` shape.
- **D-11-13 - Guest scripts only capture; classification runs in the trusted parser.** `pcb_rust_run.py` captures output, enforces the deadline from inside the guest (so a hung test leaves partial output) and removes `target/`. Libtest parsing, the Miri classifier and the Clippy/context/dependency parsers run in the plugin on recorded bytes, so candidate-controlled text never decides its own verdict and the logic is unit-testable against recordings.
- **D-11-14 - A clean Miri result needs libtest's own summary.** Exit 0 with empty or garbled output proves nothing ran. `clean` requires a `test result:` line; test failures under Miri without UB are still `clean` for UB purposes (the acceptance gate owns them).
- **D-11-15 - The dependency audit fails closed until a real advisory snapshot exists.** `pcb_lock_audit.py` exits 2 on an empty snapshot and the parser reports the scan `missing`. The shipped `snapshot.json` is an explicitly labelled unpopulated placeholder: vendoring RustSec needs a network fetch, which is an owner action, and an empty snapshot must never certify a lock.
- **D-11-16 - Suite admission is language-neutral through plugin hints, not a fork.** The Python engine assumed `.py` candidates, an overlay keyed by `tests/...` and `python.<tool>.scan` names. It now reads `overlay_prefix`, `candidate_suffixes` and an optional `trusted_inputs(files, view)` from the plugin (Python's defaults are unchanged, and its 65 tests still pass) and derives scan names from `plugin.language_id`. `variant_files` resolves the variant root from the output contract (`hidden/reference/src/lib.rs` ends with the allowed path `src/lib.rs`), so a crate keeps its `src/` layout while single-module languages behave as before.
- **D-11-17 - Pilot clusters are authored in parallel but admitted centrally.** Three authoring agents produced the packages; every package was then re-admitted by one admission run through the supervisor, and that run's report (not the agent's) is the evidence the inventory cites. The authoring reports are retained separately. Authoring never edited shared code, and tooling doubts were reported rather than worked around.
- **D-11-18 - Rust pilot tasks declare no performance workload.** Performance parsing and baselines are Prompt 13. The performance plan and image exist (PCB-11-1/2), but no task opts in, so `efficiency` is not an applicable dimension for any Rust pilot cluster and the inventory says so.
- **D-11-19 - A faulty variant declares required-group failures only.** `known-fault-rejection` compares a variant's declared `failing_cases` with the cases failing in *required* groups. Quality-only (robustness) cases are evidence for the robustness dimension, not part of the acceptance gate, so declaring one would fail the check; authors declare acceptance cases only.
- **D-11-20 - Dataset-split assignment stays unassigned.** The inventory records the visible/hidden file split of each package, but which pilot tasks are development, calibration or scored is a curator decision at freeze; the inventory says `unassigned` instead of choosing.
- **D-10-08 - Pilot task assets stay private.** Hidden bundles, reference solutions, faulty/alternative/defective variants and admission reports live under git-ignored `.protected/`. The committed `taskpacks/python-pilot/inventory.yaml` carries identities, digests, cluster metadata and status only, so the pilot corpus cannot leak through the repository.
- **D-10-09 - Guest scripts are excluded from strict mypy by documented override.** `polycodebench_lang_python.guest.*` and `polycodebench_runner.guest_helper` are plain-stdlib, POSIX-only sources copied verbatim into the pinned images; annotating them for a Windows-hosted type check would misrepresent how they execute. They remain covered by the image tests and the Docker conformance run. The plugin, profile, plans and parsers are type checked strictly and pass with no findings.
- **D-10-10 - Evidence boundary.** Images are local development builds; the sandbox is local Docker, not a production worker. All admission and conformance evidence is `development_sandbox` tier. Registry publication, production-worker pinning and production isolation are deployment/owner-deferred Prompt 06 work, and no live provider, judge or cloud action was taken.
- **D-10-05 - Anti-pattern fixtures are held to their pre-registered declarations.** `expected_issue_families` is authored before execution. When a fixture failed to demonstrate a declared family, the fix was either to correct a detector that was genuinely too narrow (D-10-03) or to make the defective variant actually exhibit the anti-pattern it claimed — never to delete the declaration and weaken the check. `ledger-stream`'s defective variant gained a real index-only loop; `rate-limiter`'s unused `seen=[]` default now actually mutates, which is what makes it a shared-state defect rather than a benign one.
- **D-10-11 - Test evidence is trusted only as far as the guest isolates the candidate (review finding, residual risk).** The trusted pytest reporting plugin, the hidden overlay tests and the candidate's `solution.py` run in one interpreter inside one guest. Candidate code could in principle read the overlay tests, write to the report file descriptor, or tamper with `tests/` at import time. Technical Spec 10.4 states the same limit (tests are withheld during solving but "not assumed impossible to inspect at grading runtime"). Mitigations in place: overlays and configuration are materialised only from trusted roles, the output contract limits candidates to declared files (`PYTHONPATH` puts the guest tree before the candidate directory), the control record must contain `session_finish`, the inventory reconciliation rejects unknown records and missing required cases, and the guest is network-less and short-lived. Not mitigated: an adversarial candidate that forges its own report. A stronger boundary (grader process outside the candidate interpreter, per-run report nonce, separate result channel) is deferred to the generic evaluator (Prompt 12) and the production tier (Prompt 06); development-tier admission evidence must not be read as anti-tampering proof.
- **D-09-10 - Evidence boundary:** every model response in these tests is a scripted FIXTURE; the sandbox is local Docker (development isolation); the database and artifact store are real. E2E-13 and E2E-14 passed at that level. Live model behaviour, provider tool-call conventions and the two-model pilot remain Prompt 17, and production isolation remains the owner-deferred Prompt 06 gate.
- **D-09-11 - Independent review:** an adversarial review of this prompt is recorded in `docs/implementation/reports/prompt-09.md` with each finding's disposition.
- **D-09-12 - Open limitation: run `budget_profile` is not bound to the protocol budget:** each frozen protocol embeds its own `solve_budget` (mirroring `config/budgets/pilot-v1.yaml`, proposed and not authorized for live spend), and the session enforces that embedded budget. `DatabaseAssignmentLoader` does not read the run configuration's `budget_profile` or compare it with the protocol budget, so a run naming one profile could execute under a different protocol's limits without error. Not fixed in Prompt 09 because the profile-to-protocol mapping is undecided. Required before live spend (Prompt 17): load the budget profiles, fail closed when the named profile is missing or differs from the protocol budget, and pin the check with a regression test.
# D-16-01 - Local reviewed release boundary

Prompt 16 uses transactional SQLite for local release state and compare-and-swap board pointers.
The publisher accepts allowlisted aggregate projections, requires validator receipts bound to the
exact source and projection, and signs canonical manifest bytes with Ed25519. Signing keys stay
outside workers. The CLI is for trusted local administration; role flags are not remote
authentication. Public deployment is deferred to the authorized deployment prompt. Synthetic
fixtures are labeled internal/exploratory and cannot support benchmark claims. The optional
full-product editorial index is disabled.

# D-17-01 - Fixed bounded pilot plan and dispatch gate

The exploratory plan uses `single-shot-v1`, three planned samples per task/model, master seed
`17017`, and a maximum of three provider deliveries per logical attempt; all attempts are retained
and best-answer selection is disabled. Both language rosters contain 12 executable-admitted
clusters, but none is rights-cleared, quality-admitted, curator-frozen, registered to the hidden
lane, or admitted on a production worker. The two candidate identities/prices, active hard spend
limit, distinct calibrated judge, production services, and run-start entrypoint are unresolved, so
all 144 logical attempts remain held before dispatch. The plan and preflight evidence contain no
provider outputs or benchmark claims.

# D-17-02 - No invented run command

`pcb-model plan` only calculates compatibility and a bounded cost plan. The repository has no
operator run-start CLI or HTTP route; `RunCreationService` is an internal use case and requires
already frozen database identities. Do not report a fabricated `pcb run` invocation as executable.
The planning invocation is documented in `reports/prompt-17.md`; a supported authenticated run
entrypoint is a concrete software prerequisite to resuming this pilot.


# D-23-01 ? C++ executable name and typed tool identity

The C++ executable remains `clang++`, but the shared `ToolIdentity.name` is a `Slug` and cannot contain `+`. Plans now use the stable identity slug `clang-plus-plus`; identity resolution maps it to the exact `clang++` key in the C++ image record. Command argv continues to invoke `clang++`. This keeps executable semantics intact while satisfying the typed contract.

# D-23-02 ? Fail closed on incomplete language identities

A language capability/profile entry is not evidence of an admitted image or task. Java remains outside the production allowlist until its runtime/evaluator/performance images are built, probed and admitted. JavaScript and TypeScript have separate source plugin entrypoints and shared plan/output/grading code, but remain outside the production allowlist until their complete image identities and task packs are admitted. The audit reports these gaps instead of promoting source code or a profile label into a supported capability.

# D-21-01 ? The shared evaluator asks for `language_profile`, not a per-language attribute

`evaluator.py` resolved a language profile by duck-typing `python_profile` then `rust_profile`.
Every other plugin publishes `language_profile` under the shared `LanguageProfileEvaluator`
protocol, so for C, C++, Go and Java the lookup silently returned `None`: findings were still
recorded and reached the scorer, but no profile item was ever scored, so a language's whole
quality section read as empty rather than unevaluated. The evaluator now reads the protocol
attribute. Python and Rust also publish `language_profile`, so their behaviour is unchanged; their
existing per-language accessors are left in place as aliases.

This is a scoring-interpretation change for any language other than Python and Rust, so it is
recorded here rather than treated as a refactor. It raises scores for those languages from
"unscored" to "scored"; it does not change any published score that was already computed.

# D-21-02 ? An analyzer that printed nothing is missing evidence, not a clean scan

`scan_observation` already distinguished `findings=None` (`MISSING`) from `findings=0`
(`MEASURED`). The C++ clang-tidy parser reported `findings=0` whenever its output was not in the
one stream it read, so a candidate that merely made clang-tidy unreadable scored full marks on
every clang-tidy-fed item -- the exact inversion the PCB-21-2 DoD forbids. The parser now reads
both captured streams and raises when a successful run yielded no readable diagnostic, which the
existing `_guard` converts to `MISSING`. The counterpart assertion for the context scanner
(`complete: false` -> raise) is the in-repo convention this follows.

# D-21-03 ? Instrumented fixtures declare lane expectations

Four C++ and four C fixtures declared `expected_lane_findings`, and one each declared
`candidate_crash`/`build_error`, but the shared `FixtureExpectation` schema rejected them, so those
manifests could not validate. Rather than delete the declarations or special-case C++, the schema
gained the two outcomes an instrumented language genuinely has, and `SuiteAdmission` now analyzes
any fixture that declares lane findings and checks them. C++ lane declarations name the profile's
equivalence families (`manual-ownership`, `undefined-behaviour`, `data-race`), not sanitizer
wording, because those are the keys observations actually carry.

# D-22-01 ? The Go build cache lives in the workspace, not in the image

The recorded Go admission failed every one of its six variants with
`build:build harness error (tool_error)`. The cause was not the task code: the plans set
`GOCACHE=/opt/pcb/cache/go-build` and the sandbox runs each guest with `--read-only` as an
unprivileged uid that owns only `/workspace` and `/tmp`. `go build` therefore failed before
compiling anything, with

    failed to initialize build cache at /opt/pcb/cache/go-build: mkdir ...: read-only file system

Exit 2 is in the plan's declared `TOOL_ERRORS`, so the supervisor read the run as a *harness* error
rather than a candidate failure, and `parse_build` returned `incomplete` for all six variants. The
asymmetry in the failed report is the proof: `go.gofmt.scan` and `go.context.scan` were `measured`
while `go.vet`, `go.staticcheck` and `go.gosec` were `missing` — those three load packages through
the build cache, and the first two do not.

`GOCACHE`, `GOMODCACHE`, `GOPATH` and `GOTMPDIR` now point under the workspace tmpfs, which is
writable, sized from the plan's own disk budget and discarded with the container. `GOTMPDIR` is
included because the default compile work directory is `/tmp`, which the sandbox caps at 16 MiB; a
build that fills it dies with `ENOSPC`, which is indistinguishable from a candidate build error.
The guest runner creates the four directories, because Go creates three of them itself but
*requires* `GOTMPDIR` to exist.

This is a defect in the environment the plans assumed, not in the exit contract: a tool that
cannot start is a tool error, and that classification was correct.

# D-22-02 ? Instrumentation is a per-recipe contract, not an image capability

The Go build's distinctness gate failed while `require_distinct` demanded that the *performance*
image be unable to run `go test -race`. It can: the race detector ships inside the pinned
`golang` base, so every recipe has it. Demanding a missing feature would mean shipping a different
toolchain to obtain a property the scoring rule actually cares about.

The C/C++ plugins already solve this (D-20-x): sanitizer runtimes ship with clang, so
`ImageIdentities.require_release_recipe` refuses any measurement plan whose recipe does not record
`instrumentation: none`. Go now records the same three fields — `instrumentation` and
`accepts_instrumented_plans` per recipe, declared in `infra/images/go/recipes.yaml` — and both
guards are wired in: `performance_plan` calls `require_release_recipe("performance")` and the race
plan calls `require_instrumented("runtime")`. The build-side gate checks the *declaration*, and
separately refuses to declare a recipe instrumentable if that image cannot run `-race`.

The second guard is the one that stops a false clean: a race plan aimed at a recipe with no
detector would report `clean` for a race that was never looked for.

# D-22-03 ? A plugin declares where its hidden overlays live

Go's hidden suite is at `hidden/topwords/*_test.go`, not `hidden/tests/`, because a Go test file
must be compiled as part of the package it exercises and may use unexported identifiers. The
shared `SuiteAdmission` hardcoded `("hidden/tests/", "hidden/perf/")`, so Go's test plans raised
`PlanInputError: missing overlay input 'work/topwords/behaviour_test.go'` for every variant.

Rather than add a `if language == "go"` branch to the engine, this follows the existing
`overlay_prefix`/`candidate_suffixes` extension points: the engine reads `overlay_roots` and
`overlay_suffixes` off the plugin, defaulting to today's Python layout. Go declares
`overlay_roots = ("hidden/topwords/", "hidden/perf/")` and `overlay_suffixes = ("_test.go",)` — the
suffix matters because that directory also holds nothing else, and a package directory may hold
non-test files a task author adds later.

# D-22-04 ? The image build refreshes the allowlist

`assert_plan_allowed` rejects any plan naming an image that is not in
`config/plugins/allowlist-v1.yaml`, so a build that updates only `config/images/go-v1.json` leaves
every Go plan unrunnable. Every other language builder rewrites that file; Go's did not, which is
how the Go allowlist kept naming three digests that no longer existed.

`build_go_images.write_allowlist` now parses the document and replaces only the Go entry's digest
list. It deliberately does not patch the file with a regex: a regex-based version of this edit
silently deleted the C and C++ entries during development, which is the exact class of failure the
function exists to prevent.

# D-22-05 ? Analyzer cleanliness is read from stdout, never from a missing file

Two required Go scans reported `missing` on a correct candidate, for two different reasons that a
file-based convention cannot distinguish from a crash.

**staticcheck** keeps its own fact cache under `$HOME/.cache`. The guest runs with no `HOME`, so
it resolved to `/.cache` and failed with `read-only file system` before analysing anything. Every
plan now sets `HOME` and `XDG_CACHE_HOME` into the workspace tmpfs, and the guest runner creates
them. This is the same class of defect as D-22-01: the tool needed a writable path and the
environment did not give it one.

**gosec** was told `-out=out/gosec.json`, and that file is written *only when gosec has a finding
to report*. A clean run leaves the path non-existent, which the parser - correctly refusing to treat
an absent report as a clean scan - reported as `missing`. So the reference solution was penalised
for being clean, which is the exact inversion PCB-22-2's DoD forbids. gosec writes its JSON report
to stdout, so the plan captures that stream and `-out`/`-quiet` are dropped: `-quiet` additionally
suppresses the per-issue lines that make a partial run legible.

The parser's original behaviour is kept. A run that produces nothing on stdout still yields
`MISSING`, because that is what a gosec that died before reporting looks like. What changed is that
"clean" is now an observable fact - an empty `Issues` list in a report that exists - rather than the
absence of a file.

# D-22-06 ? The analyzer cache is pinned with XDG_CACHE_HOME, not HOME

The fix for D-22-05 first set `HOME` into every Go plan's environment and was refused:
`ExecRequest.safe_environment` (packages/runner/src/polycodebench_runner/contracts.py:108) treats
`HOME` as protected, alongside `PATH`, `DOCKER_*`, `AWS_*`, `PCB_*` and `SSH_*`. That guard is
deliberate — a candidate must not be able to steer where the toolchain reads from — so the plan
pins `XDG_CACHE_HOME` instead, which is what staticcheck consults first. Verified against the
built image: with `XDG_CACHE_HOME` set, `staticcheck -f json ./...` exits 0 and creates its fact
cache under that path.

# D-22-07 ? An empty stream after a completed run is a clean scan

`staticcheck` prints one JSON object per finding and **nothing at all** when there are none; `gosec`
does the same on stdout. Both parsers treated an empty stream as "no report" and returned `MISSING`,
so the reference solution — the cleanest code in the package — was the only candidate those two
required scans could not measure. That is the inversion PCB-22-2's DoD forbids, reached from the
opposite direction to D-22-05: there the report file was absent because the tool had nothing to say,
and here the stream is empty for the same reason.

The fix is to stop treating "empty" as a synonym for "absent". The `_guarded` prologue has already
classified the execution from supervisor evidence alone: a timeout, a tool error and a missing
required output are all `MISSING` before any parser body runs. If the body is reached with status
`completed`, the tool ran and reported nothing, which is a measured clean scan. A body reached with
`completed_with_findings` and an empty stream stays `MISSING`, because a tool that exits nonzero
having printed nothing has not told us what it found.

So the parsers keep the strict behaviour where it is warranted and yield `findings=0` only where the
supervisor's own record proves the tool ran to completion.

# D-22-08 ? The stress oracle asserted the wrong tie-break

`TestLargeInputFinishesWithCorrectCounts` failed against the *reference* solution:
`large input[2]: got {delta 20000} want {gamma 20000}`. All four words occur 20000 times, so the
case is decided entirely by the tie-break. The visible contract says "Words with the same count are
ordered alphabetically", which is `alpha, beta, delta, gamma`; the test's `want` list was written in
insertion order and asserted `gamma` before `delta`.

The reference was right and the test was wrong. The test was corrected to the contract's order, and
a comment now states that the case is a pure tie-break check, because a reader comparing the list to
the `builder.WriteString` line above it would otherwise reasonably read the insertion order as
intended.

This is recorded because it is the kind of defect that makes an oracle look like a solution bug: the
admission reported `candidate_timeout:collection` for every variant, which pointed at the harness,
not at a test whose expectation contradicted its own contract.

# D-22-09 ? The Go suite budget is 100s, not 60s

A cold `go test` in this container takes roughly 40 seconds, almost all of it compiling the standard
library into an empty build cache; the test bodies themselves run in 0.02s and 1.6s. Against a 60s
suite budget, five acceptance repetitions plus the three-repetition stress group ran at 44-54s each
and any load on the host pushed them past the deadline, which the parser then reported as
`candidate_timeout:collection` - a candidate fault for what was host contention.

`suite_timeout_seconds` is raised to 100, which is still inside the plugin's own `MAX_PLAN_SECONDS`
of 110 and inside the driver's 120s exec cap. The value is a real budget, not a workaround: a
candidate that genuinely hangs still hits the deadline, and the `timeout-case` fixture exists to prove
that path.

# D-22-10 ? InventoryGroup carries the group classification

`SuiteAdmission.evaluate` decides how many repetitions to run from
`inventory[group.group_id].classification`, and the field existed on every plugin's `OracleGroup`
but not on the shared `InventoryGroup` the engine actually reads. Every plugin's
`inventory_document()` omitted it, so the attribute access raised `AttributeError` the first time a
task declared a `quality_only` group with its own repetition count - which is exactly the case
`suite_timeout_seconds: 100` and the three-repetition stress group created.

`InventoryGroup.classification` now carries it with a default of `acceptance`, so a plugin that has
not declared it keeps today's behaviour, and the Go inventory populates it from the oracle. The
field is a declaration of *what a group is for*, not a scoring knob: acceptance groups are re-run
once per evaluation repetition because their verdict gates the candidate, while quality-only groups
keep their own count so a resource leak or a race can never become a wrong-answer failure.

# D-22-11 ? The race fixture asserts the evidence its task can actually produce

`race-defective` declares `expected_issue_families: [data-race]`, but `top-words` is a
deliberately nonconcurrent task: its quality plan sets `race: unsupported`, `race_groups: []` and
declares no `goroutines_channels` opportunity, so no race plan is built and the detector never
runs. The expectation was unreachable by construction, and the admission check reported it as
`quality-defect-detected: fail` even though the variant's actual findings
(`goroutine-lifecycle`, `string-building`) were exactly right.

The expectation was corrected to the families this task produces. The alternative - flipping the
task to `race: required` so the declared expectation became true - would have been the wrong fix: it
would make a counting task declare a concurrency opportunity it does not have, purely to satisfy a
manifest. That is precisely the false charge PCB-22-3's DoD forbids.

The defect itself is real and was verified directly rather than assumed: running
`go test -race -count=1 -run ^TestTiesBreakAlphabetically$` against this variant's sources in the
pinned runtime image reports `WARNING: DATA RACE` and fails. The context scanner, by contrast,
reports only `accumulator-in-loop` for it (checked rule by rule), which maps to the `string-building`
family. The expectation was therefore set to the family the task can observe.

The measured data-race path is not left uncovered. `scripts/go_conformance.py`'s
`race-applicability-is-contextual` case runs the same defect through a *concurrent* view of the task
(`race: required`, `goroutines_channels: 1`) in the real runtime image and asserts
`go.race.scan = measured 1` with the finding owned by robustness, alongside a clean control run that
must measure zero. Coverage moves to where the evidence exists rather than being deleted.

# D-22-12 ? An empty structured report is a result, not an absence (supersedes D-22-07's mechanism)

D-22-07 fixed this on the parser's `status == "completed"`. That was not sufficient, and the
admission said so: after the fix, `go.staticcheck.scan` was still `missing` while exiting 0 with no
timeout. Instrumenting `plan_status` showed why - it does not return `completed` for a zero-byte
required structured output. It returns its own status:

    "empty_report",  # a successful structured report is present but contains no evidence

That status exists precisely for this shape (results.py:26, produced at results.py:111-117), and the
Go parsers were not handling it. staticcheck and gosec now accept `completed` *or* `empty_report` as
a measured clean scan.

The distinction that keeps this honest is the exit status. A tool that exited nonzero having printed
nothing has not said what it found, so it stays `MISSING` - the guard only widens to the case the
supervisor itself proved was a successful run with nothing to report.

`tests/test_go_plugin.py` now pins both directions:
`test_a_clean_staticcheck_run_is_measured_zero_not_missing` and
`test_an_analyzer_that_died_before_reporting_is_still_missing`. The first was mutation-checked:
removing `empty_report` from the accepted statuses makes it fail with
`incomplete: staticcheck produced no report`, so the test pins the behaviour rather than the code.

# D-22-13 ? Go fixture bytes are LF, and the repository says so

Every Go fixture variant and the reference reported a `formatting` finding, and
`test_go_docker.py` failed its reference case with `gofmt.scan value=1`. `gofmt -d` on the reference
showed the entire file rewritten - not a formatting defect but a line-ending one: the working tree
had checked the sources out as CRLF under `core.autocrlf=true`, and `gofmt` accepts LF only.

This matters more than a cosmetic finding. `gofmt` is a required analyzer whose findings are
scored, so a developer's checkout configuration silently decided that the *reference solution* was
misformatted. The same bytes produced by two different clones would have scored differently.

Fixed by adding a `.gitattributes` (`* text=auto` plus `*.go`, `*.mod`, `*.sum` as `text eol=lf`) and
normalising the ten checked-in Go fixture files to LF. `.gitattributes` alone would only fix future
checkouts; the working tree had to be corrected for the run in progress to be valid.

The same trap exists for the Go sources embedded in `scripts/go_conformance.py`: they are Python
byte literals, so they inherit the Python file's endings. `go_source()` now normalises them, because
otherwise the clean-sample case - the one that asserts correct code is *not* penalised - fails on a
CRLF checkout, which is the precise false positive the case exists to prevent.

# D-23-01 - Java measurement modes are task-frozen and image-declared

The Java performance image declares the complete `cold` and `steady-state` JVM flag sets and fixed
warmup/measurement counts. A task freezes one of those mode names at admission. Cold execution uses
`-Xint`; steady-state execution uses a single active processor and the fixed JVM options recorded
in `config/images/java-v1.json`. The runtime and evaluator images do not contain these measurement
policy files or static analyzers. Unknown modes fail closed, and plan construction has no candidate-
specific timing or output input. This keeps the interpretation of a Java timing invariant across
candidates and makes image digest changes visible to task resealing/admission.

# D-25-01 - Gate precedence over judgments is structural, not advisory

The repository-task grader computes the mandatory executable gate before any judge result is
read (`executable_gate`), and `combine_gate` accepts no scores: it can only add failing or
incomplete conditions and never rescind a failure. The scorer's `Scorecard` contract already
forbids nonzero contributions on a failed gate, so the DoD "judgments cannot override failed
mandatory tests" holds at two independent layers. Production orchestration checks
`judge_is_wanted` and does not spend judge budget on a gate-failed candidate; an operator may
still request a diagnostic judgement, and the regression case proves a perfect judgement over a
functionally failing patch still scores 0.000000.

# D-25-02 - Judge outcomes reach the scorer through one adapter, and judge evidence is labelled

`packages/scoring/src/polycodebench_scoring/judge_evidence.py` is the only conversion between
`ItemOutcome` (judge services) and `RubricItemEvidence` (scoring): `ready` becomes `measured`
with a half-even basis-point mean, `needs_review` stays blocking review evidence with no score,
`infra_blocked` becomes `missing`, and a judgement that misses a frozen item produces an
explicit missing row rather than silently scoring a subset. Because the judge panel is
unprovisioned (`config/judging/panel-v1.yaml`), all Prompt 25 judge evidence is deterministic
fixture votes through the real build/parse/aggregate services and is labelled
`fixture_judge_votes` in every report; no live judge call is made or claimed.

# D-25-03 - Baseline-aware repo-task quality evidence: legacy debt is context

Convention analysis runs on the frozen baseline and the candidate workspace and shares the
evaluator's relation vocabulary (`baseline_relations`). The frozen authoring contract declares
`baseline_penalty_relations` (default `introduced`, `worsened`; overridable before freeze per
`config/scoring/evidence_ownership.yaml`); `unchanged_out_of_scope` findings are visible context
and never a penalty, and the new-code scope is exactly the candidate's changed files, so
unchanged files are never scored as new code. The convention rules are the machine-ownable
subset of the repository's written conventions; whether a construct communicates or a
duplication is acceptable stays with the frozen residual rubric items, each carrying a residual
reason.

# D-25-04 - Repo-task scoring applicability is code_quality at this pilot step

The repo-task quality plan declares `applicable_dimensions: [code_quality]`, so the scorer's
frozen six-item code-quality weight plan is exercised end to end (judge-backed items) while
idiomatic/robustness/scenario evidence and efficiency measurement stay out of scope and are
declared not applicable rather than fabricated. Expanding applicability requires language
profile/scenario evidence and is left to the calibration prompts.

# D-24-01 - "Native-compatible" is labelled `inspired`, not `native`

Prompt 24 must "admit native-compatible and deliberately adapted/ported fixtures" whose
methodology labels "differ correctly", while also using "only available allowed source assets;
unavailable/private datasets are explicit blockers rather than invented fixtures labeled official".
The E2E-36 evidence column names the labels `Native/adapted`, so those two constraints pull in
opposite directions and the choice is recorded rather than made silently.

Three documents constrain the label, and the two *method* documents agree:

- `docs/methodology/swebench.md`: "Locally authored tasks that follow similar repository-patch
  patterns are labeled **SWE-bench-inspired** and are not mixed into official SWE-bench scores."
- Architecture A §2: "Custom tasks following the pattern are labeled 'SWE-bench-inspired'."
- `docs/methodology/source-terms-register.md`: no SWE-bench dataset, repository, or patch has
  cleared rights, so an official import is a blocker, not a task.
- Against these, `methodology_label` is the *only* provenance field on the public
  `visible-manifest.json`; `source_kind`, `rights` and `attribution` are administrative and absent
  from the public projection. A public reader of a `native`-labelled authored fixture would
  therefore take it for official SWE-bench data - exactly what the pack forbids.

T §17.1 says "the public label MUST use the compatibility level", which is what makes this a real
discrepancy rather than a naming preference: read as *protocol* compatibility, the authored fixture
is native-compatible; read as *data* provenance, it is not native, because T's own native rule
requires "an upstream source URL" that only real dataset data can have.

**Resolution.** The label follows provenance, not shape, and is checked fail-closed:

- authored fixture, native record format and native F2P/P2P evaluation -> `inspired`
- authored fixture with declared departures from a native evaluation rule -> `adapted`, with the
  departures recorded in `protocol_deviations` and required by the record validator
- imported official dataset record with an upstream source URL and a pinned revision -> `native`

`MethodologyRecord` refuses `native` without an upstream source URL *and* revision, and
`validate_methodology` refuses an `inspired` record that carries an upstream source URL, so an
invented fixture cannot be published as official by any route. The deviations register
(`config/methodology/deviations-v1.yaml`) already permits all three labels for `swebench`, and
`never_mix_native_and_adapted_scores_without_labels` is satisfied by attaching the label to every
metric export.

**E2E-36 wording, addressed explicitly.** The scenario's *assertions* are "native metric preserved;
modified rules labeled; candidate digest prevents stale native cache reuse", and the scenario
subject is the "Native repo-suite fixture". All three hold: the native metric is produced by the
pinned upstream evaluator (`swebench==5.0.2`), the modified rules carry the `adapted` label with
their deviations recorded, and the run identity binds task, candidate and evaluator digests. The
evidence column's phrase "Native/adapted labels" is read as "the labels distinguishing the
native-protocol fixture from the adapted one", and the ledger records the actual labels rather than
the shorthand. An owner who reads it as demanding a literal `native` label on an authored fixture
would need cleared official dataset rights first; that import path is implemented and
source-identity-checked, but it has nothing to import today.

# D-24-02 - The native metric comes from the pinned upstream evaluator, or from nothing

PCB-24-2 forbids "loosely recreating its result from a generic test fraction". Two decisions
follow. First, `native_metrics()` accepts the upstream evaluator's own result object and never
recomputes counts; a local test fraction cannot reach it. Second, the upstream package is pinned to
an exact revision (`swebench==5.0.2`) and a mismatch raises instead of degrading: the resolution rule
is version-specific (skip semantics in `test_failed`, the suite-ran evidence that stops a run that
never executed from scoring every F2P test as resolved, and the exit-code cross-check), so two
releases graded by different revisions would not be comparable. The evaluator digest covers the
revision and the upstream entry points the adapter calls, so an upstream change invalidates cached
grades rather than silently reusing them.
