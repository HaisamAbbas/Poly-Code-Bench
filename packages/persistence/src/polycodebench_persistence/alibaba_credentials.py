"""Alibaba ECS RAM-role credentials backed by Alibaba's rotating credential SDK."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

import boto3.session  # type: ignore[import-untyped]
import botocore.session  # type: ignore[import-untyped]
from botocore.credentials import ReadOnlyCredentials  # type: ignore[import-untyped]


class AlibabaCredentialRecord(Protocol):
    access_key_id: str | None
    access_key_secret: str | None
    security_token: str | None


class AlibabaCredentialClient(Protocol):
    def get_credential(self) -> AlibabaCredentialRecord: ...


class AlibabaEcsRamRoleCredentials:
    """Resolve the current SDK-managed STS credentials for each signed request."""

    method = "custom-alibaba-ecs-ram-role"
    account_id: str | None = None

    def __init__(self, credential_client: AlibabaCredentialClient) -> None:
        self._credential_client = credential_client

    def get_frozen_credentials(self) -> ReadOnlyCredentials:
        try:
            record = self._credential_client.get_credential()
        except Exception:
            raise RuntimeError("Alibaba ECS RAM-role credentials are unavailable") from None
        access_key_id = record.access_key_id
        access_key_secret = record.access_key_secret
        security_token = record.security_token
        if not access_key_id or not access_key_secret or not security_token:
            raise RuntimeError("Alibaba ECS RAM-role credentials are unavailable")
        return ReadOnlyCredentials(access_key_id, access_key_secret, security_token)

    def get_deferred_property(self, property_name: str) -> Callable[[], Any]:
        return lambda: getattr(self, property_name, None)


class AlibabaEcsRamRoleProvider:
    METHOD = "custom-alibaba-ecs-ram-role"
    CANONICAL_NAME = "custom Alibaba ECS RAM role"

    def __init__(self, credential_client: AlibabaCredentialClient) -> None:
        self._credential_client = credential_client

    def load(self) -> AlibabaEcsRamRoleCredentials:
        return AlibabaEcsRamRoleCredentials(self._credential_client)


def _credential_client(role_name: str) -> AlibabaCredentialClient:
    """Create Alibaba's IMDSv2-only, automatically refreshing ECS role provider."""
    try:
        from alibabacloud_credentials.client import Client  # type: ignore[import-untyped]
        from alibabacloud_credentials.provider.ecs_ram_role import (  # type: ignore[import-untyped]
            EcsRamRoleCredentialsProvider,
        )
    except ImportError as error:  # pragma: no cover - dependency is part of the runtime lock
        raise RuntimeError("Alibaba ECS RAM-role support is not installed") from error

    provider = EcsRamRoleCredentialsProvider(
        role_name=role_name,
        disable_imds_v1=True,
        async_update_enabled=False,
    )
    return cast(AlibabaCredentialClient, Client(provider=provider))


def boto3_session_for_ram_role(
    role_name: str,
    *,
    credential_client: AlibabaCredentialClient | None = None,
) -> boto3.session.Session:
    """Prioritize an attached Alibaba ECS role and never fall back to static AWS keys."""
    source = credential_client or _credential_client(role_name)
    botocore_session = botocore.session.Session()
    resolver = botocore_session.get_component("credential_provider")
    resolver.insert_before("env", AlibabaEcsRamRoleProvider(source))
    return boto3.session.Session(botocore_session=botocore_session)
