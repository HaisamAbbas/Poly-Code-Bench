"""The live exploratory builder reads real persisted scorecards from disposable PostgreSQL."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from polycodebench_core.application_errors import InvalidState
from polycodebench_operations.cli import main as ops_main
from polycodebench_operations.live_release import build_live_release
from polycodebench_persistence.database import Database
from polycodebench_persistence.scoring import PostgresScoringRepository
from polycodebench_publication.releases import (
    NOT_APPLICABLE_EXPLORATORY,
    ReleasePrincipal,
    ReleaseStore,
    ValidationEvidence,
)
from test_scoring_postgres import _frozen_evaluation, _record, _test_database_url


@pytest.fixture(scope="module")
def database() -> Database:
    instance = Database(_test_database_url())
    yield instance
    instance.dispose()


def test_builder_reads_persisted_scorecards_into_a_valid_live_release(
    database: Database, tmp_path: Path
) -> None:
    values = _frozen_evaluation(database.engine)
    run_id = UUID(str(values["run_id"]))
    with pytest.raises(InvalidState, match="no persisted scorecards"):
        build_live_release(database.engine, [run_id])

    PostgresScoringRepository(database.engine).store(_record(values), actor="live-release-test")
    content, projection, evidence = build_live_release(database.engine, [run_id])

    assert projection["fixture_kind"] == "live_exploratory"
    assert projection["scope"] == "exploratory"
    (entry,) = content["entries"]
    assert entry["rank"] is None
    metrics = {metric["metric_id"]: metric for metric in entry["metrics"]}
    assert metrics["total_score"]["value"] == "0.000000"
    assert metrics["pass_rate"]["value"] == "0.000000"
    assert str(values["task_id"]) not in json.dumps([content, projection])
    assert {row["check"] for row in evidence if row.get("outcome")} == {
        "coverage_intervals",
        "judge_calibration",
        "native_labels",
        "rights",
        "scorer_replay",
    }
    assert all(
        row.get("outcome", "verified") in {"verified", NOT_APPLICABLE_EXPLORATORY}
        for row in evidence
    )

    store = ReleaseStore(tmp_path / "releases.db")
    principal = ReleasePrincipal("live-test", frozenset({"curator", "reviewer"}))
    doc = store.draft(principal, content, projection, "draft")
    doc = store.validate(
        principal,
        doc["id"],
        tuple(ValidationEvidence(**row) for row in evidence),
        doc["version"],
        "validate",
    )
    assert doc["state"] == "review_required"

    with pytest.raises(InvalidState, match="do not exist"):
        build_live_release(database.engine, [UUID(int=1)])


def test_ops_cli_writes_documents_for_the_release_lifecycle(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    values = _frozen_evaluation(database.engine)
    PostgresScoringRepository(database.engine).store(_record(values), actor="live-release-test")
    monkeypatch.setenv("PCB_DATABASE_URL", _test_database_url())
    output = tmp_path / "live"

    code = ops_main(
        ["releases", "build-live", "--run-id", str(values["run_id"]), "--output-dir", str(output)]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0, report
    assert report["fixture_kind"] == "live_exploratory"
    assert "judge_calibration" in report["not_applicable_checks"]
    assert {path.name for path in output.iterdir()} == {
        "content.json",
        "evidence.json",
        "projection.json",
    }
