from __future__ import annotations

import pytest
from botocore.config import Config
from polycodebench_persistence import object_store
from polycodebench_persistence.object_store import S3ArtifactStore, object_store_addressing_style


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
