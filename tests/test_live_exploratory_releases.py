"""Unranked ``live_exploratory`` releases: projection rules, receipts, builder and API label."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_api.app import create_app
from polycodebench_core.application_errors import InvalidState
from polycodebench_operations.release_sync import verified_publication_snapshot
from polycodebench_publication.keyring import Keyring
from polycodebench_publication.live import (
    LiveScorecardRow,
    live_release_documents,
    live_release_evidence,
)
from polycodebench_publication.releases import (
    LIVE_EXPLORATORY_DISCLOSURE,
    NOT_APPLICABLE_EXPLORATORY,
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
    validate_projection,
    validate_release_kind,
)

PRINCIPAL = ReleasePrincipal("live-test", frozenset({"curator", "reviewer", "publisher"}))
RUN_A = "0a0a0a0a-0000-4000-8000-000000000001"
RUN_B = "0b0b0b0b-0000-4000-8000-000000000002"


def _projection(**overrides: Any) -> dict[str, Any]:
    projection = {
        "schema_version": 1,
        "fixture_kind": "live_exploratory",
        "scope": "exploratory",
        "cohort_digest": digest({"cohort": "live"}),
        "limitations": [LIVE_EXPLORATORY_DISCLOSURE],
        "metrics": [
            {
                "metric_id": "pass_rate",
                "value": "50",
                "interval_low": None,
                "interval_high": None,
                "coverage": "1",
                "conditional_on_pass": False,
            }
        ],
    }
    projection.update(overrides)
    return projection


def _row(run_id: str, task: str, sample: int, gate: str | None, composite: str | None) -> Any:
    scored = gate is not None
    return LiveScorecardRow(
        run_id=run_id,
        model_provider="ollama",
        model_name=f"model-{run_id[:2]}",
        model_revision="rev-1",
        task_id=task,
        task_version=1,
        language="python",
        stratum="python-core",
        cluster_id=f"cluster-{task}",
        sample_index=sample,
        attempt_state="completed",
        evaluation_id=f"evaluation-{run_id[:2]}-{task}-{sample}" if scored else None,
        evaluation_state="ready" if scored else None,
        scorecard_id=f"scorecard-{run_id[:2]}-{task}-{sample}" if scored else None,
        gate=gate,
        composite=Decimal(composite) if composite is not None else None,
        policy_digest=digest({"policy": "live"}) if scored else None,
        scorer_digest=digest({"scorer": "live"}) if scored else None,
        evidence_digest=digest({"evidence": task, "sample": sample}) if scored else None,
        evidence_verified=scored,
    )


def _rows() -> list[LiveScorecardRow]:
    return [
        _row(RUN_A, "task-one", 0, "pass", "0.80000000"),
        _row(RUN_A, "task-two", 0, "fail", "0.00000000"),
        _row(RUN_B, "task-one", 0, "pass", "0.60000000"),
        _row(RUN_B, "task-two", 0, None, None),
    ]


def _receipts(doc: dict[str, Any]) -> tuple[ValidationEvidence, ...]:
    return tuple(
        ValidationEvidence(
            check,
            doc["content_digest"],
            digest({"receipt": check}),
            digest({"receipt": check}),
            f"internal:synthetic/{check}",
        )
        for check in sorted(REQUIRED_CHECKS)
    )


def _publish(
    store: ReleaseStore, content: dict[str, Any], projection: dict[str, Any]
) -> tuple[str, SigningKey]:
    rows = _rows()
    doc = store.draft(PRINCIPAL, content, projection, "draft")
    evidence = live_release_evidence(content, projection, rows)
    doc = store.validate(PRINCIPAL, doc["id"], evidence, doc["version"], "validate")
    doc = store.review(PRINCIPAL, doc["id"], "reviewed", doc["version"], "review")
    doc = store.approve(PRINCIPAL, doc["id"], "approved", doc["version"], "approve")
    signer = SigningKey("live-test-key", Ed25519PrivateKey.generate())
    doc = store.publish(PRINCIPAL, doc["id"], signer, 0, doc["version"], "publish")
    return str(doc["id"]), signer


def test_live_exploratory_projection_requires_exploratory_scope_and_disclosure() -> None:
    validate_projection(_projection())
    with pytest.raises(InvalidState, match="explicitly exploratory"):
        validate_projection(_projection(scope="ranked_eligible"))
    with pytest.raises(InvalidState, match="unranked disclosure"):
        validate_projection(_projection(limitations=["Live data"]))
    with pytest.raises(InvalidState, match="explicitly exploratory"):
        validate_projection(_projection(fixture_kind="live_ranked"))


def test_synthetic_projection_rules_are_unchanged() -> None:
    synthetic = _projection(fixture_kind="synthetic_internal", limitations=["Synthetic"])
    validate_projection(synthetic)
    with pytest.raises(InvalidState, match="explicitly exploratory"):
        validate_projection({**synthetic, "scope": "ranked_eligible"})
    validate_release_kind({"anything": "private"}, synthetic)


def test_live_exploratory_content_cannot_carry_rank(tmp_path: Path) -> None:
    content, projection = live_release_documents(_rows())
    ranked = copy.deepcopy(content)
    ranked["entries"][0]["rank"] = 1
    with pytest.raises(InvalidState, match="cannot rank"):
        validate_release_kind(ranked, projection)
    with pytest.raises(InvalidState, match="typed public release content"):
        validate_release_kind({"kind": "recovery_rehearsal_release"}, projection)
    store = ReleaseStore(tmp_path / "releases.db")
    with pytest.raises(InvalidState, match="cannot rank"):
        store.draft(PRINCIPAL, ranked, projection, "ranked")


def test_not_applicable_receipts_are_limited_to_live_exploratory_checks(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "releases.db")
    synthetic = store.draft(
        PRINCIPAL,
        {"policy": {}},
        _projection(fixture_kind="synthetic_internal", limitations=["Synthetic"]),
        "synthetic",
    )
    receipts = list(_receipts(synthetic))
    receipts[0] = replace(receipts[0], outcome=NOT_APPLICABLE_EXPLORATORY)
    with pytest.raises(InvalidState, match="not applicable"):
        store.validate(PRINCIPAL, synthetic["id"], tuple(receipts), 1, "synthetic-na")
    receipts[0] = replace(receipts[0], outcome="assumed")
    with pytest.raises(InvalidState, match="unknown validation receipt outcome"):
        store.validate(PRINCIPAL, synthetic["id"], tuple(receipts), 1, "synthetic-unknown")

    validated = store.validate(PRINCIPAL, synthetic["id"], _receipts(synthetic), 1, "synthetic-ok")
    assert all("outcome" not in row for row in validated["validation"]["report"]["receipts"])

    content, projection = live_release_documents(_rows())
    live = store.draft(PRINCIPAL, content, projection, "live")
    evidence = list(live_release_evidence(content, projection, _rows()))
    membership = next(i for i, row in enumerate(evidence) if row.check == "membership")
    evidence[membership] = replace(evidence[membership], outcome=NOT_APPLICABLE_EXPLORATORY)
    with pytest.raises(InvalidState, match="not applicable"):
        store.validate(PRINCIPAL, live["id"], tuple(evidence), 1, "live-membership-na")


def test_builder_aggregates_rows_and_records_not_applicable_checks() -> None:
    content, projection = live_release_documents(_rows())
    assert projection["fixture_kind"] == "live_exploratory"
    assert projection["scope"] == "exploratory"
    assert LIVE_EXPLORATORY_DISCLOSURE in projection["limitations"]
    entries = {entry["model_config_id"]: entry for entry in content["entries"]}
    assert all(entry["rank"] is None for entry in entries.values())
    complete = entries["live-ollama-model-0a-0a0a0a0a"]
    metrics = {metric["metric_id"]: metric for metric in complete["metrics"]}
    assert metrics["total_score"]["status"] == "measured"
    assert metrics["total_score"]["value"] == "40.000000"
    assert metrics["pass_rate"]["value"] == "50.000000"
    partial = entries["live-ollama-model-0b-0b0b0b0b"]
    assert {metric["status"] for metric in partial["metrics"]} == {"insufficient_information"}
    assert partial["coverage"]["samples"] == 1
    assert "task-one" not in str(content) and "task-one" not in str(projection)

    evidence = live_release_evidence(content, projection, _rows())
    outcomes = {item.check: item.outcome for item in evidence}
    assert set(outcomes) == REQUIRED_CHECKS
    assert outcomes["judge_calibration"] == NOT_APPLICABLE_EXPLORATORY
    assert outcomes["membership"] == "verified"
    assert all(item.expected_digest == item.observed_digest for item in evidence)


def test_builder_receipts_reject_a_changed_database_snapshot(tmp_path: Path) -> None:
    content, projection = live_release_documents(_rows())
    store = ReleaseStore(tmp_path / "releases.db")
    doc = store.draft(PRINCIPAL, content, projection, "draft")
    changed = _rows()
    changed[-1] = _row(RUN_B, "task-two", 0, "pass", "1.00000000")
    with pytest.raises(InvalidState, match="evidence mismatch"):
        store.validate(
            PRINCIPAL,
            doc["id"],
            live_release_evidence(content, projection, changed),
            doc["version"],
            "stale",
        )
    unverified = [replace(row, evidence_verified=False) for row in _rows()]
    with pytest.raises(InvalidState, match="evidence mismatch"):
        store.validate(
            PRINCIPAL,
            doc["id"],
            live_release_evidence(content, projection, unverified),
            doc["version"],
            "unverified",
        )


def test_builder_refuses_mixed_policies_and_unscored_runs() -> None:
    rows = _rows()
    rows[0] = replace(rows[0], policy_digest=digest({"policy": "other"}))
    with pytest.raises(InvalidState, match="one frozen scoring policy"):
        live_release_documents(rows)
    with pytest.raises(InvalidState, match="no persisted scorecards"):
        live_release_documents([_row(RUN_A, "task-one", 0, None, None)])


def test_published_live_release_syncs_and_api_labels_every_response(tmp_path: Path) -> None:
    content, projection = live_release_documents(_rows())
    store = ReleaseStore(tmp_path / "releases.db")
    release_id, signer = _publish(store, content, projection)
    keyring = Keyring().rotate(signer, at="2026-10-09T00:00:00Z")
    snapshots, pointer = verified_publication_snapshot(store, keyring=keyring)
    assert pointer["release_id"] == release_id
    assert snapshots[0]["projection"]["fixture_kind"] == "live_exploratory"
    receipts = store.get(release_id)["validation"]["report"]["receipts"]
    assert {row["check"] for row in receipts if row.get("outcome")} == {
        "coverage_intervals",
        "judge_calibration",
        "native_labels",
        "rights",
        "scorer_replay",
    }

    app = create_app(store=store, cursor_key=b"live-exploratory-test-cursor-key-00")

    async def verify() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            releases = (await client.get("/v1/releases")).json()
            assert releases["data"][0]["fixture_kind"] == "live_exploratory"
            assert releases["data"][0]["scope"] == "exploratory"
            board = (await client.get("/v1/leaderboard")).json()
            assert board["meta"]["fixture_kind"] == "live_exploratory"
            assert board["meta"]["exploratory"] is True
            assert {row["ranking_label"] for row in board["data"]} == {"exploratory"}
            assert {row["rank"] for row in board["data"]} == {None}
            entry_id = board["data"][0]["model_config_id"]
            model = (await client.get(f"/v1/models/{entry_id}")).json()
            assert model["meta"]["fixture_kind"] == "live_exploratory"
            methods = (await client.get("/v1/methodology/live-exploratory-v1")).json()
            assert methods["meta"]["fixture_kind"] == "live_exploratory"

    asyncio.run(verify())


def _model_failure(run_id: str, task: str, sample: int) -> LiveScorecardRow:
    return replace(
        _row(run_id, task, sample, None, None),
        attempt_state="failed",
        attempt_failure_class="model_failure",
        run_policy_digest=digest({"policy": "live"}),
    )


def test_model_failures_are_scored_zero_and_disclosed() -> None:
    """PolyCodeBench-Architecture-v1.md 5.4/10.3: a model failure counts as zero, not missing."""
    rows = [
        _row(RUN_A, "task-one", 0, "pass", "0.80000000"),
        _model_failure(RUN_A, "task-two", 0),
    ]
    content, projection = live_release_documents(rows)
    (entry,) = content["entries"]
    metrics = {metric["metric_id"]: metric for metric in entry["metrics"]}
    assert metrics["total_score"]["status"] == "measured"
    assert metrics["total_score"]["value"] == "40.000000"
    assert metrics["pass_rate"]["value"] == "50.000000"
    assert entry["coverage"]["samples"] == 2
    disclosure = "1 attempt(s) ended in a model failure"
    assert any(item.startswith(disclosure) for item in projection["limitations"])
    evidence = live_release_evidence(content, projection, rows)
    assert all(item.expected_digest == item.observed_digest for item in evidence)


def test_all_model_failure_run_publishes_zero_but_infrastructure_failure_does_not() -> None:
    content, projection = live_release_documents(
        [_model_failure(RUN_A, "task-one", 0), _model_failure(RUN_A, "task-two", 0)]
    )
    (entry,) = content["entries"]
    assert {metric["value"] for metric in entry["metrics"]} == {"0.000000"}
    assert content["policy_digest"] == digest({"policy": "live"})
    assert any(item.startswith("2 attempt(s) ended") for item in projection["limitations"])
    infrastructure = replace(_model_failure(RUN_A, "task-one", 0), attempt_failure_class=None)
    with pytest.raises(InvalidState, match="no persisted scorecards"):
        live_release_documents([infrastructure])
