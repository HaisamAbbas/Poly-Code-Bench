"""Internal reports with explicit fixture provenance and reproducible evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from polycodebench_publication.aggregation import (
    BootstrapConfig,
    CohortPolicy,
    MetricDefinition,
    Observation,
    PublicationModel,
    aggregate,
    bootstrap,
    paired_comparison,
)


class ReportRequest(PublicationModel):
    kind: Literal["report_request"] = "report_request"
    fixture_kind: Literal["synthetic_internal"] = "synthetic_internal"
    cohort: CohortPolicy
    metrics: tuple[MetricDefinition, ...] = Field(min_length=1)
    observations: tuple[Observation, ...]
    entry_ids: tuple[str, ...] = Field(min_length=1)
    bootstrap: BootstrapConfig = BootstrapConfig()
    paired_entries: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def valid_entries(self) -> ReportRequest:
        if len(set(self.entry_ids)) != len(self.entry_ids):
            raise ValueError("duplicate report entry IDs")
        if len({metric.metric_id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate report metrics")
        for left, right in self.paired_entries:
            if left == right or left not in self.entry_ids or right not in self.entry_ids:
                raise ValueError("paired entries must name two distinct report entries")
        return self


def generate_report(request: ReportRequest) -> dict[str, Any]:
    aggregates = []
    intervals = []
    comparisons = []
    for metric in request.metrics:
        for entry_id in request.entry_ids:
            aggregates.append(
                aggregate(
                    request.cohort,
                    metric,
                    request.observations,
                    entry_id,
                ).model_dump(mode="json")
            )
            intervals.append(
                bootstrap(
                    request.cohort,
                    metric,
                    request.observations,
                    entry_id,
                    request.bootstrap,
                ).model_dump(mode="json")
            )
        for left, right in request.paired_entries:
            comparisons.append(
                paired_comparison(
                    request.cohort,
                    metric,
                    request.observations,
                    left,
                    right,
                    request.bootstrap,
                ).model_dump(mode="json")
            )
    return {
        "schema_version": 1,
        "fixture_kind": request.fixture_kind,
        "claim": "Synthetic/internal behavior evidence; not model benchmark results.",
        "request_digest": request.content_digest(),
        "cohort_digest": request.cohort.content_digest(),
        "metrics": [metric.model_dump(mode="json") for metric in request.metrics],
        "aggregates": aggregates,
        "uncertainty": intervals,
        "paired_comparisons": comparisons,
        "editorial_index_enabled": False,
    }


def execute_request(path: Path) -> dict[str, Any]:
    from polycodebench_publication.cli import read_json

    # JSON-mode validation admits JSON arrays for immutable tuples while preserving strict
    # scalar checks; duplicate keys and non-finite values were already rejected by read_json.
    payload = json.dumps(read_json(path), allow_nan=False)
    return generate_report(ReportRequest.model_validate_json(payload))


def render_report(report: dict[str, Any]) -> str:
    """A reviewable internal Markdown report; never a public projection/export."""
    lines = [
        "# Internal aggregation report",
        "",
        report["claim"],
        "",
        f"Request digest: `{report['request_digest']}`",
        f"Fixed cohort digest: `{report['cohort_digest']}`",
        "",
        "| Entry | Metric | Value | Coverage | Status | Ranking label |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    for row in report["aggregates"]:
        lines.append(
            "| "
            + " | ".join(
                cell(row[key])
                for key in (
                    "entry_id",
                    "metric_id",
                    "value",
                    "coverage",
                    "status",
                    "ranking_label",
                )
            )
            + " |"
        )
    lines.extend(["", "Uncertainty and paired comparisons (fixed-seed evidence):", "", "```json"])
    lines.append(
        json.dumps(
            {
                "uncertainty": report["uncertainty"],
                "paired_comparisons": report["paired_comparisons"],
            },
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    )
    lines.extend(["```", "", "Optional full-product editorial index: disabled.", ""])
    return "\n".join(lines)
