from __future__ import annotations

from typing import Literal
from uuid import UUID

import pytest
from polycodebench_core.benchmark_audit_documents import AuditDocumentRef, ImmutableArtifactRef
from polycodebench_core.benchmark_imports import freeze_sample
from polycodebench_core.benchmark_pilot import (
    PILOT_BENCHMARKS,
    PILOT_REQUIRED_SOURCE_GROUPS,
    PilotCalibrationHeldOutPair,
    PilotCalibrationObservation,
    PilotCalibrationPlan,
    PilotCalibrationSourceScope,
    PilotDatasetReadiness,
    PilotRelationLabel,
    PilotSourceReadiness,
)
from polycodebench_services.benchmark_pilot import (
    build_detector_calibration_report,
    build_pilot_preflight,
    unmeasured_detector_calibration_report,
)
from polycodebench_services.match_verification import match_relation_rubric_digest
from pydantic import ValidationError


def _digest(value: int) -> str:
    return f"sha256:{value:064x}"


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _dataset_inputs() -> tuple[PilotDatasetReadiness, ...]:
    samples: dict[str, tuple[Literal["official", "original", "sanitized", "verified"], str]] = {
        "humaneval": ("official", "test/"),
        "mbpp": ("sanitized", "11"),
        "swe-bench-verified": ("verified", "django/django__"),
    }
    items: list[PilotDatasetReadiness] = []
    for index, (slug, revision, split) in enumerate(PILOT_BENCHMARKS, start=1):
        variant, prefix = samples[slug]
        if slug == "mbpp":
            eligible = tuple(str(task_id) for task_id in range(11, 511))
        elif slug == "humaneval":
            eligible = tuple(f"{prefix}{task_id}" for task_id in range(164))
        else:
            eligible = tuple(f"{prefix}{task_id}" for task_id in range(120))
        seed = "20261009"
        selected, membership_digest = freeze_sample(
            benchmark_slug=slug,
            revision=revision,
            split=split,
            variant=variant,
            seed=seed,
            eligible_item_ids=eligible,
        )
        items.append(
            PilotDatasetReadiness(
                benchmark_slug=slug,
                revision=revision,
                split=split,
                variant=variant,
                sample_seed=seed,
                source_digest=_digest(100 + index),
                source_artifact_ref=ImmutableArtifactRef(
                    artifact_id=_uuid(600 + index),
                    digest=_digest(100 + index),
                    visibility="restricted",
                    media_type="application/json",
                ),
                rights_evidence_digest=_digest(200 + index),
                eligible_ids=eligible,
                selected_ids=selected,
                membership_digest=membership_digest,
                plan_frozen_at="2026-10-09T09:00:00Z",
                search_started_at=None,
            )
        )
    return tuple(items)


def _source_inputs() -> tuple[PilotSourceReadiness, ...]:
    return tuple(
        PilotSourceReadiness(
            source_group=group,
            snapshot_digest=_digest(300 + index),
            scope_evidence_digest=_digest(400 + index),
            max_requests=2_000,
            max_response_bytes=5_242_880,
            max_total_bytes=178_956_970,
            plan_frozen_at="2026-10-09T09:00:00Z",
            search_started_at=None,
        )
        for index, group in enumerate(PILOT_REQUIRED_SOURCE_GROUPS, start=1)
    )


