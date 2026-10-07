"""Prompt 32 submission and bounded-approval integration against PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import pytest
from migration_support import require_migrated_through
from polycodebench_api.app import create_app
from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.errors import ApiError
from polycodebench_api.postgres_submissions import PostgresSubmissionStore
from polycodebench_api.submissions import SubmissionRate
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.jobs import StageOutcome
from polycodebench_core.model_contracts import (
    ModelCapabilities,
    PriceSnapshot,
    ProviderKind,
    UsageCounter,
)
from polycodebench_core.model_planning import ModelConfig
from polycodebench_persistence.database import Database
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    artifact,
    attempt,
    audit_event,
    budget_account,
    budget_resource,
    campaign,
    config_document,
    model_revision,
    run,
    stage_job,
    stage_job_event,
    task,
    task_set,
    task_set_member,
    task_version,
)
from polycodebench_publication.releases import ReleaseStore
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import Engine, make_url
from test_jobs_postgres import (
    _artifact_repo,
    _MemoryArtifactStore,
    _register_worker,
    _verified_upload,
)

REQUIRED_REVISION = "c41e1d8ab0f6"


def _test_database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("submission integration requires a disposable PostgreSQL test database")
    return value


def _migration_check_url(test_url: str) -> str:
    """Allow schema revision checks with a separate migration identity.

    The submission tests deliberately connect through the submitter role, which should not
    need SELECT access to Alembic's bookkeeping table. CI uses a database owner for both
    connections; local role-scoped runs may supply a privileged URL for the same test DB.
    """
    value = os.environ.get("PCB_TEST_MIGRATION_DATABASE_URL") or test_url
    if make_url(value).database != make_url(test_url).database:
        pytest.fail("PCB_TEST_MIGRATION_DATABASE_URL must target PCB_TEST_DATABASE_URL's database")
    return value


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    test_url = _test_database_url()
    database = Database(test_url)
    with database.engine.connect() as connection:
        if (
            connection.execute(text("SELECT to_regclass('public.model_submission')")).scalar_one()
            is None
        ):
            pytest.fail("PostgreSQL model-submission schema is not installed")
    migration_engine = create_engine(
        _migration_check_url(test_url), pool_pre_ping=True, hide_parameters=True
    )
    try:
        with migration_engine.connect() as connection:
            require_migrated_through(connection, REQUIRED_REVISION)
    finally:
        migration_engine.dispose()
    yield database.engine
    database.dispose()


def test_concurrent_requests_cannot_exceed_one_subject_rate_limit(engine: Engine) -> None:
    store = PostgresSubmissionStore(engine)
    subject = f"prompt32-concurrency-{uuid4()}"
    request_count = 8
    start = Barrier(request_count)
    payload = {
        "model_name": "Concurrent test fixture",
        "provider": "fixture",
        "organization": None,
        "contact_email": "fixture@example.org",
        "endpoint_url": "https://models.example.org/v1",
        "source_url": "https://models.example.org/source",
        "source_license": "test-only",
        "permission_attested": True,
    }

    def submit(index: int) -> str:
        start.wait(timeout=10)
        try:
            store.submit(
                subject=subject,
                request_id=f"parallel-{index}",
                payload=payload,
                rate=SubmissionRate(limit=2, window_seconds=3600),
            )
            return "created"
        except ApiError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        outcomes = list(executor.map(submit, range(request_count)))

    counts = Counter(outcomes)
    assert counts["created"] == 2
    assert counts["RATE_LIMITED"] == request_count - 2


def test_submitter_database_role_is_scoped_and_supports_idempotent_retry() -> None:
    """Exercise submit, audit and owner reads under the actual submitter RLS policy."""
    role_engine = create_engine(_test_database_url(), pool_pre_ping=True, hide_parameters=True)

    store = PostgresSubmissionStore(role_engine)
    subject = f"prompt32-owner-scope-{uuid4()}"
    payload = {
        "model_name": "Submitter role regression fixture",
        "provider": "fixture",
        "organization": None,
        "contact_email": "fixture@example.org",
        "endpoint_url": "https://models.example.org/v1",
        "source_url": "https://models.example.org/source",
        "source_license": "test-only",
        "permission_attested": True,
    }
    try:
        created = store.submit(
            subject=subject,
            request_id="owner-scope-request-0001",
            payload=payload,
            rate=SubmissionRate(limit=2, window_seconds=3600),
        )
        replayed = store.submit(
            subject=subject,
            request_id="owner-scope-request-0001",
            payload=payload,
            rate=SubmissionRate(limit=2, window_seconds=3600),
        )
        assert replayed["submission_id"] == created["submission_id"]
        audit_engine = create_engine(
            _migration_check_url(_test_database_url()), hide_parameters=True
        )
        try:
            with audit_engine.connect() as connection:
                audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_event "
                        "WHERE action = 'model_submission.create' "
                        "AND resource_type = 'model_submission' "
                        "AND resource_id = :submission_id "
                        "AND request_id = 'owner-scope-request-0001'"
                    ),
                    {"submission_id": str(created["submission_id"])},
                ).scalar_one()
            assert audit_count == 1
        finally:
            audit_engine.dispose()
        assert (
            store.get_owned(subject=subject, submission_id=str(created["submission_id"]))[
                "submission_id"
            ]
            == created["submission_id"]
        )
        with pytest.raises(ApiError) as forbidden:
            store.get_owned(
                subject=f"{subject}-other",
                submission_id=str(created["submission_id"]),
            )
        assert forbidden.value.code == "NOT_FOUND"
    finally:
        role_engine.dispose()


def test_authenticated_submission_routes_use_postgres_roles(tmp_path: Path, engine: Engine) -> None:
    """Exercise submit, reviewer reject and owner status through the PostgreSQL app adapter."""
    del engine  # The fixture verifies the dedicated database revision before app startup.
    database = Database(_test_database_url())
    release_store = ReleaseStore(Path(str(tmp_path)) / "submission-routes.sqlite3")
    route_owner = f"route-owner-{uuid4()}"
    route_other = f"route-other-{uuid4()}"
    request_suffix = uuid4().hex
    tokens = TokenDirectory(
        {
            "route-submitter-token-000001": ApiPrincipal(
                route_owner,
                frozenset({"submitter"}),
                email="route-owner@example.org",
                email_verified=True,
            ),
            "route-other-token-0000001": ApiPrincipal(
                route_other,
                frozenset({"submitter"}),
                email="route-other@example.org",
                email_verified=True,
            ),
            "route-reviewer-token-00001": ApiPrincipal(
                "route-reviewer",
                frozenset({"reviewer"}),
                mfa=True,
            ),
        }
    )
    app = create_app(
        store=release_store,
        cursor_key=b"prompt32-postgres-route-test-key-000",
        tokens=tokens,
        persistence_database=database,
    )

    async def verify() -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/v1/model-submissions",
                headers={
                    "Authorization": "Bearer route-submitter-token-000001",
                    "Idempotency-Key": f"route-submit-32-1-{request_suffix}",
                },
                json={
                    "kind": "model_submission_input",
                    "model_name": "PostgreSQL route fixture",
                    "provider": "Fixture Provider",
                    "contact_email": "route-owner@example.org",
                    "endpoint_url": "https://models.example.org/v1",
                    "source_url": "https://models.example.org/model-card",
                    "source_license": "test-only permission",
                    "permission_attested": True,
                },
            )
            assert created.status_code == 201
            submission_id = created.json()["data"]["submission_id"]
            assert created.json()["data"]["status"] == "pending"
            assert "secret_ref" not in created.text

            review_headers = {"Authorization": "Bearer route-reviewer-token-00001"}
            queue = await client.get("/v1/admin/model-submissions", headers=review_headers)
            assert queue.status_code == 200
            reviewed = await client.get(
                f"/v1/admin/model-submissions/{submission_id}", headers=review_headers
            )
            assert reviewed.status_code == 200
            assert reviewed.json()["data"]["submission_id"] == submission_id

            rejected = await client.post(
                f"/v1/admin/model-submissions/{submission_id}/reject",
                headers={
                    **review_headers,
                    "Idempotency-Key": f"route-reject-32-1-{request_suffix}",
                },
                json={
                    "reason": "Rejected by the PostgreSQL route integration fixture.",
                    "expected_version": 0,
                },
            )
            assert rejected.status_code == 200
            assert rejected.json()["data"]["status"] == "rejected"

            own_status = await client.get(
                f"/v1/model-submissions/{submission_id}",
                headers={"Authorization": "Bearer route-submitter-token-000001"},
            )
            assert own_status.status_code == 200
            assert own_status.json()["data"]["status"] == "rejected"
            foreign_status = await client.get(
                f"/v1/model-submissions/{submission_id}",
                headers={"Authorization": "Bearer route-other-token-0000001"},
            )
            assert foreign_status.status_code == 404
            return submission_id

    try:
        submission_id = asyncio.run(verify())
        audit_engine = create_engine(
            _migration_check_url(_test_database_url()), hide_parameters=True
        )
        try:
            with audit_engine.connect() as connection:
                actions = (
                    connection.execute(
                        text(
                            "SELECT action FROM audit_event "
                            "WHERE resource_type = 'model_submission' AND resource_id = :id "
                            "ORDER BY action"
                        ),
                        {"id": submission_id},
                    )
                    .scalars()
                    .all()
                )
            assert actions == ["model_submission.create", "model_submission.reject"]
        finally:
            audit_engine.dispose()
    finally:
        database.dispose()


def test_approved_submission_recovers_one_bounded_postgres_run(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approval, endpoint review, queued run and replay work under least-privilege API roles."""
    with engine.connect() as connection:
        role_state = connection.execute(
            text(
                "SELECT current_user, "
                "pg_has_role(current_user, 'pcb_public_reader', 'MEMBER'), "
                "pg_has_role(current_user, 'pcb_submitter', 'MEMBER'), "
                "pg_has_role(current_user, 'pcb_submission_reviewer', 'MEMBER'), "
                "pg_has_role(current_user, 'pcb_submission_approver', 'MEMBER'), "
                "pg_has_role(current_user, 'pcb_endpoint_administrator', 'MEMBER'), "
                "pg_has_role(current_user, 'pcb_administrator', 'MEMBER'), "
                "pg_has_role(current_user, 'pcb_operator', 'MEMBER')"
            )
        ).one()
    assert role_state[1:6] == (True, True, True, True, True)
    assert role_state[6:] == (False, False)

    database = Database(_test_database_url())
    scheduler_database = Database(_migration_check_url(_test_database_url()))
    migration_engine = scheduler_database.engine
    release_store = ReleaseStore(Path(str(tmp_path)) / "approval-routes.sqlite3")
    owner_subject = f"approval-owner-{uuid4()}"
    tokens = TokenDirectory(
        {
            "approval-owner-token-00000001": ApiPrincipal(
                owner_subject,
                frozenset({"submitter"}),
                email="approval-owner@example.org",
                email_verified=True,
            ),
            "approval-admin-token-00000001": ApiPrincipal(
                "approval-admin", frozenset({"administrator"}), mfa=True
            ),
        }
    )
    app = create_app(
        store=release_store,
        cursor_key=b"prompt32-postgres-approval-test-key-000",
        tokens=tokens,
        persistence_database=database,
    )
    headers = {"Authorization": "Bearer approval-admin-token-00000001"}
    owner_headers = {"Authorization": "Bearer approval-owner-token-00000001"}
    request_suffix = uuid4().hex
    endpoint_url = "https://models.example.org/v1"
    endpoint_capabilities = ModelCapabilities(
        native_tools=False,
        structured_output=False,
        seed=False,
        temperature=False,
        context_limit_tokens=16_384,
        max_output_tokens_limit=128,
        usage_counters=frozenset({UsageCounter.INPUT, UsageCounter.OUTPUT}),
        output_cap_bounds_all_billed_output=True,
        output_limit_parameter="max_tokens",
    )

    async def request(client: httpx.AsyncClient, method: str, path: str, **kwargs: object):
        return await client.request(method, path, **kwargs)

    async def verify() -> tuple[str, str, dict[str, object]]:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            submitted = await request(
                client,
                "POST",
                "/v1/model-submissions",
                headers={
                    **owner_headers,
                    "Idempotency-Key": f"approval-submit-32-1-{request_suffix}",
                },
                json={
                    "kind": "model_submission_input",
                    "model_name": "Synthetic PostgreSQL approval fixture",
                    "provider": "Synthetic fixture",
                    "contact_email": "approval-owner@example.org",
                    "endpoint_url": endpoint_url,
                    "source_url": "https://models.example.org/model-card",
                    "source_license": "test-only permission",
                    "permission_attested": True,
                },
            )
            assert submitted.status_code == 201, submitted.text
            submission_id = submitted.json()["data"]["submission_id"]

            registered = await request(
                client,
                "POST",
                "/v1/admin/model-endpoints",
                headers=headers,
                json={
                    "provider_kind": "openai_compatible",
                    "base_url": endpoint_url,
                    "secret_ref": "secret://test/fixture-key",
                    "network_policy": {
                        "kind": "public-https-allowlist",
                        "allowed_hosts": ["models.example.org"],
                        "allowed_cidrs": [],
                    },
                    "declared_capabilities": endpoint_capabilities.model_dump(mode="json"),
                },
            )
            assert registered.status_code == 201, registered.text
            endpoint_id = registered.json()["data"]["endpoint_registration_id"]

            ids, run_plan = _seed_postgres_approval_plan(
                migration_engine, endpoint_id, endpoint_capabilities
            )
            permission_review = {
                "source_url": "https://models.example.org/model-card",
                "source_license": "test-only permission",
                "evidence_reference": "synthetic-fixture://source-permission-review",
                "rights_confirmed": True,
                "decision_reason": "Synthetic fixture permission record; no live model call.",
            }
            approval = {
                "endpoint_registration_id": endpoint_id,
                "run_request": run_plan,
                "permission_review": permission_review,
            }
            approval_headers = {
                **headers,
                "Idempotency-Key": f"approval-run-32-1-{request_suffix}",
            }

            # A pending endpoint is rejected before any run can be inserted.
            pending = await request(
                client,
                "POST",
                f"/v1/admin/model-submissions/{submission_id}/approve",
                headers=approval_headers,
                json=approval,
            )
            assert pending.status_code == 403, pending.text
            with migration_engine.connect() as connection:
                assert (
                    connection.execute(
                        select(run.c.id).where(run.c.campaign_id == ids["campaign"])
                    ).first()
                    is None
                )

            decided = await request(
                client,
                "POST",
                f"/v1/admin/model-endpoints/{endpoint_id}/decision",
                headers=headers,
                json={
                    "decision": "approved",
                    "reason": "Synthetic fixture conformance; no endpoint was contacted.",
                    "expected_version": 0,
                    "conformance_report": {"passed": True, "evidence_kind": "synthetic-test"},
                },
            )
            assert decided.status_code == 200, decided.text

            submissions = app.state.services.submissions
            original_finish = submissions.finish_approval
            fail_once = True

            def fail_first_finish(**kwargs: object) -> dict[str, object]:
                nonlocal fail_once
                if fail_once:
                    fail_once = False
                    raise RuntimeError("injected interruption after idempotent run creation")
                return original_finish(**kwargs)

            monkeypatch.setattr(submissions, "finish_approval", fail_first_finish)
            first_attempt = await request(
                client,
                "POST",
                f"/v1/admin/model-submissions/{submission_id}/approve",
                headers=approval_headers,
                json=approval,
            )
            assert first_attempt.status_code == 503, first_attempt.text

            recovered = await request(
                client,
                "POST",
                f"/v1/admin/model-submissions/{submission_id}/approve",
                headers=approval_headers,
                json=approval,
            )
            assert recovered.status_code == 202, recovered.text
            run_id = recovered.json()["data"]["resulting_run_id"]
            assert recovered.json()["data"]["status"] == "approved"

            replayed = await request(
                client,
                "POST",
                f"/v1/admin/model-submissions/{submission_id}/approve",
                headers=approval_headers,
                json=approval,
            )
            assert replayed.status_code == 202, replayed.text
            assert replayed.json()["data"]["resulting_run_id"] == run_id

            owner_status = await request(
                client,
                "GET",
                f"/v1/model-submissions/{submission_id}",
                headers=owner_headers,
            )
            assert owner_status.status_code == 200, owner_status.text
            assert owner_status.json()["data"]["run_status"] == "queued"
            assert owner_status.json()["data"]["run_progress"] == {
                "schema_version": 1,
                "attempt_states": {"queued": 1},
                "solve_job_states": {"queued": 1},
            }

            memory_store = _MemoryArtifactStore()
            worker_resource_class = f"synthetic-test-{ids['task_version'].hex[:16]}"
            worker_id = _register_worker(
                scheduler_database,
                memory_store,
                label="submission-run-status",
                queue_class="solve",
                resource_class=worker_resource_class,
            )
            scheduler = PostgresJobRepository(scheduler_database.engine)
            claim = scheduler.claim(worker_id)
            assert claim is not None
            running_status = await request(
                client,
                "GET",
                f"/v1/model-submissions/{submission_id}",
                headers=owner_headers,
            )
            assert running_status.status_code == 200, running_status.text
            assert running_status.json()["data"]["run_status"] == "running"
            assert running_status.json()["data"]["run_progress"] == {
                "schema_version": 1,
                "attempt_states": {"running": 1},
                "solve_job_states": {"leased": 1},
            }

            output = _verified_upload(
                _artifact_repo(scheduler_database, memory_store),
                b"synthetic PostgreSQL submission result",
            )
            scheduler.complete(
                claim,
                output_artifact_id=output,
                outcome=StageOutcome(quality_gate="pass"),
            )
            completed_status = await request(
                client,
                "GET",
                f"/v1/model-submissions/{submission_id}",
                headers=owner_headers,
            )
            assert completed_status.status_code == 200, completed_status.text
            assert completed_status.json()["data"]["run_status"] == "completed"
            assert completed_status.json()["data"]["run_progress"] == {
                "schema_version": 1,
                "attempt_states": {"completed": 1},
                "solve_job_states": {"succeeded": 1},
            }
            return submission_id, run_id, ids

    try:
        submission_id, run_id, ids = asyncio.run(verify())
        with migration_engine.connect() as connection:
            run_rows = (
                connection.execute(
                    select(
                        run.c.id, run.c.status, run.c.campaign_id, run.c.config_document_id
                    ).where(run.c.id == run_id)
                )
                .mappings()
                .all()
            )
            assert len(run_rows) == 1
            assert run_rows[0]["status"] == "completed"
            assert run_rows[0]["campaign_id"] == ids["campaign"]
            assert run_rows[0]["config_document_id"] == ids["run_config"]
            attempts = connection.execute(
                select(attempt.c.id, attempt.c.state).where(attempt.c.run_id == run_id)
            ).all()
            assert len(attempts) == 1 and attempts[0][1] == "completed"
            solve_jobs = (
                connection.execute(
                    select(
                        stage_job.c.id,
                        stage_job.c.attempt_id,
                        stage_job.c.stage,
                        stage_job.c.state,
                        stage_job.c.queue_class,
                        stage_job.c.resource_class,
                        stage_job.c.input_digest,
                    ).where(stage_job.c.attempt_id == attempts[0][0])
                )
                .mappings()
                .all()
            )
            assert len(solve_jobs) == 1
            assert solve_jobs[0]["stage"] == "solve"
            assert solve_jobs[0]["state"] == "succeeded"
            assert solve_jobs[0]["queue_class"] == "solve"
            assert solve_jobs[0]["resource_class"] == (
                f"synthetic-test-{ids['task_version'].hex[:16]}"
            )
            assert solve_jobs[0]["input_digest"].startswith("sha256:")
            assert (
                connection.execute(
                    select(stage_job_event.c.event_kind)
                    .where(stage_job_event.c.job_id == solve_jobs[0]["id"])
                    .order_by(stage_job_event.c.event_seq)
                )
                .scalars()
                .all()
            ) == ["job_created", "job_claimed", "job_succeeded"]
            budget = connection.execute(
                select(budget_account.c.hard_limit_micro_usd).where(
                    budget_account.c.scope_kind == "run",
                    budget_account.c.scope_id == run_id,
                )
            ).scalar_one()
            assert budget == 100_000
            resource_limits = dict(
                connection.execute(
                    select(budget_resource.c.resource, budget_resource.c.hard_limit).where(
                        budget_resource.c.account_id
                        == select(budget_account.c.id)
                        .where(
                            budget_account.c.scope_kind == "run",
                            budget_account.c.scope_id == run_id,
                        )
                        .scalar_subquery()
                    )
                ).all()
            )
            assert resource_limits == {"input_tokens": 8192, "output_tokens": 128}
            actions = (
                connection.execute(
                    select(audit_event.c.action).where(
                        audit_event.c.resource_type == "model_submission",
                        audit_event.c.resource_id == submission_id,
                    )
                )
                .scalars()
                .all()
            )
            assert sorted(actions) == [
                "model_submission.approval_started",
                "model_submission.approved",
                "model_submission.create",
            ]
    finally:
        scheduler_database.dispose()
        database.dispose()


