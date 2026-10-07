"""Candidate artifact integrity and path validation before grading."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from polycodebench_core.models import TaskOutputContract
from polycodebench_orchestration.grading.candidates import (
    CandidateArtifactRejected,
    decode_file_candidate,
)


def _contract(**overrides: object) -> TaskOutputContract:
    value: dict[str, object] = {
        "kind": "task_output_contract",
        "schema_version": 1,
        "submission_kind": "files",
        "allowed_paths": ["src"],
        "maximum_artifact_bytes": 100,
        "maximum_file_bytes": 80,
        "maximum_files": 4,
        "findings_limit": None,
    }
    value.update(overrides)
    return TaskOutputContract.model_validate(value, strict=True)


def _artifact(*, path: str = "src/main.py", data: bytes = b"print('ok')") -> bytes:
    return json.dumps(
        {
            "submission_kind": "files",
            "validity": "valid",
            "reasons": [],
            "payload": {
                "files": [
                    {
                        "path": path,
                        "size": len(data),
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                        "content_b64": base64.b64encode(data).decode("ascii"),
                    }
                ]
            },
        },
        separators=(",", ":"),
    ).encode()


def _decode(body: bytes, contract: TaskOutputContract | None = None) -> dict[str, bytes]:
    return decode_file_candidate(
        body,
        expected_artifact_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        contract=contract or _contract(),
    )


def test_valid_file_candidate_preserves_exact_bytes() -> None:
    body = _artifact(data=b"\x00source\r\n")
    assert _decode(body) == {"src/main.py": b"\x00source\r\n"}


def test_rejects_artifact_digest_mismatch_before_parsing() -> None:
    with pytest.raises(CandidateArtifactRejected, match="artifact digest"):
        decode_file_candidate(
            _artifact(), expected_artifact_digest="sha256:" + "0" * 64, contract=_contract()
        )

    with pytest.raises(CandidateArtifactRejected, match="artifact digest"):
        decode_file_candidate(_artifact(), expected_artifact_digest="0" * 64, contract=_contract())


@pytest.mark.parametrize("path", ["../outside.py", "src/../outside.py", "/absolute.py"])
def test_rejects_unsafe_candidate_paths(path: str) -> None:
    with pytest.raises(CandidateArtifactRejected, match="path"):
        _decode(_artifact(path=path))


def test_rejects_tampered_file_digest_and_declared_size() -> None:
    body = json.loads(_artifact())
    body["payload"]["files"][0]["digest"] = "0" * 64
    with pytest.raises(CandidateArtifactRejected, match="size or digest"):
        _decode(json.dumps(body, separators=(",", ":")).encode())

    body = json.loads(_artifact())
    body["payload"]["files"][0]["digest"] = hashlib.sha256(b"print('ok')").hexdigest()
    with pytest.raises(CandidateArtifactRejected, match="size or digest"):
        _decode(json.dumps(body, separators=(",", ":")).encode())

    body = json.loads(_artifact())
    body["payload"]["files"][0]["size"] += 1
    with pytest.raises(CandidateArtifactRejected, match="size or digest"):
        _decode(json.dumps(body, separators=(",", ":")).encode())


def test_rejects_invalid_extraction_and_size_overflow() -> None:
    invalid = json.loads(_artifact())
    invalid["validity"] = "contract_invalid"
    with pytest.raises(CandidateArtifactRejected, match="valid file submission"):
        _decode(json.dumps(invalid, separators=(",", ":")).encode())

    aggregate = json.loads(_artifact(data=b"x" * 30))
    second = dict(aggregate["payload"]["files"][0])
    second["path"] = "src/other.py"
    aggregate["payload"]["files"].append(second)
    with pytest.raises(CandidateArtifactRejected, match="aggregate size"):
        _decode(
            json.dumps(aggregate, separators=(",", ":")).encode(),
            _contract(maximum_artifact_bytes=40, maximum_file_bytes=40),
        )


def test_rejects_duplicate_json_keys() -> None:
    body = b'{"submission_kind":"files","submission_kind":"patch"}'
    with pytest.raises(CandidateArtifactRejected, match="strict JSON"):
        _decode(body)


def test_rejects_file_and_directory_path_collisions() -> None:
    body = json.loads(_artifact(path="src/module"))
    body["payload"]["files"].append(
        {
            "path": "src/module/main.py",
            "size": 1,
            "digest": "sha256:" + hashlib.sha256(b"x").hexdigest(),
            "content_b64": base64.b64encode(b"x").decode("ascii"),
        }
    )
    with pytest.raises(CandidateArtifactRejected, match="file ancestors"):
        _decode(json.dumps(body, separators=(",", ":")).encode())