class _PilotEvidenceResolver:
    def verify_artifact(self, artifact: ImmutableArtifactRef) -> bool:
        return artifact.visibility in {"restricted", "private"}

    def verify_benchmark_population(
        self,
        benchmark_slug: str,
        revision: str,
        split: str,
        variant: str,
        source_artifact: ImmutableArtifactRef,
        eligible_ids: tuple[str, ...],
    ) -> bool:
        expected: tuple[str, ...]
        if benchmark_slug == "humaneval":
            expected = tuple(f"test/{task_id}" for task_id in range(164))
        elif benchmark_slug == "mbpp":
            expected = tuple(str(task_id) for task_id in range(11, 511))
        else:
            expected = tuple(f"django/django__{task_id}" for task_id in range(120))
        return (
            (benchmark_slug, revision, split)
            in {(slug, rev, split_name) for slug, rev, split_name in PILOT_BENCHMARKS}
            and variant in {"official", "original", "sanitized", "verified"}
            and source_artifact.digest.startswith("sha256:")
            and eligible_ids == expected
        )

    def verify_benchmark_rights(self, benchmark_slug: str, evidence_digest: str) -> bool:
        return benchmark_slug in {
            slug for slug, _, _ in PILOT_BENCHMARKS
        } and evidence_digest.startswith("sha256:")

    def verify_source_scope(
        self, source_group: str, snapshot_digest: str, scope_evidence_digest: str
    ) -> bool:
        return (
            source_group in PILOT_REQUIRED_SOURCE_GROUPS
            and snapshot_digest != scope_evidence_digest
        )

    def verify_live_connector_conformance(
        self, source_group: str, snapshot_digest: str, scope_evidence_digest: str
    ) -> bool:
        return self.verify_source_scope(source_group, snapshot_digest, scope_evidence_digest)

    def verify_review_plan(self, plan_digest: str, frozen_at: str) -> bool:
        return plan_digest.startswith("sha256:") and frozen_at.startswith("2026-")

    def verify_reviewer_roster(self, roster_digest: str) -> bool:
        return roster_digest.startswith("sha256:")


def _plan() -> PilotCalibrationPlan:
    return PilotCalibrationPlan(
        plan_id=_uuid(1),
        frozen_at="2026-10-09T10:00:00Z",
        sample_plan_digest=_digest(500),
        detector_config_digest=_digest(501),
        relation_rubric_digest=match_relation_rubric_digest(),
        approved_source_scopes=tuple(
            PilotCalibrationSourceScope(
                source_group=group,
                source_snapshot_ref=AuditDocumentRef(
                    document_id=_uuid(100 + index),
                    digest=_digest(600 + index),
                    kind="corpus_snapshot",
                ),
            )
            for index, group in enumerate(PILOT_REQUIRED_SOURCE_GROUPS)
        ),
        held_out_pairs=tuple(
            PilotCalibrationHeldOutPair(
                pair_digest=_digest(1_000 + index),
                family_digest=_digest(2_000 + index % 30),
            )
            for index in range(100)
        ),
        analysis_seed="20261009",
        bootstrap_replicates=2_000,
        minimum_precision_lower_95="0.950000",
        minimum_recall_lower_95="0.950000",
        maximum_false_positive_rate_upper_95="0.050000",
        maximum_unknown_fraction="0.000000",
        plan_author_subject="plan-owner",
        detector_operator_subject="detector-operator",
    )


def _observations(plan: PilotCalibrationPlan) -> tuple[PilotCalibrationObservation, ...]:
    observations: list[PilotCalibrationObservation] = []
    groups = PILOT_REQUIRED_SOURCE_GROUPS
    for index, pair_digest in enumerate(plan.held_out_pair_digests):
        is_boilerplate = index == 98
        is_self_source = index == 99
        truth: PilotRelationLabel = "shared_concept" if index >= 50 else "semantic_duplicate"
        control_kind: Literal["ordinary", "boilerplate", "self_source"] = "ordinary"
        prediction: Literal["positive", "negative"] = (
            "negative" if truth == "shared_concept" else "positive"
        )
        if is_boilerplate:
            control_kind = "boilerplate"
            truth = "boilerplate"
            prediction = "negative"
        elif is_self_source:
            control_kind = "self_source"
            truth = "self_source"
            prediction = "negative"
        source_scope = plan.approved_source_scopes[index % 3]
        observations.append(
            PilotCalibrationObservation(
                pair_digest=pair_digest,
                family_digest=plan.held_out_family_digests[index % 30],
                evidence_ref=AuditDocumentRef(
                    document_id=_uuid(3_000 + index),
                    digest=pair_digest,
                    kind="match_evidence",
                ),
                source_snapshot_ref=source_scope.source_snapshot_ref,
                source_group=groups[index % 3],
                language="python",
                modality="code",
                control_kind=control_kind,
                detector_config_digest=plan.detector_config_digest,
                candidate_prediction="positive" if control_kind != "ordinary" else prediction,
                substantive_prediction=prediction,
                predicted_at="2026-10-09T10:01:00Z",
                reviewer_one_subject="reviewer-one",
                reviewer_one_vote=truth,
                reviewer_one_at="2026-10-09T10:02:00Z",
                reviewer_one_evidence=ImmutableArtifactRef(
                    artifact_id=_uuid(4_000 + index * 3),
                    digest=_digest(5_000 + index * 3),
                    visibility="private",
                    media_type="application/json",
                ),
                reviewer_two_subject="reviewer-two",
                reviewer_two_vote=truth,
                reviewer_two_at="2026-10-09T10:03:00Z",
                reviewer_two_evidence=ImmutableArtifactRef(
                    artifact_id=_uuid(4_001 + index * 3),
                    digest=_digest(5_001 + index * 3),
                    visibility="private",
                    media_type="application/json",
                ),
                adjudicator_subject=None,
                adjudicator_vote=None,
                adjudicated_at=None,
                adjudication_evidence=None,
            )
        )
    return tuple(observations)


