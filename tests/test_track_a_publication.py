"""Track A uses the existing reviewed release draft boundary without public publishing."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from polycodebench_core.application_errors import InvalidState, NotFound
from polycodebench_core.canonical import canonical_digest, canonical_envelope
from polycodebench_evaluation.track_a import (
    DetectionResult,
    RepairResult,
    TrackACohort,
    TrackASample,
    TrackATaskCell,
    aggregate_track_a,
)
from polycodebench_publication.cli import main as release_main
from polycodebench_publication.releases import ReleaseStore
from polycodebench_publication.track_a import render_track_a_report, track_a_release_documents


def complete_aggregate():
    tasks = tuple(
        TrackATaskCell(
            task_id=f"{language}-history",
            task_version=1,
            language=language,
            source_family="authored_history",
            cluster_id=f"{language}-cluster-1",
            stratum_id="history",
            repair_required=True,
            required_bug_count=1,
        )
        for language in ("python", "rust")
    )
    body = {
        "schema_version": 1,
        "tasks": tasks,
        "required_languages": ("python", "rust"),
        "planned_samples": 1,
        "source_weights_bps": (("python", "history", 10000), ("rust", "history", 10000)),
        "repair_source_weights_bps": (
            ("python", "history", 10000),
            ("rust", "history", 10000),
        ),
        "language_weights_bps": (("python", 5000), ("rust", 5000)),
    }
    digest_payload = {
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "required_languages": ["python", "rust"],
        "planned_samples": 1,
        "source_weights_bps": [["python", "history", 10000], ["rust", "history", 10000]],
        "repair_source_weights_bps": [
            ["python", "history", 10000],
            ["rust", "history", 10000],
        ],
        "language_weights_bps": [["python", 5000], ["rust", 5000]],
    }
    cohort = TrackACohort(
        **body,
        cohort_digest=canonical_digest(canonical_envelope("track_a_cohort", digest_payload)),
    )
    samples = tuple(
        TrackASample(
            entry_id="internal-entry",
            task_id=task.task_id,
            task_version=1,
            sample_index=0,
            result=DetectionResult(
                status="complete",
                true_positive=1,
                false_positive=0,
                false_negative=0,
                unresolved_findings=0,
                duplicate_count=0,
                precision=Decimal(1),
                recall=Decimal(1),
                f1=Decimal(1),
                localization=Decimal(100),
                explanation=Decimal(100),
                severity=Decimal(100),
                matched_pairs=(("finding-1", "bug-1"),),
                edge_digests=("sha256:" + "1" * 64,),
                reasons=(),
            ),
            repair=RepairResult(
                status="evaluated",
                repair_required=True,
                composite_score_bp=10000,
                evaluation_id=f"evaluation-{task.language}",
                evaluation_digest="sha256:" + "2" * 64,
                gate="pass",
                patch_digest="sha256:" + "3" * 64,
                patched_source_digest="sha256:" + "4" * 64,
                reasons=(),
            ),
            scorecard_digest="sha256:" + "5" * 64,
            evidence_tier="authored_internal",
        )
        for task in tasks
    )
    return aggregate_track_a(cohort, samples, "internal-entry")


def test_track_a_complete_aggregate_creates_only_a_reviewable_internal_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    aggregate = complete_aggregate()
    aggregate_document = aggregate.model_dump(mode="json")
    content, projection = track_a_release_documents(aggregate_document)
    assert projection["scope"] == "exploratory"
    assert all(metric["interval_low"] is None for metric in projection["metrics"])
    assert not any("private" in key or "evidence" in key for key in projection)
    report = render_track_a_report(content, projection)
    assert "not model benchmark results" in report
    assert "uncertainty intervals are unavailable" in report

    input_path = tmp_path / "aggregate.json"
    input_path.write_text(json.dumps(aggregate_document), encoding="utf-8")
    store_path = tmp_path / "releases.sqlite"
    assert (
        release_main(
            [
                "track-a-draft",
                "--aggregate",
                str(input_path),
                "--store",
                str(store_path),
                "--subject",
                "local-curator",
                "--role",
                "curator",
                "--request-id",
                "track-a-draft-1",
            ]
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["state"] == "draft"
    assert created["approval"] is None and created["manifest"] is None
    assert created["projection"] == projection
    assert ReleaseStore(store_path).get(created["id"])["state"] == "draft"
    with pytest.raises(NotFound):
        ReleaseStore(store_path).public(created["id"])


def test_track_a_release_adapter_rejects_partial_or_pending_aggregates() -> None:
    aggregate = complete_aggregate().model_dump(mode="json")
    aggregate["strict_status"] = "partial"
    aggregate["completed_attempts"] = 1
    aggregate["coverage"] = "0.5"
    with pytest.raises(InvalidState, match="review or missingness"):
        track_a_release_documents(aggregate)
