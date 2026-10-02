"""Internal report entrypoints; fixtures are not model benchmark results."""

import json

from polycodebench_publication.aggregation import BootstrapConfig, MetricDefinition
from polycodebench_publication.cli import main
from polycodebench_publication.reporting import ReportRequest, generate_report
from test_publication_aggregation import cohort, rows


def test_report_cli_replay_and_changed_archive(tmp_path):
    policy = cohort()
    request = ReportRequest(
        cohort=policy,
        metrics=(MetricDefinition(metric_id="total_score", label="All attempts"),),
        observations=rows(policy),
        entry_ids=("internal-a",),
        bootstrap=BootstrapConfig(replicates=30),
    )
    # Strict Python validation requires typed models; JSON mode is the file boundary.
    source = tmp_path / "request.json"
    source.write_text(request.model_dump_json(), encoding="utf-8")
    output = tmp_path / "aggregate.json"
    assert main(["aggregate", "--request", str(source), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result == generate_report(request)
    assert result["fixture_kind"] == "synthetic_internal"
    assert not result["editorial_index_enabled"]
    markdown = tmp_path / "report.md"
    assert main(["report", "--request", str(source), "--output", str(markdown)]) == 0
    assert "not model benchmark results" in markdown.read_text(encoding="utf-8")
    assert main(["replay", "--request", str(source), "--archived", str(output)]) == 0
    result["cohort_digest"] = "changed"
    output.write_text(json.dumps(result), encoding="utf-8")
    assert main(["replay", "--request", str(source), "--archived", str(output)]) == 2
