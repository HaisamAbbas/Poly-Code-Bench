from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from polycodebench_core.benchmark_audit_documents import AuditDocumentRef, AuditKind, EntityRef
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.retrieval import (
    RetrievalCacheIdentity,
    RetrievalCandidateHit,
    RetrievalIndexPin,
    RetrievalPlan,
    RetrievalPlanRequest,
    RetrievalQueryOutcome,
    RetrievalQueryUnit,
    RetrievalSourcePin,
    RetrievalStage,
    SourceGroupSlug,
)
from polycodebench_services.benchmark_audit_catalog import load_audit_catalog
from polycodebench_services.retrieval import (
    build_retrieval_coverage,
    build_retrieval_plan,
    initial_retrieval_coverage,
    retrieval_cache_key,
    select_retrieval_candidates,
    verify_retrieval_replay,
)
from pydantic import ValidationError

ROOT = Path(__file__).parents[1]
BUNDLE = load_audit_catalog(ROOT / "config" / "benchmark-audit")
DIGEST = "sha256:" + "a" * 64
SOURCE_GROUPS: tuple[SourceGroupSlug, ...] = (
    "common-crawl",
    "github",
    "hugging-face",
    "arxiv",
    "stack-exchange",
    "wikipedia",
    "benchmark-repositories",
    "other-public-datasets",
)


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _entity(number: int, kind: str) -> EntityRef:
    return EntityRef(entity_id=_uuid(number), entity_kind=kind, digest=DIGEST)


def _audit_ref(kind: AuditKind, number: int) -> AuditDocumentRef:
    return AuditDocumentRef(document_id=_uuid(number), digest=DIGEST, kind=kind)


def _request(
    source_groups: tuple[SourceGroupSlug, ...] = ("github",),
    *,
    component_count: int = 1,
    source_pins: tuple[RetrievalSourcePin, ...] = (),
    seed: str = "17",
) -> RetrievalPlanRequest:
    return RetrievalPlanRequest.model_validate(
        {
            "audit_plan_ref": _audit_ref("audit_plan", 1),
            "task_ref": _entity(2, "task_version"),
            "component_refs": tuple(
                _entity(10 + index, "audit_component") for index in range(component_count)
            ),
            "source_groups": source_groups,
            "source_pins": source_pins,
            "seed": seed,
        }
    )


def _hypothetical_plan(
    source_groups: tuple[SourceGroupSlug, ...] = ("github",), *, seed: str = "17"
) -> RetrievalPlan:
    """A schema-valid synthetic plan for pure selection tests, not source approval evidence."""
    request = _request(source_groups, seed=seed)
    blocked_plan = build_retrieval_plan(BUNDLE, request)
    values = blocked_plan.model_dump(mode="python")
    for index, pin in enumerate(values["source_pins"]):
        group = source_groups[index]
        pin.update(
            {
                "source_revision": f"fixture-revision-{group}",
                "snapshot_ref": _audit_ref("corpus_snapshot", 100 + index).model_dump(
                    mode="python"
                ),
                "rights_ref": _audit_ref("corpus_snapshot", 200 + index).model_dump(mode="python"),
                "privacy_scope_digest": DIGEST,
                "index_pins": (
                    {"stage": "exact_normalized", "index_digest": DIGEST},
                    {"stage": "lexical_code", "index_digest": DIGEST},
                ),
                "state": "provided",
                "blocker_codes": (),
            }
        )
    for unit in values["query_units"]:
        if unit["stage"] in {"exact_normalized", "lexical_code"}:
            unit["state"] = "planned"
            unit["reason_codes"] = ()
    values["state"] = "planned"
    return RetrievalPlan.model_validate(values)


def _unit(
    plan: RetrievalPlan, source_group: SourceGroupSlug, stage: RetrievalStage
) -> RetrievalQueryUnit:
    return next(
        unit
        for unit in plan.query_units
        if unit.source_group == source_group and unit.stage == stage
    )


