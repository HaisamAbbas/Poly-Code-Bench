from __future__ import annotations

import base64
import hashlib

import pytest
from botocore.config import Config
from polycodebench_persistence import object_store
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
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
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
    assert headers["content-md5"] == base64.b64encode(
        hashlib.md5(body, usedforsecurity=False).digest()
    )
    authorization = headers["authorization"].decode("latin1")
    signed_headers = authorization.split("SignedHeaders=")[1].split(",")[0]
    assert "x-oss-forbid-overwrite" in signed_headers.split(";")
    assert store._client.meta.region_name == "cn-hangzhou"


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
