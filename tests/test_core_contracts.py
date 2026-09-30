from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/core/src"))

from polycodebench_core.canonical import (  # noqa: E402
    canonical_document_bytes,
    canonical_envelope_bytes,
    canonical_json_bytes,
    parse_json_strict,
    sha256_bytes,
)
from polycodebench_core.errors import (  # noqa: E402
    DuplicateKeyError,
    ForbiddenCoercionError,
    InvalidCanonicalValueError,
    PolyCodeBenchContractError,
    RangeViolationError,
    UnknownFieldError,
    UnsafePathError,
)
from polycodebench_core.identity import (  # noqa: E402
    MonotonicTimer,
    bundle_digest,
    derive_sample_seed,
    file_manifest,
    new_entity_id,
    utc_timestamp,
    validate_relative_path,
)
from polycodebench_core.models import (  # noqa: E402
    BudgetProfile,
    BundleFile,
    Candidate,
    ContractGraph,
    RunConfig,
    TaskVersion,
)
from polycodebench_core.validation import parse_document, validate_document  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/contracts"
GOLDEN = json.loads((FIXTURES / "canonical-vectors.json").read_text(encoding="utf-8"))
INVALID = json.loads((FIXTURES / "invalid-vectors.json").read_text(encoding="utf-8"))
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def run_config(**changes: object) -> RunConfig:
    value: dict[str, object] = {
        "schema_version": 1,
        "kind": "run_config",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "task_set_digest": DIGEST_A,
        "model_config_digest": DIGEST_B,
        "harness_digest": DIGEST_A,
        "protocol_id": "standard-agent-v1",
        "sampling": {
            "schema_version": 1,
            "kind": "sampling_config",
            "samples_per_task": 3,
            "master_seed": "18446744073709551615",
            "temperature": "0.000000",
            "provider_seed_policy": "pass_if_supported",
        },
        "budget_profile": "agent-small-v1",
        "evaluation_policy_digest": DIGEST_B,
        "hardware_class": "cpu-perf-x86-v1",
        "judge_panel_digest": None,
        "split": "public_scored",
    }
    value.update(changes)
    return RunConfig.model_validate(value)


def task_value() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "task_version",
        "task_id": "python-stream-parser-001",
        "version": 1,
        "track": "B",
        "family": "codegen",
        "primary_language": "python",
        "secondary_languages": ["rust"],
        "source": {
            "schema_version": 1,
            "kind": "task_source",
            "source_kind": "authored",
            "immutable_revision": "git:0123456789abcdef",
            "issue_or_cve": None,
            "rights_record_id": "rights-2026-001",
            "first_public_at": None,
            "curated_at": "2026-09-30T12:00:00Z",
            "date_confidence": "verified",
        },
        "cluster_id": "parser-cluster-01",
        "difficulty": "medium",
        "stratum_id": "python-codegen-medium",
        "visible_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": "11111111-1111-4111-8111-111111111112",
            "digest": DIGEST_A,
            "visibility": "public",
        },
        "hidden_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": "11111111-1111-4111-8111-111111111113",
            "digest": DIGEST_B,
            "visibility": "hidden",
        },
        "output_contract_digest": DIGEST_A,
        "runtime": {
            "schema_version": 1,
            "kind": "task_runtime",
            "image_digest": DIGEST_A,
            "language_plugin_id": "python",
            "language_plugin_version": "1.0.0",
            "build_recipe_digest": DIGEST_A,
            "test_recipe_digest": DIGEST_B,
            "resource_class": "cpu-small-v1",
        },
        "acceptance": {
            "schema_version": 1,
            "kind": "task_acceptance",
            "required_test_group_ids": ["functional-required"],
            "hard_condition_ids": [],
            "required_outputs": ["solution.py"],
            "protected_paths": ["tests/hidden"],
        },
        "quality_plan": {
            "schema_version": 1,
            "kind": "task_quality_plan",
            "applicable_dimensions": ["security", "robustness"],
            "evidence_owners": {"python.security.v1": "security"},
            "required_analyzers": ["python-lint-v1"],
            "performance_policy_id": None,
            "judge_policy_id": None,
        },
        "oracle": {
            "schema_version": 1,
            "kind": "task_oracle",
            "test_version": "parser-tests-v1",
            "ground_truth_version": None,
            "reference_version": "parser-reference-v1",
        },
        "protocol_constraints": {
            "schema_version": 1,
            "kind": "protocol_constraints",
            "protocol_id": "standard-agent-v1",
            "allowed_tools": ["read_file", "run_public_tests"],
            "public_test_feedback": True,
            "hidden_feedback": False,
            "network_policy": "disabled",
            "dependency_inventory_digest": DIGEST_A,
            "maximum_model_turns": 30,
            "maximum_tool_calls": 100,
            "maximum_wall_seconds": 600,
        },
        "admission_report": {
            "schema_version": 1,
            "kind": "admission_report",
            "reference_check": "pass",
            "faulty_check": "pass",
            "alternative_check": "pass",
            "flakiness_check": "pass",
            "reviewer_id": "reviewer-01",
            "reviewed_at": "2026-09-30T12:05:00Z",
        },
    }


