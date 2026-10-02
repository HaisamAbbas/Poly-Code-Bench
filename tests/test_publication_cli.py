"""Internal CLI behavior: strict inputs, deterministic replay and safe local mutation."""

from __future__ import annotations

import json
from pathlib import Path

from polycodebench_publication.aggregation import (
    BootstrapConfig,
    CohortPolicy,
    CohortTask,
    MetricDefinition,
    Observation,
    StratumWeight,
)
from polycodebench_publication.cli import main
from polycodebench_publication.reporting import ReportRequest


def _request() -> ReportRequest:
    cohort = CohortPolicy(
        tasks=(
            CohortTask(task_id="task-1", language="python", stratum="easy", cluster_id="c1"),
            CohortTask(task_id="task-2", language="python", stratum="easy", cluster_id="c2"),
        ),
        required_languages=("python",),
        stratum_weights=(StratumWeight(language="python", stratum="easy", weight_bps=10000),),
        planned_samples=1,
        protocol_digest="protocol",
        tool_context_policy_digest="tools",
        budget_tier="internal",
        scorer_digest="scorer",
        evaluator_digest="evaluator",
        judge_panel_digest="panel",
        hardware_class="internal",
        seeds=(16,),
        applicability_policy_digest="applicability",
        compatibility_policy_digest="compatibility",
    )
    observations = tuple(
        Observation(
            entry_id="fixture-entry",
            task_id=f"task-{i}",
            sample_index=0,
            scorecard_digest=f"scorecard-{i}",
            metric_id="total_score",
            status="pass",
            value=str(i * 10),
        )
        for i in (1, 2)
    )
    return ReportRequest(
        cohort=cohort,
        metrics=(MetricDefinition(metric_id="total_score", label="Total"),),
        observations=observations,
        entry_ids=("fixture-entry",),
        bootstrap=BootstrapConfig(seed=16001, replicates=30),
    )


def test_cli_fixed_seed_replay_and_internal_report(tmp_path: Path, capsys: object) -> None:
    request = tmp_path / "request.json"
    request.write_text(_request().model_dump_json(), encoding="utf-8")
    output = tmp_path / "aggregate.json"
    assert main(["aggregate", "--request", str(request), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["fixture_kind"] == "synthetic_internal"
    assert result["editorial_index_enabled"] is False
    assert result["uncertainty"][0]["seed"] == 16001
    assert main(["replay", "--request", str(request), "--archived", str(output)]) == 0
    report = tmp_path / "report.md"
    assert main(["report", "--request", str(request), "--output", str(report)]) == 0
    assert "not model benchmark results" in report.read_text(encoding="utf-8")
    result["cohort_digest"] = "tampered"
    output.write_text(json.dumps(result), encoding="utf-8")
    assert main(["replay", "--request", str(request), "--archived", str(output)]) == 2


def test_cli_rejects_ambiguous_and_extra_request_fields(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    for content in ('{"entry_ids": [], "entry_ids": []}', '{"value": NaN}'):
        request.write_text(content, encoding="utf-8")
        assert main(["aggregate", "--request", str(request)]) == 2
    payload = _request().model_dump(mode="json")
    payload["private_export"] = True
    request.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["aggregate", "--request", str(request)]) == 2


def test_cli_release_refuses_unsafe_projection(tmp_path: Path) -> None:
    content = tmp_path / "content.json"
    content.write_text('{"internal": "private evidence"}', encoding="utf-8")
    projection = tmp_path / "projection.json"
    projection.write_text('{"private_evidence": "leak"}', encoding="utf-8")
    assert (
        main(
            [
                "create",
                "--store",
                str(tmp_path / "releases.sqlite"),
                "--subject",
                "curator-1",
                "--role",
                "curator",
                "--request-id",
                "unsafe",
                "--content",
                str(content),
                "--projection",
                str(projection),
            ]
        )
        == 2
    )
