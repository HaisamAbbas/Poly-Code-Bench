"""Prompt 33 drain drill on real PostgreSQL + SeaweedFS: drained workers take no new work and a
stale worker cannot commit after its lease is recovered (E2E-43 drain/stale-commit subcase).

Uses the operator entrypoints (`pcb-ops workers drain`, `pcb-scheduler reap`) rather than
repository calls wherever an operator would act.
"""

from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stdout
from uuid import uuid4

import pytest
from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.jobs import StageOutcome
from polycodebench_core.telemetry import configure_logging, log_context
from polycodebench_operations.cli import main as ops_main
from polycodebench_orchestration.cli import main as scheduler_main
from polycodebench_persistence.models import stage_job
from sqlalchemy import func, select, text, update
from test_jobs_postgres import (  # noqa: F401 - module fixtures
    _artifact_repo,
    _attempt,
    _definition,
    _register_worker,
    _repo,
    _verified_upload,
    database,
    object_store,
)


def _cli(main, argv: list[str]) -> tuple[int, object]:  # type: ignore[no-untyped-def]
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    output = buffer.getvalue().strip()
    return code, json.loads(output) if output else None


def test_drained_worker_takes_no_work_and_cannot_commit_after_recovery(
    database,  # noqa: F811 - shared module fixture from test_jobs_postgres
    object_store,  # noqa: F811
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setenv("PCB_DATABASE_URL", str(database.engine.url.render_as_string(False)))
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "prompt33-drill")
    repository = _repo(database)
    queue_class = f"solve-p33-{uuid4().hex[:12]}"
    provider_key = f"fixture-p33-{uuid4().hex[:12]}"
    job_id = repository.create_dag(
        scope_type="attempt",
        scope_id=_attempt(database),
        jobs=(_definition(queue_class=queue_class, provider_key=provider_key),),
        actor="scheduler",
    )["solve"]
    old_worker = _register_worker(
        database, object_store, label="old-release", queue_class=queue_class
    )
    stale_claim = repository.claim(old_worker)
    assert stale_claim is not None and stale_claim.job_id == job_id

    # Rollout: the operator drains the old-release worker while its stage is in flight.
    code, output = _cli(ops_main, ["workers", "drain", str(old_worker)])
    assert code == 0 and output["draining"] == {str(old_worker): True}

    # The old worker stalls past its lease; the operator's reaper recovers the job.
    with database.engine.begin() as connection:
        connection.execute(
            update(stage_job)
            .where(stage_job.c.id == job_id)
            .values(lease_until=func.now() - text("interval '1 second'"))
        )
    code, reaped = _cli(scheduler_main, ["reap", "--limit", "50"])
    assert code == 0 and any(item["job_id"] == str(job_id) for item in reaped)
    with database.engine.begin() as connection:  # skip the retry backoff, as E2E-07 does
        connection.execute(
            update(stage_job).where(stage_job.c.id == job_id).values(available_at=func.now())
        )

    # The drained worker now has a free slot and a ready job - and still takes nothing.
    assert repository.claim(old_worker) is None, "a draining worker must not take new work"
    new_worker = _register_worker(
        database, object_store, label="new-release", queue_class=queue_class
    )
    fresh = repository.claim(new_worker)
    assert fresh is not None and fresh.job_id == job_id and fresh.fence > stale_claim.fence

    artifacts = _artifact_repo(database, object_store)
    output_artifact = _verified_upload(artifacts, f"p33 output {uuid4()}".encode())
    outcome = StageOutcome(quality_gate="pass")
    with pytest.raises(LeaseLost):
        repository.complete(stale_claim, output_artifact_id=output_artifact, outcome=outcome)
    assert (
        repository.complete(fresh, output_artifact_id=output_artifact, outcome=outcome)
        == "completed"
    )
    with database.engine.connect() as connection:
        row = connection.execute(
            select(stage_job.c.state, stage_job.c.fence).where(stage_job.c.id == fresh.job_id)
        ).one()
    assert row.fence == fresh.fence, "the committed result carries the new fence only"


def test_runbook_containment_commands_disable_workers_and_cancel_attempts(
    database,  # noqa: F811 - shared module fixture from test_jobs_postgres
    object_store,  # noqa: F811
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """The exact scheduler commands the compromised-identity, outage and quarantine runbooks use."""

    monkeypatch.setenv("PCB_DATABASE_URL", str(database.engine.url.render_as_string(False)))
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "prompt33-runbook")
    repository = _repo(database)
    queue_class = f"solve-p33c-{uuid4().hex[:12]}"
    attempt_id = _attempt(database)
    job_id = repository.create_dag(
        scope_type="attempt",
        scope_id=attempt_id,
        jobs=(_definition(queue_class=queue_class, provider_key=f"fx-{uuid4().hex[:8]}"),),
        actor="scheduler",
    )["solve"]
    worker = _register_worker(database, object_store, label="suspect", queue_class=queue_class)

    code, output = _cli(scheduler_main, ["worker-status", str(worker), "disabled"])
    assert code == 0 and output == {"changed": True, "worker_id": str(worker)}
    assert repository.claim(worker) is None, "a disabled worker must not take work"

    code, output = _cli(
        scheduler_main, ["cancel-attempt", str(attempt_id), "--reason", "runbook drill"]
    )
    assert code == 0 and output["cancelled_jobs"] >= 1
    with database.engine.connect() as connection:
        state = connection.execute(
            select(stage_job.c.state).where(stage_job.c.id == job_id)
        ).scalar_one()
    assert state in {"cancelled", "skipped"}


def test_structured_logs_name_the_run_without_leaking_secrets(
    database,  # noqa: F811 - shared module fixture
) -> None:  # type: ignore[no-untyped-def]
    stream = io.StringIO()
    configure_logging(environment="integration", role="scheduler", stream=stream)
    url = database.engine.url.render_as_string(hide_password=False)
    with log_context(run_id="run-p33", job_id="job-p33", fence=9):
        logging.getLogger("pcb.p33").error("database call failed for %s", url)
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert record["run_id"] == "run-p33" and record["fence"] == "9"
    password = database.engine.url.password
    if password:
        assert password not in stream.getvalue()
