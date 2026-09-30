"""Strict parsing helpers that translate Pydantic failures to typed contract errors."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from polycodebench_core.canonical import canonical_json_bytes, parse_json_strict
from polycodebench_core.errors import (
    ForbiddenCoercionError,
    InvalidDocumentError,
    PolyCodeBenchContractError,
    RangeViolationError,
    UnknownFieldError,
    UnsafePathError,
)

_COERCION_ERRORS = {"int_type", "bool_type", "string_type", "float_type"}
_RANGE_ERRORS = {
    "greater_than",
    "greater_than_equal",
    "less_than",
    "less_than_equal",
    "string_too_long",
    "string_too_short",
}


def validate_document[TModel: BaseModel](model_type: type[TModel], value: object) -> TModel:
    try:
        wire_bytes = canonical_json_bytes(value)
        return model_type.model_validate_json(wire_bytes, strict=True)
    except PolyCodeBenchContractError:
        raise
    except ValidationError as exc:
        first = exc.errors(include_input=False)[0]
        location = ".".join(str(item) for item in first["loc"])
        error_type = str(first["type"])
        message = str(first["msg"])
        if error_type == "extra_forbidden":
            raise UnknownFieldError(message, path=location) from exc
        if error_type in _COERCION_ERRORS:
            raise ForbiddenCoercionError(message, path=location) from exc
        if error_type in _RANGE_ERRORS:
            raise RangeViolationError(message, path=location) from exc
        if "path" in location or "required_outputs" in location or "protected_paths" in location:
            raise UnsafePathError(message, path=location) from exc
        raise InvalidDocumentError(message, path=location) from exc


def parse_document[TModel: BaseModel](model_type: type[TModel], raw: str | bytes) -> TModel:
    try:
        value = parse_json_strict(raw)
    except PolyCodeBenchContractError:
        raise
    return validate_document(model_type, value)
