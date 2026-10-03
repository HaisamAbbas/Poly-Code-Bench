# Repository-task methodology: authoring, admission and grading

Prompt 25 (PCB-25-1..4, WP-20 part 2). This document is the frozen description of how
PolyCodeBench's realistic repository tasks are authored, admitted and graded, and where their
methodology boundary lies.

## Methodology boundary (PCB-25-4)

Repository tasks in `taskpacks/repo-tasks/` are **CursorBench-inspired**: their shape - one real
developer request spanning several files and modules of a working repository, evaluated with
acceptance evidence plus bounded graders for the genuinely non-executable criteria - follows the
public methodology description at <https://cursor.com/blog/cursorbench>. The tasks themselves are
**independently curated** by PolyCodeBench. No CursorBench task, grader, score or private asset is
used, and nothing is a reproduction of the private benchmark: no claim of private task access or
exact reproduction is made anywhere in the packs, reports or code, and the inspiration statements
in the pack manifests are validated against exactly those claims.

The source description measures correctness alongside code quality, efficiency (median completion
tokens) and interaction behavior; PolyCodeBench makes no claim that the approach ignores quality
or efficiency. PolyCodeBench's own evaluation covers correctness (executable acceptance),
code quality (baseline-aware quality evidence plus bounded rubric items) and keeps efficiency as a
declared not-applicable dimension for these fixture tasks rather than pretending to measure it.

Labels are a provenance record, not a ranking: `native` is reserved for imported upstream
benchmark records (Prompt 24's suite adapter), `adapted` for modified upstream tasks, `inspired`
for independently curated tasks shaped after a public methodology description. The authoring
validator refuses `native` for independently curated packs and refuses reproduction claims in
inspiration statements.

## Authoring (PCB-25-1)

A repo-task pack is a standard task package (`manifest.yaml`, strict import through
`TaskPackageImporter`, visible/hidden separation, disclosure scan) plus `repo-task.yaml`, the
authoring document (`polycodebench_services.repo_tasks`):

- `request`: the developer request as written, the repository paths it spans, and the repository's
  own convention documents (visible to the solver).
- `allowed_changes`: the exact output-contract allowlist plus protected paths.
- `acceptance`: frozen criteria. Each criterion declares exactly one `evidence_method`
  (`executable` or `judge_rubric`) and an explicit `required_gate` status.

Two invariants:

- **Multiple valid implementations can succeed.** Criteria are written against observable
  behaviour, never against the reference's structure, and every pack ships a distinct
  alternative-valid variant that passes the same frozen contract. Admission refuses packs without
  the full variant matrix (`reference`, `alternative`, `faulty`, `quality_defective`).
- **Hidden requirements are not improvised after seeing a candidate.** `seal_acceptance_contract`
  digests the criteria, the hidden case inventory, the rubric identity and the convention rules
  into one contract digest *before any candidate exists*. Grading verifies the digest first
  (`verify_acceptance_contract`) and raises `AcceptanceContractDrift` on any change: added
  criteria, re-gated criteria and edited case inventories are refusals, not new requirements.

## Acceptance evidence (PCB-25-2)

- **Executable criteria** are the mandatory acceptance contract. The task's own hidden runner
  executes them against the candidate workspace. The runner contract is fixed:
  `python acceptance_runner.py --workspace DIR --report FILE` writes
  `{"cases": [{"case_id", "outcome", "reason"}]}` covering exactly the frozen inventory; a report
  naming other cases is a harness breach and makes grading incomplete rather than silently
  accepted.
- **Judge criteria** cover only genuinely non-executable requirements. Each is bounded to item ids
  of the frozen rubric (`config/judging/rubric-v1.yaml`) and carries a residual reason explaining
  why no executable or static check owns it. Judgments run through the existing judge services
  (packet build, vote parse, aggregate); outcomes reach the scorer only through
  `polycodebench_scoring.judge_evidence`, which maps `ready/needs_review/infra_blocked` to
  `measured/needs_review/missing` and never invents a score.

**Gate precedence.** The mandatory gate is computed from required criteria before any judge result
is read (`executable_gate`), and `combine_gate` accepts no scores at all: a judgment can only add
failing conditions (a required-gate judge criterion below its frozen minimum), never rescind a
failed mandatory test. A perfect judgment over a functionally failing patch grades a zero at the
scorecard (`Scorecard` also refuses nonzero contributions on a failed gate). Production
orchestration checks `judge_is_wanted` and does not spend judge budget on gate-failed candidates;
an operator-requested diagnostic judgment still cannot change the outcome.

## Baseline-aware quality evidence (PCB-25-3)

The declared convention rules are deterministic AST checks run on both the frozen baseline
snapshot and the candidate workspace; findings are related through the shared
`evaluator.baseline_relations` vocabulary (`introduced`, `worsened`, `unchanged_in_scope`,
`unchanged_out_of_scope`, `resolved`, `unknown`), and each finding is one canonical issue however
many rules or locations report it.

- **Legacy debt is context.** Pre-existing findings the candidate never touched are
  `unchanged_out_of_scope`: visible in the report, never a penalty. The pack fixtures deliberately
  contain such debt (`cfgkit/legacy_report.py`).
- **Unchanged files are never scored as new code.** The new-code scope is exactly the files the
  candidate changed; penalties only apply to findings in that scope (or to `introduced` findings),
  per the frozen `baseline_penalty_relations` of the authoring contract.
- Patch artifacts (`unified_diff`, applied through the solve-session patch engine) and workspace
  artifacts (`source_bundle`) grade identically; protected and out-of-allowlist paths are refused
  before anything is computed.

## Admission (PCB-25-4)

`polycodebench_evaluation.repo_task_admission` runs the authored variants through the same
acceptance and convention machinery grading uses: the reference passes the full hidden inventory
five times with identical outcomes, the alternative passes and differs from the reference, the
functionally failing variant fails at least its declared cases, and the quality-defective variant
passes every functional case while the convention scan reports its intended defect families in its
changed files. Reports record `local_fixture` execution tier and `fixture_judge_votes` judge
evidence class: judge endpoints are unprovisioned in this workspace (`config/judging/panel-v1.yaml`),
so judge evidence in Prompt 25 reports is deterministic fixture votes through the real judge
services, and no live judge call is made or claimed.

## What is not claimed

- No production-worker execution, no live judge or model calls, no scored benchmark result.
- No performance/efficiency measurement for these tasks (declared not applicable).
- Prompt 24's upstream suite adapter, native metric export and native cache identity remain a
  separate, unimplemented scope; this document covers only the independently curated suite.
