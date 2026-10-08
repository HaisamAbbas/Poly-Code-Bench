"""Model-specific temporal eligibility from interval-valued audit evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    ChronologyInterval,
    ImmutableArtifactRef,
    ModelContextPayload,
    TemporalExplanationCode,
    TemporalState,
    timestamp_evidence_bounds_ns,
)

_ACCEPTED_EXPOSURE_BASES = {"archive_capture", "verified_upstream_record"}


@dataclass(frozen=True)
class TemporalEligibilityResult:
    """Qualified outcome that preserves the source evidence used for comparison."""

    status: TemporalState
    claim_qualifier_codes: tuple[
        Literal[
            "no_originality_claim",
            "not_training_proof",
            "provider_declared_cutoff",
            "provider_declared_pin",
            "insufficient_evidence",
        ],
        ...,
    ]
    source_evidence_refs: tuple[AuditDocumentRef | ImmutableArtifactRef, ...]
    explanation_code: TemporalExplanationCode


def evaluate_temporal_eligibility(
    chronology: tuple[ChronologyInterval, ...],
    model_context: ModelContextPayload | None,
) -> TemporalEligibilityResult:
    """Compare accepted upstream exposure intervals to one pinned model cutoff.

    Benchmark release dates are deliberately not substituted for upstream source
    dates. Unverified or local receipt claims cannot produce a positive post-cutoff
    result, and a cutoff receipt never establishes originality or training.
    """
    if model_context is None or model_context.pin_confidence in {"mutable_alias", "unknown"}:
        return TemporalEligibilityResult(
            "mutable_model_context", ("insufficient_evidence",), (), "revision_not_pinned"
        )

    context_qualifiers: list[Literal["provider_declared_cutoff", "provider_declared_pin"]] = []
    if model_context.pin_confidence == "provider_declared":
        context_qualifiers.append("provider_declared_pin")
    if model_context.cutoff_confidence == "provider_declared":
        context_qualifiers.append("provider_declared_cutoff")
    if model_context.cutoff_confidence == "unknown" or model_context.training_cutoff is None:
        return TemporalEligibilityResult(
            "unknown_cutoff",
            ("insufficient_evidence", *context_qualifiers),
            (),
            "cutoff_unknown",
        )

    exposure_records = tuple(
        item for item in chronology if item.event == "upstream_public_exposure"
    )
    accepted = tuple(
        item
        for item in exposure_records
        if item.verification_state == "verified" and item.basis in _ACCEPTED_EXPOSURE_BASES
    )
    if not accepted:
        return TemporalEligibilityResult(
            "unknown_source_time",
            ("insufficient_evidence", *context_qualifiers),
            (),
            "no_accepted_source_date",
        )

    cutoff = model_context.training_cutoff
    assert cutoff.earliest is not None and cutoff.latest is not None
    cutoff_start = timestamp_evidence_bounds_ns(cutoff.earliest)[0]
    cutoff_end = timestamp_evidence_bounds_ns(cutoff.latest)[1]
    for update in model_context.update_history:
        update_start = timestamp_evidence_bounds_ns(update.earliest)[0] if update.earliest else None
        update_end = timestamp_evidence_bounds_ns(update.latest)[1] if update.latest else None
        if (
            update.verification_state != "verified"
            or update_start is None
            or update_end is None
            or update_end >= cutoff_start
        ):
            return TemporalEligibilityResult(
                "mutable_model_context",
                ("insufficient_evidence", *context_qualifiers),
                (),
                "model_update_not_proven_before_cutoff",
            )
    outcomes: list[Literal["pre", "post", "overlap", "unknown"]] = []
    refs: list[AuditDocumentRef | ImmutableArtifactRef] = []
    for item in accepted:
        if item.evidence_ref is not None:
            refs.append(item.evidence_ref)
        source_start = timestamp_evidence_bounds_ns(item.earliest)[0] if item.earliest else None
        source_end = timestamp_evidence_bounds_ns(item.latest)[1] if item.latest else None
        if source_end is not None and source_end < cutoff_start:
            outcomes.append("pre")
        elif source_start is not None and source_start > cutoff_end:
            outcomes.append("post")
        elif source_start is not None and source_end is not None:
            outcomes.append("overlap")
        else:
            outcomes.append("unknown")

    if "pre" in outcomes:
        return TemporalEligibilityResult(
            "pre_cutoff_exposure_detected",
            ("no_originality_claim", "not_training_proof", *context_qualifiers),
            tuple(refs),
            "exposure_precedes_cutoff",
        )
    if "overlap" in outcomes:
        return TemporalEligibilityResult(
            "interval_overlap",
            ("no_originality_claim", "not_training_proof", *context_qualifiers),
            tuple(refs),
            "source_cutoff_intervals_overlap",
        )
    if "unknown" in outcomes:
        return TemporalEligibilityResult(
            "unknown_source_time",
            ("insufficient_evidence", *context_qualifiers),
            tuple(refs),
            "source_interval_incomplete",
        )

    # Do not issue a green temporal badge while an unverified earlier exposure
    # claim remains in the immutable chronology. It stays visible for review.
    if any(item.verification_state != "verified" for item in exposure_records):
        return TemporalEligibilityResult(
            "unknown_source_time",
            ("insufficient_evidence", *context_qualifiers),
            tuple(refs),
            "unverified_source_could_precede_cutoff",
        )
    return TemporalEligibilityResult(
        "post_declared_cutoff",
        ("no_originality_claim", "not_training_proof", *context_qualifiers),
        tuple(refs),
        "exposure_follows_cutoff",
    )