def test_canonical_golden_vectors_match_exact_bytes_and_digest() -> None:
    for vector in GOLDEN["vectors"]:
        encoded = canonical_envelope_bytes(
            vector["kind"], vector["payload"], vector["schema_version"]
        )
        reordered = canonical_envelope_bytes(
            vector["kind"], vector["reordered_payload"], vector["schema_version"]
        )
        assert encoded.decode("utf-8") == vector["expected_canonical_utf8"]
        assert encoded == reordered
        assert sha256_bytes(encoded) == vector["expected_digest"]
        changed = dict(vector["payload"])
        changed["semantic_change"] = True
        assert canonical_envelope_bytes(vector["kind"], changed) != encoded


def test_deterministic_property_checks_for_nested_key_order() -> None:
    rng = random.Random(20_260_902)
    for index in range(256):
        keys = [f"field_{number:02d}" for number in range(8)]
        values = {
            key: [index, rng.randrange(0, 100_000), index % 2 == 0, "naïve 🚀"] for key in keys
        }
        shuffled = list(keys)
        rng.shuffle(shuffled)
        reordered = {key: values[key] for key in shuffled}
        assert canonical_json_bytes(values) == canonical_json_bytes(reordered)


def test_strict_json_invalid_fixtures_reject_in_both_parser_paths() -> None:
    for vector in INVALID["json"]:
        with pytest.raises(PolyCodeBenchContractError) as raised:
            parse_json_strict(vector["raw"])
        assert raised.value.code.value == vector["error_code"]
    for vector in INVALID["byte_inputs"]:
        with pytest.raises(PolyCodeBenchContractError) as raised:
            parse_json_strict(bytes.fromhex(vector["hex"]))
        assert raised.value.code.value == vector["error_code"]


def test_strict_value_validation_and_typed_model_errors() -> None:
    with pytest.raises(InvalidCanonicalValueError):
        canonical_json_bytes({"fraction": 1.5})
    with pytest.raises(InvalidCanonicalValueError):
        canonical_json_bytes({"large": 9_007_199_254_740_992})
    with pytest.raises(InvalidCanonicalValueError):
        canonical_json_bytes({"é": "non-ASCII key"})
    with pytest.raises(UnknownFieldError):
        validate_document(RunConfig, {**run_config().model_dump(), "surprise": True})
    bad_coercion = run_config().model_dump(mode="json")
    bad_coercion["sampling"]["samples_per_task"] = "3"  # type: ignore[index]
    with pytest.raises(ForbiddenCoercionError):
        validate_document(RunConfig, bad_coercion)
    bad_range = run_config().model_dump(mode="json")
    bad_range["sampling"]["samples_per_task"] = 0  # type: ignore[index]
    with pytest.raises(RangeViolationError):
        validate_document(RunConfig, bad_range)
    with pytest.raises(ValidationError):
        RunConfig.model_validate({**run_config().model_dump(), "schema_version": 2})


def test_document_parser_rejects_duplicates_before_model_validation() -> None:
    sample = json.dumps(run_config().model_dump(mode="json"), separators=(",", ":"))
    raw = sample[:-1] + ',"schema_version":1}'
    with pytest.raises(DuplicateKeyError):
        parse_document(RunConfig, raw)


