"""Prompt 03 integration checks against an explicitly provisioned PostgreSQL test DB."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from polycodebench_core.application_errors import (
    AuthorizationError,
    IdempotencyConflict,
    OptimisticVersionConflict,
    PersistenceUnavailable,
)
from polycodebench_persistence.database import Database
from polycodebench_persistence.identities import PostgresIdentityRepository
from polycodebench_persistence.models import (
    artifact,
    audit_event,
    campaign,
    config_document,
    idempotency_record,
    model_revision,
    model_submission,
    run,
    task,
    task_set,
    task_set_member,
    task_version,
)
from polycodebench_persistence.runs import PostgresRunRepository
from polycodebench_services.rbac import Permission, Principal, Role, authorize
from polycodebench_services.runs import RunCreateRequest, RunCreationService
from sqlalchemy import insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

TASK_RUNTIME_IMAGE_DIGEST = (
    "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)


def _test_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not set; PostgreSQL integration tests are opt-in")
    from sqlalchemy.engine import make_url

    database_name = make_url(value).database or ""
    if "test" not in database_name.lower():
        pytest.fail("PCB_TEST_DATABASE_URL must name a dedicated database containing 'test'")
    return value


@pytest.fixture(scope="session")
def database() -> Database:
    instance = Database(_test_url())
    with instance.engine.connect() as connection:
        if connection.execute(text("SELECT to_regclass('public.run')")).scalar_one() is None:
            pytest.fail("PostgreSQL test schema is missing; apply the Prompt 03 migration first")
    yield instance
    instance.dispose()


def _seed(
    engine: Engine,
    *,
    samples_per_task: int = 2,
    master_seed: str = "18446744073709551615",
    resource_class: str = "small",
) -> dict[str, object]:
    ids = {
        name: uuid4()
        for name in (
            "artifact",
            "task",
            "task_version",
            "task_set",
            "run_config",
            "capabilities",
            "model",
            "campaign",
        )
    }

    def digest_for(name: str) -> str:
        return "sha256:" + sha256(str(ids[name]).encode("ascii")).hexdigest()

    task_set_digest = digest_for("task_set")
    with engine.begin() as connection:
        connection.execute(
            insert(artifact).values(
                id=ids["artifact"],
                visibility="internal",
                content_digest=digest_for("artifact"),
                size_bytes=1,
                media_type="application/json",
                storage_key=f"test/{ids['artifact']}",
                encryption_domain="test",
                status="provisional",
            )
        )
        connection.execute(
            insert(task).values(
                id=ids["task"],
                slug=f"prompt03-{ids['task']}",
                family="unit",
                source_identity="fixture",
                primary_language="python",
            )
        )
        connection.execute(
            insert(task_version).values(
                id=ids["task_version"],
                task_id=ids["task"],
                version=1,
                digest=digest_for("task_version"),
                manifest_artifact_id=ids["artifact"],
                visible_artifact_id=ids["artifact"],
                hidden_artifact_id=ids["artifact"],
                language="python",
                family="unit",
                cluster_id="cluster",
                stratum_id="stratum",
                schema_version=1,
                document={
                    "task": "fixture",
                    "runtime": {
                        "schema_version": 1,
                        "kind": "task_runtime",
                        "image_digest": TASK_RUNTIME_IMAGE_DIGEST,
                        "language_plugin_id": "python",
                        "language_plugin_version": "local-fixture-v1",
                        "build_recipe_digest": digest_for("task"),
                        "test_recipe_digest": digest_for("artifact"),
                        "resource_class": resource_class,
                    },
                },
            )
        )
        connection.execute(
            insert(task_set).values(
                id=ids["task_set"],
                name=f"prompt03-{ids['task_set']}",
                version=1,
                digest=task_set_digest,
                split="test",
                status="draft",
                manifest_artifact_id=ids["artifact"],
                row_version=0,
            )
        )
        connection.execute(
            insert(task_set_member).values(
                task_set_id=ids["task_set"],
                task_version_id=ids["task_version"],
                stratum_id="stratum",
                sampling_weight_bp=10000,
            )
        )
        connection.execute(
            text(
                "UPDATE task_set SET status='frozen', frozen_at=now(), row_version=1 WHERE id=:id"
            ),
            {"id": ids["task_set"]},
        )
        for name, kind, document in (
            (
                "run_config",
                "run_config",
                {
                    "sampling": {
                        "task_set_digest": task_set_digest,
                        "samples_per_task": samples_per_task,
                        "master_seed": master_seed,
                    }
                },
            ),
            ("capabilities", "capabilities", {"supports_tools": False}),
        ):
            connection.execute(
                insert(config_document).values(
                    id=ids[name],
                    kind=kind,
                    version_label="test-v1",
                    digest=digest_for(name),
                    canonical_artifact_id=ids["artifact"],
                    schema_version=1,
                    document=document,
                )
            )
        connection.execute(
            insert(model_revision).values(
                id=ids["model"],
                provider=f"fixture-{ids['model']}",
                name="fixture",
                immutable_revision="rev-1",
                capabilities_config_id=ids["capabilities"],
            )
        )
        connection.execute(
            insert(campaign).values(
                id=ids["campaign"],
                name=f"prompt03-{ids['campaign']}",
                status="planned",
                owner_subject="integration-test",
                row_version=0,
            )
        )
    return ids


def _request(ids: dict[str, object], *, samples_per_task: int = 2) -> RunCreateRequest:
    return RunCreateRequest(
        campaign_id=ids["campaign"],
        config_document_id=ids["run_config"],
        task_set_id=ids["task_set"],
        model_revision_id=ids["model"],
        samples_per_task=samples_per_task,
        master_seed="18446744073709551615",
    )


def test_concurrent_same_key_replays_one_atomic_run(database: Database) -> None:
    ids = _seed(database.engine)
    service = RunCreationService(PostgresRunRepository(database.engine))
    principal = Principal("integration-test", frozenset({Role.OPERATOR}))
    request = _request(ids)
    with pytest.raises(AuthorizationError):
        service.create(
            Principal("curator-cannot-create-run", frozenset({Role.CURATOR})),
            request,
            f"denied-{uuid4()}",
        )
    with database.engine.connect() as connection:
        assert (
            connection.execute(select(run.c.id).where(run.c.campaign_id == ids["campaign"])).all()
            == []
        )
    barrier = Barrier(2)
    key = f"same-request-{uuid4()}"

    def create() -> object:
        barrier.wait(timeout=5)
        return service.create(principal, request, key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = (
            future.result(timeout=20)
            for future in (executor.submit(create), executor.submit(create))
        )
    assert first.run_id == second.run_id
    assert len(first.attempt_ids) == len(second.attempt_ids) == 2
    assert {first.replayed, second.replayed} == {False, True}
    with database.engine.connect() as connection:
        assert (
            connection.execute(select(run.c.id).where(run.c.id == first.run_id)).all().__len__()
            == 1
        )
        count = connection.execute(
            text("SELECT count(*) FROM attempt WHERE run_id=:id"), {"id": first.run_id}
        ).scalar_one()
        assert count == 2
        seeds = connection.execute(
            text("SELECT seed FROM attempt WHERE run_id=:id"), {"id": first.run_id}
        ).scalars()
        assert all(0 <= int(seed) <= 18_446_744_073_709_551_615 for seed in seeds)


def test_changed_request_conflicts_and_attempt_insert_failure_rolls_back(
    database: Database,
) -> None:
    ids = _seed(database.engine)
    service = RunCreationService(PostgresRunRepository(database.engine))
    principal = Principal("integration-test", frozenset({Role.OPERATOR}))
    changed_key = f"changed-body-{uuid4()}"
    service.create(principal, _request(ids), changed_key)
    with pytest.raises(IdempotencyConflict):
        service.create(principal, _request(ids, samples_per_task=1), changed_key)

    failed_ids = _seed(database.engine)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION pcb_test_reject_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.sample_index = 1 THEN
                    RAISE EXCEPTION 'injected integration failure' USING ERRCODE='P0001';
                END IF;
                RETURN NEW;
            END $$
        """)
        connection.exec_driver_sql(
            "CREATE TRIGGER pcb_test_reject_attempt BEFORE INSERT ON attempt "
            "FOR EACH ROW EXECUTE FUNCTION pcb_test_reject_attempt()"
        )
    try:
        with pytest.raises(PersistenceUnavailable):
            rollback_key = f"rollback-{uuid4()}"
            service.create(principal, _request(failed_ids), rollback_key)
    finally:
        with database.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS pcb_test_reject_attempt ON attempt")
            connection.exec_driver_sql("DROP FUNCTION IF EXISTS pcb_test_reject_attempt()")
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                select(run.c.id).where(run.c.campaign_id == failed_ids["campaign"])
            ).first()
            is None
        )
        assert (
            connection.execute(
                select(idempotency_record.c.id).where(idempotency_record.c.key == rollback_key)
            ).first()
            is None
        )
        assert (
            connection.execute(
                select(audit_event.c.id).where(audit_event.c.request_id == rollback_key)
            ).first()
            is None
        )


