# PolyCodeBench — Codex End-to-End Implementation Prompt Pack

Version 1.0 · 30 September 2026

**35 numbered prompts, 00–34, with engineering tickets, deliverables, definitions of done, phase gates, and a required completion report.** Three auxiliary prompts handle interruption, an external blocker, and revalidation after changes. This pack instructs future implementation; it does not claim that any code or test has already been completed.

## 1. Documents and how to use this pack

Give Codex access to these three Markdown files in the repository/workspace:

1. `PolyCodeBench-Architecture-v1.md`
2. `PolyCodeBench-Technical-Implementation-Spec-v1.md`
3. `PolyCodeBench-Codex-End-to-End-Prompt-Pack-v1.md` — this file.

Start with **Prompt 00**, then give one numbered prompt at a time. The prompt blocks below are the copy-ready instructions; the surrounding phase map helps you choose and review them. Prompt 00 establishes a persistent execution contract so later prompts work in new Codex sessions as well.

Supplying the next prompt authorizes its implementation scope. Within that scope, Codex should implement, verify, fix, and produce the requested evidence autonomously. Each numbered prompt ends with a report and stops; it must not silently run the next numbered prompt. Existing authorization for a bounded live run or deployment remains valid—do not ask for the same permission repeatedly.

The documents define product requirements. This pack defines execution order and evidence. It does not waive missing infrastructure, human calibration, coverage, or correctness gates. A blocked live test must remain blocked even when local code is finished.

### 1.1 Source versions used to prepare this pack

| Document | SHA-256 of the supplied file bytes |
|---|---|
| Architecture v1 | `6dfee84b9787f315d7d5aa7afd9b1de29760aac16b5d3ac887b7ecd76d899d69` |
| Technical implementation spec v1 | `2dbfd0f00c561b9348419a2659794913658fb47d15090e09a3e905a89cdea7bd` |

These hashes identify the source snapshot, not future repository commits. If line endings or later user edits change them, inspect the actual differences and record the new source version; do not reject equivalent formatting or silently assume changed requirements are unchanged.

### 1.2 Reading notation

**A §N** means section N of the architecture document. **T §N** means section N of the technical specification. **WP-NN**, **REQ-NN**, and **E2E-NN** retain the exact identifiers from the technical specification. **PCB-NN-X** identifies an engineering ticket introduced by this pack; it is not a renamed work package.

Use existing repository structure where it satisfies the contracts. Suggested file/module locations are ownership boundaries, not instructions to rewrite working code to obtain prettier paths.

## 2. Phase map

| Phase | Prompts | Main deliverable | Exit gate |
|---|---|---|---|
| Phase 0 — Baseline and execution plan | 00 | Repository gap analysis, execution contract, ticket/evidence ledger | Every requirement, work package and test has a known owner/status; existing work preserved. |
| Phase 1 — Methodology and foundations | 01–05 | Reproducible workspace, schemas, database, artifact storage, task registry and admission policy | Inputs can be validated/frozen with trustworthy provenance; foundational contracts verified. |
| Phase 2 — Python/Rust harness and pilot | 06–17 | Isolated execution, durable jobs, models/tools, language plugins, quality evaluation, scoring, internal pilot | Two real model configurations complete the specified 144-attempt exploratory pilot with evidence and replay, or exact blockers remain explicit. |
| Phase 3 — Track A | 18 | Bug-source construction, detection/adjudication, patch evaluation | Known/false/novel/duplicate findings and repair failures are handled correctly. |
| Phase 4 — Remaining languages | 19–23 | JS, TS, C, C++, Go and Java plugins and admitted tasks | Every required language passes the same extension contract without core rewrites. |
| Phase 5 — Remaining Track B suites | 24–28 | Repository repair, realistic tasks, self-repair, Q&A and prediction | Each family preserves its protocol/native metric and uses only applicable scoring. |
| Phase 6 — Public product | 29–32 | Public API, all seven pages, evidence views and reviewed model submission | Actual published data drives complete public workflows; private data stays restricted. |
| Phase 7 — Operational hardening | 33 | Production-shaped infrastructure, restoration, monitoring and runbooks | Staging deploy/recovery/isolation/withdrawal rehearsals have real evidence. |
| Phase 8 — Final integrated audit | 34 | Full requirements audit, defect fixes, release-readiness report | No unjustified completed gates; every required capability has appropriate end-to-end evidence. |

Phase-ending prompts are **00, 05, 17, 18, 23, 28, 32, 33 and 34**. They must include a phase-wide gate summary as well as their prompt report. The other prompts use the same brief report format for their own scope.

## 3. Prompt-to-deliverable map

| Prompt | Work package | Deliverable |
|---|---|---|
| 00 | Prerequisite to all | Gap map, source manifest, execution contract and complete work ledger |
| 01 | WP-01 | Workspace, dependency locks, CI, startup configuration and methodology register |
| 02 | WP-02 | Canonical types, serialization, JSON Schemas and cross-language fixtures |
| 03 | WP-03 | Database/migrations, repository services, identity, audit and idempotency |
| 04 | WP-04 | Verified artifacts, visibility enforcement and safe lifecycle management |
| 05 | WP-05 | Task packages, admission/freezing, splits and source methodology records |
| 06 | WP-06 | Development and production VM sandbox drivers |
| 07 | WP-07 | Job DAG, leases/fences, recovery, cancellation and capacity slots |
| 08 | WP-08 | Model adapters, endpoint registration, usage and budget gateway |
| 09 | WP-09 | Single-shot and standard agent with durable tools/checkpoints |
| 10 | WP-10 | Python plugin, pinned toolchain, task fixtures and profiles |
| 11 | WP-11 | Rust plugin, pinned toolchain, task fixtures and profiles |
| 12 | WP-12 | Independent grading, analyzer evidence and robustness pipeline |
| 13 | WP-13 | Paired runtime/memory measurements and stability policy |
| 14 | WP-14 | Three-vote judge pipeline, reviewer flow and calibration evidence |
| 15 | WP-15 | Pure scoring, gating, language profiles and deterministic replay |
| 16 | WP-16 | Aggregates, intervals, immutable releases and reviewed publication |
| 17 | WP-17 | Real two-model Python/Rust pilot and internal report |
| 18 | WP-18 | Complete Track A workflow |
| 19 | WP-19, part 1 | Separate JavaScript and TypeScript support |
| 20 | WP-19, part 2 | C support |
| 21 | WP-19, part 3 | C++ support |
| 22 | WP-19, part 4 | Go support |
| 23 | WP-19, part 5 | Java support and complete language coverage audit |
| 24 | WP-20, part 1 | SWE-style/native repository repair adapter |
| 25 | WP-20, part 2 | Independently curated realistic repository tasks |
| 26 | WP-20, part 3 | Fixed-budget self-repair protocol |
| 27 | WP-20, part 4 | Repository Q&A and fact-based grading |
| 28 | WP-20, part 5 | Output/test prediction and complete suite coverage audit |
| 29 | WP-21 | Complete public projections/API and generated client |
| 30 | WP-22, part 1 | Leaderboard, language view and model profile |
| 31 | WP-22, part 2 | Comparison, task explorer and methodology pages |
| 32 | WP-22, part 3; WP-23 | Seventh page plus model-submission approval workflow |
| 33 | WP-24 | Production IaC, operational drills and runbooks |
| 34 | All WP-01–24 | Final integrated audit and verified repair pass |

## 4. Execution contract: applies to every prompt

Prompt 00 must place this contract in `docs/implementation/execution-contract.md`, preserve its requirements, and link this pack. Later prompts read that file first.

### 4.1 Work behavior

1. Read applicable `AGENTS.md`/repository instructions and inspect the current working tree. Preserve unrelated changes. Do not reset, force-push, replace the repository, or delete working modules merely to follow this pack’s suggested structure.
2. Resolve actual source-document paths in Prompt 00. Read the named sections and relevant contracts for each prompt. Reinspect changed source files when their digest changes. Do not work from this pack’s summary alone.
3. Implement the requested behavior in the current repository. Plans, stubs, mocked screenshots, TODOs, and generated interfaces without integrated behavior are not completion.
4. Use existing correct implementations when they satisfy the spec; write a gap map before major refactors. Do not assume this is a different benchmark or an unrelated prior project.
5. Select routine implementation details autonomously. Record them in an ADR only when they affect architecture, public contracts, interpretation of scores, security, operations, or maintenance meaningfully. Do not turn every small choice into an approval request.
6. Respect actual tool/environment restrictions. If a required action is rejected, report the rejected action and reason; never suggest disabling the control. Complete safe, independent work first.
7. Local/reversible coding and ordinary verification are authorized by the prompt. Paid provider calls/cloud provisioning/publication must stay inside an explicitly authorized target, budget and scope. Reuse existing authorization; where it is absent, prepare the exact plan/config first and identify the missing authorization as the final blocker.
8. Do not send messages, open public issues/PRs, or publish results solely because a prompt creates local changes. Use repository/user authorization for external writes.
9. Run relevant checks, fix failures, and repeat only what the change or remaining risk requires. Do not rerun expensive live campaigns reflexively.
10. Stop after the current numbered prompt and its report. Supplying the next numbered prompt is the handoff; do not ask the user to approve work already authorized within the current prompt.

### 4.2 Truthful evidence

- Preserve REQ, WP and E2E IDs. Every implemented ticket links to requirements, changed paths and appropriate verification.
- Distinguish fixture, unit, integration, browser, real sandbox, live provider, human review, and production-shaped evidence. A passing fake transport does not satisfy E2E-31.
- “Test written” and “test passed” are different. “Code implemented” and “phase accepted” are different. Missing credentials, data rights, human judgments or hardware remain explicit blockers.
- Some E2E scenarios span several prompts. Mark the tested subcase and environment; keep the full scenario pending until all its required variants have run. Do not mark E2E-25/26/39 complete because one early route passed.
- Do not invent benchmark outcomes, provider usage, pricing, confidence intervals, source licenses, tool capabilities or human approvals.
- Keep model failures in the denominator, gate quality after correctness failure, preserve unknowns/N/A, and separate generation from evaluation retries.
- Never weaken acceptance criteria, skip a required analyzer, raise budgets, expose hidden data, change cohort weights or substitute an easier task to make a gate pass.
- A specification discrepancy is recorded with evidence, affected clauses, proposed resolution and scope. For nonbreaking implementation clarifications choose the narrowest compatible interpretation. For material product/scoring/security changes, prepare a concrete amendment and leave the affected gate pending for the owner’s decision.

### 4.3 Persistent implementation ledger

Maintain these artifacts inside the repository, adapting paths once if existing conventions require it:

