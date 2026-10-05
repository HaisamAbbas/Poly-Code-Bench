from __future__ import annotations

import secrets
from pathlib import Path

import pytest
import yaml
from polycodebench_operations import localenv, recovery
from sqlalchemy.engine import make_url

from scripts import local_stack


def test_local_prepare_generates_private_database_and_object_store_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local_stack, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(local_stack, "REALM_IMPORT_DIR", tmp_path / "realm")
    monkeypatch.setattr(local_stack, "IDENTITY_PATH", tmp_path / "identities.json")

    values = local_stack.prepare()
    second = local_stack.prepare()

    credential_names = (
        "PCB_LOCAL_POSTGRES_PASSWORD",
        "PCB_LOCAL_S3_ACCESS_KEY",
        "PCB_LOCAL_S3_SECRET_KEY",
        "PCB_WEB_AUTH_SIGNING_KEY",
        "PCB_CURSOR_SIGNING_KEY",
    )
    credentials = [values[name] for name in credential_names]
    assert all(len(value) >= 32 for value in credentials)
    assert len(set(credentials)) == len(credentials)
    assert all(second[name] == values[name] for name in credential_names)
    assert make_url(values["PCB_MIGRATION_DATABASE_URL"]).password == values[
        "PCB_LOCAL_POSTGRES_PASSWORD"
    ]
    assert (
        make_url(values["PCB_OPS_REHEARSAL_DATABASE_URL"]).database
        == local_stack.LOCAL_OPS_DATABASE
    )
    assert values["PCB_LOCAL_POSTGRES_PASSWORD"] not in capsys.readouterr().out


def test_compose_and_rehearsal_config_reference_generated_credentials() -> None:
    compose = yaml.safe_load((local_stack.ROOT / "compose.yaml").read_text("utf-8"))
    services = compose["services"]
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"].startswith(
        "${PCB_LOCAL_POSTGRES_PASSWORD:"
    )
    assert services["object-store"]["environment"]["AWS_ACCESS_KEY_ID"].startswith(
        "${PCB_LOCAL_S3_ACCESS_KEY:"
    )
    assert services["object-store"]["environment"]["AWS_SECRET_ACCESS_KEY"].startswith(
        "${PCB_LOCAL_S3_SECRET_KEY:"
    )

    rehearsal = yaml.safe_load(
        (local_stack.ROOT / "config/operations/rehearsal-local.yaml").read_text("utf-8")
    )["source"]
    assert rehearsal["database_url_env"] == "PCB_OPS_REHEARSAL_DATABASE_URL"
    assert rehearsal["admin_database_url_env"] == "PCB_MIGRATION_DATABASE_URL"
    assert "database_url" not in rehearsal and "admin_database_url" not in rehearsal


def test_isolated_rehearsal_generates_independent_credentials() -> None:
    first = localenv.IsolatedEnvironment("a", "n1", "p1", "s1")
    second = localenv.IsolatedEnvironment("b", "n2", "p2", "s2")

    assert first.postgres_password != second.postgres_password
    assert first.object_store_access_key != second.object_store_access_key
    assert first.object_store_secret_key != second.object_store_secret_key
    assert make_url(first.database_url).password == first.postgres_password


def test_local_s3_client_requires_and_uses_ephemeral_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    def fake_client(service: str, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return kwargs

    monkeypatch.setattr(recovery.boto3, "client", fake_client)
    with pytest.raises(RuntimeError, match="local object-store credentials"):
        recovery._s3("http://127.0.0.1:8333")

    access_key, secret_key = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    result = recovery._s3("http://127.0.0.1:8333", access_key, secret_key)
    assert result["aws_access_key_id"] == access_key
    assert result["aws_secret_access_key"] == secret_key
