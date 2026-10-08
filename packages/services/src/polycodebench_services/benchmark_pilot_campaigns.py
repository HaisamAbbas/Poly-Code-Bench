"""Fail-closed readiness and accounting for the Prompt102 pilot campaign."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Protocol

from polycodebench_core.benchmark_audit_documents import AuditDocumentRef
from polycodebench_core.benchmark_pilot import PILOT_BENCHMARKS
from polycodebench_core.benchmark_pilot_campaigns import (
    PILOT_EXPECTED_REPLACEMENTS,
    PILOT_EXPECTED_SEALED_TASKS,
    PILOT_REPLACEMENTS_PER_BENCHMARK,
    MonitorTickCampaignEvidence,
    Prompt102BenchmarkCount,
    Prompt102CampaignReport,
    Prompt102Mode,
    Prompt102ModeCount,
    ReplacementCampaignCandidate,
    SealedTaskCampaignEvidence,
)
from polycodebench_core.canonical import canonical_digest


class Prompt102EvidenceResolver(Protocol):
    """Trusted persistence/authority adapter for source, human, key and usage evidence."""

    def verify_replacement_candidate(self, candidate: ReplacementCampaignCandidate) -> bool:
        """Verify persisted plan/validation, rights, ancestry, identities and usage receipts."""
        ...

    def verify_sealed_task(self, task: SealedTaskCampaignEvidence) -> bool:
        """Verify manifest digest, event order, commitment, lineage and provider receipts."""
        ...

    def verify_monitor_tick(self, tick: MonitorTickCampaignEvidence) -> bool:
        """Verify policy, new-source coverage, source-local usage and safe persisted alerts."""
        ...


def build_prompt102_campaign_report(
    replacements: tuple[ReplacementCampaignCandidate, ...],
    sealed_tasks: tuple[SealedTaskCampaignEvidence, ...],
    monitor_tick: MonitorTickCampaignEvidence | None,
    *,
    evidence_resolver: Prompt102EvidenceResolver | None,
) -> Prompt102CampaignReport:
    """Reconcile bounded campaign evidence without creating tasks or dispatching work."""
    if len(replacements) > PILOT_EXPECTED_REPLACEMENTS:
        raise ValueError("replacement pilot cannot exceed its 12 preregistered candidates")
    if len(sealed_tasks) > PILOT_EXPECTED_SEALED_TASKS:
        raise ValueError("sealed-task pilot cannot exceed its six preregistered tasks")
    if len({item.task_ref.entity_id for item in replacements}) != len(replacements):
        raise ValueError("replacement campaign tasks must be unique")
    if len({item.validation_ref for item in replacements}) != len(replacements):
        raise ValueError("replacement campaign validation evidence must be unique")
    if len({item.task_ref.entity_id for item in sealed_tasks}) != len(sealed_tasks):
        raise ValueError("sealed campaign tasks must be independent and unique")
    if len({item.manifest_ref for item in sealed_tasks}) != len(sealed_tasks):
        raise ValueError("each sealed task must have a distinct manifest")
    if len({item.commitment_digest for item in sealed_tasks}) != len(sealed_tasks):
        raise ValueError("each sealed task must have a distinct hiding commitment")
    if len(
        {item.task_ref.entity_id for item in replacements}
        & {item.task_ref.entity_id for item in sealed_tasks}
    ):
        raise ValueError("replacement and sealed pilot task identities must not overlap")

    blockers: set[str] = set()
    by_benchmark: dict[str, list[ReplacementCampaignCandidate]] = defaultdict(list)
    for candidate in replacements:
        by_benchmark[candidate.benchmark_slug].append(candidate)
    verified_candidates = {
        candidate.validation_ref: (
            evidence_resolver is not None
            and evidence_resolver.verify_replacement_candidate(candidate)
        )
        for candidate in replacements
    }
    replacement_counts = tuple(
        Prompt102BenchmarkCount(
            benchmark_slug=slug,
            competency_slice_id=(
                next(iter({item.competency_slice_id for item in by_benchmark.get(slug, [])}))
                if len({item.competency_slice_id for item in by_benchmark.get(slug, [])}) == 1
                else None
            ),
            observed=len(by_benchmark.get(slug, [])),
            accepted=sum(
                _candidate_is_accepted(candidate) and verified_candidates[candidate.validation_ref]
                for candidate in by_benchmark.get(slug, [])
            ),
        )
        for slug, _, _ in PILOT_BENCHMARKS
    )
    all_replacement_evidence_verified = bool(replacements) and all(verified_candidates.values())
    accepted_replacements = sum(
        _candidate_is_accepted(candidate) and verified_candidates[candidate.validation_ref]
        for candidate in replacements
    )
    budgets_reconciled = _replacement_budgets_reconcile(replacements)
    verified_replacement_count = sum(verified_candidates.values())

    if len(replacements) != PILOT_EXPECTED_REPLACEMENTS:
        blockers.add("replacement_campaign_requires_12_candidates")
    for slug, items in by_benchmark.items():
        if len(items) != PILOT_REPLACEMENTS_PER_BENCHMARK:
            blockers.add(f"replacement_slice_requires_four:{slug}")
    for slug, _, _ in PILOT_BENCHMARKS:
        if len(by_benchmark.get(slug, [])) != PILOT_REPLACEMENTS_PER_BENCHMARK:
            blockers.add(f"replacement_slice_requires_four:{slug}")
        slice_ids = {item.competency_slice_id for item in by_benchmark.get(slug, [])}
        if len(slice_ids) > 1:
            blockers.add(f"replacement_competency_slice_changed:{slug}")
    observed_slice_ids = {item.competency_slice_id for item in replacements}
    if len(observed_slice_ids) not in {0, 3}:
        blockers.add("replacement_campaign_requires_three_frozen_competency_slices")
    if accepted_replacements != len(replacements) or not replacements:
        blockers.add("replacement_validity_rights_lineage_or_review_incomplete")
    if not all_replacement_evidence_verified:
        blockers.add("replacement_plan_human_or_usage_evidence_unverified")
    if not budgets_reconciled:
        blockers.add("replacement_author_checker_budget_not_reconciled")

    sealed_verified = {
        item.manifest_ref: (
            evidence_resolver is not None and evidence_resolver.verify_sealed_task(item)
        )
        for item in sealed_tasks
    }
    verified_sealed_tasks = sum(sealed_verified.values())
    locally_screened_tasks = sum(
        sealed_verified[item.manifest_ref]
        and item.local_screening_result == "passed"
        and item.lineage_state == "verified_independent"
        for item in sealed_tasks
    )
    authorized_disclosures = sum(
        sealed_verified[item.manifest_ref]
        and item.local_screening_result == "passed"
        and item.disclosure_result == "authorized"
        and item.lineage_state == "verified_independent"
        for item in sealed_tasks
    )
    production_mode_sealed_tasks = sum(
        sealed_verified[item.manifest_ref]
        and item.production_mode
        and item.local_screening_result == "passed"
        and item.disclosure_result == "authorized"
        and item.lineage_state == "verified_independent"
        for item in sealed_tasks
    )
    mode_counts = _build_mode_counts(
        replacements,
        verified_candidates,
        sealed_tasks,
        sealed_verified,
    )
    if len(sealed_tasks) != PILOT_EXPECTED_SEALED_TASKS:
        blockers.add("sealed_campaign_requires_six_independent_tasks")
    if locally_screened_tasks != len(sealed_tasks):
        blockers.add("sealed_local_screening_or_independent_lineage_incomplete")
    if authorized_disclosures != len(sealed_tasks):
        blockers.add("authorized_sealed_disclosure_not_verified")
    if production_mode_sealed_tasks != PILOT_EXPECTED_SEALED_TASKS:
        blockers.add("six_production_key_and_timestamp_provider_receipts_unavailable")

    monitor_verified = (
        monitor_tick is not None
        and evidence_resolver is not None
        and evidence_resolver.verify_monitor_tick(monitor_tick)
    )
    complete_changed_source_rescan = (
        monitor_verified and monitor_tick is not None and monitor_tick.rescan_state == "complete"
    )
    if monitor_tick is None:
        blockers.add("changed_source_monitor_tick_missing")
    elif monitor_tick.rescan_state != "complete":
        blockers.add("changed_source_rescan_incomplete")
    if monitor_tick is not None and not monitor_verified:
        blockers.add("monitor_policy_coverage_usage_or_alert_history_unverified")

    if evidence_resolver is None:
        blockers.add("trusted_campaign_evidence_resolver_unavailable")
    query_reserved = monitor_tick.query_units_reserved if monitor_tick else 0
    query_reported = monitor_tick.query_units_used if monitor_tick else 0
    query_verified = query_reported if monitor_verified else 0
    cost_reserved = monitor_tick.cost_micro_usd_reserved if monitor_tick else 0
    cost_reported = monitor_tick.cost_micro_usd_used if monitor_tick else 0
    cost_verified = cost_reported if monitor_verified else 0
    alerts_observed = len(monitor_tick.alert_refs) if monitor_tick else 0

    input_scope = {
        "replacements": [item.model_dump(mode="json") for item in replacements],
        "sealed_tasks": [item.model_dump(mode="json") for item in sealed_tasks],
        "monitor_tick": monitor_tick.model_dump(mode="json") if monitor_tick else None,
    }
    ready = not blockers
    any_evidence = bool(replacements or sealed_tasks or monitor_tick)
    return Prompt102CampaignReport(
        status="ready" if ready else "partial" if any_evidence else "blocked",
        observed_replacements=len(replacements),
        accepted_replacements=accepted_replacements,
        verified_replacement_candidates=verified_replacement_count,
        replacements_by_benchmark=replacement_counts,
        mode_counts=mode_counts,
        replacement_evidence_verified=all_replacement_evidence_verified,
        replacement_budgets_reconciled=budgets_reconciled,
        observed_sealed_tasks=len(sealed_tasks),
        locally_screened_tasks=locally_screened_tasks,
        authorized_disclosures=authorized_disclosures,
        verified_sealed_tasks=verified_sealed_tasks,
        production_mode_sealed_tasks=production_mode_sealed_tasks,
        monitor_ticks=1 if monitor_tick else 0,
        changed_source_rescans=1 if complete_changed_source_rescan else 0,
        monitor_evidence_verified=monitor_verified,
        query_units_reserved=query_reserved,
        query_units_reported=query_reported,
        query_units_verified=query_verified,
        cost_micro_usd_reserved=cost_reserved,
        cost_micro_usd_reported=cost_reported,
        cost_micro_usd_verified=cost_verified,
        monitor_alerts_observed=alerts_observed,
        input_scope_digest=canonical_digest(input_scope),
        blockers=tuple(sorted(blockers)),
    )


def _build_mode_counts(
    replacements: tuple[ReplacementCampaignCandidate, ...],
    verified_replacements: dict[AuditDocumentRef, bool],
    sealed_tasks: tuple[SealedTaskCampaignEvidence, ...],
    verified_seals: dict[AuditDocumentRef, bool],
) -> tuple[Prompt102ModeCount, ...]:
    categories: dict[str, tuple[Prompt102Mode, ...]] = {
        "authoring": ("genuine_human", "approved_model"),
        "source": ("none", "fixture", "approved_live"),
        "model": ("none", "development_mock", "approved_live"),
        "key_provider": ("development", "approved_production"),
        "timestamp_provider": ("development", "trusted_authority"),
    }
    replacement_modes = {
        "authoring": {item.validation_ref: item.authoring_mode for item in replacements}
    }
    sealed_modes = {
        axis: {item.manifest_ref: getattr(item, attribute) for item in sealed_tasks}
        for axis, attribute in (
            ("source", "source_evidence_mode"),
            ("model", "model_evidence_mode"),
            ("key_provider", "key_provider_mode"),
            ("timestamp_provider", "timestamp_provider_mode"),
        )
    }
    result: list[Prompt102ModeCount] = []
    for axis, modes in categories.items():
        values = replacement_modes[axis] if axis == "authoring" else sealed_modes[axis]
        verified = verified_replacements if axis == "authoring" else verified_seals
        for mode in modes:
            result.append(
                Prompt102ModeCount(
                    axis=axis,  # type: ignore[arg-type]
                    mode=mode,
                    reported_count=sum(value == mode for value in values.values()),
                    verified_count=sum(
                        value == mode and verified.get(identity, False)
                        for identity, value in values.items()
                    ),
                )
            )
    return tuple(result)


def _candidate_is_accepted(candidate: ReplacementCampaignCandidate) -> bool:
    return (
        candidate.validation_state == "accepted"
        and candidate.rights_state == "approved"
        and candidate.lineage_state in {"verified_source_family", "verified_independent"}
    )


def _replacement_budgets_reconcile(
    replacements: tuple[ReplacementCampaignCandidate, ...],
) -> bool:
    if not replacements:
        return False
    plans: dict[tuple[str, object], list[ReplacementCampaignCandidate]] = defaultdict(list)
    for item in replacements:
        plans[(item.benchmark_slug, item.plan_ref)].append(item)
    plan_refs_by_benchmark: dict[str, set[object]] = defaultdict(set)
    benchmark_refs_by_slug: dict[str, set[object]] = defaultdict(set)
    for (slug, plan_ref), items in plans.items():
        plan_refs_by_benchmark[slug].add(plan_ref)
        benchmark_refs_by_slug[slug].update(item.benchmark_ref for item in items)
        caps = {
            (
                item.plan_max_drafts,
                item.plan_max_rounds,
                item.plan_max_cost_micro_usd,
                item.plan_max_wall_seconds,
                item.plan_frozen_at,
            )
            for item in items
        }
        if len(caps) != 1:
            return False
        max_drafts, max_rounds, max_cost, max_wall, _ = next(iter(caps))
        if (
            len(items) > max_drafts
            or sum(item.rounds_spent for item in items) > max_rounds
            or sum(item.cost_micro_usd_spent for item in items) > max_cost
            or sum(item.wall_seconds_spent for item in items) > max_wall
        ):
            return False
        source_caps: dict[str, set[int]] = defaultdict(set)
        source_counts = Counter(item.source_id for item in items)
        for item in items:
            source_caps[item.source_id].add(item.source_quota_maximum)
        if any(
            len(source_caps[source_id]) != 1 or count > next(iter(source_caps[source_id]))
            for source_id, count in source_counts.items()
        ):
            return False
    return all(len(items) <= 1 for items in plan_refs_by_benchmark.values()) and all(
        len(items) <= 1 for items in benchmark_refs_by_slug.values()
    )