def _hit(
    plan: RetrievalPlan,
    *,
    candidate_number: int,
    source_group: SourceGroupSlug = "github",
    stage: RetrievalStage = "exact_normalized",
    rank: int = 1,
    source_document_digest: str | None = None,
    candidate_component_digest: str | None = None,
) -> RetrievalCandidateHit:
    component = plan.component_refs[0]
    source_pin = next(pin for pin in plan.source_pins if pin.source_group == source_group)
    assert source_pin.source_revision is not None
    return RetrievalCandidateHit(
        task_ref=plan.task_ref,
        component_ref=component,
        query_unit_digest=_unit(plan, source_group, stage).unit_digest,
        source_group=source_group,
        source_revision=source_pin.source_revision,
        source_document_digest=source_document_digest or f"sha256:{candidate_number:064x}",
        candidate_component_digest=candidate_component_digest
        or f"sha256:{candidate_number + 1000:064x}",
        stage=stage,
        rank=rank,
    )


def _cache_identity(
    *,
    tenant_id: UUID | None = None,
    permission_digest: str = DIGEST,
    corpus_digest: str = DIGEST,
    method_digest: str = DIGEST,
    selection_seed: str = "17",
    max_candidates_per_source: int = 20,
    max_candidates_total: int = 100,
) -> RetrievalCacheIdentity:
    return RetrievalCacheIdentity(
        tenant_id=tenant_id or _uuid(300),
        permission_scope_digest=permission_digest,
        task_digest=DIGEST,
        component_digest=DIGEST,
        source_group="github",
        corpus_snapshot_digest=corpus_digest,
        index_digest=DIGEST,
        method_digest=method_digest,
        stage="exact_normalized",
        query_digest=DIGEST,
        selection_seed=selection_seed,
        max_candidates_per_source=max_candidates_per_source,
        max_candidates_total=max_candidates_total,
    )


def test_retrieval_plan_freezes_stage_order_and_every_query_coverage_unit() -> None:
    plan = build_retrieval_plan(
        BUNDLE,
        _request(("common-crawl", "github"), component_count=2),
    )

    assert plan.state == "blocked"
    assert plan.dispatch_allowed is False
    assert plan.max_candidates_per_source == 20
    assert plan.max_candidates_total == 100
    assert tuple(pin.stage for pin in plan.method_pins) == (
        "exact_normalized",
        "lexical_code",
        "semantic",
    )
    assert len(plan.query_units) == 2 * 2 * 3
    assert all(pin.state == "blocked" for pin in plan.source_pins)
    assert {unit.state for unit in plan.query_units} == {"blocked", "unsupported"}
    lexical = next(pin for pin in plan.method_pins if pin.stage == "lexical_code")
    semantic = next(pin for pin in plan.method_pins if pin.stage == "semantic")
    assert lexical.state == "partial"
    assert lexical.supported_features == ("token_shingles",)
    assert lexical.unsupported_features == ("code_structure",)
    assert semantic.state == "unsupported"
    assert "approved_embedding_index_unavailable" in semantic.reason_codes

    coverage = initial_retrieval_coverage(plan)
    assert coverage.state == "blocked"
    assert len(coverage.outcomes) == len(plan.query_units)
    assert not any(item.state == "no_match" for item in coverage.outcomes)


def test_query_unit_identity_binds_component_fingerprint_digest() -> None:
    original_request = _request()
    changed_component = original_request.component_refs[0].model_copy(
        update={"digest": "sha256:" + "b" * 64}
    )
    changed_request = original_request.model_copy(update={"component_refs": (changed_component,)})

    original = build_retrieval_plan(BUNDLE, original_request)
    changed = build_retrieval_plan(BUNDLE, changed_request)

    assert (
        original.query_units[0].component_ref.entity_id
        == changed.query_units[0].component_ref.entity_id
    )
    assert original.query_units[0].unit_digest != changed.query_units[0].unit_digest


def test_source_approval_metadata_and_index_references_do_not_unlock_live_queries() -> None:
    source_pin = RetrievalSourcePin(
        source_group="github",
        source_revision="commit-abcdef",
        snapshot_ref=_audit_ref("corpus_snapshot", 100),
        rights_ref=_audit_ref("corpus_snapshot", 101),
        privacy_scope_digest=DIGEST,
        index_pins=(
            RetrievalIndexPin(stage="exact_normalized", index_digest=DIGEST),
            RetrievalIndexPin(stage="lexical_code", index_digest=DIGEST),
        ),
        state="provided",
        blocker_codes=(),
    )
    plan = build_retrieval_plan(BUNDLE, _request(source_pins=(source_pin,)))

    assert plan.source_pins[0].state == "blocked"
    assert "source_authorization_not_approved" in plan.source_pins[0].blocker_codes
    assert "source_artifact_verifier_unavailable" in plan.source_pins[0].blocker_codes
    assert "retrieval_index_runtime_not_implemented" in plan.source_pins[0].blocker_codes
    assert all(unit.state != "planned" for unit in plan.query_units)


