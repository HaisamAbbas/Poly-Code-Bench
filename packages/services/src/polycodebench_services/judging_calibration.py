"""Calibration use cases: disjoint selection, label import, agreement and confusion metrics.

Technical Spec 15.3. The judge panel is measured against qualified human labels on a set that is
disjoint from the scored packets. Selection is seeded and stratified so the set cannot be tuned by
adding or removing candidates, and every report states its status: ``measured`` only when real
labels exist, otherwise ``blocked`` with the missing inputs named.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from polycodebench_core.canonical import sha256_bytes
from polycodebench_core.judge_calibration import (
    BIAS_DIRECTION_MARGIN,
    BIAS_MINIMUM_DISAGREEMENTS,
    BiasAssessment,
    CalibrationLabel,
    CalibrationPacket,
    CalibrationPolicy,
    CalibrationReport,
    ItemAgreement,
    audit_selection,
    blocked_report,
    require_labels,
    to_basis_points,
)
from polycodebench_core.judge_contracts import CalibrationBlocked, JudgementResult

ANCHORS = ("0.000000", "0.500000", "1.000000")
Disjointness = Literal["disjoint", "not_demonstrated", "violated"]


def select_calibration_packets(
    *,
    candidates: Sequence[CalibrationPacket],
    scored_packet_digests: Sequence[str],
    policy: CalibrationPolicy,
) -> tuple[CalibrationPacket, ...]:
    """Choose the calibration set: seeded, per language, stratified, and disjoint from scoring.

    Selection order depends only on packet digests and the frozen seed, so the same candidates
    always produce the same set. A packet that was already used for a scored judgement can never
    enter the calibration set, and vice versa.
    """
    scored = set(scored_packet_digests)
    overlap = [packet.packet_digest for packet in candidates if packet.packet_digest in scored]
    if overlap:
        raise CalibrationBlocked(
            "calibration candidates overlap scored packets; a disjoint set is required"
        )
    selected: list[CalibrationPacket] = []
    languages = sorted({packet.language for packet in candidates})
    for language in languages:
        pool = [packet for packet in candidates if packet.language == language]
        ordered_all = sorted(
            pool,
            key=lambda packet: sha256_bytes(
                f"{policy.selection_seed}/{language}/{packet.packet_digest}".encode()
            ),
        )
        chosen: list[CalibrationPacket] = []
        seen: set[str] = set()

        for stratum in policy.required_strata:
            available = [
                entry
                for entry in ordered_all
                if stratum in entry.strata and entry.packet_id not in seen
            ]
            if not available:
                raise CalibrationBlocked(
                    f"no calibration packet for stratum {stratum.value} in {language}"
                )
            remaining = policy.required_packets_per_language - len(chosen)
            # Each stratum contributes a third of the target when the pool allows it, drawn without
            # replacement, so one packet cannot fill every stratum.
            for entry in available[: min(remaining, max(1, len(available) // 3))]:
                seen.add(entry.packet_id)
                chosen.append(entry)
        remaining = policy.required_packets_per_language - len(chosen)
        if remaining > 0:
            for entry in [item for item in ordered_all if item.packet_id not in seen][:remaining]:
                seen.add(entry.packet_id)
                chosen.append(entry)
        if len(chosen) < policy.required_packets_per_language:
            raise CalibrationBlocked(
                f"{language} offers {len(pool)} calibration packets, "
                f"policy requires {policy.required_packets_per_language}"
            )
        for entry in chosen:
            selected.append(
                CalibrationPacket(
                    packet_id=entry.packet_id,
                    packet_digest=entry.packet_digest,
                    language=entry.language,
                    strata=entry.strata,
                    instruction_attempt_count=entry.instruction_attempt_count,
                    selection_digest=sha256_bytes(
                        f"{policy.policy_id}/{policy.version}/{entry.packet_id}/"
                        f"{entry.packet_digest}".encode()
                    ),
                )
            )
    return tuple(sorted(selected, key=lambda packet: (packet.language, packet.packet_id)))


def import_labels(
    documents: Sequence[Mapping[str, object]],
    *,
    policy: CalibrationPolicy,
    packets: Sequence[CalibrationPacket],
) -> tuple[CalibrationLabel, ...]:
    """Validate an imported label file. Invalid or unqualified evidence is refused, not repaired."""
    labels: list[CalibrationLabel] = []
    for index, document in enumerate(documents):
        if not isinstance(document, Mapping):
            raise CalibrationBlocked(f"label {index} is not an object")
        try:
            labels.append(CalibrationLabel.model_validate(dict(document), strict=False))
        except ValueError as error:
            raise CalibrationBlocked(f"label {index} is invalid: {str(error)[:200]}") from None
    require_labels(tuple(labels), policy=policy, packets=tuple(packets))
    return tuple(labels)


def measure_calibration(
    *,
    policy: CalibrationPolicy,
    rubric_digest: str,
    panel_digest: str,
    packets: Sequence[CalibrationPacket],
    labels: Sequence[CalibrationLabel],
    results: Mapping[str, JudgementResult],
    scored_packet_digests: Sequence[str] = (),
    missing_inputs: Sequence[str] = (),
    notes: str = "",
) -> CalibrationReport:
    """Compare judge item scores with human labels, or report the blocker honestly.

    ``results`` maps a packet id to the judgement result obtained for that calibration packet. A
    calibration packet with no judge result cannot be compared and is reported as missing rather
    than counted as agreement.
    """
    packets_tuple = tuple(packets)
    if missing_inputs:
        return blocked_report(
            policy=policy,
            rubric_digest=rubric_digest,
            panel_digest=panel_digest,
            missing_inputs=tuple(missing_inputs),
            packets=packets_tuple,
            labels_received=len(labels),
            notes=notes,
        )
    # Disjointness is established here or not at all: without the scored digests it stays
    # unproven, and a report never claims a property it did not check.
    scored = set(scored_packet_digests)
    disjointness: Disjointness = "disjoint" if scored else "not_demonstrated"
    if scored and any(packet.packet_digest in scored for packet in packets_tuple):
        return blocked_report(
            policy=policy,
            rubric_digest=rubric_digest,
            panel_digest=panel_digest,
            missing_inputs=(
                "the calibration set overlaps scored packets; a disjoint set is required",
            ),
            packets=packets_tuple,
            labels_received=len(labels),
            notes=notes,
            disjointness="violated",
        )
    require_labels(tuple(labels), policy=policy, packets=packets_tuple)
    by_packet_language = {packet.packet_id: packet.language for packet in packets_tuple}
    per_language: dict[str, int] = {
        language: sum(1 for value in by_packet_language.values() if value == language)
        for language in sorted(set(by_packet_language.values()))
    }
    short = sorted(
        language
        for language, count in per_language.items()
        if count < policy.required_packets_per_language
    )
    if short:
        return blocked_report(
            policy=policy,
            rubric_digest=rubric_digest,
            panel_digest=panel_digest,
            missing_inputs=(
                f"calibration set has {per_language[short[0]]} {short[0]} packets, "
                f"policy requires {policy.required_packets_per_language}",
            ),
            packets=packets_tuple,
            labels_received=len(labels),
            notes=notes,
            disjointness=disjointness,
        )
    if not labels:
        return blocked_report(
            policy=policy,
            rubric_digest=rubric_digest,
            panel_digest=panel_digest,
            missing_inputs=(
                f"no qualified {policy.qualification_pattern} human labels for the "
                f"{len(packets_tuple)} selected calibration packets",
            ),
            packets=packets_tuple,
            labels_received=0,
            notes=notes,
            disjointness=disjointness,
        )
    unjudged = sorted({label.packet_id for label in labels} - set(results))
    if unjudged:
        return blocked_report(
            policy=policy,
            rubric_digest=rubric_digest,
            panel_digest=panel_digest,
            missing_inputs=(
                f"{len(unjudged)} calibration packets have no judge result, so no agreement "
                "can be computed for them",
            ),
            packets=packets_tuple,
            labels_received=len(labels),
            notes=notes,
            disjointness=disjointness,
        )

    human: dict[tuple[str, str], Decimal] = {}
    for label in labels:
        human[(label.packet_id, label.item_id)] = Decimal(label.score)
    comparisons = _comparisons(
        results=results,
        human=human,
        packets={packet.packet_id: packet for packet in packets_tuple},
    )
    matched = [entry for entry in comparisons if entry.exact]
    exact_bp = _bp(len(matched), len(comparisons))
    below = sum(1 for entry in comparisons if entry.delta < 0)
    above = sum(1 for entry in comparisons if entry.delta > 0)
    disagreements = below + above
    bias = _bias(below, above, disagreements)
    confusion = _confusion(comparisons)
    per_item = _per_item(comparisons)
    audit = audit_selection(
        packets_tuple, percent=policy.audit_sample_percent, seed=policy.selection_seed
    )
    audit_rate = _bp(len(audit), len(packets_tuple))
    target_met = bool(exact_bp >= policy.promotion_target_bp and not bias.systematic)
    return CalibrationReport(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        rubric_digest=rubric_digest,
        panel_digest=panel_digest,
        status="measured",
        packets_required_per_language=policy.required_packets_per_language,
        packets_selected=packets_tuple,
        packets_by_language=per_language,
        labels_received=len(labels),
        labels_expected=sum(
            len({label.item_id for label in labels if label.packet_id == packet.packet_id})
            for packet in packets_tuple
        ),
        items_compared=len(comparisons),
        exact_agreement_bp=exact_bp,
        promotion_target_bp=policy.promotion_target_bp,
        promotion_target_met=target_met,
        confusion_matrix=confusion,
        per_item=per_item,
        bias=bias,
        audit_selection=audit,
        audit_rate_bp=audit_rate,
        disjointness=disjointness,
        notes=notes,
    )


@dataclass(frozen=True)
class _Comparison:
    item_id: str
    judge: Decimal
    human: Decimal

    @property
    def exact(self) -> bool:
        return self.judge == self.human

    @property
    def delta(self) -> Decimal:
        return self.judge - self.human


def _comparisons(
    *,
    results: Mapping[str, JudgementResult],
    human: Mapping[tuple[str, str], Decimal],
    packets: Mapping[str, CalibrationPacket],
) -> tuple[_Comparison, ...]:
    """Compare each label with the judge result of *its own* packet.

    A result whose identity does not match the calibration packet it was filed under is skipped
    rather than compared, so agreement is never computed across mismatched pairs.
    """
    comparisons: list[_Comparison] = []
    for (packet_id, item_id), label_score in sorted(human.items()):
        result = results[packet_id]
        packet = packets.get(packet_id)
        if result.packet_id != packet_id or result.packet_digest != (
            packet.packet_digest if packet else None
        ):
            continue
        outcome = next((entry for entry in result.items if entry.item_id == item_id), None)
        if outcome is None or outcome.mean_score is None:
            # An item the panel could not answer is not agreement; it is a missing measurement.
            continue
        comparisons.append(_Comparison(item_id, Decimal(outcome.mean_score), label_score))
    return tuple(comparisons)


def _confusion(comparisons: Sequence[_Comparison]) -> dict[str, dict[str, int]]:
    matrix = {anchor: {other: 0 for other in ANCHORS} for anchor in ANCHORS}
    for entry in comparisons:
        matrix[_anchor(entry.human)][_anchor(entry.judge)] += 1
    return matrix


def _anchor(value: Decimal) -> str:
    for anchor in ANCHORS:
        if abs(value - Decimal(anchor)) <= Decimal("0.000001"):
            return anchor
    return value.quantize(Decimal("0.000001")).to_eng_string()


def _per_item(comparisons: Sequence[_Comparison]) -> tuple[ItemAgreement, ...]:
    by_item: dict[str, list[_Comparison]] = {}
    for entry in comparisons:
        by_item.setdefault(entry.item_id, []).append(entry)
    rows: list[ItemAgreement] = []
    for item_id in sorted(by_item):
        entries = by_item[item_id]
        matched = sum(1 for entry in entries if entry.exact)
        below = sum(1 for entry in entries if entry.delta < 0)
        above = sum(1 for entry in entries if entry.delta > 0)
        rows.append(
            ItemAgreement(
                item_id=item_id,
                compared=len(entries),
                exact_matches=matched,
                agreement_bp=_bp(matched, len(entries)),
                judge_below_human=below,
                judge_above_human=above,
            )
        )
    return tuple(rows)


def _bias(below: int, above: int, disagreements: int) -> BiasAssessment:
    if disagreements < BIAS_MINIMUM_DISAGREEMENTS:
        return BiasAssessment(
            judge_below_human=below,
            judge_above_human=above,
            disagreements=disagreements,
            systematic=None,
            direction="insufficient_evidence",
        )
    share = Decimal(max(below, above)) / Decimal(disagreements)
    if share < BIAS_DIRECTION_MARGIN:
        return BiasAssessment(
            judge_below_human=below,
            judge_above_human=above,
            disagreements=disagreements,
            systematic=False,
            direction="none",
        )
    return BiasAssessment(
        judge_below_human=below,
        judge_above_human=above,
        disagreements=disagreements,
        systematic=True,
        direction="judge_below_human" if below > above else "judge_above_human",
    )


def _bp(matches: int, compared: int) -> int:
    if compared == 0:
        raise CalibrationBlocked("an agreement rate needs at least one comparison")
    return to_basis_points(Decimal(matches) / Decimal(compared))