| Artifact | Required content |
|---|---|
| `docs/implementation/source-manifest.json` | Actual source paths, versions and hashes |
| `docs/implementation/phase-map.md` | Prompt/phase sequence and phase gate state |
| `docs/implementation/tickets.md` | Every PCB ticket with owner prompt, dependencies, implementation status, verification status, acceptance criteria and evidence |
| `docs/implementation/requirements-matrix.md` | REQ-01–14 → WP/tickets → code paths → E2E evidence |
| `docs/implementation/e2e-matrix.md` | E2E-01–43 and required variants/environments, actual status, commands and artifacts |
| `docs/implementation/decisions.md` | ADR/discrepancy index, including explicit “none” when appropriate |
| `docs/implementation/commands.md` | Verified setup/test/run/recovery commands for this actual repository |
| `docs/implementation/reports/prompt-NN.md` | Required brief report for each prompt |
| `docs/implementation/reports/phase-N.md` | Consolidated report at each phase end |
| `docs/implementation/progress.json` | Last completed prompt, active scope, blockers, source version, evidence summary and exact next action |

Store detailed test logs and run artifacts in the repository’s appropriate test/report/artifact system, not large blobs in the brief report. Redact secrets and held-out material. Do not commit private task data or raw credentials. Use git commit IDs when available; otherwise record the actual working-tree state rather than inventing a commit.

Implementation state: `not_started`, `in_progress`, `implemented`. Verification state: `not_run`, `passed`, `failed`, `blocked`. A ticket is **DONE** only when implemented and all its required verification passes. An incomplete external gate cannot be labeled DONE merely because code exists.

If a dependency is blocked, implement any independently verifiable work within the authorized prompt, but keep dependent gates blocked. The next action is the unblock/resume step, not an instruction to pretend the phase completed. If the user later explicitly requests an independent later prompt, work on that scope while preserving the unmet dependency; do not silently waive it.

### 4.4 Global definition of done

A prompt is complete when its requested behavior is integrated through actual entrypoints; source contracts are satisfied; targeted tests actually pass; required live/human/infrastructure evidence exists where specified; all changed paths and evidence are recorded; relevant documentation and matrices agree; and no required ticket/gate remains failed, pending or blocked.

A phase is complete only when all of its prompt gates and the phase-wide integration gate pass. A public ranked release has additional scientific coverage/calibration conditions; completing website code or the exploratory pilot does not satisfy them.

## 5. Required completion report

**Every prompt and every phase must end with this information, briefly.** Use the exact structure below in the final response and corresponding report file. The short response may link to detailed evidence. Do not replace it with “all done.”

```text
Prompt NN / Phase N — DONE | PARTIAL | BLOCKED

1. Implemented functionality and changed files
   - Behavior implemented; relevant changed paths.

2. Tests/commands actually run and their results
   - Exact command; environment; PASS/FAIL; result or evidence path.
   - Explicitly list required checks not run and why.

3. Acceptance gates
   - Satisfied: ticket/gate/E2E IDs and evidence.
   - Pending: IDs and unfinished work/verification.
   - Blocked: IDs, concrete cause, and required input/action.

4. Decisions or specification discrepancies recorded
   - Decision/discrepancy ID and chosen/proposed resolution; or None.

5. Exact next command or numbered prompt
   - If DONE: “Next: Prompt NN — <title>.”
   - Otherwise: one exact available command, or “Next: Auxiliary R1,”
     with the recorded blocker/re-entry scope. Do not invent a command.
```

At phase boundaries, item 3 includes the aggregate phase gate. “No tests needed” must be justified by the scope; documentation-only Prompt 00 can report document/consistency checks instead of application tests. A command line is “actually run” only if it was executed during this work or its prior evidence is explicitly identified as prior/reused and still applicable.

## 6. Numbered implementation prompts

### Prompt 00 — Read the documents and establish the implementation baseline

**Phase:** 0 · **Scope:** prerequisite to all WP · **Read:** both source documents in full, this pack §§1–5, repository instructions · **Deliverable:** implementation control documents and an evidence-based gap map.

```text
Execute Prompt 00 for PolyCodeBench in the current repository/workspace.

Locate PolyCodeBench-Architecture-v1.md, PolyCodeBench-Technical-Implementation-Spec-v1.md, and this prompt pack. Read them in full, then inspect repository instructions, git status, structure, existing code/tests/configuration, and available execution tools. Do not assume a blank repository or clone an unrelated project. If a required source document is absent, identify that exact missing file after completing the useful local inspection.

Engineering tickets:
- PCB-00-1 — Establish source identity. Record actual paths, versions and hashes; inspect differences from the pack's recorded hashes. Definition of done: the authoritative sources and any actual changes are explicit.
- PCB-00-2 — Build a requirement-to-code gap map. Classify each REQ-01–14 and WP-01–24 as present/partial/absent/unverified using actual files. Definition of done: no existing functionality is declared correct merely from naming or documentation.
- PCB-00-3 — Initialize the execution contract and ledgers specified in pack §4. Copy all normative contract/report requirements without weakening them. Register every PCB ticket in this pack and every E2E-01–43 scenario, with owner prompt and required evidence. Definition of done: later sessions can resume from repository state.
- PCB-00-4 — Assess local development and external prerequisites: Python/Node/package managers, PostgreSQL, object storage, Docker/VM capability, model/judge endpoints, budgets, source rights and human calibration. Do not print secrets or start paid work. Definition of done: unknown/blocked prerequisites are distinguished from absent implementation.
- PCB-00-5 — Resolve implementation sequencing and record material discrepancies. Preserve correct existing work and unrelated changes; list refactor boundaries only where needed. Definition of done: the next prompt is concrete and compatible with the source documents.

Verification: record the read-only inspection and consistency commands actually run. No application test result is required or invented if no application exists. Check that 14 requirements, 24 work packages, 43 E2E scenarios, and Prompts 00–34 have ledger entries and owners.

Finish with the exact five-part report in pack §5 and the Phase 0 aggregate gate. If ready, next is Prompt 01 — Bootstrap the workspace and methodology register. Stop after the report.
```

### Prompt 01 — Bootstrap the workspace and methodology register

**Phase:** 1 · **WP:** WP-01 · **Read:** T §§1–2,22,26; A §§2–3,16 · **Prerequisite:** Prompt 00 · **Deliverable:** reproducible development baseline.

```text
Execute Prompt 01. Read docs/implementation/execution-contract.md, the source manifest, current progress and the source sections listed for this prompt. Honor the contract and preserve existing correct implementations.

Engineering tickets:
- PCB-01-1 — Create or reconcile the Python/TypeScript workspace and module boundaries. Establish core/services/persistence/orchestration/runner/evaluation/scoring/publication/plugin ownership. DoD: packages build/import and prohibited dependencies are checked; no fake production behavior is added.
- PCB-01-2 — Resolve compatible runtime/package versions and commit reproducible lockfiles plus approved development images. DoD: a clean environment can install the locked dependencies without floating latest tags or undocumented PATH assumptions.
- PCB-01-3 — Implement validated role-specific startup configuration, development service definitions and a minimal CI pipeline for format/type/test/build checks. DoD: invalid/missing required config fails safely and the existing smoke checks actually run.
- PCB-01-4 — Create docs/methodology/{swebench,livecodebench,cursorbench,deepcodebench}.md and a source/terms register. Use the primary sources referenced in the architecture; record verified methods, native metrics, adaptations, unavailable private assets and unresolved rights. DoD: no invented methods or claims of reproducing private CursorBench tasks.

Deliver locked workspace files, configuration schemas, local bootstrap instructions, CI, module-boundary checks and methodology records. Add verified commands to the command registry; do not list planned commands as working.

Verification: clean install/build/import/config smoke checks and applicable existing CI checks. Methodology review is evidence, not a claim that benchmark execution already works.

Use the exact five-part completion report. If all gates pass, next is Prompt 02 — Implement canonical contracts and schemas.
```

### Prompt 02 — Implement canonical contracts and schemas

**Phase:** 1 · **WP:** WP-02 · **Read:** T §§3–4,14; A §§7,12 · **Prerequisite:** Prompt 01 · **Deliverable:** shared, versioned contracts · **Primary E2E:** E2E-01.

```text
Execute Prompt 02 under the persisted execution contract. Read the current source manifest, ledgers, T §§3–4 and relevant scoring/schema contracts before implementation.

Engineering tickets:
- PCB-02-1 — Implement strict task, run, candidate, observation, artifact, scorecard, protocol and plugin contract models, statuses and typed errors. DoD: unknown fields, invalid references/ranges and forbidden coercions are rejected; schema versions are explicit.
- PCB-02-2 — Implement pcb-json-v1 canonical bytes and content/bundle digests. Enforce ASCII sorted keys, safe JSON integer bounds, decimal-string money/64-bit seeds, UTF-8 rules, duplicate-key rejection and deterministic file manifests. DoD: Python and TypeScript match byte-for-byte on shared vectors.
- PCB-02-3 — Implement IDs, UTC timestamps, monotonic-duration handling, validated relative paths and deterministic task/sample seed derivation. DoD: unsigned 64-bit seeds are stored/serialized without loss or signed overflow; source/provider bytes are not silently normalized.
- PCB-02-4 — Generate committed JSON Schemas and initial OpenAPI/shared-client contracts from authoritative models. DoD: schema drift is detectable in CI and consumers use the generated contracts rather than handwritten conflicting types.

Deliver models, serializers, schemas, cross-language golden fixtures, and explicit invalid-input fixtures. Include Unicode, large values, reordered keys, decimal strings, duplicate JSON keys and unsafe paths.

Run E2E-01 and schema/serialization property checks through both runtimes. Record actual commands and outputs; no frontend application is required yet.

End with the exact five-part report. Next when DONE: Prompt 03 — Build persistence, identity and idempotency.
```

### Prompt 03 — Build persistence, identity and idempotency

**Phase:** 1 · **WP:** WP-03 · **Read:** T §§5,8.2,9.3,20,22.4 · **Prerequisite:** Prompt 02 · **Deliverable:** transactional persistence · **Primary E2E:** E2E-02, E2E-25 foundational subcases.

```text
Execute Prompt 03 under the execution contract. Implement the data model required by the technical spec, adapting existing storage only where necessary.

Engineering tickets:
- PCB-03-1 — Add schema/migrations for tasks/configs, runs/attempts/evaluations, jobs/dependencies/executions, workers/capacity slots, artifact references, evidence/review, scorecards/releases, endpoint requests, ledgers and audit/idempotency records. DoD: empty-database creation and upgrade paths work; cyclic foreign-key creation order is handled correctly; constraints match semantics.
- PCB-03-2 — Implement service/repository transactions and immutable-record protections. DoD: attempt/evaluation separation is real, no published evidence is overwritten, and foreign keys/unique constraints enforce identities and retry boundaries.
- PCB-03-3 — Implement administrative identity/RBAC foundations, optimistic version checks and append-only audit events. DoD: permissions are enforced in services/API boundaries, with role-specific database credentials and no secret values in records/logs.
- PCB-03-4 — Implement request idempotency and transaction-safe run/attempt creation service behavior. DoD: same key and request replay one result; changed request conflicts; creation cannot leave partial attempt sets. This foundational service may be integration-tested before the scheduler exists.

Deliver migrations, repositories/services, auth/audit foundations, error mapping and integration fixtures using actual PostgreSQL.

Run migration, concurrent duplicate-request, immutable-write and permission tests. Link E2E-02/E2E-25 evidence with its actual scope; full API/run variants remain pending until their entrypoints exist. Do not claim all permission routes have been tested.

Finish with the exact report. Next when DONE: Prompt 04 — Implement artifact storage and visibility.
```