def test_retrieval_plan_rejects_duplicate_sources_and_malformed_source_pins() -> None:
    with pytest.raises(ValidationError, match="source groups must be unique"):
        _request(("github", "github"))
    with pytest.raises(ValidationError, match="provided retrieval sources"):
        RetrievalSourcePin(
            source_group="github",
            source_revision=None,
            snapshot_ref=None,
            rights_ref=None,
            privacy_scope_digest=None,
            index_pins=(),
            state="provided",
            blocker_codes=(),
        )


def test_candidate_selection_deduplicates_stages_and_retains_per_source_truncation() -> None:
    plan = _hypothetical_plan()
    candidates = [_hit(plan, candidate_number=index, rank=index + 1) for index in range(1, 26)]
    candidates.extend(
        _hit(
            plan,
            candidate_number=index,
            stage="lexical_code",
            rank=1,
        )
        for index in range(1, 26)
    )

    result = select_retrieval_candidates(plan, tuple(candidates))

    assert result.observed_hits == 50
    assert result.unique_candidates == 25
    assert len(result.selected_candidates) == 20
    assert result.source_limits[0].unique_candidates == 25
    assert result.source_limits[0].discarded_candidates == 5
    assert all(item.stage == "exact_normalized" for item in result.selected_candidates)


def test_total_candidate_cap_is_deterministic_and_bounded_at_one_hundred() -> None:
    plan = _hypothetical_plan(SOURCE_GROUPS[:6])
    hits = tuple(
        _hit(plan, candidate_number=index, source_group=source, rank=1)
        for source_index, source in enumerate(SOURCE_GROUPS[:6], start=1)
        for index in range(source_index * 100, source_index * 100 + 20)
    )

    result = select_retrieval_candidates(plan, hits)
    replayed_order = select_retrieval_candidates(plan, tuple(reversed(hits)))

    assert result.unique_candidates == 120
    assert len(result.selected_candidates) == 100
    assert result.total_cap_discarded == 20
    assert all(item.retained_candidates == 20 for item in result.source_limits)
    assert replayed_order.result_digest == result.result_digest
    assert replayed_order.input_candidates_digest == result.input_candidates_digest


def test_candidate_selection_rejects_hits_for_blocked_or_changed_scope() -> None:
    blocked_plan = build_retrieval_plan(BUNDLE, _request())
    hit = RetrievalCandidateHit(
        task_ref=blocked_plan.task_ref,
        component_ref=blocked_plan.component_refs[0],
        query_unit_digest=_unit(blocked_plan, "github", "exact_normalized").unit_digest,
        source_group="github",
        source_revision="unknown",
        source_document_digest=DIGEST,
        candidate_component_digest=DIGEST,
        stage="exact_normalized",
        rank=1,
    )
    with pytest.raises(ValueError, match="outside the frozen"):
        select_retrieval_candidates(blocked_plan, (hit,))

    plan = _hypothetical_plan()
    stale_revision = _hit(plan, candidate_number=1).model_copy(
        update={"source_revision": "different-commit"}
    )
    with pytest.raises(ValueError, match="outside the frozen"):
        select_retrieval_candidates(plan, (stale_revision,))


