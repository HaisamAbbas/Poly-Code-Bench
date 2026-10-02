"""Calibration contracts: human labels, disjoint selection, agreement and confusion metrics.

Technical Spec 15.3 requires a human-labeled calibration set that is *disjoint* from scored
packets, at least 30 representative packets per pilot language, and a reported item agreement
with a confusion matrix before any ranked release. Nothing in this module can turn a missing
label into an agreement number: with no labels the report status is ``blocked`` and the missing
inputs are named.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from polycodebench_core.application_errors import ServiceError
from polycodebench_core.canonical import canonical_document_bytes, sha256_bytes
from polycodebench_core.judge_contracts import (
    AnchorId,
    CalibrationBlocked,
    ItemId,
    JudgeModel,
)
from polycodebench_core.models import ContractModel, Decimal6, Digest, EntityId, Slug, UtcTimestamp


class CalibrationStratum(StrEnum):
    REPRESENTATIVE = "representative"
    ADVERSARIAL_COMMENT = "adversarial_comment"
    STYLISTIC_ALTERNATIVE = "stylistic_alternative"


REQUIRED_PACKETS_PER_LANGUAGE = 30
PROMOTION_TARGET_BP = 8000
# A bias is called systematic when the two disagreement directions differ by at least this share
# of compared items and at least this many items disagree.
BIAS_DIRECTION_MARGIN = Decimal("0.60")
BIAS_MINIMUM_DISAGREEMENTS = 5
DECLARED_ANCHORS = frozenset({"0.000000", "0.500000", "1.000000"})


BASIS_POINTS = 10_000


def to_basis_points(value: Decimal) -> int:
    """A ratio in [0, 1] as an integer count of basis points (0..10000)."""
    scaled = (value * BASIS_POINTS).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


class CalibrationLabelError(ServiceError):
    code = "JUDGE_CALIBRATION_LABEL_INVALID"
    status_code = 422


class CalibrationLabel(JudgeModel):
    """One qualified human label for one item of one calibration packet."""

    kind: Literal["calibration_label"] = "calibration_label"
    packet_id: EntityId
    packet_digest: Digest
    item_id: ItemId
    score: Decimal6
    labeler_subject: Annotated[str, Field(min_length=1, max_length=255)]
    qualification: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")]
    rationale: Annotated[str, Field(min_length=16, max_length=2000)]
    cited_anchor_ids: tuple[AnchorId, ...] = Field(min_length=1)
    labeled_at: UtcTimestamp

    @model_validator(mode="after")
    def label_is_individual(self) -> CalibrationLabel:
        if len(set(self.cited_anchor_ids)) != len(self.cited_anchor_ids):
            raise ValueError("a label cites each anchor once")
        if self.score not in DECLARED_ANCHORS:
            raise ValueError(f"a human label must use a declared anchor, not {self.score}")
        return self


class CalibrationPacket(JudgeModel):
    """One packet admitted to the calibration set, with the strata it represents."""

    kind: Literal["calibration_packet"] = "calibration_packet"
    packet_id: EntityId
    packet_digest: Digest
    language: Literal["python", "rust"]
    strata: tuple[CalibrationStratum, ...] = Field(min_length=1, max_length=3)
    instruction_attempt_count: Annotated[int, Field(ge=0)] = 0
    selection_digest: Digest | None = None

    @model_validator(mode="after")
    def strata_are_unique(self) -> CalibrationPacket:
        if len(set(self.strata)) != len(self.strata):
            raise ValueError("calibration strata must be unique per packet")
        return self


class CalibrationPolicy(ContractModel):
    """The frozen calibration policy. Changing a count or target is a new policy version."""

    kind: Literal["calibration_policy"] = "calibration_policy"
    schema_version: Literal[1] = 1
    policy_id: Slug
    version: Annotated[int, Field(ge=1)]
    required_packets_per_language: Annotated[int, Field(ge=1, le=1000)] = (
        REQUIRED_PACKETS_PER_LANGUAGE
    )
    required_strata: tuple[CalibrationStratum, ...] = (
        CalibrationStratum.REPRESENTATIVE,
        CalibrationStratum.ADVERSARIAL_COMMENT,
        CalibrationStratum.STYLISTIC_ALTERNATIVE,
    )
    promotion_target_bp: Annotated[int, Field(ge=0, le=10_000)] = PROMOTION_TARGET_BP
    selection_seed: Annotated[str, Field(min_length=1, max_length=64)] = "judge-calibration-v1"
    audit_sample_percent: Annotated[int, Field(ge=0, le=100)] = 10
    qualification_pattern: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")] = (
        "polycodebench_reviewer_v1"
    )


class ItemAgreement(JudgeModel):
    kind: Literal["item_agreement"] = "item_agreement"
    item_id: ItemId
    compared: Annotated[int, Field(ge=0)]
    exact_matches: Annotated[int, Field(ge=0)]
    agreement_bp: Annotated[int, Field(ge=0, le=10_000)] | None
    judge_below_human: Annotated[int, Field(ge=0)]
    judge_above_human: Annotated[int, Field(ge=0)]


class BiasAssessment(JudgeModel):
    """Direction-of-bias summary. ``systematic`` is None when there is too little disagreement."""

    kind: Literal["bias_assessment"] = "bias_assessment"
    judge_below_human: Annotated[int, Field(ge=0)]
    judge_above_human: Annotated[int, Field(ge=0)]
    disagreements: Annotated[int, Field(ge=0)]
    systematic: bool | None
    direction: Literal["none", "judge_below_human", "judge_above_human", "insufficient_evidence"]


class CalibrationReport(ContractModel):
    """What calibration actually measured, or why it could not.

    ``status == "blocked"`` means the gate is unmet. It is not a pass with a caveat: the
    promotion target and the bias direction stay None, and ``missing_inputs`` names what a human
    or operator has to supply.
    """

    kind: Literal["calibration_report"] = "calibration_report"
    schema_version: Literal[1] = 1
    policy_id: Slug
    policy_version: Annotated[int, Field(ge=1)]
    rubric_digest: Digest
    panel_digest: Digest
    status: Literal["blocked", "measured"]
    packets_required_per_language: Annotated[int, Field(ge=1)]
    packets_selected: tuple[CalibrationPacket, ...] = Field(max_length=2000)
    packets_by_language: dict[str, int]
    labels_received: Annotated[int, Field(ge=0)]
    labels_expected: Annotated[int, Field(ge=0)]
    items_compared: Annotated[int, Field(ge=0)]
    exact_agreement_bp: Annotated[int, Field(ge=0, le=10_000)] | None
    promotion_target_bp: Annotated[int, Field(ge=0, le=10_000)]
    promotion_target_met: bool | None
    confusion_matrix: dict[str, dict[str, int]]
    per_item: tuple[ItemAgreement, ...] = Field(max_length=64)
    bias: BiasAssessment
    audit_selection: tuple[CalibrationPacket, ...] = Field(max_length=200)
    audit_rate_bp: Annotated[int, Field(ge=0, le=10_000)]
    disjointness: Literal["disjoint", "not_demonstrated", "violated"] = "not_demonstrated"
    missing_inputs: tuple[str, ...] = ()
    notes: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def blocked_reports_measure_nothing(self) -> CalibrationReport:
        if self.status == "blocked":
            if self.exact_agreement_bp is not None or self.promotion_target_met is not None:
                raise ValueError("a blocked calibration report cannot report agreement")
            if not self.missing_inputs:
                raise ValueError("a blocked calibration report names its missing inputs")
        else:
            if self.exact_agreement_bp is None or self.promotion_target_met is None:
                raise ValueError("a measured calibration report carries its agreement result")
            if self.labels_received == 0:
                raise ValueError("a measured calibration report needs actual labels")
        return self

    def digest(self) -> str:
        return sha256_bytes(canonical_document_bytes(self))

    def canonical_bytes(self) -> bytes:
        return canonical_document_bytes(self)


def require_labels(
    labels: tuple[CalibrationLabel, ...],
    *,
    policy: CalibrationPolicy,
    packets: tuple[CalibrationPacket, ...],
) -> None:
    """Refuse a label set that is not qualified human evidence for calibration packets.

    Four failures are refused rather than tolerated: an unqualified labeler, a label for a packet
    that is not in the disjoint calibration set, a duplicate disagreement between labelers on the
    same item, and a label whose anchor citations are not declared by the caller's packet scope.
    """
    packet_ids = {packet.packet_id for packet in packets}
    digests = {packet.packet_digest: packet.packet_id for packet in packets}
    if len(digests) != len(packets):
        raise CalibrationBlocked("calibration packet digests must be unique")
    seen: dict[tuple[str, str], CalibrationLabel] = {}
    for label in labels:
        if label.packet_id not in packet_ids:
            raise CalibrationBlocked(
                f"label for packet {label.packet_id} is outside the calibration set"
            )
        if digests.get(label.packet_digest) != label.packet_id:
            raise CalibrationBlocked("label packet digest does not match the selected packet")
        if label.qualification != policy.qualification_pattern:
            raise CalibrationLabelError(
                f"labeler {label.labeler_subject} is not a qualified "
                f"{policy.qualification_pattern} reviewer"
            )
        key = (label.packet_id, label.item_id)
        previous = seen.get(key)
        if previous is not None and previous.score != label.score:
            raise CalibrationLabelError(
                f"calibration item {label.item_id} has two different human labels; "
                "the disagreement must be adjudicated before metrics are reported"
            )
        seen[key] = label


def audit_selection(
    packets: tuple[CalibrationPacket, ...],
    *,
    percent: int,
    seed: str,
) -> tuple[CalibrationPacket, ...]:
    """Deterministic seeded audit sample of calibration packets (Technical Spec 15.3).

    The sample depends only on packet digests and the frozen seed, so a re-run selects the same
    packets and cannot be tuned by adding or removing candidates.
    """
    if percent <= 0:
        return ()
    ordered = sorted(
        packets,
        key=lambda packet: sha256_bytes(f"{seed}/{packet.packet_digest}".encode()),
    )
    count = max(1, (len(ordered) * percent) // 100) if percent < 100 else len(ordered)
    return tuple(sorted(ordered[:count], key=lambda packet: packet.packet_id))


def blocked_report(
    *,
    policy: CalibrationPolicy,
    rubric_digest: str,
    panel_digest: str,
    missing_inputs: tuple[str, ...],
    packets: tuple[CalibrationPacket, ...] = (),
    labels_received: int = 0,
    notes: str = "",
    disjointness: Literal["disjoint", "not_demonstrated", "violated"] = "not_demonstrated",
) -> CalibrationReport:
    """The only honest report when human calibration inputs are absent."""
    if not missing_inputs:
        raise CalibrationBlocked("a blocked calibration report must name its missing inputs")
    by_language: dict[str, int] = {}
    for packet in packets:
        by_language[packet.language] = by_language.get(packet.language, 0) + 1
    return CalibrationReport(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        rubric_digest=rubric_digest,
        panel_digest=panel_digest,
        status="blocked",
        packets_required_per_language=policy.required_packets_per_language,
        packets_selected=packets,
        packets_by_language=by_language,
        labels_received=labels_received,
        labels_expected=policy.required_packets_per_language * max(len(by_language), 1),
        items_compared=0,
        exact_agreement_bp=None,
        promotion_target_bp=policy.promotion_target_bp,
        promotion_target_met=None,
        confusion_matrix={},
        per_item=(),
        bias=BiasAssessment(
            judge_below_human=0,
            judge_above_human=0,
            disagreements=0,
            systematic=None,
            direction="insufficient_evidence",
        ),
        audit_selection=(),
        audit_rate_bp=0,
        disjointness=disjointness,
        missing_inputs=missing_inputs,
        notes=notes,
    )