### Prompt 04 — Implement artifact storage and visibility

**Phase:** 1 · **WP:** WP-04 · **Read:** T §§5.3,6,20; A §§12,14 · **Prerequisite:** Prompt 03 · **Deliverable:** verified artifact lifecycle · **Primary E2E:** E2E-03, E2E-26 storage subcases.

```text
Execute Prompt 04 under the execution contract. Build artifact integrity and access control before using files as authoritative evaluation evidence.

Engineering tickets:
- PCB-04-1 — Implement provisional upload, expected size/hash validation, independent finalization and immutable verified artifact registration. DoD: an object-store ETag is never substituted for SHA-256; altered or truncated bytes cannot become verified evidence.
- PCB-04-2 — Enforce hidden/internal/public visibility domains and role-scoped reads/writes. DoD: digest knowledge does not authorize access, private/public deduplication does not leak data, and a solve identity cannot list/read hidden assets.
- PCB-04-3 — Implement manifest edges, safe artifact references, byte quotas and retry-safe deduplication. DoD: authoritative database references are committed only after verification; upload/commit interruption does not create contradictory identities.
- PCB-04-4 — Add provisional garbage collection and allowlisted public projection/export support. DoD: referenced/published/held artifacts are retained; publication makes reviewed projection objects without changing a private object's ACL in place; downloads are inert and authorized.

Deliver storage adapter, finalizer, access policies, cleanup logic and integration tests against the configured local object store. Record production policy validation separately from local emulation.

Run E2E-03 and storage variants of E2E-26, including role denial, size/hash mismatch, duplicate upload and cleanup of orphaned bytes. Future public routes still require their own privacy tests.

Use the exact report. Next when DONE: Prompt 05 — Build task admission and freeze the methodology contracts.
```

### Prompt 05 — Build task admission and freeze the methodology contracts

**Phase:** 1, exit · **WP:** WP-05 · **Read:** T §§1,4,11,17,18,25; A §§2,4,8–11 · **Prerequisite:** Prompt 04 · **Deliverable:** frozen task/policy workflow · **Primary E2E:** E2E-04, E2E-27.

```text
Execute Prompt 05 under the execution contract. Complete the methodology/foundations phase without pretending later language/evaluation workers already exist.

Engineering tickets:
- PCB-05-1 — Implement task-package import and separate visible/hidden bundle construction, safe repository snapshot handling, rights/provenance metadata, output contracts and immutable task-version registration. DoD: hidden data/references cannot appear in model-visible exports or visible image build contexts.
- PCB-05-2 — Implement task-set freeze, cluster/split validation, earliest-exposure dates, model-cutoff provenance and family/stratum membership. DoD: related variants cannot leak across designated splits; curation date cannot turn an old problem into a post-cutoff problem.
- PCB-05-3 — Implement admission orchestration/interfaces and the validation/freezing CLI/API. If needed, add the smallest compliant local sandbox invocation for trusted authored admission fixtures, recording it as a prerequisite slice of WP-06 for Prompt 06 to extend. Actually execute reference/faulty/alternative fixture checks; do not simulate their outcomes. DoD: unvalidated tasks cannot enter a scored task set, execution tier is recorded, and production task admission remains subject to production-worker validation.
- PCB-05-4 — Finalize versioned pilot contracts for scoring weights, applicability/owners, language profiles, method deviations, budgets and evidence schemas from the sources. DoD: proposed calibration parameters remain labeled pilot parameters; substantive uncertainty/rights restrictions are recorded; no omitted benchmark family.

Deliver registry, importer, admission reports/contracts, taskset commands, method records and initial fixture packages. Update the phase report with which structural/authored-fixture admission checks are passed and which language/production admission variants are scheduled in Prompts 10–12/17. Record this sequencing clarification in the implementation decisions, without changing acceptance criteria for scored tasks.

Run schema/import/path/privacy/split/date/freeze tests and applicable E2E-04/E2E-27 subcases. Distinguish completion of the admission mechanism from later scientific task admission. If a source dependency requires actual runtime evidence before freeze, leave that task unfrozen.

End with the exact five-part report and Phase 1 gate. Next when phase scope is DONE: Prompt 06 — Implement sandbox drivers and isolation.
```

### Prompt 06 — Implement sandbox drivers and isolation

**Phase:** 2 · **WP:** WP-06 · **Read:** T §§6,10,22; A §§3,6,15 · **Prerequisites:** artifact/task contracts · **Deliverable:** bounded development and VM execution · **E2E:** E2E-05, E2E-06.

```text
Execute Prompt 06 under the execution contract. Reuse any compliant local execution slice created for admission; implement the full sandbox contracts without weakening isolation to simplify tests.

Engineering tickets:
- PCB-06-1 — Implement SandboxProvider and typed create/stage/execute/snapshot/terminate/destroy operations. DoD: lifecycle actions are scoped, idempotent where specified, logged and timeout-bounded; candidate arguments never become host-shell interpolation.
- PCB-06-2 — Complete LocalDockerSandboxProvider and guest resource/path/process policy. DoD: limits, safe extraction, no exposed Docker socket/host namespaces/secrets, and development-only result identity are enforced externally to candidate code.
- PCB-06-3 — Implement Ec2VmSandboxProvider and deployment configuration for disposable guests. DoD: supervisor credentials remain outside guests, no instance role/metadata access, restricted control channel, offline candidate container, stage-scoped transfer capabilities and distinct solve/grading lanes.
- PCB-06-4 — Implement TTL/orphan cleanup, destruction verification, checkpoint collection and isolation attestation. DoD: guest/resource cleanup is confirmed before capacity reuse; a developer cannot request a production isolation badge.

Deliver working driver code, guest bootstrap/image definitions, approved sandbox policies, infrastructure plan and adversarial containment fixtures. Use only explicitly authorized cloud target/budget for real VM tests. If unavailable, complete local/driver/IaC work and report the real-VM gate blocked; do not claim production isolation.

Run actual containment tests for network/metadata denial, path/symlink escape, process/time/memory/disk limits, cancellation and cleanup. E2E-05/06 require production-driver evidence where their contract specifies it; local results are additional evidence, not a substitute.

Use the required five-part report. Next when required gates pass: Prompt 07 — Implement durable jobs and recovery.
```

### Prompt 07 — Implement durable jobs and recovery

**Phase:** 2 · **WP:** WP-07 · **Read:** T §§5,7,8,9.3; A §5 · **Prerequisites:** persistence/artifacts/sandbox · **Deliverable:** crash-safe scheduler · **E2E:** E2E-07–09.

```text
Execute Prompt 07 under the execution contract. Implement scheduling with actual database concurrency and real worker lifecycle evidence.

Engineering tickets:
- PCB-07-1 — Implement the stage DAG, branch-specific skip semantics and transactional job creation/unblocking. DoD: unknown/failed prerequisites cannot be mistaken for successful quality inputs; a successfully executed wrong model answer is not an infrastructure retry.
- PCB-07-2 — Implement short SQL claims, 120-second leases, 30-second heartbeats, fencing tokens and execution records according to the spec. DoD: stale workers cannot dispatch or commit authoritative results and duplicate completion is idempotent only for the same output.
- PCB-07-3 — Implement capacity-slot claims, matching resource classes, cleanup states, campaign/provider fairness and bounded retry/reaper rules. DoD: no over-allocation or premature reuse of a still-running guest; all deliveries and failure classes remain visible.
- PCB-07-4 — Implement cancel/revoke/resume semantics and progress events. DoD: cancellation prevents new dispatch, terminates work, preserves completed evidence/usage and does not turn unrun tasks into model failures.

Deliver scheduler, worker control APIs/processes, event/state repositories, recovery tests and verified operator commands. Integrate with existing artifact/sandbox services rather than using a parallel in-memory execution path.

Run E2E-07/08/09 with two competing workers and injected crashes around lease expiry, artifact upload and result commit. Assert database state, artifact references, guest termination and emitted events. Preserve failure injections and logs as evidence.

Use the required report. Next when DONE: Prompt 08 — Implement model adapters and budget accounting.
```

### Prompt 08 — Implement model adapters and budget accounting

**Phase:** 2 · **WP:** WP-08 · **Read:** T §§4.2,8,20,22.2; A §§5,6 · **Prerequisites:** scheduler/persistence/artifacts · **Deliverable:** accountable model gateway · **E2E:** E2E-10–12.

```text
Execute Prompt 08 under the execution contract. Build a model gateway that protects secrets, preserves provider semantics and accounts for ambiguous outcomes.

Engineering tickets:
- PCB-08-1 — Implement OpenAI-compatible, Anthropic, Google and local endpoint adapters behind ModelAdapter. DoD: capability validation covers tools/schema/seeds/reasoning/context/usage; unsupported controls reject or create explicit cohort exceptions rather than silently disappearing.
- PCB-08-2 — Implement endpoint registration/approval and secret references with network-policy validation. DoD: unreviewed public endpoints are not contacted, SSRF/private-address rules hold, and local inference endpoints require explicit internal registration.
- PCB-08-3 — Implement call intents/deliveries, persisted responses and usage/pricing records. DoD: recovery consumes already-stored responses; provider request IDs/revisions and unknown usage are preserved; no response shopping after failures.
- PCB-08-4 — Implement atomic hierarchical cost reservations, token/turn limits, provider throttling and settlement/reconciliation. DoD: ambiguous requests retain exposure, retries reserve additional cost, missing usage is not zero, and unavailable enforceable cost bounds block strict-cap claims.

Deliver adapters, gateway, budget ledger/services, capability fixtures, endpoint checks and plan/cost output. Use current official provider documentation when implementing provider specifics; do not infer capabilities from names.

Run E2E-10/11/12 with actual PostgreSQL and deterministic transport fault fixtures. Label fixture evidence honestly. Use available authorized credentials for bounded provider smoke checks; list untested live adapters without fabricating results. The two-model live pilot remains Prompt 17's gate.

Finish with the required report. Next when DONE: Prompt 09 — Implement single-shot and agent execution.
```

### Prompt 09 — Implement single-shot and agent execution

**Phase:** 2 · **WP:** WP-09 · **Read:** T §§4,7–10; A §§2.3,5–7 · **Prerequisites:** task/sandbox/model gateway · **Deliverable:** bounded solve loop · **E2E:** E2E-13, E2E-14.

