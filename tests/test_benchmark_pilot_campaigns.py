from __future__ import annotations

from typing import Literal
from uuid import UUID

import pytest
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    EntityRef,
    ImmutableArtifactRef,
)
from polycodebench_core.benchmark_pilot_campaigns import (
    MonitorTickCampaignEvidence,
    ReplacementCampaignCandidate,
    SealedTaskCampaignEvidence,
)
from polycodebench_services.benchmark_pilot_campaigns import build_prompt102_campaign_report
from pydantic import ValidationError


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _digest(value: int) -> str:
    return f"sha256:{value:064x}"


def _ref(kind: str, value: int) -> AuditDocumentRef:
    return AuditDocumentRef(document_id=_uuid(value), digest=_digest(value), kind=kind)  # type: ignore[arg-type]


def _artifact(value: int) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_id=_uuid(value),
        digest=_digest(value),
        visibility="private",
        media_type="application/json",
    )


def _replacements(*, rounds: int = 1) -> tuple[ReplacementCampaignCandidate, ...]:
    slugs: tuple[Literal["humaneval", "mbpp", "swe-bench-verified"], ...] = (
        "humaneval",
        "mbpp",
        "swe-bench-verified",
    )
    values: list[ReplacementCampaignCandidate] = []
    for slug_index, slug in enumerate(slugs):
        for item_index in range(4):
            index = slug_index * 4 + item_index
            identity = 1_000 + index * 10
            values.append(
                ReplacementCampaignCandidate(
                    benchmark_slug=slug,
                    competency_slice_id=f"slice-{slug}",
                    benchmark_ref=_ref("benchmark_snapshot", 100 + slug_index),
                    plan_ref=_ref("replacement_plan", 200 + slug_index),
                    validation_ref=_ref("replacement_validation", 300 + index),
                    task_ref=EntityRef(entity_id=_uuid(400 + index), entity_kind="task_version"),
                    source_id=f"source-{slug}",
                    source_family_id=f"family-{slug}-{item_index}",
                    rights_evidence_refs=(_ref("match_evidence", 14_000 + index),),
                    ancestry_refs=(_ref("task_fingerprint", 15_000 + index),),
                    rights_state="approved",
                    validation_state="accepted",
                    lineage_state="verified_independent",
                    author_ref=EntityRef(entity_id=_uuid(identity), entity_kind="reviewer"),
                    checker_refs=(
                        EntityRef(entity_id=_uuid(identity + 1), entity_kind="reviewer"),
                    ),
                    reviewer_ref=EntityRef(entity_id=_uuid(identity + 2), entity_kind="reviewer"),
                    authoring_mode="genuine_human",
                    authoring_evidence_ref=_artifact(2_000 + index * 3),
                    checker_evidence_ref=_artifact(2_001 + index * 3),
                    usage_evidence_ref=_artifact(2_002 + index * 3),
                    plan_frozen_at="2026-10-09T08:00:00Z",
                    work_started_at="2026-10-09T09:00:00Z",
                    rounds_spent=rounds,
                    cost_micro_usd_spent=100,
                    wall_seconds_spent=60,
                    plan_max_drafts=4,
                    plan_max_rounds=8,
                    plan_max_cost_micro_usd=5_000,
                    plan_max_wall_seconds=3_600,
                    source_quota_maximum=4,
                )
            )
    return tuple(values)


def _sealed_tasks(*, production: bool = True) -> tuple[SealedTaskCampaignEvidence, ...]:
    values: list[SealedTaskCampaignEvidence] = []
    for index in range(6):
        values.append(
            SealedTaskCampaignEvidence(
                task_ref=EntityRef(entity_id=_uuid(5_000 + index), entity_kind="task_version"),
                manifest_ref=_ref("sealed_manifest", 6_000 + index),
                screening_event_ref=_ref("seal_access_event", 7_000 + index * 2),
                disclosure_event_ref=_ref("seal_access_event", 7_001 + index * 2),
                commitment_digest=_digest(8_000 + index),
                owner_author_ref=EntityRef(
                    entity_id=_uuid(9_000 + index * 2), entity_kind="reviewer"
                ),
                independent_reviewer_ref=EntityRef(
                    entity_id=_uuid(9_001 + index * 2), entity_kind="reviewer"
                ),
                lineage_state="verified_independent",
                local_screening_result="passed",
                disclosure_result="authorized",
                source_evidence_mode="none" if production else "fixture",
                model_evidence_mode="none" if production else "development_mock",
                key_provider_mode="approved_production" if production else "development",
                timestamp_provider_mode="trusted_authority" if production else "development",
                timestamp_receipt_ref=_artifact(10_000 + index) if production else None,
                lineage_evidence_ref=_artifact(11_000 + index),
                screening_evidence_ref=_artifact(12_000 + index),
            )
        )
    return tuple(values)


