"""Private benchmark-audit API boundaries using in-memory, synthetic repositories."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from polycodebench_api import audit_cli
from polycodebench_api.app import create_app
from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.context import AuditAccessPolicy
from polycodebench_api.submissions import SubmissionStore
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    AuditPlanDocument,
    audit_document_digest,
    parse_audit_document,
)
from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_persistence.benchmark_audit import AuditDocumentWrite, AuditRunWrite
from polycodebench_publication.releases import ReleaseStore

TENANT_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TENANT_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OPERATOR_TOKEN = "operator-private-audit-token-01"
OTHER_TENANT_TOKEN = "operator-private-audit-token-02"
REVIEWER_TOKEN = "reviewer-private-audit-token-01"


def _plan(actor: str = "operator-1") -> AuditPlanDocument:
    payload = {
        "id": str(uuid4()),
        "kind": "audit_plan",
        "schema_version": 1,
        "payload": {
            "benchmark_ref": {
                "document_id": str(uuid4()),
                "digest": "sha256:" + "a" * 64,
                "kind": "benchmark_snapshot",
            },
            "task_refs": [],
            "sample_design": {},
            "model_context": None,
            "source_plan": [],
            "methods": ["finite_scan"],
            "policy": {
                "document_id": str(uuid4()),
                "digest": "sha256:" + "b" * 64,
                "kind": "risk_policy",
            },
            "limits": {"max_query_units": 10, "max_storage_bytes": 1000},
            "visibility": "private",
            "seed": "7",
        },
        "metadata": {
            "created_at": "2026-10-08T12:00:00Z",
            "timestamp_precision": "second",
            "actor": actor,
            "trace_id": None,
            "row_version": 0,
        },
    }
    document = parse_audit_document(canonical_json_bytes(payload))
    assert isinstance(document, AuditPlanDocument)
    return document


class _AuditRepository:
    def __init__(self) -> None:
        self.documents: dict[UUID, tuple[AuditDocument, UUID]] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, AuditDocumentWrite]] = {}
        self.save_calls = 0
        self.run_calls: list[dict[str, object]] = []

    def save_document_idempotent(
        self,
        document: AuditDocument,
        *,
        subject: str,
        route: str,
        idempotency_key: str,
        request_digest: str,
        tenant_id: UUID,
    ) -> AuditDocumentWrite:
        identity = (subject, route, idempotency_key)
        replay = self.idempotency.get(identity)
        digest = audit_document_digest(document)
        if replay is not None:
            assert replay[0] == request_digest
            return replay[1]
        self.save_calls += 1
        write = AuditDocumentWrite(document.id, digest, True)
        self.documents[document.id] = (document, tenant_id)
        self.idempotency[identity] = (request_digest, write)
        return write

    def get_document(
        self, document_id: UUID, *, tenant_id: UUID | None = None
    ) -> AuditDocument | None:
        stored = self.documents.get(document_id)
        if stored is None or stored[1] != tenant_id:
            return None
        return stored[0]

    def list_documents(
        self, *, kind: str, created_by: str, tenant_id: UUID, limit: int, offset: int
    ) -> tuple[tuple[AuditDocument, ...], int]:
        documents = tuple(
            document
            for document, tenant in self.documents.values()
            if tenant == tenant_id
            and document.kind == kind
            and document.metadata.actor == created_by
        )
        return documents[offset : offset + limit], len(documents)

    def list_audit_runs(
        self, *, created_by: str, tenant_id: UUID, limit: int, offset: int
    ) -> tuple[tuple[dict[str, Any], ...], int]:
        return (), 0

    def get_audit_run(
        self, *, audit_run_id: UUID, created_by: str, tenant_id: UUID
    ) -> dict[str, Any] | None:
        return None

    def create_audit_run(
        self,
        *,
        plan_document_id: UUID,
        idempotency_key: str,
        reserved_query_units: int,
        reserved_storage_bytes: int,
        actor: str,
        tenant_id: UUID,
        campaign_id: UUID | None = None,
    ) -> AuditRunWrite:
        self.run_calls.append(
            {
                "plan_document_id": plan_document_id,
                "idempotency_key": idempotency_key,
                "reserved_query_units": reserved_query_units,
                "reserved_storage_bytes": reserved_storage_bytes,
                "actor": actor,
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
            }
        )
        return AuditRunWrite(uuid4(), True, 0)


class _DenyAuditAccess:
    def allows(self, *, principal: ApiPrincipal, document: AuditDocument, action: str) -> bool:
        return False


class _PublicHealthProjection:
    def __init__(self, report: Mapping[str, object]) -> None:
        self.report = report

    def get(self, report_id: UUID) -> Mapping[str, object] | None:
        if self.report.get("report_id") == str(report_id):
            return self.report
        return None


def _app(
    tmp_path: Path, repo: _AuditRepository, *, access: AuditAccessPolicy | None = None, public=None
):
    tokens = TokenDirectory(
        {
            OPERATOR_TOKEN: ApiPrincipal(
                subject_id="operator-1", roles=frozenset({"operator"}), tenant_id=TENANT_A
            ),
            OTHER_TENANT_TOKEN: ApiPrincipal(
                subject_id="operator-1", roles=frozenset({"operator"}), tenant_id=TENANT_B
            ),
            "tenantless-audit-token-0001": ApiPrincipal(
                subject_id="operator-1", roles=frozenset({"operator"})
            ),
            REVIEWER_TOKEN: ApiPrincipal(
                subject_id="reviewer-1", roles=frozenset({"reviewer"}), tenant_id=TENANT_A
            ),
        }
    )
    return create_app(
        store=ReleaseStore(tmp_path / "releases.sqlite3"),
        submissions=SubmissionStore(tmp_path / "submissions.sqlite3"),
        cursor_key=b"benchmark-audit-api-test-key-0123456789",
        tokens=tokens,
        benchmark_audit=repo,  # type: ignore[arg-type]
        audit_access=access,
        public_benchmark_health=public,
    )


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def test_private_api_requires_tenant_and_denies_cross_tenant_object_reads(tmp_path: Path) -> None:
    repo = _AuditRepository()
    app = _app(tmp_path, repo)
    plan = _plan()
    repo.documents[plan.id] = (plan, TENANT_A)

    async def verify() -> None:
        async with _client(app) as client:
            tenantless = await client.get(
                f"/v1/benchmark-audit/plans/{plan.id}",
                headers={"Authorization": "Bearer tenantless-audit-token-0001"},
            )
            assert tenantless.status_code == 403
            assert set(tenantless.json()) == {"error"}
            assert tenantless.json()["error"]["request_id"]

            cross_tenant = await client.get(
                f"/v1/benchmark-audit/plans/{plan.id}",
                headers={"Authorization": f"Bearer {OTHER_TENANT_TOKEN}"},
            )
            assert cross_tenant.status_code == 404
            assert "actor" not in cross_tenant.text
            assert "operator-1" not in cross_tenant.text

            owner = await client.get(
                f"/v1/benchmark-audit/plans/{plan.id}",
                headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
            )
            assert owner.status_code == 200
            assert owner.headers["cache-control"] == "private, no-store"
            assert owner.headers["etag"]
            assert owner.json()["data"]["kind"] == "audit_plan"

    asyncio.run(verify())


def test_private_api_object_acl_is_fail_closed(tmp_path: Path) -> None:
    repo = _AuditRepository()
    plan = _plan()
    repo.documents[plan.id] = (plan, TENANT_A)
    app = _app(tmp_path, repo, access=_DenyAuditAccess())

    async def verify() -> None:
        async with _client(app) as client:
            response = await client.get(
                f"/v1/benchmark-audit/plans/{plan.id}",
                headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
            )
            assert response.status_code == 404
            assert "audit_plan" not in response.text

    asyncio.run(verify())


def test_private_pages_are_bounded_and_cursors_bind_the_tenant(tmp_path: Path) -> None:
    repo = _AuditRepository()
    first = _plan()
    second = _plan()
    repo.documents[first.id] = (first, TENANT_A)
    repo.documents[second.id] = (second, TENANT_A)
    app = _app(tmp_path, repo)

    async def verify() -> None:
        async with _client(app) as client:
            headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
            response = await client.get("/v1/benchmark-audit/plans?limit=1", headers=headers)
            assert response.status_code == 200
            assert response.json()["meta"]["returned"] == 1
            cursor = response.json()["meta"]["next_cursor"]
            assert cursor

            replay = await client.get(
                "/v1/benchmark-audit/plans?limit=1",
                headers={**headers, "If-None-Match": response.headers["etag"]},
            )
            assert replay.status_code == 304

            next_page = await client.get(
                f"/v1/benchmark-audit/plans?limit=1&cursor={cursor}", headers=headers
            )
            assert next_page.status_code == 200
            assert next_page.json()["meta"]["returned"] == 1

            cross_tenant = await client.get(
                f"/v1/benchmark-audit/plans?limit=1&cursor={cursor}",
                headers={"Authorization": f"Bearer {OTHER_TENANT_TOKEN}"},
            )
            assert cross_tenant.status_code == 400
            assert cross_tenant.json()["error"]["code"] == "INVALID_CURSOR"

    asyncio.run(verify())


def test_audit_cli_covers_catalog_planning_and_stored_document_reads(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _AuditRepository()
    app = _app(tmp_path, repo)
    config_dir = Path(__file__).resolve().parents[1] / "config" / "benchmark-audit"
    monkeypatch.setenv("PCB_AUDIT_CONFIG_DIR", str(config_dir))

    def request(method, url, token, body, extra_headers):  # type: ignore[no-untyped-def]
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        response = client.request(
            method,
            parsed.path + (f"?{parsed.query}" if parsed.query else ""),
            content=body,
            headers={
                "Authorization": f"Bearer {token}",
                **({"Content-Type": "application/json"} if body is not None else {}),
                **extra_headers,
            },
        )
        return response.status_code, response.content

    with TestClient(app) as client:
        monkeypatch.setattr(audit_cli, "_request", request)
        auth = ["--token", OPERATOR_TOKEN, "--api-url", "http://127.0.0.1:8000"]

        assert audit_cli.main(["audit", "scope-preview", *auth]) == 0
        scope = json.loads(capsys.readouterr().out)["data"]
        assert scope["catalog_version"]
        assert scope["benchmarks"]

        resource_request = tmp_path / "resource-request.json"
        resource_request.write_text(
            json.dumps(
                {
                    "task_counts": {"humaneval": 2},
                    "source_groups": ["github"],
                    "stages": ["lexical"],
                    "average_item_bytes": 1024,
                }
            ),
            encoding="utf-8",
        )
        assert (
            audit_cli.main(["audit", "resource-plan", "--payload", str(resource_request), *auth])
            == 0
        )
        estimate = json.loads(capsys.readouterr().out)["data"]
        assert estimate["dispatch_allowed"] is False
        assert estimate["total_tasks"] == 2

        plan = _plan()
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(plan.model_dump_json(), encoding="utf-8")
        assert (
            audit_cli.main(
                [
                    "audit",
                    "plan",
                    "--payload",
                    str(plan_file),
                    "--idempotency-key",
                    "audit-cli-plan-001",
                    *auth,
                ]
            )
            == 0
        )
        created = json.loads(capsys.readouterr().out)["data"]
        assert created["state"] == "planned"
        assert created["dispatch_authorized"] is False

        assert audit_cli.main(["audit", "list", "plans", *auth]) == 0
        page = json.loads(capsys.readouterr().out)["data"]
        assert [document["id"] for document in page] == [str(plan.id)]

        assert audit_cli.main(["audit", "show", "plans", str(plan.id), *auth]) == 0
        detail = json.loads(capsys.readouterr().out)["data"]
        assert detail["id"] == str(plan.id)
        assert detail["kind"] == "audit_plan"


def test_restricted_evidence_reads_require_reviewer_mfa(tmp_path: Path) -> None:
    app = _app(tmp_path, _AuditRepository())

    async def verify() -> None:
        async with _client(app) as client:
            response = await client.get(
                f"/v1/benchmark-audit/matches/{uuid4()}",
                headers={"Authorization": f"Bearer {REVIEWER_TOKEN}"},
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"

    asyncio.run(verify())


def test_plan_dry_run_does_not_write_and_create_is_idempotent(tmp_path: Path) -> None:
    repo = _AuditRepository()
    app = _app(tmp_path, repo)
    document = _plan()
    headers = {
        "Authorization": f"Bearer {OPERATOR_TOKEN}",
        "Idempotency-Key": "plan-create-001",
        "If-None-Match": "*",
    }

    async def verify() -> None:
        async with _client(app) as client:
            body = document.model_dump(mode="json")
            preview = await client.post(
                "/v1/benchmark-audit/plans?dry_run=true", json=body, headers=headers
            )
            assert preview.status_code == 200
            assert preview.json()["data"]["state"] == "validated"
            assert preview.json()["data"]["dispatch_authorized"] is False
            assert repo.save_calls == 0

            created = await client.post(
                "/v1/benchmark-audit/plans?dry_run=false", json=body, headers=headers
            )
            assert created.status_code == 201
            assert created.json()["data"]["created"] is True
            assert created.headers["etag"]
            assert repo.save_calls == 1

            replay = await client.post(
                "/v1/benchmark-audit/plans?dry_run=false", json=body, headers=headers
            )
            assert replay.status_code == 201
            assert replay.json()["data"]["created"] is True
            assert repo.save_calls == 1

            missing_precondition = await client.post(
                "/v1/benchmark-audit/plans?dry_run=false",
                json=body,
                headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
            )
            assert missing_precondition.status_code == 422
            assert missing_precondition.json()["error"]["code"] == "SCHEMA_INVALID"

            malformed_key = await client.post(
                "/v1/benchmark-audit/plans?dry_run=false",
                json=body,
                headers={
                    "Authorization": f"Bearer {OPERATOR_TOKEN}",
                    "Idempotency-Key": "key/with/slash",
                    "If-None-Match": "*",
                },
            )
            assert malformed_key.status_code == 422
            assert repo.save_calls == 1

            duplicate_key_body = b'{"kind":"audit_plan","kind":"audit_plan"}'
            duplicate_keys = await client.post(
                "/v1/benchmark-audit/plans?dry_run=true",
                content=duplicate_key_body,
                headers={
                    "Authorization": f"Bearer {OPERATOR_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            assert duplicate_keys.status_code == 422
            assert repo.save_calls == 1

    asyncio.run(verify())


def test_plan_idempotency_is_scoped_to_tenant(tmp_path: Path) -> None:
    repo = _AuditRepository()
    app = _app(tmp_path, repo)
    tenant_a_plan = _plan()
    tenant_b_plan = _plan()

    async def verify() -> None:
        async with _client(app) as client:
            for token, document in (
                (OPERATOR_TOKEN, tenant_a_plan),
                (OTHER_TENANT_TOKEN, tenant_b_plan),
            ):
                response = await client.post(
                    "/v1/benchmark-audit/plans?dry_run=false",
                    json=document.model_dump(mode="json"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Idempotency-Key": "same-key-in-each-tenant",
                        "If-None-Match": "*",
                    },
                )
                assert response.status_code == 201
                assert response.json()["data"]["created"] is True
            assert repo.save_calls == 2
            assert len(repo.idempotency) == 2

    asyncio.run(verify())


def test_run_reservation_is_tenant_scoped_and_never_authorizes_dispatch(tmp_path: Path) -> None:
    repo = _AuditRepository()
    plan = _plan()
    repo.documents[plan.id] = (plan, TENANT_A)
    app = _app(tmp_path, repo)
    headers = {
        "Authorization": f"Bearer {OPERATOR_TOKEN}",
        "Idempotency-Key": "run-create-001",
        "If-None-Match": "*",
    }

    async def verify() -> None:
        async with _client(app) as client:
            response = await client.post(
                "/v1/benchmark-audit/runs?dry_run=false",
                json={
                    "plan_document_id": str(plan.id),
                    "reserved_query_units": 4,
                    "reserved_storage_bytes": 250,
                },
                headers=headers,
            )
            assert response.status_code == 201
            assert response.json()["data"]["dispatch_authorized"] is False
            assert repo.run_calls[0]["tenant_id"] == TENANT_A

            duplicate_members = await client.post(
                "/v1/benchmark-audit/runs?dry_run=false",
                content=(
                    '{"plan_document_id":"'
                    + str(plan.id)
                    + '","reserved_query_units":4,"reserved_query_units":5,'
                    '"reserved_storage_bytes":250}'
                ),
                headers={
                    "Authorization": f"Bearer {OPERATOR_TOKEN}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": "run-duplicate-field",
                    "If-None-Match": "*",
                },
            )
            assert duplicate_members.status_code == 422
            assert len(repo.run_calls) == 1

            denied = await client.post(
                "/v1/benchmark-audit/runs?dry_run=false",
                json={
                    "plan_document_id": str(plan.id),
                    "reserved_query_units": 4,
                    "reserved_storage_bytes": 250,
                },
                headers={"Authorization": f"Bearer {OTHER_TENANT_TOKEN}"},
            )
            assert denied.status_code == 404
            assert len(repo.run_calls) == 1

    asyncio.run(verify())


def test_public_health_is_an_allowlisted_reviewed_read_projection(tmp_path: Path) -> None:
    report_id = uuid4()
    projection: dict[str, object] = {
        "kind": "public_benchmark_health",
        "schema_version": 1,
        "report_id": str(report_id),
        "review_state": "published",
        "benchmark_label": "HumanEval",
        "benchmark_version": "v1",
        "source_window_start": "2026-01-01T00:00:00Z",
        "source_window_end": "2026-10-01T00:00:00Z",
        "selected_tasks": 4,
        "complete_tasks": 2,
        "partial_tasks": 1,
        "unknown_tasks": 0,
        "unscanned_tasks": 1,
        "blocked_tasks": 0,
        "assessed_tasks": 3,
        "low_risk_tasks": 1,
        "medium_risk_tasks": 1,
        "high_risk_tasks": 0,
        "insufficient_risk_tasks": 1,
        "limitations": ["partial_coverage", "descriptive_only"],
    }
    health_projection = _PublicHealthProjection(projection)
    app = _app(
        tmp_path,
        _AuditRepository(),
        public=health_projection,
    )

    async def verify() -> None:
        async with _client(app) as client:
            response = await client.get(f"/v1/public/benchmark-health/{report_id}")
            assert response.status_code == 200
            assert response.json()["data"]["selected_tasks"] == 4
            assert "tasks" not in response.json()["data"]

            with_private_url = dict(projection, source_url="https://secret.invalid/task")
            health_projection.report = with_private_url
            rejected = await client.get(f"/v1/public/benchmark-health/{report_id}")
            assert rejected.status_code == 404
            assert "secret.invalid" not in rejected.text

            mutation = await client.post(f"/v1/public/benchmark-health/{report_id}")
            assert mutation.status_code == 404
            assert "request_id" in mutation.json()["error"]

    asyncio.run(verify())


def test_openapi_exposes_typed_audit_contracts_without_a_scan_route(tmp_path: Path) -> None:
    app = _app(tmp_path, _AuditRepository())
    openapi = app.openapi()
    paths = openapi["paths"]
    assert "/v1/benchmark-audit/plans" in paths
    assert "/v1/benchmark-audit/runs" in paths
    assert "/v1/public/benchmark-health/{report_id}" in paths
    assert "/v1/public/audit-reports/{report_id}" in paths
    assert "/v1/public/audit-attestations/{attestation_id}" in paths
    assert all("scan" not in path.lower() for path in paths)
    assert set(paths["/v1/public/benchmark-health/{report_id}"]) == {"get"}
    assert set(paths["/v1/public/audit-reports/{report_id}"]) == {"get"}
    assert set(paths["/v1/public/audit-attestations/{attestation_id}"]) == {"get"}
    components = openapi["components"]["schemas"]
    assert "AuditRunView" in components
    assert any("Decimal" in schema.get("title", "") for schema in components.values())
    decimal_value = components["DecimalMeasurement"]["properties"]["value"]["anyOf"]
    assert {entry["type"] for entry in decimal_value} == {"string", "null"}
    null_reasons = components["DecimalMeasurement"]["properties"]["null_reason"]["anyOf"]
    assert "withheld" in next(entry["enum"] for entry in null_reasons if "enum" in entry)
    mutation_data = components["AuditMutationResult"]["properties"]
    assert set(mutation_data) == {"data", "meta", "schema_version"}
    assert "AuditApiErrorEnvelope" in components
    assert (
        paths["/v1/benchmark-audit/plans"]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/AuditPlanDocument"
    )
    assert (
        paths["/v1/benchmark-audit/runs"]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]["title"]
        == "AuditRunCreateInput"
    )