def _seed_postgres_approval_plan(
    engine: Engine, endpoint_id: str, capabilities: ModelCapabilities
) -> tuple[dict[str, object], dict[str, object]]:
    ids = {
        name: uuid4()
        for name in (
            "artifact",
            "task",
            "task_version",
            "task_set",
            "model_config",
            "capabilities",
            "run_config",
            "model_revision",
            "campaign_budget",
            "campaign",
        )
    }
    task_digest = canonical_digest({"task": str(ids["task_version"]), "fixture": True})
    task_set_digest = canonical_digest({"task_set": str(ids["task_set"]), "fixture": True})
    artifact_digest = canonical_digest({"artifact": str(ids["artifact"]), "fixture": True})
    price = PriceSnapshot(
        price_id="synthetic-approval-test-price",
        input_micro_usd_per_million_tokens=1_000_000,
        output_micro_usd_per_million_tokens=1_000_000,
        basis="contract_price",
        source="Synthetic integration fixture; not a real price.",
        effective_date="2026-10-06",
    )
    model_name = f"synthetic-model-{ids['model_revision']}"
    model_config = ModelConfig(
        schema_version=1,
        kind="model_config",
        provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        model=model_name,
        immutable_revision=f"synthetic-revision-{ids['model_revision']}",
        endpoint_id=UUID(endpoint_id),
        declared_capabilities=capabilities,
        price=price,
        temperature=None,
        seed_policy="omit",
        reasoning=None,
        max_output_tokens=128,
        strict_money_cap=True,
    )
    model_config_document = model_config.model_dump(mode="json")
    model_config_digest = canonical_digest(model_config_document)
    model_capabilities_document = capabilities.model_dump(mode="json")
    caps_digest = canonical_digest(model_capabilities_document)
    run_config_document = {
        "schema_version": 1,
        "kind": "run_config",
        "model_config_digest": model_config_digest,
        "sampling": {
            "task_set_digest": task_set_digest,
            "samples_per_task": 1,
            "master_seed": "42",
        },
    }
    resource_class = f"synthetic-test-{ids['task_version'].hex[:16]}"
    with engine.begin() as connection:
        connection.execute(
            insert(artifact).values(
                id=ids["artifact"],
                visibility="internal",
                content_digest=artifact_digest,
                size_bytes=1,
                media_type="application/json",
                storage_key=f"synthetic-test/{ids['artifact']}",
                encryption_domain="synthetic-test",
                status="provisional",
            )
        )
        connection.execute(
            insert(task).values(
                id=ids["task"],
                slug=f"prompt32-approval-{ids['task']}",
                family="unit",
                source_identity="synthetic-test-fixture",
                primary_language="python",
            )
        )
        connection.execute(
            insert(task_version).values(
                id=ids["task_version"],
                task_id=ids["task"],
                version=1,
                digest=task_digest,
                manifest_artifact_id=ids["artifact"],
                visible_artifact_id=ids["artifact"],
                hidden_artifact_id=ids["artifact"],
                language="python",
                family="unit",
                cluster_id="synthetic-test",
                stratum_id="synthetic-test",
                schema_version=1,
                document={
                    "kind": "synthetic_test_task",
                    "runtime": {"resource_class": resource_class},
                },
            )
        )
        connection.execute(
            insert(task_set).values(
                id=ids["task_set"],
                name=f"prompt32-approval-{ids['task_set']}",
                version=1,
                digest=task_set_digest,
                split="test",
                status="draft",
                manifest_artifact_id=ids["artifact"],
                document={"evidence_kind": "synthetic-test"},
                row_version=0,
            )
        )
        connection.execute(
            insert(task_set_member).values(
                task_set_id=ids["task_set"],
                task_version_id=ids["task_version"],
                stratum_id="synthetic-test",
                sampling_weight_bp=10_000,
            )
        )
        connection.execute(
            text("UPDATE task_set SET status='frozen', frozen_at=:now, row_version=1 WHERE id=:id"),
            {"now": datetime.now(UTC), "id": ids["task_set"]},
        )
        existing_capabilities_id = connection.execute(
            select(config_document.c.id).where(
                config_document.c.kind == "capabilities",
                config_document.c.digest == caps_digest,
            )
        ).scalar_one_or_none()
        if existing_capabilities_id is not None:
            ids["capabilities"] = existing_capabilities_id
        for config_id, kind, digest, document in (
            (ids["capabilities"], "capabilities", caps_digest, model_capabilities_document),
            (
                ids["model_config"],
                "model_config",
                model_config_digest,
                model_config_document,
            ),
            (
                ids["run_config"],
                "run_config",
                canonical_digest(run_config_document),
                run_config_document,
            ),
        ):
            if kind == "capabilities" and existing_capabilities_id is not None:
                continue
            connection.execute(
                insert(config_document).values(
                    id=config_id,
                    kind=kind,
                    version_label="synthetic-test-v1",
                    digest=digest,
                    canonical_artifact_id=ids["artifact"],
                    schema_version=1,
                    document=document,
                )
            )
        connection.execute(
            insert(model_revision).values(
                id=ids["model_revision"],
                provider=ProviderKind.OPENAI_COMPATIBLE.value,
                name=model_name,
                immutable_revision=f"synthetic-revision-{ids['model_revision']}",
                endpoint_registration_id=endpoint_id,
                capabilities_config_id=ids["capabilities"],
            )
        )
        connection.execute(
            insert(budget_account).values(
                id=ids["campaign_budget"],
                scope_kind="campaign",
                scope_id=str(ids["campaign"]),
                hard_limit_micro_usd=1_000_000,
            )
        )
        connection.execute(
            insert(campaign).values(
                id=ids["campaign"],
                name=f"synthetic-test-{ids['campaign']}",
                status="planned",
                owner_subject="approval-admin",
                budget_account_id=ids["campaign_budget"],
                row_version=0,
            )
        )

    return ids, {
        "campaign_id": str(ids["campaign"]),
        "config_document_id": str(ids["run_config"]),
        "task_set_id": str(ids["task_set"]),
        "model_revision_id": str(ids["model_revision"]),
        "samples_per_task": 1,
        "master_seed": "42",
        "max_attempts": 1,
        "max_cost_micro_usd": 100_000,
        "max_input_tokens": 8192,
        "max_output_tokens": 128,
        "endpoint_registration_id": endpoint_id,
    }
