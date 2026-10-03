"""Prompt 32: authenticated requests, reviewer gates and a bounded test approval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from polycodebench_api.app import create_app
from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_core.endpoint_policy import (
    EndpointNetworkPolicy,
    NetworkPolicyKind,
    ParsedEndpoint,
    RegisteredEndpoint,
    check_resolved_addresses,
)
from polycodebench_core.model_contracts import (
    EndpointNotApproved,
    EndpointPolicyViolation,
    ModelCapabilities,
    ProviderKind,
)
from polycodebench_publication.releases import ReleaseStore
from polycodebench_services.model_endpoints import ModelEndpointService
from polycodebench_services.runs import RunCreationService

SUBMISSION_URL = "https://models.example.org/v1"
SOURCE_URL = "https://models.example.org/model/card"
ENDPOINT_ID = UUID("2c4a3db9-ec35-45f6-9f1b-47db6120a551")


def _submission(**updates: object) -> dict[str, object]:
    return {
        "kind": "model_submission_input",
        "model_name": "Fixture Candidate 1",
        "provider": "Fixture Provider",
        "contact_email": "submitter@example.org",
        "endpoint_url": SUBMISSION_URL,
        "source_url": SOURCE_URL,
        "source_license": "test-only permission",
        "permission_attested": True,
        **updates,
    }


def _token_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


class FakeEndpointRepository:
    def __init__(self, *, approved: bool = True) -> None:
        self.approved = approved
        self.lookups = 0
        self.contacts = 0
        self.policy = EndpointNetworkPolicy(
            kind=NetworkPolicyKind.PUBLIC_ALLOWLIST,
            allowed_hosts=("models.example.org",),
        )
        self.registered = RegisteredEndpoint(
            endpoint_id=ENDPOINT_ID,
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            endpoint=ParsedEndpoint(
                scheme="https", host="models.example.org", port=443, base_path="/v1"
            ),
            secret_ref="secret://fixture/model-provider",
            policy=self.policy,
            declared_capabilities=ModelCapabilities(
                native_tools=False,
                structured_output=False,
                seed=False,
                temperature=False,
                output_cap_bounds_all_billed_output=True,
            ),
        )

    def get_approved(self, endpoint_id: UUID) -> RegisteredEndpoint:
        self.lookups += 1
        if endpoint_id != ENDPOINT_ID or not self.approved:
            raise EndpointNotApproved()
        return self.registered


class FakeRunRepository:
    """Exercise the actual idempotent run service without dispatching a provider call."""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[dict[str, object]] = []
        self.by_key: dict[tuple[str, str, str], tuple[str, str, list[str]]] = {}
        self.run_id = str(uuid4())
        self.attempt_ids = [str(uuid4()), str(uuid4())]

    def create_idempotently(
        self,
        *,
        subject_id: str,
        route: str,
        idempotency_key: str,
        request_digest: str,
        request: dict[str, object],
    ) -> dict[str, object]:
        self.calls += 1
        self.requests.append(dict(request))
        key = (subject_id, route, idempotency_key)
        current = self.by_key.get(key)
        if current is not None:
            assert current[0] == request_digest
            return {"run_id": current[1], "attempt_ids": current[2], "replayed": True}
        self.by_key[key] = (request_digest, self.run_id, self.attempt_ids)
        return {"run_id": self.run_id, "attempt_ids": self.attempt_ids}


class FakeRunSummary:
    def __init__(self) -> None:
        self.status = "queued"

    def get_run_summary(self, run_id: UUID) -> dict[str, object] | None:
        if str(run_id) != run_repo.run_id:
            return None
        return {
            "status": self.status,
            "attempt_count": 2,
            "completed_count": 0,
            "failed_count": 0,
            "spent_micro_usd": 0,
            "reserved_micro_usd": 1_000_000,
            "uncertain_micro_usd": 0,
            "max_cost_micro_usd": 1_000_000,
        }


run_repo = FakeRunRepository()


def _app(
    tmp_path: Path, *, endpoint_approved: bool = True
) -> tuple[FastAPI, FakeEndpointRepository]:
    release_store = ReleaseStore(tmp_path / "prompt32.sqlite3")
    create_synthetic_release(release_store)
    endpoint_repo = FakeEndpointRepository(approved=endpoint_approved)
    run_repo.calls = 0
    run_repo.requests.clear()
    run_repo.by_key.clear()
    runs = FakeRunSummary()
    tokens = TokenDirectory(
        {
            "submitter-token-00000001": ApiPrincipal(
                "account-submit-1",
                frozenset({"submitter"}),
                email="submitter@example.org",
                email_verified=True,
            ),
            "submitter-token-00000002": ApiPrincipal(
                "account-submit-2",
                frozenset({"submitter"}),
                email="other@example.org",
                email_verified=True,
            ),
            "reviewer-token-00000001": ApiPrincipal(
                "reviewer-1", frozenset({"reviewer"}), mfa=True
            ),
            "reviewer-token-no-mfa": ApiPrincipal("reviewer-no-mfa", frozenset({"reviewer"})),
            "public-reader-token-0001": ApiPrincipal("public-reader", frozenset()),
            "admin-token-no-mfa-01": ApiPrincipal("admin-no-mfa", frozenset({"administrator"})),
            "admin-token-mfa-0001": ApiPrincipal("admin-1", frozenset({"administrator"}), mfa=True),
        }
    )
    return (
        create_app(
            store=release_store,
            cursor_key=b"prompt32-test-cursor-key-00000000",
            tokens=tokens,
            run_creation=RunCreationService(run_repo),
            endpoints=ModelEndpointService(endpoint_repo),
            runs=runs,
        ),
        endpoint_repo,
    )


def _approval_plan(*, samples: int = 2) -> dict[str, object]:
    return {
        "endpoint_registration_id": str(ENDPOINT_ID),
        "run_request": {
            "campaign_id": str(uuid4()),
            "config_document_id": str(uuid4()),
            "task_set_id": str(uuid4()),
            "model_revision_id": str(uuid4()),
            "samples_per_task": samples,
            "master_seed": "21",
            "max_attempts": 10,
            "max_cost_micro_usd": 1_000_000,
            "max_input_tokens": 100_000,
            "max_output_tokens": 20_000,
            "endpoint_registration_id": str(ENDPOINT_ID),
        },
        "permission_review": {
            "kind": "source_permission_review",
            "source_url": SOURCE_URL,
            "source_license": "test-only permission",
            "evidence_reference": "review-ticket-32-fixture",
            "rights_confirmed": True,
            "decision_reason": "The synthetic fixture has an explicit test-only permission record.",
        },
    }


async def _assert_submission_lifecycle(app: FastAPI, endpoints: FakeEndpointRepository) -> None:
    original_getaddrinfo = socket.getaddrinfo

    def forbidden_resolution(*args: object, **kwargs: object) -> object:
        raise AssertionError("model submission must never resolve or contact its endpoint")

    socket.getaddrinfo = forbidden_resolution
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            pending = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "submit-32-1",
                },
                json=_submission(),
            )
            assert pending.status_code == 201
            first = pending.json()["data"]
            assert first["status"] == "pending"
            assert first["resulting_run_id"] is None
            assert run_repo.calls == 0
            assert endpoints.contacts == 0

            public_write = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("public-reader-token-0001"),
                    "Idempotency-Key": "public-read-only",
                },
                json=_submission(),
            )
            assert public_write.status_code == 403

            replay = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "submit-32-1",
                },
                json=_submission(),
            )
            assert replay.json()["data"]["submission_id"] == first["submission_id"]
            assert run_repo.calls == 0

            secret_value = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "secret-attempt",
                },
                json=_submission(provider_secret="sk-public-must-be-rejected"),
            )
            assert secret_value.status_code == 422
            assert "sk-public-must-be-rejected" not in secret_value.text
            private_ip = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "private-ip",
                },
                json=_submission(endpoint_url="https://169.254.169.254/latest/meta-data"),
            )
            assert private_ip.status_code == 422
            wrong_account_email = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "email-mismatch",
                },
                json=_submission(contact_email="other@example.org"),
            )
            assert wrong_account_email.status_code == 403

            foreign_read = await client.get(
                f"/v1/model-submissions/{first['submission_id']}",
                headers=_token_headers("submitter-token-00000002"),
            )
            assert foreign_read.status_code == 404
            own_read = await client.get(
                f"/v1/model-submissions/{first['submission_id']}",
                headers=_token_headers("submitter-token-00000001"),
            )
            assert own_read.status_code == 200
            assert own_read.json()["data"]["status"] == "pending"

            denied = await client.get(
                "/v1/admin/model-submissions",
                headers=_token_headers("reviewer-token-no-mfa"),
            )
            assert denied.status_code == 403
            reviewer_cannot_approve = await client.post(
                f"/v1/admin/model-submissions/{first['submission_id']}/approve",
                headers={
                    **_token_headers("reviewer-token-00000001"),
                    "Idempotency-Key": "reviewer-approve",
                },
                json=_approval_plan(),
            )
            assert reviewer_cannot_approve.status_code == 403
            admin_without_mfa = await client.post(
                f"/v1/admin/model-submissions/{first['submission_id']}/approve",
                headers={
                    **_token_headers("admin-token-no-mfa-01"),
                    "Idempotency-Key": "admin-no-mfa",
                },
                json=_approval_plan(),
            )
            assert admin_without_mfa.status_code == 403
            assert run_repo.calls == 0

            reviewer_list = await client.get(
                "/v1/admin/model-submissions",
                headers=_token_headers("reviewer-token-00000001"),
            )
            assert reviewer_list.status_code == 200
            assert reviewer_list.json()["data"][0]["requester_subject"] == "account-submit-1"

            second_submission = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "reject-fixture-32",
                },
                json=_submission(model_name="Synthetic Rejected Candidate"),
            )
            assert second_submission.status_code == 201
            rejected_id = second_submission.json()["data"]["submission_id"]
            reject = await client.post(
                f"/v1/admin/model-submissions/{rejected_id}/reject",
                headers={
                    **_token_headers("reviewer-token-00000001"),
                    "Idempotency-Key": "reject-32",
                },
                json={
                    "reason": "Synthetic test request rejected by reviewer.",
                    "expected_version": 0,
                },
            )
            assert reject.status_code == 200
            assert reject.json()["data"]["status"] == "rejected"
            rejected_owner_status = await client.get(
                f"/v1/model-submissions/{rejected_id}",
                headers=_token_headers("submitter-token-00000001"),
            )
            assert rejected_owner_status.json()["data"]["status"] == "rejected"
            assert (
                rejected_owner_status.json()["data"]["rejection_reason"]
                == "Synthetic test request rejected by reviewer."
            )

            approval_headers = {
                **_token_headers("admin-token-mfa-0001"),
                "Idempotency-Key": "approve-fixture-32",
            }
            approval_plan = _approval_plan()
            approved = await client.post(
                f"/v1/admin/model-submissions/{first['submission_id']}/approve",
                headers=approval_headers,
                json=approval_plan,
            )
            assert approved.status_code == 202
            approved_data = approved.json()["data"]
            assert approved_data["status"] == "approved"
            assert approved_data["resulting_run_id"] == run_repo.run_id
            assert approved_data["approved_plan"] == approval_plan["run_request"]
            assert (
                approved_data["permission_review"]["evidence_reference"]
                == "review-ticket-32-fixture"
            )
            assert run_repo.calls == 1
            created_run = run_repo.requests[0]
            assert created_run["max_attempts"] == 10
            assert created_run["max_cost_micro_usd"] == 1_000_000
            assert created_run["max_input_tokens"] == 100_000
            assert created_run["max_output_tokens"] == 20_000
            assert endpoints.contacts == 0

            approval_replay = await client.post(
                f"/v1/admin/model-submissions/{first['submission_id']}/approve",
                headers=approval_headers,
                json=approval_plan,
            )
            assert approval_replay.status_code == 202
            assert approval_replay.json()["data"]["resulting_run_id"] == run_repo.run_id
            assert run_repo.calls == 1
            changed_plan = await client.post(
                f"/v1/admin/model-submissions/{first['submission_id']}/approve",
                headers={
                    **_token_headers("admin-token-mfa-0001"),
                    "Idempotency-Key": "approve-changed",
                },
                json=_approval_plan(samples=3),
            )
            assert changed_plan.status_code == 409
            assert run_repo.calls == 1

            app.state.services.runs.status = "running"
            updated_status = await client.get(
                f"/v1/model-submissions/{first['submission_id']}",
                headers=_token_headers("submitter-token-00000001"),
            )
            assert updated_status.json()["data"]["run_status"] == "running"
            assert "secret://" not in updated_status.text
    finally:
        socket.getaddrinfo = original_getaddrinfo


def test_prompt32_submission_permissions_bounded_approval_and_no_endpoint_io(
    tmp_path: Path,
) -> None:
    app, endpoints = _app(tmp_path)
    asyncio.run(_assert_submission_lifecycle(app, endpoints))


def test_prompt32_unapproved_endpoint_is_not_contacted_or_run(tmp_path: Path) -> None:
    app, endpoints = _app(tmp_path, endpoint_approved=False)

    async def verify() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "pending-malicious",
                },
                json=_submission(endpoint_url="https://attacker.example.org/v1"),
            )
            assert created.status_code == 201
            rejected = await client.post(
                f"/v1/admin/model-submissions/{created.json()['data']['submission_id']}/approve",
                headers={
                    **_token_headers("admin-token-mfa-0001"),
                    "Idempotency-Key": "approve-untrusted",
                },
                json=_approval_plan(),
            )
            assert rejected.status_code == 403
            assert run_repo.calls == 0
            assert endpoints.contacts == 0
            assert endpoints.lookups == 1

    asyncio.run(verify())


def test_prompt32_rate_limits_verified_submitter_and_rejects_secret_schema(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)

    async def verify() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for index in range(5):
                response = await client.post(
                    "/v1/model-submissions",
                    headers={
                        **_token_headers("submitter-token-00000001"),
                        "Idempotency-Key": f"five-per-hour-{index}",
                    },
                    json=_submission(),
                )
                assert response.status_code == 201
            limited = await client.post(
                "/v1/model-submissions",
                headers={
                    **_token_headers("submitter-token-00000001"),
                    "Idempotency-Key": "sixth-request",
                },
                json=_submission(),
            )
            assert limited.status_code == 429
            assert limited.headers["retry-after"] == "3600"
            assert run_repo.calls == 0

    asyncio.run(verify())


def test_prompt32_hashed_identity_file_and_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "long-random-test-bearer-value-32"
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "principals": [
                    {
                        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                        "principal": {
                            "subject_id": "verified-user",
                            "roles": ["submitter"],
                            "mfa": False,
                            "email": "verified@example.org",
                            "email_verified": True,
                            "expires_at": 4_102_444_800,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PCB_API_IDENTITY_FILE", str(path))
    monkeypatch.setenv("PCB_ENVIRONMENT", "production")
    directory = TokenDirectory.from_env()
    assert directory.resolve(token) == ApiPrincipal(
        "verified-user",
        frozenset({"submitter"}),
        email="verified@example.org",
        email_verified=True,
        expires_at=4_102_444_800,
    )
    assert directory.resolve("unknown-token-000000000") is None
    assert token not in path.read_text(encoding="utf-8")
    monkeypatch.setenv("PCB_ENVIRONMENT", "development")
    monkeypatch.delenv("PCB_DATABASE_URL", raising=False)
    app = create_app(
        store=ReleaseStore(tmp_path / "prompt32-auth.sqlite3"),
        cursor_key=b"prompt32-auth-file-test-cursor-key",
    )

    async def verify_authenticated_route() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            status = await client.get(
                "/v1/model-submissions/00000000-0000-4000-8000-000000000001",
                headers=_token_headers(token),
            )
            assert status.status_code == 404
            unknown = await client.get(
                "/v1/model-submissions/00000000-0000-4000-8000-000000000001",
                headers=_token_headers("unknown-token-000000000000"),
            )
            assert unknown.status_code == 401

    asyncio.run(verify_authenticated_route())
    invalid = json.loads(path.read_text(encoding="utf-8"))
    invalid["principals"][0]["principal"]["expires_at"] = 1
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(RuntimeError, match="expired"):
        TokenDirectory.from_env()


def test_prompt32_endpoint_policy_rejects_private_and_mixed_dns_answers() -> None:
    policy = EndpointNetworkPolicy(
        kind=NetworkPolicyKind.PUBLIC_ALLOWLIST,
        allowed_hosts=("models.example.org",),
    )
    with pytest.raises(EndpointPolicyViolation):
        check_resolved_addresses(["203.0.113.18"], policy)
    with pytest.raises(EndpointPolicyViolation):
        check_resolved_addresses(["8.8.8.8", "169.254.169.254"], policy)
