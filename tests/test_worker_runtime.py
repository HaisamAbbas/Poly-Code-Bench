from __future__ import annotations

import json
from pathlib import Path

import pytest
from polycodebench_orchestration.solve.worker_runtime import (
    SolveWorkerResourceSpec,
    load_image_allowlist,
)
from polycodebench_orchestration.worker_cli import main as worker_main
from pydantic import ValidationError

IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"


def _resource_spec(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "kind": "resource_spec",
        "resource_class": "local-fixture-small",
        "lane": "solve",
        "image": IMAGE,
        "image_digest": DIGEST,
        "cpu_millis": 1000,
        "memory_bytes": 512 * 1024**2,
        "disk_bytes": 128 * 1024**2,
        "pids_limit": 128,
        "timeout_seconds": 600,
        "ttl_seconds": 900,
        "executable_workspace": False,
    }
    document.update(changes)
    return document


def test_worker_resource_spec_binds_class_limits_and_image_digest() -> None:
    resource = SolveWorkerResourceSpec.model_validate(_resource_spec(), strict=True)
    assert resource.resource_class == "local-fixture-small"
    assert resource.image_digest == DIGEST
    with pytest.raises(ValidationError):
        SolveWorkerResourceSpec.model_validate(
            _resource_spec(image_digest="sha256:" + "0" * 64), strict=True
        )
    with pytest.raises(ValidationError):
        SolveWorkerResourceSpec.model_validate(
            _resource_spec(image=IMAGE.replace("@sha256:", "@sha256:untrusted")), strict=True
        )
    with pytest.raises(ValidationError):
        SolveWorkerResourceSpec.model_validate(_resource_spec(disk_bytes=512 * 1024**2 + 1))


def test_local_image_allowlist_rejects_floating_or_mismatched_images(tmp_path: Path) -> None:
    assert load_image_allowlist(Path("config/worker/local-image-allowlist.json")) == {IMAGE: DIGEST}
    invalid = tmp_path / "images.json"
    invalid.write_text(json.dumps({"python:latest": DIGEST}), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist contains"):
        load_image_allowlist(invalid)
    invalid.write_text(json.dumps({IMAGE: "sha256:" + "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist contains"):
        load_image_allowlist(invalid)
    malformed = IMAGE.replace("@sha256:", "@sha256:untrusted")
    invalid.write_text(json.dumps({malformed: DIGEST}), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist contains"):
        load_image_allowlist(invalid)


def test_worker_commands_are_inert_without_the_local_opt_in_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "dev")
    monkeypatch.delenv("PCB_LOCAL_WORKER_SETUP_ENABLED", raising=False)
    monkeypatch.delenv("PCB_WORKER_DISPATCH_ENABLED", raising=False)

    assert worker_main(["local-register"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
    assert worker_main(["local-run"]) == 2
    assert "worker configuration or operation failed" in capsys.readouterr().err
