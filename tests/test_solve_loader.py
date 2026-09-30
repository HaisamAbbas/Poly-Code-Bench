# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module
"""Assignment loading from persisted records and the worker stage executor (no sandbox needed).

EVIDENCE LABEL: real PostgreSQL and artifact store, the authored ``taskpacks/admission-smoke`` task
frozen through the real admission path. The model is a deterministic FIXTURE.
"""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from model_gateway_support import ScriptedTransport, ok
from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.jobs import JobClaim
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.solve.executor import SolveStageExecutor, stage_outcome
from polycodebench_orchestration.solve.loader import DatabaseAssignmentLoader
from polycodebench_persistence.models import attempt, config_document, run
from polycodebench_persistence.tasks import PostgresTaskRepository
from polycodebench_services.rbac import Principal, Role
from polycodebench_services.task_packages import TaskPackageImporter
from polycodebench_services.tasks import TaskAdmissionService
from solve_support import PROTOCOLS, Harness
from sqlalchemy import insert, select
from test_model_gateway_postgres import artifacts, build_world, database  # noqa: F401
from test_solve_sessions import _seed_quota
from test_task_admission_postgres import _execution_report, _seed_artifact, _task_document

PACK = Path(__file__).resolve().parents[1] / "taskpacks" / "admission-smoke"


class Jobs:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def dispatch_allowed(self, claim: JobClaim) -> bool:
        return self.allowed


def _claim(attempt_id: UUID) -> JobClaim:
    return JobClaim(
        job_id=uuid4(),
        execution_id=uuid4(),
        slot_id=uuid4(),
        worker_id=str(uuid4()),
        slot_key="slot-0",
        stage="solve",
        scope_type="attempt",
        scope_id=attempt_id,
        input_artifact_id=None,
        input_digest="sha256:" + "0" * 64,
        resource_class="small",
        queue_class="solve",
        fence=1,
        deliveries=1,
        lease_until_epoch=2**40,
    )


@pytest.fixture
def persisted(database, artifacts):  # type: ignore[no-untyped-def]
    """A real frozen task, run and attempt whose model config is the registered fixture config."""
    _seed_quota(database)
    world = build_world(database, artifacts)
    h = Harness(world, database.engine, artifacts)
    imported = TaskPackageImporter().import_package(PACK)
    domain = f"p09-{uuid4().hex}"
    manifest_id = _seed_artifact(database.engine, "internal", imported.manifest_digest, domain)
    visible_id = h.store().put(
        imported.visible_archive, kind="visible_bundle", media_type="application/zip"
    )
    hidden_id = _seed_artifact(database.engine, "hidden", imported.hidden_digest, domain)
    report = _execution_report(imported)
    document = _task_document(
        task_id=f"p09-{uuid4().hex[:12]}",
        report_digest=report.report_digest,
        visible_id=visible_id,
        hidden_id=hidden_id,
        imported=imported,
    )
    version_id = TaskAdmissionService(PostgresTaskRepository(database.engine)).freeze_task_version(
        principal=Principal(subject_id="p09-curator", roles=frozenset({Role.CURATOR})),
        document=document,
        execution_report=report,
        manifest_digest=imported.manifest_digest,
        manifest_artifact_id=manifest_id,
        visible_artifact_id=visible_id,
        hidden_artifact_id=hidden_id,
        request_id=f"p09-{uuid4()}",
    )
    model_digest = canonical_document_digest(world.config)
    run_config = {
        "kind": "run_config",
        "model_config_digest": model_digest,
        "protocol_id": "single-shot-v1",
    }
    with database.engine.begin() as connection:
        base = connection.execute(select(run).where(run.c.id == world.run_id)).mappings().one()
        config_id = uuid4()
        connection.execute(
            insert(config_document).values(
                id=config_id,
                kind="run_config",
                version_label="p09",
                digest="sha256:" + sha256(f"p09-{config_id}".encode()).hexdigest(),
                canonical_artifact_id=manifest_id,
                schema_version=1,
                document=run_config,
            )
        )
        run_id, attempt_id = uuid4(), uuid4()
        connection.execute(
            insert(run).values(
                id=run_id,
                campaign_id=base["campaign_id"],
                config_document_id=config_id,
                task_set_id=base["task_set_id"],
                model_revision_id=base["model_revision_id"],
                status="planned",
                created_by="p09",
            )
        )
        connection.execute(
            insert(attempt).values(
                id=attempt_id,
                run_id=run_id,
                task_version_id=version_id,
                sample_index=0,
                seed=2**63 + 12345,
                state="queued",
            )
        )
    campaign = world.accounts["campaign"]
    run_account = world.ledger.ensure_account(
        scope_kind="run",
        scope_id=str(run_id),
        hard_limit_micro_usd=10**9,
        parent_account_id=campaign,
    )
    world.ledger.ensure_account(
        scope_kind="attempt",
        scope_id=str(attempt_id),
        hard_limit_micro_usd=10**9,
        parent_account_id=run_account,
    )
    return h, imported, attempt_id