```text
Execute Prompt 09 under the execution contract. Implement actual solve sessions through the gateway and sandbox services.

Engineering tickets:
- PCB-09-1 — Implement single-shot extraction and standard-agent protocol selection, capability validation and family output contracts. DoD: deterministic extraction never uses another LLM to select a better candidate, and protocol identity is part of the run.
- PCB-09-2 — Implement list_files, read_file, search, apply_patch, run_command and run_public_tests with schemas, caps, protected paths and sequential tool-call ordering. DoD: only allowed visible inputs/feedback reach the model; shell execution remains inside the guest; command descendants are cleaned up.
- PCB-09-3 — Implement transcript events, bounded context policy, compaction/truncation records and atomic workspace/transcript checkpoints. DoD: restoring a checkpoint never pairs one workspace revision with a different conversation state.
- PCB-09-4 — Implement stopping, budget exhaustion, candidate freezing and infrastructure recovery. DoD: candidate bytes are immutable, no hidden feedback reaches solving, stored provider responses are consumed once, and only an uncommitted command can be replayed under the documented policy.

Deliver protocol controllers, tools, checkpoint/recovery logic, extraction fixtures and run inspection output. Keep incomplete/wrong candidates distinct from infrastructure failure.

Run E2E-13/14, including actual tool execution, interrupted command checkpoints, multiple tool calls, invalid schemas, truncation, forbidden paths and exhausted budgets. Transport fixtures are acceptable for deterministic controller checks, with live evidence deferred to Prompt 17.

Use the required report. Next when DONE: Prompt 10 — Implement Python support.
```

### Prompt 10 — Implement Python support

**Phase:** 2 · **WP:** WP-10 · **Read:** T §§11–12,18; A §11 · **Prerequisites:** task/sandbox contracts · **Deliverable:** Python plugin and valid tasks · **E2E:** E2E-04, E2E-15, relevant E2E-16.

```text
Execute Prompt 10 under the execution contract. Implement a real Python language plugin using the shared extension interfaces.

Engineering tickets:
- PCB-10-1 — Build pinned offline Python runtime/evaluator images and dependency recipes. DoD: compiler/runtime/tool/lock/rule identities are recorded and scored runs perform no online installation.
- PCB-10-2 — Implement Python build/test/symbol/analyzer/performance plans using pytest, Hypothesis, Ruff, mypy, Bandit and the approved applicable checks. DoD: nonzero finding exits are parsed correctly; missing or crashing required checks cannot look clean.
- PCB-10-3 — Implement the specified diagnostic and orthogonal idiom profiles with applicability/ownership mappings and concrete anti-pattern fixtures. DoD: mutable defaults or type expectations are evaluated in context; no syntax-count bonus or duplicate penalty.
- PCB-10-4 — Create independently authored Python admission fixtures and a pilot task inventory covering valid, wrong, alternative-valid, quality-defective and timeout cases. DoD: real reference/faulty/alternative executions validate the task oracles; admission artifacts and exposure/rights records exist.

Deliver plugin code, images, profiles, fixtures and task manifests. Reserve private task/reference assets for protected storage. Build the pilot inventory toward the specified 12 independent Python clusters; admission may complete after the generic evaluator/performance/judge stages land.

Run language conformance and executable admission checks. Document which full quality-admission gates await Prompts 12–15 rather than declaring unfinished pilot tasks fully admitted.

Finish with the required report. Next when component scope is DONE: Prompt 11 — Implement Rust support.
```

### Prompt 11 — Implement Rust support

**Phase:** 2 · **WP:** WP-11 · **Read:** T §§11–12,18; A §11 · **Prerequisites:** task/sandbox contracts · **Deliverable:** Rust plugin and valid tasks · **E2E:** E2E-04, E2E-15, E2E-16.

```text
Execute Prompt 11 under the execution contract. Add Rust through the same language/plugin contracts, not a separate execution engine.

Engineering tickets:
- PCB-11-1 — Build frozen Rust/Cargo toolchains, offline crates and distinct regular/instrumented/performance recipes. DoD: toolchain/Cargo.lock and image digests determine evaluator identity.
- PCB-11-2 — Implement build/test/symbol/plans, selected clippy rules, dependency audit and compatible Miri handling. DoD: Miri unsupported operations are distinct from a clean scan and from candidate UB; only applicable supported tasks require it.
- PCB-11-3 — Implement Rust diagnostic/orthogonal idiom profiles and evidence ownership. DoD: clone/unwrap/unsafe tokens are not automatically violations; required behavior and actual contextual evidence determine findings.
- PCB-11-4 — Author Rust admission fixtures and the pilot task inventory with valid/incorrect/alternative/quality-defective cases. DoD: intended faults are actually detected, valid alternative code is accepted, source/split/applicability metadata is complete.

Deliver plugin, pinned images, check parsers, profiles, fixtures and manifests. Build toward 12 independent Rust task clusters and record incomplete later-stage admission honestly.

Run E2E-15/16 Rust variants and executable admission checks, including tool findings, crash/unsupported behavior, resource limits and contextually valid ownership/error-handling alternatives.

Use the exact completion report. Next when component scope is DONE: Prompt 12 — Implement independent grading and normalized evidence.
```

### Prompt 12 — Implement independent grading and normalized evidence

**Phase:** 2 · **WP:** WP-12 · **Read:** T §§10–12,14.5; A §§6,8 · **Prerequisites:** solve/Python/Rust/scheduler · **Deliverable:** trusted evaluation pipeline · **E2E:** E2E-16–18; close relevant E2E-04 variants.

```text
Execute Prompt 12 under the execution contract. Connect frozen submissions to real independent evaluation and normalized evidence.

Engineering tickets:
- PCB-12-1 — Implement fresh grading environments, base/candidate validation, immutable acceptance overlays and expected test inventories. DoD: candidate edits or printed fake successes cannot replace authoritative acceptance; missing mandatory tests never count as passes.
- PCB-12-2 — Implement analyzer execution/parsing contracts and normalized observations with raw report references, tool/rule/advisory versions and explicit failure semantics. DoD: empty reports, crashes and unsupported checks cannot become perfect scores.
- PCB-12-3 — Implement baseline-to-candidate relations, semantic issue identity and reviewed cross-tool deduplication. DoD: one underlying issue has one composite owner; unchanged unrelated debt is visible without unjustified blame; ambiguous mappings remain reviewable.
- PCB-12-4 — Implement weighted robustness scenarios, seeded property/fuzz evidence and applicability plans, then complete Python/Rust functional/quality fixture integration. DoD: task-hard requirements gate correctness while optional quality scenarios remain separately weighted; actual fixture results validate those distinctions.

Deliver the working evaluation graph, report parsers, scenario runner, normalized-evidence manifests, raw artifacts and integrated fixtures. Keep native metrics separate from any stricter PolyCodeBench gate.

Run E2E-16/17/18 and relevant admitted-task variants of E2E-04. Include no-regression checks, valid alternative solutions, malicious output/config changes, duplicate scanner results, preexisting debt and analyzer failure. Use actual container execution, not only mocked report files.

Finish with the required report. Next when DONE: Prompt 13 — Implement performance measurement.
```

### Prompt 13 — Implement performance measurement

**Phase:** 2 · **WP:** WP-13 · **Read:** T §13; A §8.5 · **Prerequisites:** sandbox/evaluation · **Deliverable:** reproducible runtime/memory evidence · **E2E:** E2E-19, E2E-20.

```text
Execute Prompt 13 under the execution contract. Measure generated-code performance separately from model-generation latency and cost.

Engineering tickets:
- PCB-13-1 — Implement PerformancePlan validation, exclusive capacity/hardware matching, reference identity and output verification. DoD: candidate/reference use the same physical worker/allocation class, frozen workload/flags and equivalent runtime; instrumented builds cannot enter the speed lane.
- PCB-13-2 — Implement randomized paired order, specified warmup/iteration counts, cold/steady-state modes and whole-process-tree memory recording. DoD: every iteration and input/environment identity is preserved, with compile time separately reported.
- PCB-13-3 — Implement canaries, frozen stability thresholds, bounded block retries and first-valid-block selection. DoD: noise invalidates the affected block consistently; the fastest rerun is never cherry-picked.
- PCB-13-4 — Implement workload aggregation, ratio floors, weighted geometric means, variance and censored-timeout handling. DoD: a lower bound is not reported as an exact duration; the documented efficiency transform has golden checks and does not claim proof of Big-O.

Deliver performance runner, worker reservation integration, raw measurements, hardware records, canary fixtures and score-ready outputs. Mark unavailable dedicated/homogeneous hardware as a blocked gate; ordinary shared CI timings cannot establish production comparisons.

Run E2E-19/20 with actual execution and deterministic invalid-block scenarios. Verify that deliberately wrong-but-fast output fails acceptance/output verification and does not win an efficiency score.

Use the exact report. Next when DONE: Prompt 14 — Implement judging, review and calibration.
```

### Prompt 14 — Implement judging, review and calibration

**Phase:** 2 · **WP:** WP-14 · **Read:** T §15; A §8.8 · **Prerequisites:** gateway/evidence · **Deliverable:** bounded judge and adjudication workflow · **E2E:** E2E-21, E2E-22.

```text
Execute Prompt 14 under the execution contract. Implement judges only for residual rubric items, with traceable human-review boundaries.

Engineering tickets:
- PCB-14-1 — Build anonymized evidence packets and fixed, versioned rubric/panel definitions. DoD: candidate identity/rank/cost are withheld, citations must exist, judge has no execution tools, and candidate comments are untrusted data.
- PCB-14-2 — Implement three logical votes, schema validation, fixed bounded invalid-vote recovery and averaging. DoD: every delivery is retained; fewer than three valid required votes cannot produce a ready result; low scores are not discarded as retries.
- PCB-14-3 — Implement disagreement triggers, reviewer access/decisions and immutable supersession. DoD: overrides cite evidence/anchors/reason and preserve original votes; changing the panel requires a new evaluation/cohort version.
- PCB-14-4 — Implement calibration import/evaluation/reporting and build the disjoint labeled-packet workflow specified in T §15.3. DoD: actual qualified human labels, agreement/confusion metrics and audit selection are recorded; absent labels or judge access remain blocked instead of becoming invented reviews.

Deliver packet/vote schemas, judge service, review API/CLI, adversarial fixtures and calibration report or explicit missing inputs. Use one cohort panel distinct from both pilot candidate models.

Run E2E-21/22, including 1/0.5/1 averaging, invalid JSON, missing votes, false citations, disagreement and comment-based instructions. Fixture behavior does not replace live judge/human calibration evidence. Finish every locally implementable item before reporting an external blocker.

Use the exact report. Next when required gates pass: Prompt 15 — Implement deterministic scoring and replay. If calibration blocks only live validation, record that dependency honestly if the owner authorizes independent scoring implementation.
```

### Prompt 15 — Implement deterministic scoring and replay

**Phase:** 2 · **WP:** WP-15 · **Read:** T §14,§18; A §§8,11 · **Prerequisites:** typed validated evidence and applicable evaluator outputs · **Deliverable:** pure scorecards · **E2E:** E2E-23, E2E-24.

