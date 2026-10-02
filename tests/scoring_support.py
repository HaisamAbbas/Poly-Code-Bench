"""Synthetic scoring fixtures (Technical Spec 14.6).

Everything here is hand-constructed from the documented formulas. No fixture value was copied from
a scorer run: ``S90 E80 Q85 I90 R80`` is the specification's own example, ``time ratio 2 / memory
ratio 1.5`` is the specification's own efficiency example, and the expected composites in
:data:`GOLDEN` are the results of evaluating those formulas by hand in the test assertions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _extra in ("packages/core/src", "packages/scoring/src", "packages/plugins-api/src"):
    _path = str(REPO_ROOT / _extra)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from polycodebench_core.models import Confidence, ScoreDimension  # noqa: E402
from polycodebench_plugins_api import (  # noqa: E402
    ApplicabilityRule,
    FrozenTask,
    LanguageProfile,
    ProfileItem,
    RuleMapping,
)
from polycodebench_scoring.manifest import (  # noqa: E402
    BlockingReason,
    EfficiencyMeasurement,
    EvidenceRef,
    GateVerdict,
    RequiredEvidenceRecord,
    RubricItemEvidence,
    ScoringInvocation,
    SecurityIssueEvidence,
    ValidatedEvidenceManifest,
)

CONFIG_ROOT = REPO_ROOT / "config"
POLICY_PATH = CONFIG_ROOT / "scoring" / "pilot-v1.yaml"
OWNERSHIP_PATH = CONFIG_ROOT / "scoring" / "evidence_ownership.yaml"

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
RUN_ID = "3f1b0c2e-5d4a-4b6c-8e9f-0a1b2c3d4e5f"
CANDIDATE_ID = "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
RECORDED_AT = "2026-09-30T12:00:00Z"
SCORER_DIGEST = "sha256:" + "d" * 64
ADVISORY_DIGEST = "sha256:" + "e" * 64

ALL_QUALITY = (
    ScoreDimension.SECURITY,
    ScoreDimension.EFFICIENCY,
    ScoreDimension.CODE_QUALITY,
    ScoreDimension.IDIOMATIC,
    ScoreDimension.ROBUSTNESS,
)

PYTHON_IDIOMS = {
    "iteration_laziness": 3000,
    "stdlib_api_choice": 3000,
    "data_protocol_modeling": 2500,
    "context_resource_abstraction": 1500,
}
PYTHON_DIAGNOSTICS = {
    "readability_idioms": 2500,
    "type_hints": 1500,
    "stdlib_use": 1500,
    "error_handling": 1500,
    "lint_style": 1000,
    "performance_awareness": 2000,
}

#: Security is measured as a dimension value; the fixture supplies the issues that produce it.
GOLDEN_DIMENSIONS = {
    "security": "90.000000",
    "efficiency": "80.000000",
    "code_quality": "85.000000",
    "idiomatic": "90.000000",
    "robustness": "80.000000",
}
#: Technical Spec 14.6: S90 E80 Q85 I90 R80 -> 89.750000, and 90.772727 with efficiency N/A.
GOLDEN = {
    "full_quality": "89.750000",
    "efficiency_not_applicable": "90.772727",
    "duplicate_high_security": "75.000000",
    "time2_memory15": "61.666667",
    "failed_gate": "0.000000",
    "all_quality_100": "100.000000",
}


def artifact_ref(name: str, digest: str = DIGEST_A) -> EvidenceRef:
    return EvidenceRef(
        ref_type="artifact",
        ref_id=name,
        digest=digest,
        tool_id="bandit",
    )


def python_profile() -> LanguageProfile:
    """A frozen profile shaped exactly like the one the Python plugin publishes."""
    return LanguageProfile(
        language_id="python",
        profile_version="python-profile-v1",
        effective_for_scoring=False,
        diagnostic_items=tuple(
            ProfileItem(item_id=item_id, weight_bp=weight, description=item_id)
            for item_id, weight in sorted(PYTHON_DIAGNOSTICS.items())
        ),
        idiom_items=tuple(
            ProfileItem(item_id=item_id, weight_bp=weight, description=item_id)
            for item_id, weight in sorted(PYTHON_IDIOMS.items())
        ),
        rule_mappings=(
            RuleMapping(
                check_prefix="python.bandit.",
                owner=ScoreDimension.SECURITY,
                applicability="always",
                equivalence_family="security-probe",
            ),
            RuleMapping(
                check_id="python.ruff.e722",
                items=("error_handling",),
                owner=None,
                applicability="item-opportunity",
                equivalence_family="bare-except",
            ),
        ),
        applicability_rules=(
            ApplicabilityRule(
                rule_id="always",
                description="always applicable",
                opportunity_detector="task-declared",
            ),
            ApplicabilityRule(
                rule_id="item-opportunity",
                description="counts only with a frozen task opportunity",
                opportunity_detector="task-declared",
            ),
        ),
        ownership={
            "canonical-security-issue": ScoreDimension.SECURITY,
            "measured-runtime-regression": ScoreDimension.EFFICIENCY,
            "resource-cleanup-failure": ScoreDimension.ROBUSTNESS,
            "language-api-design": ScoreDimension.IDIOMATIC,
            "diagnostic-only": None,
        },
    )


def frozen_task(
    applicable: tuple[ScoreDimension, ...] = ALL_QUALITY,
    *,
    required_analyzers: tuple[str, ...] = ("bandit",),
) -> FrozenTask:
    return FrozenTask(
        task_id="synthetic-scoring-task",
        task_version=1,
        task_digest=DIGEST_A,
        primary_language="python",
        image_digest=DIGEST_B,
        required_outputs=("solution.py",),
        protected_paths=("tests",),
        required_test_group_ids=("behaviour",),
        required_analyzers=required_analyzers,
        applicable_dimensions=applicable,
        inventory_digest=DIGEST_C,
    )


def gate(status: str = "pass", *, failing: tuple[str, ...] = ()) -> GateVerdict:
    if status == "fail":
        return GateVerdict(
            status="fail",
            reasons=("required acceptance condition failed",),
            failing_conditions=failing or ("behaviour.group",),
        )
    if status == "unknown":
        return GateVerdict(
            status="unknown",
            reasons=("grading did not complete",),
            incomplete_conditions=("behaviour.group",),
        )
    return GateVerdict(status="pass", reasons=("all required conditions passed",))


def invocation(**overrides: str) -> ScoringInvocation:
    values = {
        "run_id": RUN_ID,
        "candidate_id": CANDIDATE_ID,
        "recorded_at": RECORDED_AT,
        "scorer_digest": SCORER_DIGEST,
    }
    values.update(overrides)
    return ScoringInvocation(**values)


def analyzer(analyzer_id: str = "bandit", *, status: str = "complete") -> RequiredEvidenceRecord:
    return RequiredEvidenceRecord(
        analyzer_id=analyzer_id,
        required=True,
        status=status,  # type: ignore[arg-type]
        detail="" if status == "complete" else f"analyzer reported {status}",
        evidence_refs=(artifact_ref(f"{analyzer_id}.json"),) if status == "complete" else (),
    )


def measured(
    item_id: str,
    dimension: ScoreDimension,
    weight_bp: int,
    score_bp: int,
    *,
    source: str = "judge_votes",
) -> RubricItemEvidence:
    return RubricItemEvidence(
        item_id=item_id,
        dimension=dimension,
        weight_bp=weight_bp,
        status="measured",
        score_bp=score_bp,
        opportunities=3,
        source=source,  # type: ignore[arg-type]
        evidence_refs=(artifact_ref(f"{item_id}.json"),),
    )


def code_quality_items(value_bp: int) -> list[RubricItemEvidence]:
    return [
        measured(item_id, ScoreDimension.CODE_QUALITY, weight, value_bp)
        for item_id, weight in sorted(CODE_QUALITY_WEIGHTS.items())
    ]


def idiomatic_items(value_bp: int) -> list[RubricItemEvidence]:
    items = [
        measured(item_id, ScoreDimension.IDIOMATIC, weight, value_bp)
        for item_id, weight in sorted(SCALED_IDIOM_WEIGHTS.items())
    ]
    items.extend(
        measured(item_id, ScoreDimension.IDIOMATIC, weight, value_bp)
        for item_id, weight in sorted(RESIDUAL_IDIOM_WEIGHTS.items())
    )
    return items


def robustness_items(value_bp: int) -> list[RubricItemEvidence]:
    items = [
        measured(item_id, ScoreDimension.ROBUSTNESS, weight, value_bp, source="scenario")
        for item_id, weight in sorted(ROBUSTNESS_SCENARIOS.items())
    ]
    items.extend(
        measured(item_id, ScoreDimension.ROBUSTNESS, weight, value_bp)
        for item_id, weight in sorted(RESIDUAL_ROBUSTNESS_WEIGHTS.items())
    )
    return items


def replace(manifest: ValidatedEvidenceManifest, **fields: object) -> ValidatedEvidenceManifest:
    """Rebuild a manifest with changed fields, keeping the strict tuple types intact."""
    return ValidatedEvidenceManifest.model_validate_json(
        json.dumps({**manifest.model_dump(mode="json"), **fields})
    )


CODE_QUALITY_WEIGHTS = {
    "naming_readability": 2000,
    "decomposition": 2500,
    "duplication": 1500,
    "unnecessary_complexity": 1500,
    "repository_style_consistency": 1500,
    "minimal_relevant_scope": 1000,
}
#: Idiomatic splits 8000bp of language rubric plus 2000bp of residual judge items. The language
#: rubric keeps its own relative weights, renormalised into the 8000bp block (Architecture 8.7).
IDIOM_BLOCK_WEIGHT = 8000
RESIDUAL_IDIOM_WEIGHTS = {"idiomatic_design": 1200, "error_handling_clarity": 800}
#: The same split for robustness: 8000bp of scenarios plus one residual judge item.
ROBUSTNESS_BLOCK_WEIGHT = 8000
RESIDUAL_ROBUSTNESS_WEIGHTS = {"residual_robustness_reasoning": 2000}
ROBUSTNESS_SCENARIOS = {"malformed_input": 4000, "io_failure": 2500, "cancellation": 1500}
SCALED_IDIOM_WEIGHTS = {
    item_id: weight * IDIOM_BLOCK_WEIGHT // 10_000 for item_id, weight in PYTHON_IDIOMS.items()
}


def security_issue(
    issue_key: str,
    *,
    severity: str = "high",
    family: str = "canonical-security-issue",
    relation: str = "introduced",
    tools: tuple[str, ...] = ("bandit",),
    owner: ScoreDimension | None = None,
    confidence: str = "confirmed",
    adjudication: str | None = "confirmed",
    penalty_kind: str = "security-penalty",
    digest: str = DIGEST_A,
) -> SecurityIssueEvidence:
    return SecurityIssueEvidence(
        issue_key=issue_key,
        family=family,
        penalty_kind=penalty_kind,
        severity=severity,  # type: ignore[arg-type]
        confidence=Confidence(confidence),
        relation=relation,  # type: ignore[arg-type]
        declared_owner=owner,
        tools=tools,
        evidence_refs=(artifact_ref(f"{issue_key}.json", digest),),
        adjudication=adjudication,  # type: ignore[arg-type]
    )


def efficiency(
    value: str = "100.000000",
    *,
    time_ratio: str | None = None,
    memory_ratio: str | None = None,
    time_censored: str | None = None,
    memory_censored: str | None = None,
    status: str = "complete",
) -> EfficiencyMeasurement:
    """A WP-13 efficiency result. Ratios are optional; when present the scorer re-derives."""
    return EfficiencyMeasurement(
        status=status,  # type: ignore[arg-type]
        value=value,  # type: ignore[arg-type]
        time_ratio=time_ratio,
        memory_ratio=memory_ratio,
        time_censored=time_censored,  # type: ignore[arg-type]
        memory_censored=memory_censored,  # type: ignore[arg-type]
        workloads=3,
        evidence_refs=(artifact_ref("workloads.json"),),
        reasons=() if status in {"complete", "censored"} else (f"lane is {status}",),
    )


def manifest(
    *,
    applicable: tuple[ScoreDimension, ...] = ALL_QUALITY,
    gate_status: str = "pass",
    issues: tuple[SecurityIssueEvidence, ...] = (),
    efficiency_value: EfficiencyMeasurement | None = None,
    items: list[RubricItemEvidence] | None = None,
    required_evidence: tuple[RequiredEvidenceRecord, ...] | None = None,
    blocking: tuple[BlockingReason, ...] = (),
    include_efficiency: bool = True,
    code_quality_bp: int = 8500,
    idiomatic_bp: int = 9000,
    robustness_bp: int = 8000,
    include_items: bool = True,
) -> ValidatedEvidenceManifest:
    """A complete passing manifest whose dimensions can be set independently."""
    if efficiency_value is None and include_efficiency:
        efficiency_value = efficiency()
    if items is None:
        items = [] if include_items else None
        if include_items:
            if ScoreDimension.CODE_QUALITY in applicable:
                items.extend(code_quality_items(code_quality_bp))
            if ScoreDimension.IDIOMATIC in applicable:
                items.extend(idiomatic_items(idiomatic_bp))
            if ScoreDimension.ROBUSTNESS in applicable:
                items.extend(robustness_items(robustness_bp))
    return ValidatedEvidenceManifest(
        evaluation_id=RUN_ID,
        task_id="synthetic-scoring-task",
        task_version=1,
        task_digest=DIGEST_A,
        language_id="python",
        candidate_digest=DIGEST_B,
        execution_tier="local_fixture",
        invocation=invocation(),
        gate=gate(gate_status),
        required_evidence=(analyzer(),) if required_evidence is None else required_evidence,
        applicability=(ScoreDimension.CORRECTNESS, *applicable),
        security_issues=issues,
        efficiency=efficiency_value,
        items=tuple(items or ()),
        blocking=blocking,
    )
