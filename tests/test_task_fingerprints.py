from __future__ import annotations

import hashlib
import json
import unicodedata
from uuid import uuid4

import pytest
from polycodebench_core.task_fingerprints import FingerprintConfig, PrivateFingerprintFeature
from polycodebench_services.task_fingerprinting import fingerprint_component
from pydantic import ValidationError


def _fingerprint(
    text: str,
    *,
    media_type: str = "text/plain",
    config: FingerprintConfig | None = None,
):
    content = text.encode("utf-8")
    return fingerprint_component(
        component_id=uuid4(),
        component_digest="sha256:" + hashlib.sha256(content).hexdigest(),
        content=content,
        media_type=media_type,
        config=config,
    )


def test_exact_normalized_and_lexical_views_are_separate_and_deterministic() -> None:
    source = "def decide(x: int) -> bool:\r\n    return x != 42\r\n"
    newline_variant = "def decide(x: int) -> bool:\n    return x != 42\n"
    changed_constraint = "def decide(x: int) -> bool:\n    return x == 42\n"

    first = _fingerprint(source, media_type="text/x-python")
    same_text = _fingerprint(newline_variant, media_type="text/x-python")
    changed = _fingerprint(changed_constraint, media_type="text/x-python")

    assert first.exact_digest != same_text.exact_digest
    assert first.normalized_digest == same_text.normalized_digest
    assert first.shingle_digests == same_text.shingle_digests
    assert first.normalized_digest != changed.normalized_digest
    assert first.shingle_digests != changed.shingle_digests
    assert first.code_structure_state.state == "blocked"
    assert first.code_structure_state.reason == "approved_parser_config_missing"
    assert first.semantic_state.reason == "approved_embedding_config_missing"
    assert first.config_digest == same_text.config_digest


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("result = value + 3", "result = value + 4"),
        ("return value >= 8", "return value > 8"),
        ("def f(x: int): return x", "def f(x: str): return x"),
        ("if not ready: stop()", "if ready: stop()"),
        ("answer in {'A', 'B'}", "answer in {'A', 'C'}"),
    ],
)
def test_conservative_normalization_keeps_literals_types_negation_and_constraints(
    left: str, right: str
) -> None:
    left_fingerprint = _fingerprint(left)
    right_fingerprint = _fingerprint(right)

    assert left_fingerprint.normalized_digest != right_fingerprint.normalized_digest
    assert left_fingerprint.shingle_digests != right_fingerprint.shingle_digests


def test_unicode_and_line_endings_normalize_without_calling_the_hash_byte_exact() -> None:
    composed = "café must remain true\r\n"
    decomposed = unicodedata.normalize("NFD", "café must remain true") + "\n"

    first = _fingerprint(composed)
    second = _fingerprint(decomposed)

    assert first.exact_digest != second.exact_digest
    assert first.normalized_digest == second.normalized_digest
    assert first.normalization_state.state == "available"


def test_identifier_renames_are_not_abstracted_as_exact_or_normalized_matches() -> None:
    original = _fingerprint("def calculate(total): return total + 1", media_type="text/x-python")
    renamed = _fingerprint("def calculate(amount): return amount + 1", media_type="text/x-python")

    assert original.exact_digest != renamed.exact_digest
    assert original.normalized_digest != renamed.normalized_digest
    assert original.shingle_digests != renamed.shingle_digests


def test_binary_content_has_exact_hash_but_explicit_unsupported_text_features() -> None:
    content = b"\x89PNG\r\n\x1a\n\x00\xff"
    result = fingerprint_component(
        component_id=uuid4(),
        component_digest="sha256:" + hashlib.sha256(content).hexdigest(),
        content=content,
        media_type="image/png",
    )

    assert result.exact_state.state == "available"
    assert result.normalization_state.state == "unsupported"
    assert result.lexical_state.state == "unsupported"
    assert result.code_structure_state.state == "unsupported"
    assert result.semantic_state.state == "unsupported"
    assert result.shingle_digests == ()
    assert tuple(feature.feature_kind for feature in result.private_features) == ("exact_bytes",)