def test_loader_builds_the_assignment_from_visible_data_only(
    persisted, database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    h, imported, attempt_id = persisted
    loader = DatabaseAssignmentLoader(database.engine, artifacts, PROTOCOLS)
    assignment = loader(_claim(attempt_id))
    assert assignment.attempt_id == attempt_id and assignment.scope.scope_id == attempt_id
    assert "Double the input" in assignment.instructions
    assert set(assignment.visible_files) == {"task.md", "fixtures/input.txt", "repo/README.md"}
    assert assignment.base_digest == imported.visible_digest
    assert assignment.sample_seed == 2**63 + 12345  # the full unsigned 64-bit seed survives
    assert assignment.effective.protocol.protocol_id == "single-shot-v1"
    assert assignment.effective.budget.active_solve_seconds == 10  # the task's ceiling wins
    assert (
        assignment.config == h.world.config and assignment.config_document_id == h.world.config_id
    )
    assert assignment.contract.allowed_paths == ["solution.py"]
    # hidden identity is known only as fail-closed markers, never as content
    assert imported.hidden_digest.encode() in assignment.forbidden_markers
    assert b"oracle" not in b"".join(assignment.visible_files.values()).lower()


def test_loader_rejects_a_visible_bundle_that_differs_from_the_frozen_digest(
    persisted, database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    h, _, attempt_id = persisted
    loader = DatabaseAssignmentLoader(database.engine, artifacts, PROTOCOLS)
    empty = {k: v for k, v in PROTOCOLS.items() if k != "single-shot-v1"}
    from polycodebench_core.solve_contracts import SolveError

    with pytest.raises(SolveError, match="not installed"):
        DatabaseAssignmentLoader(database.engine, artifacts, empty)(_claim(attempt_id))
    assert loader(_claim(attempt_id)).attempt_id == attempt_id


def test_executor_completes_a_model_failure_as_work_and_a_candidate_as_success(
    persisted, database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    h, _, attempt_id = persisted
    store = ArtifactResponseStore(artifacts, owner="p09", encryption_domain="solve-session")
    loader = DatabaseAssignmentLoader(database.engine, artifacts, PROTOCOLS)
    answer = json.dumps(
        {"files": [{"path": "solution.py", "content": "print(int(input()) * 2)\n"}]}
    )
    transport = ScriptedTransport([ok(answer)])
    executor = SolveStageExecutor(
        load_assignment=loader,
        gateway=h.world.gateway(transport),
        jobs=Jobs(),  # type: ignore[arg-type]
        solve_repository=h.repo,
        store_factory=lambda repo: store,
    )
    claim = _claim(attempt_id)
    result = asyncio.run(executor(claim, None, None, artifacts))  # type: ignore[arg-type]
    assert result.outcome.model_failure is False and result.outcome.quality_gate is None
    candidate = h.repo.candidate_for(attempt_id)
    assert candidate is not None and result.output_artifact_id == candidate.canonical_artifact_id
    assert len(transport.sent) == 1
    # a redelivered job is idempotent: same result, no new model request
    again = asyncio.run(
        SolveStageExecutor(
            load_assignment=loader,
            gateway=h.world.gateway(ScriptedTransport([])),
            jobs=Jobs(),  # type: ignore[arg-type]
            solve_repository=h.repo,
            store_factory=lambda repo: store,
        )(claim, None, None, artifacts)  # type: ignore[arg-type]
    )
    assert again.output_artifact_id == result.output_artifact_id

    from polycodebench_core.solve_contracts import SolveOutcome
    from polycodebench_orchestration.solve.session import SolveResult

    failed = SolveResult(
        SolveOutcome(
            status="model_failure",
            reason="single_shot:contract_invalid:no_files",
            validity="contract_invalid",
        ),
        candidate.canonical_artifact_id,
        candidate.payload_digest,
        5,
    )
    mapped = stage_outcome(failed)
    assert mapped.model_failure is True and mapped.quality_gate == "fail"
    assert mapped.failure_class is None  # completed work, not an infrastructure failure


def test_executor_stops_before_any_request_when_the_lease_is_gone(
    persisted, database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    h, _, attempt_id = persisted
    store = ArtifactResponseStore(artifacts, owner="p09", encryption_domain="solve-session")
    transport = ScriptedTransport([ok("{}")])
    executor = SolveStageExecutor(
        load_assignment=DatabaseAssignmentLoader(database.engine, artifacts, PROTOCOLS),
        gateway=h.world.gateway(transport),
        jobs=Jobs(allowed=False),  # type: ignore[arg-type]
        solve_repository=h.repo,
        store_factory=lambda repo: store,
    )
    with pytest.raises(LeaseLost):
        asyncio.run(executor(_claim(attempt_id), None, None, artifacts))  # type: ignore[arg-type]
    assert transport.sent == [] and h.repo.last_sequence(attempt_id) == -1
