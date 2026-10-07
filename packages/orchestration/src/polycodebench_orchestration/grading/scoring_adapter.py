"""Translate evaluator facts into the scorer's strict, missingness-preserving input contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Confidence, ScoreDimension
from polycodebench_evaluation.evidence import EvaluationEvidence
from polycodebench_plugins_api import FrozenTask, LanguageProfile
from polycodebench_scoring.arithmetic import split_integer
from polycodebench_scoring.manifest import (
    BlockingReason,
    DiagnosticItemView,
    EfficiencyMeasurement,
    EvidenceRef,
    GateVerdict,
    RequiredEvidenceRecord,
    RubricItemEvidence,
    ScoringInvocation,
    SecurityIssueEvidence,
    ValidatedEvidenceManifest,
)
from polycodebench_scoring.ownership import EvidenceOwnership
from polycodebench_scoring.policy import FrozenScoringPolicy


class ScoringAdapterRejected(ValueError):
    """The evaluation evidence cannot be translated without guessing or losing identity."""


def evaluation_to_manifest(
    *,
    task: FrozenTask,
    evidence: EvaluationEvidence,
    policy: FrozenScoringPolicy,
    ownership: EvidenceOwnership,
    profile: LanguageProfile | None,
    profile_source_digest: str | None,
    invocation: ScoringInvocation,
    evidence_artifact_id: str,
    evidence_artifact_digest: str,
) -> ValidatedEvidenceManifest:
    """Build scorer inputs using only frozen values and evaluator-recorded evidence.

    This adapter never estimates performance or judge values. Judge-backed rubric rows that the
    isolated evaluator did not produce remain missing; an unmeasured efficiency lane remains
    incomplete. The scorer consequently returns a nonpublishable result for those candidates.
    """
    if not evidence.evaluation_id or evidence.evaluation_id != evidence.evaluation_id.strip():
        raise ScoringAdapterRejected("evaluation identity is malformed")
    if evidence.report_digest is None or evidence.report_digest != canonical_digest(
        evidence.model_dump(mode="json", exclude={"report_digest"})
    ):
        raise ScoringAdapterRejected("evaluation report digest does not match its content")
    if (
        evidence.task_id != task.task_id
        or evidence.task_version != task.task_version
        or evidence.task_digest != task.task_digest
        or evidence.plugin_id != task.primary_language
    ):
        raise ScoringAdapterRejected("evaluation and frozen task identities disagree")
    if ownership.policy_id != policy.evidence_ownership_policy_id:
        raise ScoringAdapterRejected("scoring policy and ownership policy identities disagree")
    scenario_plan = _task_scenarios(task)
    _validate_scenarios(evidence, scenario_plan)
    if ScoreDimension.IDIOMATIC in task.applicable_dimensions:
        if profile is None or profile.language_id != task.primary_language:
            raise ScoringAdapterRejected("the exact task language profile is required")
        if profile_source_digest != policy.idiomatic.profile_source_digest:
            raise ScoringAdapterRejected(
                "language-profile source digest differs from frozen policy"
            )

    bundle_ref = EvidenceRef(
        ref_type="artifact",
        ref_id=evidence_artifact_id,
        digest=evidence_artifact_digest,
    )

    required_records = _required_evidence(task, evidence, bundle_ref)
    rubric_items = _rubric_items(task, evidence, policy, profile, bundle_ref)
    diagnostic_items = _diagnostic_items(evidence, profile)
    security_issues, unreviewed_security = _security_issues(evidence, bundle_ref)
    blocking = (
        tuple(
            BlockingReason(
                reason_class="adjudication",
                reference=f"review.{index}",
                detail=f"{entry.reason}: {entry.detail}"[:400],
            )
            for index, entry in enumerate(evidence.reviews)
        )
        if evidence.gate == "pass"
        else ()
    )

    gate: GateVerdict
    if evidence.gate == "pass":
        gate = GateVerdict(status="pass", reasons=evidence.gate_reasons)
    elif evidence.gate == "fail":
        gate = GateVerdict(
            status="fail",
            reasons=evidence.gate_reasons,
            failing_conditions=("correctness_gate_failed",),
        )
    else:
        gate = GateVerdict(
            status="unknown",
            reasons=evidence.gate_reasons,
            incomplete_conditions=("correctness_gate_incomplete",),
        )

    efficiency: EfficiencyMeasurement | None = None
    if ScoreDimension.EFFICIENCY in task.applicable_dimensions:
        efficiency = EfficiencyMeasurement(
            status="incomplete",
            reasons=("no measured performance artifact is bound to this evaluation",),
        )

    advisory_digests = {
        analyzer.tool.advisory_snapshot_digest
        for analyzer in evidence.analyzers
        if analyzer.tool.side == "candidate"
        and analyzer.tool.advisory_snapshot_state == "pinned"
        and analyzer.tool.advisory_snapshot_digest is not None
    }
    if len(advisory_digests) > 1:
        raise ScoringAdapterRejected("candidate analyzers disagree on the advisory snapshot")
    advisory_digest = next(iter(advisory_digests), None)
    advisory_applied = bool(advisory_digests)

    notes = [
        "scored input was generated from the evaluator's versioned private evidence bundle",
        "judge-backed quality items remain missing until blinded adjudication runs",
    ]
    if efficiency is not None:
        notes.append("efficiency remains incomplete until the independent performance lane runs")
    if evidence.incomplete:
        notes.extend(f"evaluator incomplete: {reason}" for reason in evidence.incomplete)

    return ValidatedEvidenceManifest(
        evaluation_id=evidence.evaluation_id,
        task_id=task.task_id,
        task_version=task.task_version,
        task_digest=task.task_digest,
        language_id=task.primary_language,
        candidate_digest=evidence.candidate_digest,
        execution_tier=evidence.execution_tier,
        invocation=invocation,
        gate=gate,
        required_evidence=required_records,
        applicability=(ScoreDimension.CORRECTNESS, *task.applicable_dimensions),
        security_issues=security_issues,
        security_unreviewed_relevant=unreviewed_security,
        efficiency=efficiency,
        items=rubric_items,
        blocking=blocking,
        diagnostic_items=diagnostic_items,
        advisory_snapshot_digest=advisory_digest,
        advisory_scans_applied=advisory_applied,
        evidence_refs=(bundle_ref,),
        notes=tuple(notes),
    )


def _required_evidence(
    task: FrozenTask, evidence: EvaluationEvidence, bundle_ref: EvidenceRef
) -> tuple[RequiredEvidenceRecord, ...]:
    records: list[RequiredEvidenceRecord] = []
    candidate_tools = [
        analyzer
        for analyzer in evidence.analyzers
        if analyzer.tool.side == "candidate" and analyzer.tool.required
    ]
    for analyzer_id in task.required_analyzers:
        matches = [
            analyzer
            for analyzer in candidate_tools
            if analyzer.tool.name == analyzer_id or analyzer.tool.analyzer_id == analyzer_id
        ]
        complete = bool(matches) and all(
            analyzer.complete and analyzer.status in {"completed", "completed_with_findings"}
            for analyzer in matches
        )
        status: Literal["complete", "incomplete", "missing"] = (
            "complete" if complete else "incomplete" if matches else "missing"
        )
        references = (
            (bundle_ref.model_copy(update={"locator": f"evidence.json#analyzers/{analyzer_id}"}),)
            if matches
            else ()
        )
        records.append(
            RequiredEvidenceRecord(
                analyzer_id=analyzer_id,
                required=True,
                status=status,
                detail=(
                    "candidate analyzer evidence was complete"
                    if complete
                    else "required analyzer evidence was not produced completely"
                ),
                evidence_refs=references,
            )
        )
    return tuple(records)


def _rubric_items(
    task: FrozenTask,
    evidence: EvaluationEvidence,
    policy: FrozenScoringPolicy,
    profile: LanguageProfile | None,
    bundle_ref: EvidenceRef,
) -> tuple[RubricItemEvidence, ...]:
    applicability = set(task.applicable_dimensions)
    output: list[RubricItemEvidence] = []
    if ScoreDimension.CODE_QUALITY in applicability:
        output.extend(
            RubricItemEvidence(
                dimension=ScoreDimension.CODE_QUALITY,
                item_id=item_id,
                weight_bp=weight_bp,
                status="missing",
                score_bp=None,
                source="judge_votes",
                reasons=("blinded code-quality judge evidence was not produced",),
            )
            for item_id, weight_bp in sorted(policy.code_quality.items.items())
        )
    if ScoreDimension.IDIOMATIC in applicability:
        if profile is None:
            raise ScoringAdapterRejected(
                "idiomatic applicability requires a frozen language profile"
            )
        profile_weights = {item.item_id: item.weight_bp for item in profile.idiom_items}
        weights = split_integer(
            policy.idiomatic.language_rubric_weight_bp,
            profile_weights,
        )
        recorded = {item.item_id: item for item in evidence.profile_items if item.group == "idiom"}
        if len(recorded) != sum(item.group == "idiom" for item in evidence.profile_items):
            raise ScoringAdapterRejected("evaluator returned duplicate idiomatic profile items")
        if set(recorded) - set(profile_weights):
            raise ScoringAdapterRejected("evaluator returned an idiom outside the frozen profile")
        for item_id, weight_bp in sorted(weights.items()):
            item = recorded.get(item_id)
            if item is not None and item.weight_bp != profile_weights[item_id]:
                raise ScoringAdapterRejected("evaluator changed a frozen language-profile weight")
            status = item.status if item is not None else "missing"
            score_bp = item.score_bp if status == "measured" and item is not None else None
            if status == "measured" and score_bp is None:
                raise ScoringAdapterRejected("measured idiomatic profile item has no score")
            output.append(
                RubricItemEvidence(
                    dimension=ScoreDimension.IDIOMATIC,
                    item_id=item_id,
                    weight_bp=weight_bp,
                    status=status,
                    score_bp=score_bp,
                    opportunities=item.opportunities if item is not None else 0,
                    source="tool",
                    evidence_refs=(
                        bundle_ref.model_copy(
                            update={"locator": f"evidence.json#profile_items/{item_id}"}
                        ),
                    )
                    if item is not None and status == "measured"
                    else (),
                    reasons=item.reasons
                    if item is not None
                    else ("language-profile evidence was not recorded",),
                )
            )
        output.extend(
            RubricItemEvidence(
                dimension=ScoreDimension.IDIOMATIC,
                item_id=item_id,
                weight_bp=weight_bp,
                status="missing",
                score_bp=None,
                source="judge_votes",
                reasons=("residual blinded idiomatic judge evidence was not produced",),
            )
            for item_id, weight_bp in sorted(policy.idiomatic.residual_judge_items.items())
        )
    if ScoreDimension.ROBUSTNESS in applicability:
        scenario_plan = _task_scenarios(task)
        quality_weights = {
            scenario_id: weight_bp
            for scenario_id, (weight_bp, hard, _group_id, _repetitions) in scenario_plan.items()
            if not hard
        }
        if not quality_weights:
            raise ScoringAdapterRejected(
                "robustness applicability has no frozen quality-weight scenarios"
            )
        scenarios = split_integer(policy.robustness.scenario_weight_bp, quality_weights)
        quality_scenarios = [item for item in evidence.scenarios if not item.hard_acceptance]
        recorded_scenarios = {item.scenario_id: item for item in quality_scenarios}
        if len(recorded_scenarios) != len(quality_scenarios):
            raise ScoringAdapterRejected("evaluator returned duplicate robustness scenarios")
        if set(recorded_scenarios) - set(scenarios):
            raise ScoringAdapterRejected("evaluator returned a scenario outside the frozen task")
        for scenario_id, weight_bp in sorted(scenarios.items()):
            scenario = recorded_scenarios.get(scenario_id)
            if scenario is None or scenario.status == "incomplete":
                status = "missing"
                score_bp = None
            else:
                status = "measured"
                score_bp = 10_000 if scenario.status == "passed" else 0
            output.append(
                RubricItemEvidence(
                    dimension=ScoreDimension.ROBUSTNESS,
                    item_id=scenario_id,
                    weight_bp=weight_bp,
                    status=status,
                    score_bp=score_bp,
                    source="scenario",
                    hard_acceptance=False,
                    evidence_refs=(
                        bundle_ref.model_copy(
                            update={"locator": f"evidence.json#scenarios/{scenario_id}"}
                        ),
                    )
                    if scenario is not None
                    else (),
                    reasons=(scenario.status,)
                    if scenario is not None
                    else ("scenario was not run",),
                )
            )
        output.extend(
            RubricItemEvidence(
                dimension=ScoreDimension.ROBUSTNESS,
                item_id=item_id,
                weight_bp=weight_bp,
                status="missing",
                score_bp=None,
                source="judge_votes",
                reasons=("residual blinded robustness judge evidence was not produced",),
            )
            for item_id, weight_bp in sorted(policy.robustness.residual_judge_items.items())
        )
    return tuple(output)


def _diagnostic_items(
    evidence: EvaluationEvidence, profile: LanguageProfile | None
) -> tuple[DiagnosticItemView, ...]:
    if profile is None:
        return ()
    recorded = {item.item_id: item for item in evidence.profile_items if item.group == "diagnostic"}
    if len(recorded) != sum(item.group == "diagnostic" for item in evidence.profile_items):
        raise ScoringAdapterRejected("evaluator returned duplicate diagnostic profile items")
    if set(recorded) - {item.item_id for item in profile.diagnostic_items}:
        raise ScoringAdapterRejected("evaluator returned a diagnostic outside the frozen profile")
    views: list[DiagnosticItemView] = []
    for definition in profile.diagnostic_items:
        item = recorded.get(definition.item_id)
        if item is not None and item.weight_bp != definition.weight_bp:
            raise ScoringAdapterRejected("evaluator changed a frozen diagnostic weight")
        status = item.status if item is not None else "missing"
        score_bp = item.score_bp if status == "measured" and item is not None else None
        if status == "measured" and score_bp is None:
            raise ScoringAdapterRejected("measured diagnostic item has no score")
        views.append(
            DiagnosticItemView(
                item_id=definition.item_id,
                language_id=profile.language_id,
                weight_bp=definition.weight_bp,
                status=status,
                score_bp=score_bp,
                opportunities=item.opportunities if item is not None else 0,
                reasons=item.reasons
                if item is not None
                else ("diagnostic evidence was not recorded",),
            )
        )
    return tuple(views)


def _security_issues(
    evidence: EvaluationEvidence, bundle_ref: EvidenceRef
) -> tuple[tuple[SecurityIssueEvidence, ...], tuple[str, ...]]:
    issues: list[SecurityIssueEvidence] = []
    unreviewed: list[str] = []
    for issue in evidence.issues:
        if issue.owner is not ScoreDimension.SECURITY:
            continue
        if issue.severity is None:
            unreviewed.append(issue.issue_key)
            continue
        try:
            confidence = Confidence(issue.confidence or "unreviewed")
        except ValueError:
            raise ScoringAdapterRejected(
                "security issue confidence is not a scoring confidence"
            ) from None
        tools = tuple(sorted({_safe_tool_id(tool) for tool in issue.tools}))
        issues.append(
            SecurityIssueEvidence(
                issue_key=issue.issue_key,
                family="canonical-security-issue",
                penalty_kind=issue.applicability_rule or "canonical_security_penalty",
                severity=issue.severity,
                confidence=confidence,
                relation=issue.relation,
                declared_owner=issue.owner,
                tools=tools,
                evidence_refs=(
                    bundle_ref.model_copy(
                        update={"locator": f"evidence.json#issues/{issue.issue_key}"}
                    ),
                ),
                # Static scanner confidence is not a human adjudication record.
                adjudication=None,
                explanation=issue.explanation,
            )
        )
    return tuple(issues), tuple(sorted(unreviewed))


def _task_scenarios(task: FrozenTask) -> dict[str, tuple[int, bool, str, int]]:
    raw = task.inventory.get("robustness_scenarios", ())
    if not isinstance(raw, list | tuple):
        raise ScoringAdapterRejected("frozen task robustness scenario list is invalid")
    scenarios: dict[str, tuple[int, bool, str, int]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ScoringAdapterRejected("frozen task robustness scenario is invalid")
        scenario_id = item.get("scenario_id")
        weight_bp = item.get("weight_bp")
        hard_acceptance = item.get("hard_acceptance")
        group_id = item.get("group_id")
        repetitions = item.get("repetitions")
        if (
            not isinstance(scenario_id, str)
            or not isinstance(weight_bp, int)
            or isinstance(weight_bp, bool)
            or weight_bp <= 0
            or not isinstance(hard_acceptance, bool)
            or not isinstance(group_id, str)
            or not isinstance(repetitions, int)
            or isinstance(repetitions, bool)
            or repetitions <= 0
            or scenario_id in scenarios
        ):
            raise ScoringAdapterRejected("frozen task scenario identity or weight is invalid")
        scenarios[scenario_id] = (weight_bp, hard_acceptance, group_id, repetitions)
    return scenarios


def _validate_scenarios(
    evidence: EvaluationEvidence,
    plan: dict[str, tuple[int, bool, str, int]],
) -> None:
    recorded = {item.scenario_id: item for item in evidence.scenarios}
    if len(recorded) != len(evidence.scenarios):
        raise ScoringAdapterRejected("evaluator returned duplicate robustness scenarios")
    if set(recorded) - set(plan):
        raise ScoringAdapterRejected("evaluator returned a scenario outside the frozen task")
    for scenario_id, scenario in recorded.items():
        weight_bp, hard_acceptance, group_id, _repetitions = plan[scenario_id]
        expected_effect = "hard_acceptance" if hard_acceptance else "quality_weight_only"
        if (
            scenario.weight_bp != weight_bp
            or scenario.hard_acceptance is not hard_acceptance
            or scenario.gate_effect != expected_effect
            or scenario.group_id != group_id
        ):
            raise ScoringAdapterRejected("evaluator scenario identity differs from frozen task")
        if scenario.status == "passed" and (
            scenario.passed_repetitions != scenario.repetitions
            or scenario.repetitions < plan[scenario_id][3]
            or scenario.credit_bp != weight_bp
        ):
            raise ScoringAdapterRejected("evaluator marked an incomplete scenario as passed")
        if scenario.status != "passed" and scenario.credit_bp != 0:
            raise ScoringAdapterRejected("failed or incomplete scenario has scoring credit")
        if hard_acceptance and scenario.status == "failed" and evidence.gate != "fail":
            raise ScoringAdapterRejected("failed hard-acceptance scenario did not fail the gate")
        if hard_acceptance and scenario.status == "incomplete" and evidence.gate == "pass":
            raise ScoringAdapterRejected("incomplete hard-acceptance scenario passed the gate")


def _safe_tool_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:128] or "unknown-tool"


__all__ = ["ScoringAdapterRejected", "evaluation_to_manifest"]