class _QualifiedReviewers:
    def is_qualified(self, subject: str, at: str) -> bool:
        return subject in {"reviewer-one", "reviewer-two"} and at.startswith("2026-10-09")


class _CalibrationEvidenceResolver:
    def verify_calibration_plan(self, plan: PilotCalibrationPlan) -> bool:
        return plan.frozen_at == "2026-10-09T10:00:00Z" and plan.digest == _plan().digest

    def verify_calibration_source_scope(self, scope: PilotCalibrationSourceScope) -> bool:
        return scope.source_snapshot_ref.document_id in {_uuid(100), _uuid(101), _uuid(102)}

    def verify_calibration_observation(self, observation: PilotCalibrationObservation) -> bool:
        return (
            observation.evidence_ref.digest == observation.pair_digest
            and observation.reviewer_one_evidence.artifact_id
            != observation.reviewer_two_evidence.artifact_id
        )


def test_pilot_preflight_blocks_without_real_membership_rights_and_source_snapshots() -> None:
    report = build_pilot_preflight(
        (),
        (),
        review_plan_digest=None,
        review_plan_frozen_at=None,
        review_search_started_at=None,
        reviewer_roster_digest=None,
        evidence_resolver=None,
    )

    assert report.status == "blocked"
    assert report.datasets_with_100_ids == 0
    assert report.datasets_with_verified_population == 0
    assert report.approved_source_snapshots == 0
    assert report.this_preflight_dispatched_queries is False
    assert "missing_exact_import_plan:humaneval" in report.missing_inputs
    assert "qualified_independent_reviewer_roster_unavailable" in report.missing_inputs


def test_pilot_preflight_accepts_only_frozen_exact_300_item_bounded_plan() -> None:
    report = build_pilot_preflight(
        _dataset_inputs(),
        _source_inputs(),
        review_plan_digest=_digest(700),
        review_plan_frozen_at="2026-10-09T09:00:00Z",
        review_search_started_at=None,
        reviewer_roster_digest=_digest(701),
        evidence_resolver=_PilotEvidenceResolver(),
    )

    assert report.status == "ready"
    assert report.expected_tasks == 300
    assert report.datasets_with_verified_population == 3
    assert report.maximum_candidate_slots == 30_000
    assert report.max_candidates_per_task == 100
    assert report.max_candidates_per_source == 20
    assert report.missing_inputs == ()


def test_pilot_preflight_detects_membership_tampering_and_unpinned_split_ids() -> None:
    datasets = list(_dataset_inputs())
    datasets[0] = datasets[0].model_copy(
        update={"membership_digest": _digest(999), "selected_ids": ("../../secret",) * 100}
    )
    report = build_pilot_preflight(
        tuple(datasets),
        (),
        review_plan_digest=None,
        review_plan_frozen_at=None,
        review_search_started_at=None,
        reviewer_roster_digest=None,
        evidence_resolver=None,
    )

    assert report.status == "blocked"
    assert "sample_ids_do_not_match_frozen_split:humaneval" in report.missing_inputs
    assert "seeded_sample_membership_mismatch:humaneval" in report.missing_inputs


