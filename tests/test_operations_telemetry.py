"""Prompt 33 telemetry: correlation IDs, redaction, held-out field drop, bounded metrics."""

from __future__ import annotations

import io
import json
import logging

import pytest
from polycodebench_core.telemetry import (
    REQUIRED_METRICS,
    MetricSpec,
    MetricsRegistry,
    configure_logging,
    log_context,
    redact,
    safe_label,
)

# These credential-shaped values are deterministic test dummies, never real credentials.
SECRETS = {
    "provider key": "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX",
    "aws key": "AKIA0000000000000000",
    "bearer": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123",
    "dsn": "postgresql+psycopg://svc:hunter2-very-secret@db.internal/pcb",
    "password": "password=s3cr3t-value",
    "pcb secret": "PCBSECRET__MODEL__OPENAI=sk-proj-abcdefghijklmnopqrstuv",
}


@pytest.mark.parametrize("label", sorted(SECRETS))
def test_redact_removes_credential_shapes(label: str) -> None:
    secret = SECRETS[label]
    redacted = redact(f"call failed: {secret} end")
    assert "REDACTED" in redacted
    for fragment in (
        "hunter2",
        "s3cr3t",
        "ABCDEFGHIJKLMNOPQRSTUVWX",
        "AKIA0000000000000000",
        "abcdefghijklmnopqrstuvwxyz0123",
        "sk-proj-abcdefghijklmnopqrstuv",
    ):
        assert fragment not in redacted


def _logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    configure_logging(
        environment="staging", role="eval-supervisor", log_format="json", stream=stream
    )
    return logging.getLogger("pcb.test.telemetry"), stream


def test_json_log_carries_correlation_ids_and_drops_held_out_fields() -> None:
    logger, stream = _logger()
    with log_context(run_id="run-1", attempt_id="att-2", job_id="job-3", fence=7):
        logger.warning(
            "stage failed for key %s",
            SECRETS["provider key"],
            extra={
                "lane": "grading",
                "hidden_test_source": "def test_secret_oracle(): assert answer == 42",
                "task_statement": "Implement the held-out task",
                "model_completion": "here is my reasoning",
                "status": "failed",
            },
        )
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert record["run_id"] == "run-1" and record["attempt_id"] == "att-2"
    assert record["job_id"] == "job-3" and record["fence"] == "7"
    assert record["environment"] == "staging" and record["role"] == "eval-supervisor"
    assert record["lane"] == "grading" and record["status"] == "failed"
    assert record["dropped_fields"] == 3
    text = stream.getvalue()
    assert "secret_oracle" not in text and "held-out task" not in text
    assert "my reasoning" not in text and "sk-ant-api03" not in text


def test_exception_text_is_redacted() -> None:
    logger, stream = _logger()
    try:
        raise RuntimeError(f"connect {SECRETS['dsn']}")
    except RuntimeError:
        logger.exception("database unavailable")
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert "hunter2" not in record["exception"] and record["exception_type"] == "RuntimeError"


def test_log_context_rejects_unknown_identifiers() -> None:
    with pytest.raises(ValueError):
        with log_context(prompt="anything"):
            pass


def test_metric_catalog_covers_t22_5_and_refuses_content_labels() -> None:
    names = {spec.name for spec in REQUIRED_METRICS}
    for required in (
        "pcb_queue_oldest_age_seconds",
        "pcb_queue_depth",
        "pcb_job_claims_total",
        "pcb_job_completions_total",
        "pcb_job_retries_total",
        "pcb_expired_leases_total",
        "pcb_orphan_guests",
        "pcb_active_slots",
        "pcb_provider_latency_seconds",
        "pcb_provider_throttles_total",
        "pcb_usage_reserved_microusd",
        "pcb_uncertain_cost_calls",
        "pcb_failures_total",
        "pcb_analyzer_crashes_total",
        "pcb_judge_invalid_total",
        "pcb_judge_disagreement_ratio",
        "pcb_canary_drift_ratio",
        "pcb_evidence_missing",
        "pcb_publication_validation_failures_total",
        "pcb_public_api_request_seconds",
    ):
        assert required in names
    for label in ("run_id", "source", "prompt_text", "user"):
        with pytest.raises(ValueError):
            MetricSpec("pcb_bad", "counter", "x", (label,))


def test_registry_bounds_label_values_and_renders_exposition() -> None:
    registry = MetricsRegistry()
    registry.inc("pcb_stale_commit_refusals_total", queue_class="grading")
    registry.inc("pcb_stale_commit_refusals_total", 2, queue_class="grading")
    registry.observe("pcb_provider_latency_seconds", 0.3, provider="fixture", kind="model")
    with pytest.raises(ValueError):
        registry.inc("pcb_job_claims_total", queue_class="def leak(): return 'source code'")
    with pytest.raises(KeyError):
        registry.inc("pcb_not_declared")
    text = registry.render()
    assert 'pcb_stale_commit_refusals_total{queue_class="grading"} 3.0' in text
    assert 'pcb_provider_latency_seconds_bucket{provider="fixture",kind="model",le="0.5"} 1' in text
    assert safe_label("Solve-e2e07") == "solve-e2e07"
    assert safe_label("contains spaces and code()") == "other"
