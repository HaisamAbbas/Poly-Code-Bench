"""Typed failures for parsing, identity and canonical contract boundaries."""

from __future__ import annotations

from polycodebench_core.models import ContractErrorCode


class PolyCodeBenchContractError(ValueError):
    code = ContractErrorCode.INVALID_DOCUMENT

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class DuplicateKeyError(PolyCodeBenchContractError):
    code = ContractErrorCode.DUPLICATE_KEY


class InvalidUtf8Error(PolyCodeBenchContractError):
    code = ContractErrorCode.INVALID_UTF8


class InvalidCanonicalValueError(PolyCodeBenchContractError):
    code = ContractErrorCode.INVALID_CANONICAL_VALUE


class UnsafePathError(PolyCodeBenchContractError):
    code = ContractErrorCode.UNSAFE_PATH


class InvalidReferenceError(PolyCodeBenchContractError):
    code = ContractErrorCode.INVALID_REFERENCE

    def __init__(self, path: str, reference: str) -> None:
        super().__init__(f"unresolved reference at {path}: {reference}", path=path)
        self.reference = reference


class UnknownFieldError(PolyCodeBenchContractError):
    """A strict document contained a field absent from its schema version."""


class ForbiddenCoercionError(PolyCodeBenchContractError):
    """A field used the wrong JSON primitive type instead of being coerced."""


class RangeViolationError(PolyCodeBenchContractError):
    code = ContractErrorCode.INVALID_RANGE


class InvalidDocumentError(PolyCodeBenchContractError):
    """A document failed a declared shape or cross-field invariant."""
