"""Deterministic contracts for bounded benchmark-monitor schedules and alerts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from polycodebench_core.application_errors import InvalidState
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    AuditPlanDocument,
    EntityRef,
    ImmutableArtifactRef,
    MonitorAlertDocument,
    MonitorAlertPayload,
    MonitorPolicyDocumentV2,
    MonitorPolicyPayloadV2,
    RiskAssessmentDocumentV2,
    parse_audit_document,
)
from polycodebench_persistence.benchmark_audit import PostgresBenchmarkAuditRepository
from polycodebench_persistence.models import (
    monitor_alert_inbox,
    monitor_slot,
    monitor_slot_source,
)
from polycodebench_services.benchmark_monitoring import (
    build_monitor_alert,
    due_monitor_slots,
    full_refresh_due,
    is_monitor_scan_stale,
    monitor_policy_idempotency_key,
    next_monitor_retry,
    plan_monitor_refresh,
)
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint


def _uuid(number: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{number:012d}")


def _ref(kind: str, number: int) -> AuditDocumentRef:
    return AuditDocumentRef(
        document_id=_uuid(number),
        digest="sha256:" + "a" * 64,
        kind=kind,  # type: ignore[arg-type]
    )


def _policy(**updates: Any) -> MonitorPolicyPayloadV2:
    source_a = _ref("corpus_snapshot", 10)
    source_b = _ref("corpus_snapshot", 11)
    value: dict[str, Any] = {
        "plan_ref": _ref("audit_plan", 1),
        "benchmark_ref": _ref("benchmark_snapshot", 2),
        "policy_version": 1,
        "state": "approved",
        "owner_subject": "user:owner",
        "approver_subject": "user:reviewer",
        "approval_evidence_ref": ImmutableArtifactRef(
            artifact_id=_uuid(3),
            digest="sha256:" + "b" * 64,
            visibility="private",
            media_type="application/json",
        ),
        "task_refs": (EntityRef(entity_id=_uuid(4), entity_kind="task_version"),),
        "source_rate_limits": (
            {
                "source_ref": source_a,
                "max_query_units_per_local_day": 100,
            },
            {
                "source_ref": source_b,
                "max_query_units_per_local_day": 100,
            },
        ),
        "cadence": "daily",
        "timezone": "UTC",
        "local_time": "09:30",
        "weekday": None,
        "full_refresh_every_days": 30,
        "stale_after_hours": 192,
        "query_units_per_task_source": 1,
        "max_query_units_per_slot": 4,
        "max_storage_bytes_per_slot": 10_000,
        "max_cost_micro_usd_per_slot": 25_000,
        "max_retries": 1,
        "max_catch_up_slots": 3,
        "in_app_recipients": ("user:owner",),
        "external_delivery": "disabled",
        "max_diagnostic_model_calls": 0,
        "max_diagnostic_cost_micro_usd": 0,
    }
    value.update(updates)
    return MonitorPolicyPayloadV2.model_validate(value)


def test_policy_freezes_scope_budgets_and_denies_external_or_model_routes() -> None:
    policy = _policy()
    assert policy.external_delivery == "disabled"
    assert policy.max_diagnostic_model_calls == 0
    assert policy.max_query_units_per_slot == 4

    with pytest.raises(ValidationError, match="owner"):
        _policy(approver_subject="user:owner")
    with pytest.raises(ValidationError, match="refresh"):
        _policy(max_query_units_per_slot=3)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _policy(alert_routes=("email:owner@example.org",))
    with pytest.raises(ValidationError, match="disabled"):
        _policy(external_delivery="email")


def test_monitor_policy_schema_v2_parses_as_its_versioned_contract() -> None:
    policy = _policy()
    parsed = parse_audit_document(
        json.dumps(
            {
                "id": str(uuid4()),
                "kind": "monitor_policy",
                "schema_version": 2,
                "payload": policy.model_dump(mode="json"),
                "metadata": {
                    "created_at": "2026-10-08T12:00:00Z",
                    "timestamp_precision": "second",
                    "actor": "monitor-policy-test",
                    "trace_id": None,
                    "row_version": 0,
                },
            }
        ).encode()
    )
    assert isinstance(parsed, MonitorPolicyDocumentV2)


def test_persisted_risk_alerts_must_stay_inside_the_approved_plan_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_payload = _policy()
    policy_ref = _ref("monitor_policy", 40)
    risk_policy_ref = _ref("risk_policy", 41)
    plan = AuditPlanDocument.model_construct(
        id=policy_payload.plan_ref.document_id,
        payload=SimpleNamespace(
            benchmark_ref=policy_payload.benchmark_ref,
            policy=risk_policy_ref,
            model_context=None,
        ),
    )
    policy_document = MonitorPolicyDocumentV2.model_construct(
        id=policy_ref.document_id,
        payload=policy_payload,
    )
    documents: dict[UUID, Any] = {
        policy_ref.document_id: policy_document,
        policy_payload.plan_ref.document_id: plan,
    }

    def lookup_document(cls: type[Any], connection: object, ref: AuditDocumentRef) -> Any:
        del cls, connection
        return documents[ref.document_id]

    monkeypatch.setattr(
        PostgresBenchmarkAuditRepository,
        "_document_by_ref",
        classmethod(lookup_document),
    )

    def build_assessment(
        *, document_id: int, task_ref: EntityRef, score: str, supersedes_id: UUID | None
    ) -> RiskAssessmentDocumentV2:
        return RiskAssessmentDocumentV2.model_construct(
            id=_uuid(document_id),
            supersedes_id=supersedes_id,
            payload=SimpleNamespace(
                task_ref=task_ref,
                plan_ref=policy_payload.plan_ref,
                context_ref=None,
                policy_ref=risk_policy_ref,
                calibration_state="validated",
                observed_index=SimpleNamespace(value=score),
                accepted_evidence=(),
            ),
        )

    approved_task = policy_payload.task_refs[0]
    outside_task = EntityRef(entity_id=_uuid(42), entity_kind="task_version")
    previous = build_assessment(
        document_id=50, task_ref=approved_task, score="10.000000", supersedes_id=None
    )

    def validate_task(task_ref: EntityRef) -> None:
        current = build_assessment(
            document_id=51, task_ref=task_ref, score="20.000000", supersedes_id=previous.id
        )
        alert_payload = build_monitor_alert(
            policy_ref=policy_ref,
            alert_type="risk_increase",
            recipients=policy_payload.in_app_recipients,
            occurred_at="2026-10-08T12:00:00Z",
            task_ref=task_ref,
            previous_assessment_ref=_ref("risk_assessment", 50),
            assessment_ref=_ref("risk_assessment", 51),
        )
        alert = MonitorAlertDocument.model_construct(payload=alert_payload)
        documents[previous.id] = previous
        documents[current.id] = current
        PostgresBenchmarkAuditRepository._validate_monitor_document(object(), alert)

    validate_task(approved_task)
    with pytest.raises(InvalidState, match="frozen plan/task scope"):
        validate_task(outside_task)


def test_daily_schedule_resolves_dst_gap_forward_and_fold_once() -> None:
    policy = _policy(timezone="America/New_York", local_time="02:30")
    spring = due_monitor_slots(
        policy,
        now=datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
        last_accounted_slot=None,
    )
    assert len(spring) == 1
    assert spring[0].slot_key == "D:2026-03-08"
    assert spring[0].scheduled_at == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)

    fall_policy = _policy(timezone="America/New_York", local_time="01:30")
    fall = due_monitor_slots(
        fall_policy,
        now=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
        last_accounted_slot=None,
    )
    assert len(fall) == 1
    assert fall[0].scheduled_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_catchup_is_bounded_and_zero_cap_keeps_only_the_current_tick() -> None:
    weekly = _policy(
        cadence="weekly",
        timezone="UTC",
        local_time="00:00",
        weekday=0,
        max_catch_up_slots=2,
    )
    slots = due_monitor_slots(
        weekly,
        now=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        last_accounted_slot=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
    )
    assert [slot.slot_key for slot in slots] == [
        "W:2026-09-21",
        "W:2026-09-28",
        "W:2026-10-05",
    ]
    assert [slot.should_dispatch for slot in slots] == [True, True, True]
    assert slots[0].missed_slots_before == 2

    no_catchup = _policy(max_catch_up_slots=0)
    missed = due_monitor_slots(
        no_catchup,
        now=datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
        last_accounted_slot=datetime(2026, 10, 4, 9, 30, tzinfo=UTC),
    )
    assert len(missed) == 1
    assert missed[0].should_dispatch
    assert missed[0].slot_key == "D:2026-10-08"
    assert missed[0].missed_slots_before == 3


def test_refreshes_target_only_approved_changed_sources_and_reserve_retries() -> None:
    policy = _policy()
    source_a, source_b = (item.source_ref for item in policy.source_rate_limits)
    incremental = plan_monitor_refresh(
        policy,
        changed_source_refs=(source_a,),
        full_refresh=False,
    )
    assert incremental.source_refs == (source_a,)
    assert incremental.query_units == 1
    assert incremental.retry_reserve_units == 2

    full = plan_monitor_refresh(policy, changed_source_refs=(), full_refresh=True)
    assert full.source_refs == (source_a, source_b)
    assert full.query_units == 2
    assert full.retry_reserve_units == 4

    with pytest.raises(ValueError, match="scope"):
        plan_monitor_refresh(
            policy,
            changed_source_refs=(_ref("corpus_snapshot", 12),),
            full_refresh=False,
        )


def test_retry_staleness_and_run_idempotency_are_bounded() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    policy = _policy()
    first_retry = next_monitor_retry(
        policy=policy,
        completed_attempts=1,
        now=now,
        local_day=date(2026, 10, 8),
    )
    assert first_retry.action == "retry"
    assert first_retry.retry_at == datetime(2026, 10, 8, 12, 0, 30, tzinfo=UTC)
    final = next_monitor_retry(
        policy=policy,
        completed_attempts=3,
        now=now,
        local_day=date(2026, 10, 8),
    )
    assert final.action == "terminal_failed"
    assert final.retry_at is None
    before_midnight = datetime(2026, 10, 8, 23, 59, 40, tzinfo=UTC)
    crossing_day = next_monitor_retry(
        policy=policy,
        completed_attempts=1,
        now=before_midnight,
        local_day=date(2026, 10, 8),
    )
    assert crossing_day.action == "terminal_failed"

    assert is_monitor_scan_stale(last_complete_at=None, now=now, stale_after_hours=24)
    assert not is_monitor_scan_stale(
        last_complete_at=now.replace(hour=0), now=now, stale_after_hours=24
    )
    assert is_monitor_scan_stale(
        last_complete_at=now.replace(hour=0), now=now, stale_after_hours=11
    )
    assert full_refresh_due(last_full_refresh_at=None, now=now, interval_days=30)
    assert not full_refresh_due(last_full_refresh_at=now, now=now, interval_days=30)
    assert full_refresh_due(
        last_full_refresh_at=now, now=now, interval_days=30, owner_requested=True
    )
    key = monitor_policy_idempotency_key(_uuid(5), "D:2026-10-08")
    assert key == "monitor:10000000-0000-4000-8000-000000000005:D:2026-10-08"


def test_alerts_are_reference_only_typed_and_deduplicated() -> None:
    policy_ref = _ref("monitor_policy", 20)
    source_ref = _ref("corpus_snapshot", 21)
    coverage_ref = _ref("coverage_manifest", 22)
    alert = build_monitor_alert(
        policy_ref=policy_ref,
        alert_type="source_outage",
        recipients=("user:owner",),
        occurred_at="2026-10-08T12:00:00Z",
        source_ref=source_ref,
        coverage_ref=coverage_ref,
    )
    assert alert.dedupe_key.startswith("sha256:")
    assert "body" not in alert.model_dump()
    tampered = alert.model_dump(mode="json")
    tampered["dedupe_key"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="dedupe key"):
        MonitorAlertPayload.model_validate_json(json.dumps(tampered))
    with pytest.raises(ValidationError, match="source outage"):
        build_monitor_alert(
            policy_ref=policy_ref,
            alert_type="source_outage",
            recipients=("user:owner",),
            occurred_at="2026-10-08T12:00:00Z",
            source_ref=_ref("audit_plan", 23),
            coverage_ref=coverage_ref,
        )


def test_policy_discontinuity_alert_names_scope_corpus_and_method_breaks() -> None:
    current = _ref("monitor_policy", 24)
    previous = _ref("monitor_policy", 25)
    with pytest.raises(ValidationError, match="reason codes"):
        build_monitor_alert(
            policy_ref=current,
            alert_type="policy_discontinuity",
            recipients=("user:owner",),
            occurred_at="2026-10-08T12:00:00Z",
            previous_policy_ref=previous,
        )
    alert = build_monitor_alert(
        policy_ref=current,
        alert_type="policy_discontinuity",
        recipients=("user:owner",),
        occurred_at="2026-10-08T12:00:00Z",
        previous_policy_ref=previous,
        discontinuity_reasons=("corpus_snapshot", "method_version", "policy_scope"),
    )
    assert alert.discontinuity_reasons == (
        "corpus_snapshot",
        "method_version",
        "policy_scope",
    )


def test_new_exposure_alert_contract_requires_predecessor_and_successor() -> None:
    with pytest.raises(ValidationError, match="previous_assessment_ref"):
        build_monitor_alert(
            policy_ref=_ref("monitor_policy", 30),
            alert_type="new_exposure",
            recipients=("user:owner",),
            occurred_at="2026-10-08T12:00:00Z",
            task_ref=EntityRef(entity_id=_uuid(31), entity_kind="task_version"),
            evidence_ref=_ref("match_evidence", 32),
        )


def test_monitor_tables_enforce_tick_source_and_inbox_deduplication() -> None:
    def unique_constraints(table: Any) -> set[tuple[str, ...]]:
        return {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }

    assert ("policy_document_id", "slot_key") in unique_constraints(monitor_slot)
    assert ("slot_id", "source_document_id") in unique_constraints(monitor_slot_source)
    assert ("alert_document_id", "recipient_subject") in unique_constraints(monitor_alert_inbox)
    default = monitor_slot.c.dispatch_authorized.server_default
    assert default is not None
