"""Bounded schedule, refresh, retry, and alert planning for benchmark monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    EntityRef,
    MonitorAlertPayload,
    MonitorAlertType,
    MonitorDiscontinuityReason,
    MonitorPolicyPayloadV2,
    compute_monitor_alert_dedupe_key,
)
from polycodebench_core.monitor_schedule import monitor_retry_at, monitor_slot_identity


@dataclass(frozen=True)
class DueMonitorSlot:
    slot_key: str
    scheduled_at: datetime
    should_dispatch: bool
    missed_slots_before: int


@dataclass(frozen=True)
class MonitorRefreshPlan:
    refresh_kind: Literal["incremental", "full"]
    source_refs: tuple[AuditDocumentRef, ...]
    query_units: int
    retry_reserve_units: int


@dataclass(frozen=True)
class MonitorRetry:
    action: Literal["retry", "terminal_failed"]
    attempt_number: int
    retry_at: datetime | None


def _slot_for_date(policy: MonitorPolicyPayloadV2, local_date: date) -> DueMonitorSlot | None:
    if policy.cadence == "weekly" and local_date.weekday() != policy.weekday:
        return None
    slot_key, scheduled_at = monitor_slot_identity(policy, local_date)
    return DueMonitorSlot(
        slot_key=slot_key,
        scheduled_at=scheduled_at,
        should_dispatch=True,
        missed_slots_before=0,
    )


def due_monitor_slots(
    policy: MonitorPolicyPayloadV2,
    *,
    now: datetime,
    last_accounted_slot: datetime | None,
) -> tuple[DueMonitorSlot, ...]:
    """Return the newest due tick plus only the allowed older catch-up slots."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("monitor clock must be timezone-aware")
    if last_accounted_slot is not None and (
        last_accounted_slot.tzinfo is None or last_accounted_slot.utcoffset() is None
    ):
        raise ValueError("last accounted monitor slot must be timezone-aware")
    zone = ZoneInfo(policy.timezone)
    local_today = now.astimezone(zone).date()
    if last_accounted_slot is None:
        start_date = local_today
    else:
        local_last = last_accounted_slot.astimezone(zone).date()
        start_date = local_last

    # Iterate only the newest 370 calendar dates. Older daily/weekly ticks are counted below.
    lower_bound = local_today - timedelta(days=369)
    oldest_date = max(start_date, lower_bound)
    candidates: list[DueMonitorSlot] = []
    current_date = oldest_date
    while current_date <= local_today:
        slot = _slot_for_date(policy, current_date)
        if (
            slot is not None
            and slot.scheduled_at <= now.astimezone(UTC)
            and (
                last_accounted_slot is None
                or slot.scheduled_at > last_accounted_slot.astimezone(UTC)
            )
        ):
            candidates.append(slot)
        current_date += timedelta(days=1)

    older_due = 0
    if start_date < lower_bound:
        days = (lower_bound - start_date).days
        if policy.cadence == "daily":
            older_due = max(0, days - 1)
        else:
            weekday = policy.weekday
            if weekday is None:
                raise ValueError("weekly monitor policy must define a weekday")
            first = start_date + timedelta(days=1)
            delta = (weekday - first.weekday()) % 7
            first_scheduled = first + timedelta(days=delta)
            if first_scheduled < lower_bound:
                older_due = 1 + (lower_bound - timedelta(days=1) - first_scheduled).days // 7

    total = older_due + len(candidates)
    if total == 0:
        return ()
    # The newest due slot is the current scheduled tick; the frozen cap applies to older backlog.
    dispatch_count = min(total, policy.max_catch_up_slots + 1)
    retained_count = dispatch_count
    retained = candidates[-retained_count:]
    if not retained:
        # The bounded history window may not include any old slots. Preserve the latest due key.
        date_for_latest = local_today
        if policy.cadence == "weekly" and date_for_latest.weekday() != policy.weekday:
            weekday = policy.weekday
            if weekday is None:
                raise ValueError("weekly monitor policy must define a weekday")
            date_for_latest -= timedelta(days=(date_for_latest.weekday() - weekday) % 7)
        latest = _slot_for_date(policy, date_for_latest)
        if latest is None:
            return ()
        retained = [latest]
    skipped = max(0, total - dispatch_count)
    result: list[DueMonitorSlot] = []
    for index, slot in enumerate(retained):
        result.append(
            DueMonitorSlot(
                slot_key=slot.slot_key,
                scheduled_at=slot.scheduled_at,
                should_dispatch=True,
                missed_slots_before=skipped if index == 0 else 0,
            )
        )
    return tuple(result)


