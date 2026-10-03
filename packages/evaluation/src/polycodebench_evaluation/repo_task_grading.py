"""Independent grading for realistic repository tasks (Prompt 25, PCB-25-2 and PCB-25-3).

One grading pass over one frozen candidate produces everything downstream needs:

1. **Executable acceptance** (PCB-25-2). The hidden acceptance inventory runs against the
   candidate workspace and every result is bound to a frozen acceptance criterion. The mandatory
   gate is computed from required criteria *before* any judge result is read.
2. **Bounded rubric items** (PCB-25-2). Genuinely non-executable criteria are scored through the
   frozen judge rubric using the existing judge services; outcomes reach the scorer only through
   :mod:`polycodebench_scoring.judge_evidence`. A judgment can never satisfy, override or
   downgrade an executable criterion: :func:`combine_gate` takes no scores at all, and a failing
   gate is never rescinded. A perfect judgment over a functionally failing patch is a zero.
3. **Baseline-aware quality evidence** (PCB-25-3). Convention findings are computed on the frozen
   baseline snapshot and the candidate workspace and related through
   ``evaluator.baseline_relations``. Repository-wide legacy debt (``unchanged_out_of_scope``) is
   context, never a candidate penalty, and unchanged files are never counted as new code: the new
   code scope is exactly the files the candidate changed.

Hidden requirements cannot appear mid-flight: :func:`grade_repo_task` verifies the sealed
acceptance contract digest (see :mod:`polycodebench_services.repo_tasks`) before it touches the
submission, and refuses to grade against edited criteria.

Execution evidence here is ``local_fixture`` tier: hidden acceptance runs as a bounded local
subprocess and the convention scan is an offline deterministic analyzer. Production-worker
execution and live judge endpoints remain separate, explicitly pending gates.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from polycodebench_core.canonical import canonical_digest, sha256_bytes
from polycodebench_core.identity import new_entity_id
from polycodebench_core.judge_contracts import JudgementResult, JudgeRubric
from polycodebench_core.models import ScoreDimension
from polycodebench_plugins_api import FrozenTask
from polycodebench_runner.guest_helper import (  # type: ignore[import-untyped]
    ToolFailure,
    apply_hunks,
    parse_patch,
)
from polycodebench_scoring.judge_evidence import judged_means, judgement_items
from polycodebench_scoring.manifest import (
    EvidenceRef,
    GateVerdict,
    RequiredEvidenceRecord,
    RubricItemEvidence,
    ScoringInvocation,
    ValidatedEvidenceManifest,
)
from polycodebench_scoring.policy import FrozenScoringPolicy
from polycodebench_scoring.scorer import ScoringOutcome, score_evaluation
from polycodebench_services.repo_tasks import (
    RepoTaskAuthoring,
    RepoTaskBinding,
    verify_acceptance_contract,
)
from polycodebench_services.task_packages import TaskPackageManifest

from polycodebench_evaluation.evaluator import CandidateRejected, baseline_relations
from polycodebench_evaluation.evidence import IssueEvidence, Relation
from polycodebench_evaluation.judge_inputs import judge_packet_input_from
from polycodebench_evaluation.plan_runner import digest_files
from polycodebench_evaluation.repo_task_conventions import (
    ConventionAnalyzer,
    ConventionFinding,
    findings_as_observations,
)

EXECUTION_TIER: Literal["local_fixture"] = "local_fixture"
CONVENTION_ANALYZER_ID = "repotask-convention"
ACCEPTANCE_TIMEOUT_SECONDS = 60
MAX_SUBMISSION_BYTES = 1_048_576

GateStatus = Literal["pass", "fail", "incomplete"]
CriterionStatus = Literal["pass", "fail", "incomplete", "graded"]
JudgeDisposition = Literal["used", "suppressed_after_mandatory_failure", "not_run"]
Severity = Literal["critical", "high", "medium", "low"]

#: The default penalized set the evaluator vocabulary agrees on. The authoring contract may
#: override it before freeze; reports use the frozen contract's set.
PENALIZED_BY_DEFAULT = ("introduced", "worsened")


@dataclass(frozen=True)
class CaseOutcome:
    """One hidden acceptance case, as reported by the task's own acceptance runner."""

    case_id: str
    outcome: Literal["pass", "fail", "error", "skipped"]
    reason: str = ""


