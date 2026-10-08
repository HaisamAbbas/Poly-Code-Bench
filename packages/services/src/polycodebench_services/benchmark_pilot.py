"""Preflight and family-aware evaluation for the actual benchmark audit pilot."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal, Protocol

from polycodebench_core.benchmark_audit_documents import ImmutableArtifactRef
from polycodebench_core.benchmark_imports import freeze_sample
from polycodebench_core.benchmark_pilot import (
    PILOT_BENCHMARKS,
    PILOT_REQUIRED_SOURCE_GROUPS,
    PILOT_SAMPLE_PER_BENCHMARK,
    ClusterBootstrapMetric,
    PilotCalibrationObservation,
    PilotCalibrationPlan,
    PilotCalibrationSourceScope,
    PilotDatasetReadiness,
    PilotDetectorCalibrationReport,
    PilotDetectorStratum,
    PilotPreflight,
    PilotSourceReadiness,
    timestamp_as_datetime,
)
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Decimal6, Digest, UtcTimestamp

from polycodebench_services.match_verification import match_relation_rubric_digest

MIN_CALIBRATION_PAIRS = 100
MIN_CALIBRATION_FAMILIES = 30
MIN_VALID_BOOTSTRAP_FRACTION = Decimal("0.95")
_BOOTSTRAP_METHOD = "family_cluster_percentile_sha256_v1"
_BOOTSTRAP_HASH_DOMAIN = b"pcb-family-cluster-bootstrap-v1\0"
_METRICS = ("precision", "recall", "false_positive_rate", "false_negative_rate")


class ReviewerQualificationResolver(Protocol):
    """Trusted application adapter for current human reviewer qualifications."""

    def is_qualified(self, subject: str, at: UtcTimestamp) -> bool: ...


class PilotEvidenceResolver(Protocol):
    """Trusted application boundary for immutable artifacts, rights, scopes and reviewers."""

    def verify_artifact(self, artifact: ImmutableArtifactRef) -> bool: ...

    def verify_benchmark_population(
        self,
        benchmark_slug: str,
        revision: str,
        split: str,
        variant: str,
        source_artifact: ImmutableArtifactRef,
        eligible_ids: tuple[str, ...],
    ) -> bool: ...

    def verify_benchmark_rights(self, benchmark_slug: str, evidence_digest: Digest) -> bool: ...

    def verify_source_scope(
        self, source_group: str, snapshot_digest: Digest, scope_evidence_digest: Digest
    ) -> bool: ...

    def verify_live_connector_conformance(
        self, source_group: str, snapshot_digest: Digest, scope_evidence_digest: Digest
    ) -> bool: ...

    def verify_review_plan(self, plan_digest: Digest, frozen_at: UtcTimestamp) -> bool: ...

    def verify_reviewer_roster(self, roster_digest: Digest) -> bool: ...


class PilotCalibrationEvidenceResolver(Protocol):
    """Trusted adapter that binds source snapshots and reviewer artifacts to stored bytes."""

    def verify_calibration_plan(self, plan: PilotCalibrationPlan) -> bool: ...

    def verify_calibration_source_scope(self, scope: PilotCalibrationSourceScope) -> bool: ...

    def verify_calibration_observation(self, observation: PilotCalibrationObservation) -> bool: ...


def _digest_seed(seed: str, replicate: int, draw: int, attempt: int) -> int:
    material = (
        _BOOTSTRAP_HASH_DOMAIN
        + seed.encode("ascii")
        + b"\0"
        + replicate.to_bytes(4, "big")
        + draw.to_bytes(4, "big")
        + attempt.to_bytes(4, "big")
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _draw_index(seed: str, replicate: int, draw: int, population: int) -> int:
    if population < 1:
        raise ValueError("family bootstrap requires at least one labeled family")
    domain = 2**64
    limit = domain - domain % population
    attempt = 0
    while True:
        candidate = _digest_seed(seed, replicate, draw, attempt)
        if candidate < limit:
            return candidate % population
        attempt += 1


def _ratio(value: int, denominator: int) -> Decimal6 | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 28
        result = (Decimal(value) / Decimal(denominator)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_EVEN
        )
    return f"{result:.6f}"


def _percentile(values: list[Decimal], numerator: int, denominator: int) -> Decimal:
    """Return a deterministic nearest-rank percentile using an exact rational rank."""
    ordered = sorted(values)
    rank = (numerator * len(ordered) + denominator - 1) // denominator
    return ordered[max(0, rank - 1)]


def _metric(
    numerator: int,
    denominator: int,
    values: list[Decimal],
    *,
    total_replicates: int,
) -> ClusterBootstrapMetric:
    point = _ratio(numerator, denominator)
    if denominator == 0:
        return ClusterBootstrapMetric(
            numerator=0,
            denominator=0,
            value=None,
            lower_95=None,
            upper_95=None,
            valid_replicates=len(values),
            total_replicates=total_replicates,
            null_reason="empty_denominator",
        )
    if Decimal(len(values)) / Decimal(total_replicates) < MIN_VALID_BOOTSTRAP_FRACTION:
        return ClusterBootstrapMetric(
            numerator=numerator,
            denominator=denominator,
            value=point,
            lower_95=None,
            upper_95=None,
            valid_replicates=len(values),
            total_replicates=total_replicates,
            null_reason="unstable_family_resamples",
        )
    lower = _percentile(values, 1, 40)
    upper = _percentile(values, 39, 40)
    return ClusterBootstrapMetric(
        numerator=numerator,
        denominator=denominator,
        value=point,
        lower_95=f"{lower.quantize(Decimal('0.000001'), rounding=ROUND_HALF_EVEN):.6f}",
        upper_95=f"{upper.quantize(Decimal('0.000001'), rounding=ROUND_HALF_EVEN):.6f}",
        valid_replicates=len(values),
        total_replicates=total_replicates,
        null_reason=None,
    )


def build_pilot_preflight(
    datasets: tuple[PilotDatasetReadiness, ...],
    sources: tuple[PilotSourceReadiness, ...],
    *,
    review_plan_digest: Digest | None,
    review_plan_frozen_at: UtcTimestamp | None,
    review_search_started_at: UtcTimestamp | None,
    reviewer_roster_digest: Digest | None,
    evidence_resolver: PilotEvidenceResolver | None,
) -> PilotPreflight:
    """Validate the exact 300-item source plan without searching or fetching anything."""
    if len({item.benchmark_slug for item in datasets}) != len(datasets):
        raise ValueError("pilot preflight cannot contain duplicate benchmark readiness records")
    if len({item.source_group for item in sources}) != len(sources):
        raise ValueError("pilot preflight cannot contain duplicate source scope records")
    dataset_by_slug = {item.benchmark_slug: item for item in datasets}
    source_by_group = {item.source_group: item for item in sources}
    blockers: set[str] = set()
    ready_datasets = 0
    verified_populations = 0
    approved_sources = 0
    frozen_timestamps: list[UtcTimestamp] = []
    search_timestamps: list[UtcTimestamp] = []
    for slug, revision, split in PILOT_BENCHMARKS:
        item = dataset_by_slug.get(slug)
        if item is None:
            blockers.add(f"missing_exact_import_plan:{slug}")
            continue
        if item.revision != revision:
            blockers.add(f"source_revision_not_pinned:{slug}")
        if item.split != split:
            blockers.add(f"benchmark_split_not_frozen:{slug}")
        expected_variant = {
            "humaneval": {"official"},
            "mbpp": {"original", "sanitized"},
            "swe-bench-verified": {"verified"},
        }[slug]
        if item.variant not in expected_variant:
            blockers.add(f"benchmark_variant_not_frozen:{slug}")
        if len(item.selected_ids) != PILOT_SAMPLE_PER_BENCHMARK:
            blockers.add(f"requires_100_unique_ids:{slug}")
        if len(set(item.selected_ids)) != len(item.selected_ids):
            blockers.add(f"duplicate_sample_ids:{slug}")
        if not _sample_ids_match_split(slug, item.selected_ids):
            blockers.add(f"sample_ids_do_not_match_frozen_split:{slug}")
        source_bytes_verified = (
            item.source_digest is not None
            and item.source_artifact_ref is not None
            and item.source_artifact_ref.digest == item.source_digest
            and evidence_resolver is not None
            and evidence_resolver.verify_artifact(item.source_artifact_ref)
        )
        if not source_bytes_verified:
            blockers.add(f"exact_source_bytes_not_verified:{slug}")
        population_verified = (
            item.revision == revision
            and item.split == split
            and item.variant is not None
            and item.source_artifact_ref is not None
            and item.source_digest is not None
            and evidence_resolver is not None
            and evidence_resolver.verify_benchmark_population(
                slug,
                revision,
                split,
                item.variant,
                item.source_artifact_ref,
                item.eligible_ids,
            )
        )
        if not population_verified:
            blockers.add(f"eligible_population_not_verified_from_pinned_bytes:{slug}")
        else:
            verified_populations += 1
        rights_verified = (
            item.rights_evidence_digest is not None
            and evidence_resolver is not None
            and evidence_resolver.verify_benchmark_rights(slug, item.rights_evidence_digest)
        )
        if not rights_verified:
            blockers.add(f"item_rights_not_approved:{slug}")
        if item.membership_digest is None or item.sample_seed is None:
            blockers.add(f"seeded_membership_not_frozen:{slug}")
        elif item.variant is not None and item.variant in expected_variant:
            try:
                expected_ids, expected_membership = freeze_sample(
                    benchmark_slug=slug,
                    revision=revision,
                    split=split,
                    variant=item.variant,
                    seed=item.sample_seed,
                    eligible_item_ids=item.eligible_ids,
                )
            except ValueError:
                blockers.add(f"eligible_population_invalid_or_too_small:{slug}")
            else:
                if (
                    item.selected_ids != expected_ids
                    or item.membership_digest != expected_membership
                ):
                    blockers.add(f"seeded_sample_membership_mismatch:{slug}")
        if item.plan_frozen_at is None:
            blockers.add(f"sample_and_sources_not_frozen:{slug}")
        else:
            frozen_timestamps.append(item.plan_frozen_at)
        if item.search_started_at is not None:
            search_timestamps.append(item.search_started_at)
            if item.plan_frozen_at is not None and timestamp_as_datetime(
                item.plan_frozen_at
            ) >= timestamp_as_datetime(item.search_started_at):
                blockers.add(f"plan_frozen_after_search_started:{slug}")
        if (
            item.revision == revision
            and item.split == split
            and item.variant in expected_variant
            and len(item.selected_ids) == PILOT_SAMPLE_PER_BENCHMARK
            and len(set(item.selected_ids)) == PILOT_SAMPLE_PER_BENCHMARK
            and _sample_ids_match_split(slug, item.selected_ids)
        ):
            ready_datasets += 1

    for group in PILOT_REQUIRED_SOURCE_GROUPS:
        source_item = source_by_group.get(group)
        if source_item is None:
            blockers.add(f"missing_approved_source_scope:{group}")
            continue
        if source_item.snapshot_digest is None or source_item.scope_evidence_digest is None:
            blockers.add(f"missing_immutable_source_snapshot_or_scope:{group}")
        scope_verified = (
            source_item.snapshot_digest is not None
            and source_item.scope_evidence_digest is not None
            and evidence_resolver is not None
            and evidence_resolver.verify_source_scope(
                group, source_item.snapshot_digest, source_item.scope_evidence_digest
            )
        )
        if not scope_verified:
            blockers.add(f"source_authorization_not_verified:{group}")
        conformance_verified = (
            source_item.snapshot_digest is not None
            and source_item.scope_evidence_digest is not None
            and evidence_resolver is not None
            and evidence_resolver.verify_live_connector_conformance(
                group, source_item.snapshot_digest, source_item.scope_evidence_digest
            )
        )
        if not conformance_verified:
            blockers.add(f"source_connector_not_live_conformant:{group}")
        if source_item.plan_frozen_at is None:
            blockers.add(f"source_caps_and_scope_not_frozen:{group}")
        else:
            frozen_timestamps.append(source_item.plan_frozen_at)
        if source_item.search_started_at is not None:
            search_timestamps.append(source_item.search_started_at)
            if source_item.plan_frozen_at is not None and timestamp_as_datetime(
                source_item.plan_frozen_at
            ) >= timestamp_as_datetime(source_item.search_started_at):
                blockers.add(f"source_plan_frozen_after_search_started:{group}")
        if (
            source_item.snapshot_digest is not None
            and source_item.scope_evidence_digest is not None
            and scope_verified
            and conformance_verified
            and source_item.plan_frozen_at is not None
            and (
                source_item.search_started_at is None
                or timestamp_as_datetime(source_item.plan_frozen_at)
                < timestamp_as_datetime(source_item.search_started_at)
            )
        ):
            approved_sources += 1

    required_sources = [
        source_by_group[group] for group in PILOT_REQUIRED_SOURCE_GROUPS if group in source_by_group
    ]
    request_caps = [item.max_requests for item in required_sources]
    byte_caps = [item.max_response_bytes for item in required_sources]
    total_byte_caps = [item.max_total_bytes for item in required_sources]
    source_requests_capped = (
        len(request_caps) == 3
        and all(0 < cap <= 2_000 for cap in request_caps)
        and sum(request_caps) <= 12_000
    )
    if not source_requests_capped:
        blockers.add("source_request_caps_missing_or_over_policy")
    source_response_bytes_capped = (
        len(byte_caps) == 3
        and all(0 < cap <= 5_242_880 for cap in byte_caps)
        and all(0 < cap <= 536_870_912 for cap in total_byte_caps)
        and sum(total_byte_caps) <= 536_870_912
    )
    if not source_response_bytes_capped:
        blockers.add("per_response_byte_caps_missing_or_over_policy")
    review_plan_frozen = (
        review_plan_digest is not None
        and review_plan_frozen_at is not None
        and evidence_resolver is not None
        and evidence_resolver.verify_review_plan(review_plan_digest, review_plan_frozen_at)
    )
    if not review_plan_frozen:
        blockers.add("independent_review_scope_not_preregistered")
    elif review_plan_frozen_at is not None:
        frozen_timestamps.append(review_plan_frozen_at)
    if review_search_started_at is not None:
        search_timestamps.append(review_search_started_at)
        if review_plan_frozen_at is not None and timestamp_as_datetime(
            review_plan_frozen_at
        ) >= timestamp_as_datetime(review_search_started_at):
            blockers.add("review_scope_frozen_after_search_started")
            review_plan_frozen = False
    if search_timestamps and (
        len(frozen_timestamps) < 7
        or max(map(timestamp_as_datetime, frozen_timestamps))
        >= min(map(timestamp_as_datetime, search_timestamps))
    ):
        blockers.add("all_benchmark_source_and_review_scopes_must_precede_first_search")
    independent_reviewer_roster_verified = (
        reviewer_roster_digest is not None
        and evidence_resolver is not None
        and evidence_resolver.verify_reviewer_roster(reviewer_roster_digest)
    )
    if not independent_reviewer_roster_verified:
        blockers.add("qualified_independent_reviewer_roster_unavailable")

    return PilotPreflight(
        status="ready" if not blockers else "blocked",
        input_scope_digest=canonical_digest(
            {
                "datasets": [item.model_dump(mode="json") for item in datasets],
                "sources": [item.model_dump(mode="json") for item in sources],
                "review_plan_digest": review_plan_digest,
                "review_plan_frozen_at": review_plan_frozen_at,
                "review_search_started_at": review_search_started_at,
                "reviewer_roster_digest": reviewer_roster_digest,
            }
        ),
        review_plan_digest=review_plan_digest,
        datasets_with_100_ids=ready_datasets,
        datasets_with_verified_population=verified_populations,
        approved_source_snapshots=approved_sources,
        source_requests_capped=source_requests_capped,
        source_response_bytes_capped=source_response_bytes_capped,
        independent_review_plan_frozen=review_plan_frozen,
        independent_reviewer_roster_verified=independent_reviewer_roster_verified,
        missing_inputs=tuple(sorted(blockers)),
    )


def _sample_ids_match_split(slug: str, item_ids: tuple[str, ...]) -> bool:
    if slug == "humaneval":
        return all(re.fullmatch(r"(?:test|HumanEval)/[0-9]+", item, re.ASCII) for item in item_ids)
    if slug == "mbpp":
        return all(
            item.isascii() and item.isdecimal() and 11 <= int(item) <= 510 for item in item_ids
        )
    return all(
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}__[0-9]+",
            item,
            re.ASCII,
        )
        for item in item_ids
    )


def _confusion(rows: Sequence[PilotCalibrationObservation]) -> dict[str, int]:
    counts = {name: 0 for name in ("tp", "fp", "tn", "fn")}
    for row in rows:
        truth = row.truth
        prediction = row.substantive_prediction
        if truth == "positive" and prediction == "positive":
            counts["tp"] += 1
        elif truth == "negative" and prediction == "positive":
            counts["fp"] += 1
        elif truth == "negative" and prediction == "negative":
            counts["tn"] += 1
        elif truth == "positive" and prediction == "negative":
            counts["fn"] += 1
    return counts


def _cluster_metrics(
    grouped: dict[str, list[PilotCalibrationObservation]],
    *,
    seed: str,
    replicates: int,
) -> dict[str, list[Decimal]]:
    family_names = tuple(sorted(grouped))
    family_confusion = {family: _confusion(grouped[family]) for family in family_names}
    values: dict[str, list[Decimal]] = {name: [] for name in _METRICS}
    for replicate in range(replicates):
        counts = {name: 0 for name in ("tp", "fp", "tn", "fn")}
        for draw in range(len(family_names)):
            family = family_names[_draw_index(seed, replicate, draw, len(family_names))]
            for key, value in family_confusion[family].items():
                counts[key] += value
        metric_counts = {
            "precision": (counts["tp"], counts["tp"] + counts["fp"]),
            "recall": (counts["tp"], counts["tp"] + counts["fn"]),
            "false_positive_rate": (counts["fp"], counts["fp"] + counts["tn"]),
            "false_negative_rate": (counts["fn"], counts["tp"] + counts["fn"]),
        }
        for name, (numerator, denominator) in metric_counts.items():
            if denominator:
                with localcontext() as context:
                    context.prec = 28
                    values[name].append(Decimal(numerator) / Decimal(denominator))
    return values


def _detector_stratum(
    axis: Literal["relation", "language", "modality", "source"],
    value: str,
    rows: tuple[PilotCalibrationObservation, ...],
    *,
    plan: PilotCalibrationPlan,
) -> PilotDetectorStratum:
    known_labeled = [item for item in rows if item.truth != "unknown"]
    known_scored = [
        item
        for item in rows
        if item.truth != "unknown" and item.substantive_prediction != "unknown"
    ]
    confusion = _confusion(known_scored)
    grouped: dict[str, list[PilotCalibrationObservation]] = defaultdict(list)
    for item in known_labeled:
        grouped[item.family_digest].append(item)
    derived_seed = f"{plan.analysis_seed}/{axis}/{value}"
    bootstrap = (
        _cluster_metrics(
            grouped,
            seed=derived_seed,
            replicates=plan.bootstrap_replicates,
        )
        if grouped
        else {name: [] for name in _METRICS}
    )
    metric_specs = {
        "precision": (confusion["tp"], confusion["tp"] + confusion["fp"]),
        "recall": (confusion["tp"], confusion["tp"] + confusion["fn"]),
        "false_positive_rate": (confusion["fp"], confusion["fp"] + confusion["tn"]),
        "false_negative_rate": (confusion["fn"], confusion["tp"] + confusion["fn"]),
    }
    metrics = {
        name: _metric(
            numerator,
            denominator,
            bootstrap[name],
            total_replicates=plan.bootstrap_replicates,
        )
        for name, (numerator, denominator) in metric_specs.items()
    }
    return PilotDetectorStratum(
        axis=axis,
        value=value,
        observed_pairs=len(rows),
        labeled_pairs=len(known_labeled),
        labeled_families=len({item.family_digest for item in known_labeled}),
        true_positive=confusion["tp"],
        false_positive=confusion["fp"],
        true_negative=confusion["tn"],
        false_negative=confusion["fn"],
        unknown_pairs=len(rows) - len(known_scored),
        precision=metrics["precision"],
        recall=metrics["recall"],
        false_positive_rate=metrics["false_positive_rate"],
        false_negative_rate=metrics["false_negative_rate"],
    )


def build_detector_calibration_report(
    plan: PilotCalibrationPlan,
    observations: tuple[PilotCalibrationObservation, ...],
    *,
    reviewer_resolver: ReviewerQualificationResolver | None,
    evidence_resolver: PilotCalibrationEvidenceResolver | None,
) -> PilotDetectorCalibrationReport:
    """Measure only a frozen family-held-out set and never enable automatic admission."""
    if plan.relation_rubric_digest != match_relation_rubric_digest():
        raise ValueError("calibration plan does not bind the registered match relation rubric")
    if plan.interval_method != _BOOTSTRAP_METHOD:
        raise ValueError("calibration plan interval method is not implemented")
    pair_digests = [item.pair_digest for item in observations]
    if len(pair_digests) != len(set(pair_digests)):
        raise ValueError("held-out calibration observations must have unique pair digests")
    expected_family_by_pair = {item.pair_digest: item.family_digest for item in plan.held_out_pairs}
    planned_pairs = set(expected_family_by_pair)
    planned_families = set(plan.held_out_family_digests)
    if any(item.pair_digest not in planned_pairs for item in observations):
        raise ValueError("an observation is outside the preregistered held-out pair set")
    if any(item.family_digest not in planned_families for item in observations):
        raise ValueError("an observation is outside the preregistered held-out family set")
    if any(
        item.family_digest != expected_family_by_pair[item.pair_digest] for item in observations
    ):
        raise ValueError("held-out pair family assignment changed after the plan was frozen")
    approved_snapshots = {
        (scope.source_group, scope.source_snapshot_ref) for scope in plan.approved_source_scopes
    }
    calibration_plan_verified = (
        evidence_resolver is not None and evidence_resolver.verify_calibration_plan(plan)
    )
    source_snapshots_verified = evidence_resolver is not None and all(
        evidence_resolver.verify_calibration_source_scope(scope)
        for scope in plan.approved_source_scopes
    )
    if any(
        (item.source_group, item.source_snapshot_ref) not in approved_snapshots
        for item in observations
    ):
        raise ValueError("an observation uses a corpus snapshot outside the frozen source scope")
    for item in observations:
        if item.detector_config_digest != plan.detector_config_digest:
            raise ValueError("detector configuration changed after the calibration plan was frozen")
        if timestamp_as_datetime(item.predicted_at) <= timestamp_as_datetime(plan.frozen_at):
            raise ValueError("detector predictions must be generated after the frozen plan")
        if min(
            timestamp_as_datetime(item.reviewer_one_at),
            timestamp_as_datetime(item.reviewer_two_at),
        ) < timestamp_as_datetime(item.predicted_at):
            raise ValueError("held-out predictions must be committed before reviewers label pairs")
        if item.adjudicated_at is not None and timestamp_as_datetime(item.adjudicated_at) < max(
            timestamp_as_datetime(item.reviewer_one_at),
            timestamp_as_datetime(item.reviewer_two_at),
        ):
            raise ValueError("adjudication must follow both independent reviewer labels")
        reviewers = [item.reviewer_one_subject, item.reviewer_two_subject]
        if item.adjudicator_subject is not None:
            reviewers.append(item.adjudicator_subject)
            assert item.adjudicated_at is not None
        if any(
            subject in {plan.plan_author_subject, plan.detector_operator_subject}
            for subject in reviewers
        ):
            raise ValueError("plan authors and detector operators cannot label their own pilot")

    independent_label_evidence_verified = (
        bool(observations)
        and evidence_resolver is not None
        and all(evidence_resolver.verify_calibration_observation(item) for item in observations)
    )

    observed = len(observations)
    missing = len(planned_pairs) - observed
    known_labeled = [item for item in observations if item.truth != "unknown"]
    labeled_families = {item.family_digest for item in known_labeled}
    known_scored = [
        item
        for item in observations
        if item.truth != "unknown" and item.substantive_prediction != "unknown"
    ]
    unknown_pairs = observed - len(known_scored)
    confusion = _confusion(known_scored)
    grouped: dict[str, list[PilotCalibrationObservation]] = defaultdict(list)
    for item in known_labeled:
        grouped[item.family_digest].append(item)
    bootstrap = (
        _cluster_metrics(
            grouped,
            seed=plan.analysis_seed,
            replicates=plan.bootstrap_replicates,
        )
        if grouped
        else {name: [] for name in _METRICS}
    )
    metric_specs = {
        "precision": (confusion["tp"], confusion["tp"] + confusion["fp"]),
        "recall": (confusion["tp"], confusion["tp"] + confusion["fn"]),
        "false_positive_rate": (confusion["fp"], confusion["fp"] + confusion["tn"]),
        "false_negative_rate": (confusion["fn"], confusion["tp"] + confusion["fn"]),
    }
    metrics = {
        name: _metric(
            numerator,
            denominator,
            bootstrap[name],
            total_replicates=plan.bootstrap_replicates,
        )
        for name, (numerator, denominator) in metric_specs.items()
    }

    review_records: list[tuple[str, UtcTimestamp]] = []
    for item in observations:
        review_records.extend(
            (
                (item.reviewer_one_subject, item.reviewer_one_at),
                (item.reviewer_two_subject, item.reviewer_two_at),
            )
        )
        if item.adjudicator_subject is not None and item.adjudicated_at is not None:
            review_records.append((item.adjudicator_subject, item.adjudicated_at))
    qualified = (
        bool(review_records)
        and reviewer_resolver is not None
        and all(reviewer_resolver.is_qualified(subject, at) for subject, at in review_records)
    )
    source_groups = {item.source_group for item in observations}
    source_refs = {(item.source_group, item.source_snapshot_ref) for item in observations}
    source_counts = Counter(item.source_group for item in observations)
    relation_counts = Counter(item.truth_relation for item in observations)
    strata: list[PilotDetectorStratum] = []
    axis_extractors: tuple[
        tuple[
            Literal["relation", "language", "modality", "source"],
            Callable[[PilotCalibrationObservation], str],
        ],
        ...,
    ] = (
        ("relation", lambda row: row.truth_relation),
        ("language", lambda row: row.language),
        ("modality", lambda row: row.modality),
        ("source", lambda row: row.source_group),
    )
    for axis, value_of in axis_extractors:
        rows_by_value: dict[str, list[PilotCalibrationObservation]] = defaultdict(list)
        for item in observations:
            rows_by_value[value_of(item)].append(item)
        strata.extend(
            _detector_stratum(axis, value, tuple(rows), plan=plan)
            for value, rows in sorted(rows_by_value.items())
        )
    controls = [item for item in observations if item.control_kind != "ordinary"]
    boilerplate = [item for item in controls if item.control_kind == "boilerplate"]
    self_source = [item for item in controls if item.control_kind == "self_source"]
    controls_pass = (
        bool(boilerplate)
        and bool(self_source)
        and all(
            item.truth_relation == item.control_kind
            and item.truth == "negative"
            and item.candidate_prediction == "positive"
            and item.substantive_prediction == "negative"
            for item in controls
        )
    )
    pair_gate = (
        missing == 0
        and len(known_labeled) >= MIN_CALIBRATION_PAIRS
        and len(labeled_families) >= MIN_CALIBRATION_FAMILIES
        and set(PILOT_REQUIRED_SOURCE_GROUPS) <= source_groups
        and calibration_plan_verified
        and source_snapshots_verified
    )
    precision = metrics["precision"]
    recall = metrics["recall"]
    false_positive_rate = metrics["false_positive_rate"]
    precision_gate = precision.lower_95 is not None and Decimal(precision.lower_95) >= Decimal(
        plan.minimum_precision_lower_95
    )
    recall_gate = (
        plan.minimum_recall_lower_95 is not None
        and recall.lower_95 is not None
        and Decimal(recall.lower_95) >= Decimal(plan.minimum_recall_lower_95)
    )
    fpr_gate = (
        plan.maximum_false_positive_rate_upper_95 is not None
        and false_positive_rate.upper_95 is not None
        and Decimal(false_positive_rate.upper_95)
        <= Decimal(plan.maximum_false_positive_rate_upper_95)
    )
    unknown_fraction = Decimal(unknown_pairs) / Decimal(observed) if observed else Decimal(1)
    unknown_gate = unknown_fraction <= Decimal(plan.maximum_unknown_fraction)
    blockers: list[str] = []
    if missing:
        blockers.append("held_out_pair_observations_missing")
    if len(known_labeled) < MIN_CALIBRATION_PAIRS:
        blockers.append("fewer_than_100_independently_labeled_pairs")
    if len(labeled_families) < MIN_CALIBRATION_FAMILIES:
        blockers.append("fewer_than_30_independent_task_families")
    if not set(PILOT_REQUIRED_SOURCE_GROUPS) <= source_groups:
        blockers.append("required_source_diversity_not_observed")
    if not source_snapshots_verified:
        blockers.append("approved_calibration_snapshots_unverified")
    if not calibration_plan_verified:
        blockers.append("calibration_plan_freeze_unverified")
    if not independent_label_evidence_verified:
        blockers.append("independent_label_artifacts_unverified")
    if not qualified:
        blockers.append("qualified_independent_reviewer_roster_unavailable_or_failed")
    if not precision_gate:
        blockers.append("precision_lower_95_threshold_not_met")
    if not recall_gate:
        blockers.append("recall_lower_95_threshold_not_met_or_not_declared")
    if not fpr_gate:
        blockers.append("false_positive_rate_upper_95_threshold_not_met_or_not_declared")
    if not unknown_gate:
        blockers.append("unknown_or_missingness_fraction_exceeds_frozen_gate")
    if not controls_pass:
        blockers.append("boilerplate_or_self_source_control_not_rejected")

    gate_status: Literal["blocked", "thresholds_met"] = (
        "thresholds_met" if not blockers else "blocked"
    )
    complete_measurement = (
        observed == len(planned_pairs)
        and len(known_scored) == len(planned_pairs)
        and calibration_plan_verified
        and source_snapshots_verified
        and independent_label_evidence_verified
        and qualified
    )
    if observed == 0:
        measurement_state: Literal["unmeasured", "descriptive", "held_out_measured"] = "unmeasured"
    elif complete_measurement:
        measurement_state = "held_out_measured"
    else:
        measurement_state = "descriptive"
    return PilotDetectorCalibrationReport(
        calibration_plan_digest=plan.digest,
        detector_config_digest=plan.detector_config_digest,
        relation_rubric_digest=plan.relation_rubric_digest,
        measurement_state=measurement_state,
        gate_status=gate_status,
        planned_pairs=len(planned_pairs),
        observed_pairs=observed,
        missing_pairs=missing,
        labeled_pairs=len(known_labeled),
        labeled_families=len(labeled_families),
        unknown_pairs=unknown_pairs,
        source_snapshot_count=len(source_refs),
        relation_counts=tuple(sorted(relation_counts.items())),
        source_counts=tuple(sorted(source_counts.items())),
        strata=tuple(strata),
        true_positive=confusion["tp"],
        false_positive=confusion["fp"],
        true_negative=confusion["tn"],
        false_negative=confusion["fn"],
        precision=precision,
        recall=recall,
        false_positive_rate=false_positive_rate,
        false_negative_rate=metrics["false_negative_rate"],
        boilerplate_controls=len(boilerplate),
        boilerplate_candidate_hits=sum(
            item.candidate_prediction == "positive" for item in boilerplate
        ),
        boilerplate_rejected=sum(
            item.candidate_prediction == "positive" and item.substantive_prediction == "negative"
            for item in boilerplate
        ),
        self_source_controls=len(self_source),
        self_source_candidate_hits=sum(
            item.candidate_prediction == "positive" for item in self_source
        ),
        self_source_rejected=sum(
            item.candidate_prediction == "positive" and item.substantive_prediction == "negative"
            for item in self_source
        ),
        control_gate_met=controls_pass,
        reviewer_qualification_verified=qualified,
        calibration_plan_verified=calibration_plan_verified,
        source_snapshots_verified=source_snapshots_verified,
        independent_label_evidence_verified=independent_label_evidence_verified,
        precision_threshold_met=precision_gate,
        recall_threshold_met=recall_gate,
        false_positive_rate_threshold_met=fpr_gate,
        unknown_fraction_threshold_met=unknown_gate,
        scope_and_sample_gate_met=pair_gate,
        semantic_auto_admission_enabled=False,
        blockers=tuple(blockers),
    )


def unmeasured_detector_calibration_report(
    plan: PilotCalibrationPlan,
) -> PilotDetectorCalibrationReport:
    """Create an explicit no-label report without converting absence into zero errors."""
    empty_metric = ClusterBootstrapMetric(
        numerator=0,
        denominator=0,
        value=None,
        lower_95=None,
        upper_95=None,
        valid_replicates=0,
        total_replicates=plan.bootstrap_replicates,
        null_reason="empty_denominator",
    )
    return PilotDetectorCalibrationReport(
        calibration_plan_digest=plan.digest,
        detector_config_digest=plan.detector_config_digest,
        relation_rubric_digest=plan.relation_rubric_digest,
        measurement_state="unmeasured",
        gate_status="blocked",
        planned_pairs=len(plan.held_out_pair_digests),
        observed_pairs=0,
        missing_pairs=len(plan.held_out_pair_digests),
        labeled_pairs=0,
        labeled_families=0,
        unknown_pairs=0,
        source_snapshot_count=0,
        relation_counts=(),
        source_counts=(),
        strata=(),
        true_positive=0,
        false_positive=0,
        true_negative=0,
        false_negative=0,
        precision=empty_metric,
        recall=empty_metric,
        false_positive_rate=empty_metric,
        false_negative_rate=empty_metric,
        boilerplate_controls=0,
        boilerplate_candidate_hits=0,
        boilerplate_rejected=0,
        self_source_controls=0,
        self_source_candidate_hits=0,
        self_source_rejected=0,
        control_gate_met=False,
        reviewer_qualification_verified=False,
        calibration_plan_verified=False,
        source_snapshots_verified=False,
        independent_label_evidence_verified=False,
        precision_threshold_met=False,
        recall_threshold_met=False,
        false_positive_rate_threshold_met=False,
        unknown_fraction_threshold_met=False,
        scope_and_sample_gate_met=False,
        blockers=(
            "held_out_pair_observations_missing",
            "fewer_than_100_independently_labeled_pairs",
            "fewer_than_30_independent_task_families",
            "qualified_independent_reviewer_roster_unavailable_or_failed",
            "behavioral_ground_truth_not_available_for_training_inclusion_claims",
        ),
    )
