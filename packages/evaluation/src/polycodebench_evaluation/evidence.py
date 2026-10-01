"""Normalized, language-neutral evaluation evidence (Technical Spec 12.2-12.5, WP-12).

The evaluator turns supervisor-recorded bytes (test reports, analyzer output, execution records)
into one canonical manifest. Raw tool numbers stay in ``native_metrics``; the stricter
PolyCodeBench gate is recorded separately so one is never silently relabelled as the other.
"""

from __future__ import annotations

from typing import Literal

from polycodebench_core.models import ScoreDimension
from polycodebench_plugins_api import PluginModel
from pydantic import Field

PlanStatus = Literal[
    "completed",
    "completed_with_findings",
    "tool_error",
    "timed_out",
    "output_missing",
]

Relation = Literal[
    "introduced",
    "worsened",
    "unchanged_in_scope",
    "unchanged_out_of_scope",
    "resolved",
    "unknown",
]

_SEVERITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def severity_rank(value: str | None) -> int:
    return _SEVERITY_RANK[value]


class RawArtifactRef(PluginModel):
    kind: Literal["raw_artifact_ref"] = "raw_artifact_ref"
    stage: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=512)
    digest: str
    size_bytes: int = Field(ge=0)
    format: str = Field(min_length=1, max_length=32)


class ToolRecord(PluginModel):
    """What makes one evidence stream comparable (Technical Spec 18.2)."""

    kind: Literal["tool_record"] = "tool_record"
    analyzer_id: str
    side: Literal["candidate", "baseline"]
    name: str
    version: str
    image_digest: str
    lock_digest: str | None
    rule_bundle_digest: str | None
    advisory_snapshot_digest: str | None
    parser_version: str
    plan_id: str
    parser_id: str
    required: bool
    scope: tuple[str, ...]


class AnalyzerEvidence(PluginModel):
    kind: Literal["analyzer_evidence"] = "analyzer_evidence"
    tool: ToolRecord
    status: PlanStatus
    detail: str = ""
    duration_ms: int = Field(ge=0)
    findings: int = Field(ge=0)
    scan_status: str | None
    scan_findings: int | None
    complete: bool
    raw: tuple[RawArtifactRef, ...]


class CaseEvidence(PluginModel):
    kind: Literal["case_evidence"] = "case_evidence"
    group_id: str
    case_id: str
    required: bool
    repetition: int = Field(ge=0)
    outcome: Literal["pass", "fail", "error", "skipped"]
    reason: str = ""
    duration_ms: int = Field(ge=0)
    input_seed: str | None
    expected_outcome_digest: str | None
    stdout_digest: str | None
    stderr_digest: str | None
    execution_identity: str


class ScenarioEvidence(PluginModel):
    kind: Literal["scenario_evidence"] = "scenario_evidence"
    scenario_id: str
    group_id: str
    weight_bp: int = Field(ge=1, le=10_000)
    repetitions: int = Field(ge=1)
    passed_repetitions: int = Field(ge=0)
    hard_acceptance: bool
    status: Literal["passed", "failed", "incomplete"]
    credit_bp: int = Field(ge=0)
    gate_effect: Literal["quality_weight_only", "hard_acceptance"]
    repetition_verdicts: tuple[str, ...]


class PropertyEvidence(PluginModel):
    """Seeds/engine/version facts for property and fuzz cases (Technical Spec 12.2)."""

    kind: Literal["property_evidence"] = "property_evidence"
    engine: str
    engine_version: str | None
    deterministic_policy: str
    examples_pinned: int | None
    case_timeout_seconds: int | None
    suite_timeout_seconds: int | None
    seeds: tuple[str, ...]
    cases: int = Field(ge=0)


class IssueEvidence(PluginModel):
    """One canonical issue: exactly one composite owner, however many tools reported it."""

    kind: Literal["issue_evidence"] = "issue_evidence"
    issue_key: str
    relation: Relation
    owner: ScoreDimension | None
    severity: Literal["critical", "high", "medium", "low"] | None
    confidence: str | None
    path: str | None
    start_line: int | None
    end_line: int | None
    tools: tuple[str, ...]
    counted_once: Literal[True] = True
    ambiguous: bool = False
    applicability_rule: str | None = None
    explanation: str | None = None


class ReviewItem(PluginModel):
    kind: Literal["review_item"] = "review_item"
    issue_key: str
    reason: Literal["ambiguous_baseline_mapping", "unsupported_required_scan", "relation_unknown"]
    detail: str = ""


class ProfileItemEvidence(PluginModel):
    kind: Literal["profile_item_evidence"] = "profile_item_evidence"
    item_id: str
    group: Literal["diagnostic", "idiom"]
    weight_bp: int
    opportunities: int = Field(ge=0)
    unique_violations: int = Field(ge=0)
    status: Literal["measured", "not_applicable", "missing"]
    score_bp: int | None
    reasons: tuple[str, ...]


class GroupVerdictEvidence(PluginModel):
    kind: Literal["group_verdict_evidence"] = "group_verdict_evidence"
    group_id: str
    required: bool
    verdict: Literal["pass", "fail", "incomplete"]
    reasons: tuple[str, ...]


class EvaluationEvidence(PluginModel):
    kind: Literal["evaluation_evidence"] = "evaluation_evidence"
    evaluation_id: str
    task_id: str
    task_version: int = Field(gt=0)
    task_digest: str
    plugin_id: str
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"]
    candidate_digest: str
    baseline_digest: str | None
    overlay_digest: str
    config_digest: str
    inventory_digest: str
    allowed_paths_ok: bool
    disallowed_paths: tuple[str, ...]
    build_verdict: Literal["pass", "fail", "incomplete"]
    build_detail: str
    gate: Literal["pass", "fail", "incomplete"]
    gate_reasons: tuple[str, ...]
    failed_cases: tuple[str, ...]
    group_verdicts: tuple[GroupVerdictEvidence, ...]
    scenarios: tuple[ScenarioEvidence, ...]
    robustness_score_bp: int | None
    cases: tuple[CaseEvidence, ...]
    property_evidence: PropertyEvidence
    analyzers: tuple[AnalyzerEvidence, ...]
    issues: tuple[IssueEvidence, ...]
    resolutions: tuple[IssueEvidence, ...]
    reviews: tuple[ReviewItem, ...]
    profile_items: tuple[ProfileItemEvidence, ...]
    diagnostic_score_bp: int | None
    idiom_score_bp: int | None
    profile_complete: bool
    native_metrics: dict[str, dict[str, int | str | None]]
    incomplete: tuple[str, ...]
    raw_artifacts: tuple[RawArtifactRef, ...]
    execution_note: str
    report_digest: str | None = None


def native_metrics_for(analyzers: list[AnalyzerEvidence]) -> dict[str, dict[str, int | str | None]]:
    """Per-tool native view, kept strictly separate from the PolyCodeBench gate."""
    result: dict[str, dict[str, int | str | None]] = {}
    for entry in analyzers:
        if entry.tool.side != "candidate":
            continue
        result[entry.tool.name] = {
            "side": "candidate",
            "status": entry.status,
            "findings": entry.findings,
            "scan_status": entry.scan_status,
            "complete": "yes" if entry.complete else "no",
        }
    return result
