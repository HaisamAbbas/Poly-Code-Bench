"""Canonical local-time resolution for frozen benchmark-monitor schedules."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from polycodebench_core.benchmark_audit_documents import MonitorPolicyPayloadV2


def monitor_slot_identity(
    policy: MonitorPolicyPayloadV2,
    local_date: date,
) -> tuple[str, datetime]:
    """Return the stable local cadence key and exact UTC slot instant.

    An ambiguous fall-back wall time resolves to its first occurrence. A nonexistent
    spring-forward time advances minute by minute to the first valid instant, with a
    three-hour upper bound so malformed timezone transitions fail closed.
    """
    if policy.cadence == "weekly" and local_date.weekday() != policy.weekday:
        raise ValueError("local date does not match the monitor policy weekday")
    zone = ZoneInfo(policy.timezone)
    hour, minute = (int(part) for part in policy.local_time.split(":"))
    requested = datetime.combine(local_date, time(hour, minute))
    for minute_offset in range(181):
        candidate = requested + timedelta(minutes=minute_offset)
        instants: set[datetime] = set()
        for fold in (0, 1):
            instant = candidate.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None) == candidate:
                instants.add(instant)
        if instants:
            cadence = "D" if policy.cadence == "daily" else "W"
            return f"{cadence}:{local_date.isoformat()}", min(instants)
    raise ValueError("scheduled local time falls in an unresolvable timezone gap")


def monitor_retry_at(
    *,
    completed_attempts: int,
    max_retries: int,
    now: datetime,
    timezone_name: str,
    local_day: date,
) -> datetime | None:
    """Compute a bounded retry instant only while its source-day reservation remains valid."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("monitor retry clock must be timezone-aware")
    if type(completed_attempts) is not int or completed_attempts < 1:
        raise ValueError("completed attempts must be a positive integer")
    if type(max_retries) is not int or not 0 <= max_retries <= 10:
        raise ValueError("monitor retries must be between zero and ten")
    if completed_attempts > max_retries:
        return None
    timezone = ZoneInfo(timezone_name)
    retry_at = now.astimezone(UTC) + timedelta(
        seconds=min(30 * (2 ** (completed_attempts - 1)), 3_600)
    )
    if retry_at.astimezone(timezone).date() != local_day:
        return None
    return retry_at
