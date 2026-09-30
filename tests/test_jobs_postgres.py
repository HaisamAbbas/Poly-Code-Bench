"""Prompt 07 scheduler recovery checks on actual PostgreSQL, SeaweedFS, and Docker."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from migration_support import require_migrated_through
from polycodebench_core.application_errors import LeaseLost, PersistenceConflict
from polycodebench_core.jobs import (
    CapacitySlotSpec,
    JobDefinition,
    JobDependencySpec,
    StageOutcome,
    WorkerRegistrationSpec,
)
from polycodebench_orchestration.worker import WorkerService
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    artifact,
    artifact_quota,
    config_document,
    stage_execution,
    stage_job,
    stage_job_event,
)
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_persistence.runs import PostgresRunRepository
from polycodebench_runner.contracts import ExecRequest, SandboxSpec
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.rbac import Principal, Role
from polycodebench_services.runs import RunCreationService
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from test_persistence_postgres import _request, _seed

IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
IMAGE_DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"


@pytest.fixture(scope="module")
def database() -> Database:
    url = os.environ.get("PCB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(url).database or "").lower():
        pytest.fail("PCB_TEST_DATABASE_URL must use a dedicated database containing 'test'")
    instance = Database(url)
    with instance.engine.connect() as connection:
        require_migrated_through(connection, "8ac42e1d09bf")
    yield instance
    instance.dispose()


@pytest.fixture(scope="module")
def object_store() -> S3ArtifactStore:
    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.skip("PCB_OBJECT_STORE_ENDPOINT is not configured")
    store = S3ArtifactStore(
        endpoint_url=endpoint,
        buckets={
            "hidden": os.environ.get("PCB_BUCKET_HIDDEN", "pcb-p07-hidden"),
            "internal": os.environ.get("PCB_BUCKET_INTERNAL", "pcb-p07-internal"),
            "public": os.environ.get("PCB_BUCKET_PUBLIC", "pcb-p07-public"),
        },
    )
    store.ensure_buckets()
    return store


def _attempt(database: Database) -> UUID:
    ids = _seed(database.engine, samples_per_task=1)
    result = RunCreationService(PostgresRunRepository(database.engine)).create(
        Principal("prompt07-integration", frozenset({Role.OPERATOR})),
        _request(ids, samples_per_task=1),
        f"prompt07-{uuid4()}",
    )
    return result.attempt_ids[0]


def _repo(database: Database) -> PostgresJobRepository:
    return PostgresJobRepository(database.engine, campaign_concurrency=4, provider_concurrency=2)


def _register_worker(
    database: Database,
    store: S3ArtifactStore,
    *,
    label: str,
    queue_class: str = "solve",
) -> UUID:
    resource_artifacts = ArtifactRepository(database.engine, store, max_upload_bytes=500_000)
    resource_artifact = _verified_upload(
        resource_artifacts,
        f"worker resource configuration {label} {uuid4()}".encode(),
        domain=f"p07-resource-{uuid4().hex}",
    )
    config_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            insert(config_document).values(
                id=config_id,
                kind="resource_spec",
                version_label="prompt07-local-v1",
                digest="sha256:" + hashlib.sha256(str(config_id).encode()).hexdigest(),
                canonical_artifact_id=resource_artifact,
                schema_version=1,
                document={"resource_class": "small", "lane": "solve"},
            )
        )
    return _repo(database).register_worker(
        WorkerRegistrationSpec(
            workload_identity=f"worker-{label}-{uuid4()}",
            lane="solve",
            hardware_class="local-docker-development",
            driver_identity="LocalDockerSandboxProvider",
            allowed_queue_classes=(queue_class,),
            allowed_resource_classes=("small",),
            resource_spec_config_id=config_id,
            slots=(
                CapacitySlotSpec(
                    slot_key="slot-0", resource_class="small", resource_spec_config_id=config_id
                ),
            ),
        )
    )


def _definition(
    key: str = "solve", *, queue_class: str = "solve", provider_key: str = "fixture-provider"
) -> JobDefinition:
    return JobDefinition(
        key=key,
        stage="solve",
        input_digest="sha256:" + "0" * 64,
        queue_class=queue_class,
        resource_class="small",
        provider_key=provider_key,
    )


def _artifact_repo(database: Database, store: S3ArtifactStore) -> ArtifactRepository:
    domain = f"p07-{uuid4().hex}"
    with database.engine.begin() as connection:
        for visibility in ("hidden", "internal", "public"):
            connection.execute(
                insert(artifact_quota).values(
                    visibility=visibility,
                    encryption_domain=domain,
                    max_bytes=1_000_000,
                    used_bytes=0,
                    reserved_bytes=0,
                )
            )
    return ArtifactRepository(database.engine, store, max_upload_bytes=500_000)


def _verified_upload(repo: ArtifactRepository, body: bytes, domain: str = "p07-shared") -> UUID:
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    with repo._engine.begin() as connection:
        for visibility in ("hidden", "internal", "public"):
            connection.execute(
                pg_insert(artifact_quota)
                .values(
                    visibility=visibility,
                    encryption_domain=domain,
                    max_bytes=1_000_000,
                    used_bytes=0,
                    reserved_bytes=0,
                )
                .on_conflict_do_nothing(index_elements=["visibility", "encryption_domain"])
            )
    owner = f"worker-{uuid4()}"
    upload_id = repo.begin_upload(
        owner=owner,
        visibility="internal",
        encryption_domain=domain,
        expected_digest=digest,
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=upload_id, owner=owner, body=body)
    return repo.finalize(upload_id=upload_id, owner=owner)


def test_e2e07_competing_workers_fence_stale_delivery_and_idempotent_commit(
    database: Database, object_store: S3ArtifactStore
) -> None:
    repository = _repo(database)
    attempt_id = _attempt(database)
    provider_key = f"fixture-e2e07-{uuid4().hex}"
    queue_class = f"solve-e2e07-{uuid4().hex}"
    job_id = repository.create_dag(
        scope_type="attempt",
        scope_id=attempt_id,
        jobs=(_definition(provider_key=provider_key, queue_class=queue_class),),
        actor="scheduler",
    )["solve"]
    workers = (
        _register_worker(database, object_store, label="a", queue_class=queue_class),
        _register_worker(database, object_store, label="b", queue_class=queue_class),
    )
    barrier = Barrier(2)

    def claim(worker_id: UUID):
        barrier.wait(timeout=5)
        return repository.claim(worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(claim, workers))
    claims = [claim for claim in (first, second) if claim is not None]
    assert len(claims) == 1
    stale = claims[0]
    assert (
        repository.claim(workers[0] if stale.worker_id != str(workers[0]) else workers[1]) is None
    )

    with database.engine.begin() as connection:
        connection.execute(
            update(stage_job)
            .where(stage_job.c.id == job_id)
            .values(lease_until=func.now() - text("interval '1 second'"))
        )
    reaped = repository.reap_expired()
    assert any(item["job_id"] == job_id for item in reaped)
    with database.engine.begin() as connection:
        connection.execute(
            update(stage_job).where(stage_job.c.id == job_id).values(available_at=func.now())
        )
    fresh = repository.claim(UUID(stale.worker_id)) or repository.claim(
        workers[1] if stale.worker_id == str(workers[0]) else workers[0]
    )
    assert fresh is not None and fresh.fence > stale.fence

    artifacts = _artifact_repo(database, object_store)
    output_id = _verified_upload(artifacts, b"prompt07-e2e07-output")
    with pytest.raises(LeaseLost):
        repository.complete(
            stale, output_artifact_id=output_id, outcome=StageOutcome(quality_gate="pass")
        )
    assert (
        repository.complete(
            fresh, output_artifact_id=output_id, outcome=StageOutcome(quality_gate="pass")
        )
        == "completed"
    )
    assert (
        repository.complete(
            fresh, output_artifact_id=output_id, outcome=StageOutcome(quality_gate="pass")
        )
        == "replayed"
    )
    with pytest.raises(PersistenceConflict):
        other_output = _verified_upload(artifacts, b"different output", domain="p07-other")
        repository.complete(
            fresh, output_artifact_id=other_output, outcome=StageOutcome(quality_gate="pass")
        )
    with database.engine.connect() as connection:
        executions = (
            connection.execute(select(stage_execution).where(stage_execution.c.job_id == job_id))
            .mappings()
            .all()
        )
        events = (
            connection.execute(select(stage_job_event).where(stage_job_event.c.job_id == job_id))
            .mappings()
            .all()
        )
        current = (
            connection.execute(select(stage_job).where(stage_job.c.id == job_id)).mappings().one()
        )
    assert len(executions) == 2 and all(row["finished_at"] is not None for row in executions)
    assert current["state"] == "succeeded" and current["output_artifact_id"] == output_id
    assert {event["event_kind"] for event in events} >= {
        "job_created",
        "job_claimed",
        "lease_expired",
        "job_succeeded",
    }


def test_e2e08_reuses_verified_upload_after_precommit_crash_and_commit_ack_loss(
    database: Database, object_store: S3ArtifactStore
) -> None:
    repository = _repo(database)
    attempt_id = _attempt(database)
    provider_key = f"fixture-e2e08-{uuid4().hex}"
    queue_class = f"build-e2e08-{uuid4().hex}"
    job_id = repository.create_dag(
        scope_type="attempt",
        scope_id=attempt_id,
        jobs=(_definition("build", provider_key=provider_key, queue_class=queue_class),),
        actor="scheduler",
    )["build"]
    worker = _register_worker(database, object_store, label="crash", queue_class=queue_class)
    retry_worker = _register_worker(
        database, object_store, label="recovery", queue_class=queue_class
    )
    first = repository.claim(worker)
    assert first is not None
    artifacts = _artifact_repo(database, object_store)

    # Injected crash boundary: object and verified artifact row committed, job result not committed.
    bytes_after_crash = b"canonical result survives worker crash"
    output_id = _verified_upload(artifacts, bytes_after_crash)
    with database.engine.begin() as connection:
        connection.execute(
            update(stage_job)
            .where(stage_job.c.id == job_id)
            .values(lease_until=func.now() - text("interval '1 second'"))
        )
    repository.reap_expired()
    with database.engine.begin() as connection:
        connection.execute(
            update(stage_job).where(stage_job.c.id == job_id).values(available_at=func.now())
        )
    second = repository.claim(retry_worker)
    assert second is not None and second.fence > first.fence
    replayed_artifact = _verified_upload(artifacts, bytes_after_crash)
    assert replayed_artifact == output_id
    assert (
        repository.complete(second, output_artifact_id=output_id, outcome=StageOutcome())
        == "completed"
    )

    # Injected crash boundary: result transaction committed but worker lost the acknowledgement.
    assert (
        repository.complete(second, output_artifact_id=output_id, outcome=StageOutcome())
        == "replayed"
    )
    artifacts.collect_garbage(now=datetime.now(UTC) + timedelta(days=31))
    verified_record, verified_bytes = artifacts.read_verified(output_id)
    assert verified_record["id"] == output_id and verified_bytes == bytes_after_crash
    with database.engine.connect() as connection:
        output_count = connection.execute(
            select(func.count()).select_from(artifact).where(artifact.c.id == output_id)
        ).scalar_one()
        job = connection.execute(select(stage_job).where(stage_job.c.id == job_id)).mappings().one()
    assert output_count == 1 and job["output_artifact_id"] == output_id


def test_job_dag_uses_gate_conditions_and_only_accepts_named_scheduler_skips(
    database: Database, object_store: S3ArtifactStore
) -> None:
    repository = _repo(database)
    artifacts = _artifact_repo(database, object_store)
    output_id = _verified_upload(artifacts, b"wrong-but-successfully-executed-answer")

    attempt_id = _attempt(database)
    provider_key = f"fixture-dag-{uuid4().hex}"
    solve_queue = f"dag-solve-{uuid4().hex}"
    definitions = (
        _definition("solve", queue_class=solve_queue, provider_key=provider_key),
        JobDefinition(
            key="quality-pass",
            stage="quality_pass",
            input_digest="sha256:" + "1" * 64,
            queue_class=f"dag-{uuid4().hex}",
            resource_class="small",
            dependencies=(JobDependencySpec(parent_key="solve", condition="gate_pass"),),
        ),
        JobDefinition(
            key="quality-fail",
            stage="quality_fail",
            input_digest="sha256:" + "2" * 64,
            queue_class=f"dag-{uuid4().hex}",
            resource_class="small",
            dependencies=(JobDependencySpec(parent_key="solve", condition="gate_fail"),),
        ),
    )
    job_ids = repository.create_dag(
        scope_type="attempt", scope_id=attempt_id, jobs=definitions, actor="scheduler"
    )
    assert (
        repository.create_dag(
            scope_type="attempt", scope_id=attempt_id, jobs=definitions, actor="scheduler-retry"
        )
        == job_ids
    )
    with pytest.raises(PersistenceConflict):
        repository.create_dag(
            scope_type="attempt",
            scope_id=attempt_id,
            jobs=(_definition("solve", queue_class=solve_queue, provider_key=provider_key),),
            actor="scheduler-changed-replay",
        )
    worker = _register_worker(
        database, object_store, label="dag", queue_class=definitions[0].queue_class
    )
    claim = repository.claim(worker)
    assert claim is not None and claim.job_id == job_ids["solve"]
    repository.complete(
        claim,
        output_artifact_id=output_id,
        outcome=StageOutcome(quality_gate="fail", model_failure=True),
    )
    with database.engine.connect() as connection:
        states = dict(
            connection.execute(
                select(stage_job.c.stage, stage_job.c.state).where(
                    stage_job.c.id.in_(tuple(job_ids.values()))
                )
            ).all()
        )
        execution = (
            connection.execute(
                select(stage_execution).where(stage_execution.c.job_id == job_ids["solve"])
            )
            .mappings()
            .one()
        )
    assert states["quality_pass"] == "skipped"
    assert states["quality_fail"] == "queued"
    assert execution["result"] == "succeeded" and execution["failure_class"] is None
    recovery_worker = _register_worker(
        database,
        object_store,
        label="quality-fail",
        queue_class=definitions[2].queue_class,
    )
    recovery_claim = repository.claim(recovery_worker)
    assert recovery_claim is not None and recovery_claim.job_id == job_ids["quality-fail"]
    repository.complete(
        recovery_claim,
        output_artifact_id=output_id,
        outcome=StageOutcome(quality_gate="pass"),
    )
    with database.engine.connect() as connection:
        failed_attempt = connection.execute(
            text("SELECT state, failure_class FROM attempt WHERE id=:id"),
            {"id": attempt_id},
        ).one()
    assert tuple(failed_attempt) == ("failed", "model_failure")

    skipped_attempt = _attempt(database)
    skip_queue = f"skip-{uuid4().hex}"
    skipped = repository.create_dag(
        scope_type="attempt",
        scope_id=skipped_attempt,
        jobs=(
            _definition("optional-method", queue_class=skip_queue),
            JobDefinition(
                key="skip-aware",
                stage="finalize",
                input_digest="sha256:" + "3" * 64,
                queue_class=skip_queue,
                resource_class="small",
                dependencies=(
                    JobDependencySpec(
                        parent_key="optional-method",
                        condition="terminal",
                        accepted_skip_reasons=("method_not_applicable",),
                    ),
                ),
            ),
        ),
        actor="scheduler",
    )
    repository.skip_unstarted(
        skipped["optional-method"], reason="method_not_applicable", actor="scheduler"
    )
    with database.engine.connect() as connection:
        child_state = connection.execute(
            select(stage_job.c.state).where(stage_job.c.id == skipped["skip-aware"])
        ).scalar_one()
    assert child_state == "queued"

    # A skip from another branch is terminal evidence but cannot satisfy this edge.
    alien_attempt = _attempt(database)
    alien = repository.create_dag(
        scope_type="attempt",
        scope_id=alien_attempt,
        jobs=(
            _definition("optional-method", queue_class=f"alien-{uuid4().hex}"),
            JobDefinition(
                key="skip-aware",
                stage="finalize",
                input_digest="sha256:" + "4" * 64,
                queue_class=f"alien-{uuid4().hex}",
                resource_class="small",
                dependencies=(
                    JobDependencySpec(
                        parent_key="optional-method",
                        condition="terminal",
                        accepted_skip_reasons=("method_not_applicable",),
                    ),
                ),
            ),
        ),
        actor="scheduler",
    )
    repository.skip_unstarted(
        alien["optional-method"], reason="feature_disabled", actor="scheduler"
    )
    with database.engine.connect() as connection:
        alien_child = connection.execute(
            select(stage_job.c.state).where(stage_job.c.id == alien["skip-aware"])
        ).scalar_one()
    assert alien_child == "dead"


def test_infrastructure_deliveries_are_visible_bounded_and_worker_can_resume(
    database: Database, object_store: S3ArtifactStore
) -> None:
    repository = _repo(database)
    attempt_id = _attempt(database)
    provider_key = f"fixture-retry-{uuid4().hex}"
    queue_class = f"retry-{uuid4().hex}"
    job_id = repository.create_dag(
        scope_type="attempt",
        scope_id=attempt_id,
        jobs=(_definition("unstable", queue_class=queue_class, provider_key=provider_key),),
        actor="scheduler",
    )["unstable"]
    worker = _register_worker(database, object_store, label="retry", queue_class=queue_class)

    assert repository.set_worker_status(worker, status="draining")
    assert repository.claim(worker) is None
    assert repository.set_worker_status(worker, status="active")
    for delivery in range(1, 4):
        claim = repository.claim(worker)
        assert claim is not None and claim.deliveries == delivery
        next_state = repository.fail(
            claim,
            failure_class="provider_unavailable",
            failure_code="fixture_transport_down",
        )
        assert next_state == ("retry_wait" if delivery < 3 else "dead")
        if delivery < 3:
            with database.engine.begin() as connection:
                connection.execute(
                    update(stage_job)
                    .where(stage_job.c.id == job_id)
                    .values(available_at=func.now())
                )

    with database.engine.connect() as connection:
        job = connection.execute(select(stage_job).where(stage_job.c.id == job_id)).mappings().one()
        executions = (
            connection.execute(select(stage_execution).where(stage_execution.c.job_id == job_id))
            .mappings()
            .all()
        )
        attempt_state = connection.execute(
            text("SELECT state, failure_class FROM attempt WHERE id=:id"),
            {"id": attempt_id},
        ).one()
    assert job["state"] == "dead" and job["deliveries"] == job["max_deliveries"] == 3
    assert len(executions) == 3 and all(
        row["failure_class"] == "provider_unavailable" for row in executions
    )
    assert tuple(attempt_state) == ("failed", "infra_blocked")


def test_scheduler_database_role_has_least_privilege_grants(database: Database) -> None:
    with database.engine.connect() as connection:
        privileges = connection.execute(
            text(
                """
                SELECT
                  has_table_privilege('pcb_scheduler', 'stage_job', 'SELECT'),
                  has_table_privilege('pcb_scheduler', 'stage_job_event', 'INSERT'),
                  has_table_privilege('pcb_scheduler', 'stage_job_event', 'DELETE'),
                  has_column_privilege('pcb_scheduler', 'stage_execution', 'finished_at', 'UPDATE'),
                  has_table_privilege('pcb_scheduler', 'artifact', 'SELECT')
                """
            )
        ).one()
    assert tuple(privileges) == (True, True, False, True, True)


def test_campaign_and_provider_fairness_cap_concurrent_leases(
    database: Database, object_store: S3ArtifactStore
) -> None:
    repository = PostgresJobRepository(
        database.engine, campaign_concurrency=1, provider_concurrency=1
    )
    attempt_id = _attempt(database)
    queue_class = f"fair-{uuid4().hex}"
    provider_key = f"provider-{uuid4().hex}"
    jobs = repository.create_dag(
        scope_type="attempt",
        scope_id=attempt_id,
        jobs=(
            _definition("first", queue_class=queue_class, provider_key=provider_key),
            _definition("second", queue_class=queue_class, provider_key=provider_key),
        ),
        actor="scheduler",
    )
    workers = (
        _register_worker(database, object_store, label="fair-a", queue_class=queue_class),
        _register_worker(database, object_store, label="fair-b", queue_class=queue_class),
    )
    first = repository.claim(workers[0])
    assert first is not None and repository.claim(workers[1]) is None
    output_id = _verified_upload(
        ArtifactRepository(database.engine, object_store, max_upload_bytes=500_000),
        b"fairness fixture output",
    )
    repository.complete(first, output_artifact_id=output_id, outcome=StageOutcome())
    second = repository.claim(workers[1])
    assert second is not None and second.job_id != first.job_id
    assert {first.job_id, second.job_id} == set(jobs.values())


def test_e2e09_cancelled_live_docker_tool_guest_is_destroyed(
    database: Database, object_store: S3ArtifactStore
) -> None:
    if os.environ.get("PCB_TEST_DOCKER") != "1":
        pytest.skip("set PCB_TEST_DOCKER=1 to run the real local Docker worker lifecycle")
    repository = _repo(database)
    attempt_id = _attempt(database)
    prepare_queue = f"solve-p07-prepare-{uuid4().hex}"
    tool_queue = f"solve-p07-cancel-{uuid4().hex}"
    prepare_key = f"fixture-p07-prepare-{uuid4().hex}"
    tool_key = f"fixture-p07-tool-{uuid4().hex}"
    jobs = repository.create_dag(
        scope_type="attempt",
        scope_id=attempt_id,
        jobs=(
            _definition("prepare", queue_class=prepare_queue, provider_key=prepare_key),
            JobDefinition(
                key="tool",
                stage="tool",
                input_digest="sha256:" + "5" * 64,
                queue_class=tool_queue,
                resource_class="small",
                provider_key=tool_key,
                dependencies=(JobDependencySpec(parent_key="prepare", condition="success"),),
            ),
        ),
        actor="scheduler",
    )
    prepare_id = jobs["prepare"]
    tool_id = jobs["tool"]
    prepare_worker = _register_worker(
        database, object_store, label="prepare", queue_class=prepare_queue
    )
    prepare_claim = repository.claim(prepare_worker)
    assert prepare_claim is not None and prepare_claim.job_id == prepare_id
    prepare_artifact = _verified_upload(
        _artifact_repo(database, object_store), b"completed preparation checkpoint"
    )
    repository.complete(
        prepare_claim,
        output_artifact_id=prepare_artifact,
        outcome=StageOutcome(quality_gate="pass"),
    )
    worker_id = _register_worker(database, object_store, label="cancel", queue_class=tool_queue)
    provider = LocalDockerSandboxProvider(
        allowed_images={IMAGE: IMAGE_DIGEST},
        state_dir=Path.cwd() / ".cache" / f"p07-worker-{uuid4().hex}",
        provider_id="p07-worker",
    )
    started = threading.Event()

    async def execute(claim, handle, sandbox, artifacts):
        started.set()
        await sandbox.execute(
            handle,
            ExecRequest(argv=("python", "-c", "import time; time.sleep(60)"), timeout_seconds=80),
        )
        raise AssertionError("cancelled tool execution unexpectedly returned")

    def spec(claim) -> SandboxSpec:
        return SandboxSpec(
            stage_id="prompt07-tool",
            fence=claim.fence,
            lane="solve",
            image=IMAGE,
            image_digest=IMAGE_DIGEST,
            cpu_millis=500,
            memory_bytes=256 * 1024**2,
            disk_bytes=64 * 1024**2,
            pids_limit=32,
            timeout_seconds=100,
            ttl_seconds=300,
        )

    first_claim = repository.claim(worker_id)
    assert first_claim is not None and first_claim.job_id == tool_id
    worker = WorkerService(
        worker_id=worker_id,
        repository=repository,
        artifacts=_artifact_repo(database, object_store),
        sandbox=provider,
        spec_factory=spec,
        executor=execute,
        heartbeat_seconds=1,
    )

    async def scenario() -> None:
        task = asyncio.create_task(worker.run_claim(first_claim))
        assert await asyncio.to_thread(started.wait, 30)
        assert (
            repository.cancel_scope(
                "attempt", attempt_id, actor="operator", reason="integration cancellation"
            )
            == 1
        )
        assert repository.claim(worker_id) is None
        with pytest.raises(LeaseLost):
            await asyncio.wait_for(task, timeout=30)

    asyncio.run(scenario())
    with database.engine.connect() as connection:
        attempt_state = connection.execute(
            text("SELECT state, failure_class FROM attempt WHERE id=:id"), {"id": attempt_id}
        ).one()
        job_state = connection.execute(
            select(stage_job.c.state).where(stage_job.c.id == tool_id)
        ).scalar_one()
        prepare_row = (
            connection.execute(select(stage_job).where(stage_job.c.id == prepare_id))
            .mappings()
            .one()
        )
        slot = connection.execute(
            text("SELECT state, guest_id FROM capacity_slot WHERE worker_id=:id"), {"id": worker_id}
        ).one()
        kinds = set(
            connection.execute(
                select(stage_job_event.c.event_kind).where(stage_job_event.c.job_id == tool_id)
            ).scalars()
        )
        model_deliveries = connection.execute(
            text(
                "SELECT count(*) FROM call_delivery d JOIN call_intent i ON i.id = d.intent_id "
                "WHERE i.attempt_id = :id"
            ),
            {"id": attempt_id},
        ).scalar_one()
    assert tuple(attempt_state) == ("cancelled", None)
    assert job_state == "cancelled" and slot.state == "available" and slot.guest_id is None
    assert prepare_row["state"] == "succeeded"
    assert prepare_row["output_artifact_id"] == prepare_artifact
    assert {"job_cancelled", "guest_destroyed"} <= kinds
    # Prompt 08 has not implemented model calls; no usage is fabricated for this tool-only fixture.
    assert model_deliveries == 0