def test_pilot_preflight_requires_population_to_match_the_pinned_source_artifact() -> None:
    datasets = list(_dataset_inputs())
    humaneval = datasets[0]
    changed_population = (*humaneval.eligible_ids[:-1], "test/999")
    selected, membership_digest = freeze_sample(
        benchmark_slug="humaneval",
        revision=humaneval.revision or "",
        split=humaneval.split or "",
        variant=humaneval.variant or "official",
        seed=humaneval.sample_seed or "0",
        eligible_item_ids=changed_population,
    )
    datasets[0] = humaneval.model_copy(
        update={
            "eligible_ids": changed_population,
            "selected_ids": selected,
            "membership_digest": membership_digest,
        }
    )

    report = build_pilot_preflight(
        tuple(datasets),
        _source_inputs(),
        review_plan_digest=_digest(700),
        review_plan_frozen_at="2026-10-09T09:00:00Z",
        review_search_started_at=None,
        reviewer_roster_digest=_digest(701),
        evidence_resolver=_PilotEvidenceResolver(),
    )

    assert report.status == "blocked"
    assert report.datasets_with_100_ids == 3
    assert report.datasets_with_verified_population == 2
    assert "eligible_population_not_verified_from_pinned_bytes:humaneval" in report.missing_inputs


def test_pilot_preflight_requires_every_plan_to_precede_the_first_search() -> None:
    datasets = list(_dataset_inputs())
    datasets[2] = datasets[2].model_copy(update={"plan_frozen_at": "2026-10-09T11:00:00Z"})
    sources = list(_source_inputs())
    sources[0] = sources[0].model_copy(update={"search_started_at": "2026-10-09T10:30:00Z"})

    report = build_pilot_preflight(
        tuple(datasets),
        tuple(sources),
        review_plan_digest=_digest(700),
        review_plan_frozen_at="2026-10-09T09:00:00Z",
        review_search_started_at=None,
        reviewer_roster_digest=_digest(701),
        evidence_resolver=_PilotEvidenceResolver(),
    )

    assert report.status == "blocked"
    assert (
        "all_benchmark_source_and_review_scopes_must_precede_first_search" in report.missing_inputs
    )


def test_cluster_bootstrap_reports_metrics_and_never_enables_automatic_admission() -> None:
    plan = _plan()
    observations = _observations(plan)
    first = build_detector_calibration_report(
        plan,
        observations,
        reviewer_resolver=_QualifiedReviewers(),
        evidence_resolver=_CalibrationEvidenceResolver(),
    )
    second = build_detector_calibration_report(
        plan,
        observations,
        reviewer_resolver=_QualifiedReviewers(),
        evidence_resolver=_CalibrationEvidenceResolver(),
    )

    assert first == second
    assert first.measurement_state == "held_out_measured"
    assert first.gate_status == "thresholds_met"
    assert first.labeled_pairs == 100
    assert first.labeled_families == 30
    assert first.source_snapshots_verified is True
    assert first.calibration_plan_verified is True
    assert first.independent_label_evidence_verified is True
    assert first.true_positive == 50
    assert first.true_negative == 50
    assert first.precision.lower_95 == "1.000000"
    assert first.recall.lower_95 == "1.000000"
    assert first.false_positive_rate.upper_95 == "0.000000"
    assert first.calibration_plan_digest == plan.digest
    assert first.detector_config_digest == plan.detector_config_digest
    assert plan.interval_method == "family_cluster_percentile_sha256_v1"
    assert plan.maximum_bootstrap_family_draws == 20_000_000
    assert {item.axis for item in first.strata} == {
        "relation",
        "language",
        "modality",
        "source",
    }
    assert all(
        item.precision.interval_method == "family_cluster_percentile_sha256_v1"
        for item in first.strata
    )
    assert first.boilerplate_controls == first.boilerplate_candidate_hits == 1
    assert first.boilerplate_rejected == 1
    assert first.self_source_controls == first.self_source_candidate_hits == 1
    assert first.self_source_rejected == 1
    assert first.semantic_auto_admission_enabled is False


def test_calibration_labels_need_an_independent_adjudicator_when_reviewers_disagree() -> None:
    plan = _plan()
    case = _observations(plan)[0].model_dump()
    case["reviewer_two_vote"] = "shared_concept"

    with pytest.raises(ValidationError, match="third-party adjudication"):
        PilotCalibrationObservation.model_validate(case)