```text
Execute Prompt 15 under the execution contract. Implement the scorer as a pure function of frozen task/policy/evidence; no model calls, network, runtime clocks or task execution inside scoring.

Engineering tickets:
- PCB-15-1 — Implement evidence completeness, pass/fail/unknown/N/A semantics and correctness gating. DoD: failed code has zero applicable quality contributions; missing required evidence cannot become either a zero failure or a perfect score.
- PCB-15-2 — Implement exact decimal composites, applicability redistribution, security penalties, quality/idiom/robustness rubrics and efficiency inputs. DoD: nominal/effective weights and item contributions explain every result; diagnostic language profiles do not double-count composite penalties.
- PCB-15-3 — Enforce canonical issue/evidence ownership, immutable scorecard identity and traceable contribution chains. DoD: reordered equivalent evidence yields identical output and duplicate findings cannot change a score.
- PCB-15-4 — Implement clean-process score replay and score/schema version handling through the CLI/API. DoD: archived validated evidence reproduces the same canonical scorecard without requesting another candidate or judge.

Verify the exact synthetic fixtures: full-quality example 89.750000; same example with efficiency predeclared N/A 90.772727; duplicate confirmed high security finding produces security 75.000000 once; time ratio 2 and memory ratio 1.5 gives efficiency 61.666667; failed correctness gives 0.000000.

Run E2E-23/24 and boundedness, monotonicity-at-fixed-applicability, duplicate/reordering, unknown-evidence and N/A properties. Do not change the formulas to make current implementation outputs pass.

Deliver scorer, scorecards/item explanations, policy/profile configs, replay command and tests. Use the exact report. Next when DONE: Prompt 16 — Implement aggregation and release publication.
```

### Prompt 16 — Implement aggregation and release publication

**Phase:** 2 · **WP:** WP-16 · **Read:** T §19,§20; A §§10,14 · **Prerequisites:** completed scorecards · **Deliverable:** comparable immutable reports/releases · **E2E:** E2E-28–30.

```text
Execute Prompt 16 under the execution contract. Connect immutable scorecards to defensible aggregate results and reviewed publication.

Engineering tickets:
- PCB-16-1 — Implement metric definitions, fixed cohort identity, sample/task/stratum/language aggregation, failure denominators and coverage. DoD: missing languages/tasks are not silently renormalized; conditional-on-pass metrics are separately labeled; filters use a common eligible cohort.
- PCB-16-2 — Implement fixed-seed clustered/hierarchical bootstrap and paired comparisons. DoD: correlated variants remain in their cluster, uncertainty is recomputed for the actual metric, replicate/seed/method evidence exists, and unstable/insufficient samples are labeled.
- PCB-16-3 — Implement draft/validate/review/approve/publish/withdraw states and versioned corrections. DoD: approval binds exact content; a change invalidates approval; published historical results never mutate in place.
- PCB-16-4 — Implement allowlisted projections, manifest signing and atomic current-pointer updates. DoD: incomplete/unsafe projections cannot publish, pointer races conflict, and private evidence is never made public through a bulk export.

Deliver internal report generation, metric/cohort schemas, uncertainty outputs, release APIs/CLI, signing verification and publication tests. Use synthetic/internal fixtures for behavior; do not label them model benchmark results. Public deployment/publishing requires an authorized target and reviewed release.

Run E2E-28/29/30 plus fixed-seed replay, unknown cutoff, partial coverage, reviewer invalidation and safe projection tests. Keep the optional full-product editorial index disabled by default.

End with the exact report. Next when DONE: Prompt 17 — Run and verify the real Python/Rust pilot.
```

### Prompt 17 — Run and verify the real Python/Rust pilot

**Phase:** 2, exit · **WP:** WP-17 · **Read:** T §§15,19,25.1; A §§17,21 · **Prerequisites:** WP-05–16 integrated and required live/human inputs · **Deliverable:** actual exploratory pilot · **E2E:** E2E-31 and integrated earlier variants.

```text
Execute Prompt 17 under the execution contract. Prove the implemented pipeline end to end using actual authorized providers and production-isolated workers.

Engineering tickets:
- PCB-17-1 — Complete and freeze at least 12 independent Python clusters and 12 independent Rust clusters with validated reference/faulty/alternative solutions, required quality opportunities, rights, hidden bundles and admission evidence. DoD: no placeholder tasks or unresolved required analyzers enter the pilot.
- PCB-17-2 — Freeze two distinct real model identities/configurations, one compatible protocol, three planned samples per task and a common distinct judge panel. Produce exact run plans, resolved capabilities/prices, resource needs, active budgets and exposure policy. DoD: the plan accounts for 24 × 2 × 3 = 144 attempts; outstanding credentials/authorization/human calibration are concrete blockers, not guessed values.
- PCB-17-3 — Execute the bounded pilot using existing authorization or obtain only the missing concrete authorization after preparation. DoD: all attempts have genuine provider/sandbox/evidence lineage; model failures count; infrastructure failures follow the specified retry/missingness policy; no best-answer selection.
- PCB-17-4 — Produce the internal exploratory release/report and replay sampled plus required scorecards. DoD: dimensions, coverage, cost/latency, intervals/limitations, tool/judge versions, failure/exclusion ledger and evidence links agree with the actual runs.

Both single-shot and standard-agent implementations must already have acceptance coverage, even if this pilot uses only one comparison protocol. The pilot is not a full product or a public ranked release; do not invent the extra independent clusters needed for ranking.

Run E2E-31 and close applicable integrated versions of earlier tests with actual evidence. If money/credentials/hardware/reviewer inputs are missing, finish every plan/config/local validation first, provide the exact bounded run command, and leave the phase gate BLOCKED. Do not replace live outputs with fixture data.

Use the exact five-part report and Phase 2 aggregate gate, including expected/completed/model-failed/infrastructure-blocked attempt counts. Next only when the pilot gate passes: Prompt 18 — Implement Track A bug hunting and repair.
```

### Prompt 18 — Implement Track A bug hunting and repair

**Phase:** 3, exit · **WP:** WP-18 · **Read:** T §§11.3,16; A §§4.2,9 · **Prerequisite:** accepted pilot · **Deliverable:** complete Python/Rust Track A · **E2E:** E2E-32–34.

```text
Execute Prompt 18 under the execution contract. Implement Track A as structured bug detection plus independently evaluated repair, using the existing harness/evidence/release services.

Engineering tickets:
- PCB-18-1 — Implement historical/pre-fix, disclosed-security and mutation task-source workflows with reproducible defects, provenance, immutable oracles, clean controls and rejected equivalent mutations. DoD: Python/Rust historical and injected tasks run, publicly disclosed security coverage has verified examples or a clearly blocked source requirement, and no hidden injection log enters visible assets.
- PCB-18-2 — Implement findings parsing/span validation, causal matching, semantic duplicates and one-to-one accepted matches. DoD: file coincidence is insufficient; TP/FP/FN and localization use the specified rules; unresolved genuinely novel findings are not automatic false positives.
- PCB-18-3 — Implement root-cause/severity rubrics, human adjudication, ground-truth version updates and cohort-wide rematching. DoD: accepted new bugs update all affected results consistently; original decisions/votes remain auditable.
- PCB-18-4 — Implement final combined-patch grading and Track A aggregates, including source/language balance and clean-control handling. DoD: good detection with bad patch retains detection but gets repair zero; no-op clean controls do not earn empty repair credit; multilingual headline follows equal-language aggregation.

Deliver source builders, annotated task packs, finding/matching/review APIs/CLI, repair reports and score explanations. Include at least a real admitted example for each claimed source family rather than only handcrafted match JSON. Keep limited samples exploratory.

Run E2E-32/33/34 with actual reports/patches and integrated evidence. Verify the TP=1/FP=1/FN=1 fixture gives P=R=F1=0.5 despite a duplicate TP, and that novel-bug review does not favor one model silently.

End with the exact report and Phase 3 aggregate gate. Next when DONE: Prompt 19 — Add JavaScript and TypeScript support.
```

### Prompt 19 — Add JavaScript and TypeScript support

**Phase:** 4 · **WP:** WP-19, part 1 · **Read:** T §18; A §11 · **Prerequisite:** accepted pilot/plugin contracts · **Deliverable:** two distinct language variants · **E2E:** E2E-15, E2E-35.

```text
Execute Prompt 19 under the execution contract. Add JavaScript and TypeScript as separate plugin/profile identities sharing appropriate runtime implementation.

Engineering tickets:
- PCB-19-1 — Build pinned Node/package-manager/runtime/test images with offline dependencies and locked recipes. DoD: dependency/advisory snapshots and test-runner identity are recorded and no online installation occurs during scored execution.
- PCB-19-2 — Implement build/test/symbol/analysis plans, ESLint, applicable security/dependency checks and strict TypeScript checks where the task requires them. DoD: JavaScript is not penalized for lacking TypeScript types; task-specific strictness passes the reference.
- PCB-19-3 — Implement async/error/concurrency/typing/idiom applicability and ownership mappings. DoD: floating promises and real async errors have evidence, unused concurrency opportunities are N/A, and stylistic modern syntax is not an automatic bonus.
- PCB-19-4 — Admit runnable JS/TS fixture tasks and run the shared language/evaluation conformance suite. DoD: valid, wrong, alternative-valid, security/async/type-defective and timeout fixtures produce the intended evidence through real entrypoints.

Deliver both plugins/profiles, images, task packs and conformance reports without language-specific branches in the scheduler or public API. Run E2E-15/35 variants and applicable analyzer/baseline/replay checks.

Use the exact report, retaining WP-19's remaining-language gates as pending. Next when this prompt is DONE: Prompt 20 — Add C support.
```

### Prompt 20 — Add C support

**Phase:** 4 · **WP:** WP-19, part 2 · **Read:** T §§12–13,18; A §11 · **Deliverable:** C plugin · **E2E:** E2E-15, E2E-35 plus instrumented/runtime cases.

```text
Execute Prompt 20 under the execution contract. Add C through the common plugin/evidence contracts.

Engineering tickets:
- PCB-20-1 — Build pinned compiler/standard/dependency recipes and separate release/instrumented images. DoD: flags and hardware identities are frozen; sanitizer/Valgrind timing never masquerades as release performance.
- PCB-20-2 — Implement compilation, tests, clang-tidy/cppcheck and applicable ASan/UBSan/Valgrind plans/parsers. DoD: build errors, sanitizer findings, unsupported checks and infrastructure failures are classified distinctly.
- PCB-20-3 — Implement ownership/error-checking/portability/UB/memory profile mappings. DoD: baseline warning debt and task-specific warning policy are respected; blanket -Werror does not silently invalidate otherwise admitted legacy tasks.
- PCB-20-4 — Admit real conformance fixtures for correct/alternative code, wrong output, bounds/UB/resource defects and timeouts. DoD: instrumentation detects intended executed defects while reports acknowledge coverage limits; no blanket claim of proven memory safety.

Deliver the plugin, pinned recipes, profiles, task manifests and actual test evidence. Run shared E2E-15/35 variants, candidate-vs-baseline analysis, acceptance gate and performance-lane isolation checks.

Use the exact report. Next when DONE: Prompt 21 — Add C++ support.
```

### Prompt 21 — Add C++ support

**Phase:** 4 · **WP:** WP-19, part 3 · **Read:** T §§12–13,18; A §11 · **Deliverable:** C++ plugin · **E2E:** E2E-15, E2E-35.