def plan_monitor_refresh(
    policy: MonitorPolicyPayloadV2,
    *,
    changed_source_refs: tuple[AuditDocumentRef, ...],
    full_refresh: bool,
) -> MonitorRefreshPlan:
    """Select only policy-approved sources and reserve worst-case bounded retry units."""
    allowed = tuple(item.source_ref for item in policy.source_rate_limits)
    changed = set(changed_source_refs)
    if any(ref.kind != "corpus_snapshot" for ref in changed):
        raise ValueError("changed monitor sources must be corpus snapshots")
    if not changed <= set(allowed):
        raise ValueError("changed corpus snapshot falls outside the approved monitor source scope")
    sources = allowed if full_refresh else tuple(ref for ref in allowed if ref in changed)
    attempts = policy.max_retries + 1
    units_per_attempt = len(policy.task_refs) * len(sources) * policy.query_units_per_task_source
    query_units = units_per_attempt * attempts
    if query_units > policy.max_query_units_per_slot:
        raise ValueError("monitor refresh and its bounded retries exceed the frozen slot budget")
    return MonitorRefreshPlan(
        refresh_kind="full" if full_refresh else "incremental",
        source_refs=sources,
        query_units=units_per_attempt,
        retry_reserve_units=query_units,
    )


def next_monitor_retry(
    *,
    policy: MonitorPolicyPayloadV2,
    completed_attempts: int,
    now: datetime,
    local_day: date,
) -> MonitorRetry:
    """Apply bounded exponential retry; the final failed attempt stays terminal."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("monitor retry clock must be timezone-aware")
    if type(completed_attempts) is not int or completed_attempts < 1:
        raise ValueError("completed_attempts must be a positive integer")
    retry_at = monitor_retry_at(
        completed_attempts=completed_attempts,
        max_retries=policy.max_retries,
        now=now,
        timezone_name=policy.timezone,
        local_day=local_day,
    )
    if retry_at is None:
        return MonitorRetry("terminal_failed", completed_attempts, None)
    return MonitorRetry("retry", completed_attempts + 1, retry_at)


def build_monitor_alert(
    *,
    policy_ref: AuditDocumentRef,
    alert_type: MonitorAlertType,
    recipients: tuple[str, ...],
    occurred_at: str,
    task_ref: EntityRef | None = None,
    source_ref: AuditDocumentRef | None = None,
    evidence_ref: AuditDocumentRef | None = None,
    previous_assessment_ref: AuditDocumentRef | None = None,
    assessment_ref: AuditDocumentRef | None = None,
    coverage_ref: AuditDocumentRef | None = None,
    previous_policy_ref: AuditDocumentRef | None = None,
    sealed_manifest_ref: AuditDocumentRef | None = None,
    discontinuity_reasons: tuple[MonitorDiscontinuityReason, ...] = (),
) -> MonitorAlertPayload:
    """Build a reference-only alert; presentation text is resolved from fixed UI templates."""
    key = compute_monitor_alert_dedupe_key(
        policy_ref=policy_ref,
        alert_type=alert_type,
        task_ref=task_ref,
        source_ref=source_ref,
        evidence_ref=evidence_ref,
        previous_assessment_ref=previous_assessment_ref,
        assessment_ref=assessment_ref,
        coverage_ref=coverage_ref,
        previous_policy_ref=previous_policy_ref,
        sealed_manifest_ref=sealed_manifest_ref,
        discontinuity_reasons=discontinuity_reasons,
    )
    return MonitorAlertPayload(
        policy_ref=policy_ref,
        alert_type=alert_type,
        dedupe_key=key,
        recipient_subjects=recipients,
        occurred_at=occurred_at,
        task_ref=task_ref,
        source_ref=source_ref,
        evidence_ref=evidence_ref,
        previous_assessment_ref=previous_assessment_ref,
        assessment_ref=assessment_ref,
        coverage_ref=coverage_ref,
        previous_policy_ref=previous_policy_ref,
        sealed_manifest_ref=sealed_manifest_ref,
        discontinuity_reasons=discontinuity_reasons,
    )


def monitor_policy_idempotency_key(policy_id: UUID, slot_key: str) -> str:
    """Return bounded ASCII audit-run identity for a monitor tick."""
    if not slot_key.isascii() or not slot_key or len(slot_key) > 32:
        raise ValueError("monitor slot key must be bounded ASCII")
    return f"monitor:{policy_id}:{slot_key}"


def is_monitor_scan_stale(
    *, last_complete_at: datetime | None, now: datetime, stale_after_hours: int
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("staleness clock must be timezone-aware")
    if type(stale_after_hours) is not int or not 1 <= stale_after_hours <= 8_760:
        raise ValueError("staleness threshold must be between one hour and one year")
    if last_complete_at is None:
        return True
    if last_complete_at.tzinfo is None or last_complete_at.utcoffset() is None:
        raise ValueError("last complete monitor scan must be timezone-aware")
    return now.astimezone(UTC) - last_complete_at.astimezone(UTC) > timedelta(
        hours=stale_after_hours
    )


def full_refresh_due(
    *,
    last_full_refresh_at: datetime | None,
    now: datetime,
    interval_days: int,
    owner_requested: bool = False,
) -> bool:
    """Require a finite full-scope refresh at the frozen interval or owner request."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("full-refresh clock must be timezone-aware")
    if type(interval_days) is not int or not 1 <= interval_days <= 366:
        raise ValueError("full-refresh interval must be between one and 366 days")
    if last_full_refresh_at is None:
        return True
    if last_full_refresh_at.tzinfo is None or last_full_refresh_at.utcoffset() is None:
        raise ValueError("last full refresh must be timezone-aware")
    return owner_requested or now.astimezone(UTC) - last_full_refresh_at.astimezone(
        UTC
    ) >= timedelta(days=interval_days)
