"""Bounded local retrieval planning, candidate selection and deterministic replay helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from polycodebench_core.benchmark_audit_documents import EntityRef
from polycodebench_core.benchmark_audit_registry import AuditCatalogBundle, SourceGroupPolicy
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.retrieval import (
    RETRIEVAL_STAGE_ORDER,
    CandidateSelectionResult,
    RetrievalCacheIdentity,
    RetrievalCandidateHit,
    RetrievalCoverageManifest,
    RetrievalCoverageState,
    RetrievalMethodPin,
    RetrievalPlan,
    RetrievalPlanRequest,
    RetrievalPlanState,
    RetrievalQueryOutcome,
    RetrievalQueryUnit,
    RetrievalReplayResult,
    RetrievalSourcePin,
    RetrievalStage,
    RetrievalUnitState,
    SourceCandidateLimit,
    SourceGroupSlug,
)
from polycodebench_core.task_fingerprints import FingerprintConfig

_STAGE_RANK = {stage: position for position, stage in enumerate(RETRIEVAL_STAGE_ORDER)}
_MAX_OBSERVED_HITS = 100_000


def _method_pins(config: FingerprintConfig) -> tuple[RetrievalMethodPin, ...]:
    config_digest = canonical_digest(config.model_dump(mode="json"))
    exact_digest = canonical_digest(
        {
            "config_digest": config_digest,
            "features": ["exact_bytes", "normalized_text"],
            "method": "exact-normalized-retrieval-v1",
        }
    )
    lexical_digest = canonical_digest(
        {
            "config_digest": config_digest,
            "features": ["token_shingles"],
            "method": "lexical-retrieval-v1",
        }
    )
    return (
        RetrievalMethodPin(
            stage="exact_normalized",
            method_digest=exact_digest,
            state="available",
            supported_features=("exact_bytes", "normalized_text"),
            unsupported_features=(),
            reason_codes=(),
        ),
        RetrievalMethodPin(
            stage="lexical_code",
            method_digest=lexical_digest,
            state="partial",
            supported_features=("token_shingles",),
            unsupported_features=("code_structure",),
            reason_codes=("code_structure_parser_not_configured",),
        ),
        RetrievalMethodPin(
            stage="semantic",
            method_digest=None,
            state="unsupported",
            supported_features=(),
            unsupported_features=("semantic",),
            reason_codes=("approved_embedding_index_unavailable",),
        ),
    )


def _source_blockers(
    policy: SourceGroupPolicy,
    requested_pin: RetrievalSourcePin | None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if policy.authorization_state != "approved_scoped":
        blockers.append("source_authorization_not_approved")
    if policy.connector_state not in {"importable", "audit_conformant"}:
        blockers.append("source_connector_not_registered")
    if policy.conformance_state != "live_verified":
        blockers.append("source_conformance_not_live_verified")
    if requested_pin is None:
        blockers.extend(
            (
                "approved_corpus_snapshot_missing",
                "source_rights_manifest_missing",
                "derived_index_manifest_missing",
                "privacy_permission_scope_missing",
            )
        )
    elif requested_pin.state == "blocked":
        blockers.extend(requested_pin.blocker_codes)
    # The current service has neither a trusted artifact/rights verifier nor a local index reader.
    blockers.extend(
        (
            "source_artifact_verifier_unavailable",
            "retrieval_index_runtime_not_implemented",
        )
    )
    return tuple(sorted(set(blockers)))


def _build_source_pin(
    source_group: SourceGroupSlug,
    policy: SourceGroupPolicy,
    requested_pin: RetrievalSourcePin | None,
) -> RetrievalSourcePin:
    blockers = _source_blockers(policy, requested_pin)
    if requested_pin is None:
        return RetrievalSourcePin(
            source_group=source_group,
            source_revision=None,
            snapshot_ref=None,
            rights_ref=None,
            privacy_scope_digest=None,
            index_pins=(),
            state="blocked",
            blocker_codes=blockers,
        )
    values = requested_pin.model_dump(mode="python")
    values["state"] = "blocked" if blockers else "provided"
    values["blocker_codes"] = blockers
    return RetrievalSourcePin.model_validate(values)


def retrieval_query_unit_digest(
    *,
    request: RetrievalPlanRequest,
    component_ref: EntityRef,
    source_group: str,
    stage: RetrievalStage,
) -> str:
    return canonical_digest(
        {
            "audit_plan_ref": request.audit_plan_ref.model_dump(mode="json"),
            "task_ref": request.task_ref.model_dump(mode="json"),
            "component_ref": component_ref.model_dump(mode="json"),
            "source_group": source_group,
            "stage": stage,
        }
    )


def build_retrieval_plan(
    bundle: AuditCatalogBundle,
    request: RetrievalPlanRequest,
    *,
    fingerprint_config: FingerprintConfig | None = None,
) -> RetrievalPlan:
    """Freeze fixed stages and coverage units; this function never reads an index or network."""
    limits = bundle.limits
    if len(request.source_groups) > limits.max_source_groups_per_plan:
        raise ValueError("retrieval source scope exceeds the frozen source-group limit")
    if limits.max_candidates_per_source_per_task > 20 or limits.max_candidates_per_task > 100:
        raise ValueError("catalog retrieval caps exceed the Prompt89 pilot ceiling")
    policies = {item.slug: item for item in bundle.source_policies.groups}
    unknown = {group for group in request.source_groups if group not in policies}
    if unknown:
        raise ValueError(f"unknown source groups: {sorted(unknown)}")
    requested_pins = {item.source_group: item for item in request.source_pins}
    source_pins = tuple(
        _build_source_pin(group, policies[group], requested_pins.get(group))
        for group in request.source_groups
    )
    method_pins = _method_pins(fingerprint_config or FingerprintConfig())
    units: list[RetrievalQueryUnit] = []
    for component in request.component_refs:
        for source in source_pins:
            for method in method_pins:
                state: RetrievalUnitState
                if method.state == "unsupported":
                    state = "unsupported"
                    reasons = method.reason_codes
                elif source.state == "blocked":
                    state = "blocked"
                    reasons = source.blocker_codes
                elif method.state == "blocked":
                    state = "blocked"
                    reasons = method.reason_codes
                elif method.stage not in {pin.stage for pin in source.index_pins}:
                    state = "blocked"
                    reasons = ("source_stage_index_missing",)
                else:
                    state = "planned"
                    reasons = ()
                units.append(
                    RetrievalQueryUnit(
                        unit_digest=retrieval_query_unit_digest(
                            request=request,
                            component_ref=component,
                            source_group=source.source_group,
                            stage=method.stage,
                        ),
                        component_ref=component,
                        source_group=source.source_group,
                        stage=method.stage,
                        state=state,
                        reason_codes=reasons,
                    )
                )
    query_limit = limits.max_query_units_per_plan
    if len(units) > query_limit:
        raise ValueError("retrieval query units exceed the frozen plan limit")
    plan_state: RetrievalPlanState = (
        "blocked" if any(unit.state == "blocked" for unit in units) else "planned"
    )
    return RetrievalPlan(
        audit_plan_ref=request.audit_plan_ref,
        task_ref=request.task_ref,
        component_refs=request.component_refs,
        source_pins=source_pins,
        method_pins=method_pins,
        query_units=tuple(units),
        seed=request.seed,
        max_candidates_per_source=limits.max_candidates_per_source_per_task,
        max_candidates_total=limits.max_candidates_per_task,
        state=plan_state,
    )


def retrieval_plan_digest(plan: RetrievalPlan) -> str:
    return canonical_digest(plan.model_dump(mode="json"))


def _candidate_identity(candidate: RetrievalCandidateHit) -> str:
    return canonical_digest(
        {
            "task_ref": candidate.task_ref.model_dump(mode="json"),
            "component_ref": candidate.component_ref.model_dump(mode="json"),
            "source_group": candidate.source_group,
            "source_revision": candidate.source_revision,
            "source_document_digest": candidate.source_document_digest,
            "candidate_component_digest": candidate.candidate_component_digest,
        }
    )


def _candidate_order(
    plan: RetrievalPlan, candidate: RetrievalCandidateHit
) -> tuple[int, int, str, str]:
    seed_tie = canonical_digest(
        {"candidate_identity": _candidate_identity(candidate), "seed": plan.seed}
    )
    return (
        _STAGE_RANK[candidate.stage],
        candidate.rank,
        candidate.source_group,
        seed_tie,
    )


def _candidate_input_digest(candidates: Iterable[RetrievalCandidateHit]) -> str:
    values = tuple(candidates)
    ordered = sorted(
        values,
        key=lambda item: (
            _candidate_identity(item),
            _STAGE_RANK[item.stage],
            item.rank,
            item.query_unit_digest,
        ),
    )
    return canonical_digest([item.model_dump(mode="json") for item in ordered])


def select_retrieval_candidates(
    plan: RetrievalPlan,
    candidates: tuple[RetrievalCandidateHit, ...],
) -> CandidateSelectionResult:
    """Deduplicate and cap already-produced hits; this does not query an index or source."""
    if len(candidates) > _MAX_OBSERVED_HITS:
        raise ValueError("observed retrieval hits exceed the bounded selection input limit")
    pin_by_group = {item.source_group: item for item in plan.source_pins}
    unit_by_digest = {item.unit_digest: item for item in plan.query_units}
    component_by_id = {item.entity_id: item for item in plan.component_refs}
    for hit in candidates:
        unit = unit_by_digest.get(hit.query_unit_digest)
        pin = pin_by_group.get(hit.source_group)
        component = component_by_id.get(hit.component_ref.entity_id)
        if (
            hit.task_ref != plan.task_ref
            or component != hit.component_ref
            or unit is None
            or unit.state != "planned"
            or unit.component_ref != hit.component_ref
            or unit.source_group != hit.source_group
            or unit.stage != hit.stage
            or pin is None
            or pin.state != "provided"
            or hit.source_revision != pin.source_revision
        ):
            raise ValueError("candidate hit is outside the frozen planned task/source/stage scope")

    best_by_identity: dict[str, RetrievalCandidateHit] = {}
    for hit in sorted(candidates, key=lambda item: _candidate_order(plan, item)):
        best_by_identity.setdefault(_candidate_identity(hit), hit)
    unique_hits = tuple(best_by_identity.values())
    ordered_by_source: dict[str, list[RetrievalCandidateHit]] = defaultdict(list)
    for hit in unique_hits:
        ordered_by_source[hit.source_group].append(hit)

    source_limits: list[SourceCandidateLimit] = []
    source_retained: list[RetrievalCandidateHit] = []
    for source in plan.source_pins:
        source_candidates = sorted(
            ordered_by_source[source.source_group],
            key=lambda item: _candidate_order(plan, item),
        )
        retained = source_candidates[: plan.max_candidates_per_source]
        source_retained.extend(retained)
        source_limits.append(
            SourceCandidateLimit(
                source_group=source.source_group,
                unique_candidates=len(source_candidates),
                retained_candidates=len(retained),
                discarded_candidates=len(source_candidates) - len(retained),
            )
        )
    ordered_retained = sorted(source_retained, key=lambda item: _candidate_order(plan, item))
    selected = tuple(ordered_retained[: plan.max_candidates_total])
    total_discarded = len(ordered_retained) - len(selected)
    input_digest = _candidate_input_digest(candidates)
    selected_json = [item.model_dump(mode="json") for item in selected]
    limit_json = [item.model_dump(mode="json") for item in source_limits]
    result_digest = canonical_digest(
        {
            "plan_digest": retrieval_plan_digest(plan),
            "input_candidates_digest": input_digest,
            "observed_hits": len(candidates),
            "unique_candidates": len(unique_hits),
            "selected_candidates": selected_json,
            "source_limits": limit_json,
            "total_cap_discarded": total_discarded,
        }
    )
    return CandidateSelectionResult(
        plan_digest=retrieval_plan_digest(plan),
        input_candidates_digest=input_digest,
        observed_hits=len(candidates),
        unique_candidates=len(unique_hits),
        selected_candidates=selected,
        source_limits=tuple(source_limits),
        total_cap_discarded=total_discarded,
        result_digest=result_digest,
    )


def build_retrieval_coverage(
    plan: RetrievalPlan,
    outcomes: tuple[RetrievalQueryOutcome, ...],
) -> RetrievalCoverageManifest:
    units = {item.unit_digest: item for item in plan.query_units}
    by_digest = {item.unit_digest: item for item in outcomes}
    if len(by_digest) != len(outcomes) or set(by_digest) != set(units):
        raise ValueError("retrieval coverage must reconcile every planned query unit exactly once")
    for digest, outcome in by_digest.items():
        planned_unit = units[digest]
        if planned_unit.state == "blocked" and outcome.state != "blocked":
            raise ValueError("blocked retrieval units cannot be reported as searched")
        if planned_unit.state == "unsupported" and outcome.state != "unsupported":
            raise ValueError("unsupported retrieval units cannot be reported as searched")
        if planned_unit.state == "planned" and outcome.state in {"blocked", "unsupported"}:
            # A runtime may discover a later outage or missing permission; it must say why.
            continue
    all_searched = all(item.state in {"complete", "no_match"} for item in outcomes)
    any_attempts = any(item.queries_attempted > 0 for item in outcomes)
    manifest_state: RetrievalCoverageState = (
        "complete" if all_searched else "partial" if any_attempts else "blocked"
    )
    return RetrievalCoverageManifest(
        plan_digest=retrieval_plan_digest(plan), outcomes=outcomes, state=manifest_state
    )


def initial_retrieval_coverage(plan: RetrievalPlan) -> RetrievalCoverageManifest:
    outcomes = tuple(
        RetrievalQueryOutcome(
            unit_digest=unit.unit_digest,
            state=unit.state,
            queries_attempted=0,
            candidates_observed=0,
            truncated_candidates=0,
            candidates_digest=None,
            reason_code=unit.reason_codes[0] if unit.reason_codes else None,
        )
        for unit in plan.query_units
    )
    return RetrievalCoverageManifest(
        plan_digest=retrieval_plan_digest(plan), outcomes=outcomes, state="blocked"
    )


def retrieval_cache_key(identity: RetrievalCacheIdentity) -> str:
    """Include privacy permission scope and immutable corpus/method identities in cache keys."""
    return canonical_digest(identity.model_dump(mode="json"))


def verify_retrieval_replay(
    plan: RetrievalPlan,
    expected: CandidateSelectionResult,
    stored_candidates: tuple[RetrievalCandidateHit, ...] | None,
) -> RetrievalReplayResult:
    """Replay only from stored candidate hits; missing bytes never trigger a live refetch."""
    if stored_candidates is None:
        return RetrievalReplayResult(
            state="missing_evidence",
            expected_input_digest=expected.input_candidates_digest,
            expected_result_digest=expected.result_digest,
            actual_input_digest=None,
            actual_result_digest=None,
            reason_code="stored_candidate_hits_missing",
        )
    if expected.plan_digest != retrieval_plan_digest(plan):
        return RetrievalReplayResult(
            state="mismatch",
            expected_input_digest=expected.input_candidates_digest,
            expected_result_digest=expected.result_digest,
            actual_input_digest=_candidate_input_digest(stored_candidates),
            actual_result_digest=None,
            reason_code="frozen_retrieval_plan_changed",
        )
    actual_input = _candidate_input_digest(stored_candidates)
    if actual_input != expected.input_candidates_digest:
        return RetrievalReplayResult(
            state="mismatch",
            expected_input_digest=expected.input_candidates_digest,
            expected_result_digest=expected.result_digest,
            actual_input_digest=actual_input,
            actual_result_digest=None,
            reason_code="stored_candidate_input_digest_changed",
        )
    try:
        actual = select_retrieval_candidates(plan, stored_candidates)
    except ValueError:
        return RetrievalReplayResult(
            state="mismatch",
            expected_input_digest=expected.input_candidates_digest,
            expected_result_digest=expected.result_digest,
            actual_input_digest=actual_input,
            actual_result_digest=None,
            reason_code="stored_candidate_hits_outside_frozen_plan",
        )
    replayed = actual.result_digest == expected.result_digest
    return RetrievalReplayResult(
        state="replayed" if replayed else "mismatch",
        expected_input_digest=expected.input_candidates_digest,
        expected_result_digest=expected.result_digest,
        actual_input_digest=actual_input,
        actual_result_digest=actual.result_digest,
        reason_code=None if replayed else "candidate_selection_result_changed",
    )
