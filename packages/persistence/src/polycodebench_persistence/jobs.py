"""Durable PostgreSQL stage scheduling, leases, recovery, and progress events."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    LeaseLost,
    PersistenceConflict,
)
from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_core.jobs import JobClaim, JobDefinition, StageOutcome, WorkerRegistrationSpec
from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    attempt,
    audit_event,
    campaign,
    capacity_slot,
    config_document,
    evaluation,
    release,
    run,
    stage_dependency,
    stage_execution,
    stage_job,
    stage_job_event,
    worker_registration,
)

LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
REAPER_SECONDS = 30
RETRY_DELAYS_SECONDS = (10, 60)
TERMINAL_JOB_STATES = frozenset({"succeeded", "dead", "cancelled", "skipped"})


class PostgresJobRepository:
    """All scheduler decisions are committed through short database transactions."""

    def __init__(
        self,
        engine: Engine,
        *,
        campaign_concurrency: int = 4,
        provider_concurrency: int = 2,
    ) -> None:
        if campaign_concurrency < 1 or provider_concurrency < 1:
            raise ValueError("fairness concurrency bounds must be positive")
        self._engine = engine
        self._campaign_concurrency = campaign_concurrency
        self._provider_concurrency = provider_concurrency

    def create_dag(
        self,
        *,
        scope_type: str,
        scope_id: UUID,
        jobs: tuple[JobDefinition, ...],
        actor: str,
    ) -> dict[str, UUID]:
        if scope_type not in {"attempt", "evaluation", "release"} or not jobs:
            raise InvalidState("job DAG scope or contents are invalid")
        by_key = {item.key: item for item in jobs}
        if len(by_key) != len(jobs):
            raise InvalidState("job DAG keys must be unique")
        for item in jobs:
            if any(dependency.parent_key not in by_key for dependency in item.dependencies):
                raise InvalidReference("job DAG has an unknown dependency")
        _topological_order(by_key)
        ids = {key: uuid4() for key in by_key}
        try:
            with self._engine.begin() as connection:
                lock_key = f"pcb.dag:{scope_type}:{scope_id}"
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": lock_key},
                )
                scope = self._scope(connection, scope_type, scope_id, lock=True)
                if scope["state"] in {"cancelled", "cancelling", "failed"}:
                    raise InvalidState("cannot schedule work for an inactive scope")
                campaign_id = scope.get("campaign_id")
                logical_keys = {key: _logical_key(scope_type, scope_id, key) for key in by_key}
                existing_rows = (
                    connection.execute(
                        select(stage_job).where(
                            stage_job.c.logical_key.in_(tuple(logical_keys.values()))
                        )
                    )
                    .mappings()
                    .all()
                )
                all_scope_logical_keys = set(
                    connection.execute(
                        select(stage_job.c.logical_key).where(
                            getattr(stage_job.c, f"{scope_type}_id") == scope_id
                        )
                    ).scalars()
                )
                if all_scope_logical_keys and all_scope_logical_keys != set(logical_keys.values()):
                    raise PersistenceConflict("job DAG replay is incomplete or changed")
                if existing_rows:
                    existing_by_logical = {row["logical_key"]: row for row in existing_rows}
                    if set(existing_by_logical) != set(logical_keys.values()):
                        raise PersistenceConflict("job DAG replay is incomplete or changed")
                    existing_by_key = {
                        key: existing_by_logical[logical_key]
                        for key, logical_key in logical_keys.items()
                    }
                    for key, definition in by_key.items():
                        row = existing_by_key[key]
                        if any(
                            row[field] != expected
                            for field, expected in (
                                ("stage", definition.stage),
                                ("shard_key", definition.shard_key),
                                ("input_digest", definition.input_digest),
                                ("input_artifact_id", definition.input_artifact_id),
                                ("required", definition.required),
                                ("queue_class", definition.queue_class),
                                ("resource_class", definition.resource_class),
                                ("provider_key", definition.provider_key),
                                ("priority", definition.priority),
                                ("max_deliveries", definition.max_deliveries),
                            )
                        ):
                            raise PersistenceConflict(
                                "job DAG replay differs from the durable definition"
                            )
                    parent = stage_job.alias("dag_parent")
                    child = stage_job.alias("dag_child")
                    stored_edges = {
                        (
                            row.child_key,
                            row.parent_key,
                            row.edge_condition,
                            tuple(row.skip_reasons),
                        )
                        for row in connection.execute(
                            select(
                                child.c.logical_key.label("child_key"),
                                parent.c.logical_key.label("parent_key"),
                                stage_dependency.c.condition.label("edge_condition"),
                                stage_dependency.c.accepted_skip_reasons.label("skip_reasons"),
                            )
                            .select_from(
                                child.join(
                                    stage_dependency,
                                    stage_dependency.c.job_id == child.c.id,
                                ).join(
                                    parent,
                                    parent.c.id == stage_dependency.c.prerequisite_job_id,
                                )
                            )
                            .where(child.c.logical_key.in_(tuple(logical_keys.values())))
                        ).all()
                    }
                    expected_edges = {
                        (
                            logical_keys[key],
                            logical_keys[dependency.parent_key],
                            dependency.condition,
                            tuple(dependency.accepted_skip_reasons),
                        )
                        for key, definition in by_key.items()
                        for dependency in definition.dependencies
                    }
                    if stored_edges != expected_edges:
                        raise PersistenceConflict("job DAG replay dependencies changed")
                    return {key: cast(UUID, existing_by_key[key]["id"]) for key in by_key}
                if not self._scope_active(connection, scope_type, scope_id):
                    raise InvalidState("cannot add a DAG to a terminal or revoked scope")
                for key, definition in by_key.items():
                    if definition.input_artifact_id is not None:
                        source = connection.execute(
                            select(artifact.c.content_digest, artifact.c.status).where(
                                artifact.c.id == definition.input_artifact_id
                            )
                        ).one_or_none()
                        if source is None or source.status != "verified":
                            raise InvalidReference("job input must reference a verified artifact")
                        if source.content_digest != definition.input_digest:
                            raise InvalidState("job input digest does not match its artifact")
                    logical_key = logical_keys[key]
                    connection.execute(
                        insert(stage_job).values(
                            id=ids[key],
                            **{f"{scope_type}_id": scope_id},
                            stage=definition.stage,
                            shard_key=definition.shard_key,
                            input_digest=definition.input_digest,
                            input_artifact_id=definition.input_artifact_id,
                            logical_key=logical_key,
                            state="blocked" if definition.dependencies else "queued",
                            required=definition.required,
                            queue_class=definition.queue_class,
                            resource_class=definition.resource_class,
                            fairness_campaign_id=campaign_id,
                            provider_key=definition.provider_key,
                            priority=definition.priority,
                            max_deliveries=definition.max_deliveries,
                        )
                    )
                for key, definition in by_key.items():
                    for dependency in definition.dependencies:
                        connection.execute(
                            insert(stage_dependency).values(
                                job_id=ids[key],
                                prerequisite_job_id=ids[dependency.parent_key],
                                condition=dependency.condition,
                                accepted_skip_reasons=list(dependency.accepted_skip_reasons),
                            )
                        )
                    self._event(
                        connection,
                        ids[key],
                        "job_created",
                        actor,
                        details={
                            "stage": definition.stage,
                            "logical_key": logical_keys[key],
                        },
                    )
            return ids
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def register_worker(
        self, spec: WorkerRegistrationSpec, *, actor_subject: str | None = None
    ) -> UUID:
        if actor_subject is not None and not 1 <= len(actor_subject) <= 255:
            raise ValueError("worker registration audit actor is invalid")
        worker_id = uuid4()
        try:
            with self._engine.begin() as connection:
                resource_config = connection.execute(
                    select(config_document.c.kind)
                    .select_from(
                        config_document.join(
                            artifact, config_document.c.canonical_artifact_id == artifact.c.id
                        )
                    )
                    .where(
                        config_document.c.id == spec.resource_spec_config_id,
                        artifact.c.status == "verified",
                    )
                ).scalar_one_or_none()
                if resource_config != "resource_spec":
                    raise InvalidReference("worker requires a registered resource_spec config")
                connection.execute(
                    insert(worker_registration).values(
                        id=worker_id,
                        workload_identity=spec.workload_identity,
                        lane=spec.lane,
                        hardware_class=spec.hardware_class,
                        allowed_queue_classes=list(spec.allowed_queue_classes),
                        allowed_resource_classes=list(spec.allowed_resource_classes),
                        status="active",
                        last_heartbeat_at=func.now(),
                        driver_identity=spec.driver_identity,
                    )
                )
                for slot in spec.slots:
                    if slot.resource_spec_config_id != spec.resource_spec_config_id:
                        raise InvalidReference("slot resource spec must match its worker")
                    connection.execute(
                        insert(capacity_slot).values(
                            id=uuid4(),
                            worker_id=worker_id,
                            resource_spec_config_id=slot.resource_spec_config_id,
                            slot_key=slot.slot_key,
                            resource_class=slot.resource_class,
                            state="available",
                        )
                    )
                if actor_subject is not None:
                    registration_digest = (
                        "sha256:"
                        + hashlib.sha256(
                            canonical_json_bytes(spec.model_dump(mode="json"))
                        ).hexdigest()
                    )
                    connection.execute(
                        insert(audit_event).values(
                            actor_subject=actor_subject,
                            action="worker.register",
                            resource_type="worker_registration",
                            resource_id=str(worker_id),
                            after_digest=registration_digest,
                            request_id=uuid4().hex,
                            details={
                                "lane": spec.lane,
                                "hardware_class": spec.hardware_class,
                                "driver_identity": spec.driver_identity,
                                "queue_classes": list(spec.allowed_queue_classes),
                                "resource_classes": list(spec.allowed_resource_classes),
                                "slot_count": len(spec.slots),
                            },
                        )
                    )
            return worker_id
        except (InvalidReference, InvalidState):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def heartbeat_worker(self, worker_id: UUID) -> bool:
        with self._engine.begin() as connection:
            changed = connection.execute(
                update(worker_registration)
                .where(
                    worker_registration.c.id == worker_id, worker_registration.c.status == "active"
                )
                .values(
                    last_heartbeat_at=func.now(), row_version=worker_registration.c.row_version + 1
                )
            ).rowcount
            return changed == 1

    def set_worker_status(self, worker_id: UUID, *, status: str) -> bool:
        """Drain/disable/resume a worker without changing any leased guest state."""
        if status not in {"active", "draining", "disabled"}:
            raise InvalidState("worker status transition is invalid")
        with self._engine.begin() as connection:
            worker = (
                connection.execute(
                    select(worker_registration)
                    .where(worker_registration.c.id == worker_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if worker is None or worker["status"] == "quarantined":
                return False
            connection.execute(
                update(worker_registration)
                .where(worker_registration.c.id == worker_id)
                .values(status=status, row_version=worker_registration.c.row_version + 1)
            )
            connection.execute(
                update(capacity_slot)
                .where(
                    capacity_slot.c.worker_id == worker_id,
                    capacity_slot.c.job_id.is_(None),
                    capacity_slot.c.state.in_(("available", "draining", "disabled")),
                )
                .values(
                    state="available" if status == "active" else status,
                    row_version=capacity_slot.c.row_version + 1,
                    updated_at=func.now(),
                )
            )
            return True

    def skip_unstarted(self, job_id: UUID, *, reason: str, actor: str) -> None:
        """Persist a scheduler-authored branch skip; workers cannot manufacture skips."""
        if not reason or len(reason) > 64 or not reason.replace("_", "").isalnum():
            raise InvalidState("skip reason must be a stable code")
        with self._engine.begin() as connection:
            identity = (
                connection.execute(select(stage_job).where(stage_job.c.id == job_id))
                .mappings()
                .one_or_none()
            )
            if identity is None:
                raise InvalidReference("job does not exist")
            self._lock_scope(connection, _scope_type(identity), _scope_id(identity))
            job = (
                connection.execute(
                    select(stage_job).where(stage_job.c.id == job_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if job is None or job["state"] not in {"blocked", "queued"}:
                raise InvalidState("only unstarted jobs can be skipped")
            connection.execute(
                update(stage_job)
                .where(stage_job.c.id == job_id)
                .values(
                    state="skipped",
                    skip_reason=reason,
                    row_version=stage_job.c.row_version + 1,
                )
            )
            self._event(
                connection,
                job_id,
                "job_skipped",
                actor,
                details={"reason": reason, "scheduler_authored": True},
            )
            self._unblock_dependents(connection, job_id)
            self._update_scope_completion(connection, _scope_type(job), _scope_id(job))

    def claim(
        self,
        worker_id: UUID,
        *,
        job_id: UUID | None = None,
        run_id: UUID | None = None,
        stage: str | None = None,
    ) -> JobClaim | None:
        """Lock a capacity slot first, then one compatible ready job; never hold locks over work."""
        if job_id is not None and run_id is not None:
            raise InvalidState("a claim cannot target both a job and a run")
        if run_id is not None and stage != "solve":
            raise InvalidState("run-scoped claims require the solve stage")
        try:
            with self._engine.begin() as connection:
                worker = (
                    connection.execute(
                        select(worker_registration)
                        .where(
                            worker_registration.c.id == worker_id,
                            worker_registration.c.status == "active",
                            worker_registration.c.last_heartbeat_at
                            > func.now() - text("interval '90 seconds'"),
                        )
                        .with_for_update(skip_locked=True)
                    )
                    .mappings()
                    .one_or_none()
                )
                if worker is None:
                    return None
                slot = (
                    connection.execute(
                        select(capacity_slot)
                        .where(
                            capacity_slot.c.worker_id == worker_id,
                            capacity_slot.c.state == "available",
                            capacity_slot.c.resource_class.in_(worker["allowed_resource_classes"]),
                        )
                        .order_by(capacity_slot.c.slot_key)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                    .mappings()
                    .one_or_none()
                )
                if slot is None:
                    return None
                active = stage_job.alias("active_job")
                campaign_load = (
                    select(func.count(active.c.id))
                    .where(
                        active.c.state == "leased",
                        active.c.fairness_campaign_id == stage_job.c.fairness_campaign_id,
                    )
                    .scalar_subquery()
                )
                provider_load = (
                    select(func.count(active.c.id))
                    .where(
                        active.c.state == "leased",
                        active.c.provider_key == stage_job.c.provider_key,
                    )
                    .scalar_subquery()
                )
                eligibility = [
                    stage_job.c.state.in_(("queued", "retry_wait")),
                    stage_job.c.queue_class.in_(worker["allowed_queue_classes"]),
                    stage_job.c.resource_class == slot["resource_class"],
                    stage_job.c.available_at <= func.now(),
                    stage_job.c.deliveries < stage_job.c.max_deliveries,
                    or_(
                        stage_job.c.fairness_campaign_id.is_(None),
                        campaign_load < self._campaign_concurrency,
                    ),
                    provider_load < self._provider_concurrency,
                    self._scope_active_clause(),
                    ~self._has_unsatisfied_dependency_clause(),
                    ~select(capacity_slot.c.id)
                    .where(capacity_slot.c.job_id == stage_job.c.id)
                    .exists(),
                ]
                if job_id is not None:
                    eligibility.append(stage_job.c.id == job_id)
                if run_id is not None:
                    eligibility.append(
                        select(attempt.c.id)
                        .where(
                            attempt.c.id == stage_job.c.attempt_id,
                            attempt.c.run_id == run_id,
                        )
                        .exists()
                    )
                if stage is not None:
                    eligibility.append(stage_job.c.stage == stage)
                eligible = (
                    select(stage_job)
                    .where(*eligibility)
                    .order_by(
                        stage_job.c.priority.desc(),
                        campaign_load,
                        provider_load,
                        stage_job.c.available_at,
                        stage_job.c.id,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                job = connection.execute(eligible).mappings().one_or_none()
                if job is None:
                    return None
                # Counts in the selection snapshot are advisory. Serialize competing
                # reservations by fairness key, then count again in a fresh snapshot.
                fairness_keys = [f"pcb.provider:{job['provider_key']}"]
                if job["fairness_campaign_id"] is not None:
                    fairness_keys.append(f"pcb.campaign:{job['fairness_campaign_id']}")
                fairness_keys.append(f"pcb.dag:{_scope_type(job)}:{_scope_id(job)}")
                for key in sorted(fairness_keys):
                    if not connection.execute(
                        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": key},
                    ).scalar_one():
                        return None
                if not self._scope_active(connection, _scope_type(job), _scope_id(job)):
                    return None
                loads = connection.execute(
                    select(
                        func.count().filter(stage_job.c.provider_key == job["provider_key"]),
                        func.count().filter(
                            stage_job.c.fairness_campaign_id == job["fairness_campaign_id"]
                        ),
                    ).where(stage_job.c.state == "leased")
                ).one()
                if loads[0] >= self._provider_concurrency or (
                    job["fairness_campaign_id"] is not None
                    and loads[1] >= self._campaign_concurrency
                ):
                    return None
                changed = (
                    connection.execute(
                        update(stage_job)
                        .where(
                            stage_job.c.id == job["id"],
                            stage_job.c.state.in_(("queued", "retry_wait")),
                        )
                        .values(
                            state="leased",
                            owner_id=str(worker_id),
                            lease_until=func.now() + text("interval '120 seconds'"),
                            fence=stage_job.c.fence + 1,
                            deliveries=stage_job.c.deliveries + 1,
                            row_version=stage_job.c.row_version + 1,
                            error_code=None,
                        )
                        .returning(stage_job)
                    )
                    .mappings()
                    .one()
                )
                execution_id = uuid4()
                connection.execute(
                    update(capacity_slot)
                    .where(capacity_slot.c.id == slot["id"], capacity_slot.c.state == "available")
                    .values(
                        state="reserved",
                        job_id=changed["id"],
                        fence=changed["fence"],
                        row_version=capacity_slot.c.row_version + 1,
                        updated_at=func.now(),
                    )
                )
                connection.execute(
                    insert(stage_execution).values(
                        id=execution_id,
                        job_id=changed["id"],
                        fence=changed["fence"],
                        worker_id=str(worker_id),
                    )
                )
                scope_kind = _scope_type(changed)
                if scope_kind == "attempt":
                    connection.execute(
                        update(attempt)
                        .where(attempt.c.id == changed["attempt_id"], attempt.c.state == "queued")
                        .values(state="running", row_version=attempt.c.row_version + 1)
                    )
                    self._mark_run_running(connection, changed["attempt_id"])
                elif scope_kind == "evaluation":
                    connection.execute(
                        update(evaluation)
                        .where(
                            evaluation.c.id == changed["evaluation_id"],
                            evaluation.c.state == "queued",
                        )
                        .values(state="running", row_version=evaluation.c.row_version + 1)
                    )
                self._event(
                    connection,
                    changed["id"],
                    "job_claimed",
                    str(worker_id),
                    fence=changed["fence"],
                    details={"delivery": changed["deliveries"], "slot_id": str(slot["id"])},
                )
                return JobClaim(
                    job_id=changed["id"],
                    execution_id=execution_id,
                    slot_id=slot["id"],
                    worker_id=str(worker_id),
                    slot_key=slot["slot_key"],
                    stage=changed["stage"],
                    scope_type=_scope_type(changed),
                    scope_id=_scope_id(changed),
                    input_artifact_id=changed["input_artifact_id"],
                    input_digest=changed["input_digest"],
                    resource_class=changed["resource_class"],
                    queue_class=changed["queue_class"],
                    fence=changed["fence"],
                    deliveries=changed["deliveries"],
                    lease_until_epoch=int(changed["lease_until"].timestamp()),
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    def begin_dispatch(self, claim: JobClaim) -> None:
        """Reserve provisioning before creating a resource outside the transaction."""
        with self._engine.begin() as connection:
            self._lock_scope(connection, claim.scope_type, claim.scope_id)
            self._assert_claim(connection, claim)
            changed = connection.execute(
                update(capacity_slot)
                .where(capacity_slot.c.id == claim.slot_id, capacity_slot.c.state == "reserved")
                .values(
                    state="busy", row_version=capacity_slot.c.row_version + 1, updated_at=func.now()
                )
            ).rowcount
            if changed != 1:
                raise LeaseLost()
            self._event(
                connection,
                claim.job_id,
                "guest_provisioning",
                claim.worker_id,
                fence=claim.fence,
                details={"slot_id": str(claim.slot_id)},
            )

    def bind_guest(self, claim: JobClaim, guest_id: str) -> None:
        if not guest_id or len(guest_id) > 255:
            raise InvalidState("guest identity is invalid")
        with self._engine.begin() as connection:
            self._lock_scope(connection, claim.scope_type, claim.scope_id)
            self._assert_claim(connection, claim)
            changed = connection.execute(
                update(capacity_slot)
                .where(
                    capacity_slot.c.id == claim.slot_id,
                    capacity_slot.c.job_id == claim.job_id,
                    capacity_slot.c.fence == claim.fence,
                    capacity_slot.c.state.in_(("reserved", "busy")),
                )
                .values(
                    state="busy",
                    guest_id=guest_id,
                    row_version=capacity_slot.c.row_version + 1,
                    updated_at=func.now(),
                )
            ).rowcount
            if changed != 1:
                raise LeaseLost()
            self._event(
                connection,
                claim.job_id,
                "guest_started",
                claim.worker_id,
                fence=claim.fence,
                details={"guest_id": guest_id},
            )

    def unbind_guest(self, claim: JobClaim, guest_id: str) -> None:
        """Record verified destruction of one plan guest while retaining its job slot."""
        if not guest_id or len(guest_id) > 255:
            raise InvalidState("guest identity is invalid")
        with self._engine.begin() as connection:
            self._lock_scope(connection, claim.scope_type, claim.scope_id)
            self._assert_claim(connection, claim)
            changed = connection.execute(
                update(capacity_slot)
                .where(
                    capacity_slot.c.id == claim.slot_id,
                    capacity_slot.c.job_id == claim.job_id,
                    capacity_slot.c.fence == claim.fence,
                    capacity_slot.c.worker_id == UUID(claim.worker_id),
                    capacity_slot.c.state == "busy",
                    capacity_slot.c.guest_id == guest_id,
                )
                .values(guest_id=None, row_version=capacity_slot.c.row_version + 1)
            ).rowcount
            if changed != 1:
                raise LeaseLost("guest cleanup no longer belongs to this worker delivery")
            self._event(
                connection,
                claim.job_id,
                "guest_destroyed",
                claim.worker_id,
                fence=claim.fence,
                details={"guest_id": guest_id},
            )

    def heartbeat(self, claim: JobClaim) -> bool:
        with self._engine.begin() as connection:
            self._lock_scope(connection, claim.scope_type, claim.scope_id)
            try:
                self._assert_claim(connection, claim)
            except LeaseLost:
                return False
            changed = connection.execute(
                update(stage_job)
                .where(
                    stage_job.c.id == claim.job_id,
                    stage_job.c.state == "leased",
                    stage_job.c.owner_id == claim.worker_id,
                    stage_job.c.fence == claim.fence,
                    stage_job.c.lease_until > func.now(),
                    self._scope_active_clause(claim.scope_type, claim.scope_id),
                )
                .values(
                    lease_until=func.now() + text("interval '120 seconds'"),
                    row_version=stage_job.c.row_version + 1,
                )
            ).rowcount
            if changed:
                connection.execute(
                    update(worker_registration)
                    .where(worker_registration.c.id == UUID(claim.worker_id))
                    .values(
                        last_heartbeat_at=func.now(),
                        row_version=worker_registration.c.row_version + 1,
                    )
                )
                self._event(
                    connection,
                    claim.job_id,
                    "heartbeat",
                    claim.worker_id,
                    fence=claim.fence,
                    details={},
                )
            return changed == 1

    def dispatch_allowed(self, claim: JobClaim) -> bool:
        with self._engine.connect() as connection:
            try:
                self._assert_claim(connection, claim)
            except LeaseLost:
                return False
            return True

    def complete(self, claim: JobClaim, *, output_artifact_id: UUID, outcome: StageOutcome) -> str:
        outcome = StageOutcome.model_validate(outcome.model_dump())
        try:
            with self._engine.begin() as connection:
                self._lock_scope(connection, claim.scope_type, claim.scope_id)
                job = (
                    connection.execute(
                        select(stage_job).where(stage_job.c.id == claim.job_id).with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if job is None:
                    raise LeaseLost()
                if job["state"] == "succeeded" and job["fence"] == claim.fence:
                    prior = connection.execute(
                        select(
                            stage_execution.c.id,
                            stage_execution.c.worker_id,
                            stage_execution.c.output_manifest_id,
                        ).where(
                            stage_execution.c.job_id == claim.job_id,
                            stage_execution.c.fence == claim.fence,
                            stage_execution.c.result == "succeeded",
                        )
                    ).one_or_none()
                    if (
                        prior is None
                        or prior.worker_id != claim.worker_id
                        or prior.id != claim.execution_id
                    ):
                        raise LeaseLost()
                    if job["output_artifact_id"] == output_artifact_id == prior.output_manifest_id:
                        if job["result_document"] != outcome.model_dump(mode="json"):
                            raise PersistenceConflict(
                                "completion outcome conflicts with the committed result"
                            )
                        return "replayed"
                    raise PersistenceConflict(
                        "completion output conflicts with the committed result"
                    )
                self._assert_claim(connection, claim, locked_job=job)
                if not self._scope_active(connection, claim.scope_type, claim.scope_id):
                    raise LeaseLost("scope was cancelled before completion")
                output = connection.execute(
                    select(artifact.c.status).where(artifact.c.id == output_artifact_id)
                ).scalar_one_or_none()
                if output != "verified":
                    raise InvalidReference("job output must be a verified artifact")
                execution = (
                    connection.execute(
                        select(stage_execution)
                        .where(
                            stage_execution.c.job_id == claim.job_id,
                            stage_execution.c.fence == claim.fence,
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if execution is None or execution["finished_at"] is not None:
                    raise LeaseLost()
                connection.execute(
                    update(stage_execution)
                    .where(stage_execution.c.id == execution["id"])
                    .values(
                        finished_at=func.now(),
                        result="succeeded",
                        failure_class=None,
                        output_manifest_id=output_artifact_id,
                    )
                )
                connection.execute(
                    update(stage_job)
                    .where(stage_job.c.id == claim.job_id)
                    .values(
                        state="succeeded",
                        quality_gate=outcome.quality_gate,
                        result_document=outcome.model_dump(mode="json"),
                        output_artifact_id=output_artifact_id,
                        error_code=None,
                        owner_id=None,
                        lease_until=None,
                        row_version=stage_job.c.row_version + 1,
                    )
                )
                slot = (
                    connection.execute(
                        select(capacity_slot)
                        .where(capacity_slot.c.id == claim.slot_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if slot is not None:
                    if slot["guest_id"] is not None:
                        connection.execute(
                            update(capacity_slot)
                            .where(capacity_slot.c.id == claim.slot_id)
                            .values(
                                state="cleanup",
                                row_version=capacity_slot.c.row_version + 1,
                                updated_at=func.now(),
                            )
                        )
                    else:
                        self._free_slot(connection, claim.slot_id)
                self._event(
                    connection,
                    claim.job_id,
                    "job_succeeded",
                    claim.worker_id,
                    fence=claim.fence,
                    details={
                        "output_artifact_id": str(output_artifact_id),
                        "quality_gate": outcome.quality_gate,
                        "model_failure": outcome.model_failure,
                        "stage_details": outcome.event_details,
                    },
                )
                self._unblock_dependents(connection, claim.job_id)
                self._update_scope_completion(connection, claim.scope_type, claim.scope_id)
                return "completed"
        except (InvalidReference, InvalidState, LeaseLost, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def fail(
        self,
        claim: JobClaim,
        *,
        failure_class: str,
        failure_code: str | None = None,
        sandbox_cleanup_verified: bool = False,
    ) -> str:
        if not failure_class or len(failure_class) > 64:
            raise InvalidState("infrastructure failure class is invalid")
        with self._engine.begin() as connection:
            self._lock_scope(connection, claim.scope_type, claim.scope_id)
            job = (
                connection.execute(
                    select(stage_job).where(stage_job.c.id == claim.job_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if job is None:
                raise LeaseLost()
            self._assert_claim(connection, claim, locked_job=job)
            execution = (
                connection.execute(
                    select(stage_execution)
                    .where(
                        stage_execution.c.job_id == claim.job_id,
                        stage_execution.c.fence == claim.fence,
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            connection.execute(
                update(stage_execution)
                .where(stage_execution.c.id == execution["id"])
                .values(finished_at=func.now(), result="failed", failure_class=failure_class)
            )
            retryable = job["deliveries"] < job["max_deliveries"] and self._scope_active(
                connection, claim.scope_type, claim.scope_id
            )
            next_state = "retry_wait" if retryable else "dead"
            retry_delay = _retry_delay(job["logical_key"], job["deliveries"])
            connection.execute(
                update(stage_job)
                .where(stage_job.c.id == claim.job_id)
                .values(
                    state=next_state,
                    available_at=func.now() + text(f"interval '{retry_delay} seconds'"),
                    owner_id=None,
                    lease_until=None,
                    error_code=failure_code or failure_class,
                    row_version=stage_job.c.row_version + 1,
                )
            )
            if sandbox_cleanup_verified:
                slot = (
                    connection.execute(
                        select(capacity_slot)
                        .where(capacity_slot.c.id == claim.slot_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    slot is None
                    or slot["job_id"] != claim.job_id
                    or slot["fence"] != claim.fence
                    or str(slot["worker_id"]) != claim.worker_id
                    or slot["state"] != "busy"
                    or slot["guest_id"] is not None
                ):
                    raise LeaseLost("verified cleanup does not match the claimed empty slot")
                self._free_slot(connection, claim.slot_id)
                self._event(
                    connection,
                    claim.job_id,
                    "sandbox_cleanup_confirmed",
                    claim.worker_id,
                    fence=claim.fence,
                    details={"slot_id": str(claim.slot_id)},
                )
            else:
                self._queue_slot_cleanup(connection, claim.slot_id)
            self._event(
                connection,
                claim.job_id,
                "delivery_failed",
                claim.worker_id,
                fence=claim.fence,
                details={
                    "failure_class": failure_class,
                    "failure_code": failure_code,
                    "next_state": next_state,
                    "retry_delay_seconds": retry_delay,
                },
            )
            if next_state == "dead":
                self._unblock_dependents(connection, claim.job_id)
                self._update_scope_completion(connection, claim.scope_type, claim.scope_id)
            return next_state

    def reap_expired(self, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("reaper batch size must be in [1,1000]")
        recovered: list[dict[str, object]] = []
        with self._engine.begin() as connection:
            jobs = (
                connection.execute(
                    select(stage_job)
                    .where(stage_job.c.state == "leased", stage_job.c.lease_until <= func.now())
                    .order_by(stage_job.c.lease_until, stage_job.c.id)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            for job in jobs:
                if not self._lock_scope(connection, _scope_type(job), _scope_id(job), wait=False):
                    continue
                job = (
                    connection.execute(
                        select(stage_job).where(stage_job.c.id == job["id"]).with_for_update()
                    )
                    .mappings()
                    .one()
                )
                if (
                    job["state"] != "leased"
                    or connection.execute(
                        select(stage_job.c.id).where(
                            stage_job.c.id == job["id"], stage_job.c.lease_until <= func.now()
                        )
                    ).scalar_one_or_none()
                    is None
                ):
                    continue
                active_scope = self._scope_active(connection, _scope_type(job), _scope_id(job))
                execution = (
                    connection.execute(
                        select(stage_execution)
                        .where(
                            stage_execution.c.job_id == job["id"],
                            stage_execution.c.fence == job["fence"],
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if execution is not None and execution["finished_at"] is None:
                    connection.execute(
                        update(stage_execution)
                        .where(stage_execution.c.id == execution["id"])
                        .values(
                            finished_at=func.now(),
                            result="lost" if active_scope else "cancelled",
                            failure_class="lease_expired" if active_scope else "scope_cancelled",
                        )
                    )
                retryable = job["deliveries"] < job["max_deliveries"]
                state = ("retry_wait" if retryable else "dead") if active_scope else "cancelled"
                retry_delay = _retry_delay(job["logical_key"], job["deliveries"])
                connection.execute(
                    update(stage_job)
                    .where(stage_job.c.id == job["id"])
                    .values(
                        state=state,
                        available_at=func.now() + text(f"interval '{retry_delay} seconds'"),
                        owner_id=None,
                        lease_until=None,
                        error_code="lease_expired",
                        row_version=stage_job.c.row_version + 1,
                    )
                )
                slots = (
                    connection.execute(
                        select(capacity_slot)
                        .where(
                            capacity_slot.c.job_id == job["id"],
                            capacity_slot.c.fence == job["fence"],
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .all()
                )
                for slot in slots:
                    self._queue_slot_cleanup(connection, slot["id"])
                self._event(
                    connection,
                    job["id"],
                    "lease_expired",
                    "scheduler",
                    fence=job["fence"],
                    details={
                        "next_state": state,
                        "guest_cleanup_required": any(
                            slot["guest_id"] or slot["state"] == "busy" for slot in slots
                        ),
                        "retry_delay_seconds": retry_delay if state == "retry_wait" else None,
                    },
                )
                recovered.append(
                    {
                        "job_id": job["id"],
                        "state": state,
                        "slot_ids": tuple(slot["id"] for slot in slots),
                        "guest_ids": tuple(slot["guest_id"] for slot in slots if slot["guest_id"]),
                    }
                )
                if state in {"dead", "cancelled"}:
                    self._unblock_dependents(connection, job["id"])
                    self._update_scope_completion(connection, _scope_type(job), _scope_id(job))
        return tuple(recovered)

    def confirm_slot_cleanup(
        self, claim: JobClaim, *, actor: str, destruction_verified: bool
    ) -> None:
        if not destruction_verified:
            raise InvalidState("capacity slot cannot be reused without verified guest destruction")
        with self._engine.begin() as connection:
            self._lock_scope(connection, claim.scope_type, claim.scope_id)
            slot = (
                connection.execute(
                    select(capacity_slot)
                    .where(capacity_slot.c.id == claim.slot_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                slot is None
                or slot["state"] != "cleanup"
                or slot["job_id"] != claim.job_id
                or slot["fence"] != claim.fence
                or str(slot["worker_id"]) != claim.worker_id
                or actor != claim.worker_id
            ):
                raise LeaseLost("cleanup no longer belongs to this worker delivery")
            self._free_slot(connection, claim.slot_id)
            if slot["job_id"] is not None:
                self._event(
                    connection,
                    slot["job_id"],
                    "guest_destroyed",
                    actor,
                    fence=slot["fence"],
                    details={"slot_id": str(claim.slot_id)},
                )

    def cancel_scope(self, scope_type: str, scope_id: UUID, *, actor: str, reason: str) -> int:
        if scope_type not in {"attempt", "evaluation"} or not reason.strip():
            raise InvalidState("cancellation scope and reason are required")
        with self._engine.begin() as connection:
            return self._cancel_scope(connection, scope_type, scope_id, actor=actor, reason=reason)

    def _cancel_scope(
        self, connection: Connection, scope_type: str, scope_id: UUID, *, actor: str, reason: str
    ) -> int:
        self._lock_scope(connection, scope_type, scope_id)
        scope = self._scope(connection, scope_type, scope_id, lock=True)
        if scope["state"] in {"completed", "ready", "failed", "cancelled", "superseded"}:
            return 0
        # The scope state changes in the same transaction as revoking dispatch.
        # No intermediate state is visible to a concurrent claimer.
        self._set_scope_state(connection, scope_type, scope_id, "cancelled")
        rows = (
            connection.execute(
                select(stage_job)
                .where(
                    getattr(stage_job.c, f"{scope_type}_id") == scope_id,
                    stage_job.c.state.in_(("blocked", "queued", "retry_wait", "leased")),
                )
                .order_by(stage_job.c.id)
                .with_for_update()
            )
            .mappings()
            .all()
        )
        cancelled = 0
        for job in rows:
            previous_state = job["state"]
            new_fence = job["fence"] + 1 if previous_state == "leased" else job["fence"]
            connection.execute(
                update(stage_job)
                .where(stage_job.c.id == job["id"])
                .values(
                    state="cancelled",
                    owner_id=None,
                    lease_until=None,
                    fence=new_fence,
                    error_code="scope_cancelled",
                    row_version=stage_job.c.row_version + 1,
                )
            )
            if previous_state == "leased":
                execution = (
                    connection.execute(
                        select(stage_execution)
                        .where(
                            stage_execution.c.job_id == job["id"],
                            stage_execution.c.fence == job["fence"],
                            stage_execution.c.finished_at.is_(None),
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if execution:
                    connection.execute(
                        update(stage_execution)
                        .where(stage_execution.c.id == execution["id"])
                        .values(
                            finished_at=func.now(),
                            result="cancelled",
                            failure_class="scope_cancelled",
                        )
                    )
                slots = (
                    connection.execute(
                        select(capacity_slot)
                        .where(
                            capacity_slot.c.job_id == job["id"],
                            capacity_slot.c.fence == job["fence"],
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .all()
                )
                for slot in slots:
                    connection.execute(
                        update(capacity_slot)
                        .where(capacity_slot.c.id == slot["id"])
                        .values(
                            state="cleanup",
                            row_version=capacity_slot.c.row_version + 1,
                            updated_at=func.now(),
                        )
                    )
            self._event(
                connection,
                job["id"],
                "job_cancelled",
                actor,
                fence=new_fence,
                details={
                    "reason": reason,
                    "guest_cleanup_required": previous_state == "leased",
                },
            )
            cancelled += 1
        if scope_type == "attempt":
            for evaluation_id in connection.execute(
                select(evaluation.c.id)
                .where(evaluation.c.attempt_id == scope_id)
                .order_by(evaluation.c.id)
            ).scalars():
                cancelled += self._cancel_scope(
                    connection, "evaluation", evaluation_id, actor=actor, reason=reason
                )
            self._update_run_completion(connection, scope_id)
        return cancelled

    def events(self, job_id: UUID) -> tuple[dict[str, object], ...]:
        with self._engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    select(stage_job_event)
                    .where(stage_job_event.c.job_id == job_id)
                    .order_by(stage_job_event.c.event_seq)
                )
                .mappings()
                .all()
            )

    @staticmethod
    def _lock_scope(
        connection: Connection, scope_type: str, scope_id: UUID, *, wait: bool = True
    ) -> bool:
        function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
        result = connection.execute(
            text(f"SELECT {function}(hashtextextended(:key, 0))"),
            {"key": f"pcb.dag:{scope_type}:{scope_id}"},
        ).scalar_one()
        return True if wait else bool(result)

    def _assert_claim(
        self, connection: Connection, claim: JobClaim, *, locked_job: Any = None
    ) -> None:
        job = (
            locked_job
            or connection.execute(select(stage_job).where(stage_job.c.id == claim.job_id))
            .mappings()
            .one_or_none()
        )
        if (
            job is None
            or job["state"] != "leased"
            or job["owner_id"] != claim.worker_id
            or job["fence"] != claim.fence
            or _scope_type(job) != claim.scope_type
            or _scope_id(job) != claim.scope_id
        ):
            raise LeaseLost()
        slot = (
            connection.execute(select(capacity_slot).where(capacity_slot.c.id == claim.slot_id))
            .mappings()
            .one_or_none()
        )
        execution = connection.execute(
            select(stage_execution.c.id).where(
                stage_execution.c.id == claim.execution_id,
                stage_execution.c.job_id == claim.job_id,
                stage_execution.c.fence == claim.fence,
                stage_execution.c.worker_id == claim.worker_id,
                stage_execution.c.finished_at.is_(None),
            )
        ).scalar_one_or_none()
        if (
            slot is None
            or slot["job_id"] != claim.job_id
            or slot["fence"] != claim.fence
            or str(slot["worker_id"]) != claim.worker_id
            or slot["state"] not in {"reserved", "busy"}
            or execution is None
            or not self._scope_active(connection, claim.scope_type, claim.scope_id)
        ):
            raise LeaseLost()
        if (
            connection.execute(
                select(stage_job.c.id).where(
                    stage_job.c.id == claim.job_id, stage_job.c.lease_until > func.now()
                )
            ).scalar_one_or_none()
            is None
        ):
            raise LeaseLost()

    def _scope(
        self, connection: Connection, scope_type: str, scope_id: UUID, *, lock: bool = False
    ) -> dict[str, Any]:
        if scope_type == "attempt":
            query = (
                select(
                    attempt,
                    run.c.campaign_id,
                    run.c.status.label("run_state"),
                    campaign.c.status.label("campaign_state"),
                )
                .select_from(
                    attempt.join(run, attempt.c.run_id == run.c.id).join(
                        campaign, run.c.campaign_id == campaign.c.id
                    )
                )
                .where(attempt.c.id == scope_id)
            )
            if lock:
                query = query.with_for_update(of=attempt)
            row = connection.execute(query).mappings().one_or_none()
            if row is None:
                raise InvalidReference("attempt scope does not exist")
            state = (
                "cancelled"
                if row["state"] == "cancelled"
                else (
                    "cancelling"
                    if row["run_state"] == "cancelling" or row["campaign_state"] == "cancelling"
                    else row["state"]
                )
            )
            return {"state": state, "campaign_id": row["campaign_id"]}
        table = evaluation if scope_type == "evaluation" else release
        row = (
            connection.execute(
                select(table).where(table.c.id == scope_id).with_for_update()
                if lock
                else select(table).where(table.c.id == scope_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise InvalidReference(f"{scope_type} scope does not exist")
        campaign_id = None
        if scope_type == "evaluation":
            campaign_id = connection.execute(
                select(run.c.campaign_id)
                .select_from(
                    evaluation.join(attempt, evaluation.c.attempt_id == attempt.c.id).join(
                        run, attempt.c.run_id == run.c.id
                    )
                )
                .where(evaluation.c.id == scope_id)
            ).scalar_one()
        return {"state": row["state"], "campaign_id": campaign_id}

    def _scope_active(self, connection: Connection, scope_type: str, scope_id: UUID) -> bool:
        return bool(
            connection.execute(select(self._scope_active_clause(scope_type, scope_id))).scalar_one()
        )

    def _scope_active_clause(
        self, scope_type: str | None = None, scope_id: UUID | None = None
    ) -> Any:
        if scope_type is None:
            attempts = (
                select(attempt.c.id)
                .select_from(
                    attempt.join(run, attempt.c.run_id == run.c.id).join(
                        campaign, run.c.campaign_id == campaign.c.id
                    )
                )
                .where(
                    attempt.c.id == stage_job.c.attempt_id,
                    attempt.c.state.in_(("queued", "running")),
                    run.c.status.notin_(("cancelled", "cancelling", "failed")),
                    campaign.c.status.notin_(("cancelled", "cancelling", "failed")),
                )
            )
            evaluations = (
                select(evaluation.c.id)
                .select_from(
                    evaluation.join(attempt, evaluation.c.attempt_id == attempt.c.id)
                    .join(run, attempt.c.run_id == run.c.id)
                    .join(campaign, run.c.campaign_id == campaign.c.id)
                )
                .where(
                    evaluation.c.id == stage_job.c.evaluation_id,
                    evaluation.c.state.in_(("queued", "running")),
                    attempt.c.state != "cancelled",
                    run.c.status.notin_(("cancelled", "cancelling", "failed")),
                    campaign.c.status.notin_(("cancelled", "cancelling", "failed")),
                )
            )
            releases = select(release.c.id).where(
                release.c.id == stage_job.c.release_id, release.c.state.notin_(("withdrawn",))
            )
            return or_(attempts.exists(), evaluations.exists(), releases.exists())
        if scope_type == "attempt":
            query = (
                select(attempt.c.id)
                .select_from(
                    attempt.join(run, attempt.c.run_id == run.c.id).join(
                        campaign, run.c.campaign_id == campaign.c.id
                    )
                )
                .where(
                    attempt.c.id == scope_id,
                    attempt.c.state.in_(("queued", "running")),
                    run.c.status.notin_(("cancelled", "cancelling", "failed")),
                    campaign.c.status.notin_(("cancelled", "cancelling", "failed")),
                )
            )
        elif scope_type == "evaluation":
            query = (
                select(evaluation.c.id)
                .select_from(
                    evaluation.join(attempt, evaluation.c.attempt_id == attempt.c.id)
                    .join(run, attempt.c.run_id == run.c.id)
                    .join(campaign, run.c.campaign_id == campaign.c.id)
                )
                .where(
                    evaluation.c.id == scope_id,
                    evaluation.c.state.in_(("queued", "running")),
                    attempt.c.state != "cancelled",
                    run.c.status.notin_(("cancelled", "cancelling", "failed")),
                    campaign.c.status.notin_(("cancelled", "cancelling", "failed")),
                )
            )
        else:
            query = select(release.c.id).where(
                release.c.id == scope_id, release.c.state != "withdrawn"
            )
        return query.exists()

    def _has_unsatisfied_dependency_clause(self) -> Any:
        parent = stage_job.alias("dependency_parent")
        dep = stage_dependency.alias("dependency_edge")
        success = parent.c.state == "succeeded"
        gate_pass = and_(success, parent.c.quality_gate == "pass")
        gate_fail = and_(success, parent.c.quality_gate == "fail")
        terminal = and_(
            parent.c.state.in_(("succeeded", "skipped")),
            or_(
                parent.c.state != "skipped",
                dep.c.accepted_skip_reasons.contains(func.jsonb_build_array(parent.c.skip_reason)),
            ),
        )
        satisfied = or_(
            and_(dep.c.condition == "success", success),
            and_(dep.c.condition == "gate_pass", gate_pass),
            and_(dep.c.condition == "gate_fail", gate_fail),
            and_(dep.c.condition == "terminal", terminal),
        )
        return (
            select(dep.c.job_id)
            .select_from(dep.join(parent, parent.c.id == dep.c.prerequisite_job_id))
            .where(dep.c.job_id == stage_job.c.id, ~func.coalesce(satisfied, False))
            .exists()
        )

    def _unblock_dependents(self, connection: Connection, parent_id: UUID) -> None:
        children = (
            connection.execute(
                select(stage_job)
                .select_from(
                    stage_job.join(stage_dependency, stage_dependency.c.job_id == stage_job.c.id)
                )
                .where(
                    stage_dependency.c.prerequisite_job_id == parent_id,
                    stage_job.c.state == "blocked",
                )
                .with_for_update(of=stage_job)
            )
            .mappings()
            .all()
        )
        for child in children:
            edges = (
                connection.execute(
                    select(stage_dependency, stage_job)
                    .select_from(
                        stage_dependency.join(
                            stage_job, stage_dependency.c.prerequisite_job_id == stage_job.c.id
                        )
                    )
                    .where(stage_dependency.c.job_id == child["id"])
                )
                .mappings()
                .all()
            )
            resolved = all(
                _dependency_satisfied(edge) or edge["state"] in TERMINAL_JOB_STATES
                for edge in edges
            )
            if not resolved:
                continue
            satisfied = all(_dependency_satisfied(edge) for edge in edges)
            if satisfied:
                connection.execute(
                    update(stage_job)
                    .where(stage_job.c.id == child["id"])
                    .values(state="queued", row_version=stage_job.c.row_version + 1)
                )
                self._event(
                    connection,
                    child["id"],
                    "job_unblocked",
                    "scheduler",
                    details={"prerequisite_job_id": str(parent_id)},
                )
            else:
                unused_branch = any(
                    (
                        edge["state"] == "succeeded"
                        and edge["quality_gate"] in {"pass", "fail"}
                        and edge["condition"] in {"gate_pass", "gate_fail"}
                        and not _dependency_satisfied(edge)
                    )
                    or (
                        edge["state"] == "skipped"
                        and edge["skip_reason"] == "branch_not_selected"
                        and not _dependency_satisfied(edge)
                    )
                    for edge in edges
                )
                connection.execute(
                    update(stage_job)
                    .where(stage_job.c.id == child["id"])
                    .values(
                        state="skipped" if unused_branch else "dead",
                        skip_reason="branch_not_selected" if unused_branch else None,
                        error_code=None if unused_branch else "prerequisite_failed",
                        row_version=stage_job.c.row_version + 1,
                    )
                )
                self._event(
                    connection,
                    child["id"],
                    "job_skipped" if unused_branch else "job_blocked_by_prerequisite",
                    "scheduler",
                    details={
                        "prerequisite_job_id": str(parent_id),
                        "reason": "branch_not_selected" if unused_branch else "prerequisite_failed",
                    },
                )
                self._unblock_dependents(connection, child["id"])

    def _update_scope_completion(
        self, connection: Connection, scope_type: str, scope_id: UUID
    ) -> None:
        jobs = connection.execute(
            select(
                stage_job.c.state,
                stage_job.c.required,
                stage_job.c.quality_gate,
                stage_job.c.result_document,
            ).where(getattr(stage_job.c, f"{scope_type}_id") == scope_id)
        ).all()
        if jobs and all(row.state in TERMINAL_JOB_STATES for row in jobs):
            if scope_type == "attempt":
                model_failure = any(
                    row.required
                    and row.result_document is not None
                    and row.result_document.get("model_failure") is True
                    for row in jobs
                )
                infrastructure_failure = any(row.required and row.state == "dead" for row in jobs)
                connection.execute(
                    update(attempt)
                    .where(attempt.c.id == scope_id, attempt.c.state.in_(("queued", "running")))
                    .values(
                        state="failed" if model_failure or infrastructure_failure else "completed",
                        failure_class=(
                            "model_failure"
                            if model_failure
                            else "infra_blocked"
                            if infrastructure_failure
                            else None
                        ),
                        row_version=attempt.c.row_version + 1,
                    )
                )
                self._update_run_completion(connection, scope_id)
            elif scope_type == "evaluation" and any(
                row.required and row.state == "dead" for row in jobs
            ):
                connection.execute(
                    update(evaluation)
                    .where(
                        evaluation.c.id == scope_id, evaluation.c.state.in_(("queued", "running"))
                    )
                    .values(
                        state="failed",
                        failure_class="infra_blocked",
                        row_version=evaluation.c.row_version + 1,
                    )
                )

    @staticmethod
    def _mark_run_running(connection: Connection, attempt_id: UUID) -> None:
        run_id = connection.execute(
            select(attempt.c.run_id).where(attempt.c.id == attempt_id)
        ).scalar_one_or_none()
        if run_id is None:
            raise InvalidReference("attempt has no parent run")
        connection.execute(
            update(run)
            .where(run.c.id == run_id, run.c.status.in_(("planned", "queued")))
            .values(status="running", row_version=run.c.row_version + 1)
        )

    @staticmethod
    def _update_run_completion(connection: Connection, attempt_id: UUID) -> None:
        run_id = connection.execute(
            select(attempt.c.run_id).where(attempt.c.id == attempt_id)
        ).scalar_one_or_none()
        if run_id is None:
            raise InvalidReference("attempt has no parent run")

        # Serialize terminal checks for attempts that belong to the same run. The second
        # completing attempt must observe the first one's committed state before deciding the
        # aggregate run status, or concurrent completions can leave a fully finished run stuck
        # in `running`.
        run_status = connection.execute(
            select(run.c.status).where(run.c.id == run_id).with_for_update()
        ).scalar_one_or_none()
        if run_status is None:
            raise InvalidReference("attempt parent run does not exist")
        if run_status in {"completed", "failed", "cancelled"}:
            return

        attempt_states = tuple(
            connection.execute(select(attempt.c.state).where(attempt.c.run_id == run_id))
            .scalars()
            .all()
        )
        terminal = {"completed", "failed", "cancelled", "skipped"}
        if not attempt_states or any(state not in terminal for state in attempt_states):
            return

        if run_status == "cancelling" or all(state == "cancelled" for state in attempt_states):
            final_status = "cancelled"
        elif any(state == "failed" for state in attempt_states):
            final_status = "failed"
        elif any(state == "cancelled" for state in attempt_states):
            final_status = "cancelled"
        else:
            final_status = "completed"
        connection.execute(
            update(run)
            .where(run.c.id == run_id, run.c.status == run_status)
            .values(status=final_status, row_version=run.c.row_version + 1)
        )

    def _set_scope_state(
        self, connection: Connection, scope_type: str, scope_id: UUID, state: str
    ) -> None:
        table = {"attempt": attempt, "evaluation": evaluation, "release": release}[scope_type]
        connection.execute(
            update(table)
            .where(table.c.id == scope_id)
            .values(state=state, row_version=table.c.row_version + 1)
        )

    def _queue_slot_cleanup(self, connection: Connection, slot_id: UUID) -> None:
        slot = (
            connection.execute(
                select(capacity_slot).where(capacity_slot.c.id == slot_id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if slot is None:
            return
        if slot["guest_id"] is not None or slot["state"] == "busy":
            connection.execute(
                update(capacity_slot)
                .where(capacity_slot.c.id == slot_id)
                .values(
                    state="cleanup",
                    row_version=capacity_slot.c.row_version + 1,
                    updated_at=func.now(),
                )
            )
        else:
            self._free_slot(connection, slot_id)

    @staticmethod
    def _free_slot(connection: Connection, slot_id: UUID) -> None:
        worker_status = connection.execute(
            select(worker_registration.c.status)
            .select_from(
                worker_registration.join(
                    capacity_slot, capacity_slot.c.worker_id == worker_registration.c.id
                )
            )
            .where(capacity_slot.c.id == slot_id)
        ).scalar_one()
        connection.execute(
            update(capacity_slot)
            .where(capacity_slot.c.id == slot_id)
            .values(
                state="available"
                if worker_status == "active"
                else "draining"
                if worker_status == "draining"
                else "disabled",
                job_id=None,
                fence=None,
                guest_id=None,
                row_version=capacity_slot.c.row_version + 1,
                updated_at=func.now(),
            )
        )

    @staticmethod
    def _event(
        connection: Connection,
        job_id: UUID,
        event_kind: str,
        actor: str,
        *,
        fence: int | None = None,
        details: dict[str, object],
    ) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:job_id, 0))"),
            {"job_id": str(job_id)},
        )
        seq = connection.execute(
            select(func.coalesce(func.max(stage_job_event.c.event_seq), 0) + 1).where(
                stage_job_event.c.job_id == job_id
            )
        ).scalar_one()
        connection.execute(
            insert(stage_job_event).values(
                id=uuid4(),
                job_id=job_id,
                event_seq=seq,
                event_kind=event_kind,
                actor=actor,
                fence=fence,
                details=details,
            )
        )


def _retry_delay(logical_key: str, deliveries: int) -> int:
    delay = RETRY_DELAYS_SECONDS[min(max(deliveries - 1, 0), len(RETRY_DELAYS_SECONDS) - 1)]
    jitter = int(hashlib.sha256(f"{logical_key}:{deliveries}".encode()).hexdigest()[:4], 16) % 3
    return delay + jitter


def _topological_order(jobs: dict[str, JobDefinition]) -> tuple[str, ...]:
    pending = {
        key: {dependency.parent_key for dependency in job.dependencies} for key, job in jobs.items()
    }
    order: list[str] = []
    while pending:
        ready = sorted(key for key, parents in pending.items() if not parents)
        if not ready:
            raise InvalidState("job DAG contains a cycle")
        for key in ready:
            order.append(key)
            pending.pop(key)
        for parents in pending.values():
            parents.difference_update(ready)
    return tuple(order)


def _logical_key(scope_type: str, scope_id: UUID, key: str) -> str:
    digest = hashlib.sha256(f"{scope_type}:{scope_id}:{key}".encode("ascii")).hexdigest()
    return f"sha256:{digest}"


def _scope_type(job: Any) -> Literal["attempt", "evaluation", "release"]:
    return cast(
        Literal["attempt", "evaluation", "release"],
        next(
            kind for kind in ("attempt", "evaluation", "release") if job[f"{kind}_id"] is not None
        ),
    )


def _scope_id(job: Any) -> UUID:
    return cast(UUID, job[f"{_scope_type(job)}_id"])


def _dependency_satisfied(edge: Any) -> bool:
    state = edge["state"]
    condition = edge["condition"]
    if condition == "success":
        return bool(state == "succeeded")
    if condition == "gate_pass":
        return bool(state == "succeeded" and edge["quality_gate"] == "pass")
    if condition == "gate_fail":
        return bool(state == "succeeded" and edge["quality_gate"] == "fail")
    if state == "skipped":
        return bool(edge["skip_reason"] in edge["accepted_skip_reasons"])
    return bool(state == "succeeded")