def test_task_contract_rejects_unknown_paths_and_invalid_relations() -> None:
    task = parse_document(TaskVersion, json.dumps(task_value(), ensure_ascii=False))
    assert task.task_id == "python-stream-parser-001"
    unsafe = task_value()
    unsafe["acceptance"]["required_outputs"] = ["../escape"]  # type: ignore[index]
    with pytest.raises(UnsafePathError):
        validate_document(TaskVersion, unsafe)
    duplicate_language = task_value()
    duplicate_language["secondary_languages"] = ["rust", "rust"]
    with pytest.raises(PolyCodeBenchContractError):
        validate_document(TaskVersion, duplicate_language)
    same_bundle = task_value()
    same_bundle["hidden_bundle"] = same_bundle["visible_bundle"]
    with pytest.raises(PolyCodeBenchContractError):
        validate_document(TaskVersion, same_bundle)


def test_contract_graph_rejects_unresolved_run_and_artifact_references() -> None:
    candidate = Candidate.model_validate(
        {
            "schema_version": 1,
            "kind": "candidate",
            "candidate_id": "11111111-1111-4111-8111-111111111114",
            "run_id": "11111111-1111-4111-8111-111111111115",
            "task_id": "missing-task",
            "task_version": 1,
            "sample_index": 0,
            "submission_kind": "answer",
            "payload_digest": DIGEST_A,
            "artifact_ids": [],
            "frozen_at": None,
        }
    )
    with pytest.raises(ValidationError):
        ContractGraph(candidates=(candidate,))


def test_seed_money_uuid_timestamp_and_monotonic_boundaries() -> None:
    seed_case = GOLDEN["seed_cases"][0]
    assert (
        derive_sample_seed(
            seed_case["master_seed"],
            seed_case["task_version_digest"],
            seed_case["sample_index"],
        )
        == seed_case["expected_seed"]
    )
    for seed in INVALID["invalid_seed_strings"]:
        with pytest.raises(ValueError):
            derive_sample_seed(seed, DIGEST_A, 0)
    for money in INVALID["invalid_money_strings"]:
        with pytest.raises(ValidationError):
            BudgetProfile.model_validate(
                {
                    "schema_version": 1,
                    "kind": "budget_profile",
                    "budget_profile_id": "test-budget",
                    "max_cost_usd_micros": money,
                    "max_input_tokens": 0,
                    "max_output_tokens": 0,
                }
            )
    assert UUID(new_entity_id()).version == 4
    assert utc_timestamp().endswith("Z")
    with pytest.raises(ValueError):
        utc_timestamp(__import__("datetime").datetime(2026, 1, 1))
    timer = MonotonicTimer(100)
    assert timer.elapsed_ns(125) == 25
    with pytest.raises(ValueError):
        timer.elapsed_ns(99)


def test_bundle_manifest_is_path_safe_sorted_and_stable() -> None:
    entries = [
        BundleFile(schema_version=1, kind="bundle_file", **value)
        for value in GOLDEN["bundle_files"]
    ]
    manifest = file_manifest(entries)
    assert [item.path for item in manifest.files] == sorted(
        (item.path for item in entries), key=lambda path: path.encode("utf-8")
    )
    assert bundle_digest(manifest) == GOLDEN["expected_bundle_digest"]
    with pytest.raises(ValueError, match="unique"):
        file_manifest([entries[0], entries[0]])
    for path in INVALID["unsafe_paths"]:
        with pytest.raises(UnsafePathError):
            validate_relative_path(path)


def test_source_bytes_are_hashed_without_line_ending_normalization() -> None:
    lf = b"line one\nline two\n"
    crlf = b"line one\r\nline two\r\n"
    assert sha256_bytes(lf) != sha256_bytes(crlf)


def test_canonical_model_digest_omits_nonsemantic_identity_fields() -> None:
    first = run_config()
    second = run_config(run_id="11111111-1111-4111-8111-111111111199")
    assert canonical_document_bytes(first) == canonical_document_bytes(second)
