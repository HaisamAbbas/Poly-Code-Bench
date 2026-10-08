"""The audit CLI's dry-run and blocked states do not perform remote work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polycodebench_api import audit_cli


def test_registry_dry_run_is_local_and_has_a_real_read_route(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)

    assert audit_cli.main(["audit", "registry", "list", "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["remote_work"] == "none"
    assert output["method"] == "GET"
    assert output["path"] == "/v1/benchmark-audit/registry"


def test_unsupported_transition_is_blocked_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    decision = tmp_path / "decision.json"
    decision.write_text('{"decision":"accept"}', encoding="utf-8")

    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("uninstalled transitions must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)

    result = audit_cli.main(
        [
            "audit",
            "matches",
            "review",
            "11111111-1111-4111-8111-111111111111",
            "--decision",
            str(decision),
        ]
    )
    assert result == 3
    assert "BLOCKED" in capsys.readouterr().err


def test_health_dry_run_does_not_claim_a_projection_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("health dry-run must remain local")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    result = audit_cli.main(
        [
            "audit",
            "health",
            "11111111-1111-4111-8111-111111111111",
            "--context",
            "22222222-2222-4222-8222-222222222222",
            "--format",
            "json",
            "--dry-run",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 5
    assert output["server_capability"] == "blocked"
    assert output["input"]["projection_available"] is False


def test_cli_rejects_remote_http_and_redirects_are_disabled() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        audit_cli._safe_base_url("http://example.com")
    with pytest.raises(ValueError, match="absolute"):
        audit_cli._safe_base_url("https://user:password@example.com")
    with pytest.raises(ValueError, match="absolute"):
        audit_cli._safe_base_url("https://:password@example.com")

    handler = audit_cli._NoRedirectHandler()
    from urllib.request import Request

    assert (
        handler.redirect_request(
            Request("https://api.example.test"), None, 302, "Found", {}, "https://evil.test"
        )
        is None
    )