def test_coverage_distinguishes_no_match_from_failed_and_unsupported_sources() -> None:
    plan = _hypothetical_plan()
    outcomes = (
        RetrievalQueryOutcome(
            unit_digest=_unit(plan, "github", "exact_normalized").unit_digest,
            state="no_match",
            queries_attempted=1,
            candidates_observed=0,
            truncated_candidates=0,
            candidates_digest=canonical_digest([]),
            reason_code=None,
        ),
        RetrievalQueryOutcome(
            unit_digest=_unit(plan, "github", "lexical_code").unit_digest,
            state="failed",
            queries_attempted=1,
            candidates_observed=0,
            truncated_candidates=0,
            candidates_digest=None,
            reason_code="source_timeout",
        ),
        RetrievalQueryOutcome(
            unit_digest=_unit(plan, "github", "semantic").unit_digest,
            state="unsupported",
            queries_attempted=0,
            candidates_observed=0,
            truncated_candidates=0,
            candidates_digest=None,
            reason_code="approved_embedding_index_unavailable",
        ),
    )

    report = build_retrieval_coverage(plan, outcomes)

    assert report.state == "partial"
    assert {item.state for item in report.outcomes} == {"no_match", "failed", "unsupported"}
    with pytest.raises(ValidationError, match="no-match requires"):
        RetrievalQueryOutcome(
            unit_digest=DIGEST,
            state="no_match",
            queries_attempted=0,
            candidates_observed=0,
            truncated_candidates=0,
            candidates_digest=DIGEST,
            reason_code=None,
        )
    with pytest.raises(ValidationError, match="no-match requires"):
        RetrievalQueryOutcome(
            unit_digest=DIGEST,
            state="no_match",
            queries_attempted=1,
            candidates_observed=0,
            truncated_candidates=0,
            candidates_digest=DIGEST,
            reason_code="source_timeout",
        )
    with pytest.raises(ValidationError, match="truncated units require observed candidates"):
        RetrievalQueryOutcome(
            unit_digest=DIGEST,
            state="truncated",
            queries_attempted=1,
            candidates_observed=1,
            truncated_candidates=1,
            candidates_digest=None,
            reason_code="candidate_cap_reached",
        )


def test_coverage_reconciles_all_planned_units_and_rejects_clean_omissions() -> None:
    plan = build_retrieval_plan(BUNDLE, _request(("github", "arxiv")))
    outcomes = list(initial_retrieval_coverage(plan).outcomes)
    with pytest.raises(ValueError, match="blocked retrieval units"):
        build_retrieval_coverage(
            plan,
            tuple(
                outcome.model_copy(update={"state": "no_match", "queries_attempted": 1})
                if plan.query_units[index].state == "blocked"
                else outcome
                for index, outcome in enumerate(outcomes)
            ),
        )
    with pytest.raises(ValueError, match="every planned query unit"):
        build_retrieval_coverage(plan, tuple(outcomes[:-1]))


def test_cache_identity_isolated_by_tenant_scope_corpus_method_and_selection() -> None:
    identity = _cache_identity()
    base = retrieval_cache_key(identity)

    assert retrieval_cache_key(identity) == base
    assert retrieval_cache_key(_cache_identity(tenant_id=_uuid(301))) != base
    assert retrieval_cache_key(_cache_identity(permission_digest="sha256:" + "b" * 64)) != base
    assert retrieval_cache_key(_cache_identity(corpus_digest="sha256:" + "b" * 64)) != base
    assert retrieval_cache_key(_cache_identity(method_digest="sha256:" + "b" * 64)) != base
    assert retrieval_cache_key(_cache_identity(selection_seed="18")) != base
    assert retrieval_cache_key(_cache_identity(max_candidates_per_source=19)) != base
    assert retrieval_cache_key(_cache_identity(max_candidates_total=99)) != base


def test_replay_uses_stored_hits_and_reports_missing_or_changed_evidence() -> None:
    plan = _hypothetical_plan()
    hits = (_hit(plan, candidate_number=12), _hit(plan, candidate_number=11))
    selected = select_retrieval_candidates(plan, hits)

    same = verify_retrieval_replay(plan, selected, hits)
    missing = verify_retrieval_replay(plan, selected, None)
    changed = verify_retrieval_replay(
        plan,
        selected,
        (hits[0].model_copy(update={"rank": 9}), hits[1]),
    )

    assert same.state == "replayed"
    assert missing.state == "missing_evidence"
    assert missing.reason_code == "stored_candidate_hits_missing"
    assert changed.state == "mismatch"
    assert changed.reason_code == "stored_candidate_input_digest_changed"


def test_candidate_selection_rejects_more_than_bounded_input_size() -> None:
    plan = _hypothetical_plan()
    one = _hit(plan, candidate_number=1)
    with pytest.raises(ValueError, match="bounded selection input limit"):
        select_retrieval_candidates(plan, (one,) * 100_001)
