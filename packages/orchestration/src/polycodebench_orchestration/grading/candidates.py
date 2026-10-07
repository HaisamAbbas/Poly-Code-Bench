"""Safe decoding of frozen file candidates for independent grading."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping

from polycodebench_core.canonical import parse_json_strict
from polycodebench_core.models import TaskOutputContract
from polycodebench_core.solve_contracts import PathForbidden, normalize_workspace_path


class CandidateArtifactRejected(ValueError):
    """A stored solve artifact is not a valid, bounded file candidate."""


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def decode_file_candidate(
    body: bytes,
    *,
    expected_artifact_digest: str,
    contract: TaskOutputContract,
) -> dict[str, bytes]:
    """Verify a frozen ``files`` extraction before exposing it to a grading plan.

    The artifact hash binds the exact stored extraction. Each decoded file has its own
    independently checked hash, path, and size. The function does not normalize or repair source
    bytes, and it never reads task-controlled overlays or configuration from the candidate.
    """
    if (
        not _SHA256.fullmatch(expected_artifact_digest)
        or "sha256:" + hashlib.sha256(body).hexdigest() != expected_artifact_digest
    ):
        raise CandidateArtifactRejected("candidate artifact digest does not match its reference")
    if len(body) > contract.maximum_artifact_bytes + 1_048_576:
        raise CandidateArtifactRejected("candidate artifact exceeds its bounded envelope size")
    try:
        document = parse_json_strict(body)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise CandidateArtifactRejected("candidate artifact is not strict JSON") from error
    if not isinstance(document, Mapping) or set(document) != {
        "submission_kind",
        "validity",
        "reasons",
        "payload",
    }:
        raise CandidateArtifactRejected("candidate artifact has an invalid extraction envelope")
    if (
        document["submission_kind"] != "files"
        or document["validity"] != "valid"
        or document["reasons"] != []
    ):
        raise CandidateArtifactRejected("candidate artifact is not a valid file submission")
    payload = document["payload"]
    if not isinstance(payload, Mapping) or set(payload) != {"files"}:
        raise CandidateArtifactRejected("candidate artifact payload is malformed")
    entries = payload["files"]
    if not isinstance(entries, list) or not entries or len(entries) > contract.maximum_files:
        raise CandidateArtifactRejected("candidate file count is empty or exceeds its limit")

    result: dict[str, bytes] = {}
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "size",
            "digest",
            "content_b64",
        }:
            raise CandidateArtifactRejected("candidate file entry is malformed")
        raw_path = entry["path"]
        if not isinstance(raw_path, str):
            raise CandidateArtifactRejected("candidate file path is malformed")
        try:
            path = normalize_workspace_path(raw_path)
        except PathForbidden as error:
            raise CandidateArtifactRejected("candidate file path is unsafe") from error
        if path != raw_path or path in result:
            raise CandidateArtifactRejected("candidate file path is noncanonical or duplicated")
        if any(
            path.startswith(existing + "/") or existing.startswith(path + "/")
            for existing in result
        ):
            raise CandidateArtifactRejected("candidate file paths cannot contain file ancestors")

        encoded = entry["content_b64"]
        if not isinstance(encoded, str) or len(encoded) > 4 * (
            (contract.maximum_file_bytes + 2) // 3
        ):
            raise CandidateArtifactRejected("candidate file content is malformed")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise CandidateArtifactRejected("candidate file content is not valid base64") from error
        digest = entry["digest"]
        if (
            not isinstance(entry["size"], int)
            or isinstance(entry["size"], bool)
            or entry["size"] != len(data)
            or len(data) > contract.maximum_file_bytes
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or "sha256:" + hashlib.sha256(data).hexdigest() != digest
        ):
            raise CandidateArtifactRejected("candidate file size or digest does not verify")
        total_bytes += len(data)
        if total_bytes > contract.maximum_artifact_bytes:
            raise CandidateArtifactRejected("candidate files exceed their aggregate size limit")
        result[path] = data
    return result


__all__ = ["CandidateArtifactRejected", "decode_file_candidate"]