@dataclass(frozen=True)
class CriterionResult:
    """One frozen acceptance criterion against one candidate."""

    criterion_id: str
    evidence_method: Literal["executable", "judge_rubric"]
    required_gate: bool
    status: CriterionStatus
    detail: str
    case_ids: tuple[str, ...] = ()
    rubric_item_ids: tuple[str, ...] = ()
    minimum_gate_score: str | None = None


@dataclass(frozen=True)
class AcceptanceRun:
    """Raw acceptance evidence: case outcomes plus harness health."""

    cases: tuple[CaseOutcome, ...]
    harness_ok: bool = True
    harness_detail: str = ""


@dataclass(frozen=True)
class WorkspaceResult:
    """The candidate workspace after the submission is applied to the frozen baseline."""

    files: dict[str, bytes]
    changed_files: tuple[str, ...]
    deleted_files: tuple[str, ...]


@dataclass
class RepoTaskGrade:
    """One frozen candidate graded once: acceptance, quality evidence and judge disposition."""

    task_id: str
    task_version: int
    package_digest: str
    contract_digest: str
    rubric_digest: str
    submission_kind: str
    baseline_digest: str
    candidate_digest: str
    changed_files: tuple[str, ...]
    case_outcomes: tuple[CaseOutcome, ...]
    criteria: tuple[CriterionResult, ...]
    gate: GateVerdict
    judgement: JudgementResult | None
    judge_disposition: JudgeDisposition
    issues: tuple[IssueEvidence, ...] = ()
    resolutions: tuple[IssueEvidence, ...] = ()
    convention_report: tuple[dict[str, Any], ...] = ()
    judge_packet_input: Any = None
    penalized_relations: tuple[str, ...] = PENALIZED_BY_DEFAULT
    notes: tuple[str, ...] = ()

    @property
    def mandatory_gate_passed(self) -> bool:
        return self.gate.status == "pass"

    @property
    def failed_mandatory_criteria(self) -> tuple[str, ...]:
        return tuple(
            result.criterion_id
            for result in self.criteria
            if result.required_gate and result.status == "fail"
        )

    def to_manifest(
        self,
        *,
        policy: FrozenScoringPolicy,
        invocation: ScoringInvocation,
        language_id: str,
    ) -> ValidatedEvidenceManifest:
        """The scoring input for this grade (Technical Spec 14.x manifest contract).

        Judge-backed rubric rows are built only through the frozen judge-evidence adapter. When
        judgment is suppressed or absent the rows are explicit ``missing`` evidence - never a
        zero, never an inferred score.
        """
        plan = code_quality_weight_plan(policy)
        if self.judgement is not None:
            items = judgement_items(
                self.judgement,
                weights=plan,
                evidence_ref=EvidenceRef(
                    ref_type="judge_vote",
                    ref_id=f"{self.task_id}:judgement",
                ),
            )
        else:
            items = tuple(
                RubricItemEvidence(
                    item_id=item_id,
                    dimension=dimension,
                    weight_bp=weight_bp,
                    status="missing",
                    score_bp=None,
                    source="judge_votes",
                    evidence_refs=(),
                    reasons=(
                        "judge evidence was not run for this candidate ("
                        + self.judge_disposition
                        + ")",
                    ),
                )
                for item_id, (dimension, weight_bp) in sorted(plan.items())
            )
        convention_digest = sha256_bytes(
            canonical_digest({"rows": list(self.convention_report)}).encode("utf-8")
        )
        return ValidatedEvidenceManifest(
            evaluation_id=new_entity_id(),
            task_id=self.task_id,
            task_version=self.task_version,
            task_digest=self.package_digest,
            language_id=language_id,
            candidate_digest=self.candidate_digest,
            execution_tier=EXECUTION_TIER,
            invocation=invocation,
            gate=self.gate,
            required_evidence=(
                RequiredEvidenceRecord(
                    analyzer_id=CONVENTION_ANALYZER_ID,
                    required=True,
                    status="complete",
                    detail="deterministic convention scan on baseline and candidate",
                    evidence_refs=(
                        EvidenceRef(
                            ref_type="analyzer_report",
                            ref_id=f"{self.task_id}:convention-scan",
                            digest=convention_digest,
                            tool_id=CONVENTION_ANALYZER_ID,
                        ),
                    ),
                ),
            ),
            applicability=(ScoreDimension.CORRECTNESS, ScoreDimension.CODE_QUALITY),
            security_issues=(),
            security_unreviewed_relevant=(),
            efficiency=None,
            items=items,
            blocking=(),
            diagnostic_items=(),
            advisory_snapshot_digest=None,
            advisory_scans_applied=False,
            evidence_refs=(),
            notes=self.notes
            + (
                "baseline-aware quality evidence: unchanged legacy debt is context, "
                "unchanged files are never scored as new code",
            ),
        )

    def to_scorecard(
        self,
        *,
        task: FrozenTask,
        policy: FrozenScoringPolicy,
        ownership: Any,
        invocation: ScoringInvocation,
    ) -> ScoringOutcome:
        manifest = self.to_manifest(
            policy=policy,
            invocation=invocation,
            language_id=task.primary_language,
        )
        return score_evaluation(task, policy, manifest, ownership=ownership)

    def report(self) -> dict[str, Any]:
        """One JSON-able grading row for evaluation reports and evidence files."""
        return {
            "task_id": self.task_id,
            "task_version": self.task_version,
            "package_digest": self.package_digest,
            "contract_digest": self.contract_digest,
            "rubric_digest": self.rubric_digest,
            "submission_kind": self.submission_kind,
            "baseline_digest": self.baseline_digest,
            "candidate_digest": self.candidate_digest,
            "changed_files": list(self.changed_files),
            "gate": {
                "status": self.gate.status,
                "reasons": list(self.gate.reasons),
                "failing_conditions": list(self.gate.failing_conditions),
                "incomplete_conditions": list(self.gate.incomplete_conditions),
            },
            "failed_mandatory_criteria": list(self.failed_mandatory_criteria),
            "judge_disposition": self.judge_disposition,
            "cases": [
                {
                    "case_id": case.case_id,
                    "outcome": case.outcome,
                    "reason": case.reason,
                }
                for case in self.case_outcomes
            ],
            "criteria": [
                {
                    "criterion_id": result.criterion_id,
                    "evidence_method": result.evidence_method,
                    "required_gate": result.required_gate,
                    "status": result.status,
                    "detail": result.detail,
                    "case_ids": list(result.case_ids),
                    "rubric_item_ids": list(result.rubric_item_ids),
                }
                for result in self.criteria
            ],
            "convention_report": list(self.convention_report),
            "quality_issues": [
                {
                    "issue_key": issue.issue_key,
                    "relation": issue.relation,
                    "path": issue.path,
                    "severity": issue.severity,
                    "penalized": issue.relation in set(self.penalized_relations),
                    "in_new_code": issue.path in set(self.changed_files),
                }
                for issue in self.issues
            ],
        }


