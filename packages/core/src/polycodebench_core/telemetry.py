"""Structured logs and bounded metrics shared by every PolyCodeBench process (T 22.5).

Logs carry correlation identifiers (request, campaign, run, attempt, evaluation, job, fence,
execution, call) bound through :func:`log_context`, so an operator can follow one run across
services. Two controls keep logs safe to ship to a shared telemetry backend:

* :class:`RedactingJsonFormatter` scrubs credential-shaped strings (provider keys, bearer
  tokens, AWS keys, PEM blocks, DSN passwords, ``PCBSECRET__`` values) from the message, the
  exception text and every field.
* Only allowlisted ``extra`` fields are emitted. A field named like source code, a task
  statement, a prompt or model content is dropped and replaced by a count, so a caller cannot
  leak held-out material by attaching it to a log record.

Metrics use a fixed catalog (:data:`REQUIRED_METRICS`) with declared label names and bounded
label values. Free-form or high-cardinality values (source, statements, arbitrary model text,
identifiers) are refused as label values rather than exported.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import math
import re
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

CORRELATION_FIELDS = (
    "request_id",
    "campaign_id",
    "run_id",
    "attempt_id",
    "evaluation_id",
    "job_id",
    "fence",
    "execution_id",
    "call_id",
    "release_id",
    "sandbox_id",
)
"""Identifiers T 22.5 requires on every log line where applicable."""

SAFE_EXTRA_FIELDS = frozenset(
    {
        *CORRELATION_FIELDS,
        "environment",
        "role",
        "service_identity",
        "stage",
        "lane",
        "language",
        "queue_class",
        "state",
        "outcome",
        "reason_code",
        "error_code",
        "duration_ms",
        "count",
        "status",
        "provider",
        "worker_id",
        "slot_id",
        "guest_id",
        "drill",
        "step",
        "digest",
    }
)
"""Structured fields a log record may carry. Anything else is dropped (and counted)."""

_HELD_OUT_NAME = re.compile(
    r"(source|statement|prompt|completion|content|body|patch|diff|hidden|oracle|test_code|"
    r"payload|reasoning|secret|password|token|credential)",
    re.IGNORECASE,
)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED:private-key]",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws-access-key]"),
    (re.compile(r"(?i)\b(aws_secret_access_key|aws_session_token)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}\b"), "[REDACTED:provider-key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "[REDACTED:provider-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[REDACTED:token]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/\-]{12,}=*"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(authorization|x-api-key|api[_-]?key)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token)\s*[=:]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+):[^@\s]+@"), r"\1:[REDACTED]@"),
    (re.compile(r"PCBSECRET__[A-Z0-9_]+=\S+"), "PCBSECRET__[REDACTED]"),
)

_MAX_FIELD_CHARS = 512
_MAX_MESSAGE_CHARS = 4096

_context: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar(
    "pcb_log_context", default=MappingProxyType({})
)


def redact(text: str) -> str:
    """Replace credential-shaped substrings. Idempotent and safe on any string."""

    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


@contextlib.contextmanager
def log_context(**identifiers: object) -> Iterator[None]:
    """Bind correlation identifiers for log records emitted inside the block."""

    unknown = set(identifiers) - set(CORRELATION_FIELDS)
    if unknown:
        raise ValueError(f"unsupported correlation fields: {', '.join(sorted(unknown))}")
    merged = dict(_context.get())
    merged.update({key: str(value) for key, value in identifiers.items() if value is not None})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, str]:
    return dict(_context.get())


_RESERVED_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None))
) | {"message", "asctime", "taskName"}


class RedactingJsonFormatter(logging.Formatter):
    """One JSON object per line; redacted, size-bounded and field-allowlisted."""

    def __init__(self, *, environment: str, role: str, service_identity: str | None = None):
        super().__init__()
        self._static = {"environment": environment, "role": role}
        if service_identity:
            self._static["service_identity"] = service_identity

    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())[:_MAX_MESSAGE_CHARS]
        document: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": message,
            **self._static,
            **current_context(),
        }
        dropped = 0
        for key, value in vars(record).items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            if key not in SAFE_EXTRA_FIELDS or _HELD_OUT_NAME.search(key):
                dropped += 1
                continue
            document[key] = _safe_value(value)
        if dropped:
            document["dropped_fields"] = dropped
        if record.exc_info:
            exc_type = record.exc_info[0]
            document["exception_type"] = exc_type.__name__ if exc_type else None
            document["exception"] = redact(self.formatException(record.exc_info))[
                :_MAX_MESSAGE_CHARS
            ]
        return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)


def _safe_value(value: object) -> object:
    if isinstance(value, bool | int | float) or value is None:
        return value
    return redact(str(value))[:_MAX_FIELD_CHARS]


def configure_logging(
    *,
    environment: str,
    role: str,
    service_identity: str | None = None,
    log_format: Literal["json", "text"] = "json",
    level: int = logging.INFO,
    stream: Any = None,
) -> logging.Handler:
    """Install one redacting handler on the root logger (replacing earlier PCB handlers)."""

    handler = logging.StreamHandler(stream)
    if log_format == "json":
        handler.setFormatter(
            RedactingJsonFormatter(
                environment=environment, role=role, service_identity=service_identity
            )
        )
    else:
        handler.setFormatter(
            _RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    handler.set_name("pcb-telemetry")
    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == "pcb-telemetry":
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    return handler


class _RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# --------------------------------------------------------------------------------- metrics

MetricKind = Literal["counter", "gauge", "histogram"]
_LABEL_VALUE = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,63}$")
_METRIC_NAME = re.compile(r"^pcb_[a-z0-9_]+$")
_FORBIDDEN_LABEL_NAMES = re.compile(
    r"(id$|_id$|source|statement|prompt|content|message|text|code|path|url|secret|token|user)",
    re.IGNORECASE,
)
_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: MetricKind
    help: str
    labels: tuple[str, ...] = ()
    buckets: tuple[float, ...] = _DEFAULT_BUCKETS

    def __post_init__(self) -> None:
        if not _METRIC_NAME.fullmatch(self.name):
            raise ValueError(f"metric names must match pcb_[a-z0-9_]+: {self.name}")
        bad = [label for label in self.labels if _FORBIDDEN_LABEL_NAMES.search(label)]
        if bad:
            raise ValueError(f"{self.name}: high-cardinality/content label names refused: {bad}")


def _spec(name: str, kind: MetricKind, help_text: str, *labels: str) -> MetricSpec:
    return MetricSpec(name, kind, help_text, tuple(labels))


REQUIRED_METRICS: tuple[MetricSpec, ...] = (
    _spec("pcb_queue_depth", "gauge", "Ready stage jobs by queue class.", "queue_class"),
    _spec("pcb_queue_oldest_age_seconds", "gauge", "Age of the oldest ready job.", "queue_class"),
    _spec("pcb_job_claims_total", "counter", "Stage job claims.", "queue_class"),
    _spec(
        "pcb_job_completions_total",
        "counter",
        "Fence-checked completions.",
        "queue_class",
        "outcome",
    ),
    _spec("pcb_job_retries_total", "counter", "Infrastructure retries.", "queue_class"),
    _spec(
        "pcb_stale_commit_refusals_total",
        "counter",
        "Completions refused because the lease or fence was stale.",
        "queue_class",
    ),
    _spec("pcb_expired_leases_total", "counter", "Leases recovered by the reaper."),
    _spec("pcb_orphan_guests", "gauge", "Execution guests past their TTL.", "provider", "lane"),
    _spec(
        "pcb_orphan_guests_over_alert_threshold",
        "gauge",
        "Guests unreclaimed beyond TTL + 10 minutes.",
        "provider",
    ),
    _spec("pcb_orphans_reclaimed_total", "counter", "Guests destroyed by the sweep.", "provider"),
    _spec("pcb_active_slots", "gauge", "Capacity slots in use.", "queue_class"),
    _spec(
        "pcb_provider_latency_seconds",
        "histogram",
        "Model/judge provider call latency.",
        "provider",
        "kind",
    ),
    _spec("pcb_provider_throttles_total", "counter", "Provider throttling responses.", "provider"),
    _spec("pcb_provider_outage", "gauge", "1 while a provider circuit is open.", "provider"),
    _spec("pcb_usage_reserved_microusd", "gauge", "Open budget reservations.", "budget_class"),
    _spec("pcb_uncertain_cost_calls", "gauge", "Calls with ambiguous billing outcome.", "provider"),
    _spec(
        "pcb_campaign_spend_ratio",
        "gauge",
        "Spend / configured campaign threshold.",
        "budget_class",
    ),
    _spec(
        "pcb_failures_total",
        "counter",
        "Candidate versus infrastructure failures.",
        "stage",
        "failure_class",
    ),
    _spec("pcb_analyzer_crashes_total", "counter", "Analyzer crashes.", "language"),
    _spec("pcb_judge_invalid_total", "counter", "Invalid judge outputs.", "panel"),
    _spec("pcb_judge_disagreement_ratio", "gauge", "Unresolved judge disagreement.", "panel"),
    _spec(
        "pcb_canary_invalid_consecutive",
        "gauge",
        "Consecutive invalid performance canary runs.",
        "hardware_class",
    ),
    _spec("pcb_canary_drift_ratio", "gauge", "Performance canary drift.", "hardware_class"),
    _spec("pcb_evidence_missing", "gauge", "Required evidence items missing.", "stage"),
    _spec(
        "pcb_publication_validation_failures_total",
        "counter",
        "Release validation failures.",
        "check",
    ),
    _spec(
        "pcb_public_pointer_unavailable",
        "gauge",
        "1 when the public board pointer references unavailable data.",
        "board",
    ),
    _spec(
        "pcb_public_api_request_seconds",
        "histogram",
        "Public API latency.",
        "route",
        "status_class",
    ),
    _spec(
        "pcb_backup_integrity_failures",
        "gauge",
        "Failed checks in the latest backup/restore rehearsal.",
        "target",
    ),
    _spec(
        "pcb_restore_last_success_timestamp",
        "gauge",
        "Unix time of the last verified restore rehearsal.",
        "target",
    ),
    _spec(
        "pcb_secret_access_denied_total",
        "counter",
        "Refused secret/hidden-artifact access attempts observed by the service.",
        "role",
    ),
    _spec("pcb_artifact_write_failures_total", "counter", "Artifact write failures.", "bucket"),
)
"""The T 22.5 metric catalog. Alert rules may reference only these names."""


@dataclass
class _Family:
    spec: MetricSpec
    values: dict[tuple[str, ...], float] = field(default_factory=dict)
    histograms: dict[tuple[str, ...], list[float]] = field(default_factory=dict)


class MetricsRegistry:
    """Thread-safe registry rendering the Prometheus text exposition format (0.0.4)."""

    def __init__(self, specs: tuple[MetricSpec, ...] = REQUIRED_METRICS) -> None:
        self._lock = threading.Lock()
        self._families = {spec.name: _Family(spec) for spec in specs}

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._families)

    def _key(self, name: str, labels: Mapping[str, str]) -> tuple[_Family, tuple[str, ...]]:
        family = self._families.get(name)
        if family is None:
            raise KeyError(f"undeclared metric {name}")
        if set(labels) != set(family.spec.labels):
            raise ValueError(f"{name} requires labels {family.spec.labels}, got {sorted(labels)}")
        values = tuple(str(labels[label]) for label in family.spec.labels)
        for value in values:
            if not _LABEL_VALUE.fullmatch(value):
                raise ValueError(f"{name}: label value refused (unbounded or unsafe)")
        return family, values

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        family, key = self._key(name, labels)
        if family.spec.kind != "counter" or amount < 0:
            raise ValueError(f"{name} is not a counter or amount is negative")
        with self._lock:
            family.values[key] = family.values.get(key, 0.0) + amount

    def set(self, name: str, value: float, **labels: str) -> None:
        family, key = self._key(name, labels)
        if family.spec.kind != "gauge":
            raise ValueError(f"{name} is not a gauge")
        with self._lock:
            family.values[key] = float(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        family, key = self._key(name, labels)
        if family.spec.kind != "histogram" or not math.isfinite(value):
            raise ValueError(f"{name} is not a histogram or value is not finite")
        with self._lock:
            family.histograms.setdefault(key, []).append(value)

    def value(self, name: str, **labels: str) -> float | None:
        family, key = self._key(name, labels)
        with self._lock:
            return family.values.get(key)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name in sorted(self._families):
                family = self._families[name]
                spec = family.spec
                lines.append(f"# HELP {name} {spec.help}")
                lines.append(f"# TYPE {name} {spec.kind}")
                if spec.kind == "histogram":
                    for key, observations in sorted(family.histograms.items()):
                        base = dict(zip(spec.labels, key, strict=True))
                        for bound in spec.buckets:
                            count = sum(1 for item in observations if item <= bound)
                            lines.append(
                                f"{name}_bucket{_labels({**base, 'le': repr(bound)})} {count}"
                            )
                        lines.append(
                            f"{name}_bucket{_labels({**base, 'le': '+Inf'})} {len(observations)}"
                        )
                        lines.append(f"{name}_sum{_labels(base)} {sum(observations)!r}")
                        lines.append(f"{name}_count{_labels(base)} {len(observations)}")
                else:
                    for key, number in sorted(family.values.items()):
                        rendered = _labels(dict(zip(spec.labels, key, strict=True)))
                        lines.append(f"{name}{rendered} {number!r}")
        return "\n".join(lines) + "\n"


def safe_label(value: object) -> str:
    """A bounded label value: the value itself when safe, otherwise ``other``.

    Call sites that label by data-derived values (queue classes, outcomes) use this so a
    surprising value degrades to an aggregate bucket instead of raising inside a worker.
    """

    text = str(value).lower()
    return text if _LABEL_VALUE.fullmatch(text) else "other"


def _labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{value}"' for key, value in labels.items())
    return "{" + inner + "}"


def serve_metrics(registry: MetricsRegistry, port: int, host: str = "127.0.0.1") -> Any:
    """Expose ``/metrics`` on a daemon thread; returns the server (call ``shutdown()``)."""

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = registry.render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="pcb-metrics", daemon=True).start()
    return server


METRICS = MetricsRegistry()
"""Process-wide registry used by services; tests construct their own."""


__all__ = [
    "CORRELATION_FIELDS",
    "METRICS",
    "REQUIRED_METRICS",
    "SAFE_EXTRA_FIELDS",
    "MetricSpec",
    "MetricsRegistry",
    "RedactingJsonFormatter",
    "configure_logging",
    "current_context",
    "log_context",
    "redact",
    "safe_label",
    "serve_metrics",
]