def _monitor_tick() -> MonitorTickCampaignEvidence:
    return MonitorTickCampaignEvidence(
        policy_ref=_ref("monitor_policy", 13_000),
        previous_source_ref=_ref("corpus_snapshot", 13_001),
        changed_source_ref=_ref("corpus_snapshot", 13_002),
        coverage_ref=_ref("coverage_manifest", 13_003),
        query_usage_ref=_artifact(13_004),
        alert_history_ref=_artifact(13_005),
        alert_refs=(),
        slot_key="2026-10-09",
        policy_frozen_at="2026-10-09T08:00:00Z",
        search_started_at="2026-10-09T09:00:00Z",
        rescan_state="complete",
        query_units_reserved=100,
        query_units_used=8,
        cost_micro_usd_reserved=2_000,
        cost_micro_usd_used=100,
    )


class _EvidenceResolver:
    def __init__(self, *, reject_usage: bool = False) -> None:
        self.reject_usage = reject_usage

    def verify_replacement_candidate(self, candidate: ReplacementCampaignCandidate) -> bool:
        return not self.reject_usage and candidate.validation_state == "accepted"

    def verify_sealed_task(self, task: SealedTaskCampaignEvidence) -> bool:
        return not self.reject_usage and task.disclosure_result == "authorized"

    def verify_monitor_tick(self, tick: MonitorTickCampaignEvidence) -> bool:
        return not self.reject_usage and tick.rescan_state == "complete"


def test_empty_campaign_is_explicitly_blocked_without_dispatch() -> None:
    report = build_prompt102_campaign_report((), (), None, evidence_resolver=None)

    assert report.status == "blocked"
    assert report.observed_replacements == 0
    assert report.observed_sealed_tasks == 0
    assert report.monitor_ticks == 0
    assert report.this_readiness_check_dispatched_work is False
    assert "replacement_campaign_requires_12_candidates" in report.blockers
    assert "sealed_campaign_requires_six_independent_tasks" in report.blockers
    assert "changed_source_monitor_tick_missing" in report.blockers


def test_complete_campaign_reconciles_all_three_slices_seals_and_actual_usage() -> None:
    report = build_prompt102_campaign_report(
        _replacements(),
        _sealed_tasks(),
        _monitor_tick(),
        evidence_resolver=_EvidenceResolver(),
    )

    assert report.status == "ready"
    assert report.observed_replacements == report.accepted_replacements == 12
    assert {item.observed for item in report.replacements_by_benchmark} == {4}
    assert {item.accepted for item in report.replacements_by_benchmark} == {4}
    assert {item.competency_slice_id for item in report.replacements_by_benchmark} == {
        "slice-humaneval",
        "slice-mbpp",
        "slice-swe-bench-verified",
    }
    assert report.production_mode_sealed_tasks == 6
    assert report.locally_screened_tasks == report.authorized_disclosures == 6
    assert sum(item.verified_count for item in report.mode_counts if item.axis == "authoring") == 12
    assert (
        sum(item.verified_count for item in report.mode_counts if item.axis == "timestamp_provider")
        == 6
    )
    assert report.changed_source_rescans == report.monitor_ticks == 1
    assert report.query_units_reported == report.query_units_verified == 8
    assert report.cost_micro_usd_reported == report.cost_micro_usd_verified == 100
    assert report.monitor_alerts_observed == 0
    assert report.blockers == ()