def test_calibration_rejects_unplanned_source_data_and_operator_self_review() -> None:
    plan = _plan()
    observations = list(_observations(plan))
    observations[0] = observations[0].model_copy(
        update={
            "source_snapshot_ref": AuditDocumentRef(
                document_id=_uuid(999), digest=_digest(999), kind="corpus_snapshot"
            )
        }
    )
    with pytest.raises(ValueError, match="outside the frozen source scope"):
        build_detector_calibration_report(
            plan,
            tuple(observations),
            reviewer_resolver=_QualifiedReviewers(),
            evidence_resolver=_CalibrationEvidenceResolver(),
        )

    observations = list(_observations(plan))
    observations[0] = observations[0].model_copy(
        update={"family_digest": plan.held_out_family_digests[1]}
    )
    with pytest.raises(ValueError, match="family assignment changed"):
        build_detector_calibration_report(
            plan,
            tuple(observations),
            reviewer_resolver=_QualifiedReviewers(),
            evidence_resolver=_CalibrationEvidenceResolver(),
        )

    observations = list(_observations(plan))
    observations[0] = observations[0].model_copy(
        update={"reviewer_one_subject": "detector-operator"}
    )
    with pytest.raises(ValueError, match="cannot label their own pilot"):
        build_detector_calibration_report(
            plan,
            tuple(observations),
            reviewer_resolver=_QualifiedReviewers(),
            evidence_resolver=_CalibrationEvidenceResolver(),
        )


def test_calibration_stays_blocked_without_verified_reviewer_roster() -> None:
    report = build_detector_calibration_report(
        _plan(),
        _observations(_plan()),
        reviewer_resolver=None,
        evidence_resolver=_CalibrationEvidenceResolver(),
    )

    assert report.gate_status == "blocked"
    assert report.reviewer_qualification_verified is False
    assert "qualified_independent_reviewer_roster_unavailable_or_failed" in report.blockers
    assert report.semantic_auto_admission_enabled is False


def test_calibration_without_trusted_evidence_resolver_is_descriptive_and_blocked() -> None:
    plan = _plan()
    report = build_detector_calibration_report(
        plan,
        _observations(plan),
        reviewer_resolver=_QualifiedReviewers(),
        evidence_resolver=None,
    )

    assert report.measurement_state == "descriptive"
    assert report.gate_status == "blocked"
    assert report.source_snapshots_verified is False
    assert report.independent_label_evidence_verified is False
    assert "approved_calibration_snapshots_unverified" in report.blockers
    assert "independent_label_artifacts_unverified" in report.blockers


def test_calibration_rejects_out_of_range_probability_thresholds() -> None:
    values = _plan().model_dump()
    values["maximum_unknown_fraction"] = "1.000001"

    with pytest.raises(ValidationError, match=r"within \[0,1\]"):
        PilotCalibrationPlan.model_validate(values)


def test_calibration_plan_rejects_unbounded_bootstrap_work() -> None:
    values = _plan().model_dump()
    values["bootstrap_replicates"] = 20_000
    values["held_out_pairs"] = tuple(
        {"pair_digest": _digest(10_000 + index), "family_digest": _digest(20_000 + index)}
        for index in range(2_000)
    )

    with pytest.raises(ValidationError, match="work ceiling"):
        PilotCalibrationPlan.model_validate(values)


def test_calibration_label_artifacts_cannot_be_public() -> None:
    values = _observations(_plan())[0].model_dump()
    values["reviewer_one_evidence"]["visibility"] = "public"

    with pytest.raises(ValidationError, match="must remain private or restricted"):
        PilotCalibrationObservation.model_validate(values)


def test_unmeasured_calibration_report_preserves_missing_pairs_as_blocked() -> None:
    report = unmeasured_detector_calibration_report(_plan())

    assert report.measurement_state == "unmeasured"
    assert report.gate_status == "blocked"
    assert report.observed_pairs == 0
    assert report.missing_pairs == 100
    assert report.precision.value is None
    assert report.precision.null_reason == "empty_denominator"
    assert report.semantic_auto_admission_enabled is False
    assert "behavioral_ground_truth_not_available_for_training_inclusion_claims" in report.blockers


def test_calibration_requires_raw_control_candidates_to_be_filtered() -> None:
    plan = _plan()
    observations = list(_observations(plan))
    observations[98] = observations[98].model_copy(update={"substantive_prediction": "positive"})
    report = build_detector_calibration_report(
        plan,
        tuple(observations),
        reviewer_resolver=_QualifiedReviewers(),
        evidence_resolver=_CalibrationEvidenceResolver(),
    )

    assert report.gate_status == "blocked"
    assert report.control_gate_met is False
    assert "boilerplate_or_self_source_control_not_rejected" in report.blockers
    assert report.semantic_auto_admission_enabled is False