```text
Execute Prompt 21 under the execution contract. Add C++ without collapsing its language semantics into the C profile.

Engineering tickets:
- PCB-21-1 — Implement pinned task-specific C++ standard/compiler/build/test recipes with separate performance and sanitizer profiles. DoD: incompatible instrumentation combinations are rejected and reference/alternative builds use the same contract.
- PCB-21-2 — Integrate selected clang-tidy/cppcheck rules and applicable ASan/UBSan/TSan checks with normalized output. DoD: actual findings/crashes/unsupported paths retain their correct semantics; no analyzer omission silently raises scores.
- PCB-21-3 — Implement RAII/ownership, STL/container, move/value-semantics and modern-feature applicability with single composite ownership. DoD: nonowning raw pointers or justified legacy patterns are not automatically failures; measured copies and API choices are not blindly double-penalized.
- PCB-21-4 — Admit runnable fixtures for valid alternatives, ownership/exception/resource/concurrency defects and timeouts. DoD: shared extension checks pass and expected evidence reaches the ordinary scorer/replay path.

Deliver plugin, images, profiles, task fixtures and conformance evidence. Run E2E-15/35 and relevant instrumented-build, baseline-debt and release-performance separation variants.

Use the exact report. Next when DONE: Prompt 22 — Add Go support.
```

### Prompt 22 — Add Go support

**Phase:** 4 · **WP:** WP-19, part 4 · **Read:** T §§12–13,18; A §11 · **Deliverable:** Go plugin · **E2E:** E2E-15, E2E-35.

```text
Execute Prompt 22 under the execution contract. Add Go with behavioral evidence for concurrency and cancellation rather than syntax-only scoring.

Engineering tickets:
- PCB-22-1 — Build pinned Go/module/vendor/test and release recipes with offline execution. DoD: runtime/dependency/flag identity is reproducible and language registration is data-driven.
- PCB-22-2 — Integrate go test, gofmt checking, vet, staticcheck, gosec and applicable race-enabled runs. DoD: race/instrumented results cannot enter performance measurements; checker crashes cannot look like clean output.
- PCB-22-3 — Implement error/interface/stdlib/context/concurrency diagnostic and orthogonal idiom mappings. DoD: nonconcurrent tasks mark concurrency N/A and lifecycle/cancellation findings have concrete contract/evidence ownership.
- PCB-22-4 — Admit fixtures exercising valid/alternative code, ignored errors, cancellation/lifecycle faults, wrong answers and limits. DoD: scenarios detect their intended behavior under a frozen repetition policy and clean fixtures are not falsely penalized.

Deliver plugin, recipes, profiles, task packs and conformance reports. Run E2E-15/35 plus applicable race, robustness, baseline and replay checks.

Use the exact report. Next when DONE: Prompt 23 — Add Java and close language coverage.
```

### Prompt 23 — Add Java and close language coverage

**Phase:** 4, exit · **WP:** WP-19, final part · **Read:** T §§13,18,23; A §11 · **Deliverable:** Java and all-language audit · **E2E:** E2E-15, E2E-35 for all required languages.

```text
Execute Prompt 23 under the execution contract. Implement Java and audit the complete language-extension requirement.

Engineering tickets:
- PCB-23-1 — Build pinned JDK/Maven-or-Gradle/JUnit recipes, offline dependencies and frozen JIT/performance policy. DoD: declared cold/steady-state modes and warmup do not adapt to favor individual candidates.
- PCB-23-2 — Integrate SpotBugs, PMD, Checkstyle, dependency analysis and task-specific security/resource/concurrency probes. DoD: analyzer coverage, failure semantics and baseline deltas use shared contracts.
- PCB-23-3 — Implement Java profiles and admit valid/alternative/null/resource/concurrency/security/wrong-output fixtures. DoD: streams/records/SOLID terminology do not earn automatic points; behavioral and contextual evidence determines results.
- PCB-23-4 — Audit Python, Rust, JS, TS, C, C++, Go and Java end to end through plugin registration, task admission, solve output contracts, grading, scoring, replay and capability metadata. DoD: every required language has actual conformance evidence, separate JS/TS semantics, and no missing core path hidden by a capability label.

Deliver Java implementation plus docs/implementation/reports/language-coverage.md with tool/image/profile identities, task counts, relevant test variants and limitations. Fix integration defects found by this audit before claiming WP-19 complete.

Run Java checks and the affected all-language conformance checks. Reuse still-valid expensive evidence explicitly instead of rerunning it without reason. E2E-15/35 remain partial if any required language/variant is unverified.

End with the exact report and Phase 4 gate. Next when DONE: Prompt 24 — Implement repository repair benchmark adapters.
```

### Prompt 24 — Implement repository repair benchmark adapters

**Phase:** 5 · **WP:** WP-20, part 1 · **Read:** T §§12,17; A §2 · **Deliverable:** native/inspired repository repair · **E2E:** E2E-36.

```text
Execute Prompt 24 under the execution contract. Integrate repository issue repair using pinned official methodology where applicable.

Engineering tickets:
- PCB-24-1 — Implement SuiteAdapter import/validation for supported SWE-bench-style/native task records, immutable repo snapshots and patch output. DoD: source revisions/terms/protocol differences are recorded and no future fixes/hidden tests leak into solving.
- PCB-24-2 — Wrap the pinned upstream evaluator rather than loosely recreating its result from a generic test fraction. DoD: native fail-to-pass/pass-to-pass outcomes and resolution metric are preserved separately from PolyCodeBench acceptance and quality.
- PCB-24-3 — Bind cache/upstream run identity to task, candidate and evaluator digests and integrate protected grading overlays. DoD: one candidate cannot receive another candidate's cached grade and edits to visible tests cannot replace native/hidden acceptance.
- PCB-24-4 — Admit native-compatible and deliberately adapted/ported fixtures through the actual harness. DoD: their methodology labels differ correctly; patch correctness and applicable quality evidence are traceable to the same frozen candidate.

Deliver adapter, method record, task imports, native metric export and integrated reports. Use only available allowed source assets; unavailable/private datasets are explicit blockers rather than invented fixtures labeled official.

Run E2E-36 plus relevant regression/patch-tamper/cache cases. Use the exact report. Next when DONE: Prompt 25 — Implement realistic repository tasks.
```

### Prompt 25 — Implement realistic repository tasks

**Phase:** 5 · **WP:** WP-20, part 2 · **Read:** T §§11,15,17; A §2 · **Deliverable:** independently curated realistic task suite.

```text
Execute Prompt 25 under the execution contract. Implement independently curated realistic repository tasks with correctly labeled inspiration and evidence-based grading.

Engineering tickets:
- PCB-25-1 — Implement task authoring/import for developer requests spanning real files/modules, with repo conventions, allowed changes and acceptance contracts. DoD: multiple valid implementations can succeed; hidden requirements are not improvised after seeing a candidate.
- PCB-25-2 — Implement executable acceptance plus bounded rubric items for genuinely non-executable criteria, using existing judge/reviewer services. DoD: all criteria have frozen evidence methods and required-gate status; judgments cannot override failed mandatory tests.
- PCB-25-3 — Integrate patch/workspace artifacts and applicable quality profiles with baseline-aware evidence. DoD: repository-wide legacy debt does not become a candidate penalty and unchanged files are not scored as new code.
- PCB-25-4 — Admit realistic multi-file fixtures and document the Cursor-inspired methodology boundary. DoD: no claim of private CursorBench task access/exact reproduction or false statement that its approach ignores quality/efficiency.

Deliver the suite, admitted task packs, acceptance/rubric definitions, method docs and actual evaluation reports. Test at least one valid alternative, one functionally failing patch and one conventions/quality weakness that survives functional tests.

Record relevant E2E-36 and shared grading/judge cases with precise scope. Use the exact report. Next when DONE: Prompt 26 — Implement self-repair.
```

### Prompt 26 — Implement self-repair

**Phase:** 5 · **WP:** WP-20, part 3 · **Read:** T §§4.3,7–9,17; A §2 · **Deliverable:** fixed-budget repair rounds · **E2E:** E2E-37.

```text
Execute Prompt 26 under the execution contract. Implement self-repair as an explicitly versioned candidate protocol, not as hidden retries after a failed score.

Engineering tickets:
- PCB-26-1 — Implement initial/repair-round state, allowed public feedback and fixed round/budget limits. DoD: every round retains its candidate, prompt, public feedback and cost; the protocol fixes when rounds stop.
- PCB-26-2 — Implement final candidate selection without hidden-result access. DoD: final quality evaluates the protocol-selected artifact, never the best hidden-scoring round; initial/final native correctness and cumulative cost remain distinct.
- PCB-26-3 — Integrate durable checkpoints and infrastructure retries at round boundaries. DoD: recovering infrastructure does not grant additional repair rounds or erase spent budget.
- PCB-26-4 — Add admitted self-repair fixtures and native-versus-adapted methodology records. DoD: visible feedback can drive the permitted repair, while hidden outcomes cannot cause another model call.

Deliver protocol/suite implementation, round artifacts, metrics and tests. Run E2E-37, including hidden-failure feedback isolation, exhausted round budget and restart during a repair round.

Use the exact report. Next when DONE: Prompt 27 — Implement repository understanding and factual Q&A.
```

### Prompt 27 — Implement repository understanding and factual Q&A

**Phase:** 5 · **WP:** WP-20, part 4 · **Read:** T §§4,15,17.3; A §2 · **Deliverable:** fact-based repository Q&A · **E2E:** E2E-38 Q&A variants.

```text
Execute Prompt 27 under the execution contract. Implement repository Q&A as answer evaluation with repository evidence, not a six-dimensional generated-code score.

Engineering tickets:
- PCB-27-1 — Implement pinned repo/question inputs, read/search-only solving and structured answers/citations. DoD: citations reference the base snapshot; editing is disabled for this protocol; retrieval context and truncation are logged.
- PCB-27-2 — Implement atomic-fact oracles, accepted paraphrases and fixed entailment judging with preserved native adapter aggregation. DoD: missing facts get no credit, repeated facts add no credit and empty answers have fact recall zero.
- PCB-27-3 — Implement citation validity, grounding and unsupported/contradicted-claim diagnostics with explicit unknown states. DoD: incomplete claim verification is not zero hallucinations; prose does not receive invented security/runtime/idiom scores.
- PCB-27-4 — Admit cross-file Q&A fixtures with verifying code spans, alternative correct wording, wrong citations and contradictions. DoD: source/method records accurately describe DeepCodeBench-inspired or native compatibility and all fact evidence is versioned.

Deliver protocol, oracle/grade schemas, metrics, task packs and reports. Run E2E-38 Q&A cases and adversarial citation/judge fixtures. Retain the prediction variants as pending until Prompt 28.

Use the exact report. Next when DONE: Prompt 28 — Implement prediction suites and close Track B coverage.
```

### Prompt 28 — Implement prediction suites and close Track B coverage