def test_immutable_evidence_audit_and_role_permissions(database: Database) -> None:
    ids = _seed(database.engine)
    with database.engine.begin() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE task_version SET document='{}'::jsonb WHERE id=:id"),
                    {"id": ids["task_version"]},
                )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(text("DELETE FROM audit_event"))
        connection.exec_driver_sql("SET LOCAL ROLE pcb_submitter")
        connection.execute(text("SET LOCAL pcb.subject_id = 'owner@example.test'"))
        own_id, other_id = uuid4(), uuid4()
        connection.execute(
            insert(model_submission).values(
                id=own_id,
                requester_subject="owner@example.test",
                metadata_artifact_id=ids["artifact"],
                status="pending",
            )
        )
        assert connection.execute(select(model_submission.c.id)).scalars().all() == [own_id]
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    insert(model_submission).values(
                        id=other_id,
                        requester_subject="other@example.test",
                        metadata_artifact_id=ids["artifact"],
                        status="pending",
                    )
                )
        with pytest.raises(DBAPIError):
            connection.execute(text("SELECT * FROM audit_event"))


def test_identity_versioning_audit_and_service_permission(database: Database) -> None:
    repository = PostgresIdentityRepository(database.engine)
    subject = f"role-{uuid4()}"
    version = repository.grant_role(
        subject_id=subject,
        role=Role.SUBMITTER.value,
        actor_subject="admin",
        request_id="grant-1",
        expected_version=0,
    )
    assert version == 0
    assert repository.roles_for_subject(subject) == (Role.SUBMITTER.value,)
    with pytest.raises(OptimisticVersionConflict):
        repository.grant_role(
            subject_id=subject,
            role=Role.SUBMITTER.value,
            actor_subject="admin",
            request_id="stale",
            expected_version=4,
        )
    principal = Principal(subject, frozenset({Role.SUBMITTER}))
    with pytest.raises(AuthorizationError):
        authorize(principal, Permission.RUN_CREATE)
    with pytest.raises(AuthorizationError):
        authorize(principal, Permission.ROLE_ASSIGN)
    assert (
        repository.revoke_role(
            subject_id=subject,
            role=Role.SUBMITTER.value,
            actor_subject="admin",
            request_id="revoke-1",
            expected_version=version,
        )
        == 1
    )
    assert repository.roles_for_subject(subject) == ()