def test_missing_resolver_keeps_claimed_campaign_data_unverified() -> None:
    report = build_prompt102_campaign_report(
        _replacements(), _sealed_tasks(), _monitor_tick(), evidence_resolver=None
    )

    assert report.status == "partial"
    assert report.accepted_replacements == 0
    assert report.verified_sealed_tasks == 0
    assert report.production_mode_sealed_tasks == 0
    assert report.monitor_evidence_verified is False
    assert report.query_units_reported == 8
    assert report.query_units_verified == 0
    assert report.cost_micro_usd_reported == 100
    assert report.cost_micro_usd_verified == 0
    assert all(item.verified_count == 0 for item in report.mode_counts)
    assert "trusted_campaign_evidence_resolver_unavailable" in report.blockers


def test_replacement_budget_overrun_is_retained_as_a_partial_campaign() -> None:
    report = build_prompt102_campaign_report(
        _replacements(rounds=3), (), None, evidence_resolver=_EvidenceResolver()
    )

    assert report.status == "partial"
    assert report.observed_replacements == 12
    assert report.accepted_replacements == 12
    assert report.replacement_budgets_reconciled is False
    assert "replacement_author_checker_budget_not_reconciled" in report.blockers


def test_wrong_per_benchmark_candidate_distribution_is_reported_not_discarded() -> None:
    candidates = list(_replacements())
    candidate = candidates[4].model_copy(
        update={
            "benchmark_slug": "humaneval",
            "competency_slice_id": "slice-humaneval",
        }
    )
    candidates[4] = candidate

    report = build_prompt102_campaign_report(
        tuple(candidates), (), None, evidence_resolver=_EvidenceResolver()
    )

    counts = {item.benchmark_slug: item.observed for item in report.replacements_by_benchmark}
    assert counts == {"humaneval": 5, "mbpp": 3, "swe-bench-verified": 4}
    assert report.status == "partial"
    assert "replacement_slice_requires_four:humaneval" in report.blockers
    assert "replacement_slice_requires_four:mbpp" in report.blockers


def test_development_seals_do_not_satisfy_production_campaign_gate() -> None:
    report = build_prompt102_campaign_report(
        (), _sealed_tasks(production=False), None, evidence_resolver=_EvidenceResolver()
    )

    assert report.status == "partial"
    assert report.observed_sealed_tasks == 6
    assert report.locally_screened_tasks == report.authorized_disclosures == 6
    assert report.verified_sealed_tasks == 6
    assert report.production_mode_sealed_tasks == 0
    assert "six_production_key_and_timestamp_provider_receipts_unavailable" in report.blockers


def test_duplicate_task_or_manifest_evidence_fails_closed() -> None:
    replacements = list(_replacements())
    replacements[1] = replacements[1].model_copy(update={"task_ref": replacements[0].task_ref})
    with pytest.raises(ValueError, match="replacement campaign tasks must be unique"):
        build_prompt102_campaign_report(
            tuple(replacements), (), None, evidence_resolver=_EvidenceResolver()
        )

    sealed = list(_sealed_tasks())
    sealed[1] = sealed[1].model_copy(update={"manifest_ref": sealed[0].manifest_ref})
    with pytest.raises(ValueError, match="distinct manifest"):
        build_prompt102_campaign_report((), tuple(sealed), None, evidence_resolver=None)


def test_sealed_task_requires_private_trusted_timestamp_receipt() -> None:
    task = _sealed_tasks()[0].model_dump()
    task["timestamp_receipt_ref"] = None

    with pytest.raises(ValidationError, match="trusted timestamp mode"):
        SealedTaskCampaignEvidence.model_validate(task)


def test_monitor_actual_usage_cannot_exceed_reserved_units_or_cost() -> None:
    tick = _monitor_tick().model_dump()
    tick["query_units_used"] = 101

    with pytest.raises(ValidationError, match="exceeds its reservation"):
        MonitorTickCampaignEvidence.model_validate(tick)


def test_campaign_chronology_compares_fractional_utc_timestamps_as_instants() -> None:
    candidate = _replacements()[0].model_dump()
    candidate["plan_frozen_at"] = "2026-10-09T09:00:00.900Z"

    with pytest.raises(ValidationError, match="before candidate work"):
        ReplacementCampaignCandidate.model_validate(candidate)