**Phase:** 5, exit · **WP:** WP-20, final part · **Read:** T §§17,23; A §2 · **Deliverable:** output/test prediction and full suite audit · **E2E:** E2E-36–38.

```text
Execute Prompt 28 under the execution contract. Complete deterministic prediction families and verify that no requested Track B scenario disappeared from implementation.

Engineering tickets:
- PCB-28-1 — Implement code-execution/output-prediction inputs and protocol restrictions. DoD: the evaluated model cannot run the target code when the declared task measures prediction without execution; a different tool policy is a different cohort.
- PCB-28-2 — Implement test-output-prediction tasks and explicit exact_bytes/normalized_text/typed_json grading. DoD: normalization rules are frozen, parse errors are wrong answers, and judges do not rescue deterministic mismatches.
- PCB-28-3 — Admit prediction fixtures and wire native/ported/inspired methodology plus answer-only metric definitions. DoD: generated-code dimensions stay N/A and output/error types are handled consistently through reports/API schemas.
- PCB-28-4 — Audit codegen, repository repair, realistic repo tasks, self-repair, repo Q&A, output prediction and test prediction through actual entrypoints. DoD: all have source records, output contracts, allowed tools/feedback, grading, missingness and reproducible evidence; close WP-20 only when every required family passes.

Deliver suite plugins, task packs, prediction graders and docs/implementation/reports/suite-coverage.md. Run E2E-36/37/38 remaining variants and targeted integrations after any fixes; preserve prior applicable evidence rather than claiming new runs.

End with the exact report and Phase 5 gate. Next when DONE: Prompt 29 — Implement the complete public API and projections.
```

### Prompt 29 — Implement the complete public API and projections

**Phase:** 6 · **WP:** WP-21 · **Read:** T §§19–21; A §§13–14 · **Prerequisites:** WP-16,18,19,20 complete · **Deliverable:** public data contract · **E2E:** E2E-25/26/28/39 API variants.

```text
Execute Prompt 29 under the execution contract. Implement the complete public/read API against reviewed release projections and close administrative endpoint gaps from earlier modules.

Engineering tickets:
- PCB-29-1 — Implement release, leaderboard, model, language, comparison, public task, scorecard, artifact and methodology endpoints with typed metric definitions. DoD: responses come from actual published projections, never worker/hidden tables or hardcoded demo arrays.
- PCB-29-2 — Implement release/filter-bound pagination, stable sorting, decimal serialization, ETags/cache policy and common-cohort comparisons for 2–4 configurations. DoD: incompatible protocols/coverage return the specified typed reasons; filtered denominators and uncertainty reflect the actual cohort.
- PCB-29-3 — Complete administrative/API authorization, idempotency, optimistic concurrency, error taxonomy and publication/read access controls. DoD: permissions are enforced on every route/artifact; private identity probes cannot reveal hidden data or useful download tokens.
- PCB-29-4 — Generate and validate OpenAPI/TypeScript clients and safe public response fixtures. DoD: schemas/client/server agree, and fixtures originate from real test-release generation with explicitly synthetic labels where appropriate.

Deliver complete API, projections/query layer, generated client, schema contract tests and release-backed request examples. Implement access checks independently of the future UI.

Run full API variants of E2E-25/26/28, snapshot immutability and the API portion of E2E-39. Test each public route with hidden identities and changed filters/cursors. Browser navigation remains pending until later prompts.

Use the exact report. Next when DONE: Prompt 30 — Build leaderboard, language and model pages.
```

### Prompt 30 — Build leaderboard, language and model pages

**Phase:** 6 · **WP:** WP-22, part 1 · **Read:** T §21; A §14 · **Prerequisite:** public API/client · **Deliverable:** first three complete pages · **E2E:** E2E-39/40 page variants.

```text
Execute Prompt 30 under the execution contract. Build the first three production-shaped public pages using actual release-backed API data.

Engineering tickets:
- PCB-30-1 — Implement shared layout/navigation, release selection, shareable URL filter state, typed loading/error/empty states and accessible table/chart primitives. DoD: no frontend scoring formula or hardcoded language registry diverges from the API.
- PCB-30-2 — Implement leaderboard scope/mode/budget labels, sortable metrics, confidence intervals, coverage, pass rate/cost and release notices. DoD: failed/gated zero, missing, N/A and pending-review states remain visually and semantically distinct.
- PCB-30-3 — Implement language leaderboards and diagnostic profile views with opportunity counts/tool coverage. DoD: graphs do not imply evidence for untested language features or reuse inappropriate TS metrics on JS.
- PCB-30-4 — Implement model profiles with code-only radar, language/dimension heatmap, generation cost/latency and factual supported summaries. DoD: answer-only tasks receive no invented code dimensions; all displayed numbers link to source scores/evidence.

Deliver responsive, clean, data-dense pages and browser tests. Use real internal/test release projections for development; clearly label synthetic test data and never describe it as live benchmark results.

Run relevant E2E-39/40 browser cases, keyboard interaction, small/large viewport checks, failure/N/A rendering and actual page/build checks. Capture useful screenshots/test artifacts without publishing private data. Fix broken states before reporting completion.

Use the exact report. Next when DONE: Prompt 31 — Build comparison, task explorer and methodology pages.
```

### Prompt 31 — Build comparison, task explorer and methodology pages

**Phase:** 6 · **WP:** WP-22, part 2 · **Read:** T §21; A §14 · **Prerequisite:** first pages/API · **Deliverable:** next three pages and evidence navigation · **E2E:** E2E-26, E2E-39, E2E-40.

```text
Execute Prompt 31 under the execution contract. Complete the public analysis/explanation workflows with exact cohort and privacy semantics.

Engineering tickets:
- PCB-31-1 — Build 2–4 model comparison with compatibility feedback, common-task paired differences/intervals and configuration identities. DoD: the same visible task and release underpin code comparisons; incompatible budgets/protocols are not silently mixed.
- PCB-31-2 — Implement bounded escaped source/diff views, task browsing, public statement/source versions, submitted patches and tool findings. DoD: uploads are inert, large payloads are lazy-loaded, and private/held-out candidates are never accidentally exposed.
- PCB-31-3 — Implement metric-to-task-to-item-to-evidence drilldowns with raw/gated values, effective weights, versioned formulas and redacted-private explanations. DoD: a user can reconstruct a public score from unrounded contributions within documented rounding.
- PCB-31-4 — Build frozen methodology and correction/withdrawal views, native-versus-adapted labels and explicit limitations. DoD: historical URLs retain the original release identity and clearly identify successors/withdrawals.

Deliver integrated comparison/task/methodology pages and complete evidence navigation. Cross-check every displayed scalar against the API and source scorecard rather than calculating an alternative UI score.

Run E2E-39 navigation from leaderboard through comparison/task/evidence, E2E-26 public privacy/export cases and affected E2E-40 accessibility/payload states. Keep the seventh page/submission flow pending until Prompt 32.

Use the exact report. Next when DONE: Prompt 32 — Implement reviewed model submissions and close the public product phase.
```

### Prompt 32 — Implement reviewed model submissions and close the public product phase

**Phase:** 6, exit · **WP:** WP-22 final part, WP-23 · **Read:** T §§8,20–21; A §§13–14 · **Deliverable:** seventh page and reviewed request lifecycle · **E2E:** E2E-41; close E2E-25/26/39/40 variants.

```text
Execute Prompt 32 under the execution contract. Implement model-submission requests without giving public users arbitrary execution, secret access or unlimited spend.

Engineering tickets:
- PCB-32-1 — Implement submitter identity/ownership, validated metadata, rate limits and pending/rejected/approved request states. DoD: a public request creates no model call, VM or automatic benchmark run and cannot carry provider secret values in the public schema.
- PCB-32-2 — Implement reviewer/admin endpoint/capability checks, source/permission records, secret-reference setup and explicit bounded run plans. DoD: SSRF/private-address rules hold and approval binds a concrete model/config/budget, not unlimited future evaluations.
- PCB-32-3 — Implement authorized run creation and submitter-safe status updates after approval. DoD: existing idempotency/budget/audit controls apply; repeated approval/request cannot create duplicate spending; other users cannot read the request.
- PCB-32-4 — Build the seventh public page and review the complete seven-page product. DoD: request/error/status states work, public/admin/submitter permissions are tested server-side, and all pages use actual release data/contracts without placeholder features.

Deliver request/review services, routes, UI, verified test data and the complete public-flow report. Admin review may use the established private CLI/API; a large new admin dashboard is not required by this scope.

Run E2E-41 plus complete role/privacy/browser variants of E2E-25/26/39/40. Verify an approved bounded synthetic/test request transitions correctly and an unapproved malicious endpoint is never contacted. Live spending only under existing explicit authorization.

End with the exact report and Phase 6 aggregate gate. Next when DONE: Prompt 33 — Harden deployment and rehearse operations.
```

### Prompt 33 — Harden deployment and rehearse operations

**Phase:** 7, exit · **WP:** WP-24 · **Read:** T §§10,19,22,25; A §15 · **Prerequisites:** all public-product/engine modules · **Deliverable:** demonstrated operational readiness · **E2E:** E2E-42, E2E-43.

```text
Execute Prompt 33 under the execution contract. Complete production-shaped infrastructure and prove its essential operating/recovery procedures within authorized staging scope.

Engineering tickets:
- PCB-33-1 — Complete environment-separated infrastructure-as-code for network/identities, database/backups, artifacts, images, control services, workers, performance capacity, secrets, signing and public delivery. DoD: clean staging deployment is reproducible; actual identity/policy enforces environment and isolation tier, not a request string.
- PCB-33-2 — Implement validated deployment/configuration, migration/rollback/drain procedures, telemetry and required alerts. DoD: expanded schemas remain compatible, stale workers cannot commit, logs/metrics expose useful run IDs without leaking secrets/held-out content.
- PCB-33-3 — Implement and execute restore/orphan/outage/withdrawal/key-rotation procedures as permitted. DoD: isolated restoration verifies referential/digest integrity, replays ten stratified scorecards, rebuilds a public projection and reports measured recovery timing; resources are reclaimed.
- PCB-33-4 — Complete operator runbooks, retention/rights policies and documented load/security rehearsals. DoD: each runbook has exact verified commands, authorized role, expected state/result and recovery verification; unmeasured SLOs are targets, not achievements.

Deliver IaC, environment manifests, service policies, dashboards/alerts, runbooks and rehearsal reports. Prepare concrete plans and budgets before any not-yet-authorized provisioning. If authorization or access is missing, finish code/local validation and provide the exact remaining plan; do not weaken the staging gate or deploy an unrelated target.

Run E2E-42/43 and affected isolation/publication/load checks in actual staging. Preserve baseline evidence and note any production-only steps not executed. Do not publish benchmark results just because the infrastructure works.

End with the required report and Phase 7 gate. Next when DONE: Prompt 34 — Perform the final integrated audit and repair pass.
```

### Prompt 34 — Perform the final integrated audit and repair pass

**Phase:** 8, exit · **WP:** all WP-01–24 · **Read:** both source docs, all ledgers/reports and current code · **Deliverable:** verified completion assessment · **E2E:** E2E-01–43 with required variants.

