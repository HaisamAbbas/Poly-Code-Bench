"""An append-only record of where each task text has been sent.

"Seen" is an audit fact here, not an estimate. A task that was sent to a model endpoint, a hosted
generator, a reviewer outside the operator's control, or a public repository has been exposed to
whatever that party retains or trains on. The ledger keeps the earliest such time and the exact
recipients, so a later evaluation can refuse to reuse a task on an endpoint that already received
it, and can confine post-cutoff claims to tasks first exposed after the model's declared cutoff.

Post-cutoff eligibility follows ``polycodebench_services.task_packages.post_cutoff_eligibility``:
an unknown cutoff never qualifies, and curation time never establishes exposure. The only added
rule is that a task with no external exposure yet is judged at the evaluation time, because the
evaluation itself is the first exposure.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from polycodebench_taskgen.contracts import ExposureEvent, TaskgenModel


class ModelEligibility(TaskgenModel):
    eligible: bool
    reason: str
    first_external_exposure: datetime | None


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ExposureLedger:
    def __init__(self, events: Iterable[ExposureEvent] = ()) -> None:
        self._events: dict[str, list[ExposureEvent]] = {}
        for event in events:
            self.record(event)

    def record(self, event: ExposureEvent) -> None:
        _require_aware(event.occurred_at, "occurred_at")
        self._events.setdefault(event.task_id, []).append(event)

    def events(self, task_id: str) -> tuple[ExposureEvent, ...]:
        return tuple(self._events.get(task_id, ()))

    def recipients_seen(self, task_id: str) -> frozenset[str]:
        return frozenset(event.recipient_id for event in self.events(task_id) if event.is_external)

    def first_external_exposure(self, task_id: str) -> datetime | None:
        times = [event.occurred_at for event in self.events(task_id) if event.is_external]
        return min(times) if times else None

    def eligibility_for_model(
        self,
        task_id: str,
        *,
        recipient_id: str,
        cutoff_at: datetime | None,
        cutoff_confidence: Literal["verified", "estimated", "unknown"],
        evaluation_at: datetime,
    ) -> ModelEligibility:
        """Decide whether this task may enter a post-cutoff subset for one model endpoint."""
        if cutoff_at is None or cutoff_confidence == "unknown":
            return ModelEligibility(
                eligible=False,
                reason="model_cutoff_unknown",
                first_external_exposure=self.first_external_exposure(task_id),
            )
        _require_aware(cutoff_at, "cutoff_at")
        _require_aware(evaluation_at, "evaluation_at")
        first = self.first_external_exposure(task_id)
        if recipient_id in self.recipients_seen(task_id):
            return ModelEligibility(eligible=False, reason="already_exposed_to_recipient",
                                    first_external_exposure=first)  # fmt: skip
        effective_first = first if first is not None else evaluation_at
        if effective_first > cutoff_at:
            reason = (
                "first_external_exposure_after_cutoff"
                if first is not None
                else "unexposed_until_evaluation_after_cutoff"
            )
            return ModelEligibility(eligible=True, reason=reason, first_external_exposure=first)
        return ModelEligibility(eligible=False, reason="exposed_on_or_before_cutoff",
                                first_external_exposure=first)  # fmt: skip
