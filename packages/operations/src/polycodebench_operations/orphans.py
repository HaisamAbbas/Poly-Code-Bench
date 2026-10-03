"""Orphaned execution-guest sweep (T 22.5 alert, runbook worker-loss-and-orphan-cleanup.md).

A guest is an orphan once it outlives its ``expires`` label/tag (stage TTL). The sweep
reclaims every expired PolyCodeBench guest it is allowed to reclaim, then re-lists to prove
it is gone, and reports:

* ``pcb_orphan_guests``                      - expired guests still present after the sweep;
* ``pcb_orphan_guests_over_alert_threshold`` - those beyond TTL + grace (10 min default),
  the condition the required alert fires on;
* ``pcb_orphans_reclaimed_total``            - guests destroyed by this sweep.

The EC2 path runs as the ``ops-reaper`` role, which may terminate only instances tagged
``pcb:owner=polycodebench`` in its own environment (modules/identity). Unlike the stage
driver's ``collect_expired`` it also reclaims lanes the driver does not manage (for example
``performance``), so no PolyCodeBench guest can be invisible to cleanup.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from polycodebench_core.telemetry import MetricsRegistry
from polycodebench_runner.provider import LocalDockerSandboxProvider

from polycodebench_operations import localenv


@dataclass
class SweepReport:
    provider: str
    expired_before: int = 0
    reclaimed: list[str] = field(default_factory=list)
    remaining_expired: list[dict[str, Any]] = field(default_factory=list)
    over_alert_threshold: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "expired_before": self.expired_before,
            "reclaimed": len(self.reclaimed),
            "remaining_expired": self.remaining_expired,
            "over_alert_threshold": self.over_alert_threshold,
            "seconds": round(self.seconds, 3),
            "clean": not self.remaining_expired,
            "oldest_expired_seconds": max(
                (int(time.time()) - item["expires"] for item in self.remaining_expired), default=0
            ),
        }


def _local_expired(provider_id: str, now: int) -> list[dict[str, Any]]:
    ids = (
        localenv.docker(
            "ps",
            "--all",
            "--filter",
            "label=pcb.owner=polycodebench",
            "--filter",
            f"label=pcb.provider={provider_id}",
            "--format",
            "{{.ID}}",
        )
        .decode()
        .split()
    )
    expired: list[dict[str, Any]] = []
    for cid in ids:
        labels = json.loads(localenv.docker("inspect", "--format", "{{json .Config.Labels}}", cid))
        if labels.get("pcb.provider") != provider_id:
            continue
        expires = int(labels.get("pcb.expires", "0") or 0)
        if expires <= now:
            expired.append(
                {"resource": cid[:12], "lane": labels.get("pcb.lane"), "expires": expires}
            )
    return expired


def sweep_local(
    provider: LocalDockerSandboxProvider,
    *,
    grace_seconds: int = 600,
    registry: MetricsRegistry | None = None,
    clock: Callable[[], float] = time.time,
    dry_run: bool = False,
) -> SweepReport:
    """Reclaim expired guests of one driver instance (``dry_run`` only reports them)."""

    started = time.perf_counter()
    now = int(clock())
    report = SweepReport(provider="local_docker")
    report.expired_before = len(_local_expired(provider.provider_id, now))
    if not dry_run:
        report.reclaimed = list(asyncio.run(provider.collect_expired(now_epoch=now)))
    report.remaining_expired = _local_expired(provider.provider_id, now)
    report.over_alert_threshold = sum(
        1 for item in report.remaining_expired if item["expires"] + grace_seconds <= now
    )
    report.seconds = time.perf_counter() - started
    _publish(registry, report)
    return report


def _ec2_expired(ec2: Any, environment: str, now: int) -> list[dict[str, Any]]:
    paginator = ec2.get_paginator("describe_instances")
    expired: list[dict[str, Any]] = []
    for page in paginator.paginate(
        Filters=[
            {"Name": "tag:pcb:owner", "Values": ["polycodebench"]},
            {"Name": "tag:pcb:environment", "Values": [environment]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    ):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
                raw = tags.get("pcb:expires", "")
                # A guest without a parseable expiry is treated as already expired: nothing
                # PolyCodeBench launches may live without a TTL.
                expires = int(raw) if raw.isdigit() else 0
                if expires <= now:
                    expired.append(
                        {
                            "resource": instance["InstanceId"],
                            "lane": tags.get("pcb:lane"),
                            "expires": expires,
                        }
                    )
    return expired


def sweep_ec2(
    ec2: Any,
    *,
    environment: str,
    grace_seconds: int = 600,
    registry: MetricsRegistry | None = None,
    clock: Callable[[], float] = time.time,
    settle_seconds: float = 0.0,
    dry_run: bool = False,
) -> SweepReport:
    started = time.perf_counter()
    now = int(clock())
    report = SweepReport(provider="ec2_vm")
    expired = _ec2_expired(ec2, environment, now)
    report.expired_before = len(expired)
    if expired and not dry_run:
        ec2.terminate_instances(InstanceIds=[item["resource"] for item in expired])
        report.reclaimed = [item["resource"] for item in expired]
        if settle_seconds:
            time.sleep(settle_seconds)
    report.remaining_expired = _ec2_expired(ec2, environment, now)
    report.over_alert_threshold = sum(
        1 for item in report.remaining_expired if item["expires"] + grace_seconds <= now
    )
    report.seconds = time.perf_counter() - started
    _publish(registry, report)
    return report


def _publish(registry: MetricsRegistry | None, report: SweepReport) -> None:
    if registry is None:
        return
    by_lane: dict[str, int] = {}
    for item in report.remaining_expired:
        lane = str(item.get("lane") or "unknown")
        by_lane[lane] = by_lane.get(lane, 0) + 1
    for lane in {"solve", "grading", "admission", "performance", "unknown", *by_lane}:
        registry.set(
            "pcb_orphan_guests", float(by_lane.get(lane, 0)), provider=report.provider, lane=lane
        )
    registry.set(
        "pcb_orphan_guests_over_alert_threshold",
        float(report.over_alert_threshold),
        provider=report.provider,
    )
    if report.reclaimed:
        registry.inc(
            "pcb_orphans_reclaimed_total", float(len(report.reclaimed)), provider=report.provider
        )
