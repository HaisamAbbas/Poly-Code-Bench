"""Produce the Prompt 28 E2E-38 evidence document from a real run.

Runs every prediction candidate through the deterministic grader, grades both prediction families
under their frozen normalization, and records the Track B coverage audit verdict as it stands in the
repository. Nothing here is written by hand: the document is the output of the run.

    uv run python plugins/suites/swebench/scripts/prompt28_evidence.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "plugins" / "suites" / "swebench" / "src"))
sys.path.insert(0, str(ROOT / "plugins" / "suites" / "swebench" / "tests"))

import output_prediction_instance as output_fixture  # noqa: E402
import test_prediction_instance as test_fixture  # noqa: E402
from polycodebench_suites_swebench.prediction import (  # noqa: E402
    NormalizationRules,
    PredictionContractError,
    PredictionOracle,
    parse_submission,
    prediction_metric_definitions,
    validate_prediction_tools,
)
from polycodebench_suites_swebench.prediction_grading import (  # noqa: E402
    audit_track_b,
    grade_prediction_task,
)

REGISTER = yaml.safe_load(
    (ROOT / "config" / "methodology" / "deviations-v1.yaml").read_text(encoding="utf-8")
)


def build_oracle(document: Mapping[str, Any]) -> PredictionOracle:
    rules = NormalizationRules.model_validate(
        {**document["normalization"], "kind": "prediction_normalization"}
    )
    return PredictionOracle.model_validate(
        {
            "kind": "prediction_oracle",
            "normalization": rules,
            "expected": document.get("expected"),
            "expected_typed": document.get("expected_typed"),
            "reason": document["reason"],
        }
    )


def grade_output_fixture() -> dict[str, Any]:
    """Grade every output-prediction candidate under each frozen normalization."""
    outcomes: dict[str, Any] = {}
    for mode, answers in output_fixture.CANDIDATE_ANSWERS.items():
        oracle = build_oracle(output_fixture.oracle(mode))
        per_answer = {}
        for name, text in answers.items():
            submission = parse_submission(text, oracle.normalization)
            report = grade_prediction_task(
                family="output_prediction",
                task_id=output_fixture.INSTANCE_ID,
                submission=submission,
                oracle=oracle,
            )
            per_answer[name] = {
                "matched": report.matched,
                "reason": report.reason,
                "normalization_mode": report.normalization_mode,
                "candidate_digest": report.candidate_digest,
                "oracle_digest": report.oracle_digest,
            }
        outcomes[mode] = per_answer
    return outcomes


def grade_test_fixture() -> dict[str, Any]:
    """Grade the test-prediction candidates against the declared test's oracle."""
    oracle = build_oracle(test_fixture.oracle())
    outcomes: dict[str, Any] = {}
    for name, text in test_fixture.CANDIDATE_ANSWERS.items():
        submission = parse_submission(text, oracle.normalization)
        report = grade_prediction_task(
            family="test_prediction",
            task_id=test_fixture.INSTANCE_ID,
            submission=submission,
            oracle=oracle,
        )
        outcomes[name] = {
            "matched": report.matched,
            "reason": report.reason,
            "normalization_mode": report.normalization_mode,
            "candidate_digest": report.candidate_digest,
            "oracle_digest": report.oracle_digest,
            "declared_test_id": test_fixture.TEST_ID,
        }
    return outcomes


def protocol_restriction_case() -> dict[str, Any]:
    """Demonstrate the PCB-28-1 refusal on the shipped prediction protocol.

    The refusal is what makes a different tool policy a different cohort: the frozen protocol is
    checked against the contract, then each execution tool is shown to be refused individually.
    """
    protocol = yaml.safe_load(
        (ROOT / "config" / "protocols" / "prediction-v1.yaml").read_text(encoding="utf-8")
    )
    tools = list(protocol["tools"])
    validate_prediction_tools(tools)
    refusals: dict[str, str] = {}
    for tool in ("run_command", "run_tests", "patch_file", "write_file"):
        try:
            validate_prediction_tools([tool])
            refusals[tool] = "accepted"
        except PredictionContractError as error:
            refusals[tool] = str(error)[:120]
    return {
        "protocol_id": protocol["protocol_id"],
        "declared_tools": tools,
        "declared_tool_calls": protocol["budget"]["tool_calls"],
        "execution_tool_refusals": refusals,
        "verdict": "the shipped protocol cannot execute the target",
    }


def main() -> int:
    audit = audit_track_b()
    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": "prompt_28_prediction_evidence",
        "execution_tier": "local_fixture",
        "quality_admission": "pending",
        "output_prediction": grade_output_fixture(),
        "test_prediction": grade_test_fixture(),
        "protocol_restriction": protocol_restriction_case(),
        "answer_only_metric_definitions": prediction_metric_definitions(),
        "track_b_coverage": json.loads(audit.as_json()),
        "not_claimed": [
            "no live model produced these predictions: the graded submissions are the fixture's "
            "authored answers, so this evidence shows the grader's behaviour, not model skill",
            "no production-worker run occurred; grading ran at the local fixture tier",
            "repo_qa is backed by Prompt 27's dedicated authored-fixture evidence; it does not "
            "claim a live model or judge run",
            "quality admission remains pending for both prediction fixtures",
        ],
        "fixtures": {
            "output_prediction": output_fixture.record(),
            "test_prediction": test_fixture.record(),
        },
    }
    body = json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
    document["report_digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
    out = ROOT / "docs" / "implementation" / "evidence" / "prompt-28-e2e38.json"
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    print(f"  WP-20 closed: {document['track_b_coverage']['wp20_closed']}")
    print(f"  failed families: {document['track_b_coverage']['failed_families']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
