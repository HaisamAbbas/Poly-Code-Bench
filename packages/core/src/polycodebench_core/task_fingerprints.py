"""Strict contracts for private, deterministic task-component fingerprints."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polycodebench_core.canonical import canonical_digest, canonical_json_bytes
from polycodebench_core.models import Digest

FingerprintFeatureKind = Literal["exact_bytes", "normalized_text", "token_shingles"]
FingerprintCapabilityState = Literal["available", "blocked", "unsupported"]


class FingerprintConfig(BaseModel):
    """Versioned configuration for the local exact and literal-preserving lexical view."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    algorithm_version: Literal["task-fingerprint-local-v1"] = "task-fingerprint-local-v1"
    normalization_version: Literal["text-nfc-lf-preserve-v1"] = "text-nfc-lf-preserve-v1"
    tokenizer_version: Literal["unicode-literal-tokenizer-v1"] = "unicode-literal-tokenizer-v1"
    shingle_version: Literal["sha256-token-shingles-v1"] = "sha256-token-shingles-v1"
    shingle_size: int = Field(default=8, ge=3, le=64)
    max_component_bytes: int = Field(default=2_097_152, ge=1, le=16_777_216)
    max_tokens: int = Field(default=250_000, ge=1, le=1_000_000)
    unicode_database_version: str = Field(
        default_factory=lambda: unicodedata.unidata_version, min_length=1, max_length=16
    )

    @model_validator(mode="after")
    def validate_unicode_version(self) -> FingerprintConfig:
        if not self.unicode_database_version.isascii():
            raise ValueError("Unicode database version must be ASCII")
        if self.unicode_database_version != unicodedata.unidata_version:
            raise ValueError("Unicode database version must match the active extractor runtime")
        return self


class FingerprintCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: FingerprintCapabilityState
    reason: str | None

    @model_validator(mode="after")
    def state_reason_pair(self) -> FingerprintCapability:
        if self.state == "available" and self.reason is not None:
            raise ValueError("available fingerprint capabilities cannot have a blocker reason")
        if self.state != "available" and not self.reason:
            raise ValueError("unavailable fingerprint capabilities require an explicit reason")
        return self


