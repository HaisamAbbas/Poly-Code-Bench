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
        "PCB_LOCAL_WORKER_PASSWORD",
        "PCB_LOCAL_SCORER_PASSWORD",
        "PCB_WEB_AUTH_SIGNING_KEY",
        "PCB_CURSOR_SIGNING_KEY",
    )
    credentials = [values[name] for name in credential_names]
    assert all(len(value) >= 32 for value in credentials)
    assert len(set(credentials)) == len(credentials)
    assert all(second[name] == values[name] for name in credential_names)
    assert (
        make_url(values["PCB_MIGRATION_DATABASE_URL"]).password
        == values["PCB_LOCAL_POSTGRES_PASSWORD"]
    )
    assert (
        make_url(values["PCB_OPS_REHEARSAL_DATABASE_URL"]).database
        == local_stack.LOCAL_OPS_DATABASE
    )
    assert make_url(values["PCB_WORKER_DATABASE_URL"]).username == local_stack.LOCAL_WORKER_ROLE
    assert (
        make_url(values["PCB_WORKER_DATABASE_URL"]).password == values["PCB_LOCAL_WORKER_PASSWORD"]
    )
    assert values["PCB_WORKER_DISPATCH_ENABLED"] == "false"
    assert values["PCB_LOCAL_WORKER_SETUP_ENABLED"] == "false"
    assert make_url(values["PCB_SCORER_DATABASE_URL"]).username == local_stack.LOCAL_SCORER_ROLE
    assert (
        make_url(values["PCB_SCORER_DATABASE_URL"]).password == values["PCB_LOCAL_SCORER_PASSWORD"]
    )
    assert values["PCB_LOCAL_SCORING_ENABLED"] == "false"
    assert values["PCB_SERVICE_IDENTITY"] == "polycodebench-local-development"
    assert values["PCB_BUCKET_INTERNAL"] == "pcb-internal-local"
    assert values["PCB_OBJECT_STORE_ADDRESSING_STYLE"] == "path"
    assert values["PCB_LOCAL_POSTGRES_PASSWORD"] not in capsys.readouterr().out


def test_local_prepare_adds_worker_settings_to_existing_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setattr(local_stack, "ENV_PATH", env_path)
    monkeypatch.setattr(local_stack, "REALM_IMPORT_DIR", tmp_path / "realm")
    monkeypatch.setattr(local_stack, "IDENTITY_PATH", tmp_path / "identities.json")
    original = local_stack.prepare()
    omitted = {
        "PCB_LOCAL_WORKER_PASSWORD",
        "PCB_WORKER_DATABASE_URL",
        "PCB_LOCAL_SCORER_PASSWORD",
        "PCB_SCORER_DATABASE_URL",
        "PCB_ENVIRONMENT",
        "PCB_SERVICE_IDENTITY",
        "PCB_LOCAL_WORKER_SETUP_ENABLED",
        "PCB_WORKER_DISPATCH_ENABLED",
        "PCB_OBJECT_STORE_ENDPOINT",
        "PCB_OBJECT_STORE_ADDRESSING_STYLE",
        "PCB_BUCKET_HIDDEN",
        "PCB_BUCKET_INTERNAL",
        "PCB_BUCKET_PUBLIC",
    }
    env_path.write_text(
        "\n".join(
            line
            for line in env_path.read_text("utf-8").splitlines()
            if line.split("=", 1)[0] not in omitted
        )
        + "\n",
        encoding="utf-8",
    )

    upgraded = local_stack.prepare()

    assert upgraded["PCB_LOCAL_WORKER_PASSWORD"]
    assert (
        make_url(upgraded["PCB_WORKER_DATABASE_URL"]).password
        == upgraded["PCB_LOCAL_WORKER_PASSWORD"]
    )
    assert upgraded["PCB_LOCAL_API_PASSWORD"] == original["PCB_LOCAL_API_PASSWORD"]
    assert upgraded["PCB_SERVICE_IDENTITY"] == "polycodebench-local-development"
    assert upgraded["PCB_WORKER_DISPATCH_ENABLED"] == "false"
    assert upgraded["PCB_LOCAL_SCORER_PASSWORD"]
    assert (
        make_url(upgraded["PCB_SCORER_DATABASE_URL"]).password
        == upgraded["PCB_LOCAL_SCORER_PASSWORD"]
    )

    env_path.write_text(
        "\n".join(
            line
            for line in env_path.read_text("utf-8").splitlines()
            if line.split("=", 1)[0] != "PCB_LOCAL_WORKER_PASSWORD"
        )
        + "\n",
        encoding="utf-8",
    )
    recovered = local_stack.prepare()
    assert recovered["PCB_LOCAL_WORKER_PASSWORD"] == upgraded["PCB_LOCAL_WORKER_PASSWORD"]
    env_path.write_text(
        "\n".join(
            line
            for line in env_path.read_text("utf-8").splitlines()
            if line.split("=", 1)[0] != "PCB_LOCAL_SCORER_PASSWORD"
        )
        + "\n",
        encoding="utf-8",
    )
    recovered = local_stack.prepare()
    assert recovered["PCB_LOCAL_SCORER_PASSWORD"] == upgraded["PCB_LOCAL_SCORER_PASSWORD"]


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
