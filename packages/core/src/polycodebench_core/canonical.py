"""The pcb-json-v1 byte profile and digest helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from polycodebench_core.errors import (
    DuplicateKeyError,
    InvalidCanonicalValueError,
    InvalidUtf8Error,
    PolyCodeBenchContractError,
)

SAFE_INTEGER_MIN = -9_007_199_254_740_991
SAFE_INTEGER_MAX = 9_007_199_254_740_991
NON_SEMANTIC_FIELDS = frozenset(
    {
        "artifact_id",
        "artifact_ids",
        "candidate_id",
        "created_at",
        "database_id",
        "evidence_ids",
        "frozen_at",
        "raw_artifact_ids",
        "run_id",
        "scorecard_id",
        "signature",
        "signatures",
    }
)


def _reject_float(value: str) -> None:
    raise InvalidCanonicalValueError(f"floating-point JSON number is forbidden: {value}")


def _reject_constant(value: str) -> None:
    raise InvalidCanonicalValueError(f"non-JSON numeric constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def validate_canonical_value(value: Any, path: str = "$", *, top_level: bool = True) -> None:
    """Reject values outside the deliberately small JSON canonical domain."""
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not SAFE_INTEGER_MIN <= value <= SAFE_INTEGER_MAX:
            raise InvalidCanonicalValueError(f"JSON integer outside the safe range at {path}")
        return
    if type(value) is str:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise InvalidCanonicalValueError(f"lone Unicode surrogate at {path}")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise InvalidCanonicalValueError(f"string is not valid Unicode at {path}") from exc
        return
    if type(value) is list:
        for index, child in enumerate(value):
            validate_canonical_value(child, f"{path}[{index}]", top_level=False)
        return
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise InvalidCanonicalValueError(f"object key is not a string at {path}")
            if not key.isascii():
                raise InvalidCanonicalValueError(f"object key must be ASCII at {path}")
            validate_canonical_value(child, f"{path}.{key}", top_level=False)
        return
    raise InvalidCanonicalValueError(
        f"unsupported canonical value {type(value).__name__} at {path}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    validate_canonical_value(value)
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8", errors="strict")
    except (UnicodeEncodeError, ValueError) as exc:
        raise InvalidCanonicalValueError("value cannot be encoded as pcb-json-v1") from exc


def parse_json_strict(data: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate keys, floats and unsafe integers."""
    if isinstance(data, bytes):
        if data.startswith(b"\xef\xbb\xbf"):
            raise InvalidUtf8Error("UTF-8 BOM is forbidden")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise InvalidUtf8Error("input is not valid UTF-8") from exc
    else:
        text = data
        if text.startswith("\ufeff"):
            raise InvalidUtf8Error("UTF-8 BOM is forbidden")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except PolyCodeBenchContractError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InvalidCanonicalValueError("invalid JSON document") from exc
    validate_canonical_value(value)
    return value


def canonical_envelope(kind: str, payload: Any, schema_version: int = 1) -> dict[str, Any]:
    if not kind or not kind.isascii():
        raise InvalidCanonicalValueError("document kind must be non-empty ASCII")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise InvalidCanonicalValueError("canonical envelopes support schema versions 1 and 2")
    return {"kind": kind, "schema_version": schema_version, "payload": payload}


def canonical_envelope_bytes(kind: str, payload: Any, schema_version: int = 1) -> bytes:
    return canonical_json_bytes(canonical_envelope(kind, payload, schema_version))


def canonical_document_bytes(document: BaseModel) -> bytes:
    """Serialize a strict model in the versioned canonical envelope."""
    kind = getattr(document, "kind", None)
    schema_version = getattr(document, "schema_version", None)
    if not isinstance(kind, str) or type(schema_version) is not int:
        raise InvalidCanonicalValueError("document model requires kind and schema_version")
    excluded = {"kind", "schema_version"}
    excluded.update(getattr(type(document), "canonical_excluded_fields", frozenset()))
    payload = document.model_dump(mode="json", exclude=excluded)
    payload = _exclude_nonsemantic_fields(payload)
    return canonical_json_bytes(canonical_envelope(kind, payload, schema_version))


def _exclude_nonsemantic_fields(value: Any) -> Any:
    if type(value) is list:
        return [_exclude_nonsemantic_fields(child) for child in value]
    if type(value) is dict:
        return {
            key: _exclude_nonsemantic_fields(child)
            for key, child in value.items()
            if key not in NON_SEMANTIC_FIELDS
        }
    return value


def sha256_bytes(data: bytes) -> str:
    if type(data) is not bytes:
        raise TypeError("sha256_bytes requires exact bytes")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def canonical_document_digest(document: BaseModel) -> str:
    return sha256_bytes(canonical_document_bytes(document))