class PrivateFingerprintFeature(BaseModel):
    """Hash-only feature payload that must be uploaded as a non-public artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    component_id: UUID
    component_digest: Digest
    config: FingerprintConfig
    config_digest: Digest
    feature_kind: FingerprintFeatureKind
    method_version: str = Field(min_length=1, max_length=255)
    feature_digest: Digest
    payload: bytes
    payload_digest: Digest
    visibility: Literal["private"] = "private"

    @model_validator(mode="after")
    def payload_digest_matches(self) -> PrivateFingerprintFeature:
        if canonical_digest(self.config.model_dump(mode="json")) != self.config_digest:
            raise ValueError("private fingerprint config digest does not match its values")
        actual = "sha256:" + hashlib.sha256(self.payload).hexdigest()
        if actual != self.payload_digest:
            raise ValueError("private fingerprint payload digest does not match exact bytes")
        try:
            value = json.loads(self.payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("private fingerprint payload must be canonical JSON") from None
        if not isinstance(value, dict) or canonical_json_bytes(value) != self.payload:
            raise ValueError("private fingerprint payload must use canonical JSON bytes")
        expected_keys = {
            "schema_version",
            "component_id",
            "component_digest",
            "config",
            "config_digest",
            "feature_kind",
            "method_version",
            "feature_digest",
            "feature_value",
        }
        if set(value) != expected_keys or value["schema_version"] != 1:
            raise ValueError("private fingerprint payload contains unknown or missing fields")
        if (
            value["component_id"] != str(self.component_id)
            or value["component_digest"] != self.component_digest
            or value["config"] != self.config.model_dump(mode="json")
            or value["config_digest"] != self.config_digest
            or value["feature_kind"] != self.feature_kind
            or value["method_version"] != self.method_version
            or value["feature_digest"] != self.feature_digest
        ):
            raise ValueError("private fingerprint payload metadata does not match its contract")
        feature_value = value["feature_value"]
        if not isinstance(feature_value, dict):
            raise ValueError("private fingerprint feature value must be an object")
        if self.feature_kind in {"exact_bytes", "normalized_text"}:
            if set(feature_value) != {"digest"} or feature_value["digest"] != self.feature_digest:
                raise ValueError("digest-only private feature contains unexpected fields")
        elif set(feature_value) != {"token_count", "shingle_digests"}:
            raise ValueError("lexical private feature contains unexpected fields")
        elif (
            type(feature_value["token_count"]) is not int
            or feature_value["token_count"] < 1
            or not isinstance(feature_value["shingle_digests"], list)
            or any(
                not isinstance(item, str) or not item.startswith("sha256:")
                for item in feature_value["shingle_digests"]
            )
        ):
            raise ValueError("lexical private feature values are malformed")
        return self


class ComponentFingerprint(BaseModel):
    """Per-component results. Feature values contain hashes only, never source text or tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    component_id: UUID
    component_digest: Digest
    media_type: str = Field(min_length=1, max_length=128)
    config: FingerprintConfig
    config_digest: Digest
    exact_state: FingerprintCapability
    exact_digest: Digest | None
    normalization_state: FingerprintCapability
    normalized_digest: Digest | None
    lexical_state: FingerprintCapability
    token_count: int | None = Field(default=None, ge=0)
    shingle_digests: tuple[Digest, ...] = ()
    code_structure_state: FingerprintCapability
    semantic_state: FingerprintCapability
    entity_state: FingerprintCapability
    private_features: tuple[PrivateFingerprintFeature, ...]

    @model_validator(mode="after")
    def validate_feature_shape(self) -> ComponentFingerprint:
        if canonical_digest(self.config.model_dump(mode="json")) != self.config_digest:
            raise ValueError("fingerprint configuration digest does not match its values")
        if (self.exact_state.state == "available") != (self.exact_digest is not None):
            raise ValueError("exact digest must be present exactly when exact hashing is available")
        if self.exact_digest is not None and self.exact_digest != self.component_digest:
            raise ValueError("exact fingerprint digest must match the immutable component digest")
        if (self.normalization_state.state == "available") != (self.normalized_digest is not None):
            raise ValueError("normalized digest must match normalization capability state")
        if (self.lexical_state.state == "available") != (self.token_count is not None):
            raise ValueError("token count must match lexical capability state")
        if self.token_count is not None and self.token_count > self.config.max_tokens:
            raise ValueError("lexical token count exceeds its pinned feature bound")
        if self.lexical_state.state != "available" and self.shingle_digests:
            raise ValueError("unavailable lexical features cannot contain shingles")
        if self.lexical_state.state == "available":
            if self.token_count is None or not self.shingle_digests:
                raise ValueError("available lexical features require bounded non-empty shingles")
            if len(self.shingle_digests) > self.token_count:
                raise ValueError("lexical shingle count exceeds its token count")
        if tuple(sorted(set(self.shingle_digests))) != self.shingle_digests:
            raise ValueError("shingle digests must be unique and sorted")
        kinds = tuple(feature.feature_kind for feature in self.private_features)
        if len(kinds) != len(set(kinds)):
            raise ValueError("private fingerprint feature kinds must be unique")
        if any(
            feature.component_id != self.component_id
            or feature.component_digest != self.component_digest
            or feature.config != self.config
            or feature.config_digest != self.config_digest
            or feature.method_version != self.method_version
            for feature in self.private_features
        ):
            raise ValueError("private feature payload is bound to a different component/config")
        expected_digests: dict[FingerprintFeatureKind, str] = {}
        if self.exact_digest is not None:
            expected_digests["exact_bytes"] = self.exact_digest
        if self.normalized_digest is not None:
            expected_digests["normalized_text"] = self.normalized_digest
        if self.lexical_state.state == "available":
            expected_digests["token_shingles"] = canonical_digest(
                {
                    "shingle_version": self.config.shingle_version,
                    "shingle_size": self.config.shingle_size,
                    "token_count": self.token_count,
                    "shingle_digests": list(self.shingle_digests),
                }
            )
        actual_digests = {
            feature.feature_kind: feature.feature_digest for feature in self.private_features
        }
        if actual_digests != expected_digests:
            raise ValueError("private fingerprint artifacts do not match the available features")
        for feature in self.private_features:
            payload = json.loads(feature.payload.decode("utf-8"))
            value = payload["feature_value"]
            if feature.feature_kind == "token_shingles" and value != {
                "token_count": self.token_count,
                "shingle_digests": list(self.shingle_digests),
            }:
                raise ValueError("private lexical artifact does not match the result features")
        return self

    @property
    def method_version(self) -> str:
        return f"{self.config.algorithm_version}:{self.config_digest}:{self.media_type}"


class PrivateFingerprintArtifactBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    feature_kind: FingerprintFeatureKind
    artifact_id: UUID
    artifact_digest: Digest
    visibility: Literal["private"] = "private"


class PrivateFingerprintArtifactBindings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifacts: tuple[PrivateFingerprintArtifactBinding, ...]

    @model_validator(mode="after")
    def unique_feature_artifacts(self) -> PrivateFingerprintArtifactBindings:
        kinds = [artifact.feature_kind for artifact in self.artifacts]
        ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(kinds) != len(set(kinds)) or len(ids) != len(set(ids)):
            raise ValueError("private fingerprint artifact bindings must be unique")
        return self
