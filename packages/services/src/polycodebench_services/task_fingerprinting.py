"""Compute deterministic private fingerprints without model or parser dispatch."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from uuid import UUID

from polycodebench_core.canonical import canonical_digest, canonical_json_bytes
from polycodebench_core.task_fingerprints import (
    ComponentFingerprint,
    FingerprintCapability,
    FingerprintConfig,
    FingerprintFeatureKind,
    PrivateFingerprintFeature,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_MEDIA_TYPE = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$", re.ASCII)
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_TEXT_APPLICATION_TYPES = frozenset(
    {"application/json", "application/xml", "application/yaml", "application/x-yaml"}
)
_PYTHON_TYPES = frozenset({"text/x-python", "application/x-python"})
_JAVA_TYPES = frozenset({"text/x-java", "text/java", "application/x-java-source"})


def _available() -> FingerprintCapability:
    return FingerprintCapability(state="available", reason=None)


def _blocked(reason: str) -> FingerprintCapability:
    return FingerprintCapability(state="blocked", reason=reason)


def _unsupported(reason: str) -> FingerprintCapability:
    return FingerprintCapability(state="unsupported", reason=reason)


def _private_feature(
    *,
    component_id: UUID,
    component_digest: str,
    config: FingerprintConfig,
    config_digest: str,
    kind: FingerprintFeatureKind,
    method_version: str,
    feature_digest: str,
    feature_value: dict[str, object],
) -> PrivateFingerprintFeature:
    payload = canonical_json_bytes(
        {
            "schema_version": 1,
            "component_id": str(component_id),
            "component_digest": component_digest,
            "config": config.model_dump(mode="json"),
            "config_digest": config_digest,
            "feature_kind": kind,
            "method_version": method_version,
            "feature_digest": feature_digest,
            "feature_value": feature_value,
        }
    )
    return PrivateFingerprintFeature(
        component_id=component_id,
        component_digest=component_digest,
        config=config,
        config_digest=config_digest,
        feature_kind=kind,
        method_version=method_version,
        feature_digest=feature_digest,
        payload=payload,
        payload_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def _tokenize(text: str, limit: int) -> tuple[str, ...] | None:
    tokens: list[str] = []
    for match in _TOKEN.finditer(text):
        tokens.append(match.group())
        if len(tokens) > limit:
            return None
    return tuple(tokens)


def _shingles(tokens: tuple[str, ...], config: FingerprintConfig) -> tuple[str, ...]:
    if not tokens:
        return ()
    size = min(config.shingle_size, len(tokens))
    shingles = {
        canonical_digest(
            {
                "tokenizer_version": config.tokenizer_version,
                "tokens": list(tokens[start : start + size]),
            }
        )
        for start in range(len(tokens) - size + 1)
    }
    return tuple(sorted(shingles))


def fingerprint_component(
    *,
    component_id: UUID,
    component_digest: str,
    content: bytes,
    media_type: str,
    config: FingerprintConfig | None = None,
) -> ComponentFingerprint:
    """Create exact and literal-preserving lexical views; unsupported features remain explicit."""
    if type(content) is not bytes:
        raise TypeError("fingerprint input must be exact component bytes")
    if not isinstance(component_digest, str) or not _DIGEST.fullmatch(component_digest):
        raise ValueError("component digest must be a lowercase SHA-256 reference")
    if not isinstance(media_type, str) or not media_type.isascii():
        raise ValueError("component media type must be ASCII")
    normalized_media_type = media_type.split(";", 1)[0].strip().lower()
    if not _MEDIA_TYPE.fullmatch(normalized_media_type):
        raise ValueError("component media type is malformed")

    if config is not None and not isinstance(config, FingerprintConfig):
        raise TypeError("fingerprint config must use the immutable FingerprintConfig contract")
    frozen_config = config or FingerprintConfig()
    config_digest = canonical_digest(frozen_config.model_dump(mode="json"))
    method_version = f"{frozen_config.algorithm_version}:{config_digest}:{normalized_media_type}"
    if len(method_version) > 255:
        raise ValueError("fingerprint method version exceeds the persistence limit")

    if len(content) > frozen_config.max_component_bytes:
        blocked = _blocked("component_size_limit")
        return ComponentFingerprint(
            component_id=component_id,
            component_digest=component_digest,
            media_type=normalized_media_type,
            config=frozen_config,
            config_digest=config_digest,
            exact_state=blocked,
            exact_digest=None,
            normalization_state=blocked,
            normalized_digest=None,
            lexical_state=blocked,
            token_count=None,
            shingle_digests=(),
            code_structure_state=blocked,
            semantic_state=blocked,
            entity_state=blocked,
            private_features=(),
        )

    actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual_digest != component_digest:
        raise ValueError("fingerprint input bytes do not match the immutable component digest")

    exact = _private_feature(
        component_id=component_id,
        component_digest=component_digest,
        config=frozen_config,
        config_digest=config_digest,
        kind="exact_bytes",
        method_version=method_version,
        feature_digest=actual_digest,
        feature_value={"digest": actual_digest},
    )
    features = [exact]
    exact_state = _available()
    base_media_type = normalized_media_type
    is_text = base_media_type.startswith("text/") or base_media_type in _TEXT_APPLICATION_TYPES
    if not is_text:
        return ComponentFingerprint(
            component_id=component_id,
            component_digest=component_digest,
            media_type=normalized_media_type,
            config=frozen_config,
            config_digest=config_digest,
            exact_state=exact_state,
            exact_digest=actual_digest,
            normalization_state=_unsupported("non_text_component"),
            normalized_digest=None,
            lexical_state=_unsupported("non_text_component"),
            token_count=None,
            shingle_digests=(),
            code_structure_state=_unsupported("unsupported_modality"),
            semantic_state=_unsupported("unsupported_modality"),
            entity_state=_unsupported("unsupported_modality"),
            private_features=tuple(features),
        )

    try:
        source_text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        unsupported = _unsupported("invalid_utf8")
        return ComponentFingerprint(
            component_id=component_id,
            component_digest=component_digest,
            media_type=normalized_media_type,
            config=frozen_config,
            config_digest=config_digest,
            exact_state=exact_state,
            exact_digest=actual_digest,
            normalization_state=unsupported,
            normalized_digest=None,
            lexical_state=unsupported,
            token_count=None,
            shingle_digests=(),
            code_structure_state=unsupported,
            semantic_state=unsupported,
            entity_state=unsupported,
            private_features=tuple(features),
        )

    normalized_text = unicodedata.normalize(
        "NFC", source_text.replace("\r\n", "\n").replace("\r", "\n")
    )
    normalized_bytes = normalized_text.encode("utf-8", errors="strict")
    normalized_digest = "sha256:" + hashlib.sha256(normalized_bytes).hexdigest()
    features.append(
        _private_feature(
            component_id=component_id,
            component_digest=component_digest,
            config=frozen_config,
            config_digest=config_digest,
            kind="normalized_text",
            method_version=method_version,
            feature_digest=normalized_digest,
            feature_value={"digest": normalized_digest},
        )
    )

    tokens = _tokenize(normalized_text, frozen_config.max_tokens)
    if tokens is None:
        lexical_state = _blocked("token_limit_exceeded")
        token_count = None
        shingle_digests: tuple[str, ...] = ()
    elif not tokens:
        lexical_state = _unsupported("no_lexical_tokens")
        token_count = None
        shingle_digests = ()
    else:
        lexical_state = _available()
        token_count = len(tokens)
        shingle_digests = _shingles(tokens, frozen_config)
        shingle_digest = canonical_digest(
            {
                "shingle_version": frozen_config.shingle_version,
                "shingle_size": frozen_config.shingle_size,
                "token_count": token_count,
                "shingle_digests": list(shingle_digests),
            }
        )
        features.append(
            _private_feature(
                component_id=component_id,
                component_digest=component_digest,
                config=frozen_config,
                config_digest=config_digest,
                kind="token_shingles",
                method_version=method_version,
                feature_digest=shingle_digest,
                feature_value={
                    "token_count": token_count,
                    "shingle_digests": list(shingle_digests),
                },
            )
        )

    parserless_text_code = base_media_type in _PYTHON_TYPES | _JAVA_TYPES
    code_structure = (
        _blocked("approved_parser_config_missing")
        if parserless_text_code
        else _unsupported("unsupported_or_unidentified_language")
    )
    return ComponentFingerprint(
        component_id=component_id,
        component_digest=component_digest,
        media_type=normalized_media_type,
        config=frozen_config,
        config_digest=config_digest,
        exact_state=exact_state,
        exact_digest=actual_digest,
        normalization_state=_available(),
        normalized_digest=normalized_digest,
        lexical_state=lexical_state,
        token_count=token_count,
        shingle_digests=shingle_digests,
        code_structure_state=code_structure,
        semantic_state=_blocked("approved_embedding_config_missing"),
        entity_state=_blocked("approved_feature_extractor_missing"),
        private_features=tuple(features),
    )
