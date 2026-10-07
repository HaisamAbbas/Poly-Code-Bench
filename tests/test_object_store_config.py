from __future__ import annotations

import base64
import hashlib

import pytest
from alibabacloud_credentials import client as credentials_sdk_client
from alibabacloud_credentials.provider import ecs_ram_role as ecs_ram_role_module
from botocore.config import Config
from polycodebench_persistence import alibaba_credentials, object_store
from polycodebench_persistence.object_store import (
    S3ArtifactStore,
    object_store_addressing_style,
    object_store_provider,
    object_store_region,
)


def test_environment_selects_virtual_hosted_s3_addressing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_client(service: str, **kwargs: object) -> object:
        assert service == "s3"
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(object_store.boto3, "client", fake_client)
    monkeypatch.setenv("PCB_OBJECT_STORE_ADDRESSING_STYLE", "virtual")

    S3ArtifactStore.from_environment(
        endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
        buckets={"hidden": "pcb-hidden", "internal": "pcb-internal", "public": "pcb-public"},
        region_name="cn-hangzhou",
    )

    config = calls[0]["config"]
    assert isinstance(config, Config)
    assert config.s3["addressing_style"] == "virtual"
    assert object_store_addressing_style({}) == "path"
    assert object_store_provider({}) == "s3"
    assert object_store_region({}) == "us-east-1"
    with pytest.raises(ValueError, match="must be s3 or alibaba_oss"):
        object_store_provider({"PCB_OBJECT_STORE_PROVIDER": "azure"})
    with pytest.raises(ValueError, match="region identifier"):
        object_store_region({"PCB_OBJECT_STORE_REGION": "cn/hangzhou"})


def test_invalid_object_store_addressing_style_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PCB_OBJECT_STORE_ADDRESSING_STYLE", "bucket-in-path")

    with pytest.raises(ValueError, match="must be path or virtual"):
        S3ArtifactStore.from_environment(
            endpoint_url="https://objects.example.test",
            buckets={"hidden": "hidden", "internal": "internal", "public": "public"},
        )
    with pytest.raises(ValueError, match="must be path or virtual"):
        object_store_addressing_style({"PCB_OBJECT_STORE_ADDRESSING_STYLE": "invalid"})