```text
Execute Prompt 34 under the execution contract. Act as the implementation engineer and final reviewer for the actual PolyCodeBench system. Audit and fix the integrated product; do not merely summarize previous reports or count unit tests.

Engineering tickets:
- PCB-34-1 — Reconcile the current repository against both source documents, all REQ-01–14, WP-01–24 and PCB tickets. Read actual code/config/entrypoints and inspect changed source hashes. DoD: every requirement has a concrete implementation path and appropriate verification; outdated reports or placeholder-only features are identified.
- PCB-34-2 — Verify the complete E2E-01–43 matrix and its language/family/route/environment variants. Inspect raw evidence, commands, dates/revisions and artifact identities; rerun missing/stale/risk-affected checks. DoD: no fixture masquerades as a live provider/VM test and no skipped/blocked case is counted passed. Reuse valid expensive evidence explicitly; do not automatically repeat the whole paid campaign.
- PCB-34-3 — Trace representative complete journeys through the real entrypoints: task import/admission/freeze → run planning/budget → model/agent → frozen candidate → fresh grading → evidence/judging → score/replay → release review → public API/website. Cover both protocols, all eight language identities, every Track B family and Track A detection/repair. DoD: integrations, visibility and identity remain intact across boundaries, with synthetic journey fixtures clearly labeled where used and the actual 144-attempt pilot separately evidenced.
- PCB-34-4 — Exercise decisive failure paths: wrong-fast code, bogus findings, novel bugs, private artifact probes, test tampering, analyzer/judge failure, ambiguous billing, stale leases, cancellation, missing coverage and approval-invalidating changes. Fix defects at their cause and rerun impacted checks. DoD: failures produce correct outcomes without score inflation, unauthorized actions or discarded evidence.
- PCB-34-5 — Audit readiness and produce the final report bundle. DoD: native-method claims, calibration, held-out handling, source rights, real costs/unknowns, CI/variance, restore evidence and every website page agree with actual artifacts; remaining limitations are concrete and not hidden by “MVP done.”

Create docs/implementation/reports/final-e2e-audit.md, an updated requirement/E2E matrix, an evidence index, a defect-and-fix log, a scored-release-readiness checklist, and exact verified next commands. Explicitly distinguish:
1. Code implemented and locally/integration verified.
2. Real model/VM/human calibration evidence satisfied or blocked.
3. Exploratory pilot complete versus public ranked-release criteria satisfied.
4. Ready for owner release review versus actually published to an authorized target.

Do all available implementation/fix/verification work before reporting external blockers. Do not invent missing data, human approvals, extra independent clusters or cloud results. Do not lower gates or change weights/task sets silently. A production/publication action requires the already authorized target/budget/review or a concrete final authorization request; readiness is not a claim of deployment.

Finish with the exact five-part completion report and Phase 8 aggregate gate. If all requested gates are DONE, there is no Prompt 35: give one exact verified read-only health/inspection command from the actual command registry as the next action. If blocked, give the exact unblock command when available or Auxiliary R2 with the recorded blocker. Stop.
```

## 7. Auxiliary prompts

Use these only when their situation applies. They do not replace the numbered sequence or silently mark a blocked phase complete.

### Auxiliary R1 — Resume interrupted or failed work

```text
Resume PolyCodeBench from the repository's persisted implementation state.

Read repository instructions, docs/implementation/execution-contract.md, source-manifest.json, progress.json, the latest prompt/phase report and the ticket/E2E matrices. Inspect git status and actual implementation/evidence before accepting previous completion claims.

Determine the last active numbered prompt and its unfinished tickets. Reconcile source/code changes since the recorded state. Preserve correct work and unrelated edits. Continue that prompt's implementation and required verification, fixing failures rather than restarting the project or jumping phases.

If interrupted work included a model call, job, upload or release action, inspect its durable identity/state before retrying. Do not create duplicate spending, overwrite frozen evidence or repeat an already committed action blindly.

Complete all available work. If an external blocker remains, record its exact input/action and finish nonblocked tasks first. Update all affected ledgers and end with the same five-part report for the resumed numbered prompt, including the exact next command or numbered prompt. Do not automatically run the next prompt.
```

### Auxiliary R2 — Close an external validation or authorization blocker

```text
Close the recorded PolyCodeBench external blocker using the credentials, environment, human review, budget or authorization now available in this session. Read the execution contract, progress, blocked gate, source requirement and prepared run/deployment/validation plan first.

Verify the supplied access/authorization matches that exact target, scope and bounded cost. Reuse already valid authorization; do not ask for it again. Do not print secrets. If a required input is still absent, complete any remaining safe preparation and state only the specific missing item.

Execute the previously blocked real verification/action when authorized, using existing immutable identities, budgets and idempotency/recovery controls. Preserve actual commands, usage and artifacts. Do not substitute fixture results or relabel development isolation as production.

Re-evaluate the affected tickets/E2E variants/phase gates and any dependent evidence made stale by changes. Update ledgers/reports. Finish with the exact five-part completion report and the precise next numbered prompt or verified command. Do not run unrelated expensive campaigns or publish to a new target.
```

### Auxiliary R3 — Revalidate after code, spec, tool or model changes

```text
Revalidate PolyCodeBench after the current change without silently invalidating old results.

Read the execution contract, current source manifest, git diff/revisions and prior accepted evidence. Identify changed task, candidate, toolchain, analyzer/rules/advisory data, provider/model/harness, judge, scoring, hardware, API/UI and deployment identities as applicable.

Build an impact map from those changes to requirements, tickets, E2E variants and existing releases. Preserve old immutable artifacts and public historical results. Record any material specification discrepancy and prepare a concrete versioned amendment where needed.

Fix implementation regressions and run the smallest sufficient affected verification set, plus the necessary integrated journey checks. Rescore or reevaluate only under the correct new identities; regenerate candidates only when the change actually requires it. Paid execution must remain within existing authorization/budgets.

Update source/evidence manifests, matrices, discrepancy records and release correction/readiness state. Finish with the exact five-part report, clearly separating newly run evidence from prior evidence reused and listing the exact next command or numbered prompt. Do not change historical scores in place or automatically start the entire prompt sequence again.
```

## 8. End-to-end ownership and closure map

The technical specification remains authoritative for each scenario's complete assertion. These owner prompts must collect evidence; Prompt 34 checks completeness and fixes gaps. Ranges in this table denote all individual scenarios in that range.

| E2E scenario | Implementation/evidence owners | Required closure detail |
|---|---|---|
| E2E-01 | 02 | Actual Python and TypeScript canonical-byte equality and invalid-input checks |
| E2E-02 | 03, 07, 17 | Database/service idempotency plus integrated run creation |
| E2E-03 | 04 | Real object-store altered-byte rejection |
| E2E-04 | 05, 10, 11, 12, 17 | Reference/faulty/alternative admission, including actual pilot tasks |
| E2E-05 | 06, 33 | Production VM/network/metadata/path isolation |
| E2E-06 | 06, 33 | Enforced resource limits and real termination |
| E2E-07 | 07 | Concurrent reclaim and stale-fence rejection |
| E2E-08 | 07 | Artifact/transaction interruption recovery |
| E2E-09 | 07, 17 | Integrated cancellation, guest lifecycle and usage preservation |
| E2E-10 | 08 | Concurrent budget reservation transaction |
| E2E-11 | 08, 09 | Stored-response reuse across controller restart |
| E2E-12 | 08 | Ambiguous provider outcome and uncertain-cost accounting |
| E2E-13 | 09, 17 | Both protocols and correct cohort/tool/budget identities |
| E2E-14 | 09 | Transcript/workspace checkpoint recovery |
| E2E-15 | 10, 11, 19–23 | Every required language and fixture category |
| E2E-16 | 11, 12, 19–23 | Findings versus crash/unsupported semantics for applicable tools |
| E2E-17 | 12, 24 | Protected test inventories/overlays and native adapter behavior |
| E2E-18 | 12 | Canonical issue deduplication and unrelated baseline debt |
| E2E-19 | 13 | Actual paired measurements and full resource accounting |
| E2E-20 | 13 | Invalid-block handling and first-valid selection |
| E2E-21 | 14 | Three-vote aggregation and evidence preservation |
| E2E-22 | 14 | Invalid/missing judge votes and untrusted candidate instructions |
| E2E-23 | 15 | All golden formulas, gating/N/A and ownership properties |
| E2E-24 | 15, 17, 33 | Clean replay, live pilot replay and restored evidence replay |
| E2E-25 | 03, 29, 32 | Every administrative/submitter/public role and applicable route |
| E2E-26 | 04, 29, 31, 32 | Storage, public API, pages/downloads/exports and private-ID probes |
| E2E-27 | 05, 16, 29 | Exposure/cutoff/split semantics through registry and public filters |
| E2E-28 | 16, 29 | Common cohorts and honest missing-language/coverage behavior |
| E2E-29 | 16 | Correct clustering, paired differences and interval reproducibility |
| E2E-30 | 16, 33 | Approval invalidation, atomic pointer race and historical correction |
| E2E-31 | 17 | Actual authorized 144-attempt two-model Python/Rust pilot |
| E2E-32 | 18 | Detection counts, duplicates and missed-bug credit |
| E2E-33 | 18 | Novel valid bug review and consistent oracle revision |
| E2E-34 | 18 | Separate detection/repair and clean-control semantics |
| E2E-35 | 10, 11, 19–23 | JS/TS, concurrency/idiom applicability and language-specific cases |
| E2E-36 | 24, 25 | Native/adapted labels, real repository tasks and cache identity |
| E2E-37 | 26 | Self-repair feedback/round selection/recovery |
| E2E-38 | 27, 28 | Both factual Q&A and deterministic prediction families |
| E2E-39 | 29, 30, 31, 32 | Complete browser evidence journeys over consistent API values |
| E2E-40 | 30, 31, 32 | All seven pages, missingness/interval states and accessibility/load |
| E2E-41 | 32 | No execution before review; bounded approved submission run |
| E2E-42 | 33 | Actual isolated restore and ten-scorecard/projection reconstruction |
| E2E-43 | 33 | Clean staging deployment and operational drills |

## 9. Final review checklist for the owner

Use this checklist when reading phase reports; no implementation status is implied by this document.

- Is the report describing behavior that exists, with changed paths and real commands, rather than only a plan?
- Do satisfied gates point to evidence, and do pending/blocked gates remain visible?
- Are live model, VM, calibration and restore claims supported by the required evidence type?
- Are all eight language identities and every task family present by the full-product gate?
- Do failed attempts, unknowns, missing analyzers, private artifacts and novel bug findings follow the spec?
- Can a published number be traced to a fixed cohort, score item and permitted underlying evidence?
- Are native benchmark metrics/adaptations labeled accurately, and are statistical limitations disclosed?
- Does the next action name an exact available command or the correct numbered prompt?

Begin by supplying **Prompt 00** with the two source documents and this pack available in the repository. Thereafter, use the completion reports to advance, resume or unblock the specific scope.
