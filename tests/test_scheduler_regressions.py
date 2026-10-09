"""Independent scheduler regressions discovered during the Prompt 07 review."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from polycodebench_core.application_errors import LeaseLost, PersistenceConflict
from polycodebench_core.jobs import JobDefinition, JobDependencySpec, StageOutcome
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    attempt,
    capacity_slot,
    config_document,
    evaluation,
    run,
    stage_job,
)
from sqlalchemy import event, func, insert, select, text, update
from test_jobs_postgres import (
    _artifact_repo,
    _attempt,
    _MemoryArtifactStore,
    _register_worker,
    _verified_upload,
)
from test_jobs_postgres import (
    _definition as _job_definition,
)
from test_jobs_postgres import (
    database as database,
)
from test_jobs_postgres import (
    object_store as object_store,
)


def _definition(key="solve", *, queue_class="solve", provider_key=None):
    return _job_definition(
        key, queue_class=queue_class, provider_key=provider_key or f"review-provider-{uuid4().hex}"
    )


@pytest.mark.parametrize("cap", ["provider", "campaign"])
def test_concurrent_claims_cannot_exceed_fairness_cap(database, object_store, cap):
    repo = PostgresJobRepository(
        database.engine,
        campaign_concurrency=1 if cap == "campaign" else 4,
        provider_concurrency=1 if cap == "provider" else 4,
    )
    queue = f"review-fair-{uuid4().hex}"
    provider = f"review-provider-{uuid4().hex}"
    if cap == "campaign":
        repo.create_dag(
            scope_type="attempt",
            scope_id=_attempt(database),
            jobs=tuple(
                _definition(key=f"solve-{i}", queue_class=queue, provider_key=f"{provider}-{i}")
                for i in range(2)
            ),
            actor="review",
        )
    for _ in range(2 if cap == "provider" else 0):
        repo.create_dag(
            scope_type="attempt",
            scope_id=_attempt(database),
            jobs=(_definition(queue_class=queue, provider_key=provider),),
            actor="review",
        )
    workers = [
        _register_worker(database, object_store, label="fair", queue_class=queue) for _ in range(2)
    ]
    selected = Barrier(2)

    def rendezvous(conn, cursor, statement, parameters, context, executemany):
        if "ORDER BY stage_job.priority DESC" in statement:
            selected.wait(timeout=5)

    event.listen(database.engine, "after_cursor_execute", rendezvous)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(repo.claim, workers))
    finally:
        event.remove(database.engine, "after_cursor_execute", rendezvous)
    assert sum(claim is not None for claim in claims) == 1


def test_dead_prerequisite_propagates_without_leaving_scope_stalled(database, object_store):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    queue = f"review-dead-{uuid4().hex}"
    jobs = repo.create_dag(
        scope_type="attempt",
        scope_id=scope,
        actor="review",
        jobs=(
            _definition(queue_class=queue).model_copy(update={"max_deliveries": 1}),
            JobDefinition(
                key="child",
                stage="build",
                input_digest="sha256:" + "b" * 64,
                queue_class=queue,
                resource_class="small",
                dependencies=(JobDependencySpec(parent_key="solve"),),
            ),
        ),
    )
    claim = repo.claim(_register_worker(database, object_store, label="dead", queue_class=queue))
    assert claim is not None
    assert repo.fail(claim, failure_class="transport_unavailable") == "dead"
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                select(stage_job.c.state).where(stage_job.c.id == jobs["child"])
            ).scalar_one()
            == "dead"
        )
        assert (
            conn.execute(select(attempt.c.state).where(attempt.c.id == scope)).scalar_one()
            == "failed"
        )


def test_successful_gate_does_not_classify_unused_branch_as_infrastructure_failure(
    database, object_store
):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    queue = f"review-branch-{uuid4().hex}"
    jobs = repo.create_dag(
        scope_type="attempt",
        scope_id=scope,
        actor="review",
        jobs=(
            _definition(queue_class=queue),
            JobDefinition(
                key="fallback",
                stage="fallback",
                input_digest="sha256:" + "b" * 64,
                queue_class=queue,
                resource_class="small",
                dependencies=(JobDependencySpec(parent_key="solve", condition="gate_fail"),),
            ),
        ),
    )
    claim = repo.claim(_register_worker(database, object_store, label="branch", queue_class=queue))
    output = _verified_upload(_artifact_repo(database, object_store), b"successful reference gate")
    repo.complete(claim, output_artifact_id=output, outcome=StageOutcome(quality_gate="pass"))
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                select(stage_job.c.state).where(stage_job.c.id == jobs["fallback"])
            ).scalar_one()
            == "skipped"
        )
        assert (
            conn.execute(select(attempt.c.state).where(attempt.c.id == scope)).scalar_one()
            == "completed"
        )


def test_completion_replay_rejects_changed_gate_with_same_artifact(database, object_store):
    repo = PostgresJobRepository(database.engine)
    queue = f"review-replay-{uuid4().hex}"
    repo.create_dag(
        scope_type="attempt",
        scope_id=_attempt(database),
        actor="review",
        jobs=(_definition(queue_class=queue),),
    )
    claim = repo.claim(_register_worker(database, object_store, label="replay", queue_class=queue))
    output = _verified_upload(
        _artifact_repo(database, object_store), b"same artifact changed outcome"
    )
    repo.complete(claim, output_artifact_id=output, outcome=StageOutcome(quality_gate="pass"))
    with pytest.raises(PersistenceConflict):
        repo.complete(
            claim,
            output_artifact_id=output,
            outcome=StageOutcome(quality_gate="fail", model_failure=True),
        )


def test_busy_slot_with_guest_creation_in_flight_requires_cleanup_after_expiry(
    database, object_store
):
    repo = PostgresJobRepository(database.engine)
    queue = f"review-create-{uuid4().hex}"
    repo.create_dag(
        scope_type="attempt",
        scope_id=_attempt(database),
        actor="review",
        jobs=(_definition(queue_class=queue),),
    )
    worker = _register_worker(database, object_store, label="create", queue_class=queue)
    claim = repo.claim(worker)
    with database.engine.begin() as conn:
        conn.execute(
            update(capacity_slot)
            .where(capacity_slot.c.id == claim.slot_id)
            .values(state="busy", row_version=capacity_slot.c.row_version + 1)
        )
        conn.execute(
            update(stage_job)
            .where(stage_job.c.id == claim.job_id)
            .values(
                lease_until=func.now() - text("interval '1 second'"),
                row_version=stage_job.c.row_version + 1,
            )
        )
    repo.reap_expired()
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                select(capacity_slot.c.state).where(capacity_slot.c.id == claim.slot_id)
            ).scalar_one()
            == "cleanup"
        )


def _evaluation(database, scope):
    identity = uuid4()
    with database.engine.begin() as conn:
        policy = conn.execute(select(config_document.c.id).limit(1)).scalar_one()
        conn.execute(
            insert(evaluation).values(
                id=identity,
                attempt_id=scope,
                policy_config_id=policy,
                oracle_digest="sha256:" + "e" * 64,
                state="queued",
                gate="unknown",
            )
        )
    return identity


def test_stale_cleanup_cannot_release_a_new_delivery_guest(database, object_store):
    repo = PostgresJobRepository(database.engine)
    queue = f"review-cleanup-{uuid4().hex}"
    repo.create_dag(
        scope_type="attempt",
        scope_id=_attempt(database),
        actor="review",
        jobs=(_definition(queue_class=queue),),
    )
    worker = _register_worker(database, object_store, label="cleanup", queue_class=queue)
    first = repo.claim(worker)
    repo.begin_dispatch(first)
    repo.bind_guest(first, "original-guest")
    repo.fail(first, failure_class="transport_unavailable")
    repo.confirm_slot_cleanup(first, actor=first.worker_id, destruction_verified=True)
    with database.engine.begin() as conn:
        conn.execute(
            update(stage_job).where(stage_job.c.id == first.job_id).values(available_at=func.now())
        )
    second = repo.claim(worker)
    assert second.slot_id == first.slot_id and second.fence > first.fence
    repo.begin_dispatch(second)
    repo.bind_guest(second, "new-guest")
    repo.fail(second, failure_class="transport_unavailable")
    with pytest.raises(LeaseLost):
        repo.confirm_slot_cleanup(first, actor=first.worker_id, destruction_verified=True)
    with database.engine.connect() as conn:
        row = conn.execute(
            select(capacity_slot.c.state, capacity_slot.c.guest_id).where(
                capacity_slot.c.id == second.slot_id
            )
        ).one()
    assert tuple(row) == ("cleanup", "new-guest")
    repo.confirm_slot_cleanup(second, actor=second.worker_id, destruction_verified=True)


def test_cancellation_waits_for_locked_jobs_and_records_every_revocation(database):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    job = repo.create_dag(
        scope_type="attempt",
        scope_id=scope,
        actor="review",
        jobs=(_definition(queue_class=f"review-locked-{uuid4().hex}"),),
    )["solve"]
    selecting = Event()

    def before_select(conn, cursor, statement, parameters, context, executemany):
        if "WHERE stage_job.attempt_id" in statement and "FOR UPDATE" in statement:
            selecting.set()

    event.listen(database.engine, "before_cursor_execute", before_select)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.engine.begin() as conn:
                conn.execute(select(stage_job).where(stage_job.c.id == job).with_for_update())
                future = pool.submit(
                    repo.cancel_scope, "attempt", scope, actor="review", reason="locked job"
                )
                assert selecting.wait(timeout=5)
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.1)
            assert future.result(timeout=10) == 1
    finally:
        event.remove(database.engine, "before_cursor_execute", before_select)
    with database.engine.connect() as conn:
        assert (
            conn.execute(select(stage_job.c.state).where(stage_job.c.id == job)).scalar_one()
            == "cancelled"
        )
    assert [item["event_kind"] for item in repo.events(job)] == ["job_created", "job_cancelled"]


def test_parent_run_revocation_blocks_evaluation_dispatch_and_heartbeat(database, object_store):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    evaluation_id = _evaluation(database, scope)
    queue = f"review-eval-{uuid4().hex}"
    repo.create_dag(
        scope_type="evaluation",
        scope_id=evaluation_id,
        actor="review",
        jobs=(_definition(queue_class=queue),),
    )
    claim = repo.claim(_register_worker(database, object_store, label="eval", queue_class=queue))
    with database.engine.begin() as conn:
        run_id = conn.execute(select(attempt.c.run_id).where(attempt.c.id == scope)).scalar_one()
        conn.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(status="cancelled", row_version=run.c.row_version + 1)
        )
    assert repo.dispatch_allowed(claim) is False
    assert repo.heartbeat(claim) is False
    with pytest.raises(LeaseLost):
        repo.bind_guest(claim, "unauthorized-guest")


def test_attempt_cancellation_cascades_to_active_evaluation(database):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    with database.engine.connect() as conn:
        run_id = conn.execute(select(attempt.c.run_id).where(attempt.c.id == scope)).scalar_one()
    evaluation_id = _evaluation(database, scope)
    queue = f"review-cascade-{uuid4().hex}"
    repo.create_dag(
        scope_type="evaluation",
        scope_id=evaluation_id,
        actor="review",
        jobs=(_definition(queue_class=queue),),
    )
    object_store = _MemoryArtifactStore()
    claim = repo.claim(_register_worker(database, object_store, label="cascade", queue_class=queue))
    assert repo.cancel_scope("attempt", scope, actor="review", reason="cascade") == 1
    with database.engine.connect() as conn:
        assert (
            conn.execute(
                select(evaluation.c.state).where(evaluation.c.id == evaluation_id)
            ).scalar_one()
            == "cancelled"
        )
        assert conn.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one() == (
            "cancelled"
        )
    assert repo.dispatch_allowed(claim) is False
    repo.confirm_slot_cleanup(claim, actor=claim.worker_id, destruction_verified=True)


def test_evaluation_retry_exhaustion_is_infrastructure_blocked(database, object_store):
    repo = PostgresJobRepository(database.engine)
    evaluation_id = _evaluation(database, _attempt(database))
    queue = f"review-eval-dead-{uuid4().hex}"
    repo.create_dag(
        scope_type="evaluation",
        scope_id=evaluation_id,
        actor="review",
        jobs=(_definition(queue_class=queue).model_copy(update={"max_deliveries": 1}),),
    )
    claim = repo.claim(
        _register_worker(database, object_store, label="eval-dead", queue_class=queue)
    )
    repo.fail(claim, failure_class="analyzer_unavailable")
    with database.engine.connect() as conn:
        row = conn.execute(
            select(evaluation.c.state, evaluation.c.failure_class).where(
                evaluation.c.id == evaluation_id
            )
        ).one()
    assert tuple(row) == ("failed", "infra_blocked")


@pytest.mark.parametrize(
    ("gate", "expected_state", "expected_failure"),
    (
        ("pass", "ready", None),
        ("fail", "ready", None),
        ("unknown", "failed", "evaluator_incomplete"),
    ),
)
def test_evaluation_evidence_and_gate_commit_with_its_leased_job(
    database, gate, expected_state, expected_failure
):
    repo = PostgresJobRepository(database.engine)
    evaluation_id = _evaluation(database, _attempt(database))
    queue = f"review-evaluation-complete-{uuid4().hex}"
    memory_store = _MemoryArtifactStore()
    job_id = repo.create_dag(
        scope_type="evaluation",
        scope_id=evaluation_id,
        actor="evaluation-completion-fixture",
        jobs=(
            JobDefinition(
                key="evaluate",
                stage="evaluate",
                input_digest="sha256:" + "c" * 64,
                queue_class=queue,
                resource_class="small",
            ),
        ),
    )["evaluate"]
    worker_id = _register_worker(
        database,
        memory_store,
        label="evaluation-complete",
        queue_class=queue,
    )
    claim = repo.claim(worker_id)
    assert claim is not None and claim.job_id == job_id
    evidence_artifact = _verified_upload(
        _artifact_repo(database, memory_store), b"private evaluation evidence"
    )

    repo.complete(
        claim,
        output_artifact_id=evidence_artifact,
        outcome=StageOutcome(quality_gate=gate),
        evaluation_gate=gate,
    )

    with database.engine.connect() as connection:
        result = connection.execute(
            select(
                evaluation.c.state,
                evaluation.c.gate,
                evaluation.c.failure_class,
                evaluation.c.evidence_manifest_id,
            ).where(evaluation.c.id == evaluation_id)
        ).one()
    assert tuple(result) == (expected_state, gate, expected_failure, evidence_artifact)


def test_event_details_cannot_override_authoritative_outcome(database, object_store):
    repo = PostgresJobRepository(database.engine)
    queue = f"review-event-{uuid4().hex}"
    repo.create_dag(
        scope_type="attempt",
        scope_id=_attempt(database),
        actor="review",
        jobs=(_definition(queue_class=queue),),
    )
    claim = repo.claim(_register_worker(database, object_store, label="event", queue_class=queue))
    output = _verified_upload(_artifact_repo(database, object_store), b"event integrity")
    repo.complete(
        claim,
        output_artifact_id=output,
        outcome=StageOutcome(
            quality_gate="fail",
            model_failure=True,
            event_details={"model_failure": False, "quality_gate": "pass"},
        ),
    )
    result = next(row for row in repo.events(claim.job_id) if row["event_kind"] == "job_succeeded")
    assert result["details"]["model_failure"] is True
    assert result["details"]["quality_gate"] == "fail"


def test_parallel_parent_completions_unblock_join_and_complete_scope(database, object_store):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    queue = f"review-join-{uuid4().hex}"
    jobs = repo.create_dag(
        scope_type="attempt",
        scope_id=scope,
        actor="review",
        jobs=(
            _definition(key="first", queue_class=queue),
            _definition(key="second", queue_class=queue),
            JobDefinition(
                key="join",
                stage="finalize",
                input_digest="sha256:" + "b" * 64,
                queue_class=queue,
                resource_class="small",
                dependencies=(
                    JobDependencySpec(parent_key="first"),
                    JobDependencySpec(parent_key="second"),
                ),
            ),
        ),
    )
    workers = [
        _register_worker(database, object_store, label="join", queue_class=queue) for _ in range(2)
    ]
    claims = [repo.claim(worker) for worker in workers]
    output = _verified_upload(_artifact_repo(database, object_store), b"joined outputs")
    start = Barrier(2)

    def complete(claim):
        start.wait(timeout=5)
        return repo.complete(claim, output_artifact_id=output, outcome=StageOutcome())

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(complete, claims)) == ["completed", "completed"]
    final = repo.claim(workers[0])
    assert final is not None and final.job_id == jobs["join"]
    repo.complete(final, output_artifact_id=output, outcome=StageOutcome())
    with database.engine.connect() as conn:
        assert (
            conn.execute(select(attempt.c.state).where(attempt.c.id == scope)).scalar_one()
            == "completed"
        )


# PolyCodeBench-Architecture-v1.md section 5.4 (failure classification) and section 10.3
# (missing and incomplete work): a model failure, such as a malformed final artifact, counts
# against the attempt and is scored as zero, so it is a terminal attempt outcome inside a run that
# still completes. An infrastructure failure is missing data and keeps failing the run.
@pytest.mark.parametrize(
    ("outcome", "expected_attempt", "expected_failure", "expected_run"),
    [
        (StageOutcome(), "completed", None, "completed"),
        (
            StageOutcome(quality_gate="fail", model_failure=True),
            "failed",
            "model_failure",
            "completed",
        ),
        (None, "failed", "infra_blocked", "failed"),
    ],
    ids=["success", "model_failure_scores_zero", "infrastructure_failure"],
)
def test_run_lifecycle_tracks_claim_and_terminal_attempt(
    database, outcome, expected_attempt, expected_failure, expected_run
):
    repo = PostgresJobRepository(database.engine)
    scope = _attempt(database)
    queue = f"review-run-life-{uuid4().hex}"
    repo.create_dag(
        scope_type="attempt",
        scope_id=scope,
        actor="review",
        jobs=(_definition(queue_class=queue).model_copy(update={"max_deliveries": 1}),),
    )
    object_store = _MemoryArtifactStore()
    worker = _register_worker(database, object_store, label="run-life", queue_class=queue)
    with database.engine.connect() as conn:
        run_id = conn.execute(select(attempt.c.run_id).where(attempt.c.id == scope)).scalar_one()
        assert conn.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one() == "queued"

    claim = repo.claim(worker)
    assert claim is not None
    with database.engine.connect() as conn:
        assert (
            conn.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one() == "running"
        )

    if outcome is None:
        assert repo.fail(claim, failure_class="transport_unavailable") == "dead"
    else:
        output = _verified_upload(_artifact_repo(database, object_store), b"run lifecycle result")
        repo.complete(claim, output_artifact_id=output, outcome=outcome)
    with database.engine.connect() as conn:
        assert tuple(
            conn.execute(
                select(attempt.c.state, attempt.c.failure_class).where(attempt.c.id == scope)
            ).one()
        ) == (expected_attempt, expected_failure)
        assert conn.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one() == (
            expected_run
        )


def test_model_failure_beside_graded_attempt_completes_run(database):
    """Architecture v1 sections 5.4/10.3: a model-failure zero does not block its sibling."""
    repo = PostgresJobRepository(database.engine)
    object_store = _MemoryArtifactStore()
    first_attempt = _attempt(database)
    queue = f"review-run-mixed-{uuid4().hex}"
    with database.engine.begin() as conn:
        first = conn.execute(
            select(attempt.c.run_id, attempt.c.task_version_id).where(attempt.c.id == first_attempt)
        ).one()
        second_attempt = uuid4()
        conn.execute(
            insert(attempt).values(
                id=second_attempt,
                run_id=first.run_id,
                task_version_id=first.task_version_id,
                sample_index=1,
                seed=1,
                state="queued",
                row_version=0,
            )
        )
    for scope in (first_attempt, second_attempt):
        repo.create_dag(
            scope_type="attempt",
            scope_id=scope,
            actor="review",
            jobs=(_definition(queue_class=queue),),
        )
    workers = [
        _register_worker(database, object_store, label="run-mixed", queue_class=queue)
        for _ in range(2)
    ]
    claims = [repo.claim(worker) for worker in workers]
    assert all(claim is not None for claim in claims)
    output = _verified_upload(_artifact_repo(database, object_store), b"mixed run result")
    outcomes = (StageOutcome(quality_gate="fail", model_failure=True), StageOutcome())
    for claim, outcome in zip(claims, outcomes, strict=True):
        repo.complete(claim, output_artifact_id=output, outcome=outcome)
    with database.engine.connect() as conn:
        states = sorted(
            tuple(row)
            for row in conn.execute(
                select(attempt.c.state, attempt.c.failure_class).where(
                    attempt.c.run_id == first.run_id
                )
            )
        )
        assert states == [("completed", None), ("failed", "model_failure")]
        assert conn.execute(select(run.c.status).where(run.c.id == first.run_id)).scalar_one() == (
            "completed"
        )


def test_scheduler_run_lifecycle_grant_is_column_scoped(database):
    with database.engine.connect() as conn:
        permissions = conn.execute(
            text(
                "SELECT has_column_privilege('pcb_scheduler', 'run', 'status', 'UPDATE'), "
                "has_column_privilege('pcb_scheduler', 'run', 'row_version', 'UPDATE'), "
                "has_column_privilege('pcb_scheduler', 'run', 'created_by', 'UPDATE')"
            )
        ).one()
    assert tuple(permissions) == (True, True, False)


def test_concurrent_attempt_completion_finishes_parent_run(database):
    repo = PostgresJobRepository(database.engine)
    object_store = _MemoryArtifactStore()
    first_attempt = _attempt(database)
    queue = f"review-run-join-{uuid4().hex}"
    with database.engine.begin() as conn:
        first = conn.execute(
            select(attempt.c.run_id, attempt.c.task_version_id).where(attempt.c.id == first_attempt)
        ).one()
        second_attempt = uuid4()
        conn.execute(
            insert(attempt).values(
                id=second_attempt,
                run_id=first.run_id,
                task_version_id=first.task_version_id,
                sample_index=1,
                seed=1,
                state="queued",
                row_version=0,
            )
        )
    for scope in (first_attempt, second_attempt):
        repo.create_dag(
            scope_type="attempt",
            scope_id=scope,
            actor="review",
            jobs=(_definition(queue_class=queue),),
        )
    workers = [
        _register_worker(database, object_store, label="run-join", queue_class=queue)
        for _ in range(2)
    ]
    claims = [repo.claim(worker) for worker in workers]
    assert all(claim is not None for claim in claims)
    output = _verified_upload(_artifact_repo(database, object_store), b"parallel run result")
    start = Barrier(2)

    def complete(claim):
        start.wait(timeout=5)
        return repo.complete(claim, output_artifact_id=output, outcome=StageOutcome())

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(complete, claims)) == ["completed", "completed"]
    with database.engine.connect() as conn:
        assert conn.execute(select(run.c.status).where(run.c.id == first.run_id)).scalar_one() == (
            "completed"
        )
