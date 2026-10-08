"""Strict schemas and shared canonical vectors for benchmark audit documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest
from polycodebench_core.benchmark_audit_documents import (
    _DOCUMENT_MODELS,
    DecimalMeasurement,
    TimestampEvidence,
    audit_document_bytes,
    audit_document_digest,
    parse_audit_document,
    validate_audit_transition,
)
from pydantic import ValidationError

FIXTURE = Path(__file__).parent / "fixtures" / "contracts" / "benchmark-audit-vectors.json"


def _document_payload() -> dict[str, Any]:
    return {
        "registry_ref": {
            "entity_id": "22222222-2222-4222-8222-222222222222",
            "entity_kind": "benchmark_registry",
            "digest": None,
        },
        "version": "v1",
        "split": "test",
        "membership": [],
        "components": [],
        "upstream_rights": [],
        "importer_refs": [],
    }


def _document() -> dict[str, Any]:
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "kind": "benchmark_snapshot",
        "schema_version": 1,
        "payload": _document_payload(),
        "metadata": {
            "created_at": "2026-10-08T12:00:00Z",
            "timestamp_precision": "second",
            "actor": "catalog-importer",
            "trace_id": None,
            "row_version": 0,
        },
    }


def test_all_registered_kinds_have_separate_strict_payload_models() -> None:
    assert len(_DOCUMENT_MODELS) == 31
    assert set(_DOCUMENT_MODELS) == {
        "benchmark_snapshot",
        "task_fingerprint",
        "corpus_snapshot",
        "audit_plan",
        "query_manifest",
        "coverage_manifest",
        "match_evidence",
        "model_context",
        "risk_policy",
        "risk_assessment",
        "temporal_assessment",
        "sealed_manifest",
        "canary_policy",
        "seal_access_event",
        "canary_observation",
        "behavioral_audit_plan",
        "behavioral_method_registry",
        "behavioral_task_validity",
        "behavioral_observation",
        "behavioral_assessment",
        "firewall_policy",
        "firewall_scope",
        "firewall_decision",
        "replacement_source_metadata",
        "replacement_plan",
        "replacement_validation",
        "derived_benchmark_manifest",
        "monitor_policy",
        "monitor_alert",
        "benchmark_health",
        "audit_attestation",
    }
    for kind, model in _DOCUMENT_MODELS.items():
        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "kind": kind,
                    "schema_version": 1,
                    "payload": {},
                    "metadata": {
                        "created_at": "2026-10-08T12:00:00Z",
                        "timestamp_precision": "second",
                        "actor": "test",
                        "trace_id": None,
                        "row_version": 0,
                    },
                }
            )


def test_python_matches_shared_canonical_audit_vector_and_digest() -> None:
    vectors = json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]
    assert {vector["kind"] for vector in vectors} == set(_DOCUMENT_MODELS)
    for index, vector in enumerate(vectors, start=1):
        document_value = {
            "id": f"{index:08x}-1111-4111-8111-111111111111",
            "kind": vector["kind"],
            "schema_version": vector["schema_version"],
            "payload": vector["payload"],
            "metadata": {
                "created_at": "2026-10-08T12:00:00Z",
                "timestamp_precision": "second",
                "actor": "shared-vector-test",
                "trace_id": None,
                "row_version": 0,
            },
        }
        document = parse_audit_document(json.dumps(document_value).encode("utf-8"))
        assert document.kind == vector["kind"]
        assert audit_document_bytes(document).decode("utf-8") == vector["expected_canonical_utf8"]
        assert audit_document_digest(document) == vector["expected_digest"]


def test_row_identity_and_operational_metadata_do_not_change_semantic_digest() -> None:
    first = parse_audit_document(json.dumps(_document()).encode("utf-8"))
    second_value = _document()
    second_value["id"] = "33333333-3333-4333-8333-333333333333"
    second_value["metadata"] = {
        "created_at": "2026-10-08T12:00:00.123Z",
        "timestamp_precision": "millisecond",
        "actor": "different-worker",
        "trace_id": "44444444-4444-4444-8444-444444444444",
        "row_version": 19,
    }
    second = parse_audit_document(json.dumps(second_value).encode("utf-8"))
    assert audit_document_digest(first) == audit_document_digest(second)


@pytest.mark.parametrize(
    ("timestamp", "precision"),
    [
        ("2026-10-08", "day"),
        ("2026-10-08T12:00:00Z", "second"),
        ("2026-10-08T12:00:00.123Z", "millisecond"),
        ("2026-10-08T12:00:00.123456Z", "microsecond"),
        ("2026-10-08T12:00:00.123456789Z", "nanosecond"),
    ],
)
def test_source_timestamps_preserve_declared_precision(
    timestamp: str,
    precision: Literal["day", "second", "millisecond", "microsecond", "nanosecond"],
) -> None:
    evidence = TimestampEvidence(
        value=timestamp, precision=precision, uncertainty=None, source_ref=None
    )
    assert evidence.value == timestamp


def test_date_only_timestamp_cannot_be_silently_promoted_to_midnight() -> None:
    with pytest.raises(ValidationError):
        TimestampEvidence(
            value="2026-10-08T00:00:00Z", precision="day", uncertainty=None, source_ref=None
        )
    with pytest.raises(ValidationError):
        TimestampEvidence(
            value="2026-10-08T12:00:00.123Z",
            precision="second",
            uncertainty=None,
            source_ref=None,
        )


def test_nullable_decimal_requires_explicit_reason_and_rejects_float() -> None:
    assert DecimalMeasurement(value=None, null_reason="not_run").value is None
    assert DecimalMeasurement(value="0.000000", null_reason=None).value == "0.000000"
    with pytest.raises(ValidationError):
        DecimalMeasurement(value=None, null_reason=None)
    with pytest.raises(ValidationError):
        DecimalMeasurement.model_validate({"value": 0.25, "null_reason": None})


def test_unknown_fields_duplicate_keys_and_float_json_are_rejected() -> None:
    document = _document()
    payload = dict(document["payload"])
    payload["unexpected"] = True
    document["payload"] = payload
    with pytest.raises(ValueError, match="invalid benchmark_snapshot"):
        parse_audit_document(json.dumps(document))
    with pytest.raises(ValueError, match="duplicate JSON"):
        parse_audit_document('{"kind":"benchmark_snapshot","kind":"risk_policy"}')
    with pytest.raises(ValueError, match="floating-point"):
        parse_audit_document(
            json.dumps(
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "kind": "audit_plan",
                    "schema_version": 1,
                    "payload": {
                        "benchmark_ref": {
                            "document_id": "22222222-2222-4222-8222-222222222222",
                            "digest": "sha256:" + "a" * 64,
                            "kind": "benchmark_snapshot",
                        },
                        "task_refs": [],
                        "sample_design": {"fraction": 0.5},
                        "model_context": None,
                        "source_plan": [],
                        "methods": ["finite_scan"],
                        "policy": {
                            "document_id": "33333333-3333-4333-8333-333333333333",
                            "digest": "sha256:" + "b" * 64,
                            "kind": "risk_policy",
                        },
                        "limits": {"requests": 1},
                        "visibility": "private",
                        "seed": "7",
                    },
                    "metadata": {
                        "created_at": "2026-10-08T12:00:00Z",
                        "timestamp_precision": "second",
                        "actor": "test",
                        "trace_id": None,
                        "row_version": 0,
                    },
                }
            )
        )


def test_run_and_query_state_machines_do_not_confuse_shared_blocked_state() -> None:
    validate_audit_transition("run", "blocked", "scanning")
    validate_audit_transition("query", "blocked", "queued")
    with pytest.raises(ValueError, match="blocked -> scanning"):
        validate_audit_transition("query", "blocked", "scanning")
    with pytest.raises(ValueError, match="truncated -> complete"):
        validate_audit_transition("query", "truncated", "complete")
