from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/configuration/src"))

import pytest

from polycodebench_configuration import load_startup_config


def test_missing_role_specific_settings_fail_without_echoing_input() -> None:
    with pytest.raises(ValueError, match="missing required settings"):
        load_startup_config(
            {"PCB_ENVIRONMENT": "dev", "PCB_ROLE": "api", "PCB_SERVICE_IDENTITY": "api"}
        )


def test_api_config_accepts_local_reference_and_rejects_extra_keys() -> None:
    result = load_startup_config(
        {
            "PCB_ENVIRONMENT": "dev",
            "PCB_ROLE": "api",
            "PCB_SERVICE_IDENTITY": "api",
            "PCB_DATABASE_DSN_REF": "local-reference",
            "PCB_OIDC_ISSUER": "http://localhost:3000",
            "PCB_OIDC_AUDIENCE": "local",
        }
    )
    assert result.role.value == "api"
    with pytest.raises(ValueError, match="unknown PCB configuration keys"):
        load_startup_config(
            {
                "PCB_ENVIRONMENT": "dev",
                "PCB_ROLE": "api",
                "PCB_SERVICE_IDENTITY": "api",
                "PCB_DATABASE_DSN_REF": "local-reference",
                "PCB_OIDC_ISSUER": "http://localhost:3000",
                "PCB_OIDC_AUDIENCE": "local",
                "PCB_DATABASE_PASSWORD": "must-not-be-read",
            }
        )


def test_worker_config_requires_capacity_and_production_uses_https() -> None:
    with pytest.raises(ValueError, match="PCB_WORKER_QUEUE_CLASSES"):
        load_startup_config(
            {
                "PCB_ENVIRONMENT": "dev",
                "PCB_ROLE": "scheduler",
                "PCB_SERVICE_IDENTITY": "scheduler",
                "PCB_DATABASE_DSN_REF": "local-reference",
            }
        )
    with pytest.raises(ValueError, match="HTTPS"):
        load_startup_config(
            {
                "PCB_ENVIRONMENT": "production",
                "PCB_ROLE": "api",
                "PCB_SERVICE_IDENTITY": "api",
                "PCB_DATABASE_DSN_REF": "secret-reference",
                "PCB_OIDC_ISSUER": "http://id.example.org",
                "PCB_OIDC_AUDIENCE": "api",
            }
        )
