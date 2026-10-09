"""Operator CLI end-to-end tests through the authenticated API and local repositories."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from polycodebench_api import audit_cli
from polycodebench_api.app import create_app
from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.submissions import SubmissionStore
from polycodebench_core.application_errors import IdempotencyConflict
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, RegisteredEndpoint
from polycodebench_core.model_contracts import ModelCapabilities, ProviderKind
from polycodebench_publication.releases import ReleaseStore
from polycodebench_services.model_endpoints import ModelEndpointService


class MemoryEndpointRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, object]] = {}
        self.decisions: dict[tuple[str, str], dict[str, object]] = {}

    def register(
        self,
        *,
        registration_id: UUID | None = None,
        provider_kind: ProviderKind,
        base_url: str,
        secret_ref: str,
        policy: EndpointNetworkPolicy,
        declared_capabilities: ModelCapabilities,
        registered_by: str,
    ) -> UUID:
        assert registration_id is not None
        capabilities = declared_capabilities.model_dump(mode="json")
        self.rows.setdefault(
            registration_id,
            {
                "endpoint_registration_id": str(registration_id),
                "provider_kind": provider_kind.value,
                "base_url": base_url,
                "secret_configured": secret_ref != "none",
                "network_policy_id": policy.kind.value,
                "approval_status": "pending",
                "capabilities_digest": canonical_digest(capabilities),
                "registered_by": registered_by,
                "network_policy": policy.model_dump(mode="json"),
                "declared_capabilities": capabilities,
                "conformance_report": None,
                "approved_by": None,
                "approved_at": None,
                "decision_reason": None,
                "row_version": 0,
                "created_at": datetime.now(UTC),
            },
        )
        return registration_id

    def decide(
        self,
        endpoint_id: UUID,
        *,
        decision: str,
        actor: str,
        reason: str,
        expected_version: int,
        conformance_report: dict[str, object] | None = None,
        request_id: str | None = None,
    ) -> None:
        request = {
            "endpoint_id": str(endpoint_id),
            "decision": decision,
            "reason": reason,
            "expected_version": expected_version,
            "conformance_report": conformance_report,
        }
        replay_key = (actor, request_id) if request_id is not None else None
        if replay_key is not None and replay_key in self.decisions:
            if self.decisions[replay_key] != request:
                raise IdempotencyConflict()
            return
        row = self.rows[endpoint_id]
        assert row["row_version"] == expected_version
        row["approval_status"] = decision
        row["decision_reason"] = reason
        row["row_version"] = expected_version + 1
        row["conformance_report"] = conformance_report
        if decision == "approved":
            row["approved_by"] = actor
            row["approved_at"] = datetime.now(UTC)
        if replay_key is not None:
            self.decisions[replay_key] = request

    def get_approved(self, endpoint_id: UUID) -> RegisteredEndpoint:
        row = self.rows[endpoint_id]
        policy = EndpointNetworkPolicy.model_validate(row["network_policy"], strict=False)
        from polycodebench_core.endpoint_policy import parse_endpoint_url

        return RegisteredEndpoint(
            endpoint_id=endpoint_id,
            provider_kind=ProviderKind(str(row["provider_kind"])),
            endpoint=parse_endpoint_url(str(row["base_url"]), policy),
            secret_ref="secret://test/model",
            policy=policy,
            declared_capabilities=ModelCapabilities.model_validate(
                row["declared_capabilities"], strict=False
            ),
        )

    def get_registration(self, endpoint_id: UUID) -> dict[str, object] | None:
        return self.rows.get(endpoint_id)

    def list_registrations(
        self, *, statuses: tuple[str, ...], limit: int
    ) -> tuple[dict[str, object], ...]:
        return tuple(row for row in self.rows.values() if row["approval_status"] in statuses)[
            :limit
        ]


def _operator_payload(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    submission = {
        "kind": "model_submission_input",
        "model_name": "CLI Test Model",
        "provider": "CLI Test Provider",
        "contact_email": "operator@example.org",
        "endpoint_url": "https://models.example.org/v1",
        "source_url": "https://models.example.org/model-card",
        "source_license": "test permission",
        "permission_attested": True,
    }
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(json.dumps(submission), encoding="utf-8")
    capabilities = ModelCapabilities(
        native_tools=False,
        structured_output=False,
        seed=False,
        temperature=False,
        output_cap_bounds_all_billed_output=True,
    )
    endpoint = {
        "provider_kind": "openai_compatible",
        "base_url": "https://models.example.org/v1",
        "secret_ref": "secret://test/model",
        "network_policy": {
            "kind": "public-https-allowlist",
            "allowed_hosts": ["models.example.org"],
            "allowed_cidrs": [],
        },
        "declared_capabilities": capabilities.model_dump(mode="json"),
    }
    endpoint_path = tmp_path / "endpoint.json"
    endpoint_path.write_text(json.dumps(endpoint), encoding="utf-8")
    return submission_path, endpoint_path, submission


def test_cli_runs_submission_and_endpoint_review_flows_through_authenticated_api(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    submission_path, endpoint_path, submission = _operator_payload(tmp_path)
    endpoints = MemoryEndpointRepository()
    app = create_app(
        store=ReleaseStore(tmp_path / "release.sqlite3"),
        cursor_key=b"operator-cli-end-to-end-test-key-000",
        tokens=TokenDirectory(
            {
                "submitter-cli-token-00000001": ApiPrincipal(
                    "operator-account",
                    frozenset({"submitter"}),
                    email="operator@example.org",
                    email_verified=True,
                ),
                "admin-cli-token-0000000001": ApiPrincipal(
                    "operator-account",
                    frozenset({"administrator"}),
                    mfa=True,
                    email="operator@example.org",
                    email_verified=True,
                ),
            }
        ),
        submissions=SubmissionStore(tmp_path / "submissions.sqlite3"),
        endpoints=ModelEndpointService(endpoints),
    )

    with TestClient(app) as client:

        def request(method, url, token, body, extra_headers):
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

        monkeypatch.setattr(audit_cli, "_request", request)
        base = ["--api-url", "http://127.0.0.1:8000"]

        assert (
            audit_cli.main(
                [
                    "submissions",
                    "submit",
                    "--payload",
                    str(submission_path),
                    "--idempotency-key",
                    "cli-submit-001",
                    "--token",
                    "submitter-cli-token-00000001",
                    *base,
                ]
            )
            == 0
        )
        created = json.loads(capsys.readouterr().out)["data"]
        submission_id = created["submission_id"]
        assert created["status"] == "pending"
        assert created["contact_email"] == submission["contact_email"]

        assert (
            audit_cli.main(["submissions", "list", "--token", "admin-cli-token-0000000001", *base])
            == 0
        )
        queue = json.loads(capsys.readouterr().out)["data"]
        assert [item["submission_id"] for item in queue] == [submission_id]

        rejection_path = tmp_path / "rejection.json"
        rejection_path.write_text(
            json.dumps({"reason": "Synthetic CLI test rejection", "expected_version": 0}),
            encoding="utf-8",
        )
        assert (
            audit_cli.main(
                [
                    "submissions",
                    "reject",
                    submission_id,
                    "--decision",
                    str(rejection_path),
                    "--idempotency-key",
                    "cli-reject-001",
                    "--token",
                    "admin-cli-token-0000000001",
                    *base,
                ]
            )
            == 0
        )
        rejected = json.loads(capsys.readouterr().out)["data"]
        assert rejected["status"] == "rejected"
        assert rejected["rejection_reason"] == "Synthetic CLI test rejection"

        register_args = [
            "endpoints",
            "register",
            "--payload",
            str(endpoint_path),
            "--idempotency-key",
            "cli-endpoint-001",
            "--token",
            "admin-cli-token-0000000001",
            *base,
        ]
        assert audit_cli.main(register_args) == 0
        endpoint_id = json.loads(capsys.readouterr().out)["data"]["endpoint_registration_id"]
        assert audit_cli.main(register_args) == 0
        replay = json.loads(capsys.readouterr().out)["data"]["endpoint_registration_id"]
        assert replay == endpoint_id

        assert (
            audit_cli.main(["endpoints", "list", "--token", "admin-cli-token-0000000001", *base])
            == 0
        )
        endpoint_queue = json.loads(capsys.readouterr().out)["data"]
        assert [item["endpoint_registration_id"] for item in endpoint_queue] == [endpoint_id]

        assert (
            audit_cli.main(
                ["endpoints", "show", endpoint_id, "--token", "admin-cli-token-0000000001", *base]
            )
            == 0
        )
        registered = json.loads(capsys.readouterr().out)["data"]
        assert registered["approval_status"] == "pending"
        assert "secret_ref" not in registered

        decision_path = tmp_path / "endpoint-decision.json"
        decision_path.write_text(
            json.dumps(
                {
                    "decision": "approved",
                    "reason": "Synthetic CLI test approval",
                    "expected_version": 0,
                    "conformance_report": {"passed": True, "probe": "passed"},
                }
            ),
            encoding="utf-8",
        )
        assert (
            audit_cli.main(
                [
                    "endpoints",
                    "decide",
                    endpoint_id,
                    "--decision",
                    str(decision_path),
                    "--idempotency-key",
                    "cli-endpoint-decide-001",
                    "--token",
                    "admin-cli-token-0000000001",
                    *base,
                ]
            )
            == 0
        )
        decision = json.loads(capsys.readouterr().out)["data"]
        assert decision["status"] == "approved"
        assert (
            audit_cli.main(
                [
                    "endpoints",
                    "decide",
                    endpoint_id,
                    "--decision",
                    str(decision_path),
                    "--idempotency-key",
                    "cli-endpoint-decide-001",
                    "--token",
                    "admin-cli-token-0000000001",
                    *base,
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["data"]["status"] == "approved"
        conflicting_replay = client.post(
            f"/v1/admin/model-endpoints/{endpoint_id}/decision",
            headers={
                "Authorization": "Bearer admin-cli-token-0000000001",
                "Idempotency-Key": "cli-endpoint-decide-001",
            },
            json={
                "decision": "approved",
                "reason": "Different decision content",
                "expected_version": 0,
                "conformance_report": {"passed": True, "probe": "passed"},
            },
        )
        assert conflicting_replay.status_code == 409


def test_submission_cli_dry_run_validates_private_input_without_network(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    submission_path, _, _ = _operator_payload(tmp_path)

    def no_request(*args, **kwargs):
        raise AssertionError("dry-run must not make an HTTP request")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    assert (
        audit_cli.main(
            [
                "submissions",
                "submit",
                "--payload",
                str(submission_path),
                "--idempotency-key",
                "cli-submit-002",
                "--api-url",
                "http://127.0.0.1:8000",
                "--dry-run",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["remote_work"] == "none"
    assert output["method"] == "POST"
    assert output["path"] == "/v1/model-submissions"
    assert "contact_email" not in output["input"]


def test_audit_cli_capabilities_are_local_and_never_claim_dispatch(capsys) -> None:
    assert audit_cli.main(["audit", "capabilities"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dispatch_authorized"] is False
    assert "audit matches review" in output["blocked_without_reviewed_adapters"]
    assert "audit plan (immutable document, no dispatch)" in output["remote_writes"]