def test_alibaba_oss_put_uses_signed_atomic_no_overwrite_without_s3_checksums(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PCB_OBJECT_STORE_PROVIDER", "alibaba_oss")
    monkeypatch.setenv("PCB_OBJECT_STORE_REGION", "cn-hangzhou")
    monkeypatch.setenv("PCB_OBJECT_STORE_ADDRESSING_STYLE", "virtual")
    monkeypatch.setenv("PCB_ALIBABA_RAM_ROLE_NAME", "pcb-test-oss-role")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")

    class CredentialClient:
        calls = 0

        def get_credential(self) -> object:
            self.calls += 1
            return type(
                "CredentialRecord",
                (),
                {
                    "access_key_id": "STS.test-role-key",
                    "access_key_secret": "test-role-secret",
                    "security_token": "test-role-session-token",
                },
            )()

    credentials = CredentialClient()
    role_names: list[str] = []

    def fake_credentials_client(role_name: str) -> CredentialClient:
        role_names.append(role_name)
        return credentials

    monkeypatch.setattr(alibaba_credentials, "_credential_client", fake_credentials_client)
    store = S3ArtifactStore.from_environment(
        endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
        buckets={"hidden": "pcb-hidden", "internal": "pcb-internal", "public": "pcb-public"},
    )
    body = b"verified OSS payload"
    seen: dict[str, object] = {}

    def intercept(request: object, **kwargs: object) -> None:
        headers = request.headers  # type: ignore[attr-defined]
        seen["host"] = request.url.split("/")[2]  # type: ignore[attr-defined]
        seen["headers"] = {str(key).lower(): value for key, value in headers.items()}
        raise RuntimeError("request captured before network")

    store._client.meta.events.register("before-send.s3.PutObject", intercept)

    with pytest.raises(RuntimeError, match="captured before network"):
        store.put_verified(
            "internal", "evidence", "sha256:" + hashlib.sha256(body).hexdigest(), body
        )

    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert seen["host"] == "pcb-internal.oss-cn-hangzhou.aliyuncs.com"
    assert headers["x-oss-forbid-overwrite"] == b"true"
    assert "if-none-match" not in headers
    assert "x-amz-sdk-checksum-algorithm" not in headers
    assert "x-amz-trailer" not in headers
    assert headers["x-amz-security-token"] == b"test-role-session-token"
    assert headers["content-md5"] == base64.b64encode(
        hashlib.md5(body, usedforsecurity=False).digest()
    )
    authorization = headers["authorization"].decode("latin1")
    assert "Credential=STS.test-role-key/" in authorization
    signed_headers = authorization.split("SignedHeaders=")[1].split(",")[0]
    assert "x-oss-forbid-overwrite" in signed_headers.split(";")
    assert credentials.calls >= 1
    assert role_names == ["pcb-test-oss-role"]
    assert store._client.meta.region_name == "cn-hangzhou"

    calls_before_next_request = credentials.calls
    next_body = b"next signed operation"
    with pytest.raises(RuntimeError, match="captured before network"):
        store.put_verified(
            "internal",
            "evidence",
            "sha256:" + hashlib.sha256(next_body).hexdigest(),
            next_body,
        )
    assert credentials.calls > calls_before_next_request


def test_alibaba_oss_requires_virtual_hosted_addressing() -> None:
    with pytest.raises(ValueError, match="requires virtual-hosted"):
        S3ArtifactStore(
            endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
            buckets={"hidden": "pcb-hidden", "internal": "pcb-internal", "public": "pcb-public"},
            provider="alibaba_oss",
        )
    with pytest.raises(ValueError, match="DNS-compatible lowercase"):
        S3ArtifactStore(
            endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
            buckets={"hidden": "PCB-hidden", "internal": "pcb-internal", "public": "pcb-public"},
            addressing_style="virtual",
            provider="alibaba_oss",
        )


def test_alibaba_oss_requires_role_name_before_credential_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PCB_OBJECT_STORE_PROVIDER", "alibaba_oss")
    monkeypatch.setenv("PCB_OBJECT_STORE_ADDRESSING_STYLE", "virtual")
    monkeypatch.setenv("PCB_OBJECT_STORE_REGION", "cn-hangzhou")
    monkeypatch.delenv("PCB_ALIBABA_RAM_ROLE_NAME", raising=False)

    with pytest.raises(ValueError, match="valid ECS RAM role name"):
        S3ArtifactStore.from_environment(
            endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
            buckets={"hidden": "pcb-hidden", "internal": "pcb-internal", "public": "pcb-public"},
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://oss-cn-hangzhou.aliyuncs.com",
        "https://attacker.example.test",
        "https://oss-cn-beijing.aliyuncs.com",
        "https://user:password@oss-cn-hangzhou.aliyuncs.com",
        "https://oss-cn-hangzhou.aliyuncs.com:8443",
    ],
)
def test_alibaba_ram_role_never_signs_to_nonregional_or_insecure_endpoints(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="regional HTTPS endpoint"):
        object_store.create_s3_compatible_client(
            endpoint_url=endpoint,
            region_name="cn-hangzhou",
            addressing_style="virtual",
            provider="alibaba_oss",
            ram_role_name="pcb-test-role",
        )


def test_alibaba_sdk_uses_attached_role_and_disables_imdsv1_without_background_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def fake_provider(
        *, role_name: str, disable_imds_v1: bool, async_update_enabled: bool
    ) -> object:
        received["role_name"] = role_name
        received["disable_imds_v1"] = disable_imds_v1
        received["async_update_enabled"] = async_update_enabled
        return object()

    def fake_client(*, provider: object) -> object:
        received["provider"] = provider
        return object()

    monkeypatch.setattr(ecs_ram_role_module, "EcsRamRoleCredentialsProvider", fake_provider)
    monkeypatch.setattr(credentials_sdk_client, "Client", fake_client)

    alibaba_credentials._credential_client("pcb-evidence-reader")

    assert received["role_name"] == "pcb-evidence-reader"
    assert received["disable_imds_v1"] is True
    # The SDK's async mode installs process-global signal handlers and a background scheduler.
    assert received["async_update_enabled"] is False
    assert "provider" in received


def test_alibaba_sdk_errors_are_redacted_before_botocore_sees_them() -> None:
    class BrokenCredentialClient:
        def get_credential(self) -> object:
            raise RuntimeError("provider response included sensitive diagnostic data")

    with pytest.raises(RuntimeError, match="credentials are unavailable") as error:
        alibaba_credentials.AlibabaEcsRamRoleCredentials(
            BrokenCredentialClient()
        ).get_frozen_credentials()

    assert "sensitive diagnostic data" not in str(error.value)
