"""Safe handoff of complete internal Track A aggregates to the release workflow."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from polycodebench_core.application_errors import InvalidState

from polycodebench_publication.releases import digest, validate_projection

_TRACK_A_METRICS = (
    ("track_a.a_score_0_100", "a_score"),
    ("track_a.precision_ratio", "precision"),
    ("track_a.recall_ratio", "recall"),
    ("track_a.f1_ratio", "f1"),
    ("track_a.localization_0_100", "localization"),
    ("track_a.explanation_0_100", "explanation"),
    ("track_a.severity_0_100", "severity"),
    ("track_a.repair_0_100", "repair_score_bp"),
)


def _decimal_text(value: Any, *, basis_points: bool = False) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidState("Track A metric is not numeric") from exc
    if not number.is_finite():
        raise InvalidState("Track A metric must be finite")
    if basis_points:
        number /= 100
    return format(number, "f")


def track_a_release_documents(
    aggregate: Mapping[str, Any], *, limitations: tuple[str, ...] = ()
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an allowlisted projection and internal content for an existing release draft.

    Incomplete, pending-review, partial-coverage and unavailable aggregates cannot enter the
    release lifecycle. This adapter never approves or publishes a release.
    """
    if aggregate.get("kind") != "track_a_aggregate" or aggregate.get("schema_version") != 1:
        raise InvalidState("expected a version 1 Track A aggregate")
    if aggregate.get("label") != "exploratory_internal":
        raise InvalidState("only exploratory internal Track A aggregates are eligible for drafts")
    if aggregate.get("strict_status") != "complete":
        raise InvalidState("Track A review or missingness blocks release-draft creation")
    expected = aggregate.get("expected_attempts")
    completed = aggregate.get("completed_attempts")
    missing = aggregate.get("missing_attempts")
    unresolved = aggregate.get("unresolved_findings")
    model_failed = aggregate.get("model_failed_attempts")
    repair_failures = aggregate.get("repair_failure_count")
    if (
        type(expected) is not int
        or type(completed) is not int
        or type(missing) is not int
        or type(unresolved) is not int
        or type(model_failed) is not int
        or type(repair_failures) is not int
        or expected <= 0
        or completed != expected
        or missing != 0
        or unresolved != 0
        or not 0 <= model_failed <= completed
        or not 0 <= repair_failures <= completed
    ):
        raise InvalidState("Track A release drafts require complete attempt coverage")
    coverage = _decimal_text(aggregate.get("coverage"))
    if coverage is None or Decimal(coverage) != 1:
        raise InvalidState("Track A release drafts require full frozen-cohort coverage")
    if aggregate.get("a_score") is None:
        raise InvalidState("Track A release drafts require an available headline score")
    entry_id = aggregate.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise InvalidState("Track A release drafts require a fixed entry identity")

    cohort_digest = aggregate.get("cohort_digest")
    metric_rows = []
    for public_name, source_name in _TRACK_A_METRICS:
        value = aggregate.get(source_name)
        if source_name == "repair_score_bp":
            value = _decimal_text(value, basis_points=True)
        else:
            value = _decimal_text(value)
        metric_rows.append(
            {
                "metric_id": public_name,
                "value": value,
                "interval_low": None,
                "interval_high": None,
                "coverage": coverage,
                "conditional_on_pass": False,
            }
        )
    disclosed_limitations = [
        "Exploratory internal Track A release draft; not a public ranked model result.",
        "Track A uncertainty intervals are unavailable in this aggregate and are not inferred.",
        "Track A source rights, reviewer evidence and cohort identity need separate validation.",
        *limitations,
    ]
    if model_failed:
        disclosed_limitations.append(
            f"{model_failed} explicit model-failed attempt(s) are included "
            "with zero detection and repair credit."
        )
    content = {
        "schema_version": 1,
        "kind": "track_a_release_content",
        "fixture_kind": "synthetic_internal",
        "entry_id": entry_id,
        "cohort_digest": cohort_digest,
        "aggregate_digest": digest(dict(aggregate)),
        "headline_formula": "0.35*(100*F1)+0.10*L+0.10*X+0.05*V+0.40*RepairScore",
        "score_scale": "F1/precision/recall are ratios; L/X/V/repair/A are points from 0 to 100.",
        "strict_status": "complete",
        "expected_attempts": expected,
        "completed_attempts": completed,
        "missing_attempts": missing,
        "model_failed_attempts": model_failed,
        "unresolved_findings": unresolved,
        "repair_failure_count": repair_failures,
        "duplicate_count": aggregate.get("duplicate_count"),
        "source_language_scores": aggregate.get("source_language_scores"),
        "limitations": disclosed_limitations,
    }
    projection = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": cohort_digest,
        "metrics": metric_rows,
        "limitations": disclosed_limitations,
    }
    validate_projection(projection)
    return content, projection


def render_track_a_report(content: Mapping[str, Any], projection: Mapping[str, Any]) -> str:
    """Render a small internal report from the same content bound to the release draft."""
    lines = [
        "# Internal Track A report",
        "",
        "Synthetic/internal exploratory evidence; not model benchmark results.",
        "",
        f"Cohort digest: `{content['cohort_digest']}`",
        f"Entry: `{content['entry_id']}`",
        f"Aggregate digest: `{content['aggregate_digest']}`",
        f"Coverage: {content['completed_attempts']}/{content['expected_attempts']}",
        "",
        "| Metric | Value | Coverage | Interval |",
        "| --- | ---: | ---: | --- |",
    ]
    for metric in projection["metrics"]:
        value = metric["value"] if metric["value"] is not None else "N/A"
        lines.append(f"| {metric['metric_id']} | {value} | {metric['coverage']} | unavailable |")
    lines.extend(["", "Limitations:"])
    lines.extend(f"- {limitation}" for limitation in projection["limitations"])
    return "\n".join(lines) + "\n"