# --------------------------------------------------------------------------------- gate logic


def executable_gate(
    results: Sequence[CriterionResult],
) -> tuple[GateStatus, tuple[str, ...]]:
    """The mandatory executable verdict. This function reads no judge data at all."""
    failing: list[str] = []
    incomplete: list[str] = []
    for result in results:
        if not result.required_gate or result.evidence_method != "executable":
            continue
        if result.status == "fail":
            failing.append(result.criterion_id)
        elif result.status != "pass":
            incomplete.append(result.criterion_id)
    if failing:
        return "fail", tuple(sorted(failing))
    if incomplete:
        return "incomplete", tuple(sorted(incomplete))
    return "pass", ()


def judge_gate_failures(
    criteria: Sequence[CriterionResult],
    means: Mapping[str, str | None],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Required-gate judge criteria: a judgment may fail one, never pass one by override.

    Returns ``(failing, incomplete)`` criterion ids. A missing mean is ``incomplete``; a mean at
    or above the frozen minimum passes; below it fails. Nothing here can flip an executable
    result.
    """
    failing: list[str] = []
    incomplete: list[str] = []
    for result in criteria:
        if not result.required_gate or result.evidence_method != "judge_rubric":
            continue
        scores = [means.get(item_id) for item_id in result.rubric_item_ids]
        if any(score is None for score in scores):
            incomplete.append(result.criterion_id)
            continue
        minimum = result.minimum_gate_score or "0.500000"
        if any(score is not None and score < minimum for score in scores):
            failing.append(result.criterion_id)
    return tuple(sorted(failing)), tuple(sorted(incomplete))


def combine_gate(
    executable_status: GateStatus,
    executable_conditions: tuple[str, ...],
    judge_failing: tuple[str, ...],
    judge_incomplete: tuple[str, ...],
) -> GateVerdict:
    """Combine the mandatory executable verdict with judge-backed gate criteria.

    Structural guarantee (PCB-25-2 DoD): this function accepts *no* judge scores and can only
    ever add failing or incomplete conditions. A failing gate is final; no judgment, adjudication
    or quality score reaches this function and none can rescind it.
    """
    failing = tuple(sorted(set(executable_conditions) | set(judge_failing)))
    incomplete = tuple(sorted(set(judge_incomplete)))
    if executable_status == "fail" or failing:
        return GateVerdict(
            status="fail",
            reasons=("required acceptance condition failed",),
            failing_conditions=failing,
        )
    if executable_status == "incomplete" or incomplete:
        return GateVerdict(
            status="unknown",
            reasons=("grading did not complete",),
            incomplete_conditions=incomplete or executable_conditions,
        )
    return GateVerdict(status="pass", reasons=("all required acceptance conditions passed",))


# ------------------------------------------------------------------------- workspace assembly


def assemble_workspace(
    baseline: Mapping[str, bytes],
    *,
    submission_kind: Literal["source_bundle", "unified_diff"],
    payload: Mapping[str, bytes] | str,
    allowed_paths: Sequence[str],
    protected_paths: Sequence[str],
    max_file_bytes: int = 10 * 1024 * 1024,
) -> WorkspaceResult:
    """Apply one submission to the frozen baseline; every touched path is checked first.

    ``source_bundle`` overlays whole files; ``unified_diff`` applies a patch through the same
    parse/apply engine the solve-session patch tool uses. A submission that touches a protected
    path or a path outside the allowed change set is rejected before anything is computed.
    """
    merged = dict(baseline)
    deletions: set[str] = set()
    if submission_kind == "source_bundle":
        if not isinstance(payload, Mapping):
            raise CandidateRejected("a source bundle submission must be a file mapping")
        for path in sorted(payload):
            _check_path(path, allowed_paths, protected_paths)
            data = payload[path]
            if len(data) > max_file_bytes:
                raise CandidateRejected(f"{path}: file exceeds the submission size limit")
            if data == baseline.get(path):
                continue
            merged[path] = data
    elif submission_kind == "unified_diff":
        if not isinstance(payload, str):
            raise CandidateRejected("a patch submission must be text")
        if len(payload.encode("utf-8")) > MAX_SUBMISSION_BYTES:
            raise CandidateRejected("patch submission exceeds the size limit")
        try:
            file_patches = parse_patch(payload)
        except ToolFailure as error:
            raise CandidateRejected(f"patch rejected: {error}") from error
        for file_patch in file_patches:
            path = file_patch.new if file_patch.new is not None else file_patch.old
            assert path is not None
            _check_path(path, allowed_paths, protected_paths)
            original = merged.get(path, b"")
            try:
                original_text = original.decode("utf-8")
                updated = apply_hunks(original_text, file_patch.hunks, path)
            except (UnicodeDecodeError, ToolFailure) as error:
                raise CandidateRejected(f"{path}: patch does not apply: {error}") from error
            if file_patch.new is None:
                deletions.add(path)
                _ = merged.pop(path, None)
                continue
            encoded = updated.encode("utf-8")
            if len(encoded) > max_file_bytes:
                raise CandidateRejected(f"{path}: result exceeds the file size limit")
            merged[path] = encoded
    else:
        raise CandidateRejected(f"unsupported submission kind {submission_kind!r}")

    changed = {
        path
        for path in set(merged) | set(baseline)
        if merged.get(path) != baseline.get(path)
    }
    return WorkspaceResult(
        files=merged,
        changed_files=tuple(sorted(changed)),
        deleted_files=tuple(sorted(deletions)),
    )


def _check_path(
    path: str, allowed_paths: Sequence[str], protected_paths: Sequence[str]
) -> None:
    if any(
        path == protected or path.startswith(protected.rstrip("/") + "/")
        for protected in protected_paths
    ):
        raise CandidateRejected(f"{path}: protected path may not be changed")
    if not any(
        path == allowed or path.startswith(allowed.rstrip("/") + "/")
        for allowed in allowed_paths
    ):
        raise CandidateRejected(f"{path}: outside the allowed change set")


# ------------------------------------------------------------------------ acceptance execution


def run_hidden_acceptance(
    *,
    runner_bytes: bytes,
    workspace: Mapping[str, bytes],
    expected_case_ids: Sequence[str],
    timeout_seconds: int = ACCEPTANCE_TIMEOUT_SECONDS,
) -> AcceptanceRun:
    """Run the task's hidden acceptance runner against one workspace, bounded and local.

    The runner contract is fixed in ``docs/implementation/repo-task-method.md``: it receives
    ``--workspace`` and ``--report`` and writes ``{"cases": [{"case_id", "outcome", "reason"}]}``.
    A report naming cases outside the frozen inventory is a harness breach (incomplete grading),
    never silently accepted. A timed-out or crashing candidate is a candidate failure only when
    the runner itself is healthy.
    """
    with tempfile.TemporaryDirectory(prefix="pcb-repo-task-") as temp:
        root = Path(temp)
        workspace_dir = root / "workspace"
        for path, data in sorted(workspace.items()):
            target = workspace_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        runner_path = root / "acceptance_runner.py"
        runner_path.write_bytes(runner_bytes)
        report_path = root / "report.json"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(runner_path),
                    "--workspace",
                    str(workspace_dir),
                    "--report",
                    str(report_path),
                ],
                capture_output=True,
                timeout=timeout_seconds,
                env=env,
                cwd=str(workspace_dir),
            )
        except subprocess.TimeoutExpired:
            return AcceptanceRun(
                cases=tuple(
                    CaseOutcome(case_id, "error", "candidate execution timed out")
                    for case_id in expected_case_ids
                ),
                harness_ok=True,
                harness_detail=f"runner timed out after {timeout_seconds}s",
            )
        if not report_path.is_file():
            detail = (completed.stderr or completed.stdout or b"")[-400:].decode(
                "utf-8", errors="replace"
            )
            return AcceptanceRun(
                cases=(),
                harness_ok=False,
                harness_detail=f"acceptance runner produced no report: {detail}",
            )
        try:
            document = json.loads(report_path.read_text(encoding="utf-8"))
            cases = [
                CaseOutcome(
                    case_id=str(entry["case_id"]),
                    outcome=entry["outcome"],
                    reason=str(entry.get("reason", "")),
                )
                for entry in document["cases"]
            ]
        except (ValueError, KeyError, TypeError) as error:
            return AcceptanceRun(
                cases=(),
                harness_ok=False,
                harness_detail=f"acceptance report is invalid: {error}",
            )
    reported = [case.case_id for case in cases]
    if sorted(reported) != sorted(expected_case_ids):
        return AcceptanceRun(
            cases=tuple(cases),
            harness_ok=False,
            harness_detail=(
                "acceptance runner reported a different case inventory than the frozen "
                "contract: "
                + repr(sorted(reported))
            ),
        )
    return AcceptanceRun(cases=tuple(cases), harness_ok=True)


def evaluate_criteria(
    authoring: RepoTaskAuthoring,
    run: AcceptanceRun,
    *,
    means: Mapping[str, str | None] | None,
) -> tuple[CriterionResult, ...]:
    """Bind raw evidence to the frozen criteria. Judge criteria read only judged means."""
    by_case = {case.case_id: case for case in run.cases}
    results: list[CriterionResult] = []
    for criterion in authoring.acceptance:
        if criterion.evidence_method == "executable":
            statuses = [by_case[case_id].outcome for case_id in criterion.case_ids]
            reasons = [
                f"{case_id}: {by_case[case_id].reason}"
                for case_id in criterion.case_ids
                if by_case[case_id].outcome != "pass"
            ]
            if not run.harness_ok:
                status: CriterionStatus = "incomplete"
                detail = run.harness_detail
            elif all(outcome == "pass" for outcome in statuses):
                status = "pass"
                detail = f"{len(statuses)} hidden cases passed"
            elif any(outcome in {"fail", "error"} for outcome in statuses):
                status = "fail"
                detail = "; ".join(reasons)
            else:
                status = "incomplete"
                detail = "; ".join(reasons) or "hidden cases were skipped"
            results.append(
                CriterionResult(
                    criterion_id=criterion.criterion_id,
                    evidence_method="executable",
                    required_gate=criterion.required_gate,
                    status=status,
                    detail=detail,
                    case_ids=criterion.case_ids,
                )
            )
            continue
        scores = [means.get(item_id) for item_id in criterion.rubric_item_ids] if means else []
        minimum = criterion.minimum_gate_score or "0.500000"
        if not means or any(score is None for score in scores):
            status = "incomplete"
            detail = f"not judged (minimum={minimum})"
        elif criterion.required_gate and any(
            score is not None and score < minimum for score in scores
        ):
            status = "fail"
            detail = f"judged below frozen minimum (minimum={minimum})"
        else:
            status = "graded" if not criterion.required_gate else "pass"
            detail = f"judged (minimum={minimum})"
        results.append(
            CriterionResult(
                criterion_id=criterion.criterion_id,
                evidence_method="judge_rubric",
                required_gate=criterion.required_gate,
                status=status,
                detail=detail,
                rubric_item_ids=criterion.rubric_item_ids,
                minimum_gate_score=criterion.minimum_gate_score,
            )
        )
    return tuple(results)


# --------------------------------------------------------------------------- quality evidence


def convention_evidence(
    findings: tuple[ConventionFinding, ...],
    baseline_findings: tuple[ConventionFinding, ...],
    *,
    baseline_files: Mapping[str, bytes],
    candidate_files: Mapping[str, bytes],
    changed_files: Sequence[str],
    penalized_relations: Sequence[str],
) -> tuple[tuple[IssueEvidence, ...], tuple[IssueEvidence, ...], tuple[dict[str, Any], ...]]:
    """Baseline-aware convention evidence: relations first, penalties only where policy says."""
    import polycodebench_evaluation.repo_task_conventions as conventions_module

    tool_digest = sha256_bytes(Path(conventions_module.__file__).read_bytes())
    candidate_digest = digest_files(candidate_files)
    baseline_digest = digest_files(baseline_files)
    candidate_obs = findings_as_observations(
        findings, tool_digest=tool_digest, candidate_digest=candidate_digest
    )
    baseline_obs = findings_as_observations(
        baseline_findings, tool_digest=tool_digest, candidate_digest=baseline_digest
    )
    relations, resolutions = baseline_relations(
        candidate_obs, baseline_obs, candidate_files, baseline_files
    )
    by_key: dict[str, ConventionFinding] = {}
    for finding in findings:
        by_key.setdefault(finding.issue_key, finding)
    issues: list[IssueEvidence] = []
    rows: list[dict[str, Any]] = []
    for key in sorted(by_key):
        finding = by_key[key]
        relation = relations.get(key, "unknown")
        in_new_code = finding.path in set(changed_files)
        penalized = relation in set(penalized_relations) and (
            in_new_code or relation == "introduced"
        )
        issues.append(
            IssueEvidence(
                issue_key=key,
                relation=cast(Relation, relation),
                owner=ScoreDimension.CODE_QUALITY,
                severity=cast("Severity", finding.severity),
                confidence="high",
                path=finding.path,
                start_line=finding.line,
                end_line=finding.line,
                tools=(CONVENTION_ANALYZER_ID,),
                counted_once=True,
                ambiguous=relation == "unknown",
                explanation=f"{finding.rule_id}: {finding.detail}",
            )
        )
        rows.append(
            {
                "issue_key": key,
                "rule_id": finding.rule_id,
                "path": finding.path,
                "symbol": finding.symbol,
                "line": finding.line,
                "relation": relation,
                "penalized": penalized,
                "in_new_code": in_new_code,
                "detail": finding.detail,
            }
        )
    return tuple(issues), tuple(resolutions), tuple(rows)


# ------------------------------------------------------------------------------- the grade run


def grade_repo_task(
    *,
    authoring: RepoTaskAuthoring,
    binding: RepoTaskBinding,
    rubric: JudgeRubric,
    package: TaskPackageManifest,
    package_digest: str,
    baseline_files: Mapping[str, bytes],
    hidden_files: Mapping[str, bytes],
    submission_kind: Literal["source_bundle", "unified_diff"],
    payload: Mapping[str, bytes] | str,
    judgement: JudgementResult | None = None,
    acceptance: Callable[..., AcceptanceRun] | None = None,
) -> RepoTaskGrade:
    """Grade one frozen candidate against one sealed repository task."""
    verify_acceptance_contract(
        authoring,
        rubric=rubric,
        hidden_case_ids=binding.hidden_case_ids,
        frozen_contract_digest=binding.contract_digest,
    )
    workspace = assemble_workspace(
        baseline_files,
        submission_kind=submission_kind,
        payload=payload,
        allowed_paths=authoring.allowed_changes.allowed_paths,
        protected_paths=authoring.allowed_changes.protected_paths,
    )
    if acceptance is None:
        runner_bytes = hidden_files.get("hidden/acceptance_runner.py")
        if runner_bytes is None:
            raise ValueError("the task package declares no hidden acceptance runner")
        run = run_hidden_acceptance(
            runner_bytes=runner_bytes,
            workspace=workspace.files,
            expected_case_ids=binding.hidden_case_ids,
        )
    else:
        run = acceptance(
            workspace=workspace.files, expected_case_ids=binding.hidden_case_ids
        )

    means = judged_means(judgement) if judgement is not None else None
    criteria = evaluate_criteria(authoring, run, means=means)
    executable_status, executable_conditions = executable_gate(criteria)
    judge_failing: tuple[str, ...] = ()
    judge_incomplete: tuple[str, ...] = ()
    if judgement is not None:
        judge_failing, judge_incomplete = judge_gate_failures(criteria, means or {})
    gate = combine_gate(
        executable_status, executable_conditions, judge_failing, judge_incomplete
    )

    if judgement is not None:
        disposition: JudgeDisposition = "used"
    elif executable_status == "fail":
        disposition = "suppressed_after_mandatory_failure"
    else:
        disposition = "not_run"

    analyzer = ConventionAnalyzer(authoring.convention_rules)
    findings = analyzer.analyze(workspace.files)
    baseline_findings = analyzer.analyze(baseline_files)
    issues, resolutions, convention_report = convention_evidence(
        findings,
        baseline_findings,
        baseline_files=baseline_files,
        candidate_files=workspace.files,
        changed_files=workspace.changed_files,
        penalized_relations=authoring.baseline_penalty_relations,
    )

    packet_input = None
    if binding.judge_rubric_item_ids and workspace.changed_files:
        packet_input = judge_packet_input_from(
            language=package.task.primary_language,
            task_statement=authoring.request.text,
            constraints=_constraints(authoring),
            item_ids=binding.judge_rubric_item_ids,
            files={
                path: workspace.files[path]
                for path in sorted(workspace.files)
                if path in set(workspace.changed_files)
            },
            issues=issues,
        )

    return RepoTaskGrade(
        task_id=authoring.task_id,
        task_version=package.task.version,
        package_digest=package_digest,
        contract_digest=binding.contract_digest,
        rubric_digest=binding.rubric_digest,
        submission_kind=submission_kind,
        baseline_digest=digest_files(baseline_files),
        candidate_digest=digest_files(workspace.files),
        changed_files=workspace.changed_files,
        case_outcomes=run.cases,
        criteria=criteria,
        gate=gate,
        judgement=judgement,
        judge_disposition=disposition,
        issues=issues,
        resolutions=resolutions,
        convention_report=convention_report,
        judge_packet_input=packet_input,
        penalized_relations=tuple(authoring.baseline_penalty_relations),
        notes=(
            f"acceptance harness: {'ok' if run.harness_ok else run.harness_detail}",
            f"submission {submission_kind} over {len(workspace.changed_files)} changed file(s)",
        ),
    )


def judge_is_wanted(grade: RepoTaskGrade) -> bool:
    """Production orchestration calls this before spending judge budget.

    A failed mandatory gate already decided the outcome: judging it cannot change acceptance,
    so the packet is not dispatched. (An operator may still request a diagnostic judgement; the
    scorer still forces a zero - see the shared grading regression cases.)
    """
    return grade.gate.status == "pass"


def _constraints(authoring: RepoTaskAuthoring) -> tuple[str, ...]:
    protected = ", ".join(authoring.allowed_changes.protected_paths) or "(none)"
    constraints = [
        f"only these paths may change: {', '.join(authoring.allowed_changes.allowed_paths)}",
        f"these paths are protected: {protected}",
        f"repository conventions apply: {', '.join(authoring.request.conventions)}",
    ]
    return tuple(constraints)


# ------------------------------------------------------------------------------- scoring glue


def code_quality_weight_plan(
    policy: FrozenScoringPolicy,
) -> dict[str, tuple[ScoreDimension, int]]:
    """The frozen judge-item weight plan for the code-quality dimension."""
    return {
        item_id: (ScoreDimension.CODE_QUALITY, weight_bp)
        for item_id, weight_bp in sorted(policy.code_quality.items.items())
    }


def frozen_task_for(
    package: TaskPackageManifest,
    *,
    task_digest: str,
    inventory_digest: str,
) -> FrozenTask:
    """The task slice the scorer validates identity and applicability against."""
    return FrozenTask(
        task_id=package.task.task_id,
        task_version=package.task.version,
        task_digest=task_digest,
        primary_language=package.task.primary_language,
        image_digest=package.runtime.image_digest,
        required_outputs=tuple(package.acceptance.required_outputs),
        protected_paths=tuple(package.acceptance.protected_paths),
        required_test_group_ids=tuple(package.acceptance.required_test_group_ids),
        required_analyzers=(CONVENTION_ANALYZER_ID,),
        applicable_dimensions=(ScoreDimension.CODE_QUALITY,),
        quality={"evidence_owners": dict(package.quality_plan.evidence_owners)},
        inventory={"groups": []},
        inventory_digest=inventory_digest,
    )