def test_empty_text_does_not_create_a_zero_shingle_feature() -> None:
    result = _fingerprint(" \r\n\t")

    assert result.normalization_state.state == "available"
    assert result.lexical_state.state == "unsupported"
    assert result.lexical_state.reason == "no_lexical_tokens"
    assert result.token_count is None
    assert result.shingle_digests == ()
    assert {feature.feature_kind for feature in result.private_features} == {
        "exact_bytes",
        "normalized_text",
    }


def test_private_feature_artifacts_contain_hashes_not_component_text_or_tokens() -> None:
    secret_text = "Solve the private problem for value 9137, preserving NOT and int."
    result = _fingerprint(secret_text)

    assert result.private_features
    for feature in result.private_features:
        payload = json.loads(feature.payload)
        assert secret_text not in feature.payload.decode("utf-8")
        assert "Solve" not in feature.payload.decode("utf-8")
        assert payload["config"] == result.config.model_dump(mode="json")
        assert payload["config_digest"] == result.config_digest
        assert payload["feature_digest"] == feature.feature_digest
        assert feature.visibility == "private"
        assert feature.payload_digest == "sha256:" + hashlib.sha256(feature.payload).hexdigest()
    assert result.semantic_state.state == "blocked"


def test_python_and_java_parsers_are_blocked_without_approved_parser_config() -> None:
    python_result = _fingerprint("def solve(value): return value", media_type="text/x-python")
    java_result = _fingerprint(
        "class Solve { int run(int x) { return x; } }", media_type="text/x-java"
    )

    assert python_result.code_structure_state.reason == "approved_parser_config_missing"
    assert java_result.code_structure_state.reason == "approved_parser_config_missing"
    assert (
        python_result.code_structure_state.state
        == java_result.code_structure_state.state
        == "blocked"
    )


def test_oversize_and_mismatched_component_inputs_fail_closed() -> None:
    content = b"too large"
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    oversized = fingerprint_component(
        component_id=uuid4(),
        component_digest=digest,
        content=content,
        media_type="text/plain",
        config=FingerprintConfig(max_component_bytes=4),
    )
    assert oversized.exact_state.state == "blocked"
    assert oversized.exact_state.reason == "component_size_limit"
    assert oversized.private_features == ()

    with pytest.raises(ValueError, match="do not match"):
        fingerprint_component(
            component_id=uuid4(),
            component_digest="sha256:" + "0" * 64,
            content=content,
            media_type="text/plain",
        )


def test_private_feature_contract_rejects_unrecognized_payload_fields() -> None:
    feature = _fingerprint("private fixture value").private_features[0]
    body = json.loads(feature.payload)
    body["feature_value"]["source_text"] = "private"
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    invalid = feature.model_dump()
    invalid["payload"] = payload
    invalid["payload_digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()

    with pytest.raises(ValidationError, match="unexpected fields"):
        PrivateFingerprintFeature.model_validate(invalid)


def test_configuration_digest_changes_when_extractor_settings_change() -> None:
    content = b"A candidate lexical problem has enough words to index."
    source_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    component_id = uuid4()
    default = fingerprint_component(
        component_id=component_id,
        component_digest=source_digest,
        content=content,
        media_type="text/plain",
        config=FingerprintConfig(shingle_size=8),
    )
    changed = fingerprint_component(
        component_id=component_id,
        component_digest=source_digest,
        content=content,
        media_type="text/plain",
        config=FingerprintConfig(shingle_size=9),
    )

    assert default.config_digest != changed.config_digest
    assert default.method_version != changed.method_version
    assert default.exact_digest == changed.exact_digest


def test_unicode_database_config_cannot_claim_a_different_runtime() -> None:
    with pytest.raises(ValidationError, match="active extractor runtime"):
        FingerprintConfig(unicode_database_version="0.0")
