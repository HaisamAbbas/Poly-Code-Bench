"""Local safety checks for audit reservation and diagnostic run boundaries."""

from __future__ import annotations

from uuid import UUID

import pytest
from polycodebench_core.model_contracts import CallScope
from polycodebench_persistence.benchmark_audit import _frozen_limit
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    audit_document,
    benchmark_registry,
    call_intent,
    match_candidate,
    stage_job,
)
from polycodebench_services.runs import RunCreateRequest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint


def _run_request(**updates: object) -> dict[str, object]:
    request: dict[str, object] = {
        "campaign_id": UUID("11111111-1111-4111-8111-111111111111"),
        "config_document_id": UUID("22222222-2222-4222-8222-222222222222"),
        "task_set_id": UUID("33333333-3333-4333-8333-333333333333"),
        "model_revision_id": UUID("44444444-4444-4444-8444-444444444444"),
        "samples_per_task": 1,
        "master_seed": "7",
    }
    request.update(updates)
    return request


def test_diagnostic_runs_require_frozen_audit_identity_and_all_cost_caps() -> None:
    audit_run_id = UUID("55555555-5555-4555-8555-555555555555")
    with pytest.raises(ValidationError, match="require approved audit metadata"):
        RunCreateRequest.model_validate(
            _run_request(
                purpose="audit_diagnostic",
                max_cost_micro_usd=10,
                max_input_tokens=100,
                max_output_tokens=100,
                endpoint_registration_id=UUID("66666666-6666-4666-8666-666666666666"),
            )
        )
    with pytest.raises(ValidationError, match="require explicit cost and token caps"):
        RunCreateRequest.model_validate(
            _run_request(purpose="audit_diagnostic", audit_run_id=audit_run_id)
        )
    with pytest.raises(ValidationError, match="capped run requires token and endpoint limits"):
        RunCreateRequest.model_validate(
            _run_request(
                purpose="audit_diagnostic",
                audit_run_id=audit_run_id,
                max_cost_micro_usd=10,
            )
        )

    request = RunCreateRequest.model_validate(
        _run_request(
            purpose="audit_diagnostic",
            audit_run_id=audit_run_id,
            max_cost_micro_usd=10,
            max_input_tokens=100,
            max_output_tokens=100,
            endpoint_registration_id=UUID("66666666-6666-4666-8666-666666666666"),
        )
    )
    assert request.audit_run_id == audit_run_id


def test_non_diagnostic_run_cannot_claim_audit_metadata() -> None:
    with pytest.raises(ValidationError, match="only audit diagnostic runs"):
        RunCreateRequest.model_validate(
            _run_request(audit_run_id=UUID("55555555-5555-4555-8555-555555555555"))
        )


def test_model_call_scope_supports_audit_runs_without_replacing_attempt_scope() -> None:
    audit = CallScope(kind="audit_run", scope_id=UUID("55555555-5555-4555-8555-555555555555"))
    attempt = CallScope(kind="attempt", scope_id=UUID("77777777-7777-4777-8777-777777777777"))
    assert audit.kind == "audit_run"
    assert attempt.kind == "attempt"


def test_schema_keeps_direct_audit_scope_exclusive_and_diagnostic_attempt_metadata() -> None:
    stage_scope = next(
        constraint
        for constraint in stage_job.constraints
        if isinstance(constraint, CheckConstraint)
        and getattr(constraint, "name", "").endswith("one_scope")
    )
    assert all(
        f"{scope}_id" in str(stage_scope.sqltext)
        for scope in (
            "attempt",
            "evaluation",
            "release",
            "curation_round",
            "discovery_search",
            "audit_run",
        )
    )
    assert "diagnostic_audit_run_id" in call_intent.c
    diagnostic_scope = next(
        constraint
        for constraint in call_intent.constraints
        if isinstance(constraint, CheckConstraint)
        and getattr(constraint, "name", "").endswith("diagnostic_audit_context_scope")
    )
    assert "attempt_id IS NOT NULL" in str(diagnostic_scope.sqltext)
    successor = next(
        constraint
        for constraint in audit_document.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and getattr(constraint, "name", "").endswith("successor_same_kind")
    )
    assert tuple(column.name for column in successor.columns) == ("supersedes_id", "kind")
    audit_document_kind = next(
        constraint
        for constraint in audit_document.constraints
        if isinstance(constraint, CheckConstraint)
        and getattr(constraint, "name", "").endswith("kind")
    )
    assert "model_context" in str(audit_document_kind.sqltext)
    assert "seal_access_event" in str(audit_document_kind.sqltext)
    assert "canary_observation" in str(audit_document_kind.sqltext)
    sealed_head_index = next(
        index
        for index in audit_document.indexes
        if index.name == "uq_sealed_manifest_single_successor"
    )
    assert sealed_head_index.unique is True
    assert "sealed_manifest" in str(sealed_head_index.dialect_options["postgresql"]["where"])

    registry_status = next(
        constraint
        for constraint in benchmark_registry.constraints
        if isinstance(constraint, CheckConstraint)
        and getattr(constraint, "name", "").endswith("status")
    )
    assert all(
        status in str(registry_status.sqltext)
        for status in (
            "catalogued",
            "metadata_only",
            "importable",
            "audit_conformant",
            "blocked",
            "retired",
        )
    )
    confidence_reason = next(
        constraint
        for constraint in match_candidate.constraints
        if isinstance(constraint, CheckConstraint)
        and getattr(constraint, "name", "").endswith("confidence_null_reason")
    )
    assert "confidence IS NULL" in str(confidence_reason.sqltext)
    assert "confidence_null_reason IS NULL" in str(confidence_reason.sqltext)


def test_audit_queue_eligibility_requires_explicit_dispatch_authorization() -> None:
    repository = object.__new__(PostgresJobRepository)
    eligible = repository._scope_active_clause(
        "audit_run", UUID("55555555-5555-4555-8555-555555555555")
    )
    sql = str(eligible)
    assert "audit_run.dispatch_authorized IS true" in sql


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_query_units": 12}, 12),
        ({"max_query_units_per_plan": 12}, 12),
        ({"max_query_units": 12, "max_query_units_per_plan": 12}, 12),
        ({"max_query_units": 12, "max_query_units_per_plan": 13}, None),
        ({"max_query_units": True}, None),
        ({}, None),
    ],
)
def test_frozen_plan_limit_aliases_must_be_unambiguous(
    limits: dict[str, object], expected: int | None
) -> None:
    assert _frozen_limit(limits, "max_query_units", "max_query_units_per_plan") == expected
