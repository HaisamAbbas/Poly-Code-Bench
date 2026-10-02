"""Go diagnostic/idiom profile tests: applicability, ownership, token demotion and dedup.

The weights are read from ``config/languages/profiles-v1.yaml`` rather than re-typed, so a change to
the frozen weights cannot silently diverge from what the plugin scores.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from polycodebench_core.models import Confidence, MeasurementStatus, Observation, ScoreDimension
from polycodebench_lang_go import GoLanguagePlugin
from polycodebench_lang_go.observations import issue_key

PARSER = "pcb-go-parsers-1"
CANDIDATE_DIGEST = "sha256:" + "4" * 64


@pytest.fixture(scope="module")
def profile():  # type: ignore[no-untyped-def]
    return GoLanguagePlugin().go_profile


def weights() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load(
        (root / "config" / "languages" / "profiles-v1.yaml").read_text("utf-8")
    )
    return document["profiles"]["go"]


def finding(
    check_id: str,
    *,
    line: int = 10,
    path: str = "topwords/topwords.go",
    status: MeasurementStatus = MeasurementStatus.MEASURED,
    key: str | None = None,
    owner: ScoreDimension | None = None,
    confidence: Confidence | None = Confidence.HIGH,
    severity: str | None = "medium",
) -> Observation:
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=check_id,
        tool_digest="sha256:" + "1" * 64,
        candidate_digest=CANDIDATE_DIGEST,
        status=status,
        value=True if status == MeasurementStatus.MEASURED else None,
        severity=severity,
        confidence=confidence,
        location=None,
        baseline_relation=None,
        issue_key=key if key is not None else issue_key(check_id.rsplit(".", 1)[-1], path, line),
        primary_owner=owner,
        raw_artifact_ids=[],
        explanation=check_id,
    )


def scan(tool: str, status: MeasurementStatus, value: int | None) -> Observation:
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"go.{tool}.scan",
        tool_digest="sha256:" + "1" * 64,
        candidate_digest=CANDIDATE_DIGEST,
        status=status,
        value=value,
        severity=None,
        confidence=None,
        location=None,
        baseline_relation=None,
        issue_key=None,
        primary_owner=None,
        raw_artifact_ids=[],
        explanation=f"{tool} scan",
    )


# ------------------------------------------------------------------------- weights and items


def test_the_items_and_weights_come_from_the_shared_registry(profile) -> None:  # type: ignore[no-untyped-def]
    go = weights()
    assert [item.item_id for item in profile.profile.diagnostic_items] == list(
        go["diagnostic_percent"]
    )
    assert [item.item_id for item in profile.profile.idiom_items] == list(go["idiomatic_percent"])
    for item in profile.profile.diagnostic_items:
        assert item.weight_bp == round(float(go["diagnostic_percent"][item.item_id]) * 100)
    for item in profile.profile.idiom_items:
        assert item.weight_bp == round(float(go["idiomatic_percent"][item.item_id]) * 100)
    assert sum(item.weight_bp for item in profile.profile.diagnostic_items) == 10_000
    assert sum(item.weight_bp for item in profile.profile.idiom_items) == 10_000


def test_the_declared_tools_are_the_ones_the_profile_can_feed(profile) -> None:  # type: ignore[no-untyped-def]
    assert set(weights()["tools"]) == {
        "go_test",
        "go_race",
        "go_vet",
        "staticcheck",
        "gosec",
        "gofmt",
    }


# ------------------------------------------------------------------------------- ownership


def test_lifecycle_findings_have_a_concrete_composite_owner(profile) -> None:  # type: ignore[no-untyped-def]
    for check in (
        "go.context.context-replaced",
        "go.context.context-cancel-not-deferred",
        "go.context.context-not-honoured",
        "go.context.goroutine-not-joined",
        "go.context.goroutine-in-loop",
        "go.race.data-race",
    ):
        assert profile.owner(check) == ScoreDimension.ROBUSTNESS, check


def test_security_evidence_has_a_single_owner_and_no_diagnostic_item(profile) -> None:  # type: ignore[no-untyped-def]
    assert profile.owner("go.gosec.g401") == ScoreDimension.SECURITY
    assert profile.owner("go.dependency.go-2026-0001") == ScoreDimension.SECURITY
    mapping = profile.resolve("go.gosec.g401")
    assert mapping is not None and mapping.items == ()
    assert profile.resolve("go.gofmt.unformatted") is not None


def test_a_style_hint_is_unmapped_and_cannot_reach_a_score(profile) -> None:  # type: ignore[no-untyped-def]
    # These are reviewer hints; mapping them would let a hint share an issue family with a defect.
    assert profile.resolve("go.context.error-ignored-deferred") is None
    assert profile.resolve("go.context.context-created-at-entry") is None


# -------------------------------------------------------------------------- token demotion


def test_gosec_g104_alone_is_not_counted(profile) -> None:  # type: ignore[no-untyped-def]
    only_tool = [finding("go.gosec.g104", key=issue_key("ignored-error", "a.go", 7))]
    result = profile.evaluate(
        opportunities={"error_handling": 2},
        observations=[scan("context", MeasurementStatus.MEASURED, 0), *only_tool],
        required_tools=("context",),
    )
    item = next(i for i in result.diagnostic if i.item_id == "error_handling")
    assert item.status == "measured"
    assert item.unique_violations == 0
    assert item.score_bp == 10_000


def test_the_scanner_confirms_the_token_and_the_pair_counts_once(profile) -> None:  # type: ignore[no-untyped-def]
    key = issue_key("ignored-error", "topwords/topwords.go", 7)
    both = [
        finding("go.gosec.g104", key=key, line=7),
        finding("go.context.error-ignored-blank", key=key, line=7, severity="high"),
    ]
    merged = profile.normalize(both)
    assert len(merged) == 1
    assert "also reported by" in (merged[0].explanation or "")
    result = profile.evaluate(
        opportunities={"error_handling": 2},
        observations=[scan("context", MeasurementStatus.MEASURED, 1), *merged],
        required_tools=("context",),
    )
    item = next(i for i in result.diagnostic if i.item_id == "error_handling")
    assert item.unique_violations == 1
    assert item.score_bp == 5_000


def test_evaluation_ignores_a_token_even_when_normalisation_was_skipped(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(
        opportunities={"error_handling": 1},
        observations=[finding("go.gosec.g104")],
        required_tools=(),
    )
    item = next(i for i in result.diagnostic if i.item_id == "error_handling")
    assert item.unique_violations == 0


def test_a_benign_verdict_is_never_penalised(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(
        opportunities={"error_handling": 1},
        observations=[
            finding("go.context.error-ignored-deferred", status=MeasurementStatus.NOT_APPLICABLE)
        ],
        required_tools=(),
    )
    item = next(i for i in result.diagnostic if i.item_id == "error_handling")
    assert item.unique_violations == 0
    assert item.score_bp == 10_000


# ---------------------------------------------------------------------------- applicability


def test_no_frozen_opportunity_is_not_applicable_never_perfect(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(opportunities={}, observations=[], required_tools=())
    # Nothing was measurable, so there is no score at all: an all-N/A profile is not "perfect".
    assert result.diagnostic_score_bp is None
    assert result.complete is True


def test_a_nonconcurrent_task_is_not_charged_for_concurrency(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(
        opportunities={"error_handling": 1},
        observations=[finding("go.context.goroutine-not-joined")],
        required_tools=(),
    )
    assert next(i for i in result.diagnostic if i.item_id == "goroutines_channels").status == (
        "not_applicable"
    )
    assert next(i for i in result.diagnostic if i.item_id == "cancellation_context").status == (
        "not_applicable"
    )


def test_a_concurrent_task_counts_its_lifecycle_findings(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(
        opportunities={"goroutines_channels": 2, "cancellation_context": 1},
        observations=[
            scan("race", MeasurementStatus.MEASURED, 1),
            finding("go.race.data-race"),
            finding("go.context.goroutine-not-joined"),
        ],
        required_tools=("race",),
    )
    channels = next(i for i in result.diagnostic if i.item_id == "goroutines_channels")
    assert channels.status == "measured"
    assert channels.unique_violations == 2
    assert channels.score_bp == 0
    cancellation = next(i for i in result.diagnostic if i.item_id == "cancellation_context")
    # The task declares a cancellation opportunity, so that item is measurable - and clean, because
    # neither lifecycle finding is a cancellation one.
    assert (cancellation.status, cancellation.unique_violations) == ("measured", 0)
    assert cancellation.score_bp == 10_000


# ------------------------------------------------------------------------- failed evidence


def test_a_missing_required_scan_makes_its_items_missing(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(
        opportunities={"error_handling": 1, "standard_library": 1},
        observations=[
            scan("context", MeasurementStatus.MISSING, None),
            scan("gosec", MeasurementStatus.MISSING, None),
        ],
        required_tools=("context", "gosec"),
    )
    states = {i.item_id: i.status for i in result.diagnostic if i.status != "not_applicable"}
    assert states == {"error_handling": "missing", "standard_library": "missing"}
    assert result.diagnostic_score_bp is None
    assert result.complete is False


def test_an_unsupported_required_race_run_blocks_only_tasks_that_need_it(profile) -> None:  # type: ignore[no-untyped-def]
    observations = [scan("race", MeasurementStatus.NOT_APPLICABLE, None)]
    required = profile.evaluate(
        opportunities={"goroutines_channels": 1},
        observations=observations,
        required_tools=("race",),
    )
    item = next(i for i in required.diagnostic if i.item_id == "goroutines_channels")
    assert item.status == "missing"
    assert item.reasons == ("required scan unsupported: race",)

    optional = profile.evaluate(
        opportunities={"goroutines_channels": 1},
        observations=observations,
        required_tools=(),
    )
    assert next(i for i in optional.diagnostic if i.item_id == "goroutines_channels").status == (
        "measured"
    )
    assert next(i for i in optional.diagnostic if i.item_id == "goroutines_channels").score_bp == (
        10_000
    )


def test_a_clean_required_race_run_is_measured_with_zero_findings(profile) -> None:  # type: ignore[no-untyped-def]
    result = profile.evaluate(
        opportunities={"goroutines_channels": 1},
        observations=[scan("race", MeasurementStatus.MEASURED, 0)],
        required_tools=("race",),
    )
    item = next(i for i in result.diagnostic if i.item_id == "goroutines_channels")
    assert (item.status, item.unique_violations, item.score_bp) == ("measured", 0, 10_000)


# ------------------------------------------------------------------------------ dedup keys


def test_one_defect_reported_by_two_tools_is_one_issue(profile) -> None:  # type: ignore[no-untyped-def]
    key = profile.key_for("go.context.error-chain-broken", "topwords/topwords.go", 29)
    other = profile.key_for("go.context.error-chain-broken", "topwords/topwords.go", 29)
    assert key == other
    assert key.startswith("go.error-chain.")
    # A different family at the same line is a different issue.
    assert (
        profile.key_for("go.context.error-sentinel-comparison", "topwords/topwords.go", 29) != key
    )


def test_the_parser_version_is_part_of_the_recorded_identity() -> None:
    from polycodebench_lang_go.identities import PARSER_VERSION

    assert PARSER_VERSION == "pcb-go-parsers-1"
