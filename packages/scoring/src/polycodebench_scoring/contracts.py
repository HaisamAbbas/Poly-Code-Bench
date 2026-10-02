"""Base types for the scorer-owned documents.

The scorer consumes three frozen things: a task, a policy and a manifest of validated evidence. All
three, and everything the scorer produces, are strict pydantic models so that one canonical
serializer produces the bytes that are hashed, compared and archived.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from polycodebench_core.models import ContractModel

# Every scorer document is versioned; the *score* schema version is carried separately by the
# policy so a version change is a policy decision rather than a silent field addition.
SCORE_SCHEMA_VERSION = 1


class ScoringModel(ContractModel):
    """Strict, frozen, extras-rejecting base for every scorer document."""

    model_config = ContractModel.model_config
    kind: str
    schema_version: Literal[1] = 1
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset()

    def content_digest(self) -> str:
        """Canonical digest of this document's semantics (non-semantic fields excluded)."""
        from polycodebench_core.canonical import canonical_document_digest

        return canonical_document_digest(self)
